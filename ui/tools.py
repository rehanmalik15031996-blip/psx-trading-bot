"""Tool layer — the source of TRUTH for the LLM chatbot.

Every function in this module is exposed to Claude and Gemini as a callable
tool. The LLM can neither fabricate prices nor skip these calls: when the user
asks "what's MCB worth?", the model must call `get_price("MCB")` and read the
real number from our backend.

Each tool returns a JSON-serializable dict so it can be passed back to the LLM
regardless of provider. Each tool also has a JSON schema (see
`TOOL_SCHEMAS_ANTHROPIC` and `TOOL_SCHEMAS_GEMINI`) used for tool-calling.

Design notes:
  * Tools are READ-ONLY. The LLM never modifies the user's portfolio. The user
    confirms via UI buttons.
  * We prefer precise, compact dicts with units spelled out. LLMs do better
    with "price_pkr": 226.43 than with ambiguous "price": 226.43.
  * If a tool cannot answer (symbol unknown, no data), we return
    {"error": "..."} so the LLM can apologize honestly instead of hallucinating.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from brain.strategy import (
    StrategyConfig, build_prices_wide, compute_momentum, compute_realized_vol,
    pick_monthly,
)
from config.universe import symbols as universe_symbols, sector_of
from data.store import load_ohlcv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Cache: wide price frame is expensive to rebuild; keep for this process life.
# --------------------------------------------------------------------------
_WIDE_CACHE: dict[str, object] = {}


def _wide() -> pd.DataFrame:
    """Return the wide (date × symbol) close-price frame, cached."""
    if "wide" not in _WIDE_CACHE:
        _WIDE_CACHE["wide"] = build_prices_wide(universe_symbols())
    return _WIDE_CACHE["wide"]


def refresh_cache() -> None:
    """Force reload of the price cache on next access (e.g. after backfill)."""
    _WIDE_CACHE.clear()


def _latest(df: pd.DataFrame, col: str) -> tuple[pd.Timestamp, float] | tuple[None, None]:
    s = df[col].dropna()
    if s.empty:
        return None, None
    return s.index[-1], float(s.iloc[-1])


def _normalize_symbol(symbol: str) -> str:
    if not symbol:
        return ""
    return str(symbol).strip().upper()


# --------------------------------------------------------------------------
# TOOL 1: list universe
# --------------------------------------------------------------------------
def list_universe() -> dict:
    """Return the list of 15 symbols the bot trades + their sectors."""
    syms = universe_symbols()
    return {
        "count": len(syms),
        "symbols": [{"symbol": s, "sector": sector_of(s) or "Other"} for s in syms],
    }


# --------------------------------------------------------------------------
# TOOL 2: latest price + recent returns
# --------------------------------------------------------------------------
def get_price(symbol: str) -> dict:
    """Latest close price and recent returns for a ticker."""
    sym = _normalize_symbol(symbol)
    df = load_ohlcv(sym)
    if df.empty:
        return {"error": f"No price data for {sym}. Symbol must be one of the "
                         f"universe: {', '.join(universe_symbols())}"}
    df = df.sort_values("date")
    last_date = df["date"].iloc[-1]
    last_close = float(df["close"].iloc[-1])
    c = df["close"].astype(float)
    out = {
        "symbol": sym,
        "sector": sector_of(sym) or "Other",
        "as_of": str(pd.Timestamp(last_date).date()),
        "close_pkr": round(last_close, 2),
    }
    for d, name in [(1, "ret_1d"), (5, "ret_5d"), (21, "ret_21d"),
                    (63, "ret_63d"), (252, "ret_252d")]:
        if len(c) > d:
            prev = float(c.iloc[-d - 1])
            out[name] = round(last_close / prev - 1, 4)
    if "volume" in df.columns:
        v = df["volume"].astype(float)
        out["volume_last"] = int(v.iloc[-1])
        out["volume_20d_avg"] = int(v.tail(20).mean()) if len(v) >= 20 else None
    return out


# --------------------------------------------------------------------------
# TOOL 3: technical + momentum snapshot
# --------------------------------------------------------------------------
def get_technical_snapshot(symbol: str) -> dict:
    """Compact technical snapshot: momentum, volatility, trend flags."""
    sym = _normalize_symbol(symbol)
    df = load_ohlcv(sym)
    if df.empty:
        return {"error": f"No data for {sym}"}
    df = df.sort_values("date").set_index("date")
    df.index = pd.to_datetime(df.index)
    c = df["close"].astype(float)
    if len(c) < 200:
        return {"error": f"{sym}: need at least 200 bars, have {len(c)}"}

    last = float(c.iloc[-1])
    log_ret = np.log(c).diff()

    def _mom(w):
        return float(log_ret.rolling(w).sum().iloc[-1]) if len(c) > w else None

    def _sma(w):
        return float(c.rolling(w).mean().iloc[-1]) if len(c) > w else None

    hi20 = float(c.rolling(20).max().iloc[-1])
    lo20 = float(c.rolling(20).min().iloc[-1])
    hi52w = float(c.rolling(252).max().iloc[-1]) if len(c) >= 252 else hi20
    lo52w = float(c.rolling(252).min().iloc[-1]) if len(c) >= 252 else lo20

    rvol20 = float((log_ret.rolling(20).std() * np.sqrt(252)).iloc[-1])
    rvol60 = float((log_ret.rolling(60).std() * np.sqrt(252)).iloc[-1])

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi14 = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.empty else None

    sma20, sma50, sma200 = _sma(20), _sma(50), _sma(200)
    trend_up = bool(sma20 and sma50 and sma200 and (sma20 > sma50 > sma200))
    trend_down = bool(sma20 and sma50 and sma200 and (sma20 < sma50 < sma200))

    # ---------------- Bollinger Bands (analyst-requested) ----------------
    # %B in [0,1] = position between lower (0) and upper (1) band.
    # bb_width as % of mid is a squeeze/expansion gauge.
    bb_pctb = bb_width_pct = None
    bb_pctb_pctile = bb_width_pctile = None
    bb_state = None
    try:
        from ta.volatility import BollingerBands as _BB
        bb = _BB(close=c, window=20, window_dev=2)
        pctb_series = bb.bollinger_pband()
        wband_series = bb.bollinger_wband()
        if not pctb_series.empty:
            bb_pctb = float(pctb_series.iloc[-1])
        if not wband_series.empty:
            # ta returns wband already as a percent of mid: ((up-low)/mid)*100
            bb_width_pct = float(wband_series.iloc[-1])
        # 252-day percentile rank so the LLM gets context
        if pctb_series is not None and len(pctb_series.dropna()) >= 60:
            recent = pctb_series.tail(252).dropna()
            if len(recent) > 0 and bb_pctb is not None:
                bb_pctb_pctile = float((recent <= bb_pctb).mean()) * 100.0
        if wband_series is not None and len(wband_series.dropna()) >= 60:
            recent = wband_series.tail(252).dropna()
            if len(recent) > 0 and bb_width_pct is not None:
                bb_width_pctile = (
                    float((recent <= bb_width_pct).mean()) * 100.0
                )
        # Categorical state — analyst-friendly summary
        if bb_pctb is not None:
            if bb_pctb >= 0.95:
                bb_state = "near_upper_band"
            elif bb_pctb <= 0.05:
                bb_state = "near_lower_band"
            elif (bb_width_pctile is not None and bb_width_pctile <= 10.0):
                bb_state = "squeeze"
            else:
                bb_state = "neutral"
    except Exception:
        pass

    # ---------------- MACD ----------------
    macd_line = macd_signal_v = macd_hist = None
    macd_cross_days = None
    try:
        from ta.trend import MACD as _MACD
        m = _MACD(close=c, window_slow=26, window_fast=12, window_sign=9)
        ml = m.macd()
        ms = m.macd_signal()
        mh = m.macd_diff()
        if not ml.empty:
            macd_line = float(ml.iloc[-1])
        if not ms.empty:
            macd_signal_v = float(ms.iloc[-1])
        if not mh.empty:
            macd_hist = float(mh.iloc[-1])
        # Days since last sign-change in the histogram (cross detection)
        if mh is not None and len(mh.dropna()) > 5:
            sign = np.sign(mh.fillna(0.0))
            changes = sign.ne(sign.shift())
            last_change_idx = changes[changes].index
            if len(last_change_idx) > 0:
                macd_cross_days = int((mh.index[-1] - last_change_idx[-1]).days)
    except Exception:
        pass

    # ---------------- OBV (volume confirmation) ----------------
    obv_5d_change_pct = None
    try:
        if "volume" in df.columns:
            from ta.volume import OnBalanceVolumeIndicator as _OBV
            v = df["volume"].astype(float)
            obv_series = _OBV(close=c, volume=v).on_balance_volume()
            if obv_series is not None and len(obv_series.dropna()) > 6:
                cur = float(obv_series.iloc[-1])
                prev = float(obv_series.iloc[-6])
                if prev != 0:
                    obv_5d_change_pct = (cur / prev - 1.0) * 100.0
    except Exception:
        pass

    # ---------------- Stochastic RSI ----------------
    stoch_rsi = None
    try:
        from ta.momentum import StochRSIIndicator as _SR
        srsi = _SR(close=c, window=14, smooth1=3, smooth2=3).stochrsi()
        if srsi is not None and not srsi.empty:
            stoch_rsi = float(srsi.iloc[-1])
    except Exception:
        pass

    return {
        "symbol": sym,
        "as_of": str(c.index[-1].date()),
        "close_pkr": round(last, 2),
        "momentum": {
            "20d_log_ret": round(_mom(20), 4) if _mom(20) is not None else None,
            "60d_log_ret": round(_mom(60), 4) if _mom(60) is not None else None,
            "150d_log_ret": round(_mom(150), 4) if _mom(150) is not None else None,
            "250d_log_ret": round(_mom(250), 4) if _mom(250) is not None else None,
        },
        "moving_averages": {
            "sma_20": round(sma20, 2) if sma20 else None,
            "sma_50": round(sma50, 2) if sma50 else None,
            "sma_200": round(sma200, 2) if sma200 else None,
            "px_vs_sma50_pct": round((last / sma50 - 1) * 100, 2) if sma50 else None,
            "px_vs_sma200_pct": round((last / sma200 - 1) * 100, 2) if sma200 else None,
        },
        "ranges": {
            "high_20d": round(hi20, 2),
            "low_20d": round(lo20, 2),
            "high_52w": round(hi52w, 2),
            "low_52w": round(lo52w, 2),
            "dist_from_52w_high_pct": round((last / hi52w - 1) * 100, 2),
            "dist_from_52w_low_pct": round((last / lo52w - 1) * 100, 2),
        },
        "volatility": {
            "rvol_20d_ann": round(rvol20, 4),
            "rvol_60d_ann": round(rvol60, 4),
            "rvol_regime": "rising" if rvol20 > rvol60 * 1.2 else
                           "falling" if rvol20 < rvol60 * 0.8 else "stable",
        },
        "rsi_14": round(rsi14, 1) if rsi14 is not None else None,
        "trend": "up" if trend_up else "down" if trend_down else "mixed",
        # New indicator block — surfaced to the LLM via the briefing
        "bollinger": {
            "pctb": round(bb_pctb, 3) if bb_pctb is not None else None,
            "pctb_pctile_252d": (round(bb_pctb_pctile, 0)
                                 if bb_pctb_pctile is not None else None),
            "width_pct": (round(bb_width_pct, 2)
                          if bb_width_pct is not None else None),
            "width_pctile_252d": (round(bb_width_pctile, 0)
                                  if bb_width_pctile is not None else None),
            "state": bb_state,
        },
        "macd": {
            "line": round(macd_line, 3) if macd_line is not None else None,
            "signal": (round(macd_signal_v, 3)
                       if macd_signal_v is not None else None),
            "histogram": round(macd_hist, 3) if macd_hist is not None else None,
            "days_since_cross": macd_cross_days,
        },
        "obv": {
            "change_5d_pct": (round(obv_5d_change_pct, 1)
                              if obv_5d_change_pct is not None else None),
        },
        "stoch_rsi": (round(stoch_rsi, 3)
                      if stoch_rsi is not None else None),
    }


# --------------------------------------------------------------------------
# TOOL 4: universe ranking (Phase 1 view)
# --------------------------------------------------------------------------
def get_universe_ranking() -> dict:
    """Phase 1 ranking of all 15 stocks by 150d momentum, with vol filter status."""
    cfg = StrategyConfig()
    wide = _wide()
    if wide.empty:
        return {"error": "No price data loaded"}
    as_of = wide.index[-1]
    mom = compute_momentum(wide, cfg.momentum_window).loc[as_of]
    vol = compute_realized_vol(wide, cfg.vol_window).loc[as_of]
    vol_rank = vol.rank(pct=True)
    ranked = mom.sort_values(ascending=False)
    rows = []
    for i, (sym, score) in enumerate(ranked.dropna().items(), 1):
        rows.append({
            "rank": i,
            "symbol": sym,
            "sector": sector_of(sym) or "Other",
            "mom_150d_log_ret": round(float(score), 4),
            "rvol_20d_ann": round(float(vol[sym]), 4) if pd.notna(vol[sym]) else None,
            "vol_percentile": round(float(vol_rank[sym]) * 100, 1)
                if pd.notna(vol_rank[sym]) else None,
            "passes_vol_filter": bool(pd.notna(vol_rank[sym])
                                      and vol_rank[sym] <= cfg.vol_rank_cap),
        })
    return {
        "as_of": str(as_of.date()),
        "momentum_window_days": cfg.momentum_window,
        "vol_window_days": cfg.vol_window,
        "vol_filter_cap_percentile": int(cfg.vol_rank_cap * 100),
        "ranking": rows,
    }


# --------------------------------------------------------------------------
# TOOL 5: current strategy recommendation
# --------------------------------------------------------------------------
def get_strategy_signal() -> dict:
    """What would the Phase 1 rule pick if we rebalanced today?"""
    cfg = StrategyConfig()
    wide = _wide()
    if wide.empty:
        return {"error": "No price data loaded"}
    as_of = wide.index[-1]
    pick = pick_monthly(wide, as_of, cfg)
    no_gate = pick_monthly(wide, as_of, StrategyConfig(market_filter_on=False))
    return {
        "as_of": str(as_of.date()),
        "market_risk_on": bool(pick.market_risk_on),
        "recommended_action": "HOLD CASH" if not pick.selected else f"HOLD TOP-{cfg.top_n}",
        "selected_symbols": pick.selected,
        "would_pick_if_market_filter_off": no_gate.selected,
        "top_n": cfg.top_n,
        "rationale": pick.reason,
        "note": ("This is the Phase 1 rule evaluated at today's close. The "
                 "actual live strategy only rebalances on the last trading day "
                 "of each month; this is a 'what would it say today' view."),
    }


# --------------------------------------------------------------------------
# TOOL 6: market regime
# --------------------------------------------------------------------------
def get_market_regime() -> dict:
    """Current market-wide regime indicators (uses rule-based classifier)."""
    from brain.overlay import _rule_based_regime

    wide = _wide()
    if wide.empty:
        return {"error": "No price data loaded"}
    log_ret = np.log(wide).diff()
    u5 = float(log_ret.tail(5).sum().mean()) if len(wide) >= 5 else None
    u21 = float(log_ret.tail(21).sum().mean()) if len(wide) >= 21 else None
    uni_mom_150d = float(log_ret.tail(150).sum().mean()) if len(wide) >= 150 else None
    breadth = float((wide.iloc[-1] / wide.iloc[-2] - 1).gt(0).mean()) if len(wide) >= 2 else None

    decision = _rule_based_regime(
        macro={},
        universe_5d_change=u5,
    )
    return {
        "as_of": str(wide.index[-1].date()),
        "regime": decision.regime,
        "exposure_multiplier": decision.multiplier,
        "reason": decision.reason,
        "flags": decision.flags,
        "indicators": {
            "universe_ret_5d": round(u5, 4) if u5 is not None else None,
            "universe_ret_21d": round(u21, 4) if u21 is not None else None,
            "universe_150d_log_ret": round(uni_mom_150d, 4)
                if uni_mom_150d is not None else None,
            "breadth_pct_up_today": round(breadth * 100, 1)
                if breadth is not None else None,
        },
        "note": ("Regime classification uses rule-based fallback. Connect an "
                 "Anthropic API key to enable Claude-powered regime detection."),
    }


# --------------------------------------------------------------------------
# TOOL 7: analyze a specific position
# --------------------------------------------------------------------------
def analyze_position(symbol: str, entry_price: float,
                     entry_date: str | None = None,
                     quantity: float | None = None) -> dict:
    """Analyze a SINGLE position: P&L, stop, signal, suggested action.

    entry_date optional; if omitted we assume 'unknown' and skip time-based stats.
    quantity optional; if provided we compute cash P&L in PKR.
    """
    sym = _normalize_symbol(symbol)
    df = load_ohlcv(sym)
    if df.empty:
        return {"error": f"Unknown symbol {sym}"}
    df = df.sort_values("date").set_index("date")
    df.index = pd.to_datetime(df.index)
    last_date = df.index[-1]
    last_close = float(df["close"].iloc[-1])

    ent_px = float(entry_price)
    ret_pct = last_close / ent_px - 1

    days_held = None
    peak_since_entry = last_close
    if entry_date:
        try:
            ent_dt = pd.to_datetime(entry_date)
            held_bars = df[df.index >= ent_dt]
            if not held_bars.empty:
                days_held = (last_date - held_bars.index[0]).days
                peak_since_entry = float(held_bars["close"].max())
        except Exception:
            pass

    # Suggested trailing-stop level (12% below peak since entry)
    trail_stop_pct = 0.12
    suggested_stop = peak_since_entry * (1 - trail_stop_pct)

    # Ask the Phase 1 rule whether this symbol is still a valid hold
    ranking = get_universe_ranking()
    row = next((r for r in ranking.get("ranking", []) if r["symbol"] == sym), None)
    signal_view = get_strategy_signal()

    in_watchlist_top5 = sym in signal_view.get("selected_symbols", [])
    in_would_be_top5 = sym in signal_view.get("would_pick_if_market_filter_off", [])
    market_risk_on = signal_view.get("market_risk_on", True)

    # Suggested action synthesis
    if last_close <= suggested_stop:
        action = "SELL — trailing stop hit"
        reason = (f"Price {last_close:.2f} <= trailing stop {suggested_stop:.2f} "
                  f"(-{trail_stop_pct*100:.0f}% from peak {peak_since_entry:.2f})")
    elif in_watchlist_top5:
        action = "HOLD"
        reason = (f"{sym} is in today's Phase 1 top-{signal_view.get('top_n', 5)}; "
                  f"signal still valid.")
    elif in_would_be_top5:
        action = "HOLD (cautious)"
        reason = (f"{sym} is in the top-{signal_view.get('top_n', 5)} by momentum "
                  f"but the market filter is off (universe 150d mom negative). "
                  f"Strategy defaults to cash this month.")
    elif not market_risk_on:
        action = "CONSIDER TRIM"
        reason = (f"Market filter is off and {sym} is not in the would-be "
                  f"top-{signal_view.get('top_n', 5)} even with the gate removed.")
    else:
        action = "SELL — signal decay"
        reason = (f"{sym} has dropped below the Phase 1 top-{signal_view.get('top_n', 5)}; "
                  f"momentum is no longer in the leaders.")

    out = {
        "symbol": sym,
        "as_of": str(last_date.date()),
        "entry_price_pkr": round(ent_px, 2),
        "current_price_pkr": round(last_close, 2),
        "unrealized_return_pct": round(ret_pct * 100, 2),
        "peak_since_entry_pkr": round(peak_since_entry, 2),
        "drawdown_from_peak_pct": round((last_close / peak_since_entry - 1) * 100, 2),
        "suggested_trailing_stop_pkr": round(suggested_stop, 2),
        "suggested_trailing_stop_pct": int(trail_stop_pct * 100),
        "days_held": days_held,
        "in_current_top5": in_watchlist_top5,
        "in_momentum_top5_ignoring_market_filter": in_would_be_top5,
        "market_risk_on_today": market_risk_on,
        "momentum_rank": row["rank"] if row else None,
        "passes_vol_filter": row["passes_vol_filter"] if row else None,
        "suggested_action": action,
        "reasoning": reason,
    }
    if quantity is not None:
        out["quantity"] = float(quantity)
        out["cost_basis_pkr"] = round(ent_px * float(quantity), 2)
        out["market_value_pkr"] = round(last_close * float(quantity), 2)
        out["unrealized_pnl_pkr"] = round((last_close - ent_px) * float(quantity), 2)
    return out


# --------------------------------------------------------------------------
# TOOL 8: user portfolio overview (reads from ui.portfolio)
# --------------------------------------------------------------------------
def get_user_portfolio() -> dict:
    """Return all positions the user has entered, with live P&L."""
    from ui.portfolio import load_user_portfolio
    positions = load_user_portfolio()
    if not positions:
        return {"positions": [], "total_cost_pkr": 0.0,
                "total_market_value_pkr": 0.0,
                "total_unrealized_pnl_pkr": 0.0,
                "total_unrealized_pnl_pct": 0.0,
                "note": "Portfolio is empty. Add positions from the Portfolio tab."}
    total_cost, total_mv = 0.0, 0.0
    rows = []
    for p in positions:
        sym = _normalize_symbol(p["symbol"])
        df = load_ohlcv(sym)
        if df.empty:
            rows.append({"symbol": sym, "error": "no price data"})
            continue
        last_close = float(df.sort_values("date")["close"].iloc[-1])
        qty = float(p.get("quantity", 0))
        ent = float(p["entry_price"])
        cost = ent * qty
        mv = last_close * qty
        total_cost += cost
        total_mv += mv
        rows.append({
            "symbol": sym,
            "sector": sector_of(sym) or "Other",
            "entry_date": p.get("entry_date"),
            "entry_price_pkr": round(ent, 2),
            "quantity": qty,
            "cost_pkr": round(cost, 2),
            "current_price_pkr": round(last_close, 2),
            "market_value_pkr": round(mv, 2),
            "unrealized_pnl_pkr": round(mv - cost, 2),
            "unrealized_return_pct": round((last_close / ent - 1) * 100, 2) if ent else 0,
        })
    pnl = total_mv - total_cost
    return {
        "as_of": str(_wide().index[-1].date()) if not _wide().empty else None,
        "position_count": len(positions),
        "total_cost_pkr": round(total_cost, 2),
        "total_market_value_pkr": round(total_mv, 2),
        "total_unrealized_pnl_pkr": round(pnl, 2),
        "total_unrealized_pnl_pct": round((pnl / total_cost) * 100, 2)
            if total_cost > 0 else 0,
        "positions": rows,
    }


# --------------------------------------------------------------------------
# TOOL 9: recommend new buys
# --------------------------------------------------------------------------
def recommend_new_buys(max_ideas: int = 5,
                          *, with_rationale: bool = True) -> dict:
    """Today's BUY candidates: top-N by Phase 1 rule + reasons.

    When ``with_rationale=True`` (default) every idea is enriched with
    a structured explanation block from
    :func:`brain.buy_explainer.explain_buy` — drivers, risks, "why
    now", confidence percentage, and a trade plan. The Find Ideas tab
    renders this as expandable cards instead of a bare table.

    Setting ``with_rationale=False`` returns the lightweight dict
    used by the chatbot tool schema (the LLM doesn't need the full
    rationale; it can call ``explain_buy`` itself when asked).
    """
    ranking = get_universe_ranking()
    signal = get_strategy_signal()
    if "error" in ranking:
        return ranking
    top_n = max(1, min(int(max_ideas), 10))
    # If market filter is off, surface would-be picks but mark them cautious.
    cautious = not signal.get("market_risk_on", True)
    pool = (signal.get("selected_symbols")
            or signal.get("would_pick_if_market_filter_off") or [])[:top_n]
    top5_set = set(signal.get("selected_symbols") or [])

    # Shared inputs (one fetch per request — saves repeated FIPI scrapes)
    macro_impact = None
    fipi = None
    scored_news_df = None
    if with_rationale:
        try:
            macro_impact = get_macro_impact_today()
        except Exception:
            macro_impact = None
        try:
            fipi = get_fipi_flows()
        except Exception:
            fipi = None
        try:
            from ui.news_sentiment import load_scored_news
            scored_news_df = load_scored_news(max_age_hours=24 * 7)
        except Exception:
            scored_news_df = None

    # Today's predictions (already-computed forecast + entry/stop/target)
    todays_pred_index: dict[str, dict] = {}
    if with_rationale:
        try:
            tp = get_todays_predictions(max_items=50)
            for row in (tp.get("predictions") or []):
                todays_pred_index[row.get("symbol")] = row
        except Exception:
            pass

    ideas = []
    for sym in pool:
        s = get_technical_snapshot(sym)
        pr = get_price(sym)
        idea: dict = {
            "symbol": sym,
            "sector": sector_of(sym) or "Other",
            "close_pkr": pr.get("close_pkr"),
            "mom_150d_log_ret": s.get("momentum", {}).get("150d_log_ret"),
            "rvol_20d_ann": s.get("volatility", {}).get("rvol_20d_ann"),
            "rsi_14": s.get("rsi_14"),
            "dist_from_52w_high_pct":
                s.get("ranges", {}).get("dist_from_52w_high_pct"),
            "trend": s.get("trend"),
        }

        if with_rationale:
            try:
                from brain.buy_explainer import explain_buy as _explain_buy
                # Per-symbol news aggregate from the scored-news cache
                news: dict | None = None
                if scored_news_df is not None and not scored_news_df.empty:
                    try:
                        sub = scored_news_df
                        if "affected_symbols" in sub.columns:
                            sub = sub[sub["affected_symbols"].apply(
                                lambda x: sym in (x or []) if x is not None
                                else False)]
                        if len(sub) and "score" in sub.columns:
                            top_idx = sub["score"].abs().idxmax()
                            news = {
                                "n_articles": int(len(sub)),
                                "aggregate_score":
                                    float(sub["score"].mean()),
                                "top_headline":
                                    str(sub.loc[top_idx, "title"])
                                    if "title" in sub.columns else "",
                            }
                    except Exception:
                        news = None
                # Management outlook (one-row latest filing)
                try:
                    from ui import dashboard_data as _dash
                    mgmt = _dash.latest_management_outlook(symbol=sym) or {}
                except Exception:
                    mgmt = None

                fcst = todays_pred_index.get(sym, {})
                rationale = _explain_buy(
                    sym,
                    technical_snapshot=s,
                    macro_impact=macro_impact,
                    news=news,
                    fipi=fipi,
                    management_outlook=mgmt,
                    in_phase1_top5=sym in top5_set,
                    direction=fcst.get("direction") or "BULLISH",
                    conviction=fcst.get("conviction") or "MEDIUM",
                    suggested_action=(fcst.get("suggested_action")
                                       or "BUY"),
                    price_pkr=pr.get("close_pkr"),
                    sector=idea["sector"],
                    forecast_dict=fcst,
                )
                idea["rationale"] = rationale
            except Exception as e:
                idea["rationale"] = {
                    "symbol": sym,
                    "verdict": "BUY",
                    "headline": (
                        f"BUY (no rationale block — "
                        f"{type(e).__name__}: {e})"
                    ),
                    "thesis": "",
                    "key_drivers": [],
                    "key_risks": [],
                    "confidence_pct": 50,
                }
        ideas.append(idea)

    return {
        "as_of": ranking["as_of"],
        "market_risk_on": not cautious,
        "cautious_note": ("Market filter is OFF. The Phase 1 rule recommends "
                          "HOLD CASH. These are the names that would be picked "
                          "if the filter were disabled — treat as speculative.")
            if cautious else None,
        "ideas": ideas,
    }


# --------------------------------------------------------------------------
# Live-data tools: FIPI flows, news, macro, policy rate
# These wrap the existing connectors/ modules. Each call is cached per-process
# so chat turns don't hammer the RSS endpoints.
# --------------------------------------------------------------------------
_LIVE_CACHE: dict[str, tuple[float, Any]] = {}
_LIVE_TTL_SECONDS = 15 * 60  # 15 minutes


def _cached(key: str, fn):
    """Small TTL cache around an expensive live fetch."""
    import time
    now = time.time()
    if key in _LIVE_CACHE:
        ts, val = _LIVE_CACHE[key]
        if now - ts < _LIVE_TTL_SECONDS:
            return val
    val = fn()
    _LIVE_CACHE[key] = (now, val)
    return val


# --------------------------------------------------------------------------
# TOOL: FIPI / LIPI daily flows
# --------------------------------------------------------------------------
# Categories the analyst flagged as "big fish" — institutional money
# whose flow direction is the most informative single signal in PSX.
# Names are the SCStrade-normalised forms returned by
# ``SCStradeFIPIConnector._normalize_category``.
_BIG_FISH_CATEGORIES = {
    "Foreign", "Foreign Corporate", "Foreign Individual",
    "Banks / DFI", "Banks", "Mutual Funds", "Mutual Fund",
    "Insurance", "Insurance Companies",
}


def get_fipi_flows() -> dict:
    """Today's foreign vs local net flows on PSX (from SCStrade).

    Foreign > 0 means net foreign BUY (bullish); < 0 means net foreign SELL
    (risk-off signal the knowledge base calls the 'single most useful daily
    sentiment indicator').

    Output also includes the analyst-requested ``big_fish`` aggregate
    (foreign + banks + mutual funds + insurance) which is the
    institutional cohort — the cohort that drives multi-day moves.
    """
    def _run():
        try:
            from connectors.flows import SCStradeFIPIConnector
            r = SCStradeFIPIConnector().fetch()
            if not r.ok:
                return {"error": r.error or "FIPI fetch failed",
                        "source": "scstrade.com"}
            extras = r.extras or {}
            # Top 5 sectors by absolute net flow
            sectors = sorted(
                extras.get("sectors", []),
                key=lambda s: abs(s.get("net_usd_mn", 0)),
                reverse=True,
            )[:8]

            # Big-fish breakdown — analyst-flagged: foreign + banks +
            # mutual funds + insurance is the cohort that actually moves
            # PSX over multi-day windows. Individuals and brokers chase.
            participants = r.records or []
            big_fish_components: list[dict] = []
            big_fish_net = 0.0
            retail_net = 0.0
            for p in participants:
                cat = (p.get("category") or "").strip()
                net = float(p.get("net_pkr_mn") or 0.0)
                if cat in _BIG_FISH_CATEGORIES:
                    big_fish_components.append({
                        "category": cat,
                        "buy_pkr_mn": p.get("buy_pkr_mn"),
                        "sell_pkr_mn": p.get("sell_pkr_mn"),
                        "net_pkr_mn": net,
                    })
                    big_fish_net += net
                elif cat:
                    retail_net += net
            # Sort big_fish by absolute size for display
            big_fish_components.sort(
                key=lambda x: abs(x.get("net_pkr_mn") or 0.0),
                reverse=True,
            )
            big_fish_regime = (
                "institutional_buying" if big_fish_net > 0
                else "institutional_selling" if big_fish_net < 0
                else "neutral"
            )

            return {
                "as_of": extras.get("report_date"),
                "foreign_net_pkr_mn": extras.get("foreign_net_pkr_mn"),
                "local_net_pkr_mn": extras.get("local_net_pkr_mn"),
                "foreign_regime": ("net_buying"
                                   if (extras.get("foreign_net_pkr_mn") or 0) > 0
                                   else "net_selling"),
                "big_fish_net_pkr_mn": round(big_fish_net, 2),
                "big_fish_regime": big_fish_regime,
                "big_fish_components": big_fish_components,
                "retail_net_pkr_mn": round(retail_net, 2),
                "participants": participants,
                "top_sectors_by_flow": sectors,
                "source": "scstrade.com",
            }
        except Exception as e:
            return {"error": f"FIPI fetch raised: {type(e).__name__}: {e}"}

    return _cached("fipi", _run)


def get_sector_volume_heatmap(top_k: int = 5,
                                lookback_days: int = 20) -> dict:
    """Top sectors by today's traded value vs their ``lookback_days``
    average — used as a "where's the action" heatmap on the Today tab
    and inside the briefing.

    For each sector in the universe we sum (close * volume) per stock,
    compare today's total with the trailing average, and flag any
    sector running > 2× its average (institutional rotation).
    """
    try:
        from data.store import load_ohlcv
        from config.universe import UNIVERSE
        rows: dict[str, dict] = {}
        for ent in UNIVERSE:
            try:
                df = load_ohlcv(ent.symbol)
            except Exception:
                continue
            if df is None or df.empty or "volume" not in df.columns:
                continue
            df = df.tail(lookback_days + 5).copy()
            df["traded_value"] = (df["close"].astype(float)
                                  * df["volume"].astype(float))
            today_val = float(df["traded_value"].iloc[-1])
            avg_val = float(df["traded_value"].iloc[:-1]
                              .tail(lookback_days).mean())
            blk = rows.setdefault(ent.sector,
                                  {"sector": ent.sector,
                                   "today_pkr_mn": 0.0,
                                   "avg_pkr_mn": 0.0,
                                   "members": []})
            blk["today_pkr_mn"] += today_val
            blk["avg_pkr_mn"] += avg_val
            blk["members"].append(ent.symbol)
        out: list[dict] = []
        for s, blk in rows.items():
            today_mn = blk["today_pkr_mn"] / 1e6
            avg_mn = blk["avg_pkr_mn"] / 1e6
            ratio = (today_mn / avg_mn) if avg_mn > 0 else None
            out.append({
                "sector": s,
                "today_pkr_mn": round(today_mn, 1),
                "avg_pkr_mn": round(avg_mn, 1),
                "ratio_vs_avg": (round(ratio, 2)
                                  if ratio is not None else None),
                "is_hot": bool(ratio is not None and ratio >= 2.0),
                "members": blk["members"],
            })
        out.sort(key=lambda x: x["today_pkr_mn"], reverse=True)
        return {
            "lookback_days": lookback_days,
            "top": out[: top_k],
            "all": out,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# --------------------------------------------------------------------------
# TOOL: SBP policy rate + yield curve
# --------------------------------------------------------------------------
def get_policy_rate() -> dict:
    """Current SBP policy rate, corridor, and yield curve (KIBOR / T-Bill / PIB)."""
    def _run():
        try:
            from connectors.sbp import SBPPolicyRateConnector
            r = SBPPolicyRateConnector().fetch()
            if not r.ok or not r.records:
                return {"error": r.error or "SBP fetch failed"}
            snap = r.records[0]
            return {
                "as_of": snap.get("as_on"),
                "policy_rate_pct": snap.get("policy_rate_pct"),
                "corridor": {"floor": snap.get("floor_rate_pct"),
                             "ceiling": snap.get("ceiling_rate_pct")},
                "kibor": snap.get("kibor"),
                "tbill_yields_pct": snap.get("tbill_yields_pct"),
                "pib_yields_pct": snap.get("pib_yields_pct"),
                "reserves_usd_mn": snap.get("reserves_usd_mn"),
                "interpretation": _interpret_rate(snap.get("policy_rate_pct")),
                "source": "sbp.org.pk",
            }
        except Exception as e:
            return {"error": f"SBP fetch raised: {type(e).__name__}: {e}"}

    return _cached("sbp_rate", _run)


def _interpret_rate(rate: float | None) -> str:
    if rate is None:
        return ""
    if rate <= 11:
        return ("Policy rate is accommodative (<=11%). Bullish for banking, "
                "cement, autos. Historically associated with equity rallies.")
    if rate <= 16:
        return ("Policy rate is neutral/tightening (11-16%). Mixed for "
                "equities; watch inflation and growth prints.")
    return ("Policy rate is restrictive (>16%). Historically weighs on "
            "equities ex-banks; high discount rate on future earnings.")


# --------------------------------------------------------------------------
# TOOL: Macro snapshot (PKR, commodities, reserves)
# --------------------------------------------------------------------------
def get_macro_snapshot() -> dict:
    """Latest value + 5d/21d change for USD/PKR, Brent, WTI, Gold, BTC."""
    import time as _t
    key_map = {
        "usdpkr": "USD/PKR",
        "brent": "Brent (USD/bbl)",
        "wti": "WTI (USD/bbl)",
        "gold": "Gold (USD/oz)",
        "btc": "Bitcoin (USD)",
        "copper": "Copper (USD/lb)",
        "cotton": "Cotton (USD/lb)",
    }
    out = {"as_of": None, "indicators": {}}
    for key, label in key_map.items():
        path = PROJECT_ROOT / "data" / "macro" / f"{key}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path).sort_values("date")
            if df.empty:
                continue
            # macro parquets use 'value'; OHLCV parquets use 'close' — support both.
            col = "close" if "close" in df.columns else (
                "value" if "value" in df.columns else None)
            if col is None:
                continue
            last = df.iloc[-1]
            last_val = float(last[col])
            last_date = str(pd.Timestamp(last["date"]).date())
            out["as_of"] = last_date if out["as_of"] is None else out["as_of"]

            def _ret(n: int, s=df[col]) -> float | None:
                if len(s) > n:
                    prev = float(s.iloc[-n - 1])
                    if prev:
                        return round(last_val / prev - 1, 4)
                return None

            out["indicators"][key] = {
                "label": label,
                "value": round(last_val, 2 if key != "btc" else 0),
                "as_of": last_date,
                "ret_5d": _ret(5),
                "ret_21d": _ret(21),
                "ret_63d": _ret(63),
            }
        except Exception as e:
            out["indicators"][key] = {"error": f"{type(e).__name__}: {e}"}

    # Narrative interpretation
    pkr = out["indicators"].get("usdpkr", {})
    brent = out["indicators"].get("brent", {})
    lines = []
    if pkr.get("ret_21d") is not None:
        if pkr["ret_21d"] > 0.02:
            lines.append(f"PKR weakened {pkr['ret_21d']*100:+.1f}% vs USD in "
                         f"last 21d (currency stress — hurts importers, FIPI).")
        elif pkr["ret_21d"] < -0.01:
            lines.append(f"PKR strengthened {abs(pkr['ret_21d'])*100:.1f}% "
                         f"in 21d (bullish for importers, risk-on).")
    if brent.get("ret_21d") is not None:
        if brent["ret_21d"] > 0.10:
            lines.append(f"Brent up {brent['ret_21d']*100:+.1f}% in 21d — "
                         f"bullish for E&P (OGDC/PPL/POL/MARI), bearish for "
                         f"OMCs, power, autos, currency, inflation.")
        elif brent["ret_21d"] < -0.05:
            lines.append(f"Brent down {brent['ret_21d']*100:+.1f}% in 21d — "
                         f"bearish for E&P, tailwind for inflation and PKR.")
    out["narrative"] = " ".join(lines) if lines else "Macro environment stable."
    return out


# --------------------------------------------------------------------------
# TOOL: Recent news (market-wide or filtered to a symbol / sector)
# --------------------------------------------------------------------------
# Simple keyword map: ticker → patterns that would appear in headlines.
NEWS_KEYWORDS: dict[str, list[str]] = {
    "HUBC": ["HUBCO", "Hub Power", "Hubco"],
    "PABC": ["Pak Arab", "PABC", "refinery"],
    "MLCF": ["Maple Leaf", "MLCF", "cement"],
    "OGDC": ["OGDC", "oil and gas development", "Oil & Gas Dev"],
    "FABL": ["Faysal", "FABL"],
    "PPL": ["Pakistan Petroleum", "PPL"],
    "POL": ["Pakistan Oilfields", "POL"],
    "APL": ["Attock Petroleum", "APL"],
    "MCB": ["MCB Bank", "MCB "],
    "MEBL": ["Meezan", "MEBL"],
    "PSO": ["PSO", "Pakistan State Oil"],
    "KOHC": ["Kohat Cement", "KOHC"],
    "FCCL": ["Fauji Cement", "FCCL"],
    "EPCL": ["Engro Polymer", "EPCL"],
    "SEARL": ["Searle", "SEARL"],
    "NPL": ["Nishat Power", "NPL ", "Nishat Mills Power"],
}

# Sector-level keyword groups so we can attribute news to sector flows
SECTOR_KEYWORDS = {
    "Banking": ["bank", "banking", "SBP", "KIBOR", "deposit", "lending"],
    "Oil & Gas E&P": ["oil", "gas", "crude", "brent", "OPEC", "E&P",
                      "exploration", "petroleum"],
    "Cement": ["cement", "construction", "coal", "clinker"],
    "Power": ["power", "electricity", "IPP", "circular debt", "NEPRA"],
    "OMC/Refining": ["refinery", "OMC", "fuel", "margin", "motor spirit"],
    "Pharma": ["pharma", "drug", "DRAP"],
}


def get_recent_news(symbol: str | None = None, limit: int = 10) -> dict:
    """Latest RSS news (market-wide or matched to a symbol via keywords)."""
    def _pull():
        try:
            from connectors.rss_news import RssNewsConnector
            r = RssNewsConnector().fetch(per_feed=5)
            if not r.ok:
                return {"articles": [], "error": r.error or "news fetch failed"}
            return {"articles": r.records}
        except Exception as e:
            return {"articles": [],
                    "error": f"news fetch raised: {type(e).__name__}: {e}"}

    data = _cached("news_raw", _pull)
    articles = list(data.get("articles", []))

    filtered = articles
    matched_by = "all"
    if symbol:
        sym = _normalize_symbol(symbol)
        kw = NEWS_KEYWORDS.get(sym, [sym])
        sector = sector_of(sym) or ""
        sec_kw = SECTOR_KEYWORDS.get(sector, [])
        patterns = [k.lower() for k in kw + sec_kw]
        def _match(rec):
            t = ((rec.get("title") or "") + " " + (rec.get("summary") or "")).lower()
            return any(p in t for p in patterns)
        sym_hits = [a for a in articles if _match(a)]
        filtered = sym_hits if sym_hits else articles[:limit]
        matched_by = f"symbol/sector keywords ({sym})"

    filtered = filtered[:limit]
    return {
        "query": symbol or "market-wide",
        "matched_by": matched_by,
        "count": len(filtered),
        "articles": [
            {"source": a.get("source", ""),
             "title": a.get("title", ""),
             "published_at": a.get("published_at"),
             "summary": (a.get("summary") or "")[:220]}
            for a in filtered
        ],
        "error": data.get("error"),
    }


# --------------------------------------------------------------------------
# TOOL: Full context bundle for a symbol — every data layer in one call
# --------------------------------------------------------------------------
def get_full_context(symbol: str) -> dict:
    """One-shot 'everything you'd want to know' for a ticker.

    Assembles: price snapshot + technical snapshot + current momentum rank +
    symbol-matched news + FIPI flows + macro snapshot + policy rate +
    current Phase 1 signal. Use this when generating a prediction so the
    LLM sees the full picture in a single tool call.
    """
    sym = _normalize_symbol(symbol)
    price = get_price(sym)
    tech = get_technical_snapshot(sym)
    ranking = get_universe_ranking()
    row = None
    if "ranking" in ranking:
        row = next((r for r in ranking["ranking"] if r["symbol"] == sym), None)
    signal = get_strategy_signal()
    news = get_recent_news(sym, limit=8)
    fipi = get_fipi_flows()
    macro = get_macro_snapshot()
    rate = get_policy_rate()
    return {
        "symbol": sym,
        "sector": sector_of(sym) or "Other",
        "as_of": price.get("as_of") if "as_of" in price else None,
        "price": price,
        "technical": tech,
        "momentum_rank_today": row["rank"] if row else None,
        "in_phase1_top5": sym in (signal.get("selected_symbols") or []),
        "in_top5_if_filter_off": sym in (signal.get("would_pick_if_market_filter_off") or []),
        "phase1_signal": signal,
        "news": news,
        "fipi_flows": fipi,
        "macro": macro,
        "policy_rate": rate,
    }


# --------------------------------------------------------------------------
# TOOL 10: price history for charts / "what if I had bought" questions
# --------------------------------------------------------------------------
def get_price_history(symbol: str, days: int = 90) -> dict:
    """Recent daily OHLC for a symbol (for user 'what if I had bought X days ago')."""
    sym = _normalize_symbol(symbol)
    df = load_ohlcv(sym)
    if df.empty:
        return {"error": f"Unknown symbol {sym}"}
    df = df.sort_values("date").tail(max(1, int(days)))
    return {
        "symbol": sym,
        "bars": [
            {"date": str(pd.Timestamp(r["date"]).date()),
             "open": round(float(r["open"]), 2) if "open" in r else None,
             "close": round(float(r["close"]), 2),
             "volume": int(r["volume"]) if "volume" in r else None}
            for _, r in df.iterrows()
        ],
    }


# --------------------------------------------------------------------------
# Tool dispatcher: used by the LLM loop to execute a tool call
# --------------------------------------------------------------------------
# ==========================================================================
# NEW PIPELINES — overnight global risk, scored news sentiment, cost model,
# stored daily predictions. These expose the post-walk-forward upgrades to
# the chatbot.
# ==========================================================================
def get_overnight_signals() -> dict:
    """Latest overnight global-risk block: S&P 500, VIX, Nikkei, Hang Seng,
    FTSE, DXY, EM ETF + data-fitted PSX gap prior + weighted macro news tilt
    (24h) + latest FIPI snapshot. Everything the live LLM briefing shows."""
    try:
        from ui.overnight import (build_overnight_block, gap_bias_from_overnight,
                                    load_latest_fipi, load_overnight)
    except Exception as e:
        return {"error": f"overnight module unavailable: {e}"}
    cutoff = pd.Timestamp.today().normalize()
    raw = load_overnight(cutoff)
    if "error" in raw:
        return {"error": raw["error"]}
    bias = gap_bias_from_overnight(raw)
    out = {
        "as_of": str(raw.get("as_of")),
        "signals": {k: v for k, v in raw.items()
                     if k in ("sp500", "vix", "nikkei", "hangseng",
                              "ftse", "dxy", "eem")},
        "gap_prior": bias,
        "briefing_block": build_overnight_block(cutoff),
    }
    fipi = load_latest_fipi(cutoff)
    if fipi is not None:
        out["fipi"] = fipi
    return out


def get_scored_sentiment(symbol: str | None = None,
                         hours_macro: int = 24,
                         hours_ticker: int = 72) -> dict:
    """Quantified news sentiment from the scored-news cache.

    Returns:
      macro: weighted macro/policy/commodity/geopolitics tilt in last `hours_macro`
      ticker (if symbol given): weighted sentiment for that ticker in `hours_ticker`
      top_headlines: up to 5 most impactful scored headlines
    """
    try:
        from ui.news_sentiment import (load_scored_news, macro_sentiment,
                                         ticker_sentiment)
    except Exception as e:
        return {"error": f"news_sentiment module unavailable: {e}"}
    df = load_scored_news(max(hours_macro, hours_ticker))
    if df.empty:
        return {"error": "No scored news cache. Run "
                          "scripts/score_news_sentiment.py first."}
    out: dict[str, Any] = {
        "cache_size": int(len(df)),
        "macro": macro_sentiment(hours_macro, df),
    }
    if symbol:
        sym = _normalize_symbol(symbol)
        out["ticker"] = ticker_sentiment(sym, hours_ticker, df)
    df2 = df.copy()
    df2["abs"] = df2["sentiment"].abs() * df2["_w"]
    top = df2.sort_values("abs", ascending=False).head(5)
    out["top_headlines"] = [
        {
            "title": r["title"],
            "source": r["source"],
            "sentiment": round(float(r["sentiment"]), 3),
            "confidence": r["confidence"],
            "category": r["category"],
            "affected_symbols": r["affected_symbols"],
            "one_liner": r.get("one_liner", ""),
        }
        for _, r in top.iterrows()
    ]
    return out


def estimate_trade_net_return(gross_return_pct: float) -> dict:
    """Apply the PSX cost model to a gross return estimate.

    Returns gross, round-trip cost %, net after costs, net after costs+CGT,
    minimum required gross to clear the 1.0% edge threshold, and a viable flag.
    """
    try:
        from config.costs import (MINIMUM_NET_EDGE_PCT, describe_costs,
                                    trade_is_viable)
    except Exception as e:
        return {"error": f"cost model unavailable: {e}"}
    viable, d = trade_is_viable(float(gross_return_pct))
    d["minimum_edge_pct"] = MINIMUM_NET_EDGE_PCT
    d["cost_description"] = describe_costs()
    return d


def get_todays_predictions(max_items: int = 20,
                           only_actionable: bool = False) -> dict:
    """Read the stored daily predictions log and return today's (most recent)
    forecasts for the 15-stock universe, enriched with net-return after costs.

    If only_actionable=True, returns only BUY/ADD picks that clear cost+edge.
    """
    try:
        from config.costs import net_return_pct, round_trip_cost_pct
    except Exception as e:
        return {"error": f"cost model unavailable: {e}"}
    log_path = PROJECT_ROOT / "data" / "predictions_log.json"
    if not log_path.exists():
        return {"error": "No predictions_log.json yet. Run "
                          "scripts/generate_predictions.py first."}
    data = json.loads(log_path.read_text(encoding="utf-8"))
    preds = data.get("predictions") or []
    if not preds:
        return {"error": "predictions_log.json has no entries"}

    as_of = max((p.get("data_snapshot", {}).get("as_of_price_date") or "")
                  for p in preds)
    latest = [p for p in preds
                if p.get("data_snapshot", {}).get("as_of_price_date") == as_of]
    rt_cost = round_trip_cost_pct()

    # Older predictions in the log were generated before the macro-
    # impact engine existed. Compute it lazily now (cheap — same
    # deterministic rule book), so the "Why this call?" panel still
    # has macro context for them.
    cached_macro_impact: dict | None = None
    universe_syms = [p["symbol"] for p in latest]
    def _macro_for(sym: str, sector: str | None) -> dict | None:
        nonlocal cached_macro_impact
        try:
            from brain.macro_impact import compute_macro_impact
            if cached_macro_impact is None:
                cached_macro_impact = compute_macro_impact(
                    universe=universe_syms)
            return {
                "drivers": cached_macro_impact.get("drivers") or [],
                "by_sector": (cached_macro_impact.get("by_sector") or {})
                              .get(sector or "", {}),
                "by_symbol": (cached_macro_impact.get("by_symbol") or {})
                              .get(sym, {}),
            }
        except Exception:
            return None

    rows: list[dict] = []
    for p in latest:
        gross = float(p.get("expected_return_5d_mid_pct") or 0)
        net = net_return_pct(gross)

        mi = p.get("macro_impact")
        sym_block = (mi or {}).get("by_symbol") or {}
        # Best-effort backfill for older predictions
        if not mi:
            mi = _macro_for(p["symbol"], p.get("sector"))
            sym_block = (mi or {}).get("by_symbol") or {}

        # Synthesize macro_tailwinds/headwinds from the snapshot if the
        # prediction record does not already carry them (older format).
        macro_tw = p.get("macro_tailwinds") or list(
            (sym_block.get("tailwinds") or [])[:3])
        macro_hw = p.get("macro_headwinds") or list(
            (sym_block.get("headwinds") or [])[:3])

        row = {
            "symbol": p["symbol"],
            "sector": p.get("sector"),
            "direction": p.get("direction"),
            "conviction": p.get("conviction"),
            "suggested_action": p.get("suggested_action"),
            "entry_price_pkr": p.get("entry_price_pkr"),
            "suggested_stop_pkr": p.get("suggested_stop_pkr"),
            "suggested_target_pkr": p.get("suggested_target_pkr"),
            "expected_gross_5d_pct": round(gross, 2),
            "round_trip_cost_pct": rt_cost,
            "expected_net_5d_pct": net,
            "clears_cost_threshold": gross >= (rt_cost + 1.0),
            "rationale": p.get("rationale"),
            "key_drivers": p.get("key_drivers", []),
            "key_risks": p.get("key_risks", []),
            "macro_tailwinds": macro_tw,
            "macro_headwinds": macro_hw,
            "macro_impact": mi,
        }
        rows.append(row)

    rows.sort(key=lambda r: -r["expected_gross_5d_pct"])
    if only_actionable:
        rows = [r for r in rows
                 if r["suggested_action"] in ("BUY", "ADD")
                 and r["clears_cost_threshold"]]
    rows = rows[: int(max_items)]

    return {
        "as_of": as_of,
        "horizon_trading_days": latest[0].get("horizon_trading_days", 5),
        "round_trip_cost_pct": rt_cost,
        "minimum_gross_for_trade_pct": round(rt_cost + 1.0, 3),
        "n_total": len(latest),
        "predictions": rows,
    }


def get_watchlist() -> dict:
    """Return every symbol the user has added to the watchlist, enriched with
    live price, 1d / 5d return, momentum rank, and target-price status.

    Use this to answer 'what's on my watchlist', 'is anything I'm tracking
    breaking out', or 'am I near a target price on any name'."""
    try:
        from ui.watchlist import load_watchlist
    except Exception as e:
        return {"error": f"watchlist module unavailable: {e}"}
    items = load_watchlist()
    if not items:
        return {"items": [], "note": "Watchlist is empty. Add symbols from "
                                       "the Watchlist tab in the UI."}
    ranking = get_universe_ranking()
    rank_map = {r["symbol"]: r["rank"]
                for r in ranking.get("ranking", [])}
    rows: list[dict] = []
    for it in items:
        sym = it["symbol"]
        p = get_price(sym)
        last = p.get("close_pkr")
        tgt = it.get("target_price")
        rows.append({
            "symbol": sym,
            "added_date": it.get("added_date"),
            "note": it.get("note"),
            "last_price_pkr": last,
            "ret_1d_pct": p.get("ret_1d_pct"),
            "ret_5d_pct": p.get("ret_5d_pct"),
            "momentum_rank": rank_map.get(sym),
            "target_price_pkr": tgt,
            "upside_to_target_pct":
                round((tgt / last - 1) * 100, 2)
                if (tgt and last) else None,
            "alert_above_hit":
                bool(it.get("alert_above") and last and last >= it["alert_above"]),
            "alert_below_hit":
                bool(it.get("alert_below") and last and last <= it["alert_below"]),
        })
    return {"count": len(rows), "items": rows}


def get_trade_journal(limit: int = 20) -> dict:
    """Return the user's closed-trade history from data/trade_journal.json.

    Includes realized gross/net P&L in PKR, hold days, win rate, and per-trade
    details. Use whenever the user asks about their track record, prior wins/
    losses, or whether a given name has worked for them before."""
    try:
        from ui.trade_journal import load_journal, journal_stats
    except Exception as e:
        return {"error": f"trade_journal unavailable: {e}"}
    trades = load_journal()
    trades_sorted = sorted(trades,
                            key=lambda t: t.get("exit_date") or "",
                            reverse=True)
    return {"stats": journal_stats(),
            "recent_trades": trades_sorted[: int(limit)]}


# --------------------------------------------------------------------------
# Value / fundamental layer (brain/valuation.py)
# --------------------------------------------------------------------------
def get_value_signal(symbol: str) -> dict:
    """Sector-aware fair-value signal for one PSX symbol.

    Returns intrinsic value, upside vs current price, BUY_VALUE / FAIR /
    SELL_VALUE / NO_SIGNAL plus the formula and components used.
    See ``brain/valuation.py`` for the per-sector rules.
    """
    try:
        from brain.valuation import value_signal
    except Exception as e:
        return {"error": f"valuation engine unavailable: {e}"}
    sym = (symbol or "").upper()
    return value_signal(sym)


def get_universe_value_book() -> dict:
    """Run the value model on every universe ticker.

    Output is sorted from most-undervalued (highest upside %) to most-
    overvalued. Use this when the user asks "what's cheap" or "deep
    value picks".
    """
    try:
        from brain.valuation import universe_value_book
    except Exception as e:
        return {"error": f"valuation engine unavailable: {e}"}
    return universe_value_book()


# --------------------------------------------------------------------------
# Quality / earnings-momentum / earnings-calendar
# --------------------------------------------------------------------------
def get_quality_score(symbol: str) -> dict:
    """Composite 0-100 quality score for a single PSX symbol.

    Blends profitability (ROE, 30%), leverage (D/E, 25%), earnings
    stability (5y CV, 20%), revenue growth (15%), EPS growth (10%) with
    sector-aware leverage thresholds. Use to distinguish real value
    (BUY_VALUE + HIGH quality) from value traps (BUY_VALUE + JUNK).
    """
    try:
        from brain.quality import quality_score
    except Exception as e:
        return {"error": f"quality engine unavailable: {e}"}
    return quality_score((symbol or "").upper())


def get_universe_quality_book() -> dict:
    """Quality scores for the whole universe, sorted descending."""
    try:
        from brain.quality import universe_quality_book
    except Exception as e:
        return {"error": f"quality engine unavailable: {e}"}
    return universe_quality_book()


def get_earnings_momentum(symbol: str) -> dict:
    """Earnings-momentum flag (ACCELERATING / RECOVERING / STEADY /
    DECELERATING / EROSION) for a single PSX symbol with the YoY,
    prior-YoY, acceleration (pp), and 3y CAGR.
    """
    try:
        from brain.quality import earnings_momentum
    except Exception as e:
        return {"error": f"earnings_momentum unavailable: {e}"}
    return earnings_momentum((symbol or "").upper())


def get_universe_earnings_momentum() -> dict:
    """Earnings-momentum flags for the whole universe."""
    try:
        from brain.quality import universe_earnings_momentum
    except Exception as e:
        return {"error": f"earnings_momentum unavailable: {e}"}
    return universe_earnings_momentum()


def get_earnings_calendar(days_ahead: int = 21) -> dict:
    """Predicted upcoming earnings / dividend-meeting dates for the
    universe. Each row carries days_until, confidence (HIGH/MED/LOW),
    source (yfinance/cadence/sector_typical), and a blackout flag for
    events ≤5 days away.
    """
    try:
        from brain.earnings_calendar import universe_calendar
    except Exception as e:
        return {"error": f"earnings_calendar unavailable: {e}"}
    return universe_calendar(days_ahead=int(days_ahead))


def get_next_earnings(symbol: str) -> dict:
    """Predicted next earnings date for one ticker."""
    try:
        from brain.earnings_calendar import next_event
    except Exception as e:
        return {"error": f"earnings_calendar unavailable: {e}"}
    return next_event((symbol or "").upper())


def get_macro_impact_today(symbol: str | None = None) -> dict:
    """Sector- and stock-level macroeconomic impact for the current
    macro snapshot. Returns active drivers (rates, oil, USD/PKR, gold,
    copper, cotton, coal proxy, T-bill 3M, KIBOR 3M, FX reserves,
    KSE-100 momentum, CPI YoY), per-sector tailwind/headwind verdicts,
    per-stock scores, and the live industry-KPI snapshot. If `symbol`
    is provided, the response is narrowed to that one ticker plus its
    sector verdict.
    """
    try:
        from brain.macro_impact import compute_macro_impact
    except Exception as e:
        return {"error": f"macro_impact engine unavailable: {e}"}
    full = compute_macro_impact()
    if not symbol:
        return full
    sym = symbol.upper().strip()
    by_symbol = full.get("by_symbol") or {}
    sym_block = by_symbol.get(sym) or {}
    sector = sym_block.get("sector")
    by_sector = full.get("by_sector") or {}
    return {
        "symbol": sym,
        "sector": sector,
        "drivers": full.get("drivers") or [],
        "sector_verdict": by_sector.get(sector or "", {}),
        "stock_verdict": sym_block,
        "kpis": full.get("kpis") or {},
        "as_of": full.get("as_of"),
    }


def get_industry_kpis() -> dict:
    """Live numeric snapshot of the industry-specific KPIs the macro
    engine consumes: SBP T-bill 3M cut-off, KIBOR 3M, total / SBP-only
    FX reserves, KSE-100 close + 5d / 21d returns, Pakistan CPI YoY %.

    Each value is the *latest* observation from the persisted parquets
    in ``data/macro/``. Use this to answer "what's the current T-bill
    rate?", "where are FX reserves today?", "what was the latest CPI
    print?".
    """
    try:
        from brain.macro_impact import _load_kpi_snapshot
    except Exception as e:
        return {"error": f"industry KPI loader unavailable: {e}"}
    snap = _load_kpi_snapshot() or {}
    if not snap:
        return {"error": "no industry KPI parquets on disk yet"}
    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kpis":  snap,
    }


