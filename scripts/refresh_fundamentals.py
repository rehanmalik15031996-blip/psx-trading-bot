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
        try:
            from scripts._health import write_status
            write_status(workflow="fundamentals", ok=False,
                          note=f"yfinance unreachable: {probe.error}",
                          payload={})
        except Exception:
            pass
        return 1
    print(f"[fundamentals] connector OK ({probe.latency_ms:.0f}ms)")

    fr = c.fetch()
    print(f"[fundamentals] fetch: {fr.summary}  ({fr.latency_ms:.0f}ms)")
    failed = [r["symbol"] for r in fr.records if not r.get("ok")]
    rc = 0
    if failed:
        print(f"[fundamentals] FAILED symbols: {failed}")
        rc = 0 if len(failed) < len(fr.records) else 2

    try:
        from scripts._health import write_status
        n_total = len(fr.records or [])
        n_ok = n_total - len(failed)
        write_status(
            workflow="fundamentals",
            ok=(rc == 0),
            note=f"{n_ok}/{n_total} symbols refreshed",
            payload={"failed": failed[:30],
                       "total":  n_total,
                       "refreshed": n_ok},
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
