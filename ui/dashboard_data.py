"""Morning-brief aggregator.

One function, one dict. Pulls from the existing tool layer and the on-disk
caches so the Dashboard tab has a single source of truth without duplicating
business logic.

Returns a dict of the form:
  {
    "as_of":               "2026-04-24",
    "regime":              {... from tools.get_market_regime() ...},
    "strategy_signal":     {... from tools.get_strategy_signal() ...},
    "overnight":           {... from tools.get_overnight_signals() ...},
    "sentiment":           {... from tools.get_scored_sentiment() ...},
    "portfolio":           {... from tools.get_user_portfolio() ...},
    "journal_stats":       {... from trade_journal.journal_stats() ...},
    "predictions":         {... from tools.get_todays_predictions() ...},
    "prediction_accuracy": {... from load_prediction_log_stats() ...},
    "top_buys":            {... from tools.recommend_new_buys(max_ideas=5) ...},
    "universe_movers":     [{"symbol": "...", "ret_1d_pct": 2.3}, ...],
  }

Each section can individually fail with an {"error": "..."} dict; downstream
UI code renders placeholders rather than aborting the whole brief.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ui import tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS_LOG = PROJECT_ROOT / "data" / "predictions_log.json"


def _safe(fn, *args, **kwargs) -> dict:
    try:
        r = fn(*args, **kwargs)
        return r if isinstance(r, dict) else {"value": r}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def load_prediction_log_stats(last_n_days: int = 30) -> dict:
    """Summarize the hit rate of stored predictions (gross and net of costs).

    Walks `data/predictions_log.json` and keeps only entries whose actuals have
    been filled in by `scripts/check_predictions.py`. Computes:
      - scored_count
      - direction_hit_rate_gross / _net
      - avg_expected_return_pct (gross)
      - avg_actual_return_pct (gross / net)
      - inside_range_hit_rate
    """
    if not PREDICTIONS_LOG.exists():
        return {"error": "predictions_log.json missing"}
    try:
        data = json.loads(PREDICTIONS_LOG.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"could not parse predictions_log.json: {e}"}

    preds = data.get("predictions") or []
    scored = [p for p in preds
              if p.get("actual") is not None
              and isinstance(p.get("actual"), dict)
              and p["actual"].get("actual_return_pct") is not None]
    if last_n_days:
        # The predictions are timestamped by prediction date; keep the most
        # recent N calendar days of SCORED predictions.
        scored.sort(
            key=lambda p: p.get("data_snapshot", {}).get("as_of_price_date") or "",
            reverse=True,
        )
        # Not "last N days" strictly — just the most recent N*15 rows in case
        # many tickers are scored per day.
        scored = scored[: last_n_days * 15]

    n = len(scored)
    if n == 0:
        return {"scored_count": 0,
                "note": "No scored predictions yet. Run the EOD workflow "
                         "to populate actuals."}

    dh_gross = sum(1 for p in scored if p["actual"].get("direction_hit_gross")
                    or p["actual"].get("direction_hit"))
    dh_net = sum(1 for p in scored if p["actual"].get("direction_hit_net"))
    inside = sum(1 for p in scored if p["actual"].get("inside_range"))

    avg_exp = sum(float(p.get("expected_return_5d_mid_pct") or 0)
                  for p in scored) / n
    avg_act_gross = sum(float(p["actual"].get("actual_return_pct") or 0)
                        for p in scored) / n
    avg_act_net = sum(float(p["actual"].get("actual_return_net_pct")
                             or p["actual"].get("actual_return_pct") or 0)
                       for p in scored) / n

    return {
        "scored_count": n,
        "direction_hit_rate_gross_pct":
            round(100.0 * dh_gross / n, 1),
        "direction_hit_rate_net_pct":
            round(100.0 * dh_net / n, 1) if dh_net else None,
        "inside_range_hit_rate_pct": round(100.0 * inside / n, 1),
        "avg_expected_return_pct": round(avg_exp, 2),
        "avg_actual_return_gross_pct": round(avg_act_gross, 2),
        "avg_actual_return_net_pct": round(avg_act_net, 2),
    }


def universe_index_history(days: int = 60) -> dict:
    """Equal-weighted index of the 15-stock universe, normalised to 100.

    Used by the Today tab sparkline as a stand-in for KSE-100 (we don't
    cache the official index — but an equal-weighted basket of our
    universe is a good directional proxy because these are mostly
    blue-chip / index constituents).
    """
    try:
        ranking = tools.get_universe_ranking()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    syms = [r["symbol"] for r in ranking.get("ranking", [])]
    if not syms:
        return {"error": "no universe symbols"}

    series: list[list[float]] = []  # list of normalized close arrays
    dates_ref: list[str] = []
    for sym in syms:
        h = _safe(tools.get_price_history, sym, days=int(days))
        bars = h.get("bars") or []
        if len(bars) < 5:
            continue
        # Normalise each symbol's series to start at 100
        first = float(bars[0]["close"])
        if first <= 0:
            continue
        norm = [round(float(b["close"]) / first * 100.0, 4) for b in bars]
        if not dates_ref or len(bars) > len(dates_ref):
            dates_ref = [b["date"] for b in bars]
        series.append(norm)

    if not series:
        return {"error": "no price history available"}
    # Align all series to the longest one, padding shorter at the front by
    # repeating their first value (rare on PSX where data is uniform, but
    # safe).
    max_len = max(len(s) for s in series)
    aligned: list[list[float]] = []
    for s in series:
        if len(s) < max_len:
            s = [s[0]] * (max_len - len(s)) + s
        aligned.append(s)
    # Equal-weighted average per day
    n = len(aligned)
    avg = [sum(col) / n for col in zip(*aligned)]
    # Compute key stats
    last = avg[-1]
    first_v = avg[0]
    pct_change = (last / first_v - 1.0) * 100.0 if first_v else 0.0
    return {
        "series_label": "Universe (eq-weighted, 100=start)",
        "as_of_first": dates_ref[0] if dates_ref else None,
        "as_of_last": dates_ref[-1] if dates_ref else None,
        "n_symbols": n,
        "values": avg,
        "dates": dates_ref[-len(avg):],
        "pct_change_pct": round(pct_change, 2),
    }


def universe_movers(top_k: int = 3) -> dict:
    """Top-k gainers and top-k losers in the universe for the latest bar.

    `tools.get_price` returns returns as fractions (e.g. 0.012 for +1.2%);
    we multiply by 100 so callers receive percent-scaled numbers.
    """
    try:
        ranking = tools.get_universe_ranking()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    if "error" in ranking:
        return {"error": ranking["error"]}

    movers: list[dict] = []
    for sym in (r["symbol"] for r in ranking.get("ranking", [])):
        p = _safe(tools.get_price, sym)
        if "error" in p:
            continue
        r1 = p.get("ret_1d")
        r5 = p.get("ret_5d")
        movers.append({
            "symbol": sym,
            "close_pkr": p.get("close_pkr"),
            "ret_1d_pct": round(r1 * 100, 2) if r1 is not None else None,
            "ret_5d_pct": round(r5 * 100, 2) if r5 is not None else None,
        })
    movers = [m for m in movers if m.get("ret_1d_pct") is not None]
    movers.sort(key=lambda m: float(m["ret_1d_pct"]))
    return {
        "losers": movers[: top_k],
        "gainers": list(reversed(movers[-top_k:])),
    }


def management_outlook_history(symbol: str) -> list[dict]:
    """All cached Director's Report extractions for `symbol`,
    newest-first. Used by the per-stock drill-down view in the
    Reports tab so the user can see how management's outlook has
    evolved over the last few quarters."""
    p = PROJECT_ROOT / "data" / "results" / "reports.parquet"
    if not p.exists():
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(p)
    except Exception:
        return []
    if df.empty:
        return []

    df = df[df["symbol"].str.upper() == symbol.upper()].copy()
    if df.empty:
        return []

    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df = df.sort_values("filing_date", ascending=False)

    def _list(v):
        try:
            return [] if v is None else list(v.tolist() if hasattr(v, "tolist")
                                                 else v)
        except Exception:
            return []

    out: list[dict] = []
    for _, r in df.iterrows():
        # capacity_utilization_pct may be missing on older rows (pre-extension)
        cap_util = r.get("capacity_utilization_pct") if (
            "capacity_utilization_pct" in df.columns) else None
        try:
            cap_util_f = (None if cap_util is None or cap_util != cap_util
                          else float(cap_util))
        except (TypeError, ValueError):
            cap_util_f = None
        out.append({
            "symbol": r["symbol"],
            "filing_date": (r["filing_date"].strftime("%Y-%m-%d")
                              if pd.notna(r["filing_date"]) else None),
            "doc_type": r["doc_type"],
            "fy_period": r.get("fy_period") or "",
            "outlook_summary": r["outlook_summary"],
            "outlook_tone": float(r["outlook_tone"] or 0.0),
            "growth_plans": _list(r.get("growth_plans")),
            "risks_mentioned": _list(r.get("risks_mentioned")),
            "guidance_strength": r.get("guidance_strength") or "LOW",
            "capex_announced": bool(r.get("capex_announced") or False),
            "expansion_announced":
                bool(r.get("expansion_announced") or False),
            # Capacity & expansion (analyst-requested)
            "installed_capacity": (
                r.get("installed_capacity")
                if "installed_capacity" in df.columns else None) or None,
            "actual_production": (
                r.get("actual_production")
                if "actual_production" in df.columns else None) or None,
            "capacity_utilization_pct": cap_util_f,
            "new_products": _list(r.get("new_products"))
                              if "new_products" in df.columns else [],
            "key_financials_called_out":
                dict(r["key_financials_called_out"])
                if r.get("key_financials_called_out") is not None else {},
            "raw_excerpt": r.get("raw_excerpt") or "",
            "title": r.get("title") or "",
            "pdf_url": r.get("pdf_url") or "",
            "extracted_by_model": r.get("extracted_by_model") or "",
            "extraction_seconds": r.get("extraction_seconds") or None,
        })
    return out


def latest_management_outlook(symbol: str | None = None,
                                top_k: int = 15) -> dict:
    """Latest extracted Director's Report / outlook per symbol.

    Reads `data/results/reports.parquet` (built by
    `scripts/extract_director_report.py`) and returns the most recent
    filing per symbol that actually contained narrative outlook
    commentary (skipping pure dividend notices etc.).

    Returns:
        {
          "as_of": "2026-04-27",
          "rows": [
            {symbol, filing_date, doc_type, fy_period, outlook_summary,
             outlook_tone, growth_plans (list), risks_mentioned (list),
             guidance_strength, capex_announced, expansion_announced,
             title, pdf_url, extracted_by_model},
            ...
          ],
          "fresh_this_week": int,
          "summary": "12 reports cached; 3 fresh this week (HUBC/MCB/MEBL).",
        }
    """
    p = PROJECT_ROOT / "data" / "results" / "reports.parquet"
    out = {"as_of": None, "rows": [], "fresh_this_week": 0,
            "summary": "No filings extracted yet."}
    if not p.exists():
        return out
    try:
        import pandas as pd
        df = pd.read_parquet(p)
    except Exception as e:
        out["summary"] = f"reports.parquet unreadable: {e}"
        return out

    if df.empty:
        return out

    # Skip filings the LLM marked as "no narrative" — they're noise.
    df = df[~df["outlook_summary"].fillna("").str.startswith(
        "No narrative outlook")].copy()
    if symbol:
        df = df[df["symbol"].str.upper() == symbol.upper()].copy()
    if df.empty:
        return out

    # Latest per symbol.
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df = (df.sort_values(["symbol", "filing_date"])
            .drop_duplicates(subset=["symbol"], keep="last"))

    # "Fresh this week" = filing_date within last 7 calendar days.
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=7)
    fresh = int((df["filing_date"] >= cutoff).sum())

    def _list(v: Any) -> list:
        # Parquet returns nested lists as numpy arrays; coerce safely.
        try:
            if v is None:
                return []
            if hasattr(v, "tolist"):
                return list(v.tolist())
            return list(v)
        except Exception:
            return []

    rows: list[dict] = []
    for _, r in df.head(top_k).iterrows():
        rows.append({
            "symbol": r["symbol"],
            "filing_date": (r["filing_date"].strftime("%Y-%m-%d")
                              if pd.notna(r["filing_date"]) else None),
            "doc_type": r["doc_type"],
            "fy_period": r.get("fy_period") or "",
            "outlook_summary": r["outlook_summary"],
            "outlook_tone": float(r["outlook_tone"] or 0.0),
            "growth_plans": _list(r.get("growth_plans")),
            "risks_mentioned": _list(r.get("risks_mentioned")),
            "guidance_strength": r.get("guidance_strength") or "LOW",
            "capex_announced": bool(r.get("capex_announced") or False),
            "expansion_announced":
                bool(r.get("expansion_announced") or False),
            "title": r.get("title") or "",
            "pdf_url": r.get("pdf_url") or "",
            "extracted_by_model": r.get("extracted_by_model") or "",
        })
    rows.sort(key=lambda x: x["filing_date"] or "", reverse=True)

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "rows": rows,
        "fresh_this_week": fresh,
        "summary": (
            f"{len(df)} symbols with cached management outlook; "
            f"{fresh} freshly filed in the last 7 days."
        ),
    }


def material_information_recent(symbol: str | None = None,
                                  days: int = 30,
                                  top_k: int = 30) -> dict:
    """Recent Material Information filings.

    Reads ``data/material_information.parquet`` (built by
    ``scripts/refresh_material_info.py``) and returns the most recent
    filings — optionally filtered to one symbol.

    Returns::

        {"as_of": "2026-04-27",
         "rows": [{symbol, date, title, doc_id, pdf_url}, ...],
         "summary": "8 material filings in the last 30 days; 2 today."}
    """
    p = PROJECT_ROOT / "data" / "material_information.parquet"
    out: dict = {"as_of": datetime.now().strftime("%Y-%m-%d"),
                  "rows": [], "summary": "No material information cached yet."}
    if not p.exists():
        return out
    try:
        import pandas as pd
        df = pd.read_parquet(p)
    except Exception as e:
        out["summary"] = f"material_information.parquet unreadable: {e}"
        return out
    if df.empty:
        return out

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=days)
    df = df[df["date"] >= cutoff]
    if symbol:
        df = df[df["symbol"].str.upper() == symbol.upper()]
    df = df.sort_values("date", ascending=False).head(top_k)

    today_date = pd.Timestamp.now().normalize()
    today_count = int((df["date"] == today_date).sum())

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "symbol": r["symbol"],
            "date": (r["date"].strftime("%Y-%m-%d")
                      if pd.notna(r["date"]) else None),
            "title": r.get("title") or "",
            "doc_id": str(r.get("doc_id") or ""),
            "pdf_url": r.get("pdf_url") or "",
        })
    out["rows"] = rows
    out["summary"] = (
        f"{len(df)} material filings in the last {days} days; "
        f"{today_count} today."
    )
    return out


def morning_brief() -> dict:
    """Aggregate every piece of context a trader wants before the open."""
    from ui.trade_journal import journal_stats

    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "regime": _safe(tools.get_market_regime),
        "strategy_signal": _safe(tools.get_strategy_signal),
        "overnight": _safe(tools.get_overnight_signals),
        "sentiment": _safe(tools.get_scored_sentiment),
        "portfolio": _safe(tools.get_user_portfolio),
        "journal_stats": _safe(journal_stats),
        "predictions": _safe(tools.get_todays_predictions, max_items=15),
        "prediction_accuracy": _safe(load_prediction_log_stats),
        "top_buys": _safe(tools.recommend_new_buys, max_ideas=5),
        "universe_movers": _safe(universe_movers),
        "universe_index": _safe(universe_index_history, days=60),
        "value_book": _safe(tools.get_universe_value_book),
        "quality_book": _safe(tools.get_universe_quality_book),
        "earnings_calendar": _safe(tools.get_earnings_calendar, days_ahead=21),
        "management_outlook": _safe(latest_management_outlook),
        "material_information": _safe(material_information_recent, days=14),
        "macro_impact": _safe(_macro_impact_today),
    }


def _macro_impact_today() -> dict:
    """Today's deterministic per-sector macro reading.

    Wraps :func:`brain.macro_impact.compute_macro_impact` so the Today
    tab can show a Macro Radar panel without recomputing on each rerun.
    Failure modes are absorbed by ``_safe`` in :func:`morning_brief`.
    """
    from brain.macro_impact import compute_macro_impact
    return compute_macro_impact()


def _latest_bar_date_in_dir(dir_path: Path) -> str | None:
    """Scan all OHLCV parquets and return the most recent bar date as
    'YYYY-MM-DD'. Used so the freshness panel can show what date the
    actual market data goes up to (not just when the file was written)."""
    try:
        import pandas as pd
        latest = None
        for f in dir_path.glob("*.parquet"):
            try:
                df = pd.read_parquet(f, columns=["date"])
                if df.empty:
                    continue
                d = pd.to_datetime(df["date"]).max()
                if latest is None or d > latest:
                    latest = d
            except Exception:
                continue
        return None if latest is None else str(latest.date())
    except Exception:
        return None


def _latest_date_in_parquet(p: Path, col: str = "date") -> str | None:
    """Return the latest value in `col` of a parquet file as 'YYYY-MM-DD'."""
    try:
        import pandas as pd
        df = pd.read_parquet(p, columns=[col])
        if df.empty:
            return None
        return str(pd.to_datetime(df[col]).max().date())
    except Exception:
        return None


def _latest_prediction_date(p: Path) -> str | None:
    """Newest prediction_id date in predictions_log.json (e.g. 2026-04-27)."""
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        ids = [x.get("prediction_id", "") for x in data.get("predictions", [])]
        ids = [s[:10] for s in ids if s and len(s) >= 10]
        return max(ids) if ids else None
    except Exception:
        return None


def data_freshness() -> dict[str, Any]:
    """Return both the file mtimes AND the latest data-point dates of
    the key on-disk artefacts.

    Two distinct concepts:
      - `updated_at` / `age_hours`: when the file was last written by
        the GitHub Actions workflows (or the local backfill button).
      - `latest_data_date`: the most recent trading day / event date
        actually inside the file. This is what tells you "is the data
        old?" — file mtime can be very recent (today's CI ran) while
        the data still lives on Friday's close.
    """
    files = {
        "OHLCV directory": PROJECT_ROOT / "data" / "ohlcv",
        "Overnight globals":
            PROJECT_ROOT / "data" / "macro" / "overnight_global.parquet",
        "Scored news":
            PROJECT_ROOT / "data" / "news" / "scored_news.parquet",
        "Predictions log":
            PROJECT_ROOT / "data" / "predictions_log.json",
        "FIPI flows":
            PROJECT_ROOT / "data" / "flows" / "fipi_daily.parquet",
        "Director's reports":
            PROJECT_ROOT / "data" / "results" / "reports.parquet",
        "Material information":
            PROJECT_ROOT / "data" / "material_information.parquet",
    }
    out: dict[str, Any] = {}
    now = datetime.now()
    for name, p in files.items():
        if not p.exists():
            out[name] = {"exists": False}
            continue
        if p.is_dir():
            ts = max((f.stat().st_mtime for f in p.rglob("*")
                       if f.is_file()), default=0.0)
        else:
            ts = p.stat().st_mtime
        dt = datetime.fromtimestamp(ts)
        age_h = (now - dt).total_seconds() / 3600

        # Compute the "latest data point" inside the file.
        latest_data_date: str | None = None
        if name == "OHLCV directory":
            latest_data_date = _latest_bar_date_in_dir(p)
        elif name == "Overnight globals":
            latest_data_date = (_latest_date_in_parquet(p, "date")
                                 or _latest_date_in_parquet(p, "as_of"))
        elif name == "Scored news":
            latest_data_date = (_latest_date_in_parquet(p, "published_at")
                                 or _latest_date_in_parquet(p, "scored_at"))
        elif name == "Predictions log":
            latest_data_date = _latest_prediction_date(p)
        elif name == "FIPI flows":
            latest_data_date = _latest_date_in_parquet(p, "date")
        elif name == "Director's reports":
            latest_data_date = _latest_date_in_parquet(p, "filing_date")
        elif name == "Material information":
            # The parquet stores the announcement date in `date`; fall
            # back to scrape time if the column is somehow missing.
            latest_data_date = (_latest_date_in_parquet(p, "date")
                                 or _latest_date_in_parquet(p, "scraped_at_utc"))

        # How fresh is the latest data point relative to today?
        days_behind: int | None = None
        trading_days_behind: int | None = None
        if latest_data_date:
            try:
                d_data = datetime.fromisoformat(latest_data_date).date()
                d_today = datetime.now().date()
                days_behind = (d_today - d_data).days
                # Trading-days = calendar days minus weekends in the gap.
                # Approximation good enough for retail dashboards (PSX is
                # closed Sat/Sun; public holidays are rare and separately
                # reported by the EOD workflow).
                if days_behind > 0:
                    weekends = 0
                    for off in range(1, days_behind + 1):
                        wd = (d_data + timedelta(days=off)).weekday()
                        if wd >= 5:  # 5=Sat, 6=Sun
                            weekends += 1
                    trading_days_behind = max(0, days_behind - weekends)
                else:
                    trading_days_behind = 0
            except Exception:
                pass

        # Convenience boolean: is the file fresh by trading-day standards?
        is_fresh = (
            trading_days_behind is not None
            and trading_days_behind <= 1
        )

        out[name] = {
            "exists": True,
            "updated_at": dt.strftime("%Y-%m-%d %H:%M"),
            "age_hours": round(age_h, 1),
            "latest_data_date": latest_data_date,
            "days_behind_today": days_behind,
            "trading_days_behind": trading_days_behind,
            "is_fresh": is_fresh,
        }
    return out


if __name__ == "__main__":
    from rich import print
    print(data_freshness())
