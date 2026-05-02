"""Compute PSX daily turnover proxy from existing per-stock OHLCV files.

Real PSX turnover = sum of (volume * close) across the entire market.
We approximate by summing across our 17-stock universe (which captures
roughly 60-70% of KSE-100 turnover) and persist as a single time series.

This is a sentiment proxy: spikes correlate with retail FOMO; dries-up
phases correlate with risk-off / corrections. Used by the strategist
as a cross-check on the volume_signals module.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OHLCV_DIR = ROOT / "data" / "ohlcv"
OUT_PARQUET = ROOT / "data" / "macro" / "psx_universe_turnover.parquet"


def main() -> int:
    files = sorted(OHLCV_DIR.glob("*.parquet"))
    print(f"Reading {len(files)} OHLCV parquets ...")
    series: list[pd.DataFrame] = []
    for p in files:
        try:
            df = pd.read_parquet(p)
            df.columns = [c.lower() for c in df.columns]
            if not {"date", "close", "volume"}.issubset(df.columns):
                print(f"  skip {p.name}: missing date/close/volume")
                continue
            df = df[["date", "close", "volume"]].copy()
            df["date"] = pd.to_datetime(df["date"])
            df["symbol"] = p.stem
            df["pkr_turnover"] = df["close"] * df["volume"]
            series.append(df[["date", "symbol", "pkr_turnover", "volume"]])
        except Exception as e:
            print(f"  ERROR {p.name}: {e}")

    if not series:
        print("No OHLCV data found.")
        return 1

    long = pd.concat(series, ignore_index=True)
    daily = long.groupby("date", as_index=False).agg(
        universe_turnover_pkr=("pkr_turnover", "sum"),
        universe_volume=("volume", "sum"),
        n_stocks_active=("symbol", "nunique"),
    )

    # 20-day and 60-day rolling means + relative spike z-score.
    daily = daily.sort_values("date").reset_index(drop=True)
    daily["t20_mean"] = daily["universe_turnover_pkr"].rolling(20).mean()
    daily["t60_mean"] = daily["universe_turnover_pkr"].rolling(60).mean()
    daily["t60_std"] = daily["universe_turnover_pkr"].rolling(60).std()
    daily["turnover_zscore_60d"] = (
        (daily["universe_turnover_pkr"] - daily["t60_mean"]) / daily["t60_std"]
    )
    daily["turnover_ratio_20d"] = (
        daily["universe_turnover_pkr"] / daily["t20_mean"]
    )

    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    daily.to_parquet(OUT_PARQUET, index=False)
    print(f"\nWrote {len(daily):,} daily rows -> {OUT_PARQUET}")
    print(f"Date range: {daily['date'].min().date()} .. {daily['date'].max().date()}")
    print(f"Avg daily turnover: PKR {daily['universe_turnover_pkr'].mean():,.0f}")
    print()
    print("Last 10 days:")
    print(daily.tail(10)[["date", "universe_turnover_pkr", "t20_mean",
                           "turnover_zscore_60d", "turnover_ratio_20d"]]
          .to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
