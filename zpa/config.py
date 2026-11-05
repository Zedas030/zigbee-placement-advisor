"""Configuration: YAML file + HA_TOKEN from the environment (.env)."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

DEFAULTS: dict[str, Any] = {
    "language": "en",
    "timezone": "UTC",
    "source": "zha",
    "data_dir": "/data",
    "home_assistant": {"url": "http://localhost:8123", "verify_ssl": True, "timeout_seconds": 30},
    "collector": {"interval_minutes": 30, "history_backfill_days": 10},
    "web": {"host": "0.0.0.0", "port": 8099, "public_url": "http://localhost:8099"},
    # Optional "back to dashboard" button in the header (empty = no button)
    "ui": {"home_url": "", "home_label": "Dashboard"},
    "notify": {
        "services": [],
        "weekly_report": {"enabled": True, "weekday": "sunday", "time": "18:00"},
        "incidents": True,
        "before_after": True,
    },
    "wifi": {
        "source": "fritz_exporter",
        "exporter_url": "http://localhost:9787/metrics",
        "metric": "fritz_wifi_channel",
        "band_label": "2.4GHz",
        "manual_channel": None,
        "timeout_seconds": 45,  # fritz_exporter asks the router live on every scrape; 10-30 s is normal
    },
    "thresholds": {
        "analysis_window_days": 14,
        "weak_lqi_median": 80,
        "weak_outages_per_week": 2.0,
        "weak_parent_changes_per_week": 3.0,
        "anchor_min_lqi": 150,
        "min_outage_seconds": 60,
        "outage_mass_window_seconds": 120,
        "outage_mass_fraction": 0.8,
    },
    "recommendation": {
        "min_days": 7,
        "min_measurements": 100,
        "min_devices": 5,
        "stabilize_probability": 0.5,
        "days_for_full_confidence": 21,
    },
    "before_after": {"after_days": 7, "before_days": 14, "settle_hours": 24, "min_samples": 20, "lqi_improvement": 15},
    "retention": {"raw_days": 180, "daily_days": 730, "reports_keep": 104},
    "monitoring": {"failure_cycles_before_alert": 2, "stale_minutes": 180},
}


class ConfigError(ValueError):
    pass


def _merge(base: dict, override: dict, path: str = "") -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key not in base:
            raise ConfigError(f"unknown setting: {path}{key}")
        if isinstance(base[key], dict) and base[key] and not isinstance(value, dict):
            raise ConfigError(f"{path}{key} must be a mapping")
        if isinstance(base[key], dict) and base[key]:
            out[key] = _merge(base[key], value, f"{path}{key}.")
        else:
            out[key] = value
    return out


def _num(cfg: dict, dotted: str, lo: float, hi: float, integer: bool = False) -> None:
    node = cfg
    *parents, leaf = dotted.split(".")
    for p in parents:
        node = node[p]
    value = node[leaf]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{dotted} must be a number, got {value!r}")
    if integer and int(value) != value:
        raise ConfigError(f"{dotted} must be a whole number, got {value!r}")
    if not lo <= value <= hi:
        raise ConfigError(f"{dotted} must be between {lo} and {hi}, got {value!r}")


def validate(cfg: dict) -> dict:
    if cfg["language"] not in ("en", "de"):
        raise ConfigError("language must be 'en' or 'de'")
    if cfg["source"] not in ("zha",):
        raise ConfigError("source must be 'zha' (Zigbee2MQTT is not implemented yet)")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(cfg["timezone"])
    except Exception as exc:  # noqa: BLE001
        raise ConfigError(f"unknown timezone {cfg['timezone']!r}") from exc
    if not re.match(r"^https?://", str(cfg["home_assistant"]["url"])):
        raise ConfigError("home_assistant.url must start with http:// or https://")
    _num(cfg, "collector.interval_minutes", 5, 1440, True)
    _num(cfg, "collector.history_backfill_days", 0, 60, True)
    _num(cfg, "web.port", 1, 65535, True)
    _num(cfg, "home_assistant.timeout_seconds", 5, 300)
    _num(cfg, "wifi.timeout_seconds", 2, 300)
    ui = cfg["ui"]
    if ui["home_url"] is None:
        ui["home_url"] = ""
    if not isinstance(ui["home_url"], str) or (ui["home_url"] and not re.match(r"^https?://", ui["home_url"])):
        raise ConfigError("ui.home_url must be empty or start with http:// or https://")
    if not isinstance(ui["home_label"], str) or not ui["home_label"].strip() or len(ui["home_label"]) > 40:
        raise ConfigError("ui.home_label must be a short text (1..40 characters)")
    services = cfg["notify"]["services"]
    if not isinstance(services, list) or not all(isinstance(s, str) and re.match(r"^[a-z0-9_]+$", s) for s in services):
        raise ConfigError("notify.services must be a list of notify service names like 'mobile_app_phone'")
    wr = cfg["notify"]["weekly_report"]
    if str(wr["weekday"]).lower() not in WEEKDAYS:
        raise ConfigError("notify.weekly_report.weekday must be monday..sunday")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(wr["time"])):
        raise ConfigError("notify.weekly_report.time must be HH:MM")
    wifi = cfg["wifi"]
    if wifi["source"] not in ("fritz_exporter", "manual", "none"):
        raise ConfigError("wifi.source must be fritz_exporter, manual or none")
    if wifi["source"] == "manual":
        ch = wifi["manual_channel"]
        if not isinstance(ch, int) or not 1 <= ch <= 14:
            raise ConfigError("wifi.manual_channel must be 1..14 when wifi.source is manual")
    t = "thresholds."
    _num(cfg, t + "analysis_window_days", 1, 90, True)
    _num(cfg, t + "weak_lqi_median", 0, 255)
    _num(cfg, t + "weak_outages_per_week", 0, 1000)
    _num(cfg, t + "weak_parent_changes_per_week", 0, 1000)
    _num(cfg, t + "anchor_min_lqi", 0, 255)
    _num(cfg, t + "min_outage_seconds", 0, 86400)
    _num(cfg, t + "outage_mass_window_seconds", 0, 3600)
    _num(cfg, t + "outage_mass_fraction", 0.01, 1.0)
    if cfg["thresholds"]["anchor_min_lqi"] <= cfg["thresholds"]["weak_lqi_median"]:
        raise ConfigError("thresholds.anchor_min_lqi must be above thresholds.weak_lqi_median")
    r = "recommendation."
    _num(cfg, r + "min_days", 0, 365)
    _num(cfg, r + "min_measurements", 0, 10**7, True)
    _num(cfg, r + "min_devices", 2, 1000, True)
    _num(cfg, r + "stabilize_probability", 0.05, 1.0)
    _num(cfg, r + "days_for_full_confidence", 1, 365)
    b = "before_after."
    _num(cfg, b + "after_days", 1, 90)
    _num(cfg, b + "before_days", 1, 180)
    _num(cfg, b + "settle_hours", 0, 168)
    _num(cfg, b + "min_samples", 1, 10**6, True)
    _num(cfg, b + "lqi_improvement", 1, 255)
    _num(cfg, "retention.raw_days", 7, 3650, True)
    _num(cfg, "retention.daily_days", 7, 36500, True)
    _num(cfg, "retention.reports_keep", 1, 10000, True)
    if cfg["retention"]["raw_days"] < cfg["thresholds"]["analysis_window_days"]:
        raise ConfigError("retention.raw_days must be at least thresholds.analysis_window_days")
    if cfg["retention"]["raw_days"] < cfg["before_after"]["before_days"] + cfg["before_after"]["after_days"] + 2:
        raise ConfigError("retention.raw_days is too short for the before/after comparison")
    _num(cfg, "monitoring.failure_cycles_before_alert", 1, 100, True)
    _num(cfg, "monitoring.stale_minutes", 30, 100000, True)
    return cfg


def load(path: str | os.PathLike | None, env: dict | None = None) -> tuple[dict, str]:
    """Return (config, ha_token)."""
    env = os.environ if env is None else env
    data: dict = {}
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"config file is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("config file must contain a mapping at the top level")
    cfg = validate(_merge(DEFAULTS, data))
    cfg["notify"]["weekly_report"]["weekday"] = str(cfg["notify"]["weekly_report"]["weekday"]).lower()
    token = (env.get("HA_TOKEN") or "").strip()
    return cfg, token


def defaults() -> dict:
    return validate(copy.deepcopy(DEFAULTS))
