"""End-to-end: the real service against a fake Home Assistant + fake fritz_exporter.

Covers collection, history backfill, room setup via the web UI, metrics, reports,
self-monitoring (API change, bad token, HA down, stale data), the channel warning,
new-router detection with the before/after evaluation, retention, and the
guarantee that only read-only Home Assistant commands are sent.
"""
import asyncio
import datetime as dt
import json
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

import fake_ha
from zpa.config import defaults
from zpa.metrics import attach
from zpa.service import App
from zpa.web import create_web_app

TZ = ZoneInfo("Europe/Paris")
T_START = dt.datetime(2030, 1, 1, 10, 0, tzinfo=TZ).timestamp()  # a Tuesday


def make_cfg(base_url: str, tmp_path, **overrides) -> dict:
    cfg = defaults()
    cfg["timezone"] = "Europe/Paris"
    cfg["data_dir"] = str(tmp_path)
    cfg["home_assistant"]["url"] = base_url
    cfg["home_assistant"]["timeout_seconds"] = 5
    cfg["wifi"]["exporter_url"] = base_url + "/metrics"
    cfg["notify"]["services"] = ["mobile_app_phone"]
    cfg["web"]["public_url"] = "http://advisor.example.local:8099"
    for dotted, value in overrides.items():
        node = cfg
        *parents, leaf = dotted.split("__")
        for p in parents:
            node = node[p]
        node[leaf] = value
    return cfg


class Env:
    def __init__(self, tmp_path, **overrides):
        self.tmp_path = tmp_path
        self.overrides = overrides
        self.fake = fake_ha.FakeHA()
        self.clock = [T_START]
        self.fake.clock = lambda: self.clock[0]

    async def __aenter__(self):
        self.server = TestServer(self.fake.app)
        await self.server.start_server()
        self.base = str(self.server.make_url("")).rstrip("/")
        self.cfg = make_cfg(self.base, self.tmp_path, **self.overrides)
        self.app = App(self.cfg, fake_ha.TOKEN, clock=lambda: self.clock[0])
        attach(self.app)
        self.client = TestClient(TestServer(create_web_app(self.app)))
        await self.client.start_server()
        return self

    async def __aexit__(self, *exc):
        await self.client.close()
        await self.server.close()
        self.app.db.close()

    async def cycle(self, seconds: float = 1800, lqi: dict | None = None, tick: bool = True) -> dict:
        self.clock[0] += seconds
        if tick:
            self.fake.tick(lqi)
        return await self.app.collect_once()

    def notes(self, title_part: str) -> list[dict]:
        return [n for n in self.fake.notifications if title_part in n["title"]]


def run(coro):
    return asyncio.run(coro)


def test_collection_web_ui_metrics_and_report(tmp_path):
    async def main():
        async with Env(tmp_path) as env:
            h = env.fake.history
            t = T_START
            h["binary_sensor.bed_motion"] = [  # one real outage 5h ago, plus a restart 3h ago
                {"s": "on", "lu": t - 6 * 3600}, {"s": "unavailable", "lu": t - 5 * 3600},
                {"s": "off", "lu": t - 4 * 3600}, {"s": "unavailable", "lu": t - 3 * 3600},
                {"s": "off", "lu": t - 3 * 3600 + 60}]
            h["switch.plug"] = [{"s": "on", "lu": t - 6 * 3600}, {"s": "unavailable", "lu": t - 3 * 3600 + 3},
                                {"s": "on", "lu": t - 3 * 3600 + 70}]
            s = await env.cycle()
            assert s["ok"], s
            assert s["devices"] == 3 and s["device_samples"] == 3 and s["link_samples"] == 3
            assert s["zigbee_channel"] == 20 and s["wifi_channel"] == 11
            assert s["new_outages"] == 3

            stats = env.app.current_stats()
            motion = stats.devices[fake_ha.IEEE["motion"]]
            assert motion.outages == 1                      # the real one
            assert len(stats.system_outage_events) == 1     # the restart is not held against devices
            assert stats.devices[fake_ha.IEEE["plug"]].outages == 0
            assert motion.parent == fake_ha.IEEE["plug"]

            # a second cycle without traffic stores nothing new (no double counting of stale LQI)
            s2 = await env.cycle(tick=False)
            assert s2["device_samples"] == 0 and s2["link_samples"] == 0

            c = env.client
            r = await c.get("/")
            body = await r.text()
            assert r.status == 200 and "Room setup missing" in body
            r = await c.post("/setup", data=[("enabled", "Living room"), ("enabled", "Hallway"),
                                             ("enabled", "Bedroom"), ("coordinator", "Living room"),
                                             ("pair", "Hallway|Living room"), ("pair", "Bedroom|Hallway")],
                             allow_redirects=False)
            assert r.status == 303
            rm = env.app.room_model()
            assert rm.confirmed and rm.coordinator_room == "Living room"
            assert rm.adjacency == {("Hallway", "Living room"), ("Bedroom", "Hallway")}
            assert "Kitchen" not in rm.rooms
            r = await c.get("/setup")
            assert r.status == 200 and "Hallway ↔ Living room" in await r.text()

            r = await c.get("/devices")
            assert r.status == 200 and "Bedroom motion" in await r.text()
            r = await c.get(f"/device/{fake_ha.IEEE['motion']}?days=7")
            body = await r.text()
            assert r.status == 200 and "<svg" in body and "Plug" in body
            assert (await c.get("/device/nope")).status == 404

            r = await c.get("/metrics")
            m = await r.text()
            assert 'zpa_device_lqi{device="Bedroom motion"' in m or 'device="Bedroom motion"' in m
            assert "zpa_wifi_channel 11.0" in m and "zpa_zigbee_channel 20.0" in m
            assert "zpa_collector_last_success_timestamp_seconds" in m
            assert 'zpa_recommendation_status{status="insufficient_data"} 1.0' in m

            r = await c.get("/healthz")
            assert r.status == 200 and (await r.json())["healthy"] is True
            state = await (await c.get("/api/state")).json()
            assert state["recommendation"]["status"] == "insufficient_data"
            assert any("days of data" in line for line in state["text"])

            r = await c.post("/reports/now", allow_redirects=False)
            assert r.status == 303
            r = await c.get(r.headers["Location"])
            body = await r.text()
            assert r.status == 200 and "Zigbee weekly report" in body and "<style>" in body
            assert (await c.get("/report/latest")).status == 200
            assert env.fake.notifications == []  # "report now" does not notify

            assert set(env.fake.commands) <= fake_ha.READ_ONLY_COMMANDS
            assert "zha/topology/update" not in env.fake.commands
    run(main())


