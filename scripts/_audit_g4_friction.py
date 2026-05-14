"""Gap-4: estimate how much transaction-cost friction destroys the
overlay's edge.

Approach: take the 5-year per-date backtest, estimate per-date
turnover (sum of |new_weight - old_weight| across symbols, where
"old_weight" is yesterday's baseline-HOLD weight, "new_weight" is
the post-overlay weight). Apply a friction haircut of N bps per
unit-turnover and recompute the edge.

The overlay has substantial turnover because it moves whole sectors
between buckets (HOLD->TRIM->AVOID) on event days. Real-world PSX
trading on these names typically costs 25-50 bps round-trip (spread +
commission + slippage). We test at 5/15/25/50/100 bps to see at what
level the edge dies.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import statistics as st

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DATA = ROOT / "data" / "_research" / "backtest_per_date.json"

# Bucket -> long weight (same map as _research_backtest._portfolio_pnl)
BUCKET_LONG = {"BUY": 1.0, "ADD": 0.75, "HOLD": 0.50,
                "WATCH": 0.25, "AVOID": 0.0, "TRIM": 0.0}


def _per_symbol_weights(decision_actions, n_universe):
    """Map symbol -> long_weight where each name carries
    (1/n_universe) * long_fraction (* deployable_after_cash_floor)."""
    weights = {}
    cash_action = next(
        (a for a in decision_actions
         if (a.get("bucket") or "").upper() == "CASH"
         and not a.get("symbol")), None)
    cash_floor = float((cash_action or {}).get("target_weight_pct") or 0)
    deployable = max(0.0, 100.0 - cash_floor) / 100.0
    for a in decision_actions:
        sym = a.get("symbol")
        if not sym:
            continue
        b = (a.get("bucket") or "HOLD").upper()
        long_frac = BUCKET_LONG.get(b, 0.5)
        weights[sym] = (1.0 / n_universe) * long_frac * deployable
    return weights, cash_floor


def main():
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    print(f"Loaded {len(rows)} dates")

    # Baseline has every symbol at HOLD (0.5 weight scaled by 1/N).
    # Overlay changes some to other buckets. The per-date turnover is
    # sum of |w_overlay[s] - w_baseline[s]| across symbols.
    # Since BASELINE weights are constant (all HOLD), once-only turnover
    # of (sum |w_overlay - w_baseline|) is paid the FIRST time we put
    # the overlay portfolio on. Subsequent dates only pay the delta from
    # day-to-day overlay changes.
    #
    # We approximate by assuming the user runs the overlay daily and
    # rebalances to the latest overlay weights. Day-to-day turnover =
    # sum |w_today[s] - w_yest[s]|.

    # Reconstruct per-symbol weights for each date from the
    # playbook_overlay_log? Unfortunately the per_date rows don't carry
    # full action lists. We have aggregate gross_long though.
    # Workaround: use the difference gross_overlay - gross_baseline as a
    # PROXY for turnover magnitude (the rough fraction of book repriced).
    # Conservative: assume turnover = max(|gross_today - gross_yest|,
    #                                       0.10 * gross_today)
    # i.e. at least 10% turnover/day even on quiet days.

    edges = []   # (date, univ_5d, base_5d, over_5d, gross_overlay)
    for r in rows:
        if (r.get("pnl_baseline_5d_pct") is None or
            r.get("pnl_overlay_5d_pct") is None):
            continue
        edges.append({
            "as_of": r["as_of"],
            "univ_5d":  r.get("univ_ret_5d_pct") or 0,
            "base_5d":  r["pnl_baseline_5d_pct"],
            "over_5d":  r["pnl_overlay_5d_pct"],
            "gross":    r.get("gross_overlay_5d") or 0,
        })

    # Compute day-over-day gross change (proxy for total weight churn).
    edges.sort(key=lambda x: x["as_of"])
    prev_gross = None
    for e in edges:
        if prev_gross is None:
            e["churn_proxy"] = 0.10   # initial deploy cost
        else:
            e["churn_proxy"] = max(abs(e["gross"] - prev_gross), 0.05)
        prev_gross = e["gross"]

    print(f"\nMean churn_proxy per date: "
          f"{st.mean(e['churn_proxy'] for e in edges):.3f}")
    print(f"Median:    {st.median(e['churn_proxy'] for e in edges):.3f}")
    print(f"P95:       {sorted(e['churn_proxy'] for e in edges)[int(len(edges)*0.95)]:.3f}")
    print()

    # Apply friction at various bps levels. Cost per date = churn * bps.
    # The strategy is rebalanced on a weekly cadence (the backtest
    # walks Fridays). Cost is paid at rebalance, then carried through
    # the 5d forward window.
    print(f"{'bps/turn':<10} {'mean_edge_pre':>13} {'mean_edge_post':>14} "
          f"{'crash_edge_pre':>15} {'crash_edge_post':>16}")
    print("-" * 70)
    summary = {}
    for bps in [0, 5, 15, 25, 50, 100]:
        cost = lambda e, bps=bps: e["churn_proxy"] * (bps / 100.0)
        # edge vs baseline, BEFORE costs:
        pre_edges = [e["over_5d"] - e["base_5d"] for e in edges]
        post_edges = [
            (e["over_5d"] - cost(e)) - e["base_5d"]
            for e in edges
        ]
        crash_pre  = [e["over_5d"] - e["base_5d"]
                       for e in edges if e["univ_5d"] <= -3.0]
        crash_post = [(e["over_5d"] - cost(e)) - e["base_5d"]
                       for e in edges if e["univ_5d"] <= -3.0]
        m_pre   = st.mean(pre_edges)
        m_post  = st.mean(post_edges)
        cm_pre  = (st.mean(crash_pre) if crash_pre else 0)
        cm_post = (st.mean(crash_post) if crash_post else 0)
        summary[bps] = {
            "mean_edge_pre":  m_pre,
            "mean_edge_post": m_post,
            "crash_edge_pre": cm_pre,
            "crash_edge_post": cm_post,
        }
        print(f"{bps:<10} {m_pre:>+12.3f}pp {m_post:>+13.3f}pp "
              f"{cm_pre:>+14.3f}pp {cm_post:>+15.3f}pp")

    print()
    print("=" * 80)
    print("VERDICT")
    print("=" * 80)
    e25 = summary[25]
    e50 = summary[50]
    if e25["mean_edge_post"] <= -0.02 and e25["crash_edge_post"] <= 0:
        print("[MEANINGFUL] At a realistic 25 bps/turn friction, mean "
              f"edge becomes {e25['mean_edge_post']:+.2f}pp (was "
              f"{e25['mean_edge_pre']:+.2f}pp) and crash edge becomes "
              f"{e25['crash_edge_post']:+.2f}pp (was "
              f"{e25['crash_edge_pre']:+.2f}pp). Edge is below noise.")
        print("ACTION: lower turnover by debouncing overlay reactions "
              "(e.g. require a case to fire 2 days in a row before "
              "changing buckets) or by sizing reactions to signal "
              "strength so we don't churn on every micro-move.")
    elif e25["crash_edge_post"] > 0 and e25["crash_edge_post"] >= 0.3:
        print(f"[ROBUST] At 25 bps friction, crash protection survives "
              f"({e25['crash_edge_post']:+.2f}pp). The flat-week edge "
              "may go slightly negative but the system still earns its "
              "keep when it matters.")
    elif e25["mean_edge_post"] < e25["mean_edge_pre"] - 0.30:
        print(f"[CONCERN] Friction at 25bps removes "
              f"{e25['mean_edge_pre']-e25['mean_edge_post']:.2f}pp from "
              "the mean edge — turnover is high. Consider weekly "
              "rebalance rather than daily.")
    print(f"\nAt 50 bps: mean edge {e50['mean_edge_post']:+.2f}pp, "
          f"crash edge {e50['crash_edge_post']:+.2f}pp")

    out = ROOT / "data" / "_research" / "transaction_cost_stress.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
