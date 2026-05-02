"""Derive Pakistani mutual-fund "smart money" flow signals.

Reads two parquets produced by ``scripts/ingest_ahl_mf_holdings.py``:

* ``data/flows/mutual_fund_holdings.parquet`` -- long-format
  per-(month, fund, symbol) holdings (% of fund AUM, n_shares).
* ``data/flows/mf_top_holdings_summary.parquet`` -- monthly
  per-symbol summary (n_funds_holding, holding_pct_of_ff,
  change_mom_pct_pts).

Per-stock signals exposed as ``signals_for(symbol, as_of)``:

  ``mf_n_funds_holding``              count of funds with non-zero pos
  ``mf_n_funds_increasing_30d``        funds whose pct_of_fund rose MoM
  ``mf_n_funds_decreasing_30d``        ditto, fell
  ``mf_n_funds_initiating_30d``        zero -> non-zero  (highest-conviction)
  ``mf_n_funds_exiting_30d``           non-zero -> zero
  ``mf_holding_change_30d_pct_ff``     aggregate change in % of free float
  ``mf_holding_change_90d_pct_ff``     same, 3-month look-back
  ``mf_holding_change_180d_pct_ff``    same, 6-month look-back (the
                                       "trend you asked about")
  ``mf_accumulation_streak``           consecutive months of net positive flow
  ``mf_distribution_streak``           consecutive months of net negative flow
  ``mf_data_freshness_days``           age of latest report in days
  ``mf_holding_pct_of_ff``             current % of free float held by MFs

Universe-level helpers:

  ``top_accumulated(as_of, k=10)``     stocks with biggest 6-month rise
  ``top_distributed(as_of, k=10)``     stocks with biggest 6-month fall
  ``n_funds_increasing_universe(as_of)``  total funds raising any position
  ``data_freshness_days(as_of)``       age of latest report

All functions degrade gracefully when the parquets are missing or the
requested ``as_of`` is older than any report on disk -- they return
``None`` (or an empty list) so the briefing builder can skip the lens
without raising.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
HOLDINGS_PARQUET = ROOT / "data" / "flows" / "mutual_fund_holdings.parquet"
SUMMARY_PARQUET = ROOT / "data" / "flows" / "mf_top_holdings_summary.parquet"


# ---------------------------------------------------------------------------
# Loading (cached)
# ---------------------------------------------------------------------------
@dataclass
class _Cache:
    holdings_mtime: float = 0.0
    summary_mtime: float = 0.0
    holdings = None
    summary = None


_CACHE = _Cache()


def _load_holdings():
    if not HOLDINGS_PARQUET.exists():
        return None
    mtime = HOLDINGS_PARQUET.stat().st_mtime
    if _CACHE.holdings is not None and mtime == _CACHE.holdings_mtime:
        return _CACHE.holdings
    import pandas as pd
    df = pd.read_parquet(HOLDINGS_PARQUET)
    if df.empty:
        return None
    df["as_of_month"] = pd.to_datetime(df["as_of_month"]).dt.normalize()
    df["pct_of_fund"] = df["pct_of_fund"].astype(float)
    _CACHE.holdings = df
    _CACHE.holdings_mtime = mtime
    return df


def _load_summary():
    if not SUMMARY_PARQUET.exists():
        return None
    mtime = SUMMARY_PARQUET.stat().st_mtime
    if _CACHE.summary is not None and mtime == _CACHE.summary_mtime:
        return _CACHE.summary
    import pandas as pd
    df = pd.read_parquet(SUMMARY_PARQUET)
    if df.empty:
        return None
    df["as_of_month"] = pd.to_datetime(df["as_of_month"]).dt.normalize()
    _CACHE.summary = df
    _CACHE.summary_mtime = mtime
    return df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_date(as_of: str | date | datetime | None) -> date:
    if as_of is None:
        return datetime.now().date()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return datetime.strptime(str(as_of)[:10], "%Y-%m-%d").date()


def _last_n_months(as_of: date, n: int) -> list[date]:
    """Return the first-of-month for the n months at or before
    ``as_of``, oldest first. n=2 with as_of=2025-06-15 returns
    [2025-05-01, 2025-06-01]."""
    out: list[date] = []
    y, m = as_of.year, as_of.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def _months_for(as_of: date, lookback_days: int) -> tuple[date, date]:
    """Return (older_month, newer_month) cutoffs for a window."""
    newer = date(as_of.year, as_of.month, 1)
    target = as_of - timedelta(days=lookback_days)
    older = date(target.year, target.month, 1)
    return older, newer


# ---------------------------------------------------------------------------
# Per-stock signals
# ---------------------------------------------------------------------------
def signals_for(symbol: str, as_of: str | date | datetime | None = None) -> dict:
    """Return all MF-flow signals for one stock at ``as_of``.

    Designed to fail open: every key is present, missing data ⇒ ``None``.
    """
    out: dict = {
        "mf_n_funds_holding":            None,
        "mf_n_funds_increasing_30d":     None,
        "mf_n_funds_decreasing_30d":     None,
        "mf_n_funds_initiating_30d":     None,
        "mf_n_funds_exiting_30d":        None,
        "mf_holding_change_30d_pct":     None,
        "mf_holding_change_90d_pct":     None,
        "mf_holding_change_180d_pct":    None,
        "mf_accumulation_streak":        None,
        "mf_distribution_streak":        None,
        "mf_holding_pct":                None,
        "mf_holding_pct_metric":         None,  # "ff" | "equity_aums"
        "mf_data_freshness_days":        None,
    }
    sym = str(symbol).upper().strip()
    asd = _to_date(as_of)

    # ---- Per-fund signals (from holdings parquet) ---------------------
    holdings = _load_holdings()
    if holdings is not None and not holdings.empty:
        df = holdings[holdings["symbol"] == sym].copy()
        # Months at or before as_of
        df = df[df["as_of_month"].dt.date <= asd]
        if not df.empty:
            months = sorted(df["as_of_month"].dt.date.unique())
            latest_month = months[-1]
            out["mf_data_freshness_days"] = (asd - latest_month).days

            # Latest n_funds_holding
            latest_df = df[df["as_of_month"].dt.date == latest_month]
            out["mf_n_funds_holding"] = int(latest_df["fund_name"]
                                              .dropna().nunique())

            # 30-day comparison: latest two months
            if len(months) >= 2:
                prev_month = months[-2]
                cur = (df[df["as_of_month"].dt.date == latest_month]
                          .set_index("fund_name")["pct_of_fund"]
                          .to_dict())
                prv = (df[df["as_of_month"].dt.date == prev_month]
                          .set_index("fund_name")["pct_of_fund"]
                          .to_dict())
                all_funds = set(cur) | set(prv)
                inc = dec = init = exit_ = 0
                for f in all_funds:
                    c = cur.get(f, 0.0)
                    p = prv.get(f, 0.0)
                    if p == 0 and c > 0:
                        init += 1
                    elif c == 0 and p > 0:
                        exit_ += 1
                    elif c > p:
                        inc += 1
                    elif c < p:
                        dec += 1
                out["mf_n_funds_increasing_30d"]  = inc
                out["mf_n_funds_decreasing_30d"]  = dec
                out["mf_n_funds_initiating_30d"]  = init
                out["mf_n_funds_exiting_30d"]     = exit_

                # Accumulation / distribution streaks: walk back from
                # latest month, count consecutive months where net
                # flow (sum of pct_of_fund changes) was positive / neg.
                acc = dis = 0
                positive_streak = True
                negative_streak = True
                for i in range(len(months) - 1, 0, -1):
                    m_now, m_prev = months[i], months[i - 1]
                    c_map = (df[df["as_of_month"].dt.date == m_now]
                                .set_index("fund_name")["pct_of_fund"]
                                .to_dict())
                    p_map = (df[df["as_of_month"].dt.date == m_prev]
                                .set_index("fund_name")["pct_of_fund"]
                                .to_dict())
                    delta = (sum(c_map.values()) - sum(p_map.values()))
                    if positive_streak and delta > 0:
                        acc += 1
                    else:
                        positive_streak = False
                    if negative_streak and delta < 0:
                        dis += 1
                    else:
                        negative_streak = False
                out["mf_accumulation_streak"]  = acc
                out["mf_distribution_streak"]  = dis

    # ---- Aggregate % of free float (from summary parquet) -------------
    summary = _load_summary()
    if summary is not None and not summary.empty:
        sdf = summary[summary["symbol"] == sym].copy()
        sdf = sdf[sdf["as_of_month"].dt.date <= asd]
        if not sdf.empty:
            sdf = sdf.sort_values("as_of_month")
            latest = sdf.iloc[-1]
            out["mf_holding_pct"] = float(latest["holding_pct"])
            out["mf_holding_pct_metric"] = (latest.get("metric_kind")
                                             if "metric_kind" in latest.index
                                             else None)
            if out["mf_data_freshness_days"] is None:
                m = latest["as_of_month"].date()
                out["mf_data_freshness_days"] = (asd - m).days
            # Fast path: the latest report's own MoM column gives us
            # an authoritative 30-day delta for the current month.
            if ("change_mom_pct_pts" in latest.index
                    and latest["change_mom_pct_pts"] is not None):
                try:
                    out["mf_holding_change_30d_pct"] = float(
                        latest["change_mom_pct_pts"])
                except (TypeError, ValueError):
                    pass

            # Holdings change windows -- only compare rows of the SAME
            # metric_kind because '% of FF' and '% of Equity AUMs' use
            # different denominators and are not directly comparable.
            cur_metric = (latest.get("metric_kind")
                          if "metric_kind" in latest.index else None)
            same_metric = sdf[
                (sdf.get("metric_kind") == cur_metric)
                if "metric_kind" in sdf.columns else slice(None)
            ] if cur_metric else sdf

            for window_days, key in (
                (30,  "mf_holding_change_30d_pct"),
                (90,  "mf_holding_change_90d_pct"),
                (180, "mf_holding_change_180d_pct"),
            ):
                if out[key] is not None:
                    continue
                older_month, newer_month = _months_for(asd, window_days)
                cur_row = same_metric[
                    same_metric["as_of_month"].dt.date == newer_month]
                if cur_row.empty:
                    cur_row = same_metric.iloc[[-1]] if not same_metric.empty else None
                if cur_row is None or cur_row.empty:
                    continue
                latest_month = cur_row.iloc[-1]["as_of_month"].date()
                old_candidates = same_metric[
                    (same_metric["as_of_month"].dt.date <= older_month)
                    & (same_metric["as_of_month"].dt.date < latest_month)
                ]
                if old_candidates.empty:
                    continue
                old_val = float(old_candidates.iloc[-1]["holding_pct"])
                cur_val = float(cur_row.iloc[-1]["holding_pct"])
                out[key] = round(cur_val - old_val, 4)

    return out


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------
def _universe_change_table(as_of: date, lookback_days: int):
    """Return (sorted_df, latest_month, freshness_days) where df is
    indexed by symbol with columns ``cur`` and ``change``.

    Compares only rows of the SAME ``metric_kind`` so '% of FF' and
    '% of Equity AUMs' results are never mixed.
    """
    summary = _load_summary()
    if summary is None or summary.empty:
        return None, None, None
    import pandas as pd
    sdf = summary[summary["as_of_month"].dt.date <= as_of].copy()
    if sdf.empty:
        return None, None, None
    months = sorted(sdf["as_of_month"].dt.date.unique())
    latest_month = months[-1]
    freshness = (as_of - latest_month).days

    cur_full = sdf[sdf["as_of_month"].dt.date == latest_month].copy()
    if cur_full.empty:
        return None, latest_month, freshness
    cur_metric = (cur_full.iloc[0].get("metric_kind")
                  if "metric_kind" in cur_full.columns else None)
    same_metric = sdf[sdf.get("metric_kind") == cur_metric] if cur_metric else sdf
    cur = cur_full.set_index("symbol")["holding_pct"]

    # Look for a comparable older month within the same metric
    older_month, _newer_month = _months_for(as_of, lookback_days)
    same_months = sorted(same_metric["as_of_month"].dt.date.unique())
    old_candidates = [m for m in same_months
                       if m <= older_month and m < latest_month]
    if old_candidates:
        ref_month = old_candidates[-1]
        old = (same_metric[same_metric["as_of_month"].dt.date == ref_month]
                  .set_index("symbol")["holding_pct"])
        joined = pd.concat([cur.rename("cur"), old.rename("old")], axis=1)
        joined["change"] = joined["cur"] - joined["old"]
        joined = joined.dropna(subset=["change"]).sort_values(
            "change", ascending=False)
        return joined, latest_month, freshness

    # Fallback: report's own MoM column (30d signal only)
    if lookback_days <= 45 and "change_mom_pct_pts" in cur_full.columns:
        cf = cur_full.dropna(subset=["change_mom_pct_pts"]).set_index("symbol")
        if not cf.empty:
            cf = cf.assign(change=cf["change_mom_pct_pts"].astype(float),
                            cur=cf["holding_pct"].astype(float))
            return (cf[["cur", "change"]].sort_values("change",
                                                       ascending=False),
                    latest_month, freshness)

    return None, latest_month, freshness


def top_accumulated(as_of: str | date | datetime | None = None,
                     k: int = 10, lookback_days: int = 180) -> list[dict]:
    """Stocks with the biggest rise in MF holding % over the window."""
    asd = _to_date(as_of)
    df, _m, _fr = _universe_change_table(asd, lookback_days)
    if df is None or df.empty:
        return []
    head = df.head(k)
    return [
        {"symbol": sym,
          "change_pct_pts": round(float(row["change"]), 4),
          "current_pct":   (round(float(row["cur"]), 4)
                              if "cur" in row.index else None)}
        for sym, row in head.iterrows() if row["change"] > 0
    ]


def top_distributed(as_of: str | date | datetime | None = None,
                     k: int = 10, lookback_days: int = 180) -> list[dict]:
    """Stocks with the biggest fall in MF holding % over the window."""
    asd = _to_date(as_of)
    df, _m, _fr = _universe_change_table(asd, lookback_days)
    if df is None or df.empty:
        return []
    tail = df.tail(k).iloc[::-1]
    return [
        {"symbol": sym,
          "change_pct_pts": round(float(row["change"]), 4),
          "current_pct":   (round(float(row["cur"]), 4)
                              if "cur" in row.index else None)}
        for sym, row in tail.iterrows() if row["change"] < 0
    ]


def n_funds_increasing_universe(as_of: str | date | datetime | None = None) -> int | None:
    """Count of (fund, symbol) pairs where pct_of_fund grew MoM."""
    asd = _to_date(as_of)
    holdings = _load_holdings()
    if holdings is None or holdings.empty:
        return None
    df = holdings[holdings["as_of_month"].dt.date <= asd].copy()
    if df.empty:
        return None
    months = sorted(df["as_of_month"].dt.date.unique())
    if len(months) < 2:
        return 0
    cur_m, prev_m = months[-1], months[-2]
    cur = (df[df["as_of_month"].dt.date == cur_m]
              .groupby(["fund_name", "symbol"])["pct_of_fund"].max())
    prv = (df[df["as_of_month"].dt.date == prev_m]
              .groupby(["fund_name", "symbol"])["pct_of_fund"].max())
    import pandas as pd
    joined = pd.concat([cur.rename("c"), prv.rename("p")], axis=1).fillna(0.0)
    return int((joined["c"] > joined["p"]).sum())


def data_freshness_days(as_of: str | date | datetime | None = None) -> int | None:
    asd = _to_date(as_of)
    summary = _load_summary()
    holdings = _load_holdings()
    months: list[date] = []
    for src in (summary, holdings):
        if src is not None and not src.empty:
            for m in src["as_of_month"].dt.date.unique():
                if m <= asd:
                    months.append(m)
    if not months:
        return None
    return (asd - max(months)).days


def universe_summary(as_of: str | date | datetime | None = None) -> dict:
    """Top-level snapshot used by the Master Strategist briefing."""
    asd = _to_date(as_of)
    return {
        "as_of":                       asd.isoformat(),
        "data_freshness_days":         data_freshness_days(asd),
        "top_accumulated_180d":        top_accumulated(asd, k=10,
                                                       lookback_days=180),
        "top_distributed_180d":        top_distributed(asd, k=10,
                                                       lookback_days=180),
        "top_accumulated_30d":         top_accumulated(asd, k=10,
                                                       lookback_days=30),
        "top_distributed_30d":         top_distributed(asd, k=10,
                                                       lookback_days=30),
        "n_funds_increasing_universe": n_funds_increasing_universe(asd),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> int:
    import argparse
    import json
    p = argparse.ArgumentParser()
    p.add_argument("--symbol")
    p.add_argument("--as-of", default=None)
    p.add_argument("--summary", action="store_true",
                    help="Show universe-level summary.")
    args = p.parse_args()
    if args.summary:
        print(json.dumps(universe_summary(args.as_of), indent=2, default=str))
    elif args.symbol:
        print(json.dumps(signals_for(args.symbol, args.as_of),
                          indent=2, default=str))
    else:
        # Default: dump universe summary
        print(json.dumps(universe_summary(args.as_of), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
