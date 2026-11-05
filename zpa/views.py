"""View models shared by the web UI and the weekly report."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .analysis import classify_outages
from .charts import line_chart, sparkline
from .model import COORDINATOR

if TYPE_CHECKING:  # pragma: no cover
    from .service import App


def quality_class(st) -> str:
    if st.info.device_type == COORDINATOR:
        return "coord"
    if st.weak:
        return "weak"
    if st.anchor:
        return "good"
    if st.samples == 0:
        return "nodata"
    return "ok"


STATE_TONE = {"weak": "bad", "good": "ok", "ok": "ok", "coord": "accent", "nodata": ""}


def lqi_tone(app: "App", lqi: float | None) -> str:
    """Colour of an LQI meter: below the weak threshold red, below the anchor threshold amber, else green."""
    if lqi is None:
        return ""
    th = app.cfg["thresholds"]
    return "bad" if lqi < th["weak_lqi_median"] else "warn" if lqi < th["anchor_min_lqi"] else "ok"


def lqi_pct(lqi: float | None) -> int:
    return 0 if lqi is None else max(2, min(100, round(lqi / 255 * 100)))


def device_rows(app: "App", stats) -> list[dict]:
    t = app.t
    rows = []
    for st in sorted(stats.devices.values(), key=lambda s: (s.info.device_type != COORDINATOR, s.room or "~", s.name)):
        samples = app.db.device_samples(stats.window_start, stats.window_end, st.ieee)
        lqis = [r["lqi"] for r in samples if r["lqi"] is not None]
        if st.info.device_type == COORDINATOR:
            state = t("type_Coordinator")
        elif st.weak:
            state = t("weak") + ": " + ", ".join(t(f"reason_{r}") for r in st.reasons)
        elif st.anchor:
            state = t("anchor")
        else:
            state = t("ok") if st.samples else t("unknown")
        parent = stats.devices.get(st.parent).name if st.parent in stats.devices else (st.parent or "")
        rows.append({
            "ieee": st.ieee, "name": st.name, "room": st.room or t("no_room"),
            "type": t(f"type_{st.info.device_type}"), "power": st.info.power_source,
            "median": None if st.median_lqi is None else round(st.median_lqi),
            "last": st.last_lqi, "outages": st.outages, "parent_changes": st.parent_changes,
            "samples": st.samples, "state": state, "cls": quality_class(st), "parent": parent,
            "available": st.available, "spark": sparkline(lqis[-80:]),
            "tone": STATE_TONE.get(quality_class(st), ""),
            "lqi_tone": lqi_tone(app, st.median_lqi), "lqi_pct": lqi_pct(st.median_lqi),
        })
    return rows


def room_tiles(app: "App", stats, rec) -> list[dict]:
    model = app.room_model()
    by_room: dict[str, list] = {}
    for st in stats.devices.values():
        room = model.coordinator_room if st.info.device_type == COORDINATOR else st.room
        by_room.setdefault(room or "", []).append(st)
    cand = {c.room: c for c in rec.candidates}
    best = rec.best.room if rec.best else None
    tiles = []
    for r in app.db.rooms():
        if not r["enabled"]:
            continue
        devs = by_room.get(r["name"], [])
        tiles.append({
            "name": r["name"], "coordinator": bool(r["coordinator"]),
            "devices": [_room_device(app, d) for d in devs],
            "candidate": cand.get(r["name"]), "best": r["name"] == best,
            "neighbors": sorted(b if a == r["name"] else a for a, b in model.adjacency if r["name"] in (a, b)),
        })
    if by_room.get(""):
        tiles.append({"name": app.t("no_room"), "coordinator": False, "candidate": None, "best": False,
                      "neighbors": [], "devices": [_room_device(app, d) for d in by_room[""]]})
    return tiles


def _room_device(app: "App", d) -> dict:
    return {"name": d.name, "cls": quality_class(d), "ieee": d.ieee, "tone": STATE_TONE.get(quality_class(d), ""),
            "lqi": None if d.median_lqi is None else round(d.median_lqi),
            "lqi_tone": lqi_tone(app, d.median_lqi), "lqi_pct": lqi_pct(d.median_lqi)}


def before_after(app: "App") -> list[dict]:
    names = {r["ieee"]: r["name"] for r in app.db.devices(present_only=False)}
    ba = app.cfg["before_after"]
    out = []
    for ev in app.db.q("SELECT * FROM router_events ORDER BY ts DESC"):
        item: dict[str, Any] = {"name": names.get(ev["ieee"], ev["ieee"]), "room": ev["room"] or app.t("no_room"),
                                "when": app.fmt(ev["ts"]), "evaluated": ev["evaluated_ts"] is not None}
        if ev["result"]:
            res = json.loads(ev["result"])
            item["text"] = app.verdict_text(res)
            item["verdict"] = res["verdict"]
            item["deltas"] = res["deltas"]
        else:
            item["text"] = app.t("ba_pending", name=item["name"], room=item["room"], when=item["when"],
                                 days=ba["after_days"])
        out.append(item)
    return out


HERO_TONE = {"healthy": "ok", "ok": "warn", "no_candidate": "warn", "setup_needed": "warn",
             "insufficient_data": "info"}


def overview(app: "App", now: float | None = None) -> dict:
    now = app.now() if now is None else now
    stats = app.current_stats(now)
    rec = app.recommendation(now, stats)
    last_ok = app.db.get_meta("last_success_ts")
    names = {s.ieee: s.name for s in stats.devices.values()}
    return {
        "now": app.fmt(now),
        "rec": rec,
        "rec_lines": app.recommendation_text(rec),
        "basis": app.basis_text(rec),
        "candidates": [dict(c.__dict__, device_names=[names.get(i, i) for i in c.devices],
                            level=app.t(f"level_{c.confidence_level}")) for c in rec.candidates],
        "channel": app.channel_view(now),
        "health": {
            "ok": last_ok is not None,
            "text": app.t("health_ok", ago=app.t.ago(now - last_ok), source=app.db.get_meta("source_version", ""))
            if last_ok else app.t("health_never"),
            "incidents": [dict(key=r["key"], message=r["message"], since=app.fmt(r["opened_ts"]))
                          for r in app.monitor.open_incidents()],
            "last_error": app.db.get_meta("last_error"),
        },
        "devices": device_rows(app, stats),
        "rooms": room_tiles(app, stats, rec),
        "window_days": app.cfg["thresholds"]["analysis_window_days"],
        "weak_count": len(stats.weak()),
        "device_count": len(stats.devices),
        "system_events": len(stats.system_outage_events),
        "outages_week": sum(s.outages for s in app.stats(now - 7 * 86400, now + 1).devices.values()),
        "before_after": before_after(app),
        "setup_confirmed": app.db.get_meta("rooms_confirmed_ts") is not None,
        "hero_tone": HERO_TONE.get(rec.status, "info"),
    }


def device_detail(app: "App", ieee: str, days: int, now: float | None = None) -> dict | None:
    now = app.now() if now is None else now
    row = next((r for r in app.db.devices(present_only=False) if r["ieee"] == ieee), None)
    if row is None:
        return None
    start = now - days * 86400
    raw_days = app.cfg["retention"]["raw_days"]
    if days <= raw_days:
        pts = [(r["ts"], r["lqi"]) for r in app.db.device_samples(start, now + 1, ieee) if r["lqi"] is not None]
    else:
        import datetime as dt

        since = (app.local(start).date()).isoformat()
        pts = []
        for r in app.db.daily_stats(ieee, since):
            if r["lqi_median"] is None:
                continue
            day = dt.date.fromisoformat(r["day"])
            pts.append((dt.datetime.combine(day, dt.time(12), app.tz).timestamp(), r["lqi_median"]))
    all_outs = app.db.outages(start, now + 1)
    th = app.cfg["thresholds"]
    n_dev = sum(1 for r in app.db.devices() if r["device_type"] != COORDINATOR)
    real, _events = classify_outages(all_outs, n_dev, th["outage_mass_window_seconds"],
                                     th["outage_mass_fraction"], 0, now)
    real_keys = set(real)
    outs = [(o["start_ts"], o["end_ts"], (o["ieee"], o["start_ts"]) in real_keys)
            for o in all_outs if o["ieee"] == ieee]
    stats = app.current_stats(now)
    st = stats.devices.get(ieee)
    names = {r["ieee"]: r["name"] for r in app.db.devices(present_only=False)}
    links = []
    for (a, b), v in sorted(stats.link_lqi.items(), key=lambda kv: -kv[1]):
        if ieee in (a, b):
            other = b if a == ieee else a
            links.append({"name": names.get(other, other), "lqi": round(v)})
    rels = {}
    for l in app.db.link_samples(stats.window_start, stats.window_end):
        if ieee in (l["reporter"], l["neighbor"]):
            other = l["neighbor"] if l["reporter"] == ieee else l["reporter"]
            rels[names.get(other, other)] = l["relationship"] if l["reporter"] == ieee else _inverse(l["relationship"])
    for item in links:
        item["relationship"] = rels.get(item["name"], "")
    return {
        "row": row, "stats": st, "days": days,
        "chart": line_chart(pts, start, now, app.tz, th["weak_lqi_median"],
                            [(a, b) for a, b, r in outs if r], [(a, b) for a, b, r in outs if not r]),
        "outages": [{"start": app.fmt(a), "end": app.fmt(b) if b else "…", "real": r,
                     "minutes": round(((b or now) - a) / 60)} for a, b, r in reversed(outs)],
        "links": links,
        "row_view": next((r for r in device_rows(app, stats) if r["ieee"] == ieee), None),
    }


def _inverse(rel: str) -> str:
    return {"Child": "Parent", "Parent": "Child"}.get(rel, rel)
