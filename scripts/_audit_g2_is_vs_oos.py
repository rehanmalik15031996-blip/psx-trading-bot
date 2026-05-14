"""Gap-2: split the existing 5-year backtest into in-sample (pre-tuning)
vs out-of-sample (post-tuning) windows and compare edge degradation.

Critical reminder: The playbook was tuned in three rounds (Round-1, -2,
-3 of `_apply_research_fixes_*`) all in May 2026. Every case in
cases.json was either authored or modified with full knowledge of the
2021-2026 backtest results. So technically *no* date in our parquet is
strictly out-of-sample for the playbook itself.

What we CAN do is split by sub-period and check whether the edge is
stable, or whether it's concentrated in the period the tuning was most
aggressive on (the recent 12 months that the v2 / v3 rounds focused on
per the commit log). If edge collapses for distant historical periods,
that's a smoking gun for iteration / regime bias.

Splits:
  IS_distant: 2021-06 .. 2024-12 (the "long history" — less aggressive tuning)
  IS_recent:  2025-01 .. 2026-04 (the "recent window" — heavily tuned)
  OOS_live:   2026-05 onward     (period after most fixes shipped)

For each split we compute:
  - n_dates, n_fires
  - mean overlay_pnl_5d, mean baseline_pnl_5d, mean univ_5d
  - overlay edge = overlay_pnl - baseline_pnl
  - overlay edge vs universe = overlay_pnl - univ_5d
  - hit rate: fraction of dates where overlay_pnl > baseline_pnl

If IS_recent edge >> IS_distant edge, that's strong evidence of
in-sample overfitting (or genuine regime shift; the test can't fully
distinguish).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import date
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DATA = ROOT / "data" / "_research" / "backtest_per_date.json"


def _bucket(d: str) -> str:
    y, m, _ = d.split("-")
    y, m = int(y), int(m)
    # Tuning rounds were applied in May 2026. v2 (May 13) and v3
    # touched the playbook with the most recent 12 months in focus.
    if (y, m) <= (2024, 12):
        return "IS_distant_21_24"
    if (y, m) <= (2026, 4):
        return "IS_recent_25_apr26"
    return "OOS_may26_onward"


def _safe_stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    import statistics as st
    return {
        "n": len(values),
        "mean": st.mean(values),
        "median": st.median(values),
        "stdev": st.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def main() -> int:
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    print(f"Loaded {len(rows)} backtest dates")

    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_bucket[_bucket(r["as_of"])].append(r)

    print()
    print(f"{'bucket':<22} {'n':>4} {'first':<12} {'last':<12}")
    for b in ["IS_distant_21_24", "IS_recent_25_apr26", "OOS_may26_onward"]:
        rs = by_bucket.get(b, [])
        if not rs:
            print(f"{b:<22} {'0':>4} (no rows)")
            continue
        print(f"{b:<22} {len(rs):>4} {rs[0]['as_of']:<12} {rs[-1]['as_of']:<12}")

    print()
    print("=" * 92)
    print("Per-bucket performance (forward-5d)")
    print("=" * 92)
    header = (f"{'bucket':<22} {'n':>4} {'univ_5d%':>9} {'base_5d%':>9} "
              f"{'over_5d%':>9} {'edge_vs_base':>13} {'edge_vs_univ':>13} "
              f"{'win_rate':>9} {'fires/day':>10}")
    print(header)
    print("-" * 92)

    summaries: dict[str, dict] = {}
    for b in ["IS_distant_21_24", "IS_recent_25_apr26", "OOS_may26_onward"]:
        rs = by_bucket.get(b, [])
        if not rs:
            continue
        univ   = [r["univ_ret_5d_pct"]      for r in rs if r.get("univ_ret_5d_pct") is not None]
        base   = [r["pnl_baseline_5d_pct"]  for r in rs if r.get("pnl_baseline_5d_pct") is not None]
        over   = [r["pnl_overlay_5d_pct"]   for r in rs if r.get("pnl_overlay_5d_pct") is not None]
        edge_b = [o - bv for o, bv in zip(over, base)]
        edge_u = [o - u for o, u in zip(over, univ)]
        wins   = sum(1 for e in edge_b if e > 0)
        fires  = sum(r.get("n_analogues", 0) for r in rs) / max(len(rs), 1)

        summaries[b] = {
            "n": len(rs),
            "univ_mean": (sum(univ)/len(univ) if univ else 0),
            "base_mean": (sum(base)/len(base) if base else 0),
            "over_mean": (sum(over)/len(over) if over else 0),
            "edge_vs_base_mean": (sum(edge_b)/len(edge_b) if edge_b else 0),
            "edge_vs_univ_mean": (sum(edge_u)/len(edge_u) if edge_u else 0),
            "win_rate": wins / max(len(edge_b), 1),
            "fires_per_day": fires,
        }
        s = summaries[b]
        print(f"{b:<22} {s['n']:>4} {s['univ_mean']:>+8.2f}% "
              f"{s['base_mean']:>+8.2f}% {s['over_mean']:>+8.2f}% "
              f"{s['edge_vs_base_mean']:>+12.2f}pp "
              f"{s['edge_vs_univ_mean']:>+12.2f}pp "
              f"{s['win_rate']*100:>8.1f}% "
              f"{s['fires_per_day']:>9.2f}")

    print()
    print("=" * 92)
    print("VERDICT")
    print("=" * 92)
    if "IS_distant_21_24" in summaries and "IS_recent_25_apr26" in summaries:
        d = summaries["IS_distant_21_24"]
        r = summaries["IS_recent_25_apr26"]
        ed = d["edge_vs_base_mean"]
        er = r["edge_vs_base_mean"]
        ratio = er / ed if abs(ed) > 1e-6 else float("inf")
        delta_pp = er - ed
        print(f"IS_distant edge: {ed:+.2f}pp")
        print(f"IS_recent edge:  {er:+.2f}pp")
        print(f"Recent / Distant ratio: {ratio:.2f}x" if ed != 0 else
              "Recent / Distant ratio: undefined (distant edge ~0)")
        print(f"Delta (recent - distant): {delta_pp:+.2f}pp")
        print()
        if er > 0 and ed <= 0:
            print("[MEANINGFUL] Edge appears only in the recent (heavily-tuned) "
                  "window. Distant period shows zero/negative overlay edge.")
            print("            INTERPRETATION: most of the backtested edge is "
                  "in-sample / curve-fit to 2025-2026. Real OOS edge is unclear.")
            print("            ACTION: aggressively prune cases with poor "
                  "performance in 2021-2024 sub-window.")
        elif er > 0 and ed > 0 and abs(delta_pp) < 1.5:
            print("[NOT MEANINGFUL] Edge is comparable across IS_distant and "
                  "IS_recent. Looks like a genuine, stable signal — not "
                  "iteration bias.")
        elif er > 0 and ed > 0 and delta_pp > 1.5:
            print(f"[PARTIAL] Edge present in both windows but materially "
                  f"larger in recent ({delta_pp:+.2f}pp). Some iteration bias "
                  "likely, but baseline edge is positive in older window.")
            print("            ACTION: trust the baseline-edge magnitude from "
                  "the distant window; recent numbers are optimistic.")
        elif er < 0:
            print("[CRITICAL] Recent window edge is NEGATIVE despite intensive "
                  "tuning — this is a serious problem in the playbook, not "
                  "just iteration bias.")
        else:
            print("[INCONCLUSIVE] Mixed signals. Inspect per-case.")

        if "OOS_may26_onward" in summaries:
            oos = summaries["OOS_may26_onward"]
            print()
            print(f"OOS (May 2026+) data points: n={oos['n']}")
            print(f"  OOS edge_vs_base: {oos['edge_vs_base_mean']:+.2f}pp")
            print(f"  OOS edge_vs_univ: {oos['edge_vs_univ_mean']:+.2f}pp")
            if oos["n"] < 5:
                print("  (Too few OOS dates to draw conclusions yet.)")
            else:
                if oos["edge_vs_base_mean"] > 0 and oos["edge_vs_base_mean"] >= 0.5 * er:
                    print("  -> OOS holds at least half the recent IS edge. "
                          "Generalization looks OK.")
                else:
                    print("  -> OOS edge has collapsed vs recent IS. "
                          "Strong evidence of in-sample fitting.")

    out = ROOT / "data" / "_research" / "backtest_is_vs_oos_split.json"
    out.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
