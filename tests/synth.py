"""Synthetic Zigbee networks for tests: rooms + devices with known 'correct' answers."""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

from zpa.analysis import DeviceInfo, compute_stats
from zpa.config import defaults
from zpa.recommend import RoomModel, recommend

T0 = 1_760_000_000.0  # fixed epoch for reproducible tests


@dataclass
class Net:
    rooms: list[str]
    adjacency: list[tuple[str, str]]
    coordinator_room: str
    # name -> (type, room, median LQI)
    devices: dict[str, tuple[str, str | None, int]]
    links: list[tuple[str, str, int]] = field(default_factory=list)  # measured neighbor links (names)
    outages: list[tuple[str, float, float]] = field(default_factory=list)  # (name, start offset s, duration s)
    parents: list[tuple[float, str, str]] = field(default_factory=list)  # (offset s, child, parent)
    days: float = 28
    per_day: int = 48
    confirmed: bool = True
    seed: int = 1


def ieee(name: str) -> str:
    h = hashlib.md5(name.encode()).hexdigest()
    return "00:12:4b:" + ":".join(h[i:i + 2] for i in range(0, 10, 2))


def build(net: Net):
    rnd = random.Random(net.seed)
    infos = [DeviceInfo("coord", "Coordinator", "Coordinator", "Mains", net.coordinator_room)]
    ids = {"Coordinator": "coord"}
    for name, (typ, room, _lqi) in net.devices.items():
        ids[name] = ieee(name)
        infos.append(DeviceInfo(ids[name], name, typ, "Mains" if typ == "Router" else "Battery", room))
    end = T0 + net.days * 86400
    step = 86400 / net.per_day
    samples = []
    n = int(net.days * net.per_day)
    for i in range(n):
        ts = T0 + i * step
        samples.append({"ts": ts, "ieee": "coord", "lqi": 255, "rssi": 0, "available": 1})
        for name, (_t, _r, lqi) in net.devices.items():
            samples.append({"ts": ts, "ieee": ids[name], "lqi": max(0, min(255, lqi + rnd.randint(-8, 8))),
                            "rssi": -60, "available": 1})
    links = []
    scans = int(net.days * 6)
    for k in range(scans):
        ts = T0 + k * 14400 + 1
        for a, b, lqi in net.links:
            links.append({"ts": ts, "reporter": ids[a], "neighbor": ids[b], "lqi": lqi + rnd.randint(-5, 5),
                          "relationship": "Sibling", "depth": 1})
    for off, child, parent in net.parents:
        links.append({"ts": T0 + off, "reporter": ids[parent], "neighbor": ids[child], "lqi": 100,
                      "relationship": "Child", "depth": 2})
    outages = [{"ieee": ids[name], "start_ts": T0 + off, "end_ts": T0 + off + dur} for name, off, dur in net.outages]
    return infos, samples, links, outages, end, ids


def run(net: Net, cfg: dict | None = None):
    cfg = cfg or defaults()
    infos, samples, links, outages, end, ids = build(net)
    stats = compute_stats(infos, samples, links, outages, T0, end + 1, cfg["thresholds"])
    rooms = RoomModel(net.rooms, {tuple(sorted(p)) for p in net.adjacency}, net.coordinator_room, net.confirmed)
    rec = recommend(stats, rooms, cfg)
    names = {v: k for k, v in ids.items()}
    return rec, stats, names
