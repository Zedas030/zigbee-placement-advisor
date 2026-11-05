"""Recommendation logic on synthetic networks with a known correct answer."""
from synth import Net, run

from zpa.config import defaults

E, R = "EndDevice", "Router"


def corridor(**kw) -> Net:
    """Living room (coordinator) - hallway - three rooms behind it. Answer: hallway, 4 devices."""
    return Net(
        rooms=["Wohnzimmer", "Flur", "Schlafzimmer", "Bad", "Küche"],
        adjacency=[("Wohnzimmer", "Flur"), ("Flur", "Schlafzimmer"), ("Flur", "Bad"), ("Flur", "Küche")],
        coordinator_room="Wohnzimmer",
        devices={
            "Plug Wohnzimmer": (R, "Wohnzimmer", 200),
            "Window Schlafzimmer": (E, "Schlafzimmer", 60),
            "Motion Schlafzimmer": (E, "Schlafzimmer", 55),
            "Temp Bad": (E, "Bad", 50),
            "Temp Küche": (E, "Küche", 65),
        },
        **kw,
    )


def names_of(rec, names, cand):
    return sorted(names[i] for i in cand.devices)


def test_corridor_bridge_recommends_hallway_for_four_devices():
    rec, _stats, names = run(corridor())
    assert rec.status == "ok"
    assert rec.best.room == "Flur"
    assert rec.best.count == 4
    assert names_of(rec, names, rec.best) == ["Motion Schlafzimmer", "Temp Bad", "Temp Küche", "Window Schlafzimmer"]
    assert rec.best.confidence_level == "medium"
    assert len(rec.candidates) == 1  # no other room is within reach of a good router AND closer to the weak devices
    assert rec.days >= 27 and rec.measurements > 1000


def test_two_branches_pick_the_branch_with_more_weak_devices():
    """Coordinator in the middle, kitchen->balcony branch (3 weak) and hallway->bath branch (1 weak)."""
    net = Net(
        rooms=["Wohnzimmer", "Flur", "Bad", "Küche", "Balkon"],
        adjacency=[("Wohnzimmer", "Flur"), ("Flur", "Bad"), ("Wohnzimmer", "Küche"), ("Küche", "Balkon")],
        coordinator_room="Wohnzimmer",
        devices={
            "Plant 1": (E, "Balkon", 40), "Plant 2": (E, "Balkon", 45), "Plant 3": (E, "Balkon", 52),
            "Temp Bad": (E, "Bad", 60), "Plug Wohnzimmer": (R, "Wohnzimmer", 210),
        },
    )
    rec, _s, _n = run(net)
    assert rec.status == "ok"
    assert [(c.room, c.count) for c in rec.candidates] == [("Küche", 3), ("Flur", 1)]
    assert rec.best.confidence_level in ("medium", "high")
    assert rec.candidates[1].confidence < rec.best.confidence


def test_good_router_extends_reach_recommendation_goes_beyond_it():
    """Chain A(coord)-B-C(good router)-D-E with 3 weak devices in E. Answer: D (between router and E)."""
    net = Net(
        rooms=["A", "B", "C", "D", "E"],
        adjacency=[("A", "B"), ("B", "C"), ("C", "D"), ("D", "E")],
        coordinator_room="A",
        devices={
            "Router C": (R, "C", 200),
            "E1": (E, "E", 40), "E2": (E, "E", 50), "E3": (E, "E", 60),
        },
    )
    rec, _s, _n = run(net)
    assert rec.status == "ok"
    assert rec.best.room == "D"
    assert rec.best.count == 3
    assert all(c.room not in ("A", "B", "C") for c in rec.candidates)


def test_two_equally_good_rooms_give_low_confidence_and_both_are_shown():
    net = Net(
        rooms=["Wohnzimmer", "Flur", "Küche", "Schlafzimmer"],
        adjacency=[("Wohnzimmer", "Flur"), ("Wohnzimmer", "Küche"), ("Flur", "Schlafzimmer"), ("Küche", "Schlafzimmer")],
        coordinator_room="Wohnzimmer",
        devices={"S1": (E, "Schlafzimmer", 50), "S2": (E, "Schlafzimmer", 55),
                 "Plug": (R, "Wohnzimmer", 200), "Lamp": (R, "Wohnzimmer", 190)},
    )
    rec, _s, _n = run(net)
    assert rec.status == "ok"
    assert sorted((c.room, c.count) for c in rec.candidates) == [("Flur", 2), ("Küche", 2)]
    assert rec.best.confidence_level == "low"


def test_healthy_network_makes_no_recommendation():
    net = corridor()
    net.devices = {k: (t, r, 190) for k, (t, r, _l) in net.devices.items()}
    rec, _s, _n = run(net)
    assert rec.status == "healthy"
    assert rec.candidates == []


def test_too_few_days_gives_no_recommendation():
    rec, _s, _n = run(corridor(days=3))
    assert rec.status == "insufficient_data"
    assert rec.candidates == []
    assert "reason_few_days" in rec.reasons


