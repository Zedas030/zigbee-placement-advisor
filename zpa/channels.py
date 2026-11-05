"""Zigbee vs. 2.4 GHz Wi-Fi channel overlap check (read-only, never changes anything)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

import aiohttp

OK = "ok"
OVERLAP = "overlap"
UNKNOWN = "unknown"


def zigbee_mhz(channel: int) -> int:
    """Centre frequency of IEEE 802.15.4 channel 11..26 (2 MHz wide)."""
    return 2405 + 5 * (channel - 11)


def wifi_mhz(channel: int) -> int:
    return 2484 if channel == 14 else 2407 + 5 * channel


@dataclass
class ChannelCheck:
    zigbee: int | None
    wifi: int | None
    status: str
    distance_mhz: int | None = None


def check(zigbee: int | None, wifi: int | None) -> ChannelCheck:
    """A 20 MHz Wi-Fi channel occupies its centre +-10 MHz; a Zigbee channel is 2 MHz wide.

    Zigbee and Wi-Fi centres are always 2, 3, 7, 8, 12, 13, ... MHz apart, so
    <= 9 MHz means the Zigbee channel sits inside the Wi-Fi signal (overlap) and
    >= 12 MHz means it sits in a gap (ok). 40 MHz Wi-Fi would widen the occupied
    band; the router's channel width is not known to this tool.
    """
    if not zigbee or not wifi or not 11 <= zigbee <= 26 or not 1 <= wifi <= 14:
        return ChannelCheck(zigbee, wifi, UNKNOWN)
    dist = abs(zigbee_mhz(zigbee) - wifi_mhz(wifi))
    return ChannelCheck(zigbee, wifi, OVERLAP if dist <= 9 else OK, dist)


#: 2.4 GHz Wi-Fi channels usable in most of the world (14 is Japan-only and 802.11b only).
WIFI_CHANNELS = range(1, 14)
AUTO_CHANGES = 2  # this many channel changes within the window -> router probably uses auto channel
HISTORY_WINDOW = 7 * 86400


def best_wifi_channels(zigbee: int | None, limit: int = 3) -> list[tuple[int, int]]:
    """Wi-Fi channels (1..13) that do not overlap the Zigbee channel: [(wifi_channel, distance_mhz), ...].

    First the channel with the largest gap, then the best ones on the OTHER side of the Zigbee
    channel (2 and 3 next to 1 are only worse copies of 1; the other side is a real alternative if a
    neighbour's network sits on the first choice), then the rest by gap. Ties go to the lower channel.
    For Zigbee 20 (2450 MHz) that is [(1, 38), (13, 22), (12, 17)].
    """
    if not zigbee or not 11 <= zigbee <= 26:
        return []
    ok = [(w, c.distance_mhz) for w in WIFI_CHANNELS if (c := check(zigbee, w)).status == OK]
    ok.sort(key=lambda wd: (-wd[1], wd[0]))
    if not ok:
        return []
    first = ok[0]
    below = wifi_mhz(first[0]) < zigbee_mhz(zigbee)
    other = [wd for wd in ok if (wifi_mhz(wd[0]) < zigbee_mhz(zigbee)) != below]
    picked = [first] + other
    picked += [wd for wd in ok if wd not in picked]
    return picked[:limit]


@dataclass
class ChannelHistory:
    readings: int = 0
    since: float | None = None          # start of the covered period (>= now - window)
    changes: int = 0                    # Wi-Fi channel changes inside the window
    channels: list[int] = field(default_factory=list)  # distinct Wi-Fi channels seen, sorted
    overlap_share: float | None = None  # 0..1 of the covered time, None if nothing is covered
    last_change: float | None = None

    @property
    def auto(self) -> bool:
        return self.changes >= AUTO_CHANGES


def history(readings: Iterable[tuple[float, int, int | None]], now: float, window: float = HISTORY_WINDOW,
            zigbee: int | None = None, max_gap: float = 6 * 3600) -> ChannelHistory:
    """Summarise stored Wi-Fi channel readings (ts, wifi, zigbee) over the last `window` seconds.

    The last reading before the window is the starting state, so a change right at the window
    start still counts. Each reading is assumed to hold until the next one, but at most `max_gap`
    seconds (collector downtime is not counted as overlapping or clean time). `zigbee` is used for
    readings stored without a Zigbee channel.
    """
    start = now - window
    rows = sorted((float(ts), int(w), z) for ts, w, z in readings if w and ts <= now)
    before = [r for r in rows if r[0] < start]
    seq = before[-1:] + [r for r in rows if r[0] >= start]
    out = ChannelHistory(readings=len(seq))
    if not seq:
        return out
    out.since = max(seq[0][0], start)
    out.channels = sorted({w for _, w, _ in seq})
    for prev, cur in zip(seq, seq[1:]):
        if cur[1] != prev[1]:
            out.changes += 1
            out.last_change = cur[0]
    total = overlap = 0.0
    for i, (ts, w, z) in enumerate(seq):
        end = seq[i + 1][0] if i + 1 < len(seq) else now
        a, b = max(ts, start), min(end, ts + max_gap)
        if b <= a:
            continue
        status = check(z or zigbee, w).status
        if status == UNKNOWN:
            continue
        total += b - a
        if status == OVERLAP:
            overlap += b - a
    out.overlap_share = overlap / total if total else None
    return out


METRIC_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>\S+)")


def parse_exporter(text: str, metric: str, band_label: str) -> int | None:
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = METRIC_RE.match(line)
        if not m or m.group("name") != metric:
            continue
        labels = m.group("labels") or ""
        if f'"{band_label}"' in labels:
            try:
                value = int(float(m.group("value")))
            except ValueError:
                return None
            return value or None
    return None


async def read_wifi_channel(cfg: dict) -> tuple[int | None, str | None]:
    """Returns (channel, error)."""
    w = cfg["wifi"]
    if w["source"] == "none":
        return None, None
    if w["source"] == "manual":
        return int(w["manual_channel"]), None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=float(w.get("timeout_seconds", 45)))) as s:
            async with s.get(w["exporter_url"]) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status} from {w['exporter_url']}"
                text = await resp.text()
    except (aiohttp.ClientError, OSError, TimeoutError) as exc:
        # TimeoutError has an empty message; name the type so the incident text says what happened
        return None, f"{w['exporter_url']}: {type(exc).__name__} {exc}".rstrip()
    ch = parse_exporter(text, w["metric"], w["band_label"])
    if ch is None:
        return None, f"metric {w['metric']} with band {w['band_label']!r} not found"
    return ch, None
