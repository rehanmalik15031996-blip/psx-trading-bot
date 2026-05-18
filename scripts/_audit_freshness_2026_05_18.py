"""Audit data freshness across all sources as of Friday May 15, 2026.

Checks every key data feed and reports:
  - latest_date    (most recent row in the file)
  - days_behind    (today - latest_date)
  - status         (FRESH / 1-DAY-STALE / STALE)

Today reference: Monday May 18 2026. So:
  - FRESH        = latest >= 2026-05-15 (Fri)
  - 1-DAY-STALE  = latest == 2026-05-14 (Thu)
  - STALE        = latest <= 2026-05-13 (Wed or earlier)
"""
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

TODAY = date(2026, 5, 18)
FRIDAY = date(2026, 5, 15)


def _status(d: date | None) -> str:
    if d is None:
        return "MISSING"
    if d >= FRIDAY:
        return "FRESH"
    days = (TODAY - d).days
    if days <= 2:
        return f"1-DAY-STALE ({d})"
    return f"STALE ({days}d behind)"


def _parquet_latest(path: Path, date_col: str = "date") -> tuple[date | None, int]:
    if not path.exists():
        return None, 0
    df = pd.read_parquet(path)
    if df.empty or date_col not in df.columns:
        return None, 0
    s = pd.to_datetime(df[date_col]).dt.date.max()
    return s, len(df)


def _json_latest(path: Path, field: str = "as_of") -> tuple[date | None, dict]:
    if not path.exists():
        return None, {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        s = d.get(field)
        if isinstance(s, str):
            return datetime.fromisoformat(s.split("T")[0]).date(), d
        return None, d
    except Exception:
        return None, {}


print("=" * 78)
print(f"  Data Freshness Audit  (today={TODAY}, target=Fri {FRIDAY})")
print("=" * 78)

# ----------- OHLCV (per-stock price data) -----------
print("\n--- OHLCV per-stock parquet files ---")
ohlcv_dir = Path("data/ohlcv")
parquets = sorted(ohlcv_dir.glob("*.parquet"))
print(f"  Files: {len(parquets)}")
if parquets:
    rows = []
    for p in parquets[:5]:
        d, n = _parquet_latest(p)
        rows.append((p.stem, d, n, _status(d)))
    for stem, d, n, st in rows:
        print(f"    {stem:<8} latest={d}  rows={n:<5}  {st}")
    # Aggregate across all
    latest_dates = []
    for p in parquets:
        d, _ = _parquet_latest(p)
        if d:
            latest_dates.append(d)
    if latest_dates:
        max_d = max(latest_dates)
        min_d = min(latest_dates)
        n_fresh = sum(1 for d in latest_dates if d >= FRIDAY)
        n_thursday = sum(1 for d in latest_dates if d == date(2026, 5, 14))
        n_stale = len(latest_dates) - n_fresh - n_thursday
        print(f"  Universe summary: max={max_d}  min={min_d}")
        print(f"    FRESH (>= Fri):       {n_fresh}/{len(latest_dates)}")
        print(f"    1-DAY-STALE (Thu):    {n_thursday}/{len(latest_dates)}")
        print(f"    STALE (Wed earlier):  {n_stale}/{len(latest_dates)}")

# ----------- Macro feeds -----------
print("\n--- Macro feeds (data/macro/*.parquet) ---")
macro_files = [
    "kse100.parquet", "brent.parquet", "wti.parquet",
    "copper.parquet", "gold.parquet", "btc.parquet",
    "pkr_usd.parquet", "tbill.parquet", "policy_rate.parquet",
    "reserves.parquet", "cotton.parquet",
]
for f in macro_files:
    p = Path("data/macro") / f
    d, n = _parquet_latest(p)
    print(f"  {f:<22} latest={d}  rows={n:<6}  {_status(d)}")

# ----------- Predictions log -----------
print("\n--- Predictions log ---")
pred_log = Path("data/predictions/log.parquet")
d, n = _parquet_latest(pred_log)
print(f"  log.parquet            latest={d}  rows={n:<6}  {_status(d)}")
# Latest predictions JSON
pred_latest = Path("data/predictions/latest.json")
d, body = _json_latest(pred_latest)
print(f"  latest.json            as_of={d}                     {_status(d)}")
if body.get("predictions"):
    n_preds = len(body["predictions"])
    print(f"    contains {n_preds} per-stock 5d forecasts")

# ----------- Strategist decisions -----------
print("\n--- Strategist decisions ---")
strat_files = sorted(Path("data/_strategist").glob("[0-9]*.json"))
print(f"  Total dated files: {len(strat_files)}")
if strat_files:
    print(f"  Latest 5 files:")
    for p in strat_files[-5:]:
        d, body = _json_latest(p, "as_of")
        head = body.get("headline", "")[:55]
        print(f"    {p.stem:<15}  {head}")

# v2 cache
v2 = Path("data/_strategist/latest_v2.json")
d, body = _json_latest(v2, "as_of")
print(f"  latest_v2.json         as_of={d}  {_status(d)}")
if body:
    print(f"    headline: {body.get('headline')}")
    print(f"    regime:   {body.get('regime')}")

# ----------- FIPI flows -----------
print("\n--- FIPI flows ---")
fipi_log = Path("data/macro/fipi_flows.parquet")
d, n = _parquet_latest(fipi_log)
print(f"  fipi_flows.parquet     latest={d}  rows={n:<6}  {_status(d)}")

# ----------- Verdict universe / value books -----------
print("\n--- Verdict & books ---")
for f in [
    ("data/verdict_universe/latest.json",      "as_of"),
    ("data/value/value_book.json",             "as_of"),
    ("data/quality/quality_book.json",         "as_of"),
    ("data/earnings_momentum/latest.json",     "as_of"),
    ("data/macro/macro_impact.json",           "as_of"),
    ("data/regime/regime.json",                "as_of"),
]:
    p = Path(f[0])
    d, body = _json_latest(p, f[1])
    print(f"  {p.name:<28} as_of={d}  {_status(d)}")

# ----------- News scoring -----------
print("\n--- News + sentiment ---")
news_dir = Path("data/news_scored")
if news_dir.exists():
    files = sorted(news_dir.glob("*.json"))
    print(f"  files in data/news_scored: {len(files)}")
    if files:
        for p in files[-3:]:
            print(f"    {p.name}")

# Health
print("\n--- Health badges ---")
for f in ["data/_health/strategist.json",
          "data/_health/strategist_v2.json"]:
    p = Path(f)
    if p.exists():
        body = json.loads(p.read_text(encoding="utf-8"))
        ts = body.get("ts") or body.get("as_of")
        print(f"  {p.name:<30} ts={ts}")
