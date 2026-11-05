"""A fake Home Assistant (WebSocket + REST notify) and a fake fritz_exporter for end-to-end tests.

All data is synthetic and anonymised.
"""
from __future__ import annotations

import copy
import datetime as dt

from aiohttp import WSMsgType, web

TOKEN = "test-token"
IEEE = {
    "coord": "00:12:4b:00:00:00:00:01",
    "plug": "00:12:4b:00:00:00:00:02",
    "motion": "00:12:4b:00:00:00:00:03",
    "new": "00:12:4b:00:00:00:00:04",
}


def zha_devices() -> list[dict]:
    return [
        {"ieee": IEEE["coord"], "nwk": 0, "name": "Coordinator", "user_given_name": None, "device_type": "Coordinator",
         "power_source": "Mains", "available": True, "lqi": 255, "rssi": 0, "last_seen": "2026-01-01T00:00:00",
         "area_id": "living", "manufacturer": "Example", "model": "Dongle", "active_coordinator": True,
         "entities": [], "routes": [],
         "neighbors": [{"ieee": IEEE["plug"], "nwk": "0x1111", "device_type": "Router", "relationship": "Sibling",
                        "lqi": "210", "depth": "1"}]},
        {"ieee": IEEE["plug"], "nwk": 4369, "name": "Example Plug", "user_given_name": "Plug", "device_type": "Router",
         "power_source": "Mains", "available": True, "lqi": 200, "rssi": -50, "last_seen": "2026-01-01T00:00:00",
         "area_id": "living", "manufacturer": "Example", "model": "Plug", "active_coordinator": False,
         "entities": [{"entity_id": "switch.plug", "name": "Plug"}], "routes": [],
         "neighbors": [{"ieee": IEEE["coord"], "nwk": "0x0000", "device_type": "Coordinator", "relationship": "Parent",
                        "lqi": "220", "depth": "0"},
                       {"ieee": IEEE["motion"], "nwk": "0x2222", "device_type": "EndDevice", "relationship": "Child",
                        "lqi": "70", "depth": "2"}]},
        {"ieee": IEEE["motion"], "nwk": 8738, "name": "Example Motion", "user_given_name": "Bedroom motion",
         "device_type": "EndDevice", "power_source": "Battery or Unknown", "available": True, "lqi": 60, "rssi": -80,
         "last_seen": "2026-01-01T00:00:00", "area_id": "bed", "manufacturer": "Example", "model": "Motion",
         "active_coordinator": False,
         "entities": [{"entity_id": "binary_sensor.bed_motion", "name": "Motion"},
                      {"entity_id": "sensor.bed_motion_battery", "name": "Battery"}],
         "routes": [], "neighbors": []},
    ]


READ_ONLY_COMMANDS = {
    "zha/devices", "zha/network/settings", "config/area_registry/list", "config/entity_registry/list",
    "history/history_during_period", "get_services",
}


