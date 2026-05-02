"""Mirror data/macro/imf_events.json into data/playbook/_events.json so the
playbook matcher's `event:` triggers fire on the right keys.

The macro file is the authoritative source; the playbook file is the
projection used by the matcher. Keeping them in two files lets the macro
engine and the playbook evolve independently while a single edit (to the
macro file + this script) keeps them in sync.

Mapping rule:
  type='sba_approval' or 'eff_approval'           -> key='imf_sba_or_eff_approval'
  type='review_sla' or 'review_board_approval'    -> key='imf_review_completed'
  type='tranche_disbursed'                        -> key='imf_review_completed'
  type='program_lapsed'                           -> key='imf_program_lapsed'

Run after editing data/macro/imf_events.json:
    python scripts/sync_imf_events_to_playbook.py

Idempotent: re-running with no changes is a no-op.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACRO_PATH = ROOT / "data" / "macro" / "imf_events.json"
PLAYBOOK_PATH = ROOT / "data" / "playbook" / "_events.json"


TYPE_TO_KEY = {
    "sba_approval":          "imf_sba_or_eff_approval",
    "eff_approval":          "imf_sba_or_eff_approval",
    "review_sla":            "imf_review_completed",
    "review_board_approval": "imf_review_completed",
    "tranche_disbursed":     "imf_review_completed",
    "program_lapsed":        "imf_program_lapsed",
}


def main() -> None:
    if not MACRO_PATH.exists():
        print(f"NO-OP: {MACRO_PATH} does not exist.")
        return

    macro = json.loads(MACRO_PATH.read_text(encoding="utf-8"))
    macro_events = macro.get("events") or []

    existing = {}
    if PLAYBOOK_PATH.exists():
        existing = json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    schema = existing.get("_schema") or {}
    other_events = [e for e in (existing.get("events") or [])
                    if isinstance(e, dict)
                    and not str(e.get("key", "")).startswith("imf_")]

    imf_projected: list[dict] = []
    for src in macro_events:
        if not isinstance(src, dict):
            continue
        t = str(src.get("type") or "").strip().lower()
        key = TYPE_TO_KEY.get(t)
        if not key:
            continue
        imf_projected.append({
            "key": key,
            "date": src.get("date"),
            "decay_days": int(src.get("decay_days") or (60 if "approval" in t else 21)),
            "description": src.get("description") or "",
            "source": src.get("source") or "",
            "_origin": "data/macro/imf_events.json",
        })

    merged = sorted(other_events + imf_projected,
                    key=lambda e: (str(e.get("date") or ""), str(e.get("key") or "")))

    payload = {
        "_schema": schema or {
            "description": "Curated log of in-window external events the playbook matcher uses for its 'event:<key>' triggers.",
        },
        "events": merged,
    }
    PLAYBOOK_PATH.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    n_imf = len(imf_projected)
    n_other = len(other_events)
    print(f"OK: synced {n_imf} IMF events into {PLAYBOOK_PATH.name} "
          f"(plus {n_other} other events kept).")


if __name__ == "__main__":
    main()
