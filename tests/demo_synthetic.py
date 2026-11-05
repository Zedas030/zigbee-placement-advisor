"""Prints the recommendation for the synthetic test networks in readable form.

    python tests/demo_synthetic.py [en|de]

Used as evidence in acceptance protocols; the assertions live in test_recommend.py/test_beforeafter.py.
"""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]

import test_beforeafter as tba  # noqa: E402
import test_recommend as tr  # noqa: E402
from synth import Net, run  # noqa: E402

from zpa.beforeafter import evaluate  # noqa: E402
from zpa.config import defaults  # noqa: E402
from zpa.i18n import Translator  # noqa: E402

t = Translator(sys.argv[1] if len(sys.argv) > 1 else "en")
E, R = "EndDevice", "Router"


def show(title, net, expected):
    rec, _stats, names = run(net)
    print(f"\n=== {title}")
    print(f"expected: {expected}")
    if rec.status == "ok":
        for i, c in enumerate(rec.candidates[:3]):
            key = "rec_ok" if i == 0 else "rec_alt"
            print("  " + t(key, room=c.room, count=c.count, level=t(f"level_{c.confidence_level}")),
                  f"[confidence={c.confidence}, devices={', '.join(sorted(names[d] for d in c.devices))}]")
    else:
        print("  " + t({"healthy": "rec_healthy", "insufficient_data": "rec_insufficient",
                        "setup_needed": "rec_setup", "no_candidate": "rec_no_candidate"}[rec.status]))
        for k, p in zip(rec.reasons, rec.reason_params):
            print("  - " + t(k, **p))
    for key, ids in (("not_distance", rec.not_distance), ("unplaced", rec.unplaced)):
        if ids:
            print("  " + t(key, names=", ".join(names[i] for i in ids)))
    print("  " + t("basis", days=rec.days, measurements=rec.measurements, scans=rec.scans, devices=rec.devices))


show("1 Hallway bridge", tr.corridor(), "Flur, 4 devices, medium")
show("2 Two branches", Net(
    rooms=["Wohnzimmer", "Flur", "Bad", "Küche", "Balkon"],
    adjacency=[("Wohnzimmer", "Flur"), ("Flur", "Bad"), ("Wohnzimmer", "Küche"), ("Küche", "Balkon")],
    coordinator_room="Wohnzimmer",
    devices={"Plant 1": (E, "Balkon", 40), "Plant 2": (E, "Balkon", 45), "Plant 3": (E, "Balkon", 52),
             "Temp Bad": (E, "Bad", 60), "Plug Wohnzimmer": (R, "Wohnzimmer", 210)}), "Küche 3, then Flur 1")
show("3 Router extends reach", Net(
    rooms=["A", "B", "C", "D", "E"], adjacency=[("A", "B"), ("B", "C"), ("C", "D"), ("D", "E")],
    coordinator_room="A", devices={"Router C": (R, "C", 200), "E1": (E, "E", 40), "E2": (E, "E", 50),
                                   "E3": (E, "E", 60)}), "D, 3 devices")
show("4 Tie between two rooms", Net(
    rooms=["Wohnzimmer", "Flur", "Küche", "Schlafzimmer"],
    adjacency=[("Wohnzimmer", "Flur"), ("Wohnzimmer", "Küche"), ("Flur", "Schlafzimmer"), ("Küche", "Schlafzimmer")],
    coordinator_room="Wohnzimmer",
    devices={"S1": (E, "Schlafzimmer", 50), "S2": (E, "Schlafzimmer", 55), "Plug": (R, "Wohnzimmer", 200),
             "Lamp": (R, "Wohnzimmer", 190)}), "Flur and Küche, 2 each, low confidence")
net = tr.corridor()
net.links = [(w, "Plug Wohnzimmer", 70) for w in ("Window Schlafzimmer", "Motion Schlafzimmer", "Temp Bad", "Temp Küche")]
show("5 Hallway bridge + measured links", net, "Flur, 4 devices, high")
show("6 Too few days (3)", tr.corridor(days=3), "no recommendation")
show("7 Like the real network today (coordinator + 3 routers)", Net(
    rooms=["Wohnzimmer", "Schlafzimmer", "Bad", "Flur"],
    adjacency=[("Wohnzimmer", "Flur"), ("Flur", "Schlafzimmer"), ("Flur", "Bad")], coordinator_room="Wohnzimmer",
    devices={"P Wohnzimmer": (R, "Wohnzimmer", 192), "P Schlafzimmer": (R, "Schlafzimmer", 192),
             "P Bad": (R, "Bad", 128)}), "no recommendation (too few devices)")
h = tr.corridor()
h.devices = {k: (ty, r, 190) for k, (ty, r, _l) in h.devices.items()}
show("8 Healthy network", h, "no router needed")

print("\n=== Before/after with simulated data")
for title, after_lqi in (("router helps", {"W1": (E, "C", 150), "W2": (E, "C", 140)}),
                         ("router does nothing", {"W1": (E, "C", 50), "W2": (E, "C", 52)})):
    before, _ = tba.stats_for({"W1": (E, "C", 45), "W2": (E, "C", 55), "OK": (E, "A", 200)})
    after, _ = tba.stats_for({**after_lqi, "OK": (E, "A", 200)}, days=7)
    ev = evaluate(before, after, "router", "B", "B", defaults())
    d = ev.to_dict()
    print(f"  {title}: " + t(f"verdict_{d['verdict']}", improved=d["improved"], considered=d["considered"],
                              worse=d["worse"]),
          "| " + "; ".join(f"{x['name']}: LQI {x['lqi_before']:.0f} -> {x['lqi_after']:.0f}" for x in d["deltas"]))
