"""Router placement recommendation (pure function, no I/O).

Model
-----
Rooms form an undirected graph (which rooms border each other). *Anchors* are
the coordinator and every router with a good, stable link. For a weak device
`w` in room `r_w` and a candidate room `c` for a new router:

* reach(w, c)   radio reach from the new router to w: 1.0 same room,
                0.8 neighbouring room, 0 further away.
* uplink(c)     how well the new router itself would connect: best anchor
                quality times the same distance factor.
* between(w,c)  1.0 if c lies on a shortest room path between w and its
                nearest anchor, 0.6 if it is a detour.
* improvement   c must be strictly closer to w than every existing anchor;
                otherwise a new router there adds nothing (w is already as
                close to a good router as it gets — the cause is probably
                interference, metal or the device itself).

p(w, c) = reach * uplink * between. A device counts as "stabilised" by c when
p >= stabilize_probability. Candidates are ranked by that count.

Confidence combines data basis (days observed), how clear the winner is
(margin to the runner-up), whether measured neighbor links support the
geometry, and the average p. It is an estimate and is always reported with
its basis; with too little data no recommendation is made at all.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from .analysis import NetworkStats

STATUS_OK = "ok"
STATUS_SETUP = "setup_needed"
STATUS_INSUFFICIENT = "insufficient_data"
STATUS_HEALTHY = "healthy"
STATUS_NO_CANDIDATE = "no_candidate"

REACH = {0: 1.0, 1: 0.8}
DETOUR = 0.6


@dataclass
class RoomModel:
    rooms: list[str]  # enabled rooms
    adjacency: set[tuple[str, str]]
    coordinator_room: str | None
    confirmed: bool = True

    def neighbours(self) -> dict[str, set[str]]:
        nb: dict[str, set[str]] = {r: set() for r in self.rooms}
        for a, b in self.adjacency:
            if a in nb and b in nb:
                nb[a].add(b)
                nb[b].add(a)
        return nb

    def distances(self) -> dict[str, dict[str, int]]:
        nb = self.neighbours()
        out: dict[str, dict[str, int]] = {}
        for start in self.rooms:
            dist = {start: 0}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                for n in nb[cur]:
                    if n not in dist:
                        dist[n] = dist[cur] + 1
                        queue.append(n)
            out[start] = dist
        return out


@dataclass
class Candidate:
    room: str
    devices: list[str]  # ieee of stabilised weak devices
    count: int
    score: float  # sum of p over stabilised devices
    mean_p: float
    evidence_share: float  # share of devices with measured links supporting the geometry
    confidence: float = 0.0
    confidence_level: str = "low"


@dataclass
class Recommendation:
    status: str
    reasons: list[str] = field(default_factory=list)  # i18n keys with params: (key, params)
    reason_params: list[dict] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    weak: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)  # weak devices without a room
    not_distance: list[str] = field(default_factory=list)  # weak although next to an anchor
    unreachable: list[str] = field(default_factory=list)  # weak, but no room can help
    days: float = 0.0
    measurements: int = 0
    scans: int = 0
    devices: int = 0

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def add_reason(self, key: str, **params: Any) -> None:
        self.reasons.append(key)
        self.reason_params.append(params)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reasons": [{"key": k, "params": p} for k, p in zip(self.reasons, self.reason_params)],
            "candidates": [c.__dict__ for c in self.candidates],
            "weak": self.weak,
            "unplaced": self.unplaced,
            "not_distance": self.not_distance,
            "unreachable": self.unreachable,
            "days": self.days,
            "measurements": self.measurements,
            "scans": self.scans,
            "devices": self.devices,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Recommendation":
        r = cls(status=d["status"])
        for item in d.get("reasons", []):
            r.add_reason(item["key"], **item.get("params", {}))
        r.candidates = [Candidate(**c) for c in d.get("candidates", [])]
        for k in ("weak", "unplaced", "not_distance", "unreachable"):
            setattr(r, k, list(d.get(k, [])))
        for k in ("days", "measurements", "scans", "devices"):
            setattr(r, k, d.get(k, 0))
        return r


def confidence_level(value: float) -> str:
    if value >= 0.7:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


def recommend(stats: NetworkStats, rooms: RoomModel, cfg: Mapping[str, Any]) -> Recommendation:
    rc = cfg["recommendation"]
    rec = Recommendation(
        status=STATUS_OK,
        days=round(stats.days_covered, 1),
        measurements=stats.measurements,
        scans=stats.scans,
        devices=len(stats.devices),
    )
    rec.weak = sorted(d.ieee for d in stats.weak())

    # 1. data basis gates: never produce a recommendation that the data cannot carry
    if not rooms.confirmed or not rooms.coordinator_room:
        rec.status = STATUS_SETUP
        rec.add_reason("reason_setup")
        return rec
    if len(stats.devices) < rc["min_devices"]:
        rec.add_reason("reason_few_devices", have=len(stats.devices), need=rc["min_devices"])
    if stats.days_covered < rc["min_days"]:
        rec.add_reason("reason_few_days", have=round(stats.days_covered, 1), need=rc["min_days"])
    if stats.measurements < rc["min_measurements"]:
        rec.add_reason("reason_few_measurements", have=stats.measurements, need=rc["min_measurements"])
    if rec.reasons:
        rec.status = STATUS_INSUFFICIENT
        return rec
    if not rec.weak:
        rec.status = STATUS_HEALTHY
        return rec

    # 2. geometry
    dist = rooms.distances()
    room_set = set(rooms.rooms)
    anchors: list[tuple[str, float]] = []  # (room, quality)
    for a in stats.anchors():
        room = rooms.coordinator_room if a.info.device_type == "Coordinator" else a.room
        if room in room_set:
            anchors.append((room, a.anchor_quality))
    if not anchors:
        rec.status = STATUS_NO_CANDIDATE
        rec.add_reason("reason_no_anchor")
        return rec

    def d(a: str, b: str) -> int | None:
        return dist.get(a, {}).get(b)

    def uplink(c: str) -> tuple[float, int | None]:
        best, best_d = 0.0, None
        for room, q in anchors:
            dd = d(c, room)
            if dd is not None and dd in REACH and q * REACH[dd] > best:
                best, best_d = q * REACH[dd], dd
        return best, best_d

    # rooms that contain a device (used for link evidence)
    devices_in_room: dict[str, set[str]] = {}
    for st in stats.devices.values():
        room = rooms.coordinator_room if st.info.device_type == "Coordinator" else st.room
        if room:
            devices_in_room.setdefault(room, set()).add(st.ieee)

    def has_evidence(w: str, c: str) -> bool:
        """A measured link between w and a device in c or next to c."""
        near = {c} | {r for r in rooms.rooms if d(c, r) == 1}
        others = set().union(*(devices_in_room.get(r, set()) for r in near)) - {w}
        return any(tuple(sorted((w, o))) in stats.link_lqi for o in others)

    placeable: list[tuple[str, str, int]] = []  # (ieee, room, gap to nearest anchor)
    for w in rec.weak:
        st = stats.devices[w]
        if st.room not in room_set:
            rec.unplaced.append(w)
            continue
        gaps = [d(st.room, r) for r, _q in anchors if d(st.room, r) is not None]
        if not gaps:
            rec.unreachable.append(w)
            continue
        gap = min(gaps)
        if gap == 0:
            rec.not_distance.append(w)  # a good router is already in the same room
            continue
        placeable.append((w, st.room, gap))

    candidates: list[Candidate] = []
    p_min = rc["stabilize_probability"]
    helped: set[str] = set()
    for c in rooms.rooms:
        up, up_d = uplink(c)
        if up <= 0:
            continue
        devs, ps, ev = [], [], 0
        for w, r_w, gap in placeable:
            dw = d(c, r_w)
            if dw is None or dw not in REACH or dw >= gap:
                continue  # out of reach, or not closer than an existing good router
            between = 1.0 if dw + (up_d or 0) == gap else DETOUR
            p = REACH[dw] * up * between
            if p >= p_min:
                devs.append(w)
                ps.append(p)
                ev += has_evidence(w, c)
        if devs:
            helped.update(devs)
            candidates.append(Candidate(
                room=c, devices=sorted(devs), count=len(devs), score=round(sum(ps), 3),
                mean_p=round(sum(ps) / len(ps), 3), evidence_share=round(ev / len(devs), 3),
            ))
    rec.unreachable.extend(w for w, _r, _g in placeable if w not in helped)

    if not candidates:
        rec.status = STATUS_NO_CANDIDATE
        rec.add_reason("reason_no_candidate")
        return rec

    candidates.sort(key=lambda c: (-c.count, -c.score, c.room))
    data_f = min(1.0, stats.days_covered / rc["days_for_full_confidence"])
    for i, c in enumerate(candidates):
        rival = candidates[1] if i == 0 and len(candidates) > 1 else (candidates[0] if i > 0 else None)
        if rival is None:
            margin_f = 1.0
        else:
            diff = c.count - rival.count
            if diff >= 2:
                margin_f = 1.0
            elif diff == 1:
                margin_f = 0.75
            elif diff == 0 and abs(c.score - rival.score) >= 0.1 * max(c.score, rival.score):
                margin_f = 0.6
            elif diff == 0:
                margin_f = 0.5
            else:
                margin_f = 0.4  # not the winner
        value = data_f * margin_f * (0.7 + 0.3 * c.evidence_share) * min(1.0, c.mean_p / 0.8)
        c.confidence = round(value, 3)
        c.confidence_level = confidence_level(value)
    rec.candidates = candidates
    return rec
