"""ZHA data source (Home Assistant WebSocket API). Strictly read-only.

Commands used (all read-only):
  zha/devices                      devices incl. cached neighbor tables
  zha/network/settings             Zigbee channel
  config/area_registry/list        area (room) names
  config/entity_registry/list      entity categories (to choose a primary entity)
  history/history_during_period    availability history ("unavailable" states)

`zha/topology/update` (an active scan) is deliberately never sent: zigpy scans
all neighbor tables by itself every 4 hours (topology_scan_period default),
so reading the cache adds no radio traffic at all.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from ..hass import CommandError, HAClient
from ..model import COORDINATOR, DEVICE_TYPES, END_DEVICE, ROUTER, CollectResult, Device, Link, Snapshot
from .base import Source, SourceNoDataError, SourceSchemaError

log = logging.getLogger(__name__)

REQUIRED_DEVICE_KEYS = ("ieee", "device_type", "available", "lqi", "neighbors", "entities", "power_source")
REQUIRED_NEIGHBOR_KEYS = ("ieee", "lqi", "relationship")
UNAVAILABLE = "unavailable"
# Domains preferred as the "primary" entity whose history shows device availability.
DOMAIN_PRIORITY = ("binary_sensor", "sensor", "light", "switch", "cover", "lock", "climate", "fan")


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str) and value.lower().startswith("0x"):
            return int(value, 16)
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _norm_type(raw: str) -> str:
    raw = (raw or "").replace(" ", "").replace("_", "").lower()
    for t in DEVICE_TYPES:
        if t.lower() == raw:
            return t
    return END_DEVICE if raw.startswith("end") else raw or "Unknown"


def validate_devices(devices: Any) -> None:
    """Raise SourceSchemaError if ZHA's answer no longer matches what we parse."""
    if not isinstance(devices, list):
        raise SourceSchemaError(f"zha/devices returned {type(devices).__name__}, expected a list")
    missing: set[str] = set()
    for d in devices:
        if not isinstance(d, dict):
            raise SourceSchemaError("zha/devices entries are not objects")
        missing.update(k for k in REQUIRED_DEVICE_KEYS if k not in d)
        for n in d.get("neighbors") or []:
            missing.update(f"neighbors.{k}" for k in REQUIRED_NEIGHBOR_KEYS if k not in n)
    if missing:
        raise SourceSchemaError("zha/devices is missing fields: " + ", ".join(sorted(missing)))
    types = {_norm_type(d.get("device_type")) for d in devices}
    if devices and COORDINATOR not in types:
        raise SourceSchemaError("zha/devices contains no coordinator")
    unknown = types - set(DEVICE_TYPES)
    if unknown:
        raise SourceSchemaError("unknown device_type values: " + ", ".join(sorted(unknown)))


def choose_primary_entity(entity_ids: list[str], categories: dict[str, str | None]) -> str | None:
    if not entity_ids:
        return None
    plain = [e for e in entity_ids if categories.get(e) is None] or list(entity_ids)

    def rank(eid: str) -> tuple[int, str]:
        domain = eid.split(".", 1)[0]
        prio = DOMAIN_PRIORITY.index(domain) if domain in DOMAIN_PRIORITY else len(DOMAIN_PRIORITY)
        return (prio, eid)

    return sorted(plain, key=rank)[0]


def parse_devices(
    raw: list[dict], area_names: dict[str, str], categories: dict[str, str | None]
) -> tuple[list[Device], list[Link]]:
    devices: list[Device] = []
    links: list[Link] = []
    for d in raw:
        ieee = str(d["ieee"]).lower()
        entity_ids = [e.get("entity_id") for e in d.get("entities") or [] if e.get("entity_id")]
        devices.append(
            Device(
                ieee=ieee,
                name=d.get("user_given_name") or d.get("name") or ieee,
                device_type=_norm_type(d.get("device_type")),
                power_source=str(d.get("power_source") or "Unknown"),
                available=bool(d.get("available")),
                lqi=_int(d.get("lqi")),
                rssi=_int(d.get("rssi")),
                last_seen=str(d.get("last_seen")) if d.get("last_seen") is not None else None,
                area=area_names.get(d.get("area_id")) if d.get("area_id") else None,
                model=str(d.get("model") or ""),
                manufacturer=str(d.get("manufacturer") or ""),
                entity_ids=entity_ids,
                primary_entity=choose_primary_entity(entity_ids, categories),
            )
        )
        for n in d.get("neighbors") or []:
            links.append(
                Link(
                    reporter=ieee,
                    neighbor=str(n["ieee"]).lower(),
                    lqi=_int(n.get("lqi")),
                    relationship=str(n.get("relationship") or "None"),
                    depth=_int(n.get("depth")),
                )
            )
    return devices, links


