"""Validation gate for cases.json changes.

Purpose: any modification of a case (new trigger, new reaction, new
weight) should be re-validated against a HELD-OUT window before
shipping. Today the playbook is hand-edited and we have no automated
check that the change preserves edge — exactly the iteration-bias
risk our audit (gap g6/g8) flagged.

Usage:

  # Validate the WHOLE file against current backtest:
  python scripts/_validate_case_edit.py

  # Validate one specific case_id:
  python scripts/_validate_case_edit.py --case-id distribution_day_signature

  # Or, gate a candidate file BEFORE merging:
  python scripts/_validate_case_edit.py --candidate path/to/new_cases.json

Mechanism:
  1. Load the current `cases.json` (or the candidate file) and the
     reference `backtest_per_case.json`.
  2. For each case in candidate that ALSO exists in backtest_per_case
     (i.e. that has a measurable historical edge), check:
       - n_fires >= MIN_FIRES (default 5)
       - hit_rate_5d direction agrees with expected_direction
       - edge_vs_drift_5d sign agrees with expected_direction
  3. For NEW cases that don't appear in the backtest yet, emit a
     warning but don't block — they need a backtest re-run to
     validate.
  4. Print a pass/fail summary. Exits 0 if all pass, 1 if any fail.

CI hookup: add a step to the pipeline that runs this on every PR
that touches `data/playbook/cases.json`. Block merge on failure.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

DEFAULT_CASES   = ROOT / "data" / "playbook" / "cases.json"
PER_CASE        = ROOT / "data" / "_research" / "backtest_per_case.json"

MIN_FIRES               = 5
MIN_HIT_RATE_UP_CASES   = 0.45    # for UP cases, hit_rate_5d must be >= this
MIN_HIT_RATE_DOWN_CASES = 0.45    # for DOWN cases (here: 1 - hit_rate_5d)


def _load_cases(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return raw.get("cases") or raw.get("items") or []
    return raw if isinstance(raw, list) else []


def _load_backtest_index() -> dict[str, dict]:
    if not PER_CASE.exists():
        return {}
    raw = json.loads(PER_CASE.read_text(encoding="utf-8"))
    return {r["case_id"]: r for r in raw if "case_id" in r}


def _validate_case(case: dict, bt: dict) -> tuple[bool, list[str]]:
    """Return (passed, reasons). bt may be empty if no backtest data."""
    cid = case.get("id", "?")
    # expected_direction is curated in scripts/_research_analyze.py
    # (CASE_EXPECTED_DIRECTION) and propagated into backtest_per_case.
    # Cases.json itself doesn't carry it. So fall back to bt's value.
    expected = (
        case.get("expected_direction")
        or bt.get("expected_direction")
        or ""
    ).upper()
    reactions = case.get("reactions") or {}
    has_reactions = bool(reactions)
    tags = [t.upper() for t in (case.get("tags") or [])
             if isinstance(t, str)]
    is_defensive_only = "DEFENSIVE_NOT_DIRECTIONAL" in tags
    is_low_confidence = (
        "LOW_CONFIDENCE" in tags
        or (case.get("confidence") or "").upper() == "LOW"
    )

    issues: list[str] = []
    # Defensive-only cases skip the directional sanity check; their job
    # is to mitigate drawdown that's already happening, not to predict
    # next-period direction.
    if is_defensive_only:
        if not has_reactions:
            issues.append(
                "  DEFENSIVE_NOT_DIRECTIONAL case has no reactions — "
                "expected to set cash_floor / position_size_multiplier.")
        elif "cash_floor_pct" not in reactions \
                and "position_size_multiplier" not in reactions:
            issues.append(
                "  DEFENSIVE_NOT_DIRECTIONAL case should specify "
                "cash_floor_pct or position_size_multiplier.")
        return True, issues

    if not bt:
        if has_reactions:
            issues.append(
                f"  NEW case (no backtest data yet). It has reactions "
                f"({list(reactions.keys())}) — recommend running "
                "_research_backtest.py first to confirm n_fires >= "
                f"{MIN_FIRES}.")
        return True, issues   # warning only

    n_fires = bt.get("n_fires", 0)
    edge5   = bt.get("edge_vs_drift_5d_pct", 0)
    hit5    = bt.get("hit_rate_5d", 0)

    failed = False

    if n_fires < MIN_FIRES:
        issues.append(
            f"  n_fires = {n_fires} < {MIN_FIRES}. SAMPLE TOO SMALL — "
            "case should be marked LOW_CONFIDENCE in cases.json with "
            "milder reactions, or removed.")
        # Treat as warning, not a hard fail, for backward compat.
        # CI integrator can flip to fail if they want strictness.

    # Direction sanity check. LOW_CONFIDENCE cases get warnings,
    # not hard fails — the user has explicitly de-rated them and
    # weakened the reactions.
    fail_severity = "WARN" if is_low_confidence else "CRITICAL"
    def _mark_failed():
        if not is_low_confidence:
            return True
        return False

    if expected == "UP":
        if edge5 < 0:
            issues.append(
                f"  {fail_severity}: expected_direction=UP but "
                f"edge_vs_drift_5d={edge5:+.2f}pp is NEGATIVE. The case "
                "predicts the wrong direction or the trigger is mis-"
                "specified." +
                (" (LOW_CONFIDENCE — warning only.)"
                 if is_low_confidence else ""))
            if _mark_failed():
                failed = True
        if hit5 < MIN_HIT_RATE_UP_CASES:
            issues.append(
                f"  hit_rate_5d = {hit5*100:.0f}% < "
                f"{MIN_HIT_RATE_UP_CASES*100:.0f}%. Weak direction.")
    elif expected == "DOWN":
        # For DOWN cases, hit_rate_5d should be LOW (we want the index
        # to fall) — i.e. 1-hit_rate should be >= threshold.
        if edge5 > 0:
            issues.append(
                f"  {fail_severity}: expected_direction=DOWN but "
                f"edge_vs_drift_5d={edge5:+.2f}pp is POSITIVE. The case "
                "predicts the wrong direction or the trigger is mis-"
                "specified." +
                (" (LOW_CONFIDENCE — warning only.)"
                 if is_low_confidence else ""))
            if _mark_failed():
                failed = True
        if (1 - hit5) < MIN_HIT_RATE_DOWN_CASES:
            issues.append(
                f"  fall_rate_5d = {(1-hit5)*100:.0f}% < "
                f"{MIN_HIT_RATE_DOWN_CASES*100:.0f}%. Weak direction.")
    else:
        # Don't validate cases without expected_direction (older curated)
        pass

    return (not failed), issues


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--candidate", type=Path, default=None,
                    help="Path to candidate cases.json. Defaults to "
                          "the current data/playbook/cases.json.")
    p.add_argument("--case-id", default=None,
                    help="Validate only this case id (still requires "
                          "the full file as input).")
    p.add_argument("--strict-sample", action="store_true",
                    help="Treat MIN_FIRES violations as hard fails.")
    args = p.parse_args()

    cases_path = args.candidate or DEFAULT_CASES
    cases = _load_cases(cases_path)
    if args.case_id:
        cases = [c for c in cases if c.get("id") == args.case_id]
        if not cases:
            print(f"Case id {args.case_id!r} not found in {cases_path}")
            return 1

    bt_index = _load_backtest_index()
    print(f"Validating {len(cases)} case(s) from {cases_path}")
    print(f"Backtest reference: {PER_CASE}  "
          f"({len(bt_index)} cases with measured data)")
    print()

    n_pass = 0
    n_fail = 0
    n_warn = 0
    for c in cases:
        cid = c.get("id", "?")
        bt  = bt_index.get(cid, {})
        passed, issues = _validate_case(c, bt)
        if args.strict_sample and any("n_fires" in i for i in issues):
            passed = False
        if not passed:
            n_fail += 1
            print(f"FAIL {cid}")
            for i in issues:
                print(i)
            print()
        elif issues:
            n_warn += 1
            print(f"WARN {cid}")
            for i in issues:
                print(i)
            print()
        else:
            n_pass += 1
            print(f"PASS {cid}  "
                  f"(n_fires={bt.get('n_fires','?')}, "
                  f"edge5d={bt.get('edge_vs_drift_5d_pct','?')}, "
                  f"hit5d={bt.get('hit_rate_5d','?')})")

    print()
    print("=" * 80)
    print(f"SUMMARY: {n_pass} PASS, {n_warn} WARN, {n_fail} FAIL")
    print("=" * 80)
    if n_fail:
        print("Validation gate FAILED — fix the cases above before merging.")
        return 1
    if n_warn:
        print("Validation gate passed with WARNINGS — review them.")
    else:
        print("Validation gate PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
