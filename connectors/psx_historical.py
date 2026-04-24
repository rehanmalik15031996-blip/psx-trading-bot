"""PSX historical end-of-day connector.

Uses the public PSX DPS endpoint:
    GET https://dps.psx.com.pk/timeseries/eod/{SYMBOL}

Returns ~5 years of daily bars as [unix_ts_seconds, close, volume, open].

Known limitation: PSX DPS does NOT publish daily high/low via this endpoint.
Going forward we'll augment per-day records with high/low from our live
PSXTerminalConnector.fetch() ticks (which DO include high/low) and append
them. Historical high/low before the bot started running is unavailable
for free — which is fine: gradient-boosting models work perfectly well
on close-based features (returns, vol, RSI, MACD, SMAs).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from connectors.base import BaseConnector, ConnectionResult, FetchResult


class PSXHistoricalConnector(BaseConnector):
    name = "PSX DPS Historical EOD"
    category = "prices"
    layer = "Layer 5 — Microstructure"
    url = "https://dps.psx.com.pk/timeseries/eod"

    TIMEOUT = 20

    def test(self) -> ConnectionResult:
        try:
            r = requests.get(
                f"{self.url}/OGDC",
                headers=self.DEFAULT_HEADERS,
                timeout=self.TIMEOUT,
            )
            elapsed = r.elapsed.total_seconds() * 1000.0
            r.raise_for_status()
            payload = r.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            return ConnectionResult(
                name=self.name,
                ok=bool(rows),
                latency_ms=elapsed,
                sample={"rows": len(rows), "cols": len(rows[0]) if rows else 0},
                notes=f"OGDC has {len(rows)} EOD bars",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch_symbol(self, symbol: str) -> list[dict]:
        """Pull full EOD history for one symbol as a list of dict rows.

        Row schema: {date: ISO-YYYY-MM-DD, open, close, volume, symbol}
        Newest-first from DPS; we return it as newest-first (caller sorts).
        """
        r = requests.get(
            f"{self.url}/{symbol}",
            headers=self.DEFAULT_HEADERS,
            timeout=self.TIMEOUT,
        )
        r.raise_for_status()
        payload = r.json()
        if not isinstance(payload, dict) or payload.get("status") != 1:
            raise RuntimeError(f"{symbol}: DPS returned status != 1")

        raw_rows = payload.get("data", []) or []
        out: list[dict] = []
        for row in raw_rows:
            if not row or len(row) < 4:
                continue
            ts, close, volume, open_ = row[0], row[1], row[2], row[3]
            try:
                d = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                continue
            out.append({
                "date": d,
                "symbol": symbol,
                "open": float(open_) if open_ is not None else None,
                "close": float(close) if close is not None else None,
                "volume": int(volume) if volume is not None else None,
            })
        return out

    def fetch(self, symbols: list[str] | None = None) -> FetchResult:
        """Bulk fetch historical EOD for a list of symbols.

        If `symbols` is None, uses the project's configured universe.
        """
        if symbols is None:
            from config.universe import symbols as universe_symbols
            symbols = universe_symbols()

        start = time.perf_counter()
        all_rows: list[dict] = []
        errors: list[str] = []
        per_symbol_counts: dict[str, int] = {}

        for sym in symbols:
            try:
                rows = self.fetch_symbol(sym)
                all_rows.extend(rows)
                per_symbol_counts[sym] = len(rows)
            except Exception as e:
                errors.append(f"{sym}: {type(e).__name__}: {e}")
                per_symbol_counts[sym] = 0

        elapsed = (time.perf_counter() - start) * 1000.0
        ok = bool(all_rows)

        return FetchResult(
            name=self.name,
            ok=ok,
            latency_ms=elapsed,
            format="json",
            schema=["date", "symbol", "open", "close", "volume"],
            records=all_rows,
            extras={
                "per_symbol_counts": per_symbol_counts,
                "errors": errors,
            },
            summary=(
                f"{len(all_rows)} EOD bars across {len(symbols)} symbols; "
                f"errors={len(errors)}"
            ),
            error="; ".join(errors) if not ok else None,
        )
