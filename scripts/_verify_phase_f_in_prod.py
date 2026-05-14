"""Verify Phase F cases fired in production strategist run."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

s = json.loads((ROOT/"data/_strategist/latest.json").read_text(encoding="utf-8"))
print(f"as_of:       {s.get('as_of')}")
print(f"model:       {s.get('model')}")
print(f"fallback:    {s.get('fallback_used')}")
print(f"risk_stance: {s.get('risk_stance')}")
print(f"conviction:  {s.get('conviction')}")
print()

log = s.get("playbook_overlay_log") or []
ids = sorted({e.get("case_id") for e in log if isinstance(e, dict) and e.get("case_id")})
print(f"Playbook cases that drove overlays: {len(ids)}")
for cid in ids:
    print(f"  - {cid}")

phase_f = {"distribution_day_signature", "event_eve_distribution",
            "brent_plateau_e_and_p_decay"}
fired = phase_f & set(ids)
print(f"\nPhase F cases live: {len(fired)} / 3")
for c in sorted(fired):
    print(f"  [LIVE] {c}")
for c in sorted(phase_f - fired):
    print(f"  [silent] {c}")

# Check the Phase F intraday facts populated
notes = s.get("playbook_overlay_notes") or ""
print(f"\nOverlay narrative excerpt:")
print(notes[:600] if notes else "  (none)")

# Per-symbol bucket changes for user's portfolio
syms = {"PABC","MLCF","HUBC","FATIMA","HBL","POL","OGDC","MEBL","MCB","UBL","DGKC","LUCK","PPL"}
actions = s.get("actions") or []
print(f"\nUser-relevant decisions in fresh run:")
for a in actions:
    sym = a.get("symbol")
    if sym in syms:
        print(f"  {sym:<7} {(a.get('sector') or '?')[:18]:<18} "
              f"bucket={a.get('bucket'):<6} conv={a.get('conviction'):<8} "
              f"reason={(a.get('reason') or '')[:60]}")
