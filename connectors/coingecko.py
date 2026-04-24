"""CoinGecko connector — free crypto prices (retail sentiment proxy).

Docs: https://www.coingecko.com/en/api
Public endpoint requires no API key; rate-limited to ~10-30 calls/min.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from connectors.base import BaseConnector, ConnectionResult, FetchResult


class CoinGeckoConnector(BaseConnector):
    name = "CoinGecko (crypto)"
    category = "sentiment-proxy"
    layer = "Layer 4 — Behavioral"
    url = "https://api.coingecko.com/api/v3"

    # Only the two coins that actually correlate with retail "risk-on" mood
    # in Pakistan. Solana was dropped — low relevance to local sentiment and
    # noisier than ETH.
    COINS = ["bitcoin", "ethereum"]

    def _get(self, path: str, **params) -> dict:
        r = requests.get(
            f"{self.url}{path}",
            params=params,
            headers=self.DEFAULT_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def test(self) -> ConnectionResult:
        try:
            payload, elapsed = self._timed(
                self._get,
                "/simple/price",
                ids=",".join(self.COINS),
                vs_currencies="usd",
                include_24hr_change="true",
            )
            if not payload:
                return ConnectionResult(
                    name=self.name,
                    ok=False,
                    latency_ms=elapsed,
                    error="Empty response",
                )
            sample = {
                c: {
                    "usd": payload.get(c, {}).get("usd"),
                    "24h_change_pct": round(
                        payload.get(c, {}).get("usd_24h_change", 0.0), 2
                    ),
                }
                for c in self.COINS
            }
            return ConnectionResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                sample=sample,
                notes=f"{len(sample)} coins",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        """Return USD price + 24h change + 24h volume per coin.

        Dropped `market_cap_usd` — for a PSX bot the absolute cap is not an
        input, and it's a trivial function of float x price. Normalized
        `last_updated_at` from Unix seconds to ISO-8601 UTC.
        """
        start = time.perf_counter()
        try:
            payload = self._get(
                "/simple/price",
                ids=",".join(self.COINS),
                vs_currencies="usd",
                include_24hr_change="true",
                include_24hr_vol="true",
                include_last_updated_at="true",
            )
            records: list[dict] = []
            for c in self.COINS:
                d = payload.get(c, {})
                if not d:
                    continue
                ts = d.get("last_updated_at")
                iso = (
                    datetime.fromtimestamp(ts, tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                    if ts else None
                )
                records.append({
                    "coin": c,
                    "usd": d.get("usd"),
                    "change_24h_pct": round(d.get("usd_24h_change", 0.0), 3),
                    "volume_24h_usd": d.get("usd_24h_vol"),
                    "last_updated_at": iso,
                })
            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name, ok=bool(records), latency_ms=elapsed,
                format="json",
                schema=list(records[0].keys()) if records else [],
                records=records,
                summary=f"{len(records)}/{len(self.COINS)} coins with price/change/vol",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )
