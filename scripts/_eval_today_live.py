"""Pull live PSX market watch tape (Monday May 18 mid-session) and
compare against Friday May 15 close to evaluate our strategist call.

Strategist call (committed cb378eb @ 01:25 PKT today):
  BUY HIGH:  OGDC (size 6.7%, stop -5.6%)
  HOLD:      ATRL, PPL, POL, MARI
  SHORT 3%:  DGKC, KOHC (auto-promoted from Cement -4 tilt)
  AVOID:    KEL, HUBC, EPCL, KAPCO, NPL
  CASH:      50%

We are testing whether the new pre_event_derisk driver + the
predictor guards are pointing us in the right direction.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from connectors.psx_portal import PSXMarketWatchConnector, PSXIndicesConnector


# Universe — our 35-stock tracked set
UNIVERSE = [
    "OGDC", "PPL", "POL", "MARI", "PSO", "APL", "ATRL",
    "HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL",
    "DGKC", "KOHC", "LUCK", "MLCF", "FCCL",
    "KEL", "HUBC", "KAPCO", "NPL",
    "ENGROH", "EPCL", "LOTCHEM",
    "FFC", "EFERT", "FATIMA",
    "SEARL", "INDU", "PABC", "COLG", "SYS", "TRG",
]

# Strategist call buckets
STRATEGIST = {
    "BUY":  ["OGDC"],
    "HOLD": ["ATRL", "PPL", "POL", "MARI"],
    "SHORT": ["DGKC", "KOHC"],
    "AVOID": ["KEL", "HUBC", "EPCL", "KAPCO", "NPL"],
}

# Fri close cache
FRI_CLOSE = {}
for sym in UNIVERSE:
    p = Path("data/ohlcv") / f"{sym}.parquet"
    if not p.exists():
        continue
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    fri = df[df["date"].dt.date == pd.to_datetime("2026-05-15").date()]
    if not fri.empty:
        FRI_CLOSE[sym] = float(fri["close"].iloc[-1])

# Pull live tape
print("Fetching live PSX market watch...")
c = PSXMarketWatchConnector()
res = c.fetch()
recs = getattr(res, "records", [])
print(f"  got {len(recs)} symbols, captured at {datetime.now().isoformat()}")

by_sym = {r["symbol"]: r for r in recs}

# Connector now canonicalises XD/XB/XR suffixes, so OGDCXD shows
# up under symbol="OGDC" with ex_div=True. Below we just consume.
rows = []
for sym in UNIVERSE:
    r = by_sym.get(sym)
    if not r:
        continue
    fri = FRI_CLOSE.get(sym)
    live = r.get("current") or r.get("close")
    chg_vs_fri = (live / fri - 1) * 100 if (fri and live) else None
    chg_vs_ldcp = r.get("change_pct")
    rows.append({
        "symbol":      sym,
        "sector":      r.get("sector_name"),
        "fri_close":   fri,
        "live":        live,
        "high":        r.get("high"),
        "low":         r.get("low"),
        "vol":         r.get("volume"),
        "chg_today%":  chg_vs_ldcp,
        "chg_vs_fri%": chg_vs_fri,
        "ex_div":      bool(r.get("ex_div")),
    })

df = pd.DataFrame(rows)

# Pull KSE-100 + sector indices
print()
print("Fetching live indices...")
try:
    idx_res = PSXIndicesConnector().fetch()
    idx_recs = getattr(idx_res, "records", [])
    print(f"  got {len(idx_recs)} index records")
    interesting = ["KSE100", "KSE30", "KSEALL", "BANK", "OG&P", "OGT", "CEMENT", "PWR"]
    for ir in idx_recs:
        if any(x in str(ir.get("index_name", "")).upper() for x in interesting):
            print(f"  {ir.get('index_name'):<25} "
                  f"current={ir.get('current')}  "
                  f"change_pct={ir.get('change_pct')}  "
                  f"vol={ir.get('volume')}")
except Exception as e:
    print(f"  indices fetch failed: {e}")

# ---------- Strategist-call evaluation ----------
print("\n=== Strategist call evaluation (Mon mid-session vs Fri close) ===\n")
for bucket, syms in STRATEGIST.items():
    sub = df[df["symbol"].isin(syms)].copy()
    if sub.empty:
        continue
    avg = sub["chg_vs_fri%"].mean()
    print(f"  {bucket:<6} {syms}")
    print(f"         avg vs Fri = {avg:+.2f}%, today avg = {sub['chg_today%'].mean():+.2f}%")
    for _, r in sub.iterrows():
        xd = " [EX-DIV]" if r.get("ex_div") else ""
        print(f"    {r['symbol']:<7}  Fri {r['fri_close']:>7.2f} -> "
              f"live {r['live']:>7.2f}   today {r['chg_today%']:>+6.2f}%   "
              f"H/L {r['high']:>7.2f}/{r['low']:>7.2f}{xd}")
    print()

# ---------- Universe view by sector ----------
print("=== Universe by sector ===\n")
g = df.groupby("sector").agg(
    n=("symbol", "count"),
    avg_today=("chg_today%", "mean"),
    avg_vs_fri=("chg_vs_fri%", "mean"),
).round(2).sort_values("avg_today")
print(g.to_string())

# ---------- Winners + losers ----------
print("\n=== Biggest movers today ===")
print("\nTop 10 GAINERS (universe):")
print(df.nlargest(10, "chg_today%")[
    ["symbol", "sector", "live", "chg_today%", "chg_vs_fri%"]
].to_string(index=False))
print("\nTop 10 LOSERS (universe):")
print(df.nsmallest(10, "chg_today%")[
    ["symbol", "sector", "live", "chg_today%", "chg_vs_fri%"]
].to_string(index=False))

# Save
df["captured_at_utc"] = datetime.utcnow().isoformat(timespec="seconds")
out = Path("data/_research/MAY18_LIVE_REACTION.csv")
df.to_csv(out, index=False)
print(f"\nSaved live snapshot to {out}")
