"""Inspect Brent schema + full playbook + active events."""
import pandas as pd, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

print("=== Brent parquet schema ===")
df = pd.read_parquet(ROOT/"data/macro/brent.parquet")
print("columns:", list(df.columns))
print("last 5 rows:")
print(df.sort_values(df.columns[0]).tail(5).to_string())

print("\n=== cases.json full case list ===")
data = json.loads((ROOT/"data/playbook/cases.json").read_text(encoding="utf-8"))
cases = data.get("cases", [])
print(f"  n_cases: {len(cases)}")
for c in cases:
    if isinstance(c, dict):
        cid = c.get("id") or "?"
        name = c.get("name") or c.get("label") or "?"
        triggers = c.get("triggers") or c.get("match_when") or []
        n_trig = len(triggers) if isinstance(triggers, list) else "?"
        print(f"  - {cid}: {str(name)[:75]}   ({n_trig} triggers)")

print("\n=== events.json active events ===")
data = json.loads((ROOT/"data/playbook/_events.json").read_text(encoding="utf-8"))
events = data.get("events", [])
print(f"  n_events: {len(events)}")
for e in events:
    if isinstance(e, dict):
        eid = e.get("id", "?")
        label = e.get("label", "?")
        start = e.get("start_date", "?")
        end = e.get("end_date", "?")
        print(f"  - {eid} | {str(label)[:60]} | start={start} end={end}")
