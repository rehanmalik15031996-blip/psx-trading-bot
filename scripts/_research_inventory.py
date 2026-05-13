"""Inventory all historical data the backtest can use."""
from __future__ import annotations
import json, sys, pathlib
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8")

# ---- 1. OHLCV history depth + breadth ----
print("=" * 78)
print("1) OHLCV (per-stock daily price history)")
print("=" * 78)
ohlcv_dir = ROOT / "data" / "ohlcv"
ohlcv_files = sorted(ohlcv_dir.glob("*.parquet"))
print(f"  files: {len(ohlcv_files)}")
if ohlcv_files:
    sample_min, sample_max, sample_rows = None, None, []
    for fp in ohlcv_files:
        df = pd.read_parquet(fp)
        if not len(df):
            continue
        d_min, d_max = df["date"].min(), df["date"].max()
        if sample_min is None or d_min < sample_min:
            sample_min = d_min
        if sample_max is None or d_max > sample_max:
            sample_max = d_max
        sample_rows.append((fp.stem, len(df), str(d_min)[:10], str(d_max)[:10]))
    sample_rows.sort()
    print(f"  earliest: {sample_min}  latest: {sample_max}")
    print(f"  per-stock samples (first 8 + last 8 alphabetically):")
    for r in sample_rows[:8]:
        print(f"    {r[0]:<8} rows={r[1]:>5}  {r[2]} -> {r[3]}")
    print("    ...")
    for r in sample_rows[-8:]:
        print(f"    {r[0]:<8} rows={r[1]:>5}  {r[2]} -> {r[3]}")

# ---- 2. Strategist runs (per-day cached decisions) ----
print()
print("=" * 78)
print("2) Strategist daily caches (data/_strategist/YYYY-MM-DD.json)")
print("=" * 78)
strat_dir = ROOT / "data" / "_strategist"
strat_files = sorted([
    fp for fp in strat_dir.glob("*.json")
    if fp.stem != "latest" and not fp.stem.startswith("_")
])
print(f"  daily files: {len(strat_files)}")
if strat_files:
    print(f"  earliest: {strat_files[0].stem}")
    print(f"  latest:   {strat_files[-1].stem}")
    # Sample fallback_used distribution
    n_fb = 0
    n_act_total = 0
    n_overlay = 0
    for fp in strat_files:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("fallback_used"):
            n_fb += 1
        n_act_total += len(d.get("actions") or [])
        if d.get("playbook_overlay_log"):
            n_overlay += 1
    print(f"  fallback_used count: {n_fb}/{len(strat_files)}")
    print(f"  with playbook_overlay_log: {n_overlay}/{len(strat_files)}")
    print(f"  total actions across all: {n_act_total}")

# Briefings (heavier)
brief_files = sorted(strat_dir.glob("_briefing_*.json"))
print(f"  briefing files: {len(brief_files)}")
if brief_files:
    print(f"  earliest briefing: {brief_files[0].stem}")
    print(f"  latest briefing:   {brief_files[-1].stem}")

# ---- 3. Predictions log ----
print()
print("=" * 78)
print("3) Predictions log (data/predictions_log.json)")
print("=" * 78)
preds_path = ROOT / "data" / "predictions_log.json"
if preds_path.exists():
    log = json.loads(preds_path.read_text(encoding="utf-8"))
    preds = log.get("predictions", [])
    print(f"  total predictions: {len(preds)}")
    if preds:
        from collections import Counter
        gen_dates = []
        for p in preds:
            ga = (p.get("generated_at") or "")[:10]
            if ga:
                gen_dates.append(ga)
        gen_dates_sorted = sorted(set(gen_dates))
        print(f"  unique generation dates: {len(gen_dates_sorted)}")
        if gen_dates_sorted:
            print(f"  earliest: {gen_dates_sorted[0]}")
            print(f"  latest:   {gen_dates_sorted[-1]}")
        models = Counter(p.get("model") or "?" for p in preds)
        print(f"  models: {dict(models)}")
        actions = Counter(p.get("suggested_action") or "?" for p in preds)
        print(f"  action dist: {dict(actions)}")
        directions = Counter(p.get("direction") or "?" for p in preds)
        print(f"  direction dist: {dict(directions)}")
        symbols = Counter(p.get("symbol") or "?" for p in preds)
        print(f"  unique symbols: {len(symbols)}")
        print(f"  top 6 symbols by count:")
        for s, n in symbols.most_common(6):
            print(f"    {s}: {n}")

# ---- 4. FIPI ----
print()
print("=" * 78)
print("4) FIPI flows (data/flows/fipi_daily.parquet)")
print("=" * 78)
fipi_path = ROOT / "data" / "flows" / "fipi_daily.parquet"
if fipi_path.exists():
    fdf = pd.read_parquet(fipi_path)
    print(f"  rows: {len(fdf)}")
    print(f"  columns: {list(fdf.columns)}")
    if len(fdf):
        d = fdf["date"]
        print(f"  earliest: {d.min()}  latest: {d.max()}")

# ---- 5. Macro ----
print()
print("=" * 78)
print("5) Macro snapshots")
print("=" * 78)
for fp in (ROOT/"data/macro").glob("*.parquet"):
    try:
        m = pd.read_parquet(fp)
        if "date" in m.columns:
            d = m["date"]
            print(f"  {fp.stem:<20} rows={len(m):>5}  {str(d.min())[:10]} -> {str(d.max())[:10]}")
        else:
            print(f"  {fp.stem:<20} rows={len(m):>5}  cols={list(m.columns)[:6]}")
    except Exception as e:
        print(f"  {fp.stem:<20} READ-FAIL: {e}")

# ---- 6. Playbook events ----
print()
print("=" * 78)
print("6) Playbook events (data/playbook/_events.json)")
print("=" * 78)
ev_path = ROOT / "data" / "playbook" / "_events.json"
if ev_path.exists():
    ev = json.loads(ev_path.read_text(encoding="utf-8"))
    events = ev.get("events", [])
    print(f"  total events: {len(events)}")
    if events:
        from collections import Counter
        keys = Counter(e.get("key") or "?" for e in events)
        print(f"  event keys ({len(keys)}):")
        for k, n in keys.most_common():
            print(f"    {k}: {n}")
        dates = sorted(e.get("date") or "" for e in events)
        print(f"  earliest: {dates[0]}  latest: {dates[-1]}")

# ---- 7. Per-stock OHLCV histogram ----
print()
print("=" * 78)
print("7) OHLCV depth histogram (years per stock)")
print("=" * 78)
import collections
buckets = collections.Counter()
for fp in ohlcv_files:
    df = pd.read_parquet(fp)
    if not len(df):
        continue
    yrs = (pd.to_datetime(df["date"].max()) - pd.to_datetime(df["date"].min())).days / 365.25
    if yrs < 1: buckets["< 1 yr"] += 1
    elif yrs < 2: buckets["1-2 yrs"] += 1
    elif yrs < 5: buckets["2-5 yrs"] += 1
    elif yrs < 10: buckets["5-10 yrs"] += 1
    else: buckets[">= 10 yrs"] += 1
for b in ["< 1 yr", "1-2 yrs", "2-5 yrs", "5-10 yrs", ">= 10 yrs"]:
    print(f"  {b}: {buckets.get(b, 0)} stocks")

print()
print("Inventory complete.")
