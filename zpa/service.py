"""The running service: collection loop, scheduler, analysis glue."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from . import channels
from .analysis import DeviceInfo, NetworkStats, compute_stats
from .beforeafter import Evaluation, evaluate
from .config import WEEKDAYS
from .db import DB
from .hass import HAClient
from .i18n import Translator
from .model import COORDINATOR, ROUTER
from .monitor import Monitor
from .notify import Notifier
from .recommend import Recommendation, RoomModel, recommend
from .sources import Source, SourceError, create_source

log = logging.getLogger(__name__)


class App:
    def __init__(
        self,
        cfg: dict,
        token: str,
        db: DB | None = None,
        source: Source | None = None,
        notifier: Notifier | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.cfg = cfg
        self.token = token
        self.clock = clock
        self.tz = ZoneInfo(cfg["timezone"])
        self.t = Translator(cfg["language"])
        self.data_dir = Path(cfg["data_dir"])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "reports").mkdir(exist_ok=True)
        self.db = db or DB(self.data_dir / "zpa.sqlite3")
        self.source = source or create_source(cfg, token)
        self.notifier = notifier or Notifier(cfg, token)
        self.monitor = Monitor(self.db, self.t, enabled=cfg["notify"]["incidents"])
        self.metrics = None  # set by zpa.metrics.attach
        self.last_cycle: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ time
    def now(self) -> float:
        return self.clock()

    def local(self, ts: float | None) -> dt.datetime | None:
        return dt.datetime.fromtimestamp(ts, self.tz) if ts is not None else None

    def fmt(self, ts: float | None, with_time: bool = True) -> str:
        d = self.local(ts)
        if d is None:
            return self.t("never")
        return d.strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")

    # ------------------------------------------------------------ collection
    async def collect_once(self) -> dict:
        async with self._lock:
            return await self._collect_once()

    async def _collect_once(self) -> dict:
        now = self.now()
        mon = self.cfg["monitoring"]
        summary: dict[str, Any] = {"ts": now, "ok": False}
        history_since = self.db.get_meta("history_until")
        try:
            result = await self.source.collect(history_since, now)
        except SourceError as exc:
            fails = int(self.db.get_meta("consecutive_failures", 0)) + 1
            self.db.set_meta("consecutive_failures", fails)
            self.db.set_meta("last_error", {"ts": now, "kind": exc.kind, "message": str(exc)})
            summary.update(error=str(exc), kind=exc.kind)
            log.error("collection failed (%s, %d in a row): %s", exc.kind, fails, exc)
            if exc.kind == "connection":
                if fails >= mon["failure_cycles_before_alert"]:
                    self.monitor.open("connection", self.t("inc_connection", cycles=fails, detail=exc), now)
            elif exc.kind in ("auth", "schema", "no_data"):
                self.monitor.open(exc.kind, self.t(f"inc_{exc.kind}", detail=exc), now)
            else:
                self.monitor.open("schema", self.t("inc_schema", detail=exc), now)
            if self.metrics:
                self.metrics.collection_failed(exc.kind)
            await self.monitor.flush(self.notifier.send)
            self.last_cycle = summary
            return summary

        snap = result.snapshot
        for key in ("connection", "auth", "schema", "no_data", "internal"):
            self.monitor.close(key, now)
        self.db.set_meta("consecutive_failures", 0)
        if self.db.get_meta("first_snapshot_ts") is None:
            self.db.set_meta("first_snapshot_ts", now)
        baseline_ready = now - float(self.db.get_meta("first_snapshot_ts")) >= 86400

        new_devices = self.db.upsert_devices(snap.devices, now)
        coord = next((d for d in snap.devices if d.device_type == COORDINATOR), None)
        self.db.sync_rooms(snap.areas, coord.area if coord else None)
        n_dev = self.db.add_device_samples(snap.devices, now)
        n_link = self.db.add_link_samples(snap.links, now)

        # availability history -> outages
        new_out = 0
        for ieee, transitions in result.availability.items():
            new_out += self.db.apply_availability(ieee, transitions, "history")
        for d in snap.devices:  # fallback for devices whose history is not recorded
            if d.device_type != COORDINATOR and d.ieee not in result.history_covered:
                new_out += self.db.apply_availability(d.ieee, [(now, d.available)], "snapshot")
        if result.history_until is not None:
            self.db.set_meta("history_until", result.history_until)

        # progress / staleness: did any non-coordinator device send anything?
        progressed = any(
            d.device_type != COORDINATOR and d.last_seen is not None
            and self.db.get_meta("last_seen_marker", {}).get(d.ieee) != d.last_seen
            for d in snap.devices
        )
        self.db.set_meta("last_seen_marker", {d.ieee: d.last_seen for d in snap.devices})
        if progressed or self.db.get_meta("last_progress_ts") is None:
            self.db.set_meta("last_progress_ts", now)
        stale_s = mon["stale_minutes"] * 60
        others = [d for d in snap.devices if d.device_type != COORDINATOR]
        if others and now - float(self.db.get_meta("last_progress_ts")) > stale_s:
            self.monitor.open("stale", self.t("inc_stale", minutes=mon["stale_minutes"]), now)
        else:
            self.monitor.close("stale", now)

        # channel check
        wifi, wifi_err = await channels.read_wifi_channel(self.cfg)
        if wifi_err:
            self.monitor.open("wifi", self.t("inc_wifi", detail=wifi_err), now)
        else:
            self.monitor.close("wifi", now)
        if snap.zigbee_channel:
            self.db.set_meta("zigbee_channel", snap.zigbee_channel)
        if wifi:
            self.db.set_meta("wifi_channel", wifi)
            self.db.add_wifi_reading(now, wifi, snap.zigbee_channel or self.db.get_meta("zigbee_channel"))
        chk = self.channel_check()
        previous = self.db.get_meta("channel_status")
        if chk.status != channels.UNKNOWN and chk.status != previous:
            self.db.set_meta("channel_status", chk.status)
            if chk.status == channels.OVERLAP:  # warn once each time it starts overlapping
                await self.notifier.send(self.t("notify_channel_title"), self.channel_text(chk))

        # new routers -> before/after tracking
        rec = self.recommendation(now)
        if baseline_ready:
            for d in snap.devices:
                if d.ieee in new_devices and d.device_type == ROUTER:
                    best = rec.best.room if rec.best else None
                    self.db.x("INSERT OR IGNORE INTO router_events(ieee,ts,room,recommended_room) VALUES(?,?,?,?)",
                              (d.ieee, now, d.area, best))
                    log.info("new router detected: %s in %s (recommended: %s)", d.name, d.area, best)
        self.store_recommendation(rec, now)

        self.db.set_meta("last_success_ts", now)
        self.db.set_meta("source_version", snap.source_version)
        summary.update(
            ok=True, devices=len(snap.devices), links=len(snap.links), device_samples=n_dev,
            link_samples=n_link, new_outages=new_out, zigbee_channel=snap.zigbee_channel, wifi_channel=wifi,
            warnings=result.warnings, new_devices=new_devices, status=rec.status,
        )
        if self.metrics:
            self.metrics.update(self, rec)
        await self.check_notify_services(now)
        await self.monitor.flush(self.notifier.send)
        self.last_cycle = summary
        log.info("collected: %s", {k: v for k, v in summary.items() if k != "warnings"})
        return summary

    async def check_notify_services(self, now: float) -> None:
        """Once a day: verify the configured notify services exist in HA."""
        if not self.cfg["notify"]["services"] or now - float(self.db.get_meta("notify_check_ts", 0)) < 86400:
            return
        try:
            async with HAClient(self.cfg["home_assistant"]["url"], self.token,
                                verify_ssl=self.cfg["home_assistant"]["verify_ssl"]) as ha:
                services = await ha.call("get_services")
        except SourceError as exc:
            log.warning("cannot check notify services: %s", exc)
            return
        existing = set((services or {}).get("notify", {}))
        for s in self.cfg["notify"]["services"]:
            key = f"notify_{s}"
            if s in existing:
                self.monitor.close(key, now)
            else:
                self.monitor.open(key, self.t("inc_notify", service=s), now)
        self.db.set_meta("notify_check_ts", now)

    # -------------------------------------------------------------- analysis
    def device_infos(self) -> list[DeviceInfo]:
        return [DeviceInfo(r["ieee"], r["name"], r["device_type"], r["power_source"] or "", r["area"])
                for r in self.db.devices()]

    def stats(self, start: float, end: float, infos: list[DeviceInfo] | None = None) -> NetworkStats:
        return compute_stats(
            infos if infos is not None else self.device_infos(),
            self.db.device_samples(start, end),
            self.db.link_samples(start, end),
            self.db.outages(start, end),
            start, end, self.cfg["thresholds"],
        )

    def current_stats(self, now: float | None = None) -> NetworkStats:
        now = self.now() if now is None else now
        return self.stats(now - self.cfg["thresholds"]["analysis_window_days"] * 86400, now + 1)

    def room_model(self) -> RoomModel:
        rows = self.db.rooms()
        coord = next((r["name"] for r in rows if r["coordinator"]), None)
        return RoomModel(
            rooms=[r["name"] for r in rows if r["enabled"]],
            adjacency=self.db.adjacency(),
            coordinator_room=coord,
            confirmed=self.db.get_meta("rooms_confirmed_ts") is not None,
        )

    def recommendation(self, now: float | None = None, stats: NetworkStats | None = None) -> Recommendation:
        stats = stats or self.current_stats(now)
        return recommend(stats, self.room_model(), self.cfg)

    def store_recommendation(self, rec: Recommendation, now: float) -> None:
        data = json.dumps(rec.to_dict(), sort_keys=True)
        last = self.db.q("SELECT result FROM recommendations ORDER BY ts DESC LIMIT 1")
        comparable = lambda s: {k: v for k, v in json.loads(s).items() if k not in ("days", "measurements", "scans")}
        if not last or comparable(last[0]["result"]) != comparable(data):
            self.db.x("INSERT OR REPLACE INTO recommendations(ts,result) VALUES(?,?)", (now, data))

    def channel_check(self) -> channels.ChannelCheck:
        return channels.check(self.db.get_meta("zigbee_channel"), self.db.get_meta("wifi_channel"))

    def channel_history(self, now: float | None = None) -> channels.ChannelHistory:
        now = self.now() if now is None else now
        since = now - channels.HISTORY_WINDOW
        return channels.history(self.db.wifi_readings(since), now, zigbee=self.db.get_meta("zigbee_channel"))

    def channel_text(self, chk: channels.ChannelCheck | None = None, hist: channels.ChannelHistory | None = None) -> str:
        """One plain-text paragraph (notifications, weekly report summary, CLI): state, plus the fix if needed."""
        chk = chk or self.channel_check()
        parts = [self.t(f"channel_{chk.status}", zigbee=chk.zigbee or "?", wifi=chk.wifi or "?", mhz=chk.distance_mhz)]
        if hist is not None and hist.auto:
            parts.append(self.t("channel_auto", n=hist.changes))
        if chk.status == channels.OVERLAP or (hist is not None and hist.auto):
            best = channels.best_wifi_channels(chk.zigbee)
            if best:
                parts.append(self.t("channel_fix", wifi=best[0][0]))
        return " ".join(parts)

    def channel_view(self, now: float | None = None) -> dict:
        """Everything the channel card shows."""
        now = self.now() if now is None else now
        chk = self.channel_check()
        hist = self.channel_history(now)
        best = channels.best_wifi_channels(chk.zigbee)
        needs_fix = chk.status == channels.OVERLAP or hist.auto
        tone = {channels.OVERLAP: "bad", channels.OK: "warn" if hist.auto else "ok"}.get(chk.status, "")
        notes = []
        if hist.auto:
            notes.append(self.t("channel_auto", n=hist.changes))
            if hist.last_change is not None and now - hist.last_change > 86400:
                notes.append(self.t("channel_auto_settled", ago=self.t.ago(now - hist.last_change)))
        return {
            "status": chk.status, "tone": tone, "zigbee": chk.zigbee, "wifi": chk.wifi, "mhz": chk.distance_mhz,
            "headline": self.t(f"channel_{chk.status}", zigbee=chk.zigbee or "?", wifi=chk.wifi or "?",
                               mhz=chk.distance_mhz),
            "text": self.channel_text(chk, hist),
            "auto": hist.auto, "changes": hist.changes, "readings": hist.readings,
            "channels_seen": hist.channels,
            "overlap_pct": None if hist.overlap_share is None else round(hist.overlap_share * 100),
            "since": self.fmt(hist.since) if hist.since is not None else None,
            "full_window": hist.since is not None and hist.since <= now - channels.HISTORY_WINDOW + 3600,
            "notes": notes,
            "best": [{"wifi": w, "mhz": d} for w, d in best] if needs_fix else [],
            "best_text": self.t("channel_best", zigbee=chk.zigbee, list=", ".join(
                self.t("channel_best_item", wifi=w, mhz=d) for w, d in best)) if needs_fix and best else "",
            "fix": self.t("channel_fix", wifi=best[0][0]) if needs_fix and best else "",
        }

    def recommendation_text(self, rec: Recommendation) -> list[str]:
        names = {r["ieee"]: r["name"] for r in self.db.devices(present_only=False)}
        lines: list[str] = []
        if rec.status == "ok":
            b = rec.best
            lines.append(self.t("rec_ok", room=b.room, count=b.count, level=self.t(f"level_{b.confidence_level}")))
            for c in rec.candidates[1:3]:
                lines.append(self.t("rec_alt", room=c.room, count=c.count, level=self.t(f"level_{c.confidence_level}")))
        else:
            lines.append(self.t({"healthy": "rec_healthy", "insufficient_data": "rec_insufficient",
                                 "setup_needed": "rec_setup", "no_candidate": "rec_no_candidate"}[rec.status]))
            lines.extend(self.t(k, **p) for k, p in zip(rec.reasons, rec.reason_params))
        for key, ids in (("unplaced", rec.unplaced), ("not_distance", rec.not_distance), ("unreachable", rec.unreachable)):
            if ids:
                lines.append(self.t(key, names=", ".join(names.get(i, i) for i in ids)))
        return lines

    def basis_text(self, rec: Recommendation) -> str:
        return self.t("basis", days=rec.days, measurements=rec.measurements, scans=rec.scans, devices=rec.devices)

    # ---------------------------------------------------------- before/after
    async def evaluate_router_events(self, now: float) -> list[Evaluation]:
        ba = self.cfg["before_after"]
        done: list[Evaluation] = []
        for ev in self.db.q("SELECT * FROM router_events WHERE evaluated_ts IS NULL"):
            settle = ba["settle_hours"] * 3600
            if now < ev["ts"] + settle + ba["after_days"] * 86400:
                continue
            infos = self.device_infos()
            before = self.stats(ev["ts"] - ba["before_days"] * 86400, ev["ts"], infos)
            after = self.stats(ev["ts"] + settle, ev["ts"] + settle + ba["after_days"] * 86400, infos)
            result = evaluate(before, after, ev["ieee"], ev["room"], ev["recommended_room"], self.cfg)
            self.db.x("UPDATE router_events SET evaluated_ts=?, result=? WHERE ieee=?",
                      (now, json.dumps(result.to_dict()), ev["ieee"]))
            done.append(result)
            if self.cfg["notify"]["before_after"]:
                name = next((i.name for i in infos if i.ieee == ev["ieee"]), ev["ieee"])
                text = f"{name}: " + self.verdict_text(result)
                await self.notifier.send(self.t("notify_ba_title"), text, self.cfg["web"]["public_url"] + "/reports")
        return done

    def verdict_text(self, result: Evaluation | dict) -> str:
        r = result.to_dict() if isinstance(result, Evaluation) else result
        text = self.t(f"verdict_{r['verdict']}", improved=r["improved"], considered=r["considered"], worse=r["worse"])
        if r.get("as_recommended") is True:
            text += " " + self.t("as_recommended_yes")
        elif r.get("as_recommended") is False:
            text += " " + self.t("as_recommended_no", room=r["room"], rec=r["recommended_room"])
        return text

    # ----------------------------------------------------------- maintenance
    def maintenance(self, now: float) -> dict:
        today = self.local(now).date()
        days = []
        for back in range(1, 4):
            day = today - dt.timedelta(days=back)
            start = dt.datetime.combine(day, dt.time(0), self.tz).timestamp()
            end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), self.tz).timestamp()
            days.append((day.isoformat(), start, end))
        aggregated = self.db.aggregate_days(days)
        r = self.cfg["retention"]
        cutoff_day = (today - dt.timedelta(days=r["daily_days"])).isoformat()
        purged = self.db.purge(now - r["raw_days"] * 86400, cutoff_day)
        from .report import prune_reports

        removed = prune_reports(self, r["reports_keep"])
        self.db.set_meta("last_maintenance_ts", now)
        log.info("maintenance: aggregated %d day(s), purged %s, removed %d report(s)", aggregated, purged, removed)
        return {"aggregated": aggregated, "purged": purged, "reports_removed": removed}

    # ------------------------------------------------------------- scheduler
    def weekly_due(self, now: float) -> str | None:
        wr = self.cfg["notify"]["weekly_report"]
        if not wr["enabled"]:
            return None
        loc = self.local(now)
        hh, mm = map(int, wr["time"].split(":"))
        if loc.weekday() != WEEKDAYS.index(wr["weekday"]) or (loc.hour, loc.minute) < (hh, mm):
            return None
        week = f"{loc.isocalendar().year}-W{loc.isocalendar().week:02d}"
        return None if self.db.get_meta("last_weekly_report") == week else week

    async def scheduler_tick(self, now: float) -> None:
        from .report import write_report

        if self.local(now).hour == 3 and now - float(self.db.get_meta("last_maintenance_ts", 0)) > 6 * 3600:
            self.maintenance(now)
        await self.evaluate_router_events(now)
        week = self.weekly_due(now)
        if week:
            await write_report(self, now, notify=True)
            self.db.set_meta("last_weekly_report", week)

    async def run(self) -> None:
        interval = self.cfg["collector"]["interval_minutes"] * 60
        next_collect = 0.0
        while not self._stop.is_set():
            now = self.now()
            if now >= next_collect:
                try:
                    await self.collect_once()
                except Exception:  # noqa: BLE001 - the loop must survive anything
                    log.exception("unexpected error in collection cycle")
                    self.monitor.open("internal", self.t("inc_internal"), now)
                next_collect = now + interval
            try:
                await self.scheduler_tick(self.now())
            except Exception:  # noqa: BLE001
                log.exception("unexpected error in scheduler")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
