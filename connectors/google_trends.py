"""Google Trends connector — free retail search-interest proxy for PSX.

Uses `pytrends`, an unofficial wrapper around Google Trends' internal API
(no official API exists). No API key needed. Reliability caveat, stated
plainly: Google has no SLA for this and pytrends is known to get
rate-limited/blocked in bursts -- treat failures as expected noise, not a
regression to chase, and never let this block anything else in the pipeline
(hence the broad except + graceful-empty-result pattern below, matching
the fallback philosophy used across this repo's other connectors).

Why this is worth having at all: this repo's own research explicitly
describes PSX as retail-heavy and emotion-driven (see
Pakistan Stock Market Research Factors.docx, psx_strategy_v2.md). Search
interest in "PSX" / "KSE 100" is about as direct a free proxy for retail
attention/panic as exists.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from connectors.base import BaseConnector, ConnectionResult, FetchResult

# Keep this short -- pytrends batches keywords into ONE Google request and
# more keywords = more likely to get rate-limited. These two are the
# highest-signal, lowest-ambiguity terms (avoid single tickers: "OGDC" as a
# search term is too rare in Google Trends to return non-zero data).
KEYWORDS = ["PSX", "KSE 100"]


class GoogleTrendsConnector(BaseConnector):
    name = "Google Trends (PSX retail interest)"
    category = "sentiment-proxy"
    layer = "Layer 4 — Behavioral"
    url = "https://trends.google.com"

    def _pull_interest(self, timeframe: str = "now 7-d"):
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=300)
        pytrends.build_payload(KEYWORDS, timeframe=timeframe, geo="PK")
        df = pytrends.interest_over_time()
        return df

    def test(self) -> ConnectionResult:
        try:
            def pull():
                df = self._pull_interest()
                return {"rows": len(df), "cols": list(df.columns) if df is not None else []}
            sample, elapsed = self._timed(pull)
            return ConnectionResult(
                name=self.name, ok=sample["rows"] > 0, latency_ms=elapsed,
                sample=sample, notes=f"{sample['rows']} hourly points",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e} (pytrends rate-limiting is common -- not necessarily a real outage)",
            )

    def fetch(self) -> FetchResult:
        start = time.perf_counter()
        try:
            df = self._pull_interest()
            elapsed = (time.perf_counter() - start) * 1000.0
            if df is None or df.empty:
                return FetchResult(
                    name=self.name, ok=False, latency_ms=elapsed,
                    error="empty response (likely rate-limited -- expected occasionally)",
                )
            latest = df.iloc[-1]
            baseline = df[KEYWORDS].mean()
            records = [{
                "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "keyword": kw,
                "latest_value": int(latest[kw]),
                "trailing_7d_mean": round(float(baseline[kw]), 1),
                "spike_ratio": (round(float(latest[kw]) / baseline[kw], 2)
                                if baseline[kw] > 0 else None),
            } for kw in KEYWORDS]
            return FetchResult(
                name=self.name, ok=True, latency_ms=elapsed,
                format="json", schema=list(records[0].keys()),
                records=records,
                summary=f"{len(records)} keyword(s), latest hour",
            )
        except Exception as e:
            return FetchResult(
                name=self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000.0,
                error=f"{type(e).__name__}: {e} (pytrends rate-limiting is common -- not necessarily a real outage)",
            )
