"""SQLite storage. All timestamps are UTC epoch seconds."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import threading
from pathlib import Path
from typing import Any, Iterable

from .model import Device, Link

SCHEMA_VERSION = 2  # 2: wifi_readings

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS devices (
    ieee TEXT PRIMARY KEY, name TEXT, device_type TEXT, power_source TEXT, area TEXT,
    model TEXT, manufacturer TEXT, primary_entity TEXT,
    first_seen REAL, last_snapshot REAL, present INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS device_samples (
    ts REAL, ieee TEXT, lqi INTEGER, rssi INTEGER, available INTEGER, last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_ds ON device_samples (ieee, ts);
CREATE INDEX IF NOT EXISTS idx_ds_ts ON device_samples (ts);
CREATE TABLE IF NOT EXISTS link_samples (
    ts REAL, reporter TEXT, neighbor TEXT, lqi INTEGER, relationship TEXT, depth INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ls ON link_samples (ts);
CREATE INDEX IF NOT EXISTS idx_ls_pair ON link_samples (reporter, neighbor, ts);
CREATE TABLE IF NOT EXISTS outages (
    ieee TEXT, start_ts REAL, end_ts REAL, source TEXT, PRIMARY KEY (ieee, start_ts)
);
CREATE INDEX IF NOT EXISTS idx_out_start ON outages (start_ts);
CREATE TABLE IF NOT EXISTS rooms (name TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, coordinator INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS adjacency (a TEXT, b TEXT, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS router_events (
    ieee TEXT PRIMARY KEY, ts REAL, room TEXT, recommended_room TEXT,
    evaluated_ts REAL, result TEXT
);
CREATE TABLE IF NOT EXISTS recommendations (ts REAL PRIMARY KEY, result TEXT);
CREATE TABLE IF NOT EXISTS incidents (
    key TEXT PRIMARY KEY, opened_ts REAL, last_ts REAL, message TEXT,
    notified INTEGER DEFAULT 0, closed_ts REAL, close_notified INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS daily_stats (
    day TEXT, ieee TEXT, lqi_median REAL, samples INTEGER, outages INTEGER, PRIMARY KEY (day, ieee)
);
CREATE TABLE IF NOT EXISTS reports (week TEXT PRIMARY KEY, ts REAL, path TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS wifi_readings (ts REAL PRIMARY KEY, wifi INTEGER, zigbee INTEGER);
"""


def links_hash(links: Iterable[Link]) -> str:
    rows = sorted((l.neighbor, l.lqi or -1, l.relationship, l.depth or -1) for l in links)
    return hashlib.sha1(json.dumps(rows).encode()).hexdigest()