def parse_history(raw: dict, entity_to_device: dict[str, str]) -> dict[str, list[tuple[float, bool]]]:
    """Convert history_during_period output to per-device availability transitions."""
    out: dict[str, list[tuple[float, bool]]] = {}
    for entity_id, states in (raw or {}).items():
        ieee = entity_to_device.get(entity_id)
        if ieee is None:
            continue
        seq = out.setdefault(ieee, [])
        for s in states or []:
            state = s.get("s", s.get("state"))
            ts = s.get("lc", s.get("lu"))
            if ts is None:
                ts = s.get("last_changed") or s.get("last_updated")
                if isinstance(ts, str):
                    ts = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if state is None or ts is None:
                continue
            seq.append((float(ts), state != UNAVAILABLE))
    for seq in out.values():
        seq.sort()
    return out


class ZhaSource(Source):
    name = "zha"

    def __init__(self, cfg: dict, token: str):
        ha = cfg["home_assistant"]
        self.client_args = dict(url=ha["url"], token=token, timeout=ha["timeout_seconds"], verify_ssl=ha["verify_ssl"])
        self.backfill_days = cfg["collector"]["history_backfill_days"]

    def client(self) -> HAClient:
        return HAClient(**self.client_args)

    async def collect(self, history_since: float | None, now: float) -> CollectResult:
        warnings: list[str] = []
        async with self.client() as ha:
            try:
                raw_devices = await ha.call("zha/devices")
            except CommandError as exc:
                raise SourceSchemaError(f"zha/devices failed ({exc.code}): is ZHA loaded? {exc}") from exc
            validate_devices(raw_devices)
            if not raw_devices:
                raise SourceNoDataError("ZHA returned no devices")

            areas = await ha.call("config/area_registry/list")
            area_names = {a["area_id"]: a["name"] for a in areas or []}
            entities = await ha.call("config/entity_registry/list")
            categories = {e["entity_id"]: e.get("entity_category") for e in entities or [] if e.get("platform") == "zha"}

            channel = None
            try:
                ns = await ha.call("zha/network/settings")
                channel = _int(ns["settings"]["network_info"]["channel"])
            except (CommandError, KeyError, TypeError) as exc:
                warnings.append(f"zigbee channel unavailable: {exc}")

            devices, links = parse_devices(raw_devices, area_names, categories)
            snapshot = Snapshot(
                ts=now,
                devices=devices,
                links=links,
                zigbee_channel=channel,
                areas=sorted(area_names.values()),
                source_version=f"Home Assistant {ha.ha_version}",
            )

            start = history_since if history_since is not None else now - self.backfill_days * 86400
            until = now - 10  # recorder commits asynchronously; leave a small margin
            entity_to_device = {d.primary_entity: d.ieee for d in devices if d.primary_entity}
            availability: dict[str, list[tuple[float, bool]]] = {}
            covered: set[str] = set()
            if entity_to_device and until > start:
                try:
                    hist = await ha.call(
                        "history/history_during_period",
                        start_time=dt.datetime.fromtimestamp(start, dt.timezone.utc).isoformat(),
                        end_time=dt.datetime.fromtimestamp(until, dt.timezone.utc).isoformat(),
                        entity_ids=sorted(entity_to_device),
                        minimal_response=True,
                        no_attributes=True,
                        significant_changes_only=False,
                        include_start_time_state=True,
                    )
                    availability = parse_history(hist, entity_to_device)
                    covered = set(availability)
                except CommandError as exc:
                    warnings.append(f"history unavailable: {exc}")
                    until = history_since or start
            return CollectResult(
                snapshot=snapshot,
                availability=availability,
                history_covered=covered,
                history_until=until,
                warnings=warnings,
            )
