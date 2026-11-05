"""Prometheus metrics (served at /metrics)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from . import channels

if TYPE_CHECKING:  # pragma: no cover
    from .recommend import Recommendation
    from .service import App

LABELS = ["ieee", "device", "room", "type"]
STATUS_VALUES = ["ok", "healthy", "insufficient_data", "setup_needed", "no_candidate"]
CHANNEL_VALUES = {channels.OK: 0, channels.OVERLAP: 2, channels.UNKNOWN: -1}


class Metrics:
    def __init__(self) -> None:
        r = self.registry = CollectorRegistry()
        self.lqi = Gauge("zpa_device_lqi", "Last LQI reported for the device", LABELS, registry=r)
        self.lqi_median = Gauge("zpa_device_lqi_median", "Median LQI over the analysis window", LABELS, registry=r)
        self.rssi = Gauge("zpa_device_rssi_dbm", "Last RSSI reported for the device", LABELS, registry=r)
        self.available = Gauge("zpa_device_available", "1 if the device is available", LABELS, registry=r)
        self.outages = Gauge("zpa_device_outages", "Device outages in the analysis window (HA restarts excluded)", LABELS, registry=r)
        self.outages_total = Gauge("zpa_device_outages_recorded_total", "All recorded device outages still in the database", LABELS, registry=r)
        self.parent_changes = Gauge("zpa_device_parent_changes", "Parent changes in the analysis window", LABELS, registry=r)
        self.weak = Gauge("zpa_device_weak", "1 if the device is classified as weak", LABELS, registry=r)
        self.samples = Gauge("zpa_device_samples", "Measurements in the analysis window", LABELS, registry=r)
        self.link = Gauge("zpa_link_lqi_median", "Median LQI of a measured neighbor link", ["a", "b"], registry=r)
        self.zigbee_channel = Gauge("zpa_zigbee_channel", "Zigbee channel", registry=r)
        self.wifi_channel = Gauge("zpa_wifi_channel", "2.4 GHz Wi-Fi channel", registry=r)
        self.channel_status = Gauge("zpa_channel_overlap", "0 ok, 2 overlap, -1 unknown", registry=r)
        self.rec_status = Gauge("zpa_recommendation_status", "1 for the current recommendation status", ["status"], registry=r)
        self.rec_room = Gauge("zpa_recommendation_devices", "Weak devices a new router in this room would stabilise", ["room", "confidence"], registry=r)
        self.weak_total = Gauge("zpa_weak_devices", "Number of weak devices", registry=r)
        self.devices_total = Gauge("zpa_devices", "Number of Zigbee devices", registry=r)
        self.data_days = Gauge("zpa_data_days", "Days of data in the analysis window", registry=r)
        self.measurements = Gauge("zpa_measurements", "Measurements in the analysis window", registry=r)
        self.last_success = Gauge("zpa_collector_last_success_timestamp_seconds", "Last successful collection", registry=r)
        self.failures = Counter("zpa_collector_failures", "Failed collection cycles", ["kind"], registry=r)
        self.incidents = Gauge("zpa_open_incidents", "Open self-monitoring incidents", registry=r)
        self.system_events = Gauge("zpa_system_outage_events", "System-wide outage events (HA/ZHA restarts) in the window", registry=r)

    def collection_failed(self, kind: str) -> None:
        self.failures.labels(kind=kind).inc()

    def update(self, app: "App", rec: "Recommendation | None" = None) -> None:
        stats = app.current_stats()
        rec = rec or app.recommendation(stats=stats)
        for g in (self.lqi, self.lqi_median, self.rssi, self.available, self.outages, self.outages_total,
                  self.parent_changes, self.weak, self.samples, self.link, self.rec_room):
            g.clear()
        totals = {r["ieee"]: r["n"] for r in app.db.q("SELECT ieee, COUNT(*) n FROM outages GROUP BY ieee")}
        names = {}
        for st in stats.devices.values():
            lbl = dict(ieee=st.ieee, device=st.name, room=st.room or "", type=st.info.device_type)
            names[st.ieee] = st.name
            last = app.db.last_device_sample(st.ieee)
            if last is not None:
                if last["lqi"] is not None:
                    self.lqi.labels(**lbl).set(last["lqi"])
                if last["rssi"] is not None:
                    self.rssi.labels(**lbl).set(last["rssi"])
                self.available.labels(**lbl).set(int(last["available"]))
            if st.median_lqi is not None:
                self.lqi_median.labels(**lbl).set(st.median_lqi)
            self.outages.labels(**lbl).set(st.outages)
            self.outages_total.labels(**lbl).set(totals.get(st.ieee, 0))
            self.parent_changes.labels(**lbl).set(st.parent_changes)
            self.weak.labels(**lbl).set(int(st.weak))
            self.samples.labels(**lbl).set(st.samples)
        for (a, b), v in stats.link_lqi.items():
            self.link.labels(a=names.get(a, a), b=names.get(b, b)).set(v)
        chk = app.channel_check()
        if chk.zigbee:
            self.zigbee_channel.set(chk.zigbee)
        if chk.wifi:
            self.wifi_channel.set(chk.wifi)
        self.channel_status.set(CHANNEL_VALUES[chk.status])
        for s in STATUS_VALUES:
            self.rec_status.labels(status=s).set(int(rec.status == s))
        for c in rec.candidates:
            self.rec_room.labels(room=c.room, confidence=c.confidence_level).set(c.count)
        self.weak_total.set(len(stats.weak()))
        self.devices_total.set(len(stats.devices))
        self.data_days.set(stats.days_covered)
        self.measurements.set(stats.measurements)
        self.system_events.set(len(stats.system_outage_events))
        last_ok = app.db.get_meta("last_success_ts")
        if last_ok:
            self.last_success.set(last_ok)
        self.incidents.set(len(app.monitor.open_incidents()))

    def render(self) -> bytes:
        return generate_latest(self.registry)


def attach(app: "App") -> Metrics:
    app.metrics = Metrics()
    return app.metrics
