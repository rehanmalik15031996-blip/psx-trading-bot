"""Check what EOD data we have for May 14, 2026."""
import pandas as pd
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

# Macro KSE-100
print("=== KSE-100 ===")
df = pd.read_parquet(ROOT/"data/macro/kse100.parquet")
print(f"columns: {list(df.columns)}")
df["date"] = pd.to_datetime(df["date"]).dt.date
df = df.sort_values("date").tail(10)
print(df.to_string(index=False))

# User's portfolio symbols
syms = ["PABC", "MLCF", "HUBC", "FATIMA", "HBL", "POL", "OGDC"]
print("\n=== User portfolio symbols (last 4 closes) ===")
for s in syms:
    p = ROOT/f"data/ohlcv/{s}.parquet"
    if not p.exists():
        print(f"  {s}: NOT FOUND")
        continue
    d = pd.read_parquet(p)
    d["date"] = pd.to_datetime(d["date"]).dt.date
    d = d.sort_values("date").tail(4)
    closes = [(str(r['date']), float(r['close'])) for _, r in d.iterrows()]
    print(f"  {s:<8} {closes}")
