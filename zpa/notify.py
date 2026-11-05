"""Notifications through Home Assistant notify services (REST API)."""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg: dict, token: str):
        self.url = cfg["home_assistant"]["url"].rstrip("/")
        self.verify_ssl = cfg["home_assistant"]["verify_ssl"]
        self.services: list[str] = list(cfg["notify"]["services"])
        self.token = token
        self.sent: list[tuple[str, str, str]] = []  # (service, title, message), for tests/acceptance

    async def send(self, title: str, message: str, url: str | None = None) -> bool:
        """True if at least one service accepted the message (or none are configured)."""
        if not self.services:
            return True
        if not self.token:
            return False  # never send unauthenticated requests (HA would log failed logins); stays queued
        ok = False
        payload: dict = {"title": title, "message": message}
        if url:
            # mobile_app: tapping the notification opens the link (Android: clickAction, iOS: url)
            payload["data"] = {"url": url, "clickAction": url}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                for service in self.services:
                    async with s.post(
                        f"{self.url}/api/services/notify/{service}",
                        json=payload,
                        headers={"Authorization": f"Bearer {self.token}"},
                        ssl=True if self.verify_ssl else False,
                    ) as resp:
                        if resp.status < 300:
                            ok = True
                            self.sent.append((service, title, message))
                            log.info("notification sent via notify.%s: %s", service, title)
                        else:
                            log.warning("notify.%s answered HTTP %s", service, resp.status)
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            log.warning("notification failed: %s", exc)
            return False
        return ok
