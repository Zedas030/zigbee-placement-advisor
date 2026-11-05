"""Generates grafana/zigbee-placement-advisor.json (run: python grafana/build_dashboard.py)."""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "${datasource}"}
panels = []
_id = 0


def panel(kind, title, targets, x, y, w, h, **extra):
    global _id
    _id += 1
    p = {"id": _id, "type": kind, "title": title, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [dict(refId=chr(65 + i), datasource=DS, **t) for i, t in enumerate(targets)]}
    p.update(extra)
    panels.append(p)


def stat(title, expr, x, y, w=4, unit="none", mappings=None, thresholds=None, desc=""):
    panel("stat", title, [{"expr": expr, "instant": True}], x, y, w, 4, description=desc,
          fieldConfig={"defaults": {"unit": unit, "mappings": mappings or [],
                                    "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "green", "value": None}]}},
                       "overrides": []},
          options={"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"]}})


stat("Weak devices", "zpa_weak_devices", 0, 0,
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 1}, {"color": "red", "value": 3}])
stat("Zigbee devices", "zpa_devices", 4, 0)
stat("Channel check", "zpa_channel_overlap", 8, 0,
     mappings=[{"type": "value", "options": {"0": {"text": "OK", "color": "green"}, "2": {"text": "Overlap", "color": "red"},
                                             "-1": {"text": "unknown", "color": "gray"}}}])
stat("Last collection", "time() - zpa_collector_last_success_timestamp_seconds", 12, 0, unit="s",
     thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 3700}, {"color": "red", "value": 7300}],
     desc="Seconds since the last successful read of ZHA. Alert on this if Home Assistant itself is down.")
stat("Open problems", "zpa_open_incidents", 16, 0,
     thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}])
stat("Days of data", "zpa_data_days", 20, 0, unit="d")

ts_defaults = {"defaults": {"custom": {"lineWidth": 2, "showPoints": "never", "spanNulls": True}, "min": 0, "max": 255,
                            "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "green", "value": 80}]}},
               "overrides": []}
panel("timeseries", "LQI per device (last value)", [{"expr": 'zpa_device_lqi{type!="Coordinator"}', "legendFormat": "{{device}} ({{room}})"}],
      0, 4, 12, 9, fieldConfig=ts_defaults, options={"legend": {"displayMode": "table", "placement": "right", "calcs": ["min", "mean", "lastNotNull"]}})
panel("timeseries", "LQI median over the analysis window", [{"expr": 'zpa_device_lqi_median{type!="Coordinator"}', "legendFormat": "{{device}}"}],
      12, 4, 12, 9, fieldConfig=ts_defaults, options={"legend": {"displayMode": "list", "placement": "bottom"}})
panel("timeseries", "Outages in the analysis window (HA restarts excluded)", [{"expr": "zpa_device_outages", "legendFormat": "{{device}}"}],
      0, 13, 12, 8, fieldConfig={"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60}, "decimals": 0}, "overrides": []})
panel("state-timeline", "Availability", [{"expr": 'zpa_device_available{type!="Coordinator"}', "legendFormat": "{{device}}"}],
      12, 13, 12, 8, fieldConfig={"defaults": {"mappings": [{"type": "value", "options": {
          "0": {"text": "unavailable", "color": "red"}, "1": {"text": "available", "color": "green"}}}]}, "overrides": []})
panel("table", "Recommendation: weak devices a new router would stabilise",
      [{"expr": "zpa_recommendation_devices", "instant": True, "format": "table"}], 0, 21, 12, 7,
      transformations=[{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True, "instance": True, "job": True},
                                                       "renameByName": {"Value": "devices", "room": "room", "confidence": "confidence"}}}])
panel("timeseries", "Parent changes in the analysis window", [{"expr": "zpa_device_parent_changes", "legendFormat": "{{device}}"}],
      12, 21, 12, 7, fieldConfig={"defaults": {"decimals": 0}, "overrides": []})
panel("timeseries", "RSSI per device", [{"expr": 'zpa_device_rssi_dbm{type!="Coordinator"}', "legendFormat": "{{device}}"}],
      0, 28, 12, 7, fieldConfig={"defaults": {"unit": "dBm"}, "overrides": []})
panel("timeseries", "Collector failures", [{"expr": "increase(zpa_collector_failures_total[1h])", "legendFormat": "{{kind}}"}],
      12, 28, 12, 7, fieldConfig={"defaults": {"decimals": 0}, "overrides": []})

dashboard = {
    "uid": "zigbee-placement-advisor", "title": "Zigbee Placement Advisor", "tags": ["zigbee", "zha"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True, "refresh": "5m",
    "time": {"from": "now-14d", "to": "now"},
    "templating": {"list": [{"name": "datasource", "label": "Prometheus", "type": "datasource", "query": "prometheus",
                             "current": {}, "hide": 0}]},
    "annotations": {"list": []}, "panels": panels,
    "description": "Metrics of zigbee-placement-advisor (observe-only Zigbee link quality + router placement advice).",
}
out = Path(__file__).with_name("zigbee-placement-advisor.json")
out.write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
print(out)
