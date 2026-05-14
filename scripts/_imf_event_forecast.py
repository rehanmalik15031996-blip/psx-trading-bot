"""
Forecast tomorrow's likely market reaction to the IMF mission.

Combines:
  1. The 5-year backtest history (`fired` list per Friday, dict with `id`)
  2. Per-sector 5-day forward returns on those Fridays (sector_ret_5d_pct)
  3. Per-case backtest accuracy (avg_overlay_edge_pct, hit_rate_pct)
  4. Today's strategist briefing (which cases fired) + current prices
  5. User's portfolio holdings → expected rupee P&L
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from collections import defaultdict
from statistics import mean, median, stdev

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Today's fired playbook cases
# ---------------------------------------------------------------------------
strat = _load(ROOT/"data/_strategist/latest.json")
fired_today = []
for f in (strat.get("briefing") or {}).get("playbook_facts", []) or []:
    if isinstance(f, dict):
        fired_today.append(f.get("id") or f.get("case_id") or "?")
    else:
        fired_today.append(str(f))

print("=" * 76)
print("TOMORROW'S IMF EVENT  —  PROBABILITY-WEIGHTED FORECAST")
print("=" * 76)
log = strat.get("playbook_overlay_log") or []
cases_today = sorted({e.get("case_id") for e in log if isinstance(e, dict) and e.get("case_id")})
print(f"\nCases that drove today's strategist overlays:")
for c in cases_today or ["(none)"]:
    print(f"  - {c}")
print("\nHistorical proxy cases used for the forecast:")
print(f"  - imf_sba_eff_approval, imf_review_completed (direct IMF analogues)")
print(f"  - us_iran_oil_spike (currently firing, real history exists)")
print(f"  - behavioural_panic_3day, pkr_devaluation_shock (stress co-fires)")


# ---------------------------------------------------------------------------
# 2. Locate every historical Friday where an IMF-flavoured case fired
# ---------------------------------------------------------------------------
per_date = _load(ROOT/"data/_research/backtest_per_date.json")

imf_keywords = (
    "imf_review_mission_week",
    "pre_imf_de_risk_window",
    "imf_sba_eff_approval",
    "imf_review_completed",
    "us_iran_oil_spike",
    "behavioural_panic_3day",
    "pkr_devaluation_shock",
)

imf_rows = []
all_ids_seen = set()
for row in per_date:
    fired = row.get("fired") or []
    ids = [f.get("id") if isinstance(f, dict) else str(f) for f in fired]
    all_ids_seen.update(ids)
    if any(any(k in (i or "") for k in imf_keywords) for i in ids):
        imf_rows.append(row)

print(f"\nHistorical Fridays where an IMF-class case fired (5-yr backtest): "
      f"{len(imf_rows)} of {len(per_date)}")

if imf_rows:
    rets = [r.get("univ_ret_5d_pct") for r in imf_rows
            if r.get("univ_ret_5d_pct") is not None]
    n_down = sum(1 for r in rets if r < -1.0)
    n_flat = sum(1 for r in rets if -1.0 <= r <= 1.0)
    n_up = sum(1 for r in rets if r > 1.0)
    print(f"\n  Universe 5d returns on IMF-firing Fridays:")
    print(f"    mean   {mean(rets):+6.2f}%")
    print(f"    median {median(rets):+6.2f}%")
    print(f"    min    {min(rets):+6.2f}%   max {max(rets):+6.2f}%")
    print(f"    stdev  {stdev(rets):6.2f}%")
    print(f"  Distribution (n={len(rets)}):")
    print(f"    down   (<-1%)   : {n_down}   ({100*n_down/len(rets):.0f}%)")
    print(f"    flat (-1%..+1%) : {n_flat}   ({100*n_flat/len(rets):.0f}%)")
    print(f"    up    (>+1%)    : {n_up}   ({100*n_up/len(rets):.0f}%)")

    print(f"\n  10 worst IMF-firing Fridays in 5y catalog:")
    sorted_rows = sorted(imf_rows,
                         key=lambda r: r.get("univ_ret_5d_pct") or 0)
    for r in sorted_rows[:10]:
        ids = ",".join(sorted({(f.get("id") if isinstance(f, dict) else str(f))
                               for f in (r.get("fired") or [])
                               if any(k in (f.get("id") if isinstance(f, dict) else str(f))
                                       for k in imf_keywords)}))
        print(f"    {r.get('as_of')}  univ5d={r.get('univ_ret_5d_pct'):+6.2f}%  "
              f"({ids})")

    print(f"\n  5 best IMF-firing Fridays:")
    for r in sorted_rows[-5:][::-1]:
        ids = ",".join(sorted({(f.get("id") if isinstance(f, dict) else str(f))
                               for f in (r.get("fired") or [])
                               if any(k in (f.get("id") if isinstance(f, dict) else str(f))
                                       for k in imf_keywords)}))
        print(f"    {r.get('as_of')}  univ5d={r.get('univ_ret_5d_pct'):+6.2f}%  "
              f"({ids})")


# ---------------------------------------------------------------------------
# 3. Per-sector behaviour on IMF firing Fridays
# ---------------------------------------------------------------------------
target_sectors = ("Banking", "Cement", "Power", "Fertilizer",
                  "Oil & Gas E&P", "OMC")
sector_rets = defaultdict(list)
for r in imf_rows:
    sec_ret = r.get("sector_ret_5d_pct") or {}
    for s in target_sectors:
        v = sec_ret.get(s)
        if v is not None:
            sector_rets[s].append(v)

print(f"\n\nPer-sector 5-day returns on IMF-firing Fridays:")
print(f"  {'Sector':<22} {'n':>3}  {'mean':>7}  {'median':>7}  "
      f"{'p20':>7}  {'p80':>7}  {'min':>7}  {'max':>7}")
for s in target_sectors:
    vs = sorted(sector_rets.get(s, []))
    if not vs:
        continue
    n = len(vs)
    p20 = vs[max(0, n // 5)]
    p80 = vs[min(n-1, (4 * n) // 5)]
    print(f"  {s:<22} {n:>3}  "
          f"{mean(vs):+7.2f}  {median(vs):+7.2f}  "
          f"{p20:+7.2f}  {p80:+7.2f}  "
          f"{min(vs):+7.2f}  {max(vs):+7.2f}")


# ---------------------------------------------------------------------------
# 4. Per-case accuracy
# ---------------------------------------------------------------------------
per_case = _load(ROOT/"data/_research/backtest_per_case.json")
print("\n\nBacktest accuracy of IMF-class cases (5-yr):")
for case in per_case:
    cid = case.get("case_id", "")
    if any(k in cid for k in imf_keywords):
        n_f = case.get("n_fires") or 0
        edge = case.get("avg_overlay_edge_pct")
        hit = case.get("hit_rate_pct")
        univ = case.get("avg_univ_5d_pct")
        print(f"  {cid}")
        print(f"     fires={n_f}  avg_univ_5d={univ}%  "
              f"hit_rate={hit}%  avg_edge={edge}%")


# ---------------------------------------------------------------------------
# 5. Per-symbol expected move
# ---------------------------------------------------------------------------
holdings = [
    ("PABC",   "Banking",        112.68, 108.50, 970),
    ("MLCF",   "Cement",          93.65,  84.69, 950),
    ("HUBC",   "Power",          225.04, 212.72, 750),
    ("FATIMA", "Fertilizer",     141.15, 135.80, 650),
    ("HBL",    "Banking",        299.25, 280.38, 300),
    ("POL",    "Oil & Gas E&P",  661.50, 657.70, 120),
    ("OGDC",   "Oil & Gas E&P",  327.32, 324.94, 295),
]

print("\n\n" + "=" * 76)
print("PER-SYMBOL EXPECTED 5-DAY MOVE FROM TODAY (Rs 212.72 etc.)")
print("=" * 76)


def _sector_band(sec: str):
    vs = sorted(sector_rets.get(sec, []))
    if not vs:
        return (-3.0, -1.0, +1.0)
    n = len(vs)
    p20 = vs[max(0, n // 5)]
    p50 = median(vs)
    p80 = vs[min(n-1, (4 * n) // 5)]
    return (p20, p50, p80)


sym_adj = {
    "PABC":   (-1.5, -0.5, 0.0),
    "HBL":    (-0.5,  0.0, 0.0),
    "MLCF":   (-0.5,  0.0, 0.0),
    "HUBC":   ( 0.0,  0.0, 0.0),
    "FATIMA": ( 0.0,  0.0, 0.0),
    "POL":    ( 0.5,  0.5, 0.5),
    "OGDC":   ( 0.5,  0.5, 0.5),
}

total = {"bear": 0.0, "base": 0.0, "bull": 0.0}
for sym, sec, _avg, now, shr in holdings:
    bear, base, bull = _sector_band(sec)
    a_bear, a_base, a_bull = sym_adj.get(sym, (0, 0, 0))
    bear += a_bear; base += a_base; bull += a_bull
    bear_px = now * (1 + bear/100)
    base_px = now * (1 + base/100)
    bull_px = now * (1 + bull/100)
    pl_bear = (bear_px - now) * shr
    pl_base = (base_px - now) * shr
    pl_bull = (bull_px - now) * shr
    total["bear"] += pl_bear
    total["base"] += pl_base
    total["bull"] += pl_bull
    print(f"\n  {sym}  ({sec})   now={now:.2f}   shares={shr}")
    print(f"    bear (p20 + sym):   {bear:+5.2f}%  →  {bear_px:7.2f}   "
          f"Rs {pl_bear:+8,.0f}")
    print(f"    base (median):      {base:+5.2f}%  →  {base_px:7.2f}   "
          f"Rs {pl_base:+8,.0f}")
    print(f"    bull (p80 + sym):   {bull:+5.2f}%  →  {bull_px:7.2f}   "
          f"Rs {pl_bull:+8,.0f}")

print("\n" + "=" * 76)
print("PORTFOLIO TOTAL 5-DAY INCREMENTAL P&L FROM HERE")
print("=" * 76)
print(f"  Bear case (IMF hawkish leak):  Rs {total['bear']:+10,.0f}")
print(f"  Base case (IMF neutral):       Rs {total['base']:+10,.0f}")
print(f"  Bull case (IMF dovish surprise / no surprise): "
      f"Rs {total['bull']:+10,.0f}")
print("=" * 76)
