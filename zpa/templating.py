from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, select_autoescape

if TYPE_CHECKING:  # pragma: no cover
    from .service import App

BASE = Path(__file__).parent


@lru_cache(maxsize=None)
def static_text(name: str) -> str:
    return (BASE / "static" / name).read_text(encoding="utf-8")


def env(app: "App") -> Environment:
    e = getattr(app, "_jinja", None)
    if e is None:
        e = Environment(loader=FileSystemLoader(BASE / "templates"), autoescape=select_autoescape(["html"]))
        e.globals.update(t=app.t, lang=app.t.language)
        app._jinja = e
    return e
