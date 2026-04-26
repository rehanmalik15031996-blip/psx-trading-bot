"""Refresh per-stock fundamentals (EPS, BVPS, dividends, 5y financials) for
the trading universe via yfinance. Caches one parquet per symbol under
``data/fundamentals/``.

Run weekly (Sunday) by ``.github/workflows/fundamentals.yml`` and on demand
locally before generating predictions if you want a fresh value book.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make project root importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors.yfinance_fundamentals import YFinanceFundamentalsConnector


def main() -> int:
    c = YFinanceFundamentalsConnector()
    probe = c.test()
    if not probe.ok:
        print(f"[fundamentals] yfinance unreachable: {probe.error}")
        return 1
    print(f"[fundamentals] connector OK ({probe.latency_ms:.0f}ms)")

    fr = c.fetch()
    print(f"[fundamentals] fetch: {fr.summary}  ({fr.latency_ms:.0f}ms)")
    failed = [r["symbol"] for r in fr.records if not r.get("ok")]
    if failed:
        print(f"[fundamentals] FAILED symbols: {failed}")
        # Don't fail the whole run if a couple fail; the rest still wrote.
        return 0 if len(failed) < len(fr.records) else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
