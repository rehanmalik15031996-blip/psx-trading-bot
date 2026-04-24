"""Base connector class and shared types."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectionResult:
    name: str
    ok: bool
    latency_ms: float
    sample: Any = None
    error: str | None = None
    notes: str = ""

    def as_row(self) -> tuple[str, str, str, str]:
        status = "[green]OK[/green]" if self.ok else "[red]FAIL[/red]"
        latency = f"{self.latency_ms:.0f} ms"
        detail = self.error if self.error else self.notes
        return (self.name, status, latency, detail or "")


@dataclass
class FetchResult:
    """Represents real data pulled from a source (not just a health check)."""

    name: str
    ok: bool
    latency_ms: float
    format: str = "unknown"                  # json | table | text | dataframe | mixed
    schema: list[str] = field(default_factory=list)  # column names / keys in each record
    records: list[dict] = field(default_factory=list)  # main structured data
    extras: dict[str, Any] = field(default_factory=dict)  # secondary signals (e.g. sector breakdown)
    summary: str = ""                         # one-line human summary of the payload
    error: str | None = None

    @property
    def rows(self) -> int:
        return len(self.records)

    @property
    def cols(self) -> int:
        return len(self.schema)


class BaseConnector(ABC):
    """Abstract base for all data source connectors.

    Subclasses must implement:
    - name: short human-readable label
    - category: macro / prices / flows / news / sentiment / microstructure
    - test(): performs a lightweight connection check and returns ConnectionResult
    """

    name: str = "base"
    category: str = "generic"
    layer: str = "unspecified"
    url: str = ""

    DEFAULT_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 PSX-Bot/0.1"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    @abstractmethod
    def test(self) -> ConnectionResult:
        """Run a fast health check and return ConnectionResult."""

    def fetch(self) -> FetchResult:
        """Pull real data from the source. Subclasses should override."""
        return FetchResult(
            name=self.name,
            ok=False,
            latency_ms=0.0,
            error="fetch() not implemented for this connector",
        )

    def _timed(self, fn, *args, **kwargs) -> tuple[Any, float]:
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return result, elapsed_ms