def test_self_monitoring_alerts_instead_of_failing_silently(tmp_path):
    async def main():
        async with Env(tmp_path) as env:
            assert (await env.cycle())["ok"]

            env.fake.mode = "schema"  # e.g. ZHA renamed a field after an update
            s = await env.cycle()
            assert not s["ok"] and s["kind"] == "schema"
            assert len(env.notes("problem")) == 1 and "neighbors" in env.notes("problem")[0]["message"]
            await env.cycle()
            assert len(env.notes("problem")) == 1  # no spam while it persists
            env.fake.mode = "ok"
            assert (await env.cycle())["ok"]
            assert len(env.notes("resolved")) == 1

            env.fake.mode = "unknown_command"  # ZHA not loaded / command removed
            assert (await env.cycle())["kind"] == "schema"
            env.fake.mode = "bad_token"
            assert (await env.cycle())["kind"] == "auth"
            env.fake.mode = "empty"
            assert (await env.cycle())["kind"] == "no_data"
            env.fake.mode = "ok"
            await env.cycle()
            assert env.app.monitor.open_incidents() == []

            # Home Assistant unreachable: alert after 2 cycles, delivered once it is back
            good = env.app.source.client_args["url"]
            env.app.source.client_args["url"] = "http://127.0.0.1:9"
            env.app.notifier.url = "http://127.0.0.1:9"
            sent_before = len(env.fake.notifications)
            assert (await env.cycle())["kind"] == "connection"
            assert [r["key"] for r in env.app.monitor.open_incidents()] == []
            await env.cycle()
            assert [r["key"] for r in env.app.monitor.open_incidents()] == ["connection"]
            assert len(env.fake.notifications) == sent_before  # cannot be delivered while HA is down
            env.app.source.client_args["url"] = good
            env.app.notifier.url = good
            assert (await env.cycle())["ok"]
            texts = [n["message"] for n in env.fake.notifications[sent_before:]]
            assert any("not been reachable" in x for x in texts) and any(x.startswith("Resolved") for x in texts)

            # no device reports anything for longer than stale_minutes
            for _ in range(7):
                await env.cycle(tick=False)
            assert "stale" in [r["key"] for r in env.app.monitor.open_incidents()]
            await env.cycle()
            assert "stale" not in [r["key"] for r in env.app.monitor.open_incidents()]

            # Wi-Fi channel source broken -> incident; overlap -> one warning
            env.app.cfg["wifi"]["exporter_url"] = env.base + "/nothing"
            await env.cycle()
            assert "wifi" in [r["key"] for r in env.app.monitor.open_incidents()]
            env.app.cfg["wifi"]["exporter_url"] = env.base + "/metrics"
            env.fake.wifi_channel = 9  # 2452 MHz vs Zigbee 20 at 2450 MHz
            await env.cycle()
            await env.cycle()
            assert len(env.notes("channel overlap")) == 1
            assert "overlaps" in env.notes("channel overlap")[0]["message"]
            assert "fixed 1" in env.notes("channel overlap")[0]["message"]  # concrete fix for Zigbee 20
            body = await (await env.client.get("/")).text()
            assert "tone-bad" in body and "Best Wi-Fi channels for Zigbee 20: 1 (38 MHz apart)" in body
            # back to 11: two changes in 7 days -> looks like automatic channel selection (amber, still a fix)
            env.fake.wifi_channel = 11
            await env.cycle()
            view = env.app.channel_view()
            assert view["status"] == "ok" and view["auto"] and view["tone"] == "warn" and view["changes"] == 2
            body = await (await env.client.get("/")).text()
            assert "automatic channel selection" in body and "Set the 2.4 GHz Wi-Fi channel to a fixed 1" in body
            assert "zpa_wifi_channel_changes_7d 2.0" in await (await env.client.get("/metrics")).text()

            # a configured notify service that does not exist
            env.app.cfg["notify"]["services"].append("mobile_app_gone")
            env.app.db.set_meta("notify_check_ts", 0)
            await env.cycle()
            assert "notify_mobile_app_gone" in [r["key"] for r in env.app.monitor.open_incidents()]
            assert set(env.fake.commands) <= fake_ha.READ_ONLY_COMMANDS
    run(main())


