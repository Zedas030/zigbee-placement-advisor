"""Source-independent data model.

Every data source (ZHA today, Zigbee2MQTT later) converts its native data into
these structures, so storage, analysis and recommendation never depend on a
specific integration.
"""
from __future__ import annotations

from dataclasses import dataclass, field

COORDINATOR = "Coordinator"
ROUTER = "Router"
END_DEVICE = "EndDevice"
DEVICE_TYPES = (COORDINATOR, ROUTER, END_DEVICE)


@dataclass
class Device:
    ieee: str
    name: str
    device_type: str  # one of DEVICE_TYPES
    power_source: str  # "Mains", "Battery", ... as reported by the source
    available: bool
    lqi: int | None = None
    rssi: int | None = None
    last_seen: str | None = None  # opaque marker, only compared for change detection
    area: str | None = None  # room name (Home Assistant area) or None
    model: str = ""
    manufacturer: str = ""
    entity_ids: list[str] = field(default_factory=list)
    primary_entity: str | None = None  # entity used for availability history


@dataclass
class Link:
    """One row of a neighbor table: `reporter` says it hears `neighbor`."""

    reporter: str
    neighbor: str
    lqi: int | None
    relationship: str  # Parent, Child, Sibling, PreviousChild, None
    depth: int | None = None


@dataclass
class Snapshot:
    ts: float
    devices: list[Device]
    links: list[Link]
    zigbee_channel: int | None = None
    areas: list[str] = field(default_factory=list)
    source_version: str = ""  # e.g. Home Assistant version


@dataclass
class CollectResult:
    snapshot: Snapshot
    # ieee -> sorted list of (timestamp, available) transitions from history
    availability: dict[str, list[tuple[float, bool]]] = field(default_factory=dict)
    # devices for which availability history was actually returned
    history_covered: set[str] = field(default_factory=set)
    history_until: float | None = None
    warnings: list[str] = field(default_factory=list)