def get_management_outlook(symbol: str) -> dict:
    """Latest LLM-extracted management commentary for `symbol`:
    business outlook, growth/risk highlights, capacity utilization and
    new-product mentions, sourced from the company's most recent
    Director's Report PDF on PSX Data Portal. Use this to answer
    questions like 'what does HUBC management say about coal pricing?'.
    """
    try:
        from ui.dashboard_data import management_outlook_history
    except Exception as e:
        return {"error": f"management outlook module unavailable: {e}"}
    rows = management_outlook_history(symbol=symbol.upper().strip())
    if not rows:
        return {"error": f"No Director's Report on file for {symbol!r}."}
    # The history is sorted newest-first by the dashboard helper.
    return {"symbol": symbol.upper().strip(),
             "latest": rows[0],
             "history_count": len(rows)}


def get_material_information(symbol: str | None = None,
                              days: int = 14) -> dict:
    """Recent PSX Material Information disclosures (price-sensitive
    notices). If `symbol` is provided, filter to that ticker; otherwise
    return everything in the last `days`."""
    try:
        from ui.dashboard_data import material_information_recent
    except Exception as e:
        return {"error": f"material info module unavailable: {e}"}
    payload = material_information_recent(
        symbol=symbol.upper().strip() if symbol else None,
        days=int(days),
    )
    rows = payload.get("rows") or []
    return {
        "as_of":   payload.get("as_of"),
        "rows":    rows,
        "count":   len(rows),
        "summary": payload.get("summary"),
    }


