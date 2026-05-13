"""Verify compute_predictor_bias produces the expected sector + symbol bias."""
import sys, json, pathlib
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from brain import strategist_overlays as ov

# Load Monday's briefing (where imf + brent fired)
ROOT = pathlib.Path(".")
b = json.loads((ROOT/"data/_strategist/_briefing_2026-05-12.json").read_text(encoding="utf-8"))
bias = ov.compute_predictor_bias(b)
print("Fired case IDs:", bias.get("fired_case_ids"))
print()
print("Sector bias (added to score in [-1,+1]):")
for sec, v in bias.get("sector_bias", {}).items():
    print(f"  {sec:<22} {v:+.3f}")
print()
print("Symbol bias:")
for sym, v in (bias.get("symbol_bias") or {}).items():
    print(f"  {sym:<10} {v:+.3f}")
