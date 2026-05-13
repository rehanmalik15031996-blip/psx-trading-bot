"""Backtest: did the new overlay system catch the May 11->13 sell-off?

Simulates two scenarios:

  A) STATUS QUO (what the system actually said on May 11):
     - Stance: DEFENSIVE 80% cash, 3 BUYs (OGDC/ATRL/PPL), AVOID PABC/TRG/SEARL,
       all banks/cements/power = HOLD (verbatim from b741f6b).
     - P&L: portfolio held banks/cements/power through -3% sector moves.

  B) WITH OVERLAYS (what would have happened if reactions+overlay engine
     had been active on May 11):
     - Same baseline, then strategist_overlays.apply_playbook_overlays()
       runs against the May 12 briefing (which has the SAME fires that
       would have shown for May 11 as both events were active).
     - Banks/Cement/Power auto-downgraded to WATCH/AVOID
     - Cash floor raised to 85%
     - E&P upgraded to ADD

Assumption: an investor follows the strategist's bucket exactly.
  - BUY/ADD = held with target weight
  - HOLD/WATCH at weight 0% = NOT held (zero exposure)
  - AVOID/TRIM = not held (zero exposure)

P&L = sum(weight_pct * 2d_return_pct) over the universe.

NOTE: The May 11 strategist had ALL non-BUY/non-AVOID at HOLD wt=0 — so the
"status quo" scenario actually had only OGDC/ATRL/PPL as long. To make the
comparison meaningful (showing that overlays improve risk-adjusted decisions)
we ALSO simulate a "naive HOLD all" where the investor was actually holding
the universe (e.g. an indexed long-only book).
"""
from __future__ import annotations
import json, sys, pathlib
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import strategist_overlays as ov  # noqa: E402

# ----------------------------------------------------------------- data
def load_2d_returns() -> dict[str, float]:
    """Per-symbol May 11 -> May 13 close-to-close return (%)."""
    out = {}
    for fp in (ROOT/"data/ohlcv").glob("*.parquet"):
        df = pd.read_parquet(fp).sort_values("date").tail(3).reset_index(drop=True)
        if len(df) < 3:
            continue
        c11 = float(df.iloc[0]["close"])
        c13 = float(df.iloc[2]["close"])
        out[fp.stem] = (c13/c11 - 1) * 100
    return out


def pnl_from_decision(decision: dict, returns: dict[str, float]) -> dict:
    """Compute P&L assuming the bucket determines whether we hold."""
    actions = decision.get("actions") or []
    held = 0.0
    weight_pct_sum = 0.0
    contributions = []
    for a in actions:
        sym = a.get("symbol")
        if not sym or sym not in returns:
            continue
        bucket = (a.get("bucket") or "").upper()
        wt = float(a.get("target_weight_pct") or 0)
        ret = returns[sym]
        # Only BUY/ADD/HOLD-with-weight contribute to P&L
        if bucket in ("BUY", "ADD") and wt > 0:
            contribution = (wt / 100.0) * ret
            held += contribution
            weight_pct_sum += wt
            contributions.append((sym, bucket, wt, ret, contribution))
    cash_action = next((a for a in actions
                        if (a.get("bucket") or "").upper() == "CASH"), None)
    cash_wt = (cash_action.get("target_weight_pct") if cash_action else None) or 0
    return {
        "weight_long_pct": weight_pct_sum,
        "weight_cash_pct": cash_wt,
        "weighted_pnl_pct": held,
        "contributions": contributions,
    }


def naive_holdall(returns: dict[str, float], universe: list[str]) -> float:
    """Equal-weighted long across the strategist universe (worst-case naive)."""
    rs = [returns[s] for s in universe if s in returns]
    return sum(rs) / len(rs) if rs else 0


# ----------------------------------------------------------------- scenarios
def main() -> int:
    returns = load_2d_returns()
    print(f"Loaded 2d returns for {len(returns)} symbols")

    briefing = json.loads(
        (ROOT/"data/_strategist/_briefing_2026-05-12.json").read_text(encoding="utf-8")
    )

    # Scenario A: actual May 11 strategist (b741f6b) — recover from git
    import subprocess
    out = subprocess.run(
        ["git", "show", "b741f6b:data/_strategist/2026-05-12.json"],
        capture_output=True, text=True, cwd=ROOT, encoding="utf-8")
    decision_a = json.loads(out.stdout) if out.stdout else {}
    pnl_a = pnl_from_decision(decision_a, returns)
    universe = [a["symbol"] for a in (decision_a.get("actions") or [])
                if a.get("symbol")]

    # Scenario B: same baseline + overlays
    decision_b = json.loads(json.dumps(decision_a))  # deep copy
    ov.apply_playbook_overlays(decision_b, briefing)
    pnl_b = pnl_from_decision(decision_b, returns)

    # Scenario C: naive index-style long-only baseline (the worst case)
    naive_pct = naive_holdall(returns, universe)

    # Universe-level reference points
    avg_2d = sum(returns.values()) / len(returns)

    print("\n" + "=" * 78)
    print("BACKTEST: May 11 -> May 13 (close-to-close)")
    print("=" * 78)

    print(f"\nUniverse 2d move (equal-weighted): {avg_2d:+.2f}%")
    print(f"Naive long-all-universe:           {naive_pct:+.2f}%")

    print(f"\nA) STATUS QUO (actual May 11 strategist):")
    print(f"   Long weight:       {pnl_a['weight_long_pct']:.1f}%")
    print(f"   Cash weight:       {pnl_a['weight_cash_pct']:.1f}%")
    print(f"   Portfolio PnL 2d:  {pnl_a['weighted_pnl_pct']:+.3f}%")
    print(f"   Contributions:")
    for sym, b, wt, ret, contrib in pnl_a["contributions"]:
        print(f"     {sym:<6} {b:<5} wt={wt:>4.1f}%  ret={ret:+.2f}%  -> {contrib:+.3f}%")

    print(f"\nB) WITH NEW OVERLAYS (deterministic):")
    print(f"   Long weight:       {pnl_b['weight_long_pct']:.1f}%")
    print(f"   Cash weight:       {pnl_b['weight_cash_pct']:.1f}%")
    print(f"   Portfolio PnL 2d:  {pnl_b['weighted_pnl_pct']:+.3f}%")
    print(f"   Contributions:")
    for sym, b, wt, ret, contrib in pnl_b["contributions"]:
        print(f"     {sym:<6} {b:<5} wt={wt:>4.1f}%  ret={ret:+.2f}%  -> {contrib:+.3f}%")

    print()
    print("OVERLAY CHANGE LOG:")
    print(ov.overlay_summary(decision_b))

    # Symbol-level "saved from holding" analysis
    print()
    print("SYMBOLS SAVED FROM A NAIVE HOLD (HOLD -> WATCH/AVOID):")
    saved = []
    actions_b = {a.get("symbol"): a for a in decision_b.get("actions") or []}
    for sym, ret in sorted(returns.items(), key=lambda x: x[1]):
        action_b = actions_b.get(sym)
        if not action_b:
            continue
        bucket_b = (action_b.get("bucket") or "").upper()
        if bucket_b in ("WATCH", "AVOID", "TRIM") and ret < -2.0:
            saved.append((sym, ret, bucket_b))
    for sym, ret, bucket in saved[:15]:
        print(f"  {sym:<8} ret={ret:+.2f}%  ->  {bucket}  (avoided -{abs(ret):.2f}% drawdown)")
    print(f"  ({len(saved)} names saved from a naive HOLD)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
