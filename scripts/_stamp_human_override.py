"""Add the human_override sentinel to existing human-written strategist
files (morning + midday May 18) and to today's call once we write it."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FILES = [
    "data/_strategist/2026-05-18.json",
    "data/_strategist/2026-05-18_midday.json",
    "data/_strategist/latest.json",
    "data/_strategist/latest_midday.json",
]

for f in FILES:
    p = Path(f)
    if not p.exists():
        print(f"  [SKIP] {f} not found")
        continue
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("human_override") is True:
        print(f"  [OK  ] {f} already flagged")
        continue
    data["human_override"] = True
    if not str(data.get("model") or "").lower().startswith("cursor"):
        data["model"] = "cursor-claude-sonnet-4-5-manual"
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"  [SET ] {f}  -> human_override=True, model=cursor-...")

print("\nNow the scheduled Master Strategist workflow will write its")
print("output to <date>_workflow_autorun.json instead of clobbering")
print("these files.")
