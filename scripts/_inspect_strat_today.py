"""Inspect today's strategist run to extract event context + overlay log."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

s = json.loads((ROOT/"data/_strategist/latest.json").read_text(encoding="utf-8"))
print("Strategist top-level keys:", list(s.keys()))
print()
print("as_of:        ", s.get("as_of"))
print("regime:       ", s.get("regime"))
print("model:        ", s.get("model") or s.get("generated_by"))
print()

# Overlay log
log = (s.get("debug") or {}).get("overlay_log") or s.get("playbook_overlay_log") or []
print(f"overlay_log entries: {len(log)}")
fired_via = set()
for e in log:
    if isinstance(e, dict):
        cid = e.get("case_id")
        if cid:
            fired_via.add(cid)
        else:
            via = e.get("via", "")
            if isinstance(via, str) and "playbook:" in via:
                fired_via.add(via.split(":")[1])
print(f"  Cases that drove overlays: {sorted(fired_via)}")

# Briefing playbook facts
briefing = s.get("briefing") or {}
pf = briefing.get("playbook_facts") or briefing.get("fired") or []
print(f"\n  briefing playbook_facts: {len(pf)}")
for f in pf[:12]:
    if isinstance(f, dict):
        print(f"    - {f.get('id') or f.get('case_id')}  score={f.get('score')}  triggers={f.get('fired_triggers')}")
    else:
        print(f"    - {f}")

# Narrative
narr = (s.get("narrative") or s.get("market_narrative") or "")
if narr:
    print("\nNarrative:\n")
    print(narr[:1200])

# Risks
risks = s.get("risks") or []
print(f"\nRisks ({len(risks)}):")
for r in risks[:10]:
    print(f"  - {r}")

# active events
ae = briefing.get("active_events") or s.get("active_events") or []
print(f"\nActive events ({len(ae)}):")
for e in ae[:10]:
    print(f"  - {e}")
