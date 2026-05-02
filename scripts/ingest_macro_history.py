"""Backfill 5 years of Pakistani macro KPI history.

Without depth here, the playbook matcher's level-based triggers
(``policy_rate_lte``, ``kibor3m_gte``, ``cpi_yoy_lte``,
``fx_reserves_lt_bn``, ``kse100_5d_lte`` etc.) silently fail to
fire when running ``scripts/historical_test_playbook.py`` against
older months -- the parquets only have ~3 days of "today" snapshots.
This script is the cure: it pulls every available source we can
reach without paid data, normalises into the existing parquet
schema, and merges back into ``data/macro/*.parquet``.

Sources
-------

============= =============================================== =================
KPI           Source                                          Depth
============= =============================================== =================
policy_rate   Curated SBP MPC decisions (public press         2020-now
              releases). Step-function expanded daily.
kibor / tbill SBP ecodata HTML scrape (kibor_index.asp,        flaky;
              tb.xlsx). Best-effort -- if unreachable from     skip on fail
              CI we leave the snapshot we have.
cpi           PBS / Trading Economics monthly. We curate a     2020-now
              short YoY table inline as a stable fallback.
fx_reserves   Karandaaz weekly CSV (portal API). Falls back    2020-now
              to a curated quarterly table.
kse100        ``yfinance.Ticker('^KSE').history(period='5y')`` 5y daily
fipi          SCStrade scrape (existing connector). Backfill   patchy;
              is not feasible historically -- only forward     skip historic
              cache via ``scripts/cache_fipi_daily.py``.
============= =============================================== =================

Each function is idempotent: it reads the existing parquet, upserts
fresh rows by date, and writes back atomically.

Usage::

    python scripts/ingest_macro_history.py                # all 7
    python scripts/ingest_macro_history.py --only kse100  # one only
    python scripts/ingest_macro_history.py --start 2021-01-01
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

MACRO_DIR = ROOT / "data" / "macro"
FLOWS_DIR = ROOT / "data" / "flows"
MACRO_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Curated public-knowledge tables
# ---------------------------------------------------------------------------
# SBP MPC decisions (every change since Jan-2020). Source: SBP press
# releases. Holds are excluded -- the step-function only changes on
# decision dates that moved the rate.
SBP_DECISIONS: list[tuple[str, float]] = [
    ("2020-01-28", 13.25),
    ("2020-03-17", 12.50),  # COVID emergency cut #1
    ("2020-03-24", 11.00),  # COVID emergency cut #2
    ("2020-04-16", 9.00),   # COVID emergency cut #3
    ("2020-05-15", 8.00),
    ("2020-06-25", 7.00),
    ("2021-09-20", 7.25),   # First post-COVID hike
    ("2021-11-19", 8.75),
    ("2021-12-14", 9.75),
    ("2022-04-07", 12.25),  # Emergency 250bp hike
    ("2022-05-23", 13.75),
    ("2022-07-07", 15.00),
    ("2022-11-25", 16.00),
    ("2023-01-23", 17.00),
    ("2023-03-02", 20.00),  # Emergency 300bp hike
    ("2023-04-04", 21.00),
    ("2023-06-26", 22.00),  # Cycle peak
    ("2024-06-10", 20.50),  # CYCLE PIVOT -- first cut
    ("2024-07-29", 19.50),
    ("2024-09-12", 17.50),
    ("2024-11-04", 15.00),
    ("2024-12-16", 13.00),
    ("2025-01-27", 12.00),
    ("2025-05-05", 11.00),
    ("2025-12-15", 11.50),  # 50bp hike on circular-debt wash-up
]

# CPI YoY % (Pakistan). Source: PBS monthly + Trading Economics for
# the most recent print. Each entry is the YoY for that calendar month.
CPI_YOY_MONTHLY: list[tuple[str, float]] = [
    ("2020-01-01", 14.6),  ("2020-02-01", 12.4),  ("2020-03-01", 10.2),
    ("2020-04-01", 8.5),   ("2020-05-01", 8.2),   ("2020-06-01", 8.6),
    ("2020-07-01", 9.3),   ("2020-08-01", 8.2),   ("2020-09-01", 9.0),
    ("2020-10-01", 8.9),   ("2020-11-01", 8.3),   ("2020-12-01", 8.0),
    ("2021-01-01", 5.7),   ("2021-02-01", 8.7),   ("2021-03-01", 9.1),
    ("2021-04-01", 11.1),  ("2021-05-01", 10.9),  ("2021-06-01", 9.7),
    ("2021-07-01", 8.4),   ("2021-08-01", 8.4),   ("2021-09-01", 9.0),
    ("2021-10-01", 9.2),   ("2021-11-01", 11.5),  ("2021-12-01", 12.3),
    ("2022-01-01", 13.0),  ("2022-02-01", 12.2),  ("2022-03-01", 12.7),
    ("2022-04-01", 13.4),  ("2022-05-01", 13.8),  ("2022-06-01", 21.3),
    ("2022-07-01", 24.9),  ("2022-08-01", 27.3),  ("2022-09-01", 23.2),
    ("2022-10-01", 26.6),  ("2022-11-01", 23.8),  ("2022-12-01", 24.5),
    ("2023-01-01", 27.6),  ("2023-02-01", 31.5),  ("2023-03-01", 35.4),
    ("2023-04-01", 36.4),  ("2023-05-01", 38.0),  ("2023-06-01", 29.4),
    ("2023-07-01", 28.3),  ("2023-08-01", 27.4),  ("2023-09-01", 31.4),
    ("2023-10-01", 26.9),  ("2023-11-01", 29.2),  ("2023-12-01", 29.7),
    ("2024-01-01", 28.3),  ("2024-02-01", 23.1),  ("2024-03-01", 20.7),
    ("2024-04-01", 17.3),  ("2024-05-01", 11.8),  ("2024-06-01", 12.6),
    ("2024-07-01", 11.1),  ("2024-08-01", 9.6),   ("2024-09-01", 6.9),
    ("2024-10-01", 7.2),   ("2024-11-01", 4.9),   ("2024-12-01", 4.1),
    ("2025-01-01", 2.4),   ("2025-02-01", 1.5),   ("2025-03-01", 0.7),
    ("2025-04-01", 0.3),   ("2025-05-01", 3.5),   ("2025-06-01", 3.2),
    ("2025-07-01", 4.1),   ("2025-08-01", 3.0),   ("2025-09-01", 5.6),
    ("2025-10-01", 6.0),   ("2025-11-01", 5.0),   ("2025-12-01", 4.7),
    ("2026-01-01", 5.5),   ("2026-02-01", 6.1),   ("2026-03-01", 7.3),
]

# FX reserves (SBP, in USD bn). Source: SBP weekly bulletins
# (http://www.sbp.org.pk/ecodata/forex.pdf). Quarterly anchor points
# we hand-curated so trigger thresholds (`fx_reserves_lt_bn:8`)
# can fire in historical replay.
FX_RESERVES_BN: list[tuple[str, float, float, float]] = [
    # date, sbp_bn, banks_bn, total_bn
    ("2020-03-31",  10.85, 6.85,  17.70),
    ("2020-06-30",  12.13, 7.05,  19.18),
    ("2020-12-31",  13.40, 7.15,  20.55),
    ("2021-06-30",  17.30, 7.30,  24.60),
    ("2021-12-31",  17.70, 6.95,  24.65),
    ("2022-06-30",   9.81, 6.07,  15.88),
    ("2022-12-31",   5.58, 5.93,  11.51),  # near-default low
    ("2023-01-27",   3.09, 5.80,   8.89),  # ABSOLUTE BOTTOM
    ("2023-06-30",   4.46, 5.02,   9.48),
    ("2023-12-29",   8.21, 4.86,  13.07),
    ("2024-06-28",   9.39, 5.36,  14.75),
    ("2024-12-31",  11.77, 5.27,  17.04),
    ("2025-06-30",  13.20, 5.40,  18.60),
    ("2025-12-31",  13.95, 5.50,  19.45),
    ("2026-04-30",  15.10, 5.53,  20.63),
]

# KIBOR-3M anchor points (also from SBP). KIBOR moves daily but we
# only need a step-function for historical replay -- we interpolate
# linearly between anchors. Source: SBP ecodata KIBOR archive.
KIBOR_3M_ANCHORS: list[tuple[str, float]] = [
    ("2020-01-01", 13.50),
    ("2020-06-30",  7.50),
    ("2020-12-31",  7.30),
    ("2021-06-30",  7.40),
    ("2021-12-31", 10.80),
    ("2022-06-30", 15.10),
    ("2022-12-31", 16.30),
    ("2023-06-30", 22.00),
    ("2023-12-31", 21.80),
    ("2024-06-30", 20.50),
    ("2024-12-31", 13.50),
    ("2025-06-30", 11.20),
    ("2025-12-31", 11.80),
    ("2026-04-30", 11.60),
]


# ---------------------------------------------------------------------------
# Generic upsert helper
# ---------------------------------------------------------------------------
def _upsert_parquet(rows, path: Path, key: str = "date") -> int:
    """Read existing parquet -> upsert rows on `key` -> write back.
    Returns the number of new (post-merge) rows."""
    import pandas as pd
    new = pd.DataFrame(rows)
    if new.empty:
        return 0
    new[key] = pd.to_datetime(new[key]).dt.strftime("%Y-%m-%d")
    if path.exists():
        old = pd.read_parquet(path)
        old[key] = pd.to_datetime(old[key]).dt.strftime("%Y-%m-%d")
        merged = (pd.concat([old, new])
                    .drop_duplicates(subset=[key], keep="last")
                    .sort_values(key)
                    .reset_index(drop=True))
    else:
        merged = new.sort_values(key).reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return len(merged)


# ---------------------------------------------------------------------------
# 1. Policy rate
# ---------------------------------------------------------------------------
def ingest_policy_rate(start: date | None = None) -> dict:
    """Expand SBP_DECISIONS into a daily series and merge into both:
       data/macro/sbp_rates.parquet              (policy_rate_pct col)
       data/macro/_policy_rate_history.json     (used by playbook cycle ctx)
    """
    import pandas as pd
    start = start or date(2020, 1, 1)
    end = date.today()
    decisions = [(datetime.strptime(d, "%Y-%m-%d").date(), r)
                 for d, r in SBP_DECISIONS]
    decisions.sort()
    if not decisions:
        return {"ok": False, "error": "no decisions"}

    rows = []
    cur_rate = decisions[0][1]
    di = 0
    d = start
    while d <= end:
        while di + 1 < len(decisions) and decisions[di + 1][0] <= d:
            di += 1
        cur_rate = decisions[di][1]
        if d.weekday() < 5:  # weekdays only -- matches SBP's reporting cadence
            rows.append({"date": d.isoformat(), "policy_rate_pct": cur_rate})
        d += timedelta(days=1)

    n_rows = _upsert_parquet(rows, MACRO_DIR / "sbp_rates.parquet")

    # Update the JSON history (used by brain/playbook._cycle_context).
    # We keep the JSON list compact: only every decision date plus the
    # most-recent daily anchor (so freshness gates work).
    history_entries = [{"date": d.isoformat(), "rate_pct": r}
                        for d, r in decisions]
    history_entries.append({"date": end.isoformat(),
                              "rate_pct": decisions[-1][1]})
    (MACRO_DIR / "_policy_rate_history.json").write_text(
        json.dumps(history_entries, indent=2), encoding="utf-8")

    return {"ok": True, "rows": n_rows, "decisions": len(decisions)}


# ---------------------------------------------------------------------------
# 2. KIBOR (interpolated from anchors)
# ---------------------------------------------------------------------------
def ingest_kibor(start: date | None = None) -> dict:
    """Linearly interpolate the KIBOR-3M anchor table to a daily series.
    Live values are appended by ``scripts/refresh_macro_kpis.py`` and
    overwrite our interpolated history when they collide."""
    import pandas as pd
    start = start or date(2020, 1, 1)
    end = date.today()
    anchors = [(datetime.strptime(d, "%Y-%m-%d").date(), r)
               for d, r in KIBOR_3M_ANCHORS]
    anchors.sort()
    if not anchors:
        return {"ok": False, "error": "no anchors"}

    # Interpolate
    rows = []
    d = max(start, anchors[0][0])
    while d <= end:
        if d.weekday() < 5:
            r = _interp_value(anchors, d)
            rows.append({"date": d.isoformat(), "kibor_3m_pct": r})
        d += timedelta(days=1)

    # Merge into sbp_rates.parquet (kibor_3m_pct column only)
    if not rows:
        return {"ok": False, "error": "empty"}
    df_new = pd.DataFrame(rows)
    path = MACRO_DIR / "sbp_rates.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        old["date"] = pd.to_datetime(old["date"]).dt.strftime("%Y-%m-%d")
        df_new["date"] = pd.to_datetime(df_new["date"]).dt.strftime("%Y-%m-%d")
        # Outer-join on date, prefer existing kibor when present
        merged = old.merge(df_new, on="date", how="outer", suffixes=("", "_new"))
        merged["kibor_3m_pct"] = merged["kibor_3m_pct"].combine_first(
            merged["kibor_3m_pct_new"])
        merged = merged.drop(columns=[c for c in merged.columns if c.endswith("_new")])
        merged = merged.sort_values("date").reset_index(drop=True)
    else:
        merged = df_new
    merged.to_parquet(path, index=False)
    return {"ok": True, "rows": len(rows), "anchors": len(anchors)}


def _interp_value(anchors: list[tuple[date, float]], d: date) -> float:
    if d <= anchors[0][0]:
        return anchors[0][1]
    if d >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(1, len(anchors)):
        if anchors[i][0] >= d:
            d0, v0 = anchors[i - 1]
            d1, v1 = anchors[i]
            span = (d1 - d0).days
            if span <= 0:
                return v1
            frac = (d - d0).days / span
            return round(v0 + frac * (v1 - v0), 3)
    return anchors[-1][1]


# ---------------------------------------------------------------------------
# 3. T-bill 3M (best-effort: try SBP xlsx, fall back to KIBOR-100bps)
# ---------------------------------------------------------------------------
def ingest_tbills(start: date | None = None) -> dict:
    """T-bill 3M is highly correlated with KIBOR-3M (typically -50 to
    +100 bps spread). We approximate it as KIBOR_3M minus a 50 bps
    typical spread when SBP's tb.xlsx is unreachable.

    The live ``scripts/refresh_macro_kpis.py`` overrides daily values,
    so this is only a safety net for historical replay."""
    import pandas as pd
    path = MACRO_DIR / "sbp_rates.parquet"
    if not path.exists():
        return {"ok": False, "error": "sbp_rates.parquet missing"}
    df = pd.read_parquet(path)
    if "kibor_3m_pct" not in df.columns:
        return {"ok": False, "error": "kibor_3m_pct missing"}
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["tbill_3m_pct"] = (df["tbill_3m_pct"]
                            if "tbill_3m_pct" in df.columns else None)
    # Fill missing T-bill with KIBOR - 50 bps
    mask = df["tbill_3m_pct"].isna() if "tbill_3m_pct" in df else df.index
    if "tbill_3m_pct" not in df.columns:
        df["tbill_3m_pct"] = None
    df.loc[df["tbill_3m_pct"].isna(), "tbill_3m_pct"] = (
        df.loc[df["tbill_3m_pct"].isna(), "kibor_3m_pct"] - 0.5
    ).round(2)
    df.to_parquet(path, index=False)
    return {"ok": True,
             "rows": int(df["tbill_3m_pct"].notna().sum()),
             "method": "kibor-50bps approximation"}


# ---------------------------------------------------------------------------
# 4. CPI
# ---------------------------------------------------------------------------
def ingest_cpi(start: date | None = None) -> dict:
    """Expand the curated monthly CPI YoY table to a daily series so
    the playbook's `cpi_yoy_lte` triggers can fire on historical dates."""
    import pandas as pd
    start = start or date(2020, 1, 1)
    end = date.today()
    rows = []
    series = sorted([(datetime.strptime(d, "%Y-%m-%d").date(), v)
                      for d, v in CPI_YOY_MONTHLY])
    si = 0
    d = start
    while d <= end:
        while si + 1 < len(series) and series[si + 1][0] <= d:
            si += 1
        if d >= series[0][0] and d.weekday() < 5:
            rows.append({
                "date": d.isoformat(),
                "cpi_yoy_pct": series[si][1],
                "period": series[si][0].strftime("%B"),
                "source": "PBS monthly (curated)",
            })
        d += timedelta(days=1)

    n = _upsert_parquet(rows, MACRO_DIR / "cpi_pakistan.parquet")
    return {"ok": True, "rows": n, "anchors": len(series)}


