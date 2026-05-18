"""Backfill ABL.parquet from PSX historical connector.

ABL stopped appending on 2026-04-23 (stored parquet ends there) but
the PSX historical source has all 22 missing trading days through
2026-05-15. Just rewrite the parquet from the fresh fetch.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from connectors.psx_historical import PSXHistoricalConnector


SYMBOL = "ABL"
TARGET = Path("data/ohlcv") / f"{SYMBOL}.parquet"

c = PSXHistoricalConnector()
rows = c.fetch_symbol(SYMBOL)
print(f"Fetched {len(rows)} rows from PSX historical for {SYMBOL}")
if not rows:
    print("Source returned 0 rows; aborting")
    raise SystemExit(1)

df = pd.DataFrame(rows)
df["date"] = pd.to_datetime(df["date"]).dt.date
df = df.sort_values("date").drop_duplicates("date", keep="last")
df = df.reset_index(drop=True)

# Schema sanity (other parquets use date as datetime64; let's match)
df["date"] = pd.to_datetime(df["date"])
df["symbol"] = SYMBOL

# Backup old file
if TARGET.exists():
    backup = TARGET.with_suffix(".parquet.bak")
    TARGET.replace(backup)
    print(f"Backed up old parquet -> {backup.name}")

# Write new
df.to_parquet(TARGET, index=False)
print(f"Wrote {len(df)} rows to {TARGET}")
print(f"Date range: {df['date'].min().date()} -> {df['date'].max().date()}")
print("Last 5 rows:")
print(df.tail(5).to_string(index=False))
