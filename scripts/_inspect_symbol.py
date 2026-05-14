"""Quick per-symbol look-up: recent predictions + price action."""
from __future__ import annotations
import json, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")
sym = (sys.argv[1] if len(sys.argv) > 1 else "HUBC").upper()
print(f"=== {sym} ===\n")

log = json.loads((ROOT/"data/predictions_log.json").read_text(encoding="utf-8"))
preds = [p for p in log.get("predictions", []) if p.get("symbol") == sym]
print(f"Predictions on file: {len(preds)}")
for p in preds[-8:]:
    print(f"  {(p.get('generated_at') or '')[:10]}  "
          f"model={p.get('model'):<22}  "
          f"dir={(p.get('direction') or '?'):<8}  "
          f"act={(p.get('suggested_action') or '?'):<6}  "
          f"conv={(p.get('conviction') or '?'):<6}  "
          f"pred5d={p.get('predicted_return_5d_pct')}")

df = pd.read_parquet(ROOT/f"data/ohlcv/{sym}.parquet")
df["date"] = pd.to_datetime(df["date"]).dt.date
df = df.sort_values("date").tail(12).reset_index(drop=True)
print()
print(f"{sym} last 12 sessions:")
cols = list(df.columns)
for _, r in df.iterrows():
    parts = [f"{r['date']}"]
    for f in ("open", "high", "low", "close"):
        if f in cols and pd.notna(r[f]):
            parts.append(f"{f[0].upper()}={r[f]:.2f}")
    if "volume" in cols and pd.notna(r["volume"]):
        parts.append(f"Vol={r['volume']:>10.0f}")
    print("  " + "  ".join(parts))
c10 = df.iloc[0]["close"]; c_now = df.iloc[-1]["close"]
print(f"  12d ret: {(c_now/c10-1)*100:+.2f}%   close: {c_now:.2f}")

dfa = pd.read_parquet(ROOT/f"data/ohlcv/{sym}.parquet")
dfa["date"] = pd.to_datetime(dfa["date"]).dt.date
dfa = dfa.sort_values("date")
def _ret_n(n):
    if len(dfa) <= n:
        return None
    c0 = float(dfa.iloc[-n-1]["close"]); c1 = float(dfa.iloc[-1]["close"])
    return (c1/c0-1)*100 if c0 > 0 else None
print()
print(f"  trailing 5d:   {(_ret_n(5)  or 0):+.2f}%")
print(f"  trailing 21d:  {(_ret_n(21) or 0):+.2f}%")
print(f"  trailing 63d:  {(_ret_n(63) or 0):+.2f}%")
print(f"  trailing 150d: {(_ret_n(150) or 0):+.2f}%")
