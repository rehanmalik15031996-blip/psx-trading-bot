"""Test the new strategist_overlays module against the May-12 briefing.

Reads the actual May 12 briefing (which has the playbook fires recorded),
constructs a synthetic LLM-style decision (all banks/cement/power = HOLD,
mirroring what we actually saw Mon), runs the overlay, and prints the
before/after. This proves that with reactions wired:

  - imf_review_mission_week    (score 2.6) -> auto-trims Banks/Cement/Power
  - brent_spike_e_and_p        (score 1.6) -> upgrades E&P (OGDC/PPL/MARI)
  - mf_universe_distribution_broad        -> raises cash floor
  - mf_initiation_cluster                 -> narrative note

Without firing any LLM call.
"""
from __future__ import annotations
import json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import strategist_overlays as ov  # noqa: E402


def main() -> int:
    briefing = json.loads(
        (ROOT/"data/_strategist/_briefing_2026-05-12.json").read_text(encoding="utf-8")
    )

    # Build a synthetic LLM-output decision that matches what we actually
    # saw Monday: 80% cash + everything HOLD, no per-stock conviction.
    # We use the verdict_universe from the briefing for the symbol/sector list.
    verdicts = briefing.get("verdict_universe") or {}
    universe: list[tuple[str, str]] = []
    if isinstance(verdicts, dict):
        meta = {"as_of", "n", "ttl_sec", "generated_at",
                "_others", "_compression_note"}
        for k, v in verdicts.items():
            if k in meta or not isinstance(v, dict): continue
            universe.append((k, v.get("sector", "?")))
        for o in verdicts.get("_others") or []:
            if isinstance(o, dict) and o.get("symbol"):
                universe.append((o["symbol"], o.get("sector", "?")))

    # Pull sector from universe_ranking too (more reliable)
    ur = briefing.get("universe_ranking") or {}
    sec_lookup = {}
    if isinstance(ur, dict):
        for row in ur.get("ranking") or []:
            if isinstance(row, dict) and row.get("symbol"):
                sec_lookup[row["symbol"]] = row.get("sector", "?")

    actions = [{
        "symbol": None, "sector": None, "bucket": "CASH",
        "conviction": "MEDIUM", "target_weight_pct": 80.0,
        "reason": "Phase-1 risk_off; binary IMF event.",
        "contributing_signals": [],
    }]
    for sym, sec in universe:
        actions.append({
            "symbol": sym,
            "sector": sec_lookup.get(sym) or sec or "?",
            "bucket": "HOLD",
            "conviction": "LOW",
            "target_weight_pct": 0.0,
            "reason": "Default HOLD.",
            "contributing_signals": [],
        })

    decision_before = {
        "as_of": briefing.get("as_of"),
        "model": "synthetic",
        "headline": "Synthetic test decision",
        "risk_stance": "DEFENSIVE",
        "conviction": "MEDIUM",
        "narrative": "All HOLDs to test the overlay.",
        "actions": actions,
        "fallback_used": False,
        "key_drivers": [],
        "key_risks": [],
        "macro_lens": "",
        "behavioural_lens": "",
        "briefing_summary": {},
    }

    # Snapshot before for comparison
    before_buckets = {
        a.get("symbol"): (a.get("bucket"), a.get("target_weight_pct"))
        for a in decision_before["actions"]
    }

    # Apply overlays
    decision_after = json.loads(json.dumps(decision_before))  # deep copy
    ov.apply_playbook_overlays(decision_after, briefing)

    after_buckets = {
        a.get("symbol"): (a.get("bucket"), a.get("target_weight_pct"))
        for a in decision_after["actions"]
    }

    print("=" * 78)
    print("OVERLAY TEST — May 12 briefing replay")
    print("=" * 78)
    print()
    print("FIRED CASES (sorted by score):")
    fired = ov._fired_cases(briefing)
    for c in fired:
        print(f"  {c['id']:<40}  score={c.get('match_score')}  "
              f"fired={c.get('fired_triggers')}")

    print()
    print("CHANGES APPLIED:")
    print(ov.overlay_summary(decision_after))

    print()
    print("BEFORE -> AFTER (only changed):")
    print(f"  {'symbol':<10}{'before':<22}{'after':<22}")
    n_changed = 0
    for sym in sorted(before_buckets, key=lambda x: x or ""):
        b = before_buckets[sym]; a = after_buckets[sym]
        if b != a:
            n_changed += 1
            sym_label = sym or "[CASH]"
            print(f"  {sym_label:<10}{f'{b[0]} (wt={b[1]})':<22}{f'{a[0]} (wt={a[1]})':<22}")
    print(f"  ({n_changed} actions changed out of {len(before_buckets)})")

    print()
    print("CASH FLOOR: ",
          next((a.get("target_weight_pct") for a in decision_after['actions']
                if a.get('bucket') == 'CASH'), None))

    print()
    print("PLAYBOOK OVERLAY NOTES:")
    for n in decision_after.get("playbook_overlay_notes", []):
        print(f"  {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
