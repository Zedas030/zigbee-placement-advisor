"""Web UI (aiohttp): overview with room view, devices, device history, room setup, reports, /metrics."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from markupsafe import Markup

from .report import write_report
from .templating import env
from .views import device_detail, overview

if TYPE_CHECKING:  # pragma: no cover
    from .service import App

log = logging.getLogger(__name__)
APP_KEY = web.AppKey("zpa", object)
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def _render(request: web.Request, name: str, **ctx) -> web.Response:
    app: "App" = request.app[APP_KEY]
    html = env(app).get_template(name).render(page=name, **ctx)
    return web.Response(text=html, content_type="text/html", charset="utf-8")


async def index(request: web.Request) -> web.Response:
    return _render(request, "overview.html", v=overview(request.app[APP_KEY]))


async def devices(request: web.Request) -> web.Response:
    return _render(request, "devices.html", v=overview(request.app[APP_KEY]))


async def device(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    try:
        days = int(request.query.get("days", "30"))
    except ValueError:
        days = 30
    days = min(max(days, 1), app.cfg["retention"]["daily_days"])
    d = device_detail(app, request.match_info["ieee"], days)
    if d is None:
        raise web.HTTPNotFound()
    d["chart"] = Markup(d["chart"])
    return _render(request, "device.html", d=d, ranges=[7, 30, 90, 365])


async def setup_get(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    rooms = app.db.rooms()
    pairs = app.db.adjacency()
    names = [r["name"] for r in rooms]
    all_pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    confirmed = app.db.get_meta("rooms_confirmed_ts")
    return _render(request, "setup.html", rooms=rooms, pairs=pairs, all_pairs=all_pairs,
                   confirmed=app.fmt(confirmed) if confirmed else None, saved=request.query.get("saved"),
                   rooms_json=Markup(json.dumps(names)), pairs_json=Markup(json.dumps(sorted(pairs))))


async def setup_post(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    form = await request.post()
    enabled = form.getall("enabled", [])
    coordinator = form.get("coordinator", "")
    pairs = []
    for p in form.getall("pair", []):
        a, sep, b = str(p).partition("|")
        if sep:
            pairs.append((a, b))
    try:
        app.db.save_room_setup([str(e) for e in enabled], str(coordinator), pairs, app.now())
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))
    if app.metrics:
        app.metrics.update(app)
    raise web.HTTPSeeOther("/setup?saved=1")


async def reports(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    rows = [dict(week=r["week"], when=app.fmt(r["ts"]), summary=r["summary"])
            for r in app.db.q("SELECT * FROM reports ORDER BY week DESC")]
    v = overview(app)
    return _render(request, "reports.html", rows=rows, v=v)


async def report_file(request: web.Request) -> web.StreamResponse:
    return _serve_report(request.app[APP_KEY], request.match_info["week"])


async def report_latest(request: web.Request) -> web.StreamResponse:
    return _serve_report(request.app[APP_KEY], "latest")


def _serve_report(app: "App", week: str) -> web.StreamResponse:
    if week == "latest":
        row = app.db.q("SELECT * FROM reports ORDER BY week DESC LIMIT 1")
    elif WEEK_RE.match(week):
        row = app.db.q("SELECT * FROM reports WHERE week=?", (week,))
    else:
        raise web.HTTPNotFound()
    if not row:
        raise web.HTTPNotFound()
    return web.FileResponse(row[0]["path"], headers={"Content-Type": "text/html; charset=utf-8"})


async def report_now(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    path = await write_report(app, app.now(), notify=False)
    raise web.HTTPSeeOther(f"/reports/{path.stem}")


async def metrics(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    if app.metrics is None:
        raise web.HTTPServiceUnavailable()
    return web.Response(body=app.metrics.render(), headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"})


async def healthz(request: web.Request) -> web.Response:
    """Container health: the web server answers and the collector ran recently."""
    app: "App" = request.app[APP_KEY]
    now = app.now()
    last_ok = app.db.get_meta("last_success_ts")
    started = getattr(app, "started_ts", now)
    limit = app.cfg["collector"]["interval_minutes"] * 60 * 3 + 300
    # Unhealthy only if the process itself is stuck; HA being down is an incident, not a container fault.
    last_cycle = app.last_cycle.get("ts", started)
    healthy = now - last_cycle < limit
    body = {
        "healthy": healthy, "last_success": last_ok, "last_cycle": app.last_cycle.get("ts"),
        "last_cycle_ok": app.last_cycle.get("ok"), "open_incidents": [r["key"] for r in app.monitor.open_incidents()],
    }
    return web.json_response(body, status=200 if healthy else 503)


async def api_state(request: web.Request) -> web.Response:
    app: "App" = request.app[APP_KEY]
    v = overview(app)
    return web.json_response({
        "recommendation": v["rec"].to_dict(), "text": v["rec_lines"], "basis": v["basis"],
        "channel": v["channel"], "health": v["health"],
        "devices": [{k: r[k] for k in ("ieee", "name", "room", "type", "median", "last", "outages",
                                        "parent_changes", "samples", "state")} for r in v["devices"]],
    })


def create_web_app(app: "App") -> web.Application:
    wapp = web.Application()
    wapp[APP_KEY] = app
    wapp.add_routes([
        web.get("/", index),
        web.get("/devices", devices),
        web.get("/device/{ieee}", device),
        web.get("/setup", setup_get),
        web.post("/setup", setup_post),
        web.get("/reports", reports),
        web.post("/reports/now", report_now),
        web.get("/reports/{week}", report_file),
        web.get("/report/latest", report_latest),
        web.get("/metrics", metrics),
        web.get("/healthz", healthz),
        web.get("/api/state", api_state),
    ])
    wapp.router.add_static("/static", Path(__file__).parent / "static")
    return wapp
