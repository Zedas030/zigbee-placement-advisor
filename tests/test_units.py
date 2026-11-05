"""Smaller units: channels, config, ZHA parsing, storage, translations."""
import pytest

from zpa import channels
from zpa.analysis import classify_outages, parent_history
from zpa.config import ConfigError, defaults, load
from zpa.db import DB
from zpa.i18n import Translator, check_complete
from zpa.model import Device, Link
from zpa.sources.base import SourceSchemaError
from zpa.sources.zha import choose_primary_entity, parse_devices, parse_history, validate_devices

import fake_ha


# ---------------------------------------------------------------- channels
@pytest.mark.parametrize("zigbee,wifi,status,mhz", [
    (20, 11, "ok", 12),        # 2450 vs 2462 MHz: the classic "between Wi-Fi 6 and 11" choice
    (15, 1, "ok", 13),         # 2425 vs 2412
    (15, 6, "ok", 12),
    (25, 11, "ok", 13),
    (20, 9, "overlap", 2),     # 2450 vs 2452
    (11, 1, "overlap", 7),     # 2405 vs 2412
    (21, 11, "overlap", 7),
    (16, 6, "overlap", 7),
    (25, 13, "overlap", 3),
])
def test_channel_overlap(zigbee, wifi, status, mhz):
    got = channels.check(zigbee, wifi)
    assert (got.status, got.distance_mhz) == (status, mhz)


def test_channel_unknown_and_spacing():
    assert channels.check(None, 11).status == "unknown"
    assert channels.check(20, None).status == "unknown"
    assert channels.check(10, 11).status == "unknown"  # not a Zigbee channel
    # Zigbee and Wi-Fi centres are always 2, 3, 7, 8, 12, 13 ... MHz apart: <=8 is inside the Wi-Fi lobe
    dists = {channels.check(z, w).distance_mhz for z in range(11, 27) for w in range(1, 14)}
    assert {2, 3, 7, 8, 12, 13} <= dists and not dists & {9, 10, 11}


def test_parse_fritz_exporter():
    text = ('# HELP fritz_wifi_channel x\nfritz_wifi_channel{friendly_name="router.example",wifi_index="1",'
            'wifi_name="2.4GHz"} 11.0\nfritz_wifi_channel{wifi_index="2",wifi_name="5GHz"} 64.0\n')
    assert channels.parse_exporter(text, "fritz_wifi_channel", "2.4GHz") == 11
    assert channels.parse_exporter(text, "fritz_wifi_channel", "6GHz") is None


def test_best_wifi_channels():
    # Zigbee 20 = 2450 MHz: Wi-Fi 1 (2412) is 38 MHz away, then 13 (2472) and 12 (2467)
    assert channels.best_wifi_channels(20) == [(1, 38), (13, 22), (12, 17)]
    assert channels.best_wifi_channels(20, limit=1) == [(1, 38)]
    assert channels.best_wifi_channels(11) == [(13, 67), (12, 62), (11, 57)]  # nothing below 2405 MHz
    assert channels.best_wifi_channels(25) == [(1, 63), (2, 58), (3, 53)]
    assert channels.best_wifi_channels(15) == [(13, 47), (1, 13), (12, 42)]
    for z in range(11, 27):
        assert all(channels.check(z, w).status == "ok" for w, _ in channels.best_wifi_channels(z, limit=13))
    assert channels.best_wifi_channels(None) == [] and channels.best_wifi_channels(5) == []


def test_channel_history_counts_changes_and_overlap_time():
    h, day, now = 3600, 86400, 100 * 86400
    # hopping 1 -> 7 -> 11 -> 1 within the week; 7 overlaps Zigbee 20
    readings = [(now - 6 * day, 1, 20), (now - 6 * day + 12 * h, 1, 20), (now - 5 * day, 7, 20),
                (now - 5 * day + 5 * h, 7, 20), (now - 4 * day, 11, 20), (now - 3 * day, 1, None)]
    hist = channels.history(readings, now, max_gap=10 * day, zigbee=20)
    assert hist.changes == 3 and hist.auto and hist.channels == [1, 7, 11]
    assert hist.last_change == now - 3 * day and hist.since == now - 6 * day
    assert hist.overlap_share == pytest.approx(1 / 6)  # 7 held for 1 of the 6 covered days
    # a reading before the window is the starting state: a change right after the window start counts
    hist = channels.history([(now - 8 * day, 7, 20), (now - 6 * day, 1, 20)], now, max_gap=10 * day)
    assert hist.changes == 1 and not hist.auto and hist.since == now - 7 * day
    assert hist.overlap_share == pytest.approx(1 / 7)
    # stable channel: no change, no overlap
    hist = channels.history([(now - 2 * day, 1, 20), (now - h, 1, 20)], now)
    assert hist.changes == 0 and hist.overlap_share == 0 and hist.channels == [1]
    # gaps longer than max_gap (collector down) count neither as overlap nor as clean time
    hist = channels.history([(now - 3 * day, 7, 20), (now - 2 * day, 1, 20)], now, max_gap=h)
    assert hist.overlap_share == pytest.approx(0.5)
    assert channels.history([], now).readings == 0 and channels.history([], now).overlap_share is None
    # stored without Zigbee channel -> falls back to the current one; unknown is not counted
    assert channels.history([(now - h, 7, None)], now, zigbee=20).overlap_share == 1
    assert channels.history([(now - h, 7, None)], now).overlap_share is None


