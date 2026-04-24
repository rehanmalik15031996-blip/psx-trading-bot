"""yfinance connector — global commodity prices that drive PSX sectors.

Tickers of interest (kept):
- BZ=F  Brent Crude      → E&P (OGDC/PPL/MARI/POL), OMCs (PSO/SHEL), Refineries
- CL=F  WTI Crude        → same as Brent, cross-check
- CT=F  Cotton           → Textile sector (Composite / Spinning / Weaving)
- GC=F  Gold             → Risk-off / PKR devaluation hedge sentiment
- HG=F  Copper           → Global industrial cycle → cement/engineering

Dropped:
- NG=F  Natural Gas      → Pakistan gas prices are OGRA-administered, not
                            sensitive to Henry Hub. Low signal, high noise.
"""

from __future__ import annotations

import time
import warnings

from connectors.base import BaseConnector, ConnectionResult, FetchResult

warnings.filterwarnings("ignore")


class YFinanceCommoditiesConnector(BaseConnector):
    name = "yfinance (commodities)"
    category = "macro-commodities"
    layer = "Layer 1 — Macro"
    url = "https://finance.yahoo.com"

    TICKERS = {
        "Brent": "BZ=F",
        "WTI": "CL=F",
        "Cotton": "CT=F",
        "Gold": "GC=F",
        "Copper": "HG=F",
    }

    def test(self) -> ConnectionResult:
        try:
            import yfinance as yf

            def pull() -> dict:
                out = {}
                for label, ticker in self.TICKERS.items():
                    hist = yf.Ticker(ticker).history(period="5d", interval="1d")
                    if hist is not None and not hist.empty:
                        close = float(hist["Close"].iloc[-1])
                        out[label] = round(close, 2)
                return out

            sample, elapsed = self._timed(pull)
            if not sample:
                return ConnectionResult(
                    name=self.name,
                    ok=False,
                    latency_ms=elapsed,
                    error="No data returned for any ticker",
                )
            return ConnectionResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                sample=sample,
                notes=f"{len(sample)}/{len(self.TICKERS)} tickers OK",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self) -> FetchResult:
        """Pull 5-day OHLCV for each commodity, return last row plus 5-day change."""
        start = time.perf_counter()
        try:
            import yfinance as yf

            records: list[dict] = []
            errors: list[str] = []
            for label, ticker in self.TICKERS.items():
                try:
                    hist = yf.Ticker(ticker).history(period="5d", interval="1d")
                    if hist is None or hist.empty:
                        errors.append(f"{label}: no data")
                        continue
                    latest = hist.iloc[-1]
                    first = hist.iloc[0]
                    prev = hist.iloc[-2] if len(hist) >= 2 else None
                    pct_5d = (
                        (latest["Close"] - first["Close"]) / first["Close"] * 100
                        if first["Close"] else None
                    )
                    pct_1d = (
                        (latest["Close"] - prev["Close"]) / prev["Close"] * 100
                        if prev is not None and prev["Close"] else None
                    )
                    records.append({
                        "commodity": label,
                        "ticker": ticker,
                        "date": latest.name.strftime("%Y-%m-%d"),
                        "close": round(float(latest["Close"]), 2),
                        "change_1d_pct": round(pct_1d, 2) if pct_1d is not None else None,
                        "change_5d_pct": round(pct_5d, 2) if pct_5d is not None else None,
                    })
                except Exception as e:
                    errors.append(f"{label}: {type(e).__name__}")

            elapsed = (time.perf_counter() - start) * 1000.0
            return FetchResult(
                name=self.name, ok=bool(records), latency_ms=elapsed,
                format="dataframe",
                schema=list(records[0].keys()) if records else [],
                records=records,
                extras={"errors": errors},
                summary=f"{len(records)}/{len(self.TICKERS)} commodities with close + 1d/5d change",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )
