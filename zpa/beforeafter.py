"""Before/after evaluation when a new router joins the network (pure functions)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .analysis import NetworkStats

VERDICT_WORKED = "worked"
VERDICT_PARTIAL = "partial"
VERDICT_NO_EFFECT = "no_effect"
VERDICT_WORSE = "worse"
VERDICT_INSUFFICIENT = "insufficient_data"
VERDICT_NOTHING_WEAK = "nothing_weak"


@dataclass
class DeviceDelta:
    ieee: str
    name: str
    lqi_before: float | None
    lqi_after: float | None
    outages_pw_before: float
    outages_pw_after: float
    weak_before: bool
    weak_after: bool
    samples_after: int
    outcome: str  # improved | unchanged | worse | unknown


@dataclass
class Evaluation:
    verdict: str
    router: str
    room: str | None
    recommended_room: str | None
    as_recommended: bool | None
    deltas: list[DeviceDelta] = field(default_factory=list)
    improved: int = 0
    worse: int = 0
    considered: int = 0

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["deltas"] = [x.__dict__ for x in self.deltas]
        return d


def evaluate(
    before: NetworkStats,
    after: NetworkStats,
    router: str,
    room: str | None,
    recommended_room: str | None,
    cfg: Mapping[str, Any],
) -> Evaluation:
    ba = cfg["before_after"]
    ev = Evaluation(
        verdict=VERDICT_INSUFFICIENT, router=router, room=room, recommended_room=recommended_room,
        as_recommended=(room == recommended_room) if (room and recommended_room) else None,
    )
    weak_before = [s for s in before.weak() if s.ieee != router]
    if not weak_before:
        ev.verdict = VERDICT_NOTHING_WEAK
        return ev
    enough = 0
    for b in weak_before:
        a = after.devices.get(b.ieee)
        if a is None:
            continue
        ev.considered += 1
        delta = DeviceDelta(
            ieee=b.ieee, name=b.name, lqi_before=b.median_lqi, lqi_after=a.median_lqi,
            outages_pw_before=round(b.outages_per_week, 2), outages_pw_after=round(a.outages_per_week, 2),
            weak_before=True, weak_after=a.weak, samples_after=a.samples, outcome="unknown",
        )
        if a.samples >= ba["min_samples"]:
            enough += 1
            lqi_gain = (a.median_lqi or 0) - (b.median_lqi or 0) if a.median_lqi is not None and b.median_lqi is not None else 0
            fewer_outages = a.outages_per_week < b.outages_per_week
            more_outages = a.outages_per_week > b.outages_per_week + 0.5
            if not a.weak or lqi_gain >= ba["lqi_improvement"] or (fewer_outages and lqi_gain > -ba["lqi_improvement"]):
                delta.outcome = "improved"
                ev.improved += 1
            elif lqi_gain <= -ba["lqi_improvement"] or more_outages:
                delta.outcome = "worse"
                ev.worse += 1
            else:
                delta.outcome = "unchanged"
        ev.deltas.append(delta)
    if ev.considered == 0 or enough * 2 < ev.considered:
        ev.verdict = VERDICT_INSUFFICIENT
    elif ev.improved * 2 >= enough and ev.worse == 0:
        ev.verdict = VERDICT_WORKED
    elif ev.improved > 0:
        ev.verdict = VERDICT_PARTIAL
    elif ev.worse > 0:
        ev.verdict = VERDICT_WORSE
    else:
        ev.verdict = VERDICT_NO_EFFECT
    return ev
