"""Fetch + cache overnight global risk signals from yfinance.

These drive the PSX open direction, which our rule-based / LLM models were
systematically missing. Values are stored per US calendar date (yfinance native)
and the briefing helper aligns them to a PSX trading date at the point of use.

Tickers:
    ^GSPC   S&P 500        - overnight US risk
    ^VIX    CBOE VIX       - US implied vol regime
    ^N225   Nikkei 225     - Tokyo open before PSX open
    ^HSI    Hang Seng      - HK open before PSX open
    ^FTSE   FTSE 100       - Europe overlap with PSX afternoon
    DX-Y.NYB Dollar Index  - global USD strength -> EM flows
    FM      iShares Frontier ETF - frontier EM breadth proxy
    EEM     iShares EM ETF  - broader EM flows

Output: data/macro/overnight_global.parquet
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
warnings.filterwarnings("ignore")

import pandas as pd
import yfinance as yf


TICKERS = {
    "sp500":    "^GSPC",
    "vix":      "^VIX",
    "nikkei":   "^N225",
    "hangseng": "^HSI",
    "ftse":     "^FTSE",
    "dxy":      "DX-Y.NYB",
    "fm_etf":   "FM",
    "eem":      "EEM",
}

OUT = ROOT / "data" / "macro" / "overnight_global.parquet"
PERIOD = "2y"


def fetch_one(label: str, ticker: str) -> pd.DataFrame:
    print(f"  {label:<10s} {ticker:<12s} ... ", end="", flush=True)
    try:
        hist = yf.Ticker(ticker).history(period=PERIOD, interval="1d",
                                          auto_adjust=False)
    except Exception as e:
        print(f"FAIL ({type(e).__name__}: {e})")
        return pd.DataFrame()
    if hist is None or hist.empty:
        print("empty")
        return pd.DataFrame()
    hist = hist.reset_index()
    hist["date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None).dt.normalize()
    out = hist[["date", "Close"]].rename(columns={"Close": f"{label}_close"})
    out[f"{label}_ret_1d"] = out[f"{label}_close"].pct_change().round(5)
    out[f"{label}_ret_5d"] = out[f"{label}_close"].pct_change(5).round(5)
    print(f"{len(out)} rows  last={out['date'].iloc[-1].date()}  "
          f"close={out[f'{label}_close'].iloc[-1]:.2f}")
    return out


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    print(f"Fetching {PERIOD} history for {len(TICKERS)} overnight tickers:")
    for label, tkr in TICKERS.items():
        df = fetch_one(label, tkr)
        if not df.empty:
            frames.append(df)
    if not frames:
        print("ERROR: nothing fetched.")
        sys.exit(1)
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged.to_parquet(OUT, index=False)
    print(f"\nSaved {len(merged)} rows x {len(merged.columns)} cols -> {OUT}")
    last_row = merged.iloc[-1]
    print(f"Last date: {last_row['date'].date()}")
    for col in sorted([c for c in merged.columns if c.endswith("_close")]):
        v = last_row[col]
        if pd.notna(v):
            print(f"  {col:<20s} = {v:.2f}")

    try:
        from scripts._health import write_status
        write_status(
            workflow="overnight",
            ok=True,
            note=f"{len(merged)} rows, last {last_row['date'].date()}",
            payload={"rows": int(len(merged)),
                       "last_date": str(last_row["date"].date()),
                       "tickers": list(TICKERS.keys())},
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