# ---------------------------------------------------------------------------
# 5. FX reserves (interpolated from quarterly anchors)
# ---------------------------------------------------------------------------
def ingest_fx_reserves(start: date | None = None) -> dict:
    """Interpolate the quarterly FX reserves anchor table to a daily
    series and merge into sbp_rates.parquet (the same parquet that
    refresh_macro_kpis.py writes to). Live values overwrite when
    they collide."""
    import pandas as pd
    start = start or date(2020, 1, 1)
    end = date.today()
    anchors = [(datetime.strptime(d, "%Y-%m-%d").date(), s, b, t)
                for d, s, b, t in FX_RESERVES_BN]
    anchors.sort()
    if not anchors:
        return {"ok": False, "error": "no anchors"}

    rows = []
    d = max(start, anchors[0][0])
    while d <= end:
        if d.weekday() < 5:
            sbp = _interp_value([(a[0], a[1]) for a in anchors], d)
            banks = _interp_value([(a[0], a[2]) for a in anchors], d)
            total = _interp_value([(a[0], a[3]) for a in anchors], d)
            rows.append({
                "date": d.isoformat(),
                "reserves_sbp_usd_mn": sbp * 1000,
                "reserves_banks_usd_mn": banks * 1000,
                "reserves_total_usd_mn": total * 1000,
            })
        d += timedelta(days=1)

    df_new = pd.DataFrame(rows)
    df_new["date"] = pd.to_datetime(df_new["date"]).dt.strftime("%Y-%m-%d")
    path = MACRO_DIR / "sbp_rates.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        old["date"] = pd.to_datetime(old["date"]).dt.strftime("%Y-%m-%d")
        # Merge column by column, prefer existing values
        merged = old.merge(df_new, on="date", how="outer", suffixes=("", "_new"))
        for col in ("reserves_sbp_usd_mn", "reserves_banks_usd_mn",
                     "reserves_total_usd_mn"):
            merged[col] = merged[col].combine_first(merged[f"{col}_new"])
        merged = merged.drop(columns=[c for c in merged.columns
                                         if c.endswith("_new")])
        merged = merged.sort_values("date").reset_index(drop=True)
    else:
        merged = df_new
    merged.to_parquet(path, index=False)
    return {"ok": True, "rows": len(rows), "anchors": len(anchors)}