TOOL_FUNCTIONS = {
    "list_universe": list_universe,
    "get_price": get_price,
    "get_technical_snapshot": get_technical_snapshot,
    "get_universe_ranking": get_universe_ranking,
    "get_strategy_signal": get_strategy_signal,
    "get_market_regime": get_market_regime,
    "analyze_position": analyze_position,
    "get_user_portfolio": get_user_portfolio,
    "recommend_new_buys": recommend_new_buys,
    "get_price_history": get_price_history,
    "get_fipi_flows": get_fipi_flows,
    "get_sector_volume_heatmap": get_sector_volume_heatmap,
    "get_policy_rate": get_policy_rate,
    "get_macro_snapshot": get_macro_snapshot,
    "get_macro_impact_today": get_macro_impact_today,
    "get_industry_kpis": get_industry_kpis,
    "get_recent_news": get_recent_news,
    "get_full_context": get_full_context,
    "get_overnight_signals": get_overnight_signals,
    "get_scored_sentiment": get_scored_sentiment,
    "estimate_trade_net_return": estimate_trade_net_return,
    "get_todays_predictions": get_todays_predictions,
    "get_watchlist": get_watchlist,
    "get_trade_journal": get_trade_journal,
    "get_value_signal": get_value_signal,
    "get_universe_value_book": get_universe_value_book,
    "get_quality_score": get_quality_score,
    "get_universe_quality_book": get_universe_quality_book,
    "get_earnings_momentum": get_earnings_momentum,
    "get_universe_earnings_momentum": get_universe_earnings_momentum,
    "get_earnings_calendar": get_earnings_calendar,
    "get_next_earnings": get_next_earnings,
    "get_management_outlook": get_management_outlook,
    "get_material_information": get_material_information,
    "get_bots_verdict": lambda symbol=None: (
        __import__("brain.verdict_synthesizer", fromlist=["synthesize"])
        .synthesize(symbol) if symbol else
        __import__("brain.verdict_synthesizer",
                    fromlist=["synthesize_universe"])
        .synthesize_universe()
    ),
    "get_short_candidates": lambda min_conviction="LOW",
                                  max_results=10: (
        __import__("brain.short_candidates",
                    fromlist=["rank_shorts"])
        .rank_shorts(min_conviction=min_conviction,
                       max_results=max_results)
    ),
}


