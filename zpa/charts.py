"""Tiny server-side SVG charts (no JavaScript, no CDN, works in the e-mailable report)."""
from __future__ import annotations

import datetime as dt
from html import escape
from zoneinfo import ZoneInfo


def sparkline(values: list[float], width: int = 120, height: int = 28, lo: float = 0, hi: float = 255) -> str:
    if len(values) < 2:
        return ""
    step = width / (len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{height - (min(max(v, lo), hi) - lo) / (hi - lo) * (height - 2) - 1:.1f}"
        for i, v in enumerate(values)
    )
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'role="img" aria-label="LQI"><polyline points="{pts}" fill="none" stroke="currentColor" '
            f'stroke-width="1.5"/></svg>')


def line_chart(
    points: list[tuple[float, float]],
    start: float,
    end: float,
    tz: ZoneInfo,
    weak_line: float | None = None,
    outages: list[tuple[float, float | None]] | None = None,
    system_events: list[tuple[float, float | None]] | None = None,
    width: int = 760,
    height: int = 220,
    label: str = "LQI",
) -> str:
    """LQI over time with a dashed 'weak' threshold and shaded outages."""
    left, right, top, bottom = 36, 8, 8, 24
    w, h = width - left - right, height - top - bottom
    span = max(end - start, 1)

    def x(ts: float) -> float:
        return left + (ts - start) / span * w

    def y(v: float) -> float:
        return top + h - min(max(v, 0), 255) / 255 * h

    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">']
    for v in (0, 64, 128, 192, 255):
        parts.append(f'<line class="grid" x1="{left}" x2="{left + w}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                     f'<text class="axis" x="{left - 4}" y="{y(v) + 4:.1f}" text-anchor="end">{v}</text>')
    days = span / 86400
    tick_every = 1 if days <= 8 else 7 if days <= 60 else 30
    d0 = dt.datetime.fromtimestamp(start, tz).date()
    for i in range(0, int(days) + 2, tick_every):
        day = d0 + dt.timedelta(days=i)
        ts = dt.datetime.combine(day, dt.time(0), tz).timestamp()
        if start <= ts <= end:
            parts.append(f'<line class="grid" x1="{x(ts):.1f}" x2="{x(ts):.1f}" y1="{top}" y2="{top + h}"/>'
                         f'<text class="axis" x="{x(ts):.1f}" y="{height - 6}" text-anchor="middle">{day:%d.%m.}</text>')
    for cls, spans in (("sysevent", system_events or []), ("outage", outages or [])):
        for o_start, o_end in spans:
            a, b = x(max(o_start, start)), x(min(o_end if o_end else end, end))
            parts.append(f'<rect class="{cls}" x="{a:.1f}" y="{top}" width="{max(b - a, 2):.1f}" height="{h}"/>')
    if weak_line is not None:
        parts.append(f'<line class="threshold" x1="{left}" x2="{left + w}" y1="{y(weak_line):.1f}" y2="{y(weak_line):.1f}"/>')
    pts = [(x(ts), y(v)) for ts, v in points if start <= ts <= end]
    if len(pts) >= 2:
        parts.append('<polyline class="series" fill="none" points="' +
                     " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + '"/>')
    for a, b in pts if len(pts) < 60 else []:
        parts.append(f'<circle class="dot" cx="{a:.1f}" cy="{b:.1f}" r="2"/>')
    parts.append("</svg>")
    return "".join(parts)
