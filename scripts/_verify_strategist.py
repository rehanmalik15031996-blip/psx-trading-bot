"""Quick verifier for the strategist JSON."""
import json, pathlib

d = json.loads(pathlib.Path("data/_strategist/latest.json").read_text(encoding="utf-8"))
print("as_of   :", d.get("as_of"))
print("model   :", d.get("model"))
print("stance  :", d.get("risk_stance"), "/", d.get("conviction"))
print("fallback:", d.get("fallback_used"))
print()
print("headline:", d.get("headline"))
print()
print("=== verdict_distribution ===")
print(d.get("briefing_summary", {}).get("verdict_distribution"))
print()
print("=== value_distribution ===")
print(d.get("briefing_summary", {}).get("value_distribution"))
print()
print("=== mf_universe ===")
print(d.get("briefing_summary", {}).get("mf_universe"))
print()
print("=== playbook_fired ===")
print(json.dumps(d.get("briefing_summary", {}).get("playbook_analogue_fired"), indent=2, default=str)[:600])
print()
print("=== BUY/ADD actions ===")
for a in d.get("actions", []):
    bucket = a.get("bucket") or ""
    if bucket in ("BUY", "ADD"):
        sym = a.get("symbol") or "?"
        wt = a.get("target_weight_pct")
        conv = a.get("conviction")
        print(f"  {sym:<8} {bucket:<4} wt={wt}  conv={conv}")
print()
print("=== AVOID actions ===")
for a in d.get("actions", []):
    bucket = a.get("bucket") or ""
    if bucket == "AVOID":
        sym = a.get("symbol") or "?"
        print(f"  {sym:<8} AVOID  conv={a.get('conviction')}")
