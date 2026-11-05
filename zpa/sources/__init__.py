from __future__ import annotations

from .base import (
    Source,
    SourceAuthError,
    SourceConnectionError,
    SourceError,
    SourceNoDataError,
    SourceSchemaError,
)


def create_source(cfg: dict, token: str) -> Source:
    kind = cfg["source"]
    if kind == "zha":
        from .zha import ZhaSource

        return ZhaSource(cfg, token)
    # Zigbee2MQTT: implement `Source` in sources/z2m.py and add it here.
    raise ValueError(f"unknown source: {kind}")


__all__ = [
    "Source",
    "SourceError",
    "SourceAuthError",
    "SourceConnectionError",
    "SourceSchemaError",
    "SourceNoDataError",
    "create_source",
]
