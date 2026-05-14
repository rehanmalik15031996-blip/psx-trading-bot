"""Strip null d21 values from historical instances in cases.json
(playbook validator requires numeric values)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

p = ROOT/"data/playbook/cases.json"
data = json.loads(p.read_text(encoding="utf-8"))
cases = data.get("cases", []) if isinstance(data, dict) else data

n_stripped = 0
for c in cases:
    if not isinstance(c, dict):
        continue
    for inst in (c.get("historical_instances") or []):
        rx = inst.get("reactions") or {}
        for sym, vals in list(rx.items()):
            if isinstance(vals, dict):
                for k in list(vals.keys()):
                    if vals[k] is None:
                        del vals[k]
                        n_stripped += 1

if isinstance(data, dict):
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
else:
    p.write_text(json.dumps(cases, indent=2), encoding="utf-8")
print(f"Stripped {n_stripped} null reaction values.")