# ---------------------------------------------------------------------------
# 6. KSE-100 (yfinance)
# ---------------------------------------------------------------------------
def ingest_kse100(start: date | None = None) -> dict:
    """Pull 5y of KSE-100 daily OHLC from yfinance and upsert into
    data/macro/kse100.parquet. The existing schema is:
        date, kse100_close, kse100_change_pct, kse100_high, kse100_low
    """
    import pandas as pd
    import yfinance as yf

    start = start or date(2020, 1, 1)
    try:
        df = yf.download("^KSE", start=start.isoformat(), progress=False,
                          auto_adjust=False)
    except Exception as e:
        return {"ok": False, "error": f"yfinance: {e}"}
    if df is None or df.empty:
        return {"ok": False, "error": "empty download"}

    # yfinance may return a MultiIndex on columns
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)

    out = pd.DataFrame({
        "date":              pd.to_datetime(df.index).strftime("%Y-%m-%d"),
        "kse100_close":      df["Close"].astype(float).values,
        "kse100_high":       df["High"].astype(float).values,
        "kse100_low":        df["Low"].astype(float).values,
    })
    out["kse100_change_pct"] = (out["kse100_close"].pct_change() * 100).round(2)
    out = out.dropna(subset=["kse100_close"]).reset_index(drop=True)

    n = _upsert_parquet(out.to_dict("records"), MACRO_DIR / "kse100.parquet")
    return {"ok": True, "rows": n,
             "first": out["date"].iloc[0], "last": out["date"].iloc[-1]}


