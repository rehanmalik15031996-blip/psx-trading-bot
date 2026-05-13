"""Did pre-crash risk-off signals fire May 8 -> May 13?"""
import pandas as pd, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
df = pd.read_parquet(ROOT/"data/macro/overnight_global.parquet")
df["date"] = pd.to_datetime(df["date"])
recent = df[df["date"] >= "2026-05-08"].sort_values("date").reset_index(drop=True)

print("Pre-IMF / pre-crash overnight signals (May 8 -> May 13):")
print(f"  {'date':<12}{'VIX':>8}{'VIX_1d':>9}{'DXY':>8}{'DXY_1d':>9}{'US10Y':>8}{'EEM':>8}{'EEM_1d':>9}")
for _, r in recent.iterrows():
    vc = r["vix_close"]; vd = r["vix_ret_1d"]
    dx = r["dxy_close"]; dxd = r["dxy_ret_1d"]
    us = r["us10y_close"]
    em = r["eem_close"]; emd = r["eem_ret_1d"]
    print(f"  {str(r['date'].date()):<12}{vc:>8.2f}{vd*100:>+8.1f}%{dx:>8.2f}{dxd*100:>+8.1f}%"
          f"{us:>8.2f}{em:>8.2f}{emd*100:>+8.1f}%")

print("\n=== Brent / Gold ===")
for label, p in [("Brent", "data/macro/brent.parquet"),
                 ("Gold", "data/macro/gold.parquet")]:
    df2 = pd.read_parquet(ROOT/p)
    df2["date"] = pd.to_datetime(df2["date"])
    sub = df2[df2["date"] >= "2026-05-08"].sort_values("date").tail(8)
    print(f"\n{label}:")
    prev = None
    for _, r in sub.iterrows():
        c = float(r.get("close", 0))
        if prev:
            chg = (c/prev - 1) * 100
            print(f"  {r['date'].date()}  close={c:>10.2f}  ({chg:+.1f}%)")
        else:
            print(f"  {r['date'].date()}  close={c:>10.2f}")
        prev = c

print("\n=== Playbook cases on disk ===")
for f in sorted((ROOT/"data/playbook").glob("*.json")):
    print(f"\nFile: {f.name} ({f.stat().st_size}b)")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if isinstance(d, list):
            print(f"  {len(d)} entries")
            for c in d[:20]:
                if isinstance(c, dict):
                    cid = c.get("id") or c.get("case_id") or "?"
                    name = c.get("name") or c.get("label") or c.get("title") or "?"
                    print(f"   - {cid}: {name[:80]}")
        elif isinstance(d, dict):
            print(f"  dict keys: {list(d.keys())[:15]}")
            for k, v in list(d.items())[:10]:
                if isinstance(v, dict):
                    print(f"   - {k}: {(v.get('description') or v.get('label') or '')[:60]}")
    except Exception as e:
        print(f"  err {e}")

print("\n=== Playbook firing snapshot from yesterday's strategist ===")
import subprocess
out = subprocess.run(
    ["git", "show", "b741f6b:data/_strategist/2026-05-12.json"],
    capture_output=True, text=True, cwd=ROOT, encoding="utf-8")
mon = json.loads(out.stdout) if out.stdout else {}
bs = mon.get("briefing_summary") or {}
fired = bs.get("playbook_analogue_fired") or {}
ids = bs.get("playbook_analogue_ids") or []
print(f"  Playbook IDs that fired Monday: {ids or '(none)'}")
for cid, meta in (fired.items() if isinstance(fired, dict) else []):
    print(f"   - {cid}: score={meta.get('match_score')} conf={meta.get('confidence')}")
    print(f"     triggers: {meta.get('fired_triggers')}")
    print(f"     note: {meta.get('strategist_note','')[:100]}")
