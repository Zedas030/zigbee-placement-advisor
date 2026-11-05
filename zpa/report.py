"""Weekly HTML report (self-contained file) + short notification."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .templating import env, static_text
from .views import overview

if TYPE_CHECKING:  # pragma: no cover
    from .service import App

log = logging.getLogger(__name__)


def week_id(app: "App", now: float) -> str:
    iso = app.local(now).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def summary_text(app: "App", view: dict) -> str:
    t = app.t
    parts = [view["rec_lines"][0]]
    if view["rec"].status == "ok":
        parts.append(view["basis"])
    parts.append(t("summary_weak", weak=view["weak_count"], total=view["device_count"]))
    parts.append(t("summary_outages", n=view["outages_week"]))
    if view["channel"]["status"] == "overlap":
        parts.append(view["channel"]["text"])
    for inc in view["health"]["incidents"][:2]:
        parts.append("⚠ " + inc["message"])
    return " ".join(parts)


def render_report(app: "App", now: float) -> tuple[str, str, dict]:
    view = overview(app, now)
    week = week_id(app, now)
    html = env(app).get_template("report.html").render(
        v=view, week=week, generated=app.fmt(now), css=static_text("style.css"), standalone=True,
    )
    return week, html, view


async def write_report(app: "App", now: float, notify: bool = True) -> Path:
    week, html, view = render_report(app, now)
    path = app.data_dir / "reports" / f"{week}.html"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(path)
    summary = summary_text(app, view)
    app.db.x("INSERT OR REPLACE INTO reports(week,ts,path,summary) VALUES(?,?,?,?)", (week, now, str(path), summary))
    log.info("report written: %s", path)
    if notify:
        url = f"{app.cfg['web']['public_url'].rstrip('/')}/reports/{week}"
        await app.notifier.send(app.t("notify_weekly_title") + f" {week}", summary, url)
    return path


def prune_reports(app: "App", keep: int) -> int:
    rows = app.db.q("SELECT week, path FROM reports ORDER BY week DESC")
    removed = 0
    for r in rows[keep:]:
        try:
            Path(r["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        app.db.x("DELETE FROM reports WHERE week=?", (r["week"],))
        removed += 1
    return removed