def dispatch(name: str, args: dict | None) -> dict:
    """Execute a tool by name with a dict of args. Always returns a dict."""
    args = args or {}
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool {name!r}"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"error": f"Bad arguments to {name!r}: {e}"}
    except Exception as e:
        return {"error": f"Tool {name!r} raised: {type(e).__name__}: {e}"}


# --------------------------------------------------------------------------
# JSON schemas — Anthropic tools format
# --------------------------------------------------------------------------
TOOL_SCHEMAS_ANTHROPIC: list[dict] = [
    {
        "name": "list_universe",
        "description": ("List the 15 PSX stocks the bot trades, with sectors. "
                        "Use this to remind the user which symbols are supported."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_price",
        "description": ("Get the latest close price and recent returns (1d, 5d, 21d, "
                        "63d, 252d) for a single PSX symbol."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string",
                                      "description": "Ticker, e.g. 'HUBC', 'MCB'"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_technical_snapshot",
        "description": ("Detailed technical snapshot for a symbol: momentum at "
                        "multiple horizons, moving averages, 20d and 52w ranges, "
                        "volatility regime, RSI, trend flag."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_universe_ranking",
        "description": ("Full ranking of all 15 universe stocks by 150-day momentum "
                        "with volatility filter status. Use for 'what are the "
                        "leaders' questions."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_strategy_signal",
        "description": ("Today's Phase 1 recommendation: market regime gate, top-N "
                        "picks by momentum with volatility filter. This is the "
                        "mechanical system's view."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_market_regime",
        "description": ("Current market regime (NORMAL / CAUTION / CRISIS) with "
                        "breadth, 5d and 21d universe returns, and exposure "
                        "multiplier."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "analyze_position",
        "description": ("Full analysis of a SINGLE position: current P&L, suggested "
                        "trailing stop level in PKR, the Phase 1 signal's view on "
                        "the symbol, and a suggested action (HOLD / SELL / TRIM). "
                        "Use this whenever the user says 'I bought X at Y, what "
                        "should I do'."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "entry_price": {"type": "number",
                                "description": "Buy-in price per share in PKR"},
                "entry_date": {"type": "string",
                               "description": "Optional YYYY-MM-DD entry date"},
                "quantity": {"type": "number",
                             "description": "Optional number of shares"},
            },
            "required": ["symbol", "entry_price"],
        },
    },
    {
        "name": "get_user_portfolio",
        "description": ("List ALL positions the user has saved in the UI, with "
                        "live mark-to-market P&L in PKR. Call this when the user "
                        "asks about 'my portfolio'."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "recommend_new_buys",
        "description": ("Today's top buy candidates per the Phase 1 rule, with "
                        "momentum, volatility, and RSI per name. If the market "
                        "filter is off, these are flagged as cautious."),
        "input_schema": {
            "type": "object",
            "properties": {"max_ideas": {"type": "integer", "default": 5}},
        },
    },
    {
        "name": "get_price_history",
        "description": ("Daily OHLC bars for the last N trading days. Useful for "
                        "answering 'what was the price on date X' or 'how much "
                        "has Y moved in the last N days'."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"},
                           "days": {"type": "integer", "default": 90}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_fipi_flows",
        "description": ("Today's PSX foreign vs local investor flows (from "
                        "SCStrade FIPI). Returns foreign_net_pkr_mn (>0 = net "
                        "buying = bullish, <0 = net selling = risk-off), "
                        "local_net_pkr_mn, participant breakdown, and top "
                        "sectors by absolute USD flow. Call this to answer "
                        "'are foreigners buying?' or when sizing conviction."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_policy_rate",
        "description": ("Current SBP policy rate, corridor (floor/ceiling), "
                        "KIBOR 3M/6M/12M, T-Bill yields 1M/3M/6M/12M, PIB "
                        "yields 2Y/3Y/5Y/10Y/15Y, SBP FX reserves, and a "
                        "regime interpretation (accommodative/neutral/"
                        "restrictive). Important for banks, cement, autos."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_macro_snapshot",
        "description": ("Latest value + 5d/21d/63d return for USD/PKR, Brent, "
                        "WTI, Gold, BTC, Copper, Cotton. Returns a short "
                        "narrative summarising implications for PSX sectors "
                        "(E&P, OMC, autos, cement, banks)."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_recent_news",
        "description": ("Latest RSS articles from Business Recorder, Profit "
                        "by Pakistan Today, Dawn Business, The News, and "
                        "Tribune. If 'symbol' is given, filters to headlines "
                        "matching that ticker or its sector; otherwise "
                        "market-wide. Returns up to 'limit' most recent."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string",
                           "description": "Optional ticker, e.g. 'OGDC'. "
                                          "Omit for market-wide news."},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "get_full_context",
        "description": ("ONE-SHOT everything-you-need bundle for a ticker: "
                        "price, technicals, momentum rank, Phase 1 signal, "
                        "symbol-matched news, FIPI flows, macro snapshot, "
                        "policy rate. Use this when generating a prediction "
                        "or a thorough buy/sell recommendation so you have "
                        "every data layer in a single tool call."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_overnight_signals",
        "description": ("Overnight global risk snapshot that predicts today's "
                        "PSX open: S&P 500, VIX, Nikkei, Hang Seng, FTSE, "
                        "USD Index, EM ETF, plus a DATA-FITTED gap prior "
                        "(+/- PSX open %) and a weighted 24h macro news tilt. "
                        "Use this any time the user asks 'what will the market "
                        "do today / tomorrow' or 'how did global markets "
                        "close'."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_scored_sentiment",
        "description": ("Quantified news sentiment from the Claude-scored "
                        "news cache. Returns: (1) macro tilt in [-1,+1] "
                        "weighted by confidence + recency over the last "
                        "`hours_macro` hours, (2) ticker-specific tilt if "
                        "`symbol` is given, (3) top 5 highest-impact scored "
                        "headlines. Use whenever news or sentiment comes up."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string",
                             "description": "Optional PSX ticker to filter by"},
                "hours_macro": {"type": "integer", "default": 24},
                "hours_ticker": {"type": "integer", "default": 72},
            },
        },
    },
    {
        "name": "estimate_trade_net_return",
        "description": ("Apply the PSX transaction-cost model to a gross "
                        "return estimate. Returns gross, round-trip cost "
                        "(~0.56%), net after costs, net after costs+CGT, "
                        "and whether the trade clears the 1.0% minimum "
                        "edge threshold. Use whenever the user asks 'is "
                        "this trade worth it' or mentions expected returns."),
        "input_schema": {
            "type": "object",
            "properties": {
                "gross_return_pct": {
                    "type": "number",
                    "description": "Expected gross return %, e.g. 2.5 for 2.5%"
                }
            },
            "required": ["gross_return_pct"],
        },
    },
    {
        "name": "get_todays_predictions",
        "description": ("Today's stored 5-day predictions for the full 15-"
                        "stock universe, from data/predictions_log.json, "
                        "enriched with net-return-after-costs. Each row has "
                        "entry/stop/target, gross & net expected return, "
                        "conviction, drivers, risks, and a "
                        "`clears_cost_threshold` flag. Use for 'what should "
                        "I buy today' or 'what does the model say about X'."),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_items": {"type": "integer", "default": 20},
                "only_actionable": {
                    "type": "boolean", "default": False,
                    "description": ("If true, return only BUY/ADD picks "
                                     "that clear the cost threshold.")
                },
            },
        },
    },
    {
        "name": "get_watchlist",
        "description": ("List every symbol the user has saved to their "
                        "watchlist with live price, 1d/5d return, momentum "
                        "rank, target-price upside, and whether configured "
                        "price alerts have been hit. Call this when the user "
                        "asks 'what's on my watchlist', 'anything interesting "
                        "I'm tracking', or 'am I near a target on any name'."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_trade_journal",
        "description": ("Return the user's closed-position trade journal "
                        "with realized gross and net (post-cost) P&L, win "
                        "rate, average winner, average loser, and recent "
                        "trade details. Call this when the user asks about "
                        "their track record, past trades in a name, or "
                        "overall performance."),
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "get_value_signal",
        "description": ("Sector-aware fair-value (intrinsic value) signal for "
                        "ONE PSX symbol. Returns fair_value PKR, upside_pct "
                        "vs current price, signal in {BUY_VALUE, FAIR, "
                        "SELL_VALUE, NO_SIGNAL}, the method used (DDM for "
                        "banks, P/B for E&P, P/E for cement, blends for "
                        "OMC/Misc), the components, and quality warnings. "
                        "Use this for 'is this stock cheap?', 'what's the "
                        "fair value of X?', 'is X overvalued?', or any "
                        "value-investing / mean-reversion-to-fundamentals "
                        "question. This is a SLOW signal (6-24 months); "
                        "combine with momentum/news for entry timing."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_universe_value_book",
        "description": ("Run the fair-value model on all 15 stocks and "
                        "return them sorted from most-undervalued to most-"
                        "overvalued. Each row has fair_value, upside_pct, "
                        "signal, confidence, sector, and method. Use this "
                        "for 'what's cheap right now', 'find me deep value "
                        "picks', 'which stocks should I sell on valuation', "
                        "or any portfolio-wide value question."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_quality_score",
        "description": ("Composite 0-100 quality score for one stock. "
                        "Blends ROE (30%), leverage / debt-equity (25%, "
                        "sector-aware), 5y EPS stability (20%), revenue "
                        "3y CAGR (15%), EPS 3y CAGR (10%). Returns the "
                        "score, band (HIGH/MEDIUM/LOW/JUNK), and the "
                        "underlying components. Use to distinguish real "
                        "value picks from value traps and to support "
                        "'is X a quality business?' questions. Always "
                        "pair this with a value signal: high-quality + "
                        "BUY_VALUE = real edge; JUNK + BUY_VALUE = trap."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_universe_quality_book",
        "description": ("Quality scores for the whole universe, sorted "
                        "from highest to lowest. Use for 'rank by quality', "
                        "'best quality names on PSX', or before opening any "
                        "value-driven trade."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_earnings_momentum",
        "description": ("Earnings-momentum flag for one stock: "
                        "ACCELERATING (yoy>5%, growth speeding up), "
                        "RECOVERING (yoy>5% out of a prior loss), "
                        "STEADY, DECELERATING (yoy positive but "
                        "slowing), EROSION (yoy<-5%). Includes YoY %, "
                        "prior-YoY %, acceleration in pp, 3y CAGR. "
                        "Use for trend-following 'is X improving?' "
                        "questions. Best paired with momentum and "
                        "quality."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_universe_earnings_momentum",
        "description": ("Earnings-momentum flags for all 15 stocks."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_earnings_calendar",
        "description": ("Predicted next earnings / dividend-meeting "
                        "date for every universe stock within "
                        "days_ahead (default 21 days). Each row carries "
                        "days_until, confidence (HIGH/MEDIUM/LOW), "
                        "source (yfinance / cadence / sector_typical) "
                        "and an in_blackout_5d flag. CRITICAL for "
                        "event-risk management: do NOT recommend "
                        "initiating new BUY/ADD positions on stocks in "
                        "blackout window. Use for 'what's reporting "
                        "soon', 'should I exit X before earnings', "
                        "'when does Y report'."),
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {"type": "integer", "default": 21},
            },
        },
    },
    {
        "name": "get_next_earnings",
        "description": ("Predicted next earnings date for ONE stock."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_macro_impact_today",
        "description": ("Sector- and stock-level macroeconomic impact "
                        "based on today's snapshot. Returns the active "
                        "macro drivers (policy rate, oil, USD/PKR, gold, "
                        "copper, cotton, coal proxy, T-bill 3M, KIBOR "
                        "3M, FX reserves, KSE-100 momentum, CPI YoY), "
                        "per-sector tailwind/headwind verdicts, "
                        "per-stock scores, and the live industry-KPI "
                        "snapshot. Pass `symbol` to narrow the response "
                        "to one ticker."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string",
                            "description": "Optional ticker, e.g. 'MEBL'"},
            },
        },
    },
    {
        "name": "get_industry_kpis",
        "description": ("Live numeric snapshot of the industry-specific "
                        "KPIs the macro engine consumes: SBP T-bill 3M, "
                        "KIBOR 3M, FX reserves, KSE-100 close + 5d/21d "
                        "momentum, Pakistan CPI YoY %. Use for direct "
                        "questions like 'what's the current T-bill "
                        "rate?', 'where are FX reserves today?', 'what "
                        "was the latest CPI print?'."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_management_outlook",
        "description": ("Latest LLM-extracted commentary from the "
                        "company's most recent Director's Report PDF: "
                        "business outlook, growth and risk highlights, "
                        "capacity utilization, and new-product mentions. "
                        "Use this for any 'what does management say "
                        "about X' question."),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_material_information",
        "description": ("Recent PSX Material Information disclosures "
                        "(price-sensitive notices). Optional `symbol` "
                        "filters to one ticker; `days` controls the "
                        "lookback window (default 14)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "days":   {"type": "integer",
                            "description": "Lookback window in days "
                                            "(default 14)."},
            },
        },
    },
    {
        "name": "get_sector_volume_heatmap",
        "description": ("Today's most-active sectors on PSX, ranked by "
                        "traded value vs the 20-day average. Helps the "
                        "user spot which industries are catching the "
                        "day's flow."),
        "input_schema": {
            "type": "object",
            "properties": {
                "top_k":         {"type": "integer"},
                "lookback_days": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_bots_verdict",
        "description": ("THE BOT'S VERDICT — a single unified call per "
                        "stock that reconciles all seven lenses (Value, "
                        "Quality, Momentum, Macro, News, Flow, "
                        "Management) into one action with explicit "
                        "conflict resolution. ALWAYS call this when "
                        "the user is confused about contradictory "
                        "signals across tabs (e.g. 'Value tab says SELL "
                        "but the prediction says BUY — what do I do?'). "
                        "Pass `symbol` for one ticker, or omit for the "
                        "universe-wide ranking."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string",
                            "description": "Optional PSX ticker."},
            },
        },
    },
    {
        "name": "get_short_candidates",
        "description": ("Stocks the bot expects to FALL over the next "
                        "~5 sessions, ranked by a composite short_score "
                        "(0-100). The score is wired into the bot's "
                        "live data: 30 pts from the verdict "
                        "synthesizer (which itself aggregates "
                        "fundamentals, management tone, FIPI flows, "
                        "macro, news, momentum, and quality lenses), "
                        "25 pts from the BEARISH 5-day prediction (LLM "
                        "strategist, which reads material info and "
                        "overnight globals), 15 pts from per-symbol "
                        "scored news, 15 pts from technical breakdown "
                        "+ live intraday relative weakness, 10 pts "
                        "from sector macro headwinds + industry KPI "
                        "weakness, 5 pts from intraday lower-circuit "
                        "hits, and 3 pts of affirmation if the "
                        "deterministic prediction critic agrees. "
                        "Earnings within 5 days and SBP MPC within 7 "
                        "days for rate-sensitive sectors cap "
                        "conviction at MEDIUM. The response includes "
                        "a `dataset_coverage` block listing every "
                        "data source and its live availability — cite "
                        "it if the user asks 'is this connected to "
                        "all the data?'. Use this tool when the user "
                        "asks 'what should I short?', 'which stocks "
                        "will go down?', 'find me bearish ideas', or "
                        "wants a hedge against existing longs. ALWAYS "
                        "include the eligibility disclaimer and the "
                        "regime banner in the answer — Pakistan retail "
                        "shorting is restricted to PSX Single Stock "
                        "Futures and NCCPL Securities Lending & "
                        "Borrowing, and shorting in a RISK_ON regime "
                        "is the most common retail mistake."),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_conviction": {
                    "type": "string",
                    "enum": ["LOW", "MEDIUM", "HIGH"],
                    "description": "LOW (default) returns watch-list + "
                                   "actionable shorts; MEDIUM filters "
                                   "to viable shorts; HIGH filters to "
                                   "multi-signal strong shorts only.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on the returned list "
                                   "(default 10).",
                },
            },
        },
    },
]


# --------------------------------------------------------------------------
# JSON schemas — Gemini function-calling format (google-genai SDK)
# The new SDK uses lowercase OpenAPI types in plain dicts.
# --------------------------------------------------------------------------
def _anthropic_to_gemini_schema(t: dict) -> dict:
    """Convert an Anthropic tool schema to Gemini's FunctionDeclaration format."""
    props = t.get("input_schema", {}).get("properties", {})
    gemini_props: dict[str, dict] = {}
    for k, v in props.items():
        entry = {"type": v.get("type", "string")}
        if "description" in v:
            entry["description"] = v["description"]
        gemini_props[k] = entry
    decl: dict[str, Any] = {
        "name": t["name"],
        "description": t["description"],
    }
    if gemini_props:
        decl["parameters"] = {
            "type": "object",
            "properties": gemini_props,
            "required": t.get("input_schema", {}).get("required", []),
        }
    else:
        # Gemini requires a parameters object; use an empty one.
        decl["parameters"] = {"type": "object", "properties": {}}
    return decl


TOOL_SCHEMAS_GEMINI: list[dict] = [_anthropic_to_gemini_schema(t)
                                   for t in TOOL_SCHEMAS_ANTHROPIC]


# --------------------------------------------------------------------------
# JSON schemas — OpenAI / GitHub Models function-calling format
# OpenAI expects: [{"type": "function", "function": {name, description, parameters}}]
# `parameters` is a JSON-Schema object — identical shape to our Anthropic
# `input_schema`, so we can reuse it directly.
# --------------------------------------------------------------------------
def _anthropic_to_openai_schema(t: dict) -> dict:
    """Convert an Anthropic tool schema to OpenAI function-calling format."""
    params = t.get("input_schema") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": params,
        },
    }


TOOL_SCHEMAS_OPENAI: list[dict] = [_anthropic_to_openai_schema(t)
                                     for t in TOOL_SCHEMAS_ANTHROPIC]


# --------------------------------------------------------------------------
# Smoke test
# --------------------------------------------------------------------------
if __name__ == "__main__":
    from rich import print
    print("list_universe:")
    print(list_universe())
    print("\nget_price('MCB'):")
    print(get_price("MCB"))
    print("\nget_strategy_signal():")
    print(get_strategy_signal())
    print("\nanalyze_position('MCB', 380, '2026-03-15', 100):")
    print(analyze_position("MCB", 380, "2026-03-15", 100))
