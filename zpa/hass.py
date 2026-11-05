"""Minimal Home Assistant client (WebSocket commands + REST service calls)."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from .sources.base import SourceAuthError, SourceConnectionError, SourceError

log = logging.getLogger(__name__)


class CommandError(SourceError):
    def __init__(self, command: str, code: str, message: str):
        super().__init__(f"{command}: {code}: {message}")
        self.command = command
        self.code = code


class HAClient:
    """Short-lived connection: `async with HAClient(...) as ha: await ha.call(...)`."""

    def __init__(self, url: str, token: str, timeout: float = 30.0, verify_ssl: bool = True):
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.ha_version = ""
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._id = 0

    @property
    def ws_url(self) -> str:
        base = self.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return base + "/api/websocket"

    async def __aenter__(self) -> "HAClient":
        if not self.token:
            raise SourceAuthError("no Home Assistant token configured (HA_TOKEN in .env)")
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=self.timeout)
        )
        try:
            self._ws = await self._session.ws_connect(
                self.ws_url, heartbeat=30, max_msg_size=0, ssl=True if self.verify_ssl else False
            )
            first = await self._recv()
            if first.get("type") != "auth_required":
                raise SourceConnectionError(f"unexpected greeting: {first.get('type')}")
            await self._ws.send_json({"type": "auth", "access_token": self.token})
            res = await self._recv()
            if res.get("type") == "auth_invalid":
                raise SourceAuthError("Home Assistant rejected the token")
            if res.get("type") != "auth_ok":
                raise SourceConnectionError(f"unexpected auth answer: {res.get('type')}")
            self.ha_version = res.get("ha_version", "")
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            await self.close()
            raise SourceConnectionError(f"cannot reach Home Assistant at {self.url}: {exc}") from exc
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _recv(self) -> dict:
        assert self._ws is not None
        try:
            msg = await asyncio.wait_for(self._ws.receive(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise SourceConnectionError("Home Assistant did not answer in time") from exc
        if msg.type == aiohttp.WSMsgType.TEXT:
            return json.loads(msg.data)
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
            raise SourceConnectionError("Home Assistant closed the WebSocket")
        if msg.type == aiohttp.WSMsgType.ERROR:
            raise SourceConnectionError(f"WebSocket error: {self._ws.exception()}")
        raise SourceConnectionError(f"unexpected WebSocket message type {msg.type}")

    async def call(self, command: str, **params: Any) -> Any:
        assert self._ws is not None
        self._id += 1
        mid = self._id
        try:
            await self._ws.send_json({"id": mid, "type": command, **params})
        except (aiohttp.ClientError, ConnectionResetError) as exc:
            raise SourceConnectionError(f"send failed: {exc}") from exc
        while True:
            msg = await self._recv()
            if msg.get("id") != mid or msg.get("type") != "result":
                continue
            if not msg.get("success"):
                err = msg.get("error") or {}
                raise CommandError(command, err.get("code", "unknown"), err.get("message", ""))
            return msg.get("result")

    async def rest_post(self, path: str, payload: dict) -> int:
        """POST to the REST API in the same session; returns the HTTP status."""
        assert self._session is not None
        async with self._session.post(
            self.url + path,
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
            ssl=True if self.verify_ssl else False,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        ) as resp:
            return resp.status
