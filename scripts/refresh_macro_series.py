"""Daily refresh for the yfinance-sourced macro time-series.

The bot's macro impact engine reads the rolling 5-day / 21-day deltas
on commodity and FX series from these parquet files:

    data/macro/brent.parquet     Brent crude (BZ=F)
    data/macro/wti.parquet       WTI crude (CL=F)
    data/macro/gold.parquet      Gold (GC=F)
    data/macro/copper.parquet    Copper (HG=F)
    data/macro/cotton.parquet    Cotton (CT=F)
    data/macro/btc.parquet       Bitcoin (BTC-USD)
    data/macro/usdpkr.parquet    USD/PKR (PKR=X)

Until this script existed those files were only populated by
``scripts/backfill_macro.py`` — a one-shot historical pull that the
user had to remember to rerun. The result was that the macro engine
on April 29, 2026 was reading a 5-day window ending **April 24** and
calling Brent's already-rolled-over rally a "+9.7% tailwind". This
script closes that gap.

The pull strategy
-----------------
For each series we ask yfinance for the last 30 calendar days, merge
the new rows into the existing parquet (deduping by date), and
overwrite the file. yfinance returns timezone-aware indexes; we strip
to a naive ``YYYY-MM-DD`` date so the file matches the existing
schema produced by ``scripts/backfill_macro.py``.

The script is idempotent: rerunning it on the same business day
overwrites today's row in place rather than creating duplicates. If
yfinance throttles a single ticker, the others still update — every
ticker is wrapped in its own try / except so one bad pull does not
break the run.

Run::

    python scripts/refresh_macro_series.py
    python scripts/refresh_macro_series.py --series brent gold
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
warnings.filterwarnings("ignore")

OUT_DIR = ROOT / "data" / "macro"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# yfinance ticker map — the keys MUST match the parquet filenames
# already created by ``scripts/backfill_macro.py``.
SERIES: dict[str, str] = {
    "brent":   "BZ=F",
    "wti":     "CL=F",
    "gold":    "GC=F",
    "copper":  "HG=F",
    "cotton":  "CT=F",
    "btc":     "BTC-USD",
    "usdpkr":  "PKR=X",
}


def refresh_one(key: str, ticker: str, period: str = "30d") -> dict:
    """Pull the last ``period`` of yfinance closes for one ticker and
    merge into ``data/macro/<key>.parquet``.

    Returns a small status dict (``ok``, ``rows``, ``last_date``,
    ``last_value``) so the caller can log a one-line summary per
    ticker without having to re-read the parquet.
    """
    import pandas as pd
    import yfinance as yf

    try:
        hist = yf.Ticker(ticker).history(period=period, interval="1d",
                                            auto_adjust=False)
    except Exception as e:
        return {"ok": False, "key": key, "ticker": ticker,
                "error": f"{type(e).__name__}: {e}"}

    if hist is None or hist.empty:
        return {"ok": False, "key": key, "ticker": ticker,
                "error": "yfinance returned empty frame"}

    closes = hist["Close"].copy()
    if hasattr(closes, "columns"):
        # Some tickers come back as a multi-column frame; take the first.
        closes = closes.iloc[:, 0]

    df_new = pd.DataFrame({
        "date": pd.to_datetime(closes.index).tz_localize(None),
        "value": closes.values,
    }).dropna()
    df_new = df_new.sort_values("date").reset_index(drop=True)
    if df_new.empty:
        return {"ok": False, "key": key, "ticker": ticker,
                "error": "no non-null closes"}

    p = OUT_DIR / f"{key}.parquet"
    if p.exists():
        try:
            df_old = pd.read_parquet(p)
            df_old["date"] = pd.to_datetime(df_old["date"])
        except Exception:
            df_old = pd.DataFrame(columns=["date", "value"])
    else:
        df_old = pd.DataFrame(columns=["date", "value"])

    df_all = pd.concat([df_old, df_new], ignore_index=True)
    df_all = (df_all
                .drop_duplicates(subset=["date"], keep="last")
                .sort_values("date")
                .reset_index(drop=True))
    df_all.to_parquet(p, index=False)

    last = df_all.iloc[-1]
    return {
        "ok": True,
        "key": key,
        "ticker": ticker,
        "rows": int(len(df_all)),
        "last_date": last["date"].date().isoformat(),
        "last_value": round(float(last["value"]), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--series", nargs="+", default=None,
        choices=list(SERIES.keys()),
        help="Subset of series to refresh (default: all).",
    )
    parser.add_argument(
        "--period", default="30d",
        help=("yfinance lookback window. 30d is plenty for the "
              "rolling 5d / 21d deltas the macro engine consumes."),
    )
    args = parser.parse_args()

    chosen = args.series or list(SERIES.keys())
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[macro_series] start={started}  series={chosen}  "
          f"period={args.period}")

    results: list[dict] = []
    for key in chosen:
        ticker = SERIES[key]
        out = refresh_one(key, ticker, period=args.period)
        results.append(out)
        if out.get("ok"):
            print(f"  [{key:<7}] {out['ticker']:<8}  "
                  f"rows={out['rows']:<5}  "
                  f"last={out['last_date']}  "
                  f"value={out['last_value']}")
        else:
            print(f"  [{key:<7}] FAILED: {out.get('error')}")

    n_ok = sum(1 for r in results if r.get("ok"))
    print(json.dumps({"ok": n_ok == len(results),
                        "series_refreshed": n_ok,
                        "series_total": len(results)}))
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
