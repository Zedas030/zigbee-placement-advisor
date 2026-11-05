"""Before/after evaluation with simulated data."""
from synth import T0, Net, build

from zpa.analysis import compute_stats
from zpa.beforeafter import evaluate
from zpa.config import defaults

E, R = "EndDevice", "Router"


def stats_for(lqis: dict, days=14, start=T0, per_day=48, outages=()):
    net = Net(rooms=["A", "B", "C"], adjacency=[("A", "B"), ("B", "C")], coordinator_room="A",
              devices={k: v for k, v in lqis.items()}, days=days, per_day=per_day, outages=list(outages))
    infos, samples, links, outs, end, ids = build(net)
    cfg = defaults()
    return compute_stats(infos, samples, links, outs, T0, end + 1, cfg["thresholds"]), ids


def test_router_that_helps_is_rated_worked():
    before, _ = stats_for({"W1": (E, "C", 45), "W2": (E, "C", 55), "OK": (E, "A", 200)})
    after, ids = stats_for({"W1": (E, "C", 150), "W2": (E, "C", 140), "OK": (E, "A", 200), "New": (R, "B", 200)},
                           days=7)
    ev = evaluate(before, after, ids["New"], "B", "B", defaults())
    assert ev.verdict == "worked"
    assert ev.improved == 2 and ev.worse == 0 and ev.considered == 2
    assert ev.as_recommended is True
    assert {d.outcome for d in ev.deltas} == {"improved"}


def test_router_without_effect_is_rated_no_effect():
    before, _ = stats_for({"W1": (E, "C", 45), "W2": (E, "C", 55), "OK": (E, "A", 200)})
    after, _ = stats_for({"W1": (E, "C", 50), "W2": (E, "C", 52), "OK": (E, "A", 200)}, days=7)
    ev = evaluate(before, after, "router", "A", "B", defaults())
    assert ev.verdict == "no_effect"
    assert ev.as_recommended is False


def test_partial_and_worse():
    before, _ = stats_for({"W1": (E, "C", 45), "W2": (E, "C", 55), "OK": (E, "A", 200)})
    after, _ = stats_for({"W1": (E, "C", 160), "W2": (E, "C", 30), "OK": (E, "A", 200)}, days=7)
    assert evaluate(before, after, "r", "B", "B", defaults()).verdict == "partial"
    after, _ = stats_for({"W1": (E, "C", 20), "W2": (E, "C", 30), "OK": (E, "A", 200)}, days=7)
    assert evaluate(before, after, "r", "B", "B", defaults()).verdict == "worse"


def test_too_little_after_data_is_insufficient():
    before, _ = stats_for({"W1": (E, "C", 45), "OK": (E, "A", 200)})
    after, _ = stats_for({"W1": (E, "C", 150), "OK": (E, "A", 200)}, days=0.2, per_day=24)
    assert evaluate(before, after, "r", "B", "B", defaults()).verdict == "insufficient_data"


def test_nothing_weak_before():
    before, _ = stats_for({"OK": (E, "A", 200)})
    after, _ = stats_for({"OK": (E, "A", 200)}, days=7)
    assert evaluate(before, after, "r", "B", None, defaults()).verdict == "nothing_weak"


def test_fewer_outages_counts_as_improvement():
    before, _ = stats_for({"W1": (E, "C", 150), "OK": (E, "A", 200)},
                          outages=[("W1", d * 86400, 1800) for d in range(0, 14, 2)])
    assert before.weak()[0].reasons == ["outages"]
    after, _ = stats_for({"W1": (E, "C", 150), "OK": (E, "A", 200)}, days=7)
    assert evaluate(before, after, "r", "B", "B", defaults()).verdict == "worked"