def test_too_few_devices_gives_no_recommendation():
    """Like the real network at install time: coordinator + 3 routers."""
    net = Net(
        rooms=["Wohnzimmer", "Schlafzimmer", "Bad", "Flur"],
        adjacency=[("Wohnzimmer", "Flur"), ("Flur", "Schlafzimmer"), ("Flur", "Bad")],
        coordinator_room="Wohnzimmer",
        devices={"P Wohnzimmer": (R, "Wohnzimmer", 192), "P Schlafzimmer": (R, "Schlafzimmer", 192),
                 "P Bad": (R, "Bad", 60)},
    )
    rec, _s, _n = run(net)
    assert rec.status == "insufficient_data"
    assert rec.reasons == ["reason_few_devices"]
    assert rec.reason_params[0] == {"have": 4, "need": 5}


def test_too_few_measurements_gives_no_recommendation():
    rec, _s, _n = run(corridor(days=8, per_day=2))
    assert rec.status == "insufficient_data"
    assert "reason_few_measurements" in rec.reasons


def test_missing_room_setup_blocks_recommendation():
    rec, _s, _n = run(corridor(confirmed=False))
    assert rec.status == "setup_needed"


def test_weak_device_next_to_a_good_router_is_not_a_distance_problem():
    net = corridor()
    net.devices = {"Plug Wohnzimmer": (R, "Wohnzimmer", 200), "Remote Wohnzimmer": (E, "Wohnzimmer", 40),
                   "A": (E, "Flur", 190), "B": (E, "Bad", 190), "C": (E, "Küche", 190)}
    rec, _s, names = run(net)
    assert rec.status == "no_candidate"
    assert [names[i] for i in rec.not_distance] == ["Remote Wohnzimmer"]


def test_weak_device_without_room_is_reported_not_guessed():
    net = corridor()
    net.devices["Lost sensor"] = (E, None, 30)
    rec, _s, names = run(net)
    assert rec.status == "ok" and rec.best.room == "Flur" and rec.best.count == 4
    assert [names[i] for i in rec.unplaced] == ["Lost sensor"]


def test_frequent_outages_make_a_device_weak_but_ha_restarts_do_not():
    net = corridor()
    net.devices = {k: (t, r, 190) for k, (t, r, _l) in net.devices.items()}
    # Temp Bad drops out 3x per week (real outages, 30 min each)
    net.outages = [("Temp Bad", d * 86400 + 3600, 1800) for d in range(0, 28, 2)]
    # All devices drop at the same moment twice: Home Assistant restarts -> ignored
    for name in net.devices:
        net.outages += [(name, 10 * 86400 + 5, 300), (name, 20 * 86400 + 7, 300)]
    rec, stats, names = run(net)
    weak = [names[i] for i in rec.weak]
    assert weak == ["Temp Bad"]
    assert stats.devices[rec.weak[0]].reasons == ["outages"]
    assert len(stats.system_outage_events) == 2
    assert rec.best.room == "Flur" and rec.best.count == 1


def test_frequent_parent_changes_make_an_end_device_weak():
    net = corridor()
    net.devices = {k: (t, r, 190) for k, (t, r, _l) in net.devices.items()}
    net.devices["Plug Flur"] = (R, "Flur", 200)
    net.parents = [(i * 86400 / 1.5, "Temp Küche", "Plug Flur" if i % 2 else "Plug Wohnzimmer") for i in range(42)]
    rec, stats, names = run(net)
    assert [names[i] for i in rec.weak] == ["Temp Küche"]
    assert stats.devices[rec.weak[0]].reasons == ["parents"]
    assert stats.devices[rec.weak[0]].parent_changes == 41


def test_measured_links_raise_confidence():
    plain, _s, _n = run(corridor())
    net = corridor()
    net.links = [(w, "Plug Wohnzimmer", 70) for w in
                 ("Window Schlafzimmer", "Motion Schlafzimmer", "Temp Bad", "Temp Küche")]
    linked, _s, _n = run(net)
    assert linked.best.room == plain.best.room == "Flur"
    assert linked.best.evidence_share == 1.0
    assert linked.best.confidence > plain.best.confidence
    assert linked.best.confidence_level == "high"


def test_thresholds_are_configurable():
    cfg = defaults()
    cfg["thresholds"]["weak_lqi_median"] = 40  # stricter: only very bad devices count
    rec, _s, _names = run(corridor(), cfg)
    assert rec.status == "healthy"  # LQI 50..65 is no longer "weak"
    cfg = defaults()
    cfg["recommendation"]["min_devices"] = 10
    rec, _s, _names = run(corridor(), cfg)
    assert rec.status == "insufficient_data"


def test_recommendation_round_trips_through_json():
    from zpa.recommend import Recommendation

    rec, _s, _n = run(corridor())
    again = Recommendation.from_dict(rec.to_dict())
    assert again.to_dict() == rec.to_dict()