def test_recommendation_new_router_before_after_weekly_report_and_retention(tmp_path):
    async def main():
        async with Env(tmp_path, recommendation__min_devices=3, recommendation__min_days=1,
                       recommendation__min_measurements=10, before_after__min_samples=5) as env:
            app = env.app
            await env.cycle(lqi={"motion": 50})
            app.db.save_room_setup(["Living room", "Hallway", "Bedroom", "Kitchen"], "Living room",
                                   [("Living room", "Hallway"), ("Hallway", "Bedroom"), ("Living room", "Kitchen")],
                                   env.clock[0])
            for _ in range(36):  # 3 days, motion sensor weak
                await env.cycle(7200, lqi={"motion": 50})
            rec = app.recommendation()
            assert rec.status == "ok", rec.to_dict()
            assert rec.best.room == "Hallway" and rec.best.count == 1
            lines = app.recommendation_text(rec)
            assert lines[0].startswith("A router device in Hallway would probably stabilise 1")

            env.fake.add_new_router()  # the user follows the advice
            s = await env.cycle(7200, lqi={"motion": 150})
            assert s["new_devices"] == [fake_ha.IEEE["new"]]
            ev = app.db.q("SELECT * FROM router_events")
            assert len(ev) == 1 and ev[0]["room"] == "Hallway" and ev[0]["recommended_room"] == "Hallway"

            for _ in range(12 * 8 + 6):  # 8.25 days with better link
                await env.cycle(7200, lqi={"motion": 150})
            await app.scheduler_tick(env.clock[0])
            res = json.loads(app.db.q("SELECT result FROM router_events")[0]["result"])
            assert res["verdict"] == "worked" and res["as_recommended"] is True
            note = env.notes("new router evaluated")
            assert len(note) == 1 and "worked" in note[0]["message"] and "recommended room" in note[0]["message"]

            # weekly report on Sunday 18:00 local time
            now = dt.datetime.fromtimestamp(env.clock[0], TZ)
            sunday = now + dt.timedelta(days=(6 - now.weekday()) % 7)
            env.clock[0] = sunday.replace(hour=18, minute=1).timestamp()
            await app.scheduler_tick(env.clock[0])
            await app.scheduler_tick(env.clock[0] + 120)  # not twice
            weekly = env.notes("weekly report")
            assert len(weekly) == 1
            assert weekly[0]["data"]["url"].startswith("http://advisor.example.local:8099/reports/2030-W")
            rows = app.db.q("SELECT * FROM reports")
            assert len(rows) == 1 and "weak" in rows[0]["summary"]

            # retention: daily aggregation + purge of old raw data
            app.cfg["retention"]["raw_days"] = 7
            out = app.maintenance(env.clock[0])
            assert out["aggregated"] >= 1 and out["purged"]["device_samples"] > 0
            assert min(r["ts"] for r in app.db.device_samples(0)) >= env.clock[0] - 7 * 86400
            r = await env.client.get(f"/device/{fake_ha.IEEE['motion']}?days=365")
            assert r.status == 200 and "<polyline" in await r.text()  # long range comes from daily stats
            assert set(env.fake.commands) <= fake_ha.READ_ONLY_COMMANDS
    run(main())


def test_german_ui(tmp_path):
    async def main():
        async with Env(tmp_path, language="de") as env:
            await env.cycle()
            body = await (await env.client.get("/")).text()
            assert "Zigbee-Platzberater" in body and "Raummodell fehlt" in body and 'lang="de"' in body
            body = await (await env.client.get("/setup")).text()
            assert "Coordinator steht hier" in body
    run(main())


def test_dashboard_link_and_theme(tmp_path):
    async def main():
        async with Env(tmp_path, ui__home_url="http://dashboard.example.local/", ui__home_label="House") as env:
            await env.cycle()
            body = await (await env.client.get("/")).text()
            assert 'href="http://dashboard.example.local/"' in body and ">House<" in body
            assert "/static/homelab-theme.css" in body and 'class="card hero' in body
            assert "No overlap: Zigbee 20 vs Wi-Fi 11, 12 MHz apart." in body and "tone-ok" in body
            r = await env.client.get("/static/homelab-theme.css")
            assert r.status == 200 and "--accent" in await r.text()
        async with Env(tmp_path / "b") as env:
            await env.cycle()
            assert "home-btn" not in await (await env.client.get("/")).text()
    run(main())
