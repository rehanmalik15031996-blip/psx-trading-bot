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
from datetime import datetime
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
        "value_book": _safe(tools.get_universe_value_book),
        "quality_book": _safe(tools.get_universe_quality_book),
        "earnings_calendar": _safe(tools.get_earnings_calendar, days_ahead=21),
    }


def data_freshness() -> dict[str, Any]:
    """Return mtimes of the key data files so the UI can show when things
    were last updated by the GitHub Actions workflows."""
    files = {
        "OHLCV directory": PROJECT_ROOT / "data" / "ohlcv",
        "Overnight globals": PROJECT_ROOT / "data" / "macro" / "overnight_global.parquet",
        "Scored news": PROJECT_ROOT / "data" / "news" / "scored_news.parquet",
        "Predictions log": PROJECT_ROOT / "data" / "predictions_log.json",
        "FIPI flows": PROJECT_ROOT / "data" / "flows" / "fipi_daily.parquet",
    }
    out: dict[str, Any] = {}
    now = datetime.now()
    for name, p in files.items():
        if not p.exists():
            out[name] = {"exists": False}
            continue
        # For directories, use the latest mtime of any file inside.
        if p.is_dir():
            ts = max((f.stat().st_mtime for f in p.rglob("*") if f.is_file()),
                     default=0.0)
        else:
            ts = p.stat().st_mtime
        dt = datetime.fromtimestamp(ts)
        age_h = (now - dt).total_seconds() / 3600
        out[name] = {
            "exists": True,
            "updated_at": dt.strftime("%Y-%m-%d %H:%M"),
            "age_hours": round(age_h, 1),
        }
    return out


if __name__ == "__main__":
    from rich import print
    print(data_freshness())
