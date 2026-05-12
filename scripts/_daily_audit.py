"""End-of-run audit — verifies every layer of the daily pipeline is fresh."""
import json, pathlib
from datetime import datetime, date
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent

print("=" * 78)
print(f"DAILY PIPELINE AUDIT — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 78)

# ----------------------------------------------------------------- 1. Health JSONs
print("\n[1] WORKFLOW HEALTH BADGES")
print("-" * 78)
health_dir = ROOT / "data" / "_health"
for f in sorted(health_dir.glob("*.json")):
    if f.name.startswith("_"):
        continue
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        ok = d.get("ok") or d.get("status") == "ok"
        badge = d.get("badge") or ("GREEN" if ok else "RED/YELLOW")
        last = d.get("last_success_ts") or d.get("last_run_ts") or d.get("as_of") or "?"
        note = (d.get("note") or d.get("summary") or "")[:70]
        print(f"  {f.stem:<22} {badge:<8} last={str(last)[:19]:<19} {note}")
    except Exception as e:
        print(f"  {f.stem:<22} ERROR {e}")

# ----------------------------------------------------------------- 2. Macro / Overnight
print("\n[2] MACRO / OVERNIGHT FRESHNESS")
print("-" * 78)
checks = {
    "Brent crude":     ("data/macro/brent.parquet",         "date"),
    "Gold":            ("data/macro/gold.parquet",          "date"),
    "USD/PKR":         ("data/macro/usdpkr.parquet",        "date"),
    "KSE-100":         ("data/macro/kse100.parquet",        "date"),
    "SBP rates":       ("data/macro/sbp_rates.parquet",     "as_of"),
    "CPI Pakistan":    ("data/macro/cpi_pakistan.parquet",  "month"),
    "Overnight global":("data/macro/overnight_global.parquet", "date"),
    "FIPI daily":      ("data/flows/fipi_daily.parquet",    "date"),
}
for label, (path, col) in checks.items():
    fp = ROOT / path
    if not fp.exists():
        print(f"  {label:<22} MISSING")
        continue
    df = pd.read_parquet(fp)
    if col not in df.columns:
        col = df.columns[0]
    last = df[col].max() if col in df.columns else "?"
    n = len(df)
    print(f"  {label:<22} last={str(last)[:10]:<10}  rows={n:>5}")

# ----------------------------------------------------------------- 3. OHLCV
print("\n[3] OHLCV UNIVERSE")
print("-" * 78)
ohlcv = ROOT / "data" / "ohlcv"
syms = sorted([p.stem for p in ohlcv.glob("*.parquet")])
print(f"  {len(syms)} symbols, files: {', '.join(syms[:8])}, ...")
# Sample 3 to show freshness
for sym in ("OGDC", "POL", "PPL"):
    fp = ohlcv / f"{sym}.parquet"
    df = pd.read_parquet(fp).sort_values("date")
    last = df.iloc[-1]
    print(f"  {sym}  last bar = {last['date'].date()}  close = {last['close']}")

# ----------------------------------------------------------------- 4. Predictions
print("\n[4] PREDICTIONS LOG")
print("-" * 78)
log = json.loads((ROOT / "data/predictions_log.json").read_text(encoding="utf-8"))
preds = log.get("predictions", [])
print(f"  Total rows: {len(preds)}")
today_iso = date.today().isoformat()
today_rows = [r for r in preds
              if isinstance(r, dict)
              and r.get("prediction_id", "").startswith(today_iso)]
print(f"  Today ({today_iso}) rows: {len(today_rows)}")
# Sanity: count by suggested_action
from collections import Counter
acts = Counter((r.get("suggested_action") or "?") for r in today_rows)
dirs = Counter((r.get("direction") or "?") for r in today_rows)
print(f"  Action distribution: {dict(acts)}")
print(f"  Direction distribution: {dict(dirs)}")
# Validate prices populated for today's batch
broken = [r["symbol"] for r in today_rows
          if r.get("entry_price_pkr") in (None, 0, 0.0)
          or r.get("suggested_stop_pkr") is None
          or r.get("suggested_target_pkr") is None]
if broken:
    print(f"  !! BROKEN PRICES: {broken}")
else:
    print(f"  OK All {len(today_rows)} rows have entry/stop/target populated")

# ----------------------------------------------------------------- 5. Master Strategist
print("\n[5] MASTER STRATEGIST")
print("-" * 78)
strat = json.loads(
    (ROOT / "data/_strategist/latest.json").read_text(encoding="utf-8"))
print(f"  as_of        : {strat.get('as_of')}")
print(f"  model        : {strat.get('model')}")
print(f"  fallback_used: {strat.get('fallback_used')}")
print(f"  stance       : {strat.get('risk_stance')} / {strat.get('conviction')}")
print(f"  headline     : {(strat.get('headline') or '')[:120]}")
acts = strat.get("actions") or []
print(f"  actions      : {len(acts)} total")
buys = [a for a in acts if (a.get("bucket") or "") in ("BUY", "ADD")]
print(f"  BUY/ADD      : {len(buys)} -> "
      f"{[(a['symbol'], a['bucket'], a['target_weight_pct']) for a in buys]}")
avoids = [a for a in acts if (a.get("bucket") or "") == "AVOID"]
print(f"  AVOID        : {len(avoids)} -> {[a['symbol'] for a in avoids]}")

bs = strat.get("briefing_summary") or {}
print(f"  verdict_dist : {bs.get('verdict_distribution')}")
print(f"  value_dist   : {bs.get('value_distribution')}")
print(f"  mf_universe  : {bs.get('mf_universe')}")

# ----------------------------------------------------------------- 6. Git state
import subprocess
print("\n[6] GIT STATE")
print("-" * 78)
out = subprocess.run(["git", "status", "--short"], capture_output=True,
                     text=True, cwd=ROOT)
if out.stdout.strip():
    print(f"  !! UNCOMMITTED CHANGES:\n{out.stdout}")
else:
    print(f"  OK Working tree clean")
out = subprocess.run(["git", "log", "-3", "--oneline"], capture_output=True,
                     text=True, cwd=ROOT)
print(f"  Last 3 commits:")
for line in out.stdout.strip().split("\n"):
    print(f"    {line}")
out = subprocess.run(["git", "rev-list", "--count", "HEAD..origin/main"],
                     capture_output=True, text=True, cwd=ROOT)
behind = out.stdout.strip()
if behind and int(behind) > 0:
    print(f"  !! {behind} commits behind origin")
else:
    print(f"  OK HEAD == origin/main")

print("\n" + "=" * 78)
print("AUDIT COMPLETE")
print("=" * 78)