class DB:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(SCHEMA)
            # New tables are created by SCHEMA above (CREATE ... IF NOT EXISTS), so upgrading only
            # needs the version bump: 1 -> 2 added wifi_readings.
            if (self.get_meta("schema_version") or 0) < SCHEMA_VERSION:
                self.set_meta("schema_version", SCHEMA_VERSION)

    # -- helpers ---------------------------------------------------------
    def q(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def x(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).rowcount

    def many(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def get_meta(self, key: str, default: Any = None) -> Any:
        rows = self.q("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_meta(self, key: str, value: Any) -> None:
        self.x("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value)))

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # -- devices & samples ----------------------------------------------
    def upsert_devices(self, devices: list[Device], ts: float) -> list[str]:
        """Store device metadata; returns IEEEs seen for the first time."""
        known = {r["ieee"] for r in self.q("SELECT ieee FROM devices")}
        new = [d.ieee for d in devices if d.ieee not in known]
        present = {d.ieee for d in devices}
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                for d in devices:
                    self.conn.execute(
                        """INSERT INTO devices(ieee,name,device_type,power_source,area,model,manufacturer,
                               primary_entity,first_seen,last_snapshot,present)
                           VALUES(?,?,?,?,?,?,?,?,?,?,1)
                           ON CONFLICT(ieee) DO UPDATE SET name=excluded.name, device_type=excluded.device_type,
                               power_source=excluded.power_source, area=excluded.area, model=excluded.model,
                               manufacturer=excluded.manufacturer, primary_entity=excluded.primary_entity,
                               last_snapshot=excluded.last_snapshot, present=1""",
                        (d.ieee, d.name, d.device_type, d.power_source, d.area, d.model, d.manufacturer,
                         d.primary_entity, ts, ts),
                    )
                for ieee in known - present:
                    self.conn.execute("UPDATE devices SET present=0 WHERE ieee=?", (ieee,))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        return new

    def devices(self, present_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM devices" + (" WHERE present=1" if present_only else "") + " ORDER BY name"
        return self.q(sql)

    def last_device_sample(self, ieee: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM device_samples WHERE ieee=? ORDER BY ts DESC LIMIT 1", (ieee,))
        return rows[0] if rows else None

    def add_device_samples(self, devices: list[Device], ts: float) -> int:
        """Insert one sample per device, skipping devices without a new frame since last poll."""
        rows = []
        for d in devices:
            last = self.last_device_sample(d.ieee)
            if last is not None and d.last_seen is not None and last["last_seen"] == d.last_seen \
                    and bool(last["available"]) == d.available:
                continue  # no new frame received: the LQI value is stale, do not count it twice
            rows.append((ts, d.ieee, d.lqi, d.rssi, int(d.available), d.last_seen))
        if rows:
            self.many("INSERT INTO device_samples VALUES(?,?,?,?,?,?)", rows)
        return len(rows)

    def add_link_samples(self, links: list[Link], ts: float) -> int:
        """Store a reporter's neighbor table only if it changed (= a new topology scan)."""
        by_reporter: dict[str, list[Link]] = {}
        for l in links:
            by_reporter.setdefault(l.reporter, []).append(l)
        hashes = self.get_meta("link_hashes", {})
        rows = []
        for reporter, ls in by_reporter.items():
            h = links_hash(ls)
            if hashes.get(reporter) == h:
                continue
            hashes[reporter] = h
            rows.extend((ts, l.reporter, l.neighbor, l.lqi, l.relationship, l.depth) for l in ls)
        if rows:
            self.many("INSERT INTO link_samples VALUES(?,?,?,?,?,?)", rows)
            self.set_meta("link_hashes", hashes)
        return len(rows)

    def device_samples(self, since: float, until: float | None = None, ieee: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM device_samples WHERE ts>=?"
        params: list[Any] = [since]
        if until is not None:
            sql += " AND ts<?"
            params.append(until)
        if ieee is not None:
            sql += " AND ieee=?"
            params.append(ieee)
        return self.q(sql + " ORDER BY ts", params)

    def link_samples(self, since: float, until: float | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM link_samples WHERE ts>=?"
        params: list[Any] = [since]
        if until is not None:
            sql += " AND ts<?"
            params.append(until)
        return self.q(sql + " ORDER BY ts", params)

    # -- outages ----------------------------------------------------------
    def open_outage(self, ieee: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM outages WHERE ieee=? AND end_ts IS NULL ORDER BY start_ts DESC LIMIT 1", (ieee,))
        return rows[0] if rows else None

    def apply_availability(self, ieee: str, transitions: list[tuple[float, bool]], source: str) -> int:
        """Run the up/down state machine over transitions; returns number of new outages."""
        down = self.open_outage(ieee) is not None
        new = 0
        for ts, available in sorted(transitions):
            if not available and not down:
                new += self.x("INSERT OR IGNORE INTO outages(ieee,start_ts,end_ts,source) VALUES(?,?,NULL,?)",
                              (ieee, ts, source))
                down = True
            elif available and down:
                self.x("UPDATE outages SET end_ts=? WHERE ieee=? AND end_ts IS NULL AND start_ts<=?", (ts, ieee, ts))
                down = False
        return new

    def outages(self, since: float, until: float | None = None) -> list[sqlite3.Row]:
        """Outages overlapping [since, until)."""
        sql = "SELECT * FROM outages WHERE (end_ts IS NULL OR end_ts>=?)"
        params: list[Any] = [since]
        if until is not None:
            sql += " AND start_ts<?"
            params.append(until)
        return self.q(sql + " ORDER BY start_ts", params)

    # -- rooms --------------------------------------------------------------
    def sync_rooms(self, areas: list[str], coordinator_area: str | None) -> None:
        existing = {r["name"] for r in self.q("SELECT name FROM rooms")}
        for a in areas:
            if a not in existing:
                self.x("INSERT INTO rooms(name,enabled,coordinator) VALUES(?,1,0)", (a,))
        if coordinator_area and not self.q("SELECT 1 FROM rooms WHERE coordinator=1"):
            self.x("UPDATE rooms SET coordinator=1 WHERE name=?", (coordinator_area,))

    def rooms(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM rooms ORDER BY name")

    def adjacency(self) -> set[tuple[str, str]]:
        return {(r["a"], r["b"]) for r in self.q("SELECT a,b FROM adjacency")}

    def save_room_setup(self, enabled: list[str], coordinator: str, pairs: list[tuple[str, str]], ts: float) -> None:
        names = {r["name"] for r in self.rooms()}
        if coordinator not in names:
            raise ValueError("unknown coordinator room")
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.execute("UPDATE rooms SET enabled=0, coordinator=0")
                for n in enabled:
                    if n in names:
                        self.conn.execute("UPDATE rooms SET enabled=1 WHERE name=?", (n,))
                self.conn.execute("UPDATE rooms SET enabled=1, coordinator=1 WHERE name=?", (coordinator,))
                self.conn.execute("DELETE FROM adjacency")
                for a, b in pairs:
                    if a in names and b in names and a != b:
                        a, b = sorted((a, b))
                        self.conn.execute("INSERT OR IGNORE INTO adjacency(a,b) VALUES(?,?)", (a, b))
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        self.set_meta("rooms_confirmed_ts", ts)

    # -- maintenance --------------------------------------------------------
    def aggregate_days(self, days: list[tuple[str, float, float]]) -> int:
        """days: (YYYY-MM-DD, start_ts, end_ts) in local time; stores per-device daily medians."""
        done = 0
        for day, start, end in days:
            if self.q("SELECT 1 FROM daily_stats WHERE day=? LIMIT 1", (day,)):
                continue
            per: dict[str, list[int]] = {}
            for r in self.device_samples(start, end):
                if r["lqi"] is not None:
                    per.setdefault(r["ieee"], []).append(r["lqi"])
            outs: dict[str, int] = {}
            for r in self.q("SELECT ieee FROM outages WHERE start_ts>=? AND start_ts<?", (start, end)):
                outs[r["ieee"]] = outs.get(r["ieee"], 0) + 1
            rows = [(day, i, statistics.median(v) if v else None, len(v), outs.get(i, 0))
                    for i, v in per.items()]
            rows += [(day, i, None, 0, n) for i, n in outs.items() if i not in per]
            if rows:
                self.many("INSERT OR REPLACE INTO daily_stats VALUES(?,?,?,?,?)", rows)
                done += 1
        return done

    # -- Wi-Fi channel history ------------------------------------------
    def add_wifi_reading(self, ts: float, wifi: int, zigbee: int | None) -> None:
        self.x("INSERT OR REPLACE INTO wifi_readings(ts,wifi,zigbee) VALUES(?,?,?)", (ts, wifi, zigbee))

    def wifi_readings(self, since: float) -> list[tuple[float, int, int | None]]:
        """Readings from `since` on, plus the last one before it (the state at `since`)."""
        rows = self.q("SELECT ts,wifi,zigbee FROM wifi_readings WHERE ts>=? "
                      "UNION SELECT * FROM (SELECT ts,wifi,zigbee FROM wifi_readings WHERE ts<? ORDER BY ts DESC LIMIT 1) "
                      "ORDER BY ts", (since, since))
        return [(r["ts"], r["wifi"], r["zigbee"]) for r in rows]

    def purge(self, raw_before: float, daily_before_day: str) -> dict[str, int]:
        counts = {
            "device_samples": self.x("DELETE FROM device_samples WHERE ts<?", (raw_before,)),
            "link_samples": self.x("DELETE FROM link_samples WHERE ts<?", (raw_before,)),
            "outages": self.x("DELETE FROM outages WHERE end_ts IS NOT NULL AND end_ts<?", (raw_before,)),
            "recommendations": self.x("DELETE FROM recommendations WHERE ts<?", (raw_before,)),
            "daily_stats": self.x("DELETE FROM daily_stats WHERE day<?", (daily_before_day,)),
            "incidents": self.x("DELETE FROM incidents WHERE closed_ts IS NOT NULL AND closed_ts<?", (raw_before,)),
            "wifi_readings": self.x("DELETE FROM wifi_readings WHERE ts<?", (raw_before,)),
        }
        with self.lock:
            self.conn.execute("PRAGMA incremental_vacuum")
        return counts

    def daily_stats(self, ieee: str, since_day: str) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM daily_stats WHERE ieee=? AND day>=? ORDER BY day", (ieee, since_day))
