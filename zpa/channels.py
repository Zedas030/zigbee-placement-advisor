"""Zigbee vs. 2.4 GHz Wi-Fi channel overlap check (read-only, never changes anything)."""
from __future__ import annotations

import re
from dataclasses import dataclass

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
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(w["exporter_url"]) as resp:
                if resp.status != 200:
                    return None, f"HTTP {resp.status} from {w['exporter_url']}"
                text = await resp.text()
    except (aiohttp.ClientError, OSError, TimeoutError) as exc:
        return None, f"{w['exporter_url']}: {exc}"
    ch = parse_exporter(text, w["metric"], w["band_label"])
    if ch is None:
        return None, f"metric {w['metric']} with band {w['band_label']!r} not found"
    return ch, None
