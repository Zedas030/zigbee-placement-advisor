"""Self-monitoring: turns collection failures into incidents that are notified
once when they open and once when they are resolved (no notification spam).

If Home Assistant itself is down, notifications cannot be delivered through it;
they stay queued (notified=0) and are sent as soon as HA answers again. The
Prometheus metric `zpa_collector_last_success_timestamp_seconds` lets Grafana
alert independently of HA.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from .db import DB

log = logging.getLogger(__name__)

Sender = Callable[[str, str], Awaitable[bool]]  # (title, message) -> delivered?


class Monitor:
    def __init__(self, db: DB, t, enabled: bool = True):
        self.db = db
        self.t = t
        self.enabled = enabled

    def open(self, key: str, message: str, now: float) -> None:
        row = self.db.q("SELECT * FROM incidents WHERE key=?", (key,))
        if row and row[0]["closed_ts"] is None:
            self.db.x("UPDATE incidents SET last_ts=?, message=? WHERE key=?", (now, message, key))
            return
        log.warning("incident opened: %s: %s", key, message)
        self.db.x(
            """INSERT INTO incidents(key,opened_ts,last_ts,message,notified,closed_ts,close_notified)
               VALUES(?,?,?,?,0,NULL,1)
               ON CONFLICT(key) DO UPDATE SET opened_ts=excluded.opened_ts, last_ts=excluded.last_ts,
                   message=excluded.message, notified=0, closed_ts=NULL, close_notified=1""",
            (key, now, now, message),
        )

    def close(self, key: str, now: float) -> None:
        row = self.db.q("SELECT * FROM incidents WHERE key=? AND closed_ts IS NULL", (key,))
        if not row:
            return
        log.info("incident resolved: %s", key)
        # Always announce the resolution. If the opening could not be delivered (typically: HA was
        # down, so its notify service was too), "Resolved: <problem>" is the only way to learn it happened.
        self.db.x("UPDATE incidents SET closed_ts=?, close_notified=0 WHERE key=?", (now, key))

    def open_incidents(self) -> list:
        return self.db.q("SELECT * FROM incidents WHERE closed_ts IS NULL ORDER BY opened_ts")

    async def flush(self, send: Sender) -> None:
        """Deliver pending open/resolved notifications."""
        if not self.enabled:
            return
        for row in self.db.q("SELECT * FROM incidents WHERE closed_ts IS NULL AND notified=0"):
            if await send(self.t("notify_incident_title"), row["message"]):
                self.db.x("UPDATE incidents SET notified=1 WHERE key=?", (row["key"],))
        for row in self.db.q("SELECT * FROM incidents WHERE closed_ts IS NOT NULL AND close_notified=0"):
            if await send(self.t("notify_resolved_title"), self.t("inc_resolved", message=row["message"])):
                self.db.x("UPDATE incidents SET close_notified=1 WHERE key=?", (row["key"],))
