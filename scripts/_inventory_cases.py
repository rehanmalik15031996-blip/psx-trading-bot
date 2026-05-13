"""Inventory all playbook cases — triggers, reactions, intent."""
import json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
data = json.loads((ROOT/"data/playbook/cases.json").read_text(encoding="utf-8"))
cases = data.get("cases", [])
print(f"{len(cases)} cases\n")
for c in cases:
    cid = c.get("id")
    trig = c.get("trigger_signals") or []
    mn = c.get("min_triggers")
    has_reactions = "reactions" in c
    cat = c.get("category", "?")
    title = (c.get("title") or "")[:80]
    pb = (c.get("playbook") or "")[:120]
    print(f"  {cid:<42} cat={cat:<14} trig={len(trig):>2} mn={str(mn):<5} react={has_reactions}")
    print(f"      title:    {title}")
    print(f"      playbook: {pb}...")
    print()
