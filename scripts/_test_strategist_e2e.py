"""End-to-end test of master_strategist.decide_today with overlays wired."""
import sys
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from collections import Counter
from brain import master_strategist as ms

print("Calling decide_today (write_cache=False so we don't clobber today)...")
out = ms.decide_today(write_cache=False)

print()
print("=== RESULT ===")
print(f"  fallback_used: {out.get('fallback_used')}")
print(f"  headline:      {(out.get('headline') or '')[:100]}")
print(f"  stance:        {out.get('risk_stance')} / {out.get('conviction')}")
acts = out.get("actions") or []
print(f"  actions:       {len(acts)}")
buckets = Counter((a.get("bucket") or "?") for a in acts)
print(f"  bucket dist:   {dict(buckets)}")

print()
print("CASH action:")
cash = next((a for a in acts if a.get("bucket") == "CASH"), None)
print(f"  weight: {cash.get('target_weight_pct') if cash else None}")

print()
print("OVERLAY LOG:")
log = out.get("playbook_overlay_log") or []
print(f"  cases applied: {len(log)}")
for c in log:
    print(f"  - {c['case_id']} (score {c.get('match_score')}): "
          f"{len(c['changes'])} changes")

print()
print("OVERLAY NOTES:")
for n in (out.get("playbook_overlay_notes") or [])[:5]:
    print(f"  {n[:120]}")

print()
print("CHANGED ACTIONS (sample):")
for a in acts:
    sigs = a.get("contributing_signals") or []
    if any("playbook:" in s for s in sigs):
        print(f"  {a.get('symbol'):<8} sec={(a.get('sector') or '?')[:18]:<18} "
              f"bucket={a.get('bucket'):<6} wt={a.get('target_weight_pct')}")