def test_wifi_readings_storage(tmp_path):
    db = DB(tmp_path / "t.db")
    assert db.get_meta("schema_version") == 2
    for ts, w in [(10, 1), (20, 7), (30, 11), (40, 1)]:
        db.add_wifi_reading(ts, w, 20)
    assert [r[0] for r in db.wifi_readings(25)] == [20, 30, 40]  # includes the state at `since`
    assert db.purge(raw_before=25, daily_before_day="0000")["wifi_readings"] == 2
    assert [r[1] for r in db.wifi_readings(0)] == [11, 1]


def test_schema_upgrade_from_v1(tmp_path):
    db = DB(tmp_path / "t.db")
    db.conn.execute("DROP TABLE wifi_readings")
    db.set_meta("schema_version", 1)
    db.close()
    db = DB(tmp_path / "t.db")
    assert db.get_meta("schema_version") == 2
    db.add_wifi_reading(1, 1, 20)
    assert db.wifi_readings(0) == [(1.0, 1, 20)]


# ------------------------------------------------------------------ config
def test_defaults_are_valid():
    cfg = defaults()
    assert cfg["collector"]["interval_minutes"] == 30


def test_config_file_and_errors(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("language: de\nnotify:\n  services: [mobile_app_phone]\n", encoding="utf-8")
    cfg, token = load(p, env={"HA_TOKEN": " abc "})
    assert cfg["language"] == "de" and token == "abc"
    assert cfg["notify"]["services"] == ["mobile_app_phone"]
    assert cfg["notify"]["weekly_report"]["weekday"] == "sunday"  # defaults merged
    for bad, msg in [
        ("colector: {}\n", "unknown setting"),
        ("collector: {interval_minutes: 1}\n", "between"),
        ("thresholds: {weak_lqi_median: 200}\n", "anchor_min_lqi"),
        ("wifi: {source: manual}\n", "manual_channel"),
        ("notify: {services: ['notify.x']}\n", "notify.services"),
        ("timezone: Mars/Base\n", "timezone"),
        ("retention: {raw_days: 7}\n", "raw_days"),
        ("ui: {home_url: 'dashboard.local'}\n", "ui.home_url"),
        ("ui: {home_label: ''}\n", "ui.home_label"),
        ("[1, 2]\n", "mapping"),
    ]:
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigError, match=msg):
            load(p, env={})


def test_example_configs_are_valid():
    import pathlib

    root = pathlib.Path(__file__).parent.parent
    for name in ["config.example.yaml"] + (["local/config.yaml"] if (root / "local/config.yaml").exists() else []):
        cfg, _ = load(root / name, env={})
        assert cfg["web"]["port"] == 8099


# --------------------------------------------------------------------- ZHA
def test_parse_zha_devices():
    raw = fake_ha.zha_devices()
    validate_devices(raw)
    devices, links = parse_devices(raw, {"living": "Living room", "bed": "Bedroom"},
                                   {"sensor.bed_motion_battery": "diagnostic"})
    by = {d.name: d for d in devices}
    assert by["Coordinator"].device_type == "Coordinator"
    assert by["Bedroom motion"].device_type == "EndDevice" and by["Bedroom motion"].area == "Bedroom"
    assert by["Bedroom motion"].primary_entity == "binary_sensor.bed_motion"
    assert by["Plug"].lqi == 200 and by["Plug"].rssi == -50
    assert any(l.relationship == "Child" and l.neighbor == by["Bedroom motion"].ieee for l in links)
    assert all(l.lqi is not None for l in links)


def test_schema_changes_are_detected():
    raw = fake_ha.zha_devices()
    del raw[1]["neighbors"]
    with pytest.raises(SourceSchemaError, match="neighbors"):
        validate_devices(raw)
    raw = fake_ha.zha_devices()
    raw[0]["device_type"] = "Hub"
    with pytest.raises(SourceSchemaError):
        validate_devices(raw)
    with pytest.raises(SourceSchemaError):
        validate_devices({"devices": []})


def test_primary_entity_choice():
    assert choose_primary_entity(["button.x_identify", "sensor.x_temp", "binary_sensor.x"],
                                 {"button.x_identify": "config"}) == "binary_sensor.x"
    assert choose_primary_entity([], {}) is None


def test_parse_history_compressed_and_full():
    raw = {"binary_sensor.a": [{"s": "off", "lu": 100.0}, {"s": "unavailable", "lu": 200.0},
                               {"s": "on", "lu": 300.0, "lc": 300.0}],
           "sensor.b": [{"state": "unavailable", "last_changed": "2026-01-01T00:00:00+00:00"}]}
    out = parse_history(raw, {"binary_sensor.a": "A", "sensor.b": "B"})
    assert out["A"] == [(100.0, True), (200.0, False), (300.0, True)]
    assert out["B"][0][1] is False


