"""Period-slice analyser for the 5-year backtest.

Reads `data/_research/backtest_per_date.json` and aggregates metrics
for an arbitrary date window. Default window: last 12 months.
"""
from __future__ import annotations
import json
import sys
from collections import defaultdict, Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

PER_DATE = ROOT / "data" / "_research" / "backtest_per_date.json"


def slice_and_summarise(rows: list[dict], start: date, end: date,
                          label: str = "") -> dict:
    sub = [r for r in rows
           if start <= datetime.fromisoformat(r["as_of"]).date() <= end]
    if not sub:
        return {"label": label, "n": 0}

    sum_b5  = sum(r.get("pnl_baseline_5d_pct")  or 0 for r in sub)
    sum_o5  = sum(r.get("pnl_overlay_5d_pct")   or 0 for r in sub)
    sum_b21 = sum(r.get("pnl_baseline_21d_pct") or 0 for r in sub)
    sum_o21 = sum(r.get("pnl_overlay_21d_pct")  or 0 for r in sub)

    cum_b = cum_o = peak_b = peak_o = dd_b = dd_o = 0.0
    for r in sub:
        cum_b += r.get("pnl_baseline_5d_pct") or 0
        cum_o += r.get("pnl_overlay_5d_pct")  or 0
        peak_b = max(peak_b, cum_b)
        peak_o = max(peak_o, cum_o)
        dd_b = min(dd_b, cum_b - peak_b)
        dd_o = min(dd_o, cum_o - peak_o)

    avg_univ_5d  = mean(r.get("univ_ret_5d_pct")  or 0 for r in sub)
    avg_univ_21d = mean(r.get("univ_ret_21d_pct") or 0 for r in sub)
    fires = Counter(r.get("n_analogues") or 0 for r in sub)
    n_zero = fires.get(0, 0)
    gap_weeks = [r for r in sub
                 if (r.get("n_analogues") or 0) == 0
                 and (r.get("univ_ret_5d_pct") or 0) <= -3.0]

    case_fires: dict[str, list[float]] = defaultdict(list)
    for r in sub:
        for f in r.get("fired") or []:
            case_fires[f["id"]].append(r.get("univ_ret_5d_pct") or 0)
    top_cases = sorted(case_fires.items(), key=lambda kv: -len(kv[1]))[:12]

    return {
        "label": label,
        "from": str(sub[0]["as_of"])[:10],
        "to":   str(sub[-1]["as_of"])[:10],
        "n_weeks": len(sub),
        "avg_univ_5d_pct":  avg_univ_5d,
        "avg_univ_21d_pct": avg_univ_21d,
        "sum_baseline_5d_pct":  sum_b5,
        "sum_overlay_5d_pct":   sum_o5,
        "edge_5d_pct":          sum_o5 - sum_b5,
        "sum_baseline_21d_pct": sum_b21,
        "sum_overlay_21d_pct":  sum_o21,
        "edge_21d_pct":         sum_o21 - sum_b21,
        "max_dd_baseline_pct":  dd_b,
        "max_dd_overlay_pct":   dd_o,
        "dd_saved_pp":          dd_o - dd_b,
        "n_zero_fire":          n_zero,
        "n_gap_weeks":          len(gap_weeks),
        "fires_histogram":      dict(fires),
        "top_cases":            [(cid, len(rs), mean(rs) if rs else 0)
                                  for cid, rs in top_cases],
    }


def print_block(s: dict) -> None:
    print(f"\n=== {s['label']:<32} ===")
    print(f"   window: {s['from']}  ->  {s['to']}   ({s['n_weeks']} weeks)")
    print(f"   avg univ 5d / 21d : {s['avg_univ_5d_pct']:+.3f}% / "
          f"{s['avg_univ_21d_pct']:+.3f}%")
    print(f"   sum baseline 5d : {s['sum_baseline_5d_pct']:+8.2f}%")
    print(f"   sum overlay  5d : {s['sum_overlay_5d_pct']:+8.2f}%   "
          f"edge {s['edge_5d_pct']:+.2f}%")
    print(f"   sum baseline 21d: {s['sum_baseline_21d_pct']:+8.2f}%")
    print(f"   sum overlay  21d: {s['sum_overlay_21d_pct']:+8.2f}%   "
          f"edge {s['edge_21d_pct']:+.2f}%")
    print(f"   max DD baseline : {s['max_dd_baseline_pct']:+.2f}%")
    print(f"   max DD overlay  : {s['max_dd_overlay_pct']:+.2f}%   "
          f"saved {s['dd_saved_pp']:+.2f}pp")
    print(f"   zero-fire weeks : {s['n_zero_fire']}    "
          f"gap-down weeks: {s['n_gap_weeks']}")
    print(f"   top 8 cases (fires, avg univ 5d when fired):")
    for cid, n, avg in s["top_cases"][:8]:
        print(f"     {cid:<36}  n={n:>3}   univ5d={avg:+5.2f}%")


def main() -> int:
    rows = json.loads(PER_DATE.read_text(encoding="utf-8"))
    print(f"loaded {len(rows)} weekly samples")

    last_row = max(datetime.fromisoformat(r["as_of"]).date() for r in rows)
    one_year   = last_row - timedelta(days=365)
    six_months = last_row - timedelta(days=183)
    three_mo   = last_row - timedelta(days=92)
    full_start = min(datetime.fromisoformat(r["as_of"]).date() for r in rows)

    out = {
        "FULL_5Y":  slice_and_summarise(rows, full_start, last_row, "FULL 5-year"),
        "LAST_1Y":  slice_and_summarise(rows, one_year,   last_row, "LAST 12 months"),
        "LAST_6M":  slice_and_summarise(rows, six_months, last_row, "LAST 6 months"),
        "LAST_3M":  slice_and_summarise(rows, three_mo,   last_row, "LAST 3 months"),
    }

    for s in out.values():
        print_block(s)

    out_path = ROOT / "data" / "_research" / "backtest_period_slices.json"
    out_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\n[slices] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
