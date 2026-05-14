"""Streamlit renderers for Master Strategist v2 per-tab intelligence.

This module is **additive**: existing tabs keep working unchanged. Each
``render_*_card`` function reads ``data/_strategist/latest_v2.json`` and
renders the relevant slice of the v2 decision at the top of its tab.

If the v2 cache is missing (e.g. pipeline never ran), every renderer
no-ops silently — the existing tab content shows up unchanged below.

Why a single helper module?
  - Centralised JSON parsing so the cache schema can evolve once.
  - Easy to disable globally with one flag if anything breaks live.
  - Lets each existing tab keep its render function compact: just
    add ``ui.strategist_v2.render_<tab>_card()`` near the top.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent.parent
V2_CACHE = PROJECT_ROOT / "data" / "_strategist" / "latest_v2.json"


# ---------------------------------------------------------------------------
# Loaders (Streamlit-cached)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def _load_v2_cached(mtime: float) -> dict | None:
    """Cache by file mtime so edits to the cache invalidate immediately."""
    try:
        return json.loads(V2_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_v2() -> dict | None:
    if not V2_CACHE.exists():
        return None
    return _load_v2_cached(V2_CACHE.stat().st_mtime)


# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------
_REGIME_COLOR = {
    "AGGRESSIVE":  ":green[**AGGRESSIVE**]",
    "NORMAL":      ":blue[**NORMAL**]",
    "CAUTIOUS":    ":orange[**CAUTIOUS**]",
    "DEFENSIVE":   ":red[**DEFENSIVE**]",
    "CASH":        ":red[**CASH**]",
}


def _format_regime(r: str | None) -> str:
    if not r:
        return "—"
    return _REGIME_COLOR.get(r.upper(), f"**{r}**")


def _action_emoji(action: str) -> str:
    """Plain dot/arrow indicators (no full emojis)."""
    return {
        "BUY":   "▲",
        "ADD":   "▲",
        "HOLD":  "●",
        "WATCH": "○",
        "TRIM":  "▽",
        "EXIT":  "▼",
        "AVOID": "▼",
        "SHORT": "▼",
    }.get(action.upper(), "·")


def _format_pct(x: Any, digits: int = 2) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x):+.{digits}f}%"
    except (TypeError, ValueError):
        return str(x)


def _strategist_badge(v2: dict) -> None:
    """Small inline badge: 'Strategist v2 • rule-based • 2026-05-15'."""
    asof = v2.get("as_of", "")
    model = v2.get("model", "rule-based-v2")
    badge_type = "Rule-based" if v2.get("fallback_used") else "LLM-refined"
    st.caption(
        f"🧠 Strategist v2 · {badge_type} · "
        f"{model} · {asof}")


# ---------------------------------------------------------------------------
# Today tab
# ---------------------------------------------------------------------------
def render_today_card() -> None:
    """Top of the Today tab: headline, regime, bullets, narrative."""
    v2 = load_v2()
    if not v2:
        return
    today = v2.get("today_brief", {})
    with st.container(border=True):
        cols = st.columns([3, 1])
        with cols[0]:
            st.markdown(f"### {today.get('headline', '—')}")
            st.markdown(
                f"**Regime:** {_format_regime(today.get('regime'))} "
                f"({today.get('regime_confidence', 'LOW')} confidence) · "
                f"**{today.get('n_longs', 0)}** long ideas · "
                f"**{today.get('n_shorts', 0)}** short ideas")
        with cols[1]:
            ev = today.get("key_event_window") or {}
            if ev.get("days_to") is not None:
                st.metric("Next event",
                            ev.get("name", "—"),
                            f"in {ev['days_to']}d")
        bullets = today.get("bullets") or []
        if bullets:
            for b in bullets:
                st.markdown(f"- {b}")
        narr = today.get("narrative")
        if narr:
            st.markdown(f"*{narr}*")
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Long Ideas (Find Ideas tab + new dedicated card)
# ---------------------------------------------------------------------------
def render_long_ideas_card(max_show: int = 10) -> None:
    v2 = load_v2()
    if not v2:
        return
    ideas = (v2.get("long_ideas") or {}).get("ideas") or []
    if not ideas:
        return
    with st.container(border=True):
        st.markdown(
            f"### 🎯 Long Ideas — {len(ideas)} ranked candidates")
        rows = []
        for idea in ideas[:max_show]:
            pp = idea.get("position_plan") or {}
            rows.append({
                "Symbol":         idea["symbol"],
                "Sector":         idea["sector"],
                "Action":         f"{_action_emoji(idea['action'])} {idea['action']}",
                "Conv.":          idea["conviction"],
                "Score":          f"{idea['score']:+.2f}",
                "Entry":          pp.get("entry_price"),
                "Stop %":         pp.get("stop_loss_pct"),
                "Target %":       pp.get("target_pct"),
                "Size %":         pp.get("position_size_pct"),
                "R:R":            pp.get("reward_to_risk_ratio"),
                "Why":            idea.get("why", "")[:90],
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)
        with st.expander("Position-plan details", expanded=False):
            for idea in ideas[:max_show]:
                pp = idea.get("position_plan") or {}
                st.markdown(
                    f"**{idea['symbol']}** ({idea['sector']}) · "
                    f"{idea['action']} · {idea['conviction']} conv."
                )
                if pp:
                    st.markdown(
                        f"  - Entry: **{pp.get('entry_price')}** PKR · "
                        f"Stop: **{pp.get('stop_loss_price')}** "
                        f"(-{pp.get('stop_loss_pct')}%) · "
                        f"Target: **{pp.get('target_price')}** "
                        f"(+{pp.get('target_pct')}%) · "
                        f"Hold: {pp.get('hold_horizon_days')}d")
                    st.caption(pp.get("rationale", ""))
                st.markdown(f"  - Why: {idea.get('why', '')}")
                if idea.get("key_drivers"):
                    st.markdown("  - Drivers: " + " · ".join(idea["key_drivers"][:3]))
                if idea.get("key_risks"):
                    st.markdown("  - Risks: " + " · ".join(idea["key_risks"][:2]))
                st.markdown("---")
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Short Ideas tab
# ---------------------------------------------------------------------------
def render_short_ideas_card() -> None:
    v2 = load_v2()
    if not v2:
        return
    si = v2.get("short_ideas") or {}
    ideas = si.get("ideas") or []
    note  = si.get("note") or ""
    with st.container(border=True):
        st.markdown(f"### 🎯 Short Ideas — {len(ideas)} candidates "
                     f"(regime: {si.get('regime', 'NORMAL')})")
        if note:
            st.info(note)
        if ideas:
            rows = [{
                "Symbol":       i["symbol"],
                "Sector":       i["sector"],
                "Score":        f"{i['score']:+.2f}",
                "Conv.":        i["conviction"],
                "Sector tilt":  i.get("macro_sector_tilt", 0),
                "Why":          i["why"][:90],
            } for i in ideas]
            st.dataframe(rows, hide_index=True, use_container_width=True)
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Portfolio tab
# ---------------------------------------------------------------------------
def render_portfolio_card() -> None:
    v2 = load_v2()
    if not v2:
        return
    pr = v2.get("portfolio_review") or {}
    holdings = pr.get("holdings") or []
    urgent   = pr.get("urgent_actions") or []
    if not holdings:
        return
    with st.container(border=True):
        st.markdown(
            f"### 📋 Portfolio Review — {len(holdings)} holdings, "
            f"{len(urgent)} urgent action(s)")
        if urgent:
            st.error(
                "**Urgent:** " +
                ", ".join(f"{r['symbol']} ({r['action']})" for r in urgent))
        rows = [{
            "Symbol":     r["symbol"],
            "Sector":     r["sector"],
            "Qty":        r.get("qty"),
            "Cost":       r.get("avg_cost"),
            "Price":      r.get("current_price"),
            "P&L %":      r.get("pnl_pct"),
            "Action":     f"{_action_emoji(r['action'])} {r['action']}",
            "Urgency":    r["urgency"],
            "Stop":       r.get("stop_loss_price"),
            "Stop %":     r.get("stop_loss_pct"),
            "Target":     r.get("target_price"),
            "Note":       r.get("note", "")[:80],
        } for r in holdings]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Sector view — sits next to Today / Forecast tab as an info box
# ---------------------------------------------------------------------------
def render_sector_view_card() -> None:
    v2 = load_v2()
    if not v2:
        return
    sv = v2.get("sector_view") or {}
    sectors = sv.get("sectors") or []
    if not sectors:
        return
    with st.container(border=True):
        st.markdown(
            f"### 🧭 Sector Outlook — best: "
            f"**{sv.get('best_sector', '—')}**, worst: "
            f"**{sv.get('worst_sector', '—')}**")
        rows = [{
            "Sector":      s["sector"],
            "Outlook":     s["outlook"],
            "Macro tilt":  s["macro_tilt"],
            "Avg score":   f"{s['avg_score']:+.2f}",
            "Top name":    f"{s['top_name']} ({s['top_score']:+.2f})",
            "Worst":       f"{s['bottom_name']} ({s['bottom_score']:+.2f})",
            "N":           s["n_stocks"],
        } for s in sectors]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Watchlist tab
# ---------------------------------------------------------------------------
def render_watchlist_card() -> None:
    v2 = load_v2()
    if not v2:
        return
    wl = v2.get("watchlist") or {}
    names = wl.get("names") or []
    if not names:
        return
    with st.container(border=True):
        st.markdown(
            f"### 👀 Strategist Watchlist — {len(names)} names "
            "(not yet BUY-grade)")
        for n in names:
            st.markdown(
                f"- **{n['symbol']}** ({n['sector']}) · "
                f"score {n['score']:+.2f}")
            for t in (n.get("triggers_to_buy") or [])[:3]:
                st.markdown(f"    - Trigger: {t}")
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Risks card — render at top of Today + System Health tabs
# ---------------------------------------------------------------------------
def render_risks_card() -> None:
    v2 = load_v2()
    if not v2:
        return
    risks = (v2.get("risks_today") or {}).get("risks") or []
    if not risks:
        return
    with st.container(border=True):
        st.markdown(f"### ⚠️ Risks to today's positioning ({len(risks)})")
        for r in risks:
            kind = r.get("type", "risk")
            note = r.get("note", "")
            st.markdown(f"- **{kind}:** {note}")
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Events card — top of Forecast / Today tabs
# ---------------------------------------------------------------------------
def render_events_card() -> None:
    v2 = load_v2()
    if not v2:
        return
    ev = (v2.get("events_intelligence") or {}).get("events") or []
    if not ev:
        return
    with st.container(border=True):
        st.markdown(f"### 📅 Events Intelligence ({len(ev)} upcoming)")
        for e in ev:
            st.markdown(
                f"- **{e['name']}** — in **{e['days_to']}d**: "
                f"{e['reaction_plan']}")
        _strategist_badge(v2)


# ---------------------------------------------------------------------------
# Per-stock overlay (drop into existing per-stock detail pages)
# ---------------------------------------------------------------------------
def render_stock_overlay(symbol: str) -> None:
    """Inline strategist intelligence for a single symbol. Drop into
    any per-stock detail view (predictions tab, holdings detail, etc.)."""
    v2 = load_v2()
    if not v2:
        return
    rows = v2.get("stock_scores") or []
    match = next((r for r in rows if r["symbol"] == symbol), None)
    if not match:
        return
    with st.container(border=True):
        cols = st.columns([2, 1, 1])
        with cols[0]:
            st.markdown(
                f"**Strategist call:** {_action_emoji(match['action'])} "
                f"**{match['action']}** "
                f"({match['conviction']}) · score {match['score']:+.2f}")
            st.caption(match.get("why", ""))
        with cols[1]:
            st.metric("5d net %",
                      _format_pct(match.get("expected_5d_net_pct")))
        with cols[2]:
            # Try to pull position plan from long_ideas
            ideas = (v2.get("long_ideas") or {}).get("ideas") or []
            plan = next((i.get("position_plan") for i in ideas
                          if i["symbol"] == symbol), None)
            if plan:
                st.metric("Stop %", f"{plan.get('stop_loss_pct')}%",
                            f"Target {plan.get('target_pct')}%")
        if match.get("key_drivers"):
            st.markdown("**Drivers:** " + " · ".join(match["key_drivers"][:3]))


def is_available() -> bool:
    """True if a v2 cache exists. UI can use this to render a heads-up
    when the pipeline hasn't run yet."""
    return V2_CACHE.exists()
