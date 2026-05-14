"""
Compare today's (May 14) live mid-session tape against last night's
strategist forecast — find gaps and explain them.

Inputs:
  - data/_strategist/latest.json    (last night's run)
  - data/ohlcv/<SYM>.parquet         (May 13 EOD close + recent history)
  - User's live mid-session marks    (passed in below from the screenshot)

For each tracked symbol we compute:
  yest_close → today_mid  =  intraday move so far
and bucket each name into:
  - WORKING       (today's tape aligns with strategist call)
  - NEUTRAL       (small move, inconclusive)
  - GAP           (today's tape contradicts strategist call)
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from collections import defaultdict
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

LIVE_MARKS = {
    "PABC":   {"price": 108.50, "day_pct":  2.85, "day_pnl":  2920},
    "MLCF":   {"price":  84.69, "day_pct":  0.45, "day_pnl":   361},
    "HUBC":   {"price": 212.72, "day_pct":  0.61, "day_pnl":   968},
    "FATIMA": {"price": 135.80, "day_pct":  0.13, "day_pnl":   117},
    "HBL":    {"price": 280.38, "day_pct":  0.04, "day_pnl":    30},
    "POL":    {"price": 657.70, "day_pct": -0.29, "day_pnl":  -230},
    "OGDC":   {"price": 324.94, "day_pct": -0.14, "day_pnl":  -139},
}

KSE100_LIVE = {"level": 167426, "day_pct": -0.01, "day_pts": -25}


def _bucket_call(action: str) -> str:
    a = (action or "").upper()
    if a in ("BUY", "BUY_VALUE", "ADD"):
        return "BUY"
    if a in ("TRIM", "SELL", "AVOID", "REDUCE"):
        return "SELL"
    if a in ("HOLD", "WATCH"):
        return "HOLD"
    return a or "?"


def _expected_direction(bucket: str) -> int:
    return {"BUY": +1, "SELL": -1, "HOLD": 0}.get(bucket, 0)


print("=" * 78)
print("TODAY'S MARKET TAPE  vs  LAST NIGHT'S STRATEGIST FORECAST")
print(f"Live mid-session snapshot from your screenshot (May 14, 12:56 PM PKT)")
print("=" * 78)


print(f"\nKSE-100 (live):  {KSE100_LIVE['level']:,}   "
      f"{KSE100_LIVE['day_pts']:+d}  ({KSE100_LIVE['day_pct']:+.2f}%)")
print("Forecast called for: DEFENSIVE / 80% cash / pre-IMF de-risk")
print("Actual: index essentially flat — no panic, no rally → BASE CASE so far")


strat = json.loads((ROOT/"data/_strategist/latest.json").read_text(encoding="utf-8"))
actions = strat.get("actions") or []
calls = {a.get("symbol"): a for a in actions}


print("\n" + "-" * 78)
print(f"{'SYM':<8}{'STRAT':<8}{'EXPECT':<8}"
      f"{'YEST→NOW':<13}{'GAP?':<14}{'WHY'}")
print("-" * 78)

results = []
for sym, live in LIVE_MARKS.items():
    call = calls.get(sym, {})
    bucket = call.get("bucket") or "?"
    family = _bucket_call(bucket)
    expected_dir = _expected_direction(family)
    actual_pct = live["day_pct"]
    actual_dir = (1 if actual_pct > 0.30 else
                  -1 if actual_pct < -0.30 else 0)
    if expected_dir == 0 and actual_dir == 0:
        status = "ALIGN-flat"
    elif expected_dir == actual_dir and expected_dir != 0:
        status = "WORKING"
    elif actual_dir == 0:
        status = "NEUTRAL"
    elif expected_dir != 0 and actual_dir != expected_dir:
        status = "GAP*"
    else:
        status = "?"
    results.append({"sym": sym, "bucket": bucket, "expected_dir": expected_dir,
                    "actual_pct": actual_pct, "status": status,
                    "reason": call.get("reason", "")})
    print(f"{sym:<8}{bucket:<8}{family:<8}"
          f"{actual_pct:+6.2f}%      {status:<14}{call.get('reason','')[:34]}")


gaps = [r for r in results if r["status"] == "GAP*"]
working = [r for r in results if r["status"] == "WORKING"]
neutral = [r for r in results if r["status"] in ("NEUTRAL", "ALIGN-flat")]


print("\n" + "=" * 78)
print(f"SCORECARD:  WORKING={len(working)}   NEUTRAL={len(neutral)}   "
      f"GAPS={len(gaps)}")
print("=" * 78)


print("\n--- Where the strategist is WORKING ---")
if not working:
    print("  (none — early, or muted session)")
for r in working:
    print(f"  {r['sym']:<6} ({r['bucket']:<5})  actual={r['actual_pct']:+5.2f}%  "
          f"→ thesis playing out")


print("\n--- Where there's NO INFO yet ---")
for r in neutral:
    print(f"  {r['sym']:<6} ({r['bucket']:<5})  actual={r['actual_pct']:+5.2f}%  "
          f"→ tape too quiet to validate or refute")


print("\n--- Where there's a GAP (strategist call vs tape) ---")
if not gaps:
    print("  (none — strategist is aligned with tape)")
for r in gaps:
    direction = "rallied" if r["actual_pct"] > 0 else "dropped"
    print(f"  {r['sym']:<6} ({r['bucket']:<5})  market {direction} {r['actual_pct']:+5.2f}%  "
          f"vs expected {('UP' if r['expected_dir']>0 else 'DOWN')}")
    print(f"          reason given: {r['reason'][:80]}")


print("\n" + "=" * 78)
print("SECTOR-LEVEL READ")
print("=" * 78)
sectors_today = {
    "Banking":    [LIVE_MARKS["PABC"]["day_pct"], LIVE_MARKS["HBL"]["day_pct"]],
    "Power":      [LIVE_MARKS["HUBC"]["day_pct"]],
    "Cement":     [LIVE_MARKS["MLCF"]["day_pct"]],
    "Fertilizer": [LIVE_MARKS["FATIMA"]["day_pct"]],
    "E&P":        [LIVE_MARKS["POL"]["day_pct"], LIVE_MARKS["OGDC"]["day_pct"]],
}
print(f"\n  {'Sector':<14}{'avg today':<14}{'strategist stance':<28}{'aligned?'}")
strategist_stance = {
    "Banking":    "TRIM/AVOID (IMF defensive)",
    "Power":      "TRIM (IMF defensive)",
    "Cement":     "HOLD (no IMF urgency)",
    "Fertilizer": "WATCH (neutral)",
    "E&P":        "BUY (Brent + IMF safe)",
}
expected_sign = {
    "Banking": -1, "Power": -1, "Cement": 0,
    "Fertilizer": 0, "E&P": +1,
}
for sec, vals in sectors_today.items():
    avg = sum(vals)/len(vals)
    sign = +1 if avg > 0.20 else -1 if avg < -0.20 else 0
    aligned = "YES" if sign == expected_sign[sec] else ("flat" if sign == 0 else "**NO**")
    print(f"  {sec:<14}{avg:+5.2f}%        {strategist_stance[sec]:<28}{aligned}")


print("\n" + "=" * 78)
print("BIGGEST DELTAS  (forecast vs reality)")
print("=" * 78)
for r in sorted(results, key=lambda x: abs(x["actual_pct"]), reverse=True):
    sign_match = "✓" if r["status"] == "WORKING" else ("·" if r["status"] in ("NEUTRAL","ALIGN-flat") else "✗")
    print(f"  {sign_match}  {r['sym']:<8} {r['bucket']:<6} "
          f"actual {r['actual_pct']:+5.2f}%   ({r['status']})")
