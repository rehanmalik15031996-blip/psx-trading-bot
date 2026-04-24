"""High-level recommendations layer used by the Portfolio and Scanner tabs.

These functions are ALSO available to the LLM via the tool layer, but the UI
consumes them directly (no LLM call) so the dashboard always shows the same
numbers the LLM sees. Single source of truth = `ui.tools`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ui import tools


def analyze_all_positions(positions: list[dict]) -> list[dict]:
    """Run `analyze_position` for every saved user position.

    Returns a list of enriched dicts (original fields + analysis fields).
    """
    out = []
    for p in positions:
        a = tools.analyze_position(
            symbol=p["symbol"],
            entry_price=float(p["entry_price"]),
            entry_date=p.get("entry_date"),
            quantity=float(p.get("quantity", 0)) or None,
        )
        if "error" in a:
            out.append({**p, "error": a["error"]})
            continue
        out.append({**p, **a})
    return out


def portfolio_summary(analyzed: list[dict]) -> dict:
    total_cost = sum((r.get("cost_basis_pkr") or 0) for r in analyzed)
    total_mv = sum((r.get("market_value_pkr") or 0) for r in analyzed)
    pnl = total_mv - total_cost
    # Count actions
    actions = {"HOLD": 0, "SELL": 0, "TRIM": 0, "CAUTION": 0}
    for r in analyzed:
        a = str(r.get("suggested_action", ""))
        if "SELL" in a:
            actions["SELL"] += 1
        elif "TRIM" in a:
            actions["TRIM"] += 1
        elif "cautious" in a.lower() or "CAUTION" in a:
            actions["CAUTION"] += 1
        elif "HOLD" in a:
            actions["HOLD"] += 1
    return {
        "position_count": len(analyzed),
        "total_cost_pkr": round(total_cost, 2),
        "total_market_value_pkr": round(total_mv, 2),
        "unrealized_pnl_pkr": round(pnl, 2),
        "unrealized_pnl_pct": round((pnl / total_cost) * 100, 2) if total_cost else 0.0,
        "action_counts": actions,
    }


def scanner_table() -> pd.DataFrame:
    """Full universe ranking as a DataFrame ready for st.dataframe."""
    r = tools.get_universe_ranking()
    if "error" in r:
        return pd.DataFrame()
    df = pd.DataFrame(r["ranking"])
    if df.empty:
        return df
    # Add a flag for today's picks
    sig = tools.get_strategy_signal()
    picks = set(sig.get("selected_symbols") or [])
    would_be = set(sig.get("would_pick_if_market_filter_off") or [])
    df["phase1_pick"] = df["symbol"].isin(picks)
    df["would_be_pick"] = df["symbol"].isin(would_be)
    return df


def top_buys(max_ideas: int = 5) -> dict:
    return tools.recommend_new_buys(max_ideas=max_ideas)