class FakeHA:
    def __init__(self) -> None:
        self.devices = zha_devices()
        self.mode = "ok"  # ok | bad_token | schema | unknown_command | empty
        self.history: dict[str, list[dict]] = {}
        self.notify_services = ["mobile_app_phone", "persistent_notification"]
        self.notifications: list[dict] = []
        self.commands: list[str] = []
        self.wifi_channel = 11
        self.zigbee_channel = 20
        self.clock = lambda: 0.0
        self.app = web.Application()
        self.app.add_routes([
            web.get("/api/websocket", self.ws),
            web.post("/api/services/notify/{service}", self.notify),
            web.get("/metrics", self.metrics),
        ])

    # -- helpers to shape the simulated network ------------------------------
    def device(self, key: str) -> dict:
        return next(d for d in self.devices if d["ieee"] == IEEE[key])

    def tick(self, lqi: dict[str, int] | None = None) -> None:
        """Simulate traffic: every device sends a frame (new last_seen) with the given LQI."""
        stamp = dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        for d in self.devices:
            d["last_seen"] = stamp
        for key, value in (lqi or {}).items():
            self.device(key)["lqi"] = value

    def add_new_router(self) -> None:
        self.devices.append({
            "ieee": IEEE["new"], "nwk": 13107, "name": "Example Plug 2", "user_given_name": "New plug",
            "device_type": "Router", "power_source": "Mains", "available": True, "lqi": 190, "rssi": -55,
            "last_seen": "2026-01-01T00:00:00", "area_id": "hall", "manufacturer": "Example", "model": "Plug",
            "active_coordinator": False, "entities": [{"entity_id": "switch.new_plug", "name": "Plug"}],
            "routes": [], "neighbors": [{"ieee": IEEE["motion"], "nwk": "0x2222", "device_type": "EndDevice",
                                         "relationship": "Child", "lqi": "150", "depth": "2"}]})

    # -- handlers ----------------------------------------------------------------
    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2099.1.0"})
        msg = await ws.receive_json()
        if msg.get("access_token") != TOKEN or self.mode == "bad_token":
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2099.1.0"})
        async for raw in ws:
            if raw.type != WSMsgType.TEXT:
                break
            m = raw.json()
            self.commands.append(m["type"])
            await ws.send_json(self.answer(m))
        return ws

    def answer(self, m: dict) -> dict:
        def ok(result):
            return {"id": m["id"], "type": "result", "success": True, "result": result}

        t = m["type"]
        if t == "zha/devices":
            if self.mode == "unknown_command":
                return {"id": m["id"], "type": "result", "success": False,
                        "error": {"code": "unknown_command", "message": "Unknown command."}}
            if self.mode == "empty":
                return ok([])
            devs = copy.deepcopy(self.devices)
            if self.mode == "schema":
                for d in devs:
                    d.pop("neighbors", None)
                    d["neighbours_v2"] = []
            return ok(devs)
        if t == "zha/network/settings":
            return ok({"radio_type": "ezsp", "settings": {"network_info": {"channel": self.zigbee_channel}}})
        if t == "config/area_registry/list":
            return ok([{"area_id": "living", "name": "Living room"}, {"area_id": "hall", "name": "Hallway"},
                       {"area_id": "bed", "name": "Bedroom"}, {"area_id": "kitchen", "name": "Kitchen"}])
        if t == "config/entity_registry/list":
            return ok([{"entity_id": "sensor.bed_motion_battery", "platform": "zha", "entity_category": "diagnostic"},
                       {"entity_id": "binary_sensor.bed_motion", "platform": "zha", "entity_category": None},
                       {"entity_id": "switch.plug", "platform": "zha", "entity_category": None}])
        if t == "history/history_during_period":
            start = dt.datetime.fromisoformat(m["start_time"]).timestamp()
            end = dt.datetime.fromisoformat(m["end_time"]).timestamp()
            out = {}
            for e in m["entity_ids"]:
                states = [s for s in self.history.get(e, []) if start <= s["lu"] < end]
                if states or e in self.history:
                    out[e] = states or [{"s": "on", "lu": start}]
            return ok(out)
        if t == "get_services":
            return ok({"notify": {s: {} for s in self.notify_services}})
        return {"id": m["id"], "type": "result", "success": False,
                "error": {"code": "unknown_command", "message": f"not faked: {t}"}}

    async def notify(self, request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return web.Response(status=401)
        service = request.match_info["service"]
        if service not in self.notify_services:
            return web.Response(status=400)
        self.notifications.append({"service": service, **(await request.json())})
        return web.json_response([])

    async def metrics(self, request: web.Request) -> web.Response:
        return web.Response(text=(
            '# HELP fritz_wifi_channel channel\n# TYPE fritz_wifi_channel gauge\n'
            f'fritz_wifi_channel{{friendly_name="router",wifi_index="1",wifi_name="2.4GHz"}} {self.wifi_channel}.0\n'
            'fritz_wifi_channel{friendly_name="router",wifi_index="2",wifi_name="5GHz"} 36.0\n'))