# ------------------------------------------------------------------ analysis
def test_classify_outages_separates_restarts():
    outs = [{"ieee": i, "start_ts": 1000 + k, "end_ts": 1100} for k, i in enumerate("abcd")]
    outs += [{"ieee": "a", "start_ts": 5000, "end_ts": 9000}, {"ieee": "b", "start_ts": 7000, "end_ts": 7010}]
    dev, sys_events = classify_outages(outs, 4, 120, 0.8, 60, 10_000)
    assert sys_events == [(1000.0, 4)]
    assert dev == [("a", 5000.0)]  # b's 10 s blip is below min_outage_seconds


def test_parent_history_latest_claim_wins():
    links = [
        {"ts": 1, "reporter": "R1", "neighbor": "X", "relationship": "Child"},
        {"ts": 2, "reporter": "R1", "neighbor": "X", "relationship": "Child"},  # unchanged
        {"ts": 3, "reporter": "X", "neighbor": "R2", "relationship": "Parent"},
        {"ts": 4, "reporter": "R2", "neighbor": "Y", "relationship": "Sibling"},
    ]
    assert parent_history(links) == {"X": [(1.0, "R1"), (3.0, "R2")]}


# ------------------------------------------------------------------ storage
def dev(ieee, last_seen, lqi=100, available=True):
    return Device(ieee=ieee, name=ieee, device_type="Router", power_source="Mains", available=available,
                  lqi=lqi, last_seen=last_seen)


def test_samples_only_stored_when_device_sent_something(tmp_path):
    db = DB(tmp_path / "t.db")
    assert db.add_device_samples([dev("a", "t1"), dev("b", "t1")], 1) == 2
    assert db.add_device_samples([dev("a", "t1"), dev("b", "t2")], 2) == 1
    assert db.add_device_samples([dev("a", "t1", available=False)], 3) == 1


def test_link_tables_stored_only_when_changed(tmp_path):
    db = DB(tmp_path / "t.db")
    t1 = [Link("r", "a", 100, "Sibling", 1), Link("r", "b", 90, "Child", 2)]
    assert db.add_link_samples(t1, 1) == 2
    assert db.add_link_samples(list(reversed(t1)), 2) == 0
    assert db.add_link_samples([Link("r", "a", 101, "Sibling", 1)], 3) == 1


def test_availability_state_machine_is_idempotent(tmp_path):
    db = DB(tmp_path / "t.db")
    tr = [(10.0, True), (20.0, False), (30.0, True), (40.0, False)]
    assert db.apply_availability("a", tr, "history") == 2
    assert db.apply_availability("a", [(40.0, False)], "history") == 0  # overlapping window: no duplicate
    assert db.apply_availability("a", [(50.0, True)], "history") == 0
    rows = db.outages(0)
    assert [(r["start_ts"], r["end_ts"]) for r in rows] == [(20.0, 30.0), (40.0, 50.0)]


def test_retention_and_daily_aggregation(tmp_path):
    db = DB(tmp_path / "t.db")
    day = 86400
    db.add_device_samples([dev("a", "x1", 100)], 1 * day + 10)
    db.add_device_samples([dev("a", "x2", 120)], 1 * day + 20)
    db.add_device_samples([dev("a", "x3", 130)], 50 * day)
    db.add_link_samples([Link("a", "b", 99, "Sibling", 1)], 1 * day)
    db.apply_availability("a", [(1 * day + 30, False), (1 * day + 90, True)], "history")
    assert db.aggregate_days([("1970-01-02", day, 2 * day)]) == 1
    assert db.aggregate_days([("1970-01-02", day, 2 * day)]) == 0  # not twice
    counts = db.purge(raw_before=10 * day, daily_before_day="1970-01-01")
    assert counts["device_samples"] == 2 and counts["link_samples"] == 1 and counts["outages"] == 1
    assert len(db.device_samples(0)) == 1
    row = db.daily_stats("a", "1970-01-01")[0]
    assert row["lqi_median"] == 110 and row["samples"] == 2 and row["outages"] == 1
    db.purge(raw_before=0, daily_before_day="1970-01-03")
    assert db.daily_stats("a", "1970-01-01") == []


def test_room_setup(tmp_path):
    db = DB(tmp_path / "t.db")
    db.sync_rooms(["A", "B", "C"], "B")
    assert [r["name"] for r in db.rooms() if r["coordinator"]] == ["B"]
    db.save_room_setup(["A", "B"], "A", [("B", "A"), ("A", "A"), ("A", "Z")], 5)
    assert db.adjacency() == {("A", "B")}
    assert {r["name"]: r["enabled"] for r in db.rooms()} == {"A": 1, "B": 1, "C": 0}
    with pytest.raises(ValueError):
        db.save_room_setup(["A"], "Z", [], 6)


# -------------------------------------------------------------- translations
def test_translations_complete_and_formatting():
    assert check_complete() == []
    t = Translator("de")
    assert "Flur" in t("rec_ok", room="Flur", count=4, level=t("level_medium"))
    assert "mittel" in t("rec_ok", room="Flur", count=4, level=t("level_medium"))
    assert Translator("xx")("rec_title") == "Recommendation"
