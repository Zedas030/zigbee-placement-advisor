"""Per-device link statistics over a time window (pure functions, no I/O)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .model import COORDINATOR, ROUTER

REASON_LQI = "lqi"
REASON_OUTAGES = "outages"
REASON_PARENTS = "parents"


@dataclass
class DeviceInfo:
    ieee: str
    name: str
    device_type: str
    power_source: str = ""
    room: str | None = None


@dataclass
class DeviceStats:
    info: DeviceInfo
    samples: int = 0
    median_lqi: float | None = None
    last_lqi: int | None = None
    available: bool | None = None
    outages: int = 0  # real device outages (no HA/ZHA restarts, not too short)
    outages_per_week: float = 0.0
    parent_changes: int = 0
    parent_changes_per_week: float = 0.0
    parent: str | None = None
    weak: bool = False
    reasons: list[str] = field(default_factory=list)
    anchor: bool = False
    anchor_quality: float = 0.0

    @property
    def ieee(self) -> str:
        return self.info.ieee

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def room(self) -> str | None:
        return self.info.room


@dataclass
class NetworkStats:
    window_start: float
    window_end: float
    days_covered: float
    device_samples: int
    link_samples: int
    scans: int
    devices: dict[str, DeviceStats]
    link_lqi: dict[tuple[str, str], float]  # sorted (a, b) -> median LQI in both directions
    system_outage_events: list[tuple[float, int]]

    @property
    def measurements(self) -> int:
        return self.device_samples + self.link_samples

    def weak(self) -> list[DeviceStats]:
        return [d for d in self.devices.values() if d.weak]

    def anchors(self) -> list[DeviceStats]:
        return [d for d in self.devices.values() if d.anchor]


def _get(row: Any, key: str) -> Any:
    return row[key] if not isinstance(row, Mapping) else row.get(key)


def classify_outages(
    outages: Iterable[Any],
    device_count: int,
    mass_window_s: float,
    mass_fraction: float,
    min_seconds: float,
    now: float,
) -> tuple[list[tuple[str, float]], list[tuple[float, int]]]:
    """Split outages into real device outages and system-wide events.

    An outage counts as system-wide when, within `mass_window_s`, at least
    `mass_fraction` of all devices (min. 2) went unavailable: that is a Home
    Assistant/ZHA restart or a coordinator problem, not a weak link.
    Returns (device_outages[(ieee, start)], system_events[(start, n_devices)]).
    """
    rows = sorted(((_get(o, "ieee"), float(_get(o, "start_ts")), _get(o, "end_ts")) for o in outages),
                  key=lambda r: r[1])
    needed = max(2, mass_fraction * max(device_count, 1))
    system: set[int] = set()
    events: list[tuple[float, int]] = []
    i = 0
    while i < len(rows):
        j = i
        while j + 1 < len(rows) and rows[j + 1][1] - rows[i][1] <= mass_window_s:
            j += 1
        members = range(i, j + 1)
        if len({rows[k][0] for k in members}) >= needed:
            system.update(members)
            events.append((rows[i][1], len({rows[k][0] for k in members})))
            i = j + 1
        else:
            i += 1
    device: list[tuple[str, float]] = []
    for k, (ieee, start, end) in enumerate(rows):
        if k in system:
            continue
        duration = (float(end) if end is not None else now) - start
        if duration < min_seconds:
            continue
        device.append((ieee, start))
    return device, events


def parent_history(links: Iterable[Any]) -> dict[str, list[tuple[float, str]]]:
    """Reconstruct each device's parent over time from stored neighbor tables.

    Tables are stored only when they change. A claim "R lists X as Child" or
    "X lists R as Parent" sets parent(X)=R; the most recent claim wins.
    """
    by_ts: dict[float, list[Any]] = {}
    for l in links:
        by_ts.setdefault(float(_get(l, "ts")), []).append(l)
    current: dict[str, str] = {}
    history: dict[str, list[tuple[float, str]]] = {}
    for ts in sorted(by_ts):
        for l in by_ts[ts]:
            rel = str(_get(l, "relationship"))
            reporter, neighbor = _get(l, "reporter"), _get(l, "neighbor")
            if rel == "Child":
                child, parent = neighbor, reporter
            elif rel == "Parent":
                child, parent = reporter, neighbor
            else:
                continue
            if current.get(child) != parent:
                current[child] = parent
                history.setdefault(child, []).append((ts, parent))
    return history


def compute_stats(
    devices: Iterable[DeviceInfo],
    device_samples: Iterable[Any],
    link_samples: Iterable[Any],
    outages: Iterable[Any],
    window_start: float,
    window_end: float,
    thresholds: Mapping[str, Any],
) -> NetworkStats:
    infos = {d.ieee: d for d in devices}
    samples = [s for s in device_samples if window_start <= float(_get(s, "ts")) < window_end]
    links = [l for l in link_samples if window_start <= float(_get(l, "ts")) < window_end]

    per_dev: dict[str, list[Any]] = {}
    for s in samples:
        per_dev.setdefault(_get(s, "ieee"), []).append(s)
    ts_all = [float(_get(s, "ts")) for s in samples]
    days = (max(ts_all) - min(ts_all)) / 86400 if len(ts_all) > 1 else 0.0
    weeks = max(days, 1.0) / 7.0

    non_coord = [i for i, d in infos.items() if d.device_type != COORDINATOR]
    dev_out, sys_events = classify_outages(
        [o for o in outages if float(_get(o, "start_ts")) < window_end and
         (_get(o, "end_ts") is None or float(_get(o, "end_ts")) >= window_start)],
        len(non_coord) or len(infos),
        thresholds["outage_mass_window_seconds"],
        thresholds["outage_mass_fraction"],
        thresholds["min_outage_seconds"],
        window_end,
    )
    out_count: dict[str, int] = {}
    for ieee, start in dev_out:
        if start >= window_start:
            out_count[ieee] = out_count.get(ieee, 0) + 1

    parents = parent_history(links)

    pair_values: dict[tuple[str, str], list[int]] = {}
    for l in links:
        lqi = _get(l, "lqi")
        if lqi is None:
            continue
        key = tuple(sorted((_get(l, "reporter"), _get(l, "neighbor"))))
        pair_values.setdefault(key, []).append(int(lqi))
    link_lqi = {k: float(statistics.median(v)) for k, v in pair_values.items()}

    stats: dict[str, DeviceStats] = {}
    for ieee, info in infos.items():
        rows = sorted(per_dev.get(ieee, []), key=lambda r: float(_get(r, "ts")))
        lqis = [int(_get(r, "lqi")) for r in rows if _get(r, "lqi") is not None]
        st = DeviceStats(info=info, samples=len(rows))
        if lqis:
            st.median_lqi = float(statistics.median(lqis))
            st.last_lqi = lqis[-1]
        if rows:
            st.available = bool(_get(rows[-1], "available"))
        st.outages = out_count.get(ieee, 0)
        st.outages_per_week = st.outages / weeks
        hist = parents.get(ieee, [])
        st.parent = hist[-1][1] if hist else None
        st.parent_changes = max(0, len(hist) - 1)
        st.parent_changes_per_week = st.parent_changes / weeks

        if info.device_type == COORDINATOR:
            st.anchor, st.anchor_quality = True, 1.0
        elif rows:
            if st.median_lqi is not None and st.median_lqi < thresholds["weak_lqi_median"]:
                st.reasons.append(REASON_LQI)
            if st.outages and st.outages_per_week >= thresholds["weak_outages_per_week"]:
                st.reasons.append(REASON_OUTAGES)
            if st.parent_changes and st.parent_changes_per_week >= thresholds["weak_parent_changes_per_week"]:
                st.reasons.append(REASON_PARENTS)
            st.weak = bool(st.reasons)
            if (info.device_type == ROUTER and not st.weak and st.available is not False
                    and st.median_lqi is not None and st.median_lqi >= thresholds["anchor_min_lqi"]
                    and len(lqis) >= 3):
                st.anchor = True
                st.anchor_quality = min(1.0, st.median_lqi / 200.0)
        stats[ieee] = st

    # System-wide events are reported separately and never mark a device as weak.
    return NetworkStats(
        window_start=window_start,
        window_end=window_end,
        days_covered=days,
        device_samples=len(samples),
        link_samples=len(links),
        scans=len({float(_get(l, "ts")) for l in links}),
        devices=stats,
        link_lqi=link_lqi,
        system_outage_events=[e for e in sys_events if e[0] >= window_start],
    )
