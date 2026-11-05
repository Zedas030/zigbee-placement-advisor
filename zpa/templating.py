from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import __version__

if TYPE_CHECKING:  # pragma: no cover
    from .service import App

BASE = Path(__file__).parent


@lru_cache(maxsize=None)
def static_text(name: str) -> str:
    return (BASE / "static" / name).read_text(encoding="utf-8")


def report_css() -> str:
    """Theme + tool styles, inlined into the self-contained weekly report."""
    return static_text("homelab-theme.css") + "\n" + static_text("style.css")


def env(app: "App") -> Environment:
    e = getattr(app, "_jinja", None)
    if e is None:
        e = Environment(loader=FileSystemLoader(BASE / "templates"), autoescape=select_autoescape(["html"]))
        e.globals.update(t=app.t, lang=app.t.language, version=__version__,
                         home_url=app.cfg["ui"]["home_url"], home_label=app.cfg["ui"]["home_label"])
        app._jinja = e
    return e
