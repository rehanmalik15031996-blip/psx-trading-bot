"""Gap-6: audit per-case sample sizes and statistical reliability.

For each case in backtest_per_case.json:
  - Show n_fires (sample size in 258 backtest dates)
  - Show edge_vs_drift_5d and hit_rate_5d
  - Flag any case with n_fires < 10 as LOW_CONFIDENCE
  - For cases with n_fires < 5, recommend manual review

Also cross-reference against cases.json to find any case that has
ZERO fires in the 5y backtest (silent — either too tight or based
on a trigger the replay can't reconstruct).

Compute simple confidence interval on hit_rate:
  hit_rate ± 1.96 * sqrt(p*(1-p)/n)
to show how wide the uncertainty is at small n.
"""
from __future__ import annotations

import json
import os
import sys
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

PER_CASE = ROOT / "data" / "_research" / "backtest_per_case.json"
CASES = ROOT / "data" / "playbook" / "cases.json"


def _ci_hit_rate(hit: float, n: int) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    se = math.sqrt(hit * (1 - hit) / n)
    return max(0.0, hit - 1.96 * se), min(1.0, hit + 1.96 * se)


def main() -> int:
    per_case = json.loads(PER_CASE.read_text(encoding="utf-8"))
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases.get("cases") or cases.get("items") or []

    all_case_ids = {c.get("id") for c in cases if isinstance(c, dict)}
    audited_case_ids = {r.get("case_id") for r in per_case}
    missing = all_case_ids - audited_case_ids

    print(f"Total cases in cases.json:           {len(all_case_ids)}")
    print(f"Cases that fired in 5y backtest:     "
          f"{len(audited_case_ids)}")
    print(f"Cases that NEVER fired (silent):     {len(missing)}")
    if missing:
        print("Silent cases (never fired in 5y):")
        for c in sorted(missing):
            print(f"  - {c}")

    print()
    print("=" * 96)
    print(f"{'case_id':<40} {'n':>4} {'edge5d':>9} {'hit5d':>7} "
          f"{'95%CI low':>10} {'95%CI hi':>9} {'verdict':<12}")
    print("=" * 96)

    flagged_low_conf  = []
    flagged_no_edge   = []
    flagged_wrong_dir = []
    for r in sorted(per_case, key=lambda x: x.get("n_fires", 0), reverse=True):
        n      = r.get("n_fires", 0)
        edge   = r.get("edge_vs_drift_5d_pct", 0)
        hit5   = r.get("hit_rate_5d", 0)
        exp    = r.get("expected_direction", "?")
        lo, hi = _ci_hit_rate(hit5, n)
        if n < 5:
            verdict = "TINY"
            flagged_low_conf.append(r)
        elif n < 10:
            verdict = "SMALL"
            flagged_low_conf.append(r)
        elif edge < 0 and exp == "UP":
            verdict = "WRONG"
            flagged_wrong_dir.append(r)
        elif edge > 0 and exp == "DOWN":
            verdict = "WRONG"
            flagged_wrong_dir.append(r)
        elif abs(edge) < 0.1:
            verdict = "FLAT"
            flagged_no_edge.append(r)
        elif hit5 < 0.45 and abs(edge) < 0.5:
            verdict = "WEAK"
            flagged_no_edge.append(r)
        else:
            verdict = "OK"
        cid = r.get("case_id", "")[:38]
        print(f"{cid:<40} {n:>4} {edge:>+8.2f}pp {hit5*100:>6.1f}% "
              f"{lo*100:>9.1f}% {hi*100:>8.1f}% {verdict:<12}")

    print()
    print("=" * 96)
    print("SUMMARY")
    print("=" * 96)
    print(f"Silent (never fired in 5y):       {len(missing)} cases")
    print(f"Low confidence (n_fires < 10):    {len(flagged_low_conf)} cases")
    print(f"Wrong direction:                  {len(flagged_wrong_dir)} cases")
    print(f"No edge / weak edge:              {len(flagged_no_edge)} cases")

    print()
    print("Recommended actions:")
    if flagged_wrong_dir:
        print("\n  CRITICAL — cases predicting the WRONG direction:")
        for r in flagged_wrong_dir:
            print(f"    - {r['case_id']:<40s} exp={r['expected_direction']} "
                  f"edge={r['edge_vs_drift_5d_pct']:+.2f}pp")
        print("    → Either flip expected_direction OR remove case "
              "(direction inverted may be curve-fit).")
    if flagged_no_edge:
        print("\n  No-edge cases (consider removing or recalibrating):")
        for r in flagged_no_edge:
            print(f"    - {r['case_id']:<40s} edge="
                  f"{r['edge_vs_drift_5d_pct']:+.2f}pp hit="
                  f"{r['hit_rate_5d']*100:.0f}% n={r['n_fires']}")
    if flagged_low_conf:
        print("\n  Low-confidence cases (sample too small to trust):")
        for r in flagged_low_conf:
            print(f"    - {r['case_id']:<40s} n={r['n_fires']:<3} "
                  f"edge={r['edge_vs_drift_5d_pct']:+.2f}pp")
        print("    → Mark these as LOW_CONFIDENCE in cases.json. Reactions "
              "should be milder.")
    if missing:
        print("\n  Silent cases (never fired in 5y backtest):")
        for c in sorted(missing):
            print(f"    - {c}")
        print("    → Either triggers can't be reconstructed in replay "
              "(e.g. Phase F intraday cases) OR the trigger is too tight.")

    out = ROOT / "data" / "_research" / "case_sample_audit.json"
    out.write_text(json.dumps({
        "silent": sorted(missing),
        "low_confidence": [r["case_id"] for r in flagged_low_conf],
        "wrong_direction": [r["case_id"] for r in flagged_wrong_dir],
        "no_edge": [r["case_id"] for r in flagged_no_edge],
    }, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
