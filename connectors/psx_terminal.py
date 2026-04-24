"""PSX Terminal connector (psxterminal.com) — free live-ish market data.

Docs: https://github.com/mumtazkahn/psx-terminal/blob/main/API.md
REST: https://psxterminal.com/api/...
WebSocket: wss://psxterminal.com/
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from connectors.base import BaseConnector, ConnectionResult, FetchResult


def _iso_ts(unix_s: int | float | None) -> str | None:
    if unix_s is None:
        return None
    try:
        return datetime.fromtimestamp(int(unix_s), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    except (TypeError, ValueError):
        return None


class PSXTerminalConnector(BaseConnector):
    name = "PSX Terminal (REST)"
    category = "prices"
    layer = "Layer 5 — Microstructure"
    url = "https://psxterminal.com"

    TIMEOUT = 10

    def _get(self, path: str) -> dict:
        r = requests.get(
            f"{self.url}{path}",
            headers=self.DEFAULT_HEADERS,
            timeout=self.TIMEOUT,
        )
        r.raise_for_status()
        return r.json()

    def test(self) -> ConnectionResult:
        try:
            payload, elapsed = self._timed(self._get, "/api/symbols")
            count = 0
            if isinstance(payload, dict) and "data" in payload:
                data = payload["data"]
                if isinstance(data, list):
                    count = len(data)
                elif isinstance(data, dict):
                    count = sum(
                        len(v) if isinstance(v, list) else 0 for v in data.values()
                    )
            elif isinstance(payload, list):
                count = len(payload)
            return ConnectionResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                sample={"symbol_count": count},
                notes=f"{count} symbols returned",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_symbol(self, symbol: str, market: str = "REG") -> dict:
        return self._get(f"/api/ticks/{market}/{symbol}")

    # A liquid basket that cuts across the sectors most sensitive to
    # macro / flow shocks. Terminal is used for the fields Market Watch
    # does NOT publish (trades count + rupee turnover + live status).
    # Indices are handled by PSXIndicesConnector (DPS) which covers all 18
    # indices, so we no longer pull them here.
    SAMPLE_STOCKS = ["OGDC", "HBL", "LUCK", "ENGROH", "PSO", "FFC", "MCB", "MARI"]

    def fetch(self) -> FetchResult:
        """Pull live tick-quality quotes for a liquid basket.

        Kept fields (unique to Terminal): trades, value (rupee turnover),
        timestamp (ISO), status. OHLC and volume duplicate Market Watch but
        are retained for intraday refresh scenarios where MW is stale.
        """
        start = time.perf_counter()
        stock_records: list[dict] = []
        errors: list[str] = []

        for sym in self.SAMPLE_STOCKS:
            try:
                data = self._get(f"/api/ticks/REG/{sym}")
                d = data.get("data", {}) if isinstance(data, dict) else {}
                stock_records.append({
                    "symbol": d.get("symbol"),
                    "price": d.get("price"),
                    "change_pct": d.get("changePercent"),
                    "volume": d.get("volume"),
                    "trades": d.get("trades"),
                    "value_pkr": d.get("value"),
                    "high": d.get("high"),
                    "low": d.get("low"),
                    "timestamp": _iso_ts(d.get("timestamp")),
                    "status": d.get("st"),
                })
            except Exception as e:
                errors.append(f"{sym}: {type(e).__name__}")

        elapsed = (time.perf_counter() - start) * 1000.0
        ok = bool(stock_records)

        return FetchResult(
            name=self.name,
            ok=ok,
            latency_ms=elapsed,
            format="json",
            schema=list(stock_records[0].keys()) if stock_records else [],
            records=stock_records,
            extras={"errors": errors},
            summary=(
                f"{len(stock_records)}/{len(self.SAMPLE_STOCKS)} stocks "
                f"(trades, rupee turnover, live status); errors={len(errors)}"
            ),
            error="; ".join(errors) if not ok else None,
        )