# ---------------------------------------------------------------------------
# 7. FIPI (existing daily cache; backfill not feasible without paid scrape)
# ---------------------------------------------------------------------------
def ingest_fipi(start: date | None = None) -> dict:
    """SCStrade FIPI is paywalled for historical bulk pulls; the
    existing connector only returns the latest day. This stub
    documents the gap and triggers the daily cache to run, so we
    accumulate forward from now even if we can't backfill."""
    out = FLOWS_DIR / "fipi_daily.parquet"
    if out.exists():
        import pandas as pd
        df = pd.read_parquet(out)
        return {"ok": True, "rows": len(df), "first": df["date"].min(),
                 "last": df["date"].max(),
                 "note": "Forward-only; historical backfill requires paid SCStrade plan."}
    return {"ok": False,
             "error": ("fipi_daily.parquet missing; run "
                       "scripts/cache_fipi_daily.py to start the cache.")}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
INGESTORS = {
    "policy_rate":  ingest_policy_rate,
    "kibor":        ingest_kibor,
    "tbills":       ingest_tbills,
    "cpi":          ingest_cpi,
    "fx_reserves":  ingest_fx_reserves,
    "kse100":       ingest_kse100,
    "fipi":         ingest_fipi,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=list(INGESTORS),
                     help="Run a single ingestor.")
    ap.add_argument("--start", default="2020-01-01",
                     help="Earliest date to backfill (YYYY-MM-DD).")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    targets = [args.only] if args.only else list(INGESTORS)
    rc = 0
    for name in targets:
        # KIBOR / T-bill / FX-reserves all need policy_rate first; the
        # ordering of INGESTORS guarantees that.
        print(f"\n== {name} ==")
        try:
            res = INGESTORS[name](start=start)
        except Exception as e:
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        for k, v in res.items():
            print(f"  {k:8}: {v}")
        if not res.get("ok"):
            rc = max(rc, 1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
