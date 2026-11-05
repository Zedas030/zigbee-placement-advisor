"""Data source interface.

A source knows how to talk to one Zigbee integration and returns
`CollectResult` objects. Add Zigbee2MQTT by implementing `Source` in a new
module and registering it in `zpa.sources.create_source`.
"""
from __future__ import annotations

import abc

from ..model import CollectResult


class SourceError(Exception):
    """Base class. `kind` selects the self-monitoring incident."""

    kind = "source_error"


class SourceConnectionError(SourceError):
    kind = "connection"


class SourceAuthError(SourceError):
    kind = "auth"


class SourceSchemaError(SourceError):
    """The integration answered, but not in the shape we expect (API change)."""

    kind = "schema"


class SourceNoDataError(SourceError):
    kind = "no_data"


class Source(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    async def collect(self, history_since: float | None, now: float) -> CollectResult:
        """Read the current topology and availability history since `history_since`.

        Must be read-only: never change the Zigbee network or its configuration.
        """

    async def close(self) -> None:  # pragma: no cover - optional hook
        return None
