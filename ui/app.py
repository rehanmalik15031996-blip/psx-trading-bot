"""PSX Advisor — Streamlit UI.

Run from the repo root:
    streamlit run ui/app.py

Tabs:
  1. Dashboard  — morning brief: regime, overnight, today's picks, portfolio P&L,
                  news tilt, prediction accuracy, top gainers/losers.
  2. Portfolio  — live positions with sector allocation, per-position price
                  chart, trailing-stop diagnostics, close-to-journal flow,
                  realized-trade history, CSV import/export.
  3. Watchlist  — tracked symbols with target-price tracking and alert levels.
  4. Scanner    — 15-stock universe ranked by momentum + today's Phase 1 picks.
  5. Predictions- today's stored 5-day forecasts + rolling hit-rate scorecard.
  6. News       — Claude-scored PSX news feed with sentiment, tickers, category.
  7. Chat       — LLM advisor (Claude / Gemini / GitHub Models) that grounds
                  every answer in tool calls into the backend.
  8. Backtest   — on-demand Plan D Phase 1 backtest.

API keys: set ANTHROPIC_API_KEY / GOOGLE_API_KEY / GITHUB_TOKEN in your .env
or paste them in the sidebar for this session.
"""

from __future__ import annotations

import io
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the repo root is on sys.path so `brain.`, `data.`, `config.`, `ui.`
# imports work when streamlit runs this file directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_dotenv(path: Path) -> None:
    """Auto-load .env so API keys are picked up without manual export."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(PROJECT_ROOT / ".env")

import pandas as pd
import streamlit as st

from config.universe import sector_of
from config.costs import round_trip_cost_pct, minimum_gross_for_trade

from ui import tools, recommendations as recs, dashboard_data as dash
from ui.portfolio import (
    load_user_portfolio, save_user_portfolio, add_position, remove_position,
    close_position,
)
from ui.watchlist import (
    load_watchlist, add_to_watchlist, remove_from_watchlist,
)
from ui.trade_journal import load_journal, journal_stats, remove_trade
from ui.llm_clients import (
    get_client, DEFAULT_CLAUDE_MODEL, DEFAULT_GEMINI_MODEL,
    DEFAULT_GITHUB_MODEL, GITHUB_MODEL_CHOICES,
)


st.set_page_config(
    page_title="PSX Advisor",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
def _init_state():
    ss = st.session_state
    ss.setdefault("chat_history", [])
    has_gh = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"))
    ss.setdefault("provider", "github" if has_gh else "claude")
    ss.setdefault("claude_key", os.environ.get("ANTHROPIC_API_KEY", ""))
    ss.setdefault("gemini_key", os.environ.get("GOOGLE_API_KEY", "")
                  or os.environ.get("GEMINI_API_KEY", ""))
    ss.setdefault("github_key", os.environ.get("GITHUB_TOKEN", "")
                  or os.environ.get("GH_TOKEN", ""))
    ss.setdefault("claude_model", DEFAULT_CLAUDE_MODEL)
    ss.setdefault("gemini_model", DEFAULT_GEMINI_MODEL)
    ss.setdefault("github_model", DEFAULT_GITHUB_MODEL)
    ss.setdefault("_pending_close", None)  # index of a position pending close


_init_state()


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _pct(x, digits: int = 2) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:+.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _pct_raw(x, digits: int = 2) -> str:
    """Format a number already expressed in percent (not decimal)."""
    if x is None:
        return "n/a"
    try:
        return f"{float(x):+.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _pkr(x, digits: int = 0) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x):+,.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _regime_color(r: str) -> str:
    return {"NORMAL": "green", "CAUTION": "orange", "CRISIS": "red"}.get(
        str(r or "").upper(), "gray")


def _action_color(action: str) -> str:
    a = str(action or "").upper()
    if "SELL" in a:
        return "red"
    if "TRIM" in a:
        return "orange"
    if "CAUTION" in a or "cautious" in a.lower():
        return "orange"
    if "HOLD" in a or "BUY" in a or "ADD" in a:
        return "green"
    return "gray"


# --------------------------------------------------------------------------
# Persistent top strip — always visible above the tabs
# --------------------------------------------------------------------------
def render_top_strip():
    """Quick-look strip: regime, overnight gap prior, portfolio P&L, data age."""
    try:
        regime = tools.get_market_regime()
    except Exception:
        regime = {"regime": "?", "exposure_multiplier": 1.0}
    try:
        overnight = tools.get_overnight_signals()
    except Exception:
        overnight = {}
    try:
        pf = tools.get_user_portfolio()
    except Exception:
        pf = {}
    try:
        sent = tools.get_scored_sentiment(hours_macro=24)
    except Exception:
        sent = {}

    c1, c2, c3, c4, c5 = st.columns([1.2, 1.3, 1.5, 1.5, 1.5])
    with c1:
        r = regime.get("regime", "?")
        st.markdown(
            f"**Regime** :{_regime_color(r)}[{r}]  \n"
            f"Exposure ×{regime.get('exposure_multiplier', 1.0):.2f}"
        )
    with c2:
        gp = overnight.get("gap_prior") or {}
        bias = gp.get("expected_gap_pct")
        conf = gp.get("bias", "")
        if bias is None:
            st.markdown("**Overnight gap**  \nn/a")
        else:
            arrow = "▲" if bias > 0 else "▼" if bias < 0 else "–"
            color = "green" if bias > 0.2 else "red" if bias < -0.2 else "gray"
            st.markdown(
                f"**Overnight gap**  \n"
                f":{color}[{arrow} {bias:+.2f}%]  *{conf}*"
            )
    with c3:
        cost = pf.get("total_cost_pkr") or 0
        mv = pf.get("total_market_value_pkr") or 0
        pnl = pf.get("total_unrealized_pnl_pkr") or 0
        ret_pct = pf.get("total_unrealized_pnl_pct")
        if cost == 0:
            st.markdown("**Portfolio**  \nEmpty — add positions")
        else:
            color = "green" if pnl >= 0 else "red"
            ret_str = _pct_raw(ret_pct) if ret_pct is not None else ""
            st.markdown(
                f"**Portfolio**  \n"
                f":{color}[{_pkr(pnl)} PKR  {ret_str}]  \n"
                f"on {_pkr(cost)} cost"
            )
    with c4:
        macro = (sent.get("macro") or {}) if isinstance(sent, dict) else {}
        score = macro.get("score")
        n = macro.get("n", 0)
        if score is None or n == 0:
            st.markdown("**News (24h)**  \nn/a")
        else:
            color = ("green" if score > 0.1
                     else "red" if score < -0.1 else "gray")
            st.markdown(
                f"**News (24h)**  \n"
                f":{color}[{score:+.2f}]  n={n}"
            )
    with c5:
        fresh = dash.data_freshness()
        pred = fresh.get("Predictions log", {})
        over = fresh.get("Overnight globals", {})
        pred_age = (pred.get("age_hours")
                    if isinstance(pred, dict) else None)
        over_age = (over.get("age_hours")
                    if isinstance(over, dict) else None)
        st.markdown(
            "**Data freshness**  \n"
            f"Predictions: {pred_age}h  \n"
            f"Overnight:  {over_age}h"
        )

    st.divider()


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown("## PSX Advisor")
        st.caption("Plan D Phase 1 — monthly momentum w/ defensive overlay")
        st.divider()

        # -- Provider picker
        st.markdown("### Chat model")
        provider_labels = {
            "github": "GitHub (free)",
            "claude": "Claude (paid)",
            "gemini": "Gemini (paid)",
        }
        providers = list(provider_labels.keys())
        current_idx = providers.index(st.session_state.provider) \
            if st.session_state.provider in providers else 0
        provider = st.radio(
            "Provider", providers, index=current_idx, horizontal=True,
            format_func=lambda p: provider_labels[p],
            label_visibility="collapsed", key="provider_radio",
        )
        st.session_state.provider = provider

        # When using `key=...` Streamlit reads/writes session_state
        # automatically, so we deliberately omit `value=` to avoid the
        # "widget created with a default value but also had its value set
        # via the Session State API" warning.
        if provider == "github":
            st.text_input(
                "GitHub token (PAT w/ models:read)",
                type="password", key="github_key",
                help="Fine-grained PAT with 'models:read' scope. "
                     "Or set GITHUB_TOKEN in .env.",
            )
            st.selectbox(
                "GitHub model", GITHUB_MODEL_CHOICES,
                key="github_model",
                help="Low tier (gpt-4o-mini, gpt-4.1-mini, Llama) = 15 RPM. "
                     "High tier (gpt-4o, gpt-4.1) = 10 RPM / 50 RPD.",
            )
        elif provider == "claude":
            st.text_input("Anthropic API key",
                          type="password", key="claude_key",
                          help="Or set ANTHROPIC_API_KEY in .env.")
            st.text_input("Claude model", key="claude_model")
        else:
            st.text_input("Google API key",
                          type="password", key="gemini_key",
                          help="Or set GOOGLE_API_KEY in .env.")
            st.text_input("Gemini model", key="gemini_model")

        st.divider()

        # -- Data controls
        st.markdown("### Data")
        if st.button("Pull latest from GitHub", use_container_width=True,
                     help="Runs `git pull` to fetch data committed by the "
                          "daily CI workflows, then clears the price cache."):
            _do_git_pull()

        if st.button("Refresh prices from PSX DPS",
                      use_container_width=True,
                      help="Pulls today's OHLCV for the 15-stock universe "
                           "directly from PSX DPS (bypasses GitHub). Use "
                           "right after market close if the EOD workflow "
                           "hasn't run yet. Takes ~60 seconds."):
            _do_backfill()

        if st.button("Clear in-memory cache", use_container_width=True,
                      help="Forces tools.py to reload parquet files. Use "
                           "after manually editing data/ on disk."):
            tools.refresh_cache()
            st.success("Cache cleared.")
            time.sleep(0.4)
            st.rerun()

        # -- Freshness panel
        with st.expander("Data freshness", expanded=False):
            for name, info in dash.data_freshness().items():
                if not info.get("exists"):
                    st.markdown(f"- **{name}** — _missing_")
                    continue
                age = info.get("age_hours", 0)
                color = ("green" if age < 6 else "orange"
                         if age < 24 else "red")
                st.markdown(
                    f"- **{name}**  \n"
                    f"  :{color}[{info['updated_at']}]  ({age}h ago)"
                )

        st.divider()
        st.caption(
            "Data updates are committed to GitHub by the workflows in "
            "`.github/workflows/`. Press 'Pull latest' to sync locally."
        )


def _do_backfill():
    """Run scripts/backfill.py in-process so Streamlit shows progress and
    catches any exception locally."""
    with st.spinner("Pulling today's OHLCV from PSX DPS (may take ~60s)…"):
        try:
            from connectors.psx_historical import PSXHistoricalConnector
            from config.universe import symbols as universe_symbols
            from data.store import save_ohlcv
        except Exception as e:
            st.error(f"Import failed: {type(e).__name__}: {e}")
            return
        conn = PSXHistoricalConnector()
        probe = conn.test()
        if not probe.ok:
            st.error(f"PSX DPS unreachable: {probe.error}")
            return

        ok, fail = 0, 0
        last_dates: list[str] = []
        bar = st.progress(0.0)
        syms = universe_symbols()
        for i, sym in enumerate(syms):
            try:
                rows = conn.fetch_symbol(sym)
                if rows:
                    save_ohlcv(sym, rows)
                    ok += 1
                    last_dates.append(max(r["date"] for r in rows))
                else:
                    fail += 1
            except Exception:
                fail += 1
            bar.progress((i + 1) / len(syms))
        bar.empty()

        tools.refresh_cache()
        if last_dates:
            freshest = max(last_dates)
            st.success(
                f"Refreshed {ok}/{len(syms)} symbols. "
                f"Latest date on disk: **{freshest}**."
                + (f"  ({fail} failed.)" if fail else "")
            )
        else:
            st.error("No symbols refreshed.")
        time.sleep(0.8)
        st.rerun()


def _do_git_pull():
    import subprocess
    # Use GITHUB_TOKEN from env to auth against private repo (matches launcher)
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        ).stdout.strip() or "main"
        if tok and remote.startswith("https://"):
            auth_url = "https://x-access-token:" + tok + "@" + remote[len("https://"):]
            cmd = ["git", "-c", "credential.helper=",
                   "pull", auth_url, branch, "--ff-only", "--no-rebase"]
        else:
            cmd = ["git", "pull", "--ff-only", "--no-rebase"]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=60, cwd=str(PROJECT_ROOT))
        if result.returncode == 0:
            tools.refresh_cache()
            out = (result.stdout or "").strip()
            if "Already up to date" in out or "up-to-date" in out:
                st.info("Already up to date.")
            else:
                st.success(f"Pulled:\n```\n{out[-400:]}\n```")
            time.sleep(0.5)
            st.rerun()
        else:
            err = (result.stderr or result.stdout or "").strip()
            # Scrub token from output before display.
            if tok:
                err = err.replace(tok, "***")
            st.error(f"git pull failed:\n```\n{err[-400:]}\n```")
    except subprocess.TimeoutExpired:
        st.error("git pull timed out.")
    except FileNotFoundError:
        st.error("`git` not found in PATH.")
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# DASHBOARD TAB
# --------------------------------------------------------------------------
def render_dashboard_tab():
    st.markdown("### Morning brief")
    st.caption(
        "Everything you want to know before the PSX opens. Pulled from the "
        "same tools the LLM uses — there's one source of truth."
    )

    brief = dash.morning_brief()

    # ---------------- Row 1: regime + strategy signal + overnight
    c1, c2, c3 = st.columns([1, 1, 1.3])
    with c1:
        _card_regime(brief.get("regime", {}))
    with c2:
        _card_strategy(brief.get("strategy_signal", {}))
    with c3:
        _card_overnight(brief.get("overnight", {}))

    # ---------------- Row 2: today's top picks (from stored predictions)
    st.markdown("#### Today's actionable picks")
    preds = brief.get("predictions", {})
    if "error" in preds:
        st.info(preds["error"])
    else:
        actionable = [p for p in preds.get("predictions", [])
                      if p.get("suggested_action") in ("BUY", "ADD")
                      and p.get("clears_cost_threshold")]
        if not actionable:
            st.info(
                f"No BUY/ADD names clear the {preds.get('minimum_gross_for_trade_pct', '?')}% "
                f"cost+edge threshold today."
            )
        else:
            df = pd.DataFrame(actionable)[[
                "symbol", "sector", "conviction",
                "entry_price_pkr", "suggested_stop_pkr", "suggested_target_pkr",
                "expected_gross_5d_pct", "expected_net_5d_pct",
                "rationale",
            ]]
            df.columns = ["Sym", "Sector", "Conv", "Entry", "Stop", "Target",
                          "Gross %", "Net %", "Why"]
            st.dataframe(df, hide_index=True, use_container_width=True)
        st.caption(
            f"As of {preds.get('as_of', '?')} | round-trip cost "
            f"{preds.get('round_trip_cost_pct', '?')}% | need >= "
            f"{preds.get('minimum_gross_for_trade_pct', '?')}% gross to be viable"
        )

    # ---------------- Row 3: portfolio snapshot + journal + movers + sentiment
    st.markdown("#### Portfolio & market movers")
    c1, c2 = st.columns([1, 1])
    with c1:
        _card_portfolio(brief.get("portfolio", {}),
                         brief.get("journal_stats", {}))
    with c2:
        _card_movers(brief.get("universe_movers", {}))

    # ---------------- Row 4: news tilt + prediction accuracy
    c1, c2 = st.columns([1.2, 1])
    with c1:
        _card_sentiment(brief.get("sentiment", {}))
    with c2:
        _card_prediction_accuracy(brief.get("prediction_accuracy", {}))

    # ---------------- Row 5: top value picks (slow signal)
    _card_value_book(brief.get("value_book", {}))


def _card_value_book(vb: dict):
    if "error" in vb:
        st.container(border=True).warning(
            f"Value book: {vb['error']}  (run "
            f"`python -m connectors.yfinance_fundamentals`)"
        )
        return
    rows = vb.get("rows") or []
    if not rows:
        return
    counts = vb.get("signal_counts", {})
    with st.container(border=True):
        st.markdown(
            f"**Intrinsic-value scan** (slow 6-24m signal) — "
            f":green[BUY {counts.get('BUY_VALUE', 0)}] · "
            f"FAIR {counts.get('FAIR', 0)} · "
            f":red[SELL {counts.get('SELL_VALUE', 0)}]"
        )
        st.caption(
            "Most-undervalued names by sector-aware fair-value model. "
            "Use as a *holding-period* tailwind, not a 5-day entry signal."
        )
        # Top-3 most-undervalued AND top-3 most-overvalued
        ups = [r for r in rows
                if r.get("upside_pct") is not None
                and r.get("signal") != "NO_SIGNAL"]
        top_buys = ups[:3]
        top_sells = ups[-3:][::-1]
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**Most undervalued**")
            for r in top_buys:
                st.markdown(
                    f"- **{r['symbol']}** ({r.get('sector','?')[:14]})  "
                    f"`{r['current_price']}` → fair `{r.get('fair_value')}`  "
                    f"**:green[{r.get('upside_pct'):+.1f}%]**  "
                    f"_{r.get('confidence','?')}_"
                )
        with cc2:
            st.markdown("**Most overvalued**")
            for r in top_sells:
                st.markdown(
                    f"- **{r['symbol']}** ({r.get('sector','?')[:14]})  "
                    f"`{r['current_price']}` → fair `{r.get('fair_value')}`  "
                    f"**:red[{r.get('upside_pct'):+.1f}%]**  "
                    f"_{r.get('confidence','?')}_"
                )


def _card_regime(r: dict):
    if "error" in r:
        st.container(border=True).error(f"Regime: {r['error']}")
        return
    regime = r.get("regime", "?")
    color = _regime_color(regime)
    with st.container(border=True):
        st.markdown(f"**Market regime** :{color}[{regime}]")
        st.caption(r.get("reason", ""))
        ind = r.get("indicators", {})
        cc1, cc2 = st.columns(2)
        cc1.metric("5d avg ret", _pct(ind.get("universe_ret_5d")))
        cc1.metric("150d mom",   _pct(ind.get("universe_150d_log_ret")))
        cc2.metric("21d avg ret", _pct(ind.get("universe_ret_21d")))
        breadth = ind.get("breadth_pct_up_today")
        cc2.metric("Breadth up",
                    f"{breadth}%" if breadth is not None else "n/a")
        st.caption(
            f"Exposure multiplier ×{r.get('exposure_multiplier', 1.0):.2f}")


def _card_strategy(s: dict):
    if "error" in s:
        st.container(border=True).error(f"Strategy: {s['error']}")
        return
    with st.container(border=True):
        st.markdown(f"**Strategy signal** — {s.get('recommended_action', '?')}")
        st.caption(s.get("rationale", ""))
        picks = s.get("selected_symbols") or []
        would = s.get("would_pick_if_market_filter_off") or []
        if picks:
            st.markdown(f"**Top-{s.get('top_n', 5)} picks:**  {', '.join(picks)}")
        elif would:
            st.markdown(
                f"**Would-be picks (filter off):** {', '.join(would)}")
        else:
            st.markdown("**No picks today.**")


def _card_overnight(o: dict):
    if "error" in o:
        st.container(border=True).error(f"Overnight: {o['error']}")
        return
    with st.container(border=True):
        gp = o.get("gap_prior") or {}
        bias = gp.get("expected_gap_pct")
        classification = gp.get("bias", "")
        color = ("green" if (bias or 0) > 0.2
                 else "red" if (bias or 0) < -0.2 else "gray")
        if bias is not None:
            st.markdown(
                f"**Overnight gap prior** "
                f":{color}[{bias:+.2f}%]  *{classification}*"
            )
        else:
            st.markdown("**Overnight gap prior** n/a")
        st.caption(gp.get("reasoning", "")[:300])
        sigs = o.get("signals", {}) or {}
        rows = []
        for k in ("sp500", "vix", "nikkei", "hangseng", "ftse", "dxy", "eem"):
            s = sigs.get(k) or {}
            if not s:
                continue
            rows.append({
                "Market": k.upper(),
                "Close":  s.get("close"),
                "1d %":   s.get("ret_1d_pct"),
                "5d %":   s.get("ret_5d_pct"),
            })
        if rows:
            df = pd.DataFrame(rows)
            for col in ("1d %", "5d %"):
                df[col] = df[col].map(
                    lambda v: f"{v:+.2f}%" if v is not None else "n/a")
            st.dataframe(df, hide_index=True, use_container_width=True,
                          height=min(35 * (len(rows) + 1), 260))


def _card_portfolio(pf: dict, js: dict):
    with st.container(border=True):
        st.markdown("**Portfolio**")
        if pf.get("note") or (pf.get("position_count", 0) == 0
                               and not pf.get("positions")):
            st.caption(pf.get("note") or "No positions yet.")
        else:
            cost = pf.get("total_cost_pkr", 0)
            mv = pf.get("total_market_value_pkr", 0)
            pnl = pf.get("total_unrealized_pnl_pkr", 0)
            ret = pf.get("total_unrealized_pnl_pct")
            cc1, cc2 = st.columns(2)
            cc1.metric("Cost", f"{cost:,.0f} PKR")
            cc1.metric("Market value", f"{mv:,.0f} PKR")
            cc2.metric("Unrealized P&L", f"{pnl:+,.0f} PKR",
                        delta=f"{ret:+.2f}%" if ret is not None else None)
            cc2.metric("Positions", pf.get("position_count", 0))

        st.markdown("**Journal (realized)**")
        if js.get("count", 0) == 0:
            st.caption("No closed trades yet.")
        else:
            cc1, cc2 = st.columns(2)
            cc1.metric("Net realized",
                        f"{js.get('total_net_pnl_pkr', 0):+,.0f} PKR")
            cc1.metric("Win rate", f"{js.get('win_rate_pct', 0):.1f}%")
            cc2.metric("Avg winner",
                        f"{js.get('avg_winner_pct', 0):+.2f}%")
            cc2.metric("Avg loser",
                        f"{js.get('avg_loser_pct', 0):+.2f}%")


def _card_movers(m: dict):
    with st.container(border=True):
        st.markdown("**Universe movers (today)**")
        if "error" in m:
            st.caption(m["error"])
            return
        gainers = m.get("gainers", [])
        losers = m.get("losers", [])
        cc1, cc2 = st.columns(2)
        with cc1:
            st.caption(":green[Top gainers]")
            for g in gainers:
                st.markdown(
                    f"- **{g['symbol']}** {_pct_raw(g.get('ret_1d_pct'))}"
                )
        with cc2:
            st.caption(":red[Top losers]")
            for l in losers:
                st.markdown(
                    f"- **{l['symbol']}** {_pct_raw(l.get('ret_1d_pct'))}"
                )


def _card_sentiment(s: dict):
    with st.container(border=True):
        st.markdown("**News sentiment (scored)**")
        if "error" in s:
            st.caption(s["error"])
            return
        macro = (s.get("macro") or {})
        score = macro.get("score")
        n = macro.get("n", 0)
        if score is None or n == 0:
            st.caption("No recent scored headlines.")
        else:
            color = ("green" if score > 0.1
                     else "red" if score < -0.1 else "gray")
            by_cat = macro.get("by_category") or {}
            cat_str = "  ".join(f"{k}={v:+.2f}" for k, v in by_cat.items())
            st.markdown(
                f"Macro tilt (24h): :{color}[{score:+.2f}]  *(n={n})*"
            )
            if cat_str:
                st.caption(f"by category — {cat_str}")
        st.markdown("**Top 5 impactful headlines**")
        for h in (s.get("top_headlines") or [])[:5]:
            emoji = ("+" if h["sentiment"] > 0.15
                     else "-" if h["sentiment"] < -0.15 else "·")
            st.markdown(
                f"- `{emoji} {h['sentiment']:+.2f}` "
                f"**{h.get('category', '')}** — {h['title'][:120]}"
            )
            if h.get("one_liner"):
                st.caption(h["one_liner"])


def _card_prediction_accuracy(pa: dict):
    with st.container(border=True):
        st.markdown("**Prediction accuracy (rolling)**")
        if "error" in pa:
            st.caption(pa["error"])
            return
        if pa.get("scored_count", 0) == 0:
            st.caption(pa.get("note", "No scored predictions yet."))
            return
        cc1, cc2 = st.columns(2)
        cc1.metric("Direction hit (gross)",
                    f"{pa.get('direction_hit_rate_gross_pct', 0):.1f}%")
        cc1.metric("Inside range",
                    f"{pa.get('inside_range_hit_rate_pct', 0):.1f}%")
        cc2.metric("Avg expected",
                    f"{pa.get('avg_expected_return_pct', 0):+.2f}%")
        cc2.metric("Avg actual (net)",
                    f"{pa.get('avg_actual_return_net_pct', 0):+.2f}%")
        st.caption(f"n = {pa.get('scored_count', 0)} scored predictions")


# --------------------------------------------------------------------------
# PORTFOLIO TAB
# --------------------------------------------------------------------------
def render_portfolio_tab():
    st.markdown("### Your portfolio")
    st.caption(
        "Live P&L, sector allocation, trailing-stop diagnostics, and a "
        "one-click close-to-journal flow. The advisor reads these positions "
        "via `get_user_portfolio`."
    )

    # --- Add position
    with st.expander("Add a new position", expanded=False):
        universe = tools.list_universe()["symbols"]
        syms = [s["symbol"] for s in universe]
        with st.form("add_position_form", clear_on_submit=True):
            c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1.2])
            with c1:
                sym = st.selectbox("Symbol", syms)
            with c2:
                ent_px = st.number_input("Entry price (PKR)", min_value=0.01,
                                          step=1.0, format="%.2f")
            with c3:
                qty = st.number_input("Quantity", min_value=1.0, step=1.0,
                                       format="%.0f")
            with c4:
                ent_dt = st.date_input("Entry date",
                                        value=datetime.now().date())
            notes = st.text_input("Notes (optional)", max_chars=200)
            submitted = st.form_submit_button("Add position", type="primary")
            if submitted and sym and ent_px > 0 and qty > 0:
                add_position(sym, ent_px, qty, str(ent_dt), notes)
                st.success(f"Added {qty:g} × {sym} @ {ent_px:.2f}")
                time.sleep(0.4)
                st.rerun()

    # --- CSV import / export
    with st.expander("Import / export CSV", expanded=False):
        _render_csv_io()

    positions = load_user_portfolio()
    if not positions:
        st.info("No positions yet. Add one above, or ask the advisor for "
                "BUY ideas in the Chat tab.")
        _render_journal_section()
        return

    analyzed = recs.analyze_all_positions(positions)
    summary = recs.portfolio_summary(analyzed)

    # --- Summary strip
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Positions", summary["position_count"])
    c2.metric("Cost (PKR)", f"{summary['total_cost_pkr']:,.0f}")
    c3.metric("Market value", f"{summary['total_market_value_pkr']:,.0f}")
    c4.metric("Unrealized P&L",
               f"{summary['unrealized_pnl_pkr']:+,.0f} PKR",
               delta=f"{summary['unrealized_pnl_pct']:+.2f}%")
    ac = summary["action_counts"]
    c5.markdown(
        f"**Signals**<br>"
        f"HOLD: {ac['HOLD']} • SELL: {ac['SELL']} • "
        f"TRIM: {ac['TRIM']} • CAUTION: {ac['CAUTION']}",
        unsafe_allow_html=True,
    )

    # --- Sector allocation & concentration
    _render_allocation(analyzed)

    st.divider()

    # --- Per-position cards (sorted by P&L %, worst first so action items lead)
    st.markdown("### Per-position analysis")
    analyzed_sorted = sorted(
        analyzed,
        key=lambda r: (r.get("unrealized_return_pct")
                        if r.get("unrealized_return_pct") is not None else 0.0),
    )
    # But positions queued for close should stay visible — use the real index.
    positions_index = {id(r): i for i, r in enumerate(analyzed)}
    for row in analyzed_sorted:
        _render_position_card(positions_index[id(row)], row)

    # --- Close-position modal
    if st.session_state._pending_close is not None:
        _render_close_modal(st.session_state._pending_close, analyzed)

    st.divider()
    _render_journal_section()


def _render_csv_io():
    positions = load_user_portfolio()
    if positions:
        df = pd.DataFrame(positions)[
            ["symbol", "entry_date", "entry_price", "quantity", "notes"]
        ]
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        st.download_button(
            "Download portfolio as CSV", buf.getvalue(),
            file_name="psx_portfolio.csv", mime="text/csv",
            use_container_width=True,
        )
    uploaded = st.file_uploader(
        "Import positions (CSV with columns: symbol, entry_date, entry_price, "
        "quantity, notes)", type=["csv"], key="portfolio_csv")
    if uploaded is not None:
        try:
            up = pd.read_csv(uploaded)
        except Exception as e:
            st.error(f"Could not parse CSV: {e}")
            return
        required = {"symbol", "entry_price", "quantity"}
        missing = required - set(up.columns.str.lower())
        if missing:
            st.error(f"CSV missing required columns: {missing}")
            return
        up.columns = [c.lower() for c in up.columns]
        if "entry_date" not in up.columns:
            up["entry_date"] = datetime.now().strftime("%Y-%m-%d")
        if "notes" not in up.columns:
            up["notes"] = ""
        if st.button(f"Replace portfolio with {len(up)} rows from CSV",
                      type="primary"):
            save_user_portfolio(up.to_dict(orient="records"))
            st.success(f"Imported {len(up)} positions.")
            time.sleep(0.5)
            st.rerun()


def _render_allocation(analyzed: list[dict]):
    """Sector allocation bar + concentration metrics."""
    rows = []
    total_mv = 0.0
    for r in analyzed:
        mv = r.get("market_value_pkr") or 0
        total_mv += mv
        rows.append({
            "symbol": r["symbol"],
            "sector": sector_of(r["symbol"]) or "Other",
            "mv": mv,
        })
    if total_mv <= 0:
        return
    df = pd.DataFrame(rows)
    df["weight_pct"] = df["mv"] / total_mv * 100

    c1, c2 = st.columns([2, 1])
    with c1:
        sector_df = (df.groupby("sector")["weight_pct"].sum()
                     .sort_values(ascending=False))
        st.markdown("**Sector allocation**")
        st.bar_chart(sector_df, height=220)
    with c2:
        weights = df["weight_pct"].values
        hhi = float((weights ** 2).sum())
        n_eff = 10000.0 / hhi if hhi > 0 else 0
        top1 = df["weight_pct"].max()
        st.markdown("**Concentration risk**")
        st.metric("Largest position", f"{top1:.1f}%")
        st.metric("HHI", f"{hhi:.0f}",
                    help="Herfindahl index (sum of squared weights in %). "
                         "Higher = more concentrated. <1500 healthy, "
                         ">2500 concentrated, >5000 extreme.")
        st.metric("Effective # positions", f"{n_eff:.1f}",
                    help="= 10000 / HHI. If you had equal weights this "
                         "would equal your real position count.")


def _render_position_card(idx: int, row: dict):
    sym = row["symbol"]
    if "error" in row:
        with st.container(border=True):
            st.error(f"{sym}: {row['error']}")
            if st.button(f"Remove {sym}", key=f"rm_err_{idx}"):
                remove_position(idx)
                st.rerun()
        return

    action = row.get("suggested_action", "HOLD")
    color = _action_color(action)
    with st.container(border=True):
        hdr_c1, hdr_c2, hdr_c3 = st.columns([2.4, 1.6, 1])
        with hdr_c1:
            st.markdown(
                f"### {sym} — :{color}[{action}]  "
                f"`{sector_of(sym) or '—'}`"
            )
            st.caption(row.get("reasoning", ""))
        with hdr_c2:
            pnl = row.get("unrealized_pnl_pkr")
            ret = row.get("unrealized_return_pct")
            st.metric(
                "Unrealized P&L",
                f"{pnl:+,.0f} PKR" if pnl is not None else "—",
                delta=f"{ret:+.2f}%" if ret is not None else None,
            )
        with hdr_c3:
            if st.button("Close…", key=f"close_{idx}",
                          use_container_width=True, type="primary"):
                st.session_state._pending_close = idx
                st.rerun()
            if st.button("Remove", key=f"rm_{idx}",
                          use_container_width=True):
                remove_position(idx)
                st.rerun()

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Entry", f"{row.get('entry_price_pkr', 0):.2f} PKR")
            st.caption(f"on {row.get('entry_date', '—')} "
                        f"({row.get('days_held', '—')} days held)")
        with c2:
            st.metric("Current", f"{row.get('current_price_pkr', 0):.2f} PKR")
            peak = row.get('peak_since_entry_pkr', 0)
            dd = row.get('drawdown_from_peak_pct')
            st.caption(f"peak {peak:.2f}  ({_pct_raw(dd)} from peak)")
        with c3:
            stop = row.get('suggested_trailing_stop_pkr', 0)
            cur = row.get('current_price_pkr', 0) or 0
            cushion = ((cur / stop) - 1) * 100 if stop > 0 else None
            st.metric("Stop (trailing)", f"{stop:.2f} PKR")
            st.caption(
                f"{row.get('suggested_trailing_stop_pct', 12)}% below peak  "
                f"({_pct_raw(cushion)} cushion)"
            )
        with c4:
            rank = row.get("momentum_rank")
            st.metric("Momentum rank", f"#{rank}" if rank else "—")
            in_top = row.get("in_current_top5")
            st.caption(f"{'in' if in_top else 'not in'} today's top-5")

        # Mini price chart with entry / stop / current markers
        with st.expander(f"Price chart — {sym}", expanded=False):
            _render_position_chart(row)


def _render_position_chart(row: dict):
    sym = row["symbol"]
    try:
        h = tools.get_price_history(sym, days=120)
    except Exception as e:
        st.warning(f"Chart unavailable: {e}")
        return
    bars = h.get("bars") or []
    if not bars:
        st.caption("No price history available.")
        return
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[["close"]]
    df.columns = ["Close"]
    entry = row.get("entry_price_pkr")
    stop = row.get("suggested_trailing_stop_pkr")
    peak = row.get("peak_since_entry_pkr")
    if entry is not None:
        df["Entry"] = entry
    if stop is not None:
        df["Stop"] = stop
    if peak is not None:
        df["Peak"] = peak
    st.line_chart(df, height=260)


def _render_close_modal(idx: int, analyzed: list[dict]):
    if not (0 <= idx < len(analyzed)):
        st.session_state._pending_close = None
        return
    row = analyzed[idx]
    sym = row["symbol"]
    current = row.get("current_price_pkr", 0) or 0
    with st.container(border=True):
        st.markdown(f"### Close position: {sym}")
        with st.form(f"close_form_{idx}"):
            c1, c2, c3 = st.columns(3)
            with c1:
                exit_px = st.number_input(
                    "Exit price (PKR)", min_value=0.01,
                    value=float(current) if current else 1.0,
                    step=1.0, format="%.2f",
                )
            with c2:
                exit_dt = st.date_input("Exit date",
                                         value=datetime.now().date())
            with c3:
                reason = st.selectbox(
                    "Reason",
                    ["target", "stop", "signal_decay", "manual", "time_exit"],
                )
            notes = st.text_input("Exit notes (optional)", max_chars=500)
            c_ok, c_cancel = st.columns(2)
            with c_ok:
                ok = st.form_submit_button("Confirm close", type="primary",
                                            use_container_width=True)
            with c_cancel:
                cancel = st.form_submit_button("Cancel",
                                                use_container_width=True)
            if ok:
                entry = close_position(
                    idx, exit_price=exit_px, exit_date=str(exit_dt),
                    exit_reason=reason, exit_notes=notes,
                )
                if "error" in entry:
                    st.error(entry["error"])
                else:
                    st.success(
                        f"Closed {sym}: gross {entry['gross_return_pct']:+.2f}% / "
                        f"net {entry['net_return_pct']:+.2f}%  "
                        f"({entry['net_pnl_pkr']:+,.0f} PKR net)"
                    )
                    st.session_state._pending_close = None
                    time.sleep(0.8)
                    st.rerun()
            if cancel:
                st.session_state._pending_close = None
                st.rerun()


def _render_journal_section():
    """Closed-trade history with realized P&L."""
    st.markdown("### Trade journal (realized)")
    trades = load_journal()
    if not trades:
        st.info("No closed trades yet. Close a position above to record "
                "realized P&L here (gross and net of PSX costs).")
        return

    stats = journal_stats()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Trades", stats["count"])
    c2.metric("Win rate", f"{stats['win_rate_pct']:.1f}%")
    c3.metric("Net realized P&L",
               f"{stats['total_net_pnl_pkr']:+,.0f} PKR")
    c4.metric("Avg winner", f"{stats['avg_winner_pct']:+.2f}%")
    c5.metric("Avg loser",  f"{stats['avg_loser_pct']:+.2f}%")

    df = pd.DataFrame(trades)
    view = df[[
        "symbol", "entry_date", "exit_date", "hold_days",
        "entry_price", "exit_price", "quantity",
        "gross_return_pct", "net_return_pct",
        "gross_pnl_pkr", "net_pnl_pkr", "exit_reason",
    ]].copy()
    view.columns = ["Sym", "Entry date", "Exit date", "Days held",
                    "Entry", "Exit", "Qty",
                    "Gross %", "Net %", "Gross PKR", "Net PKR", "Reason"]
    st.dataframe(view.iloc[::-1], hide_index=True, use_container_width=True)

    # CSV export of the journal
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button(
        "Download journal as CSV", buf.getvalue(),
        file_name="psx_trade_journal.csv", mime="text/csv",
    )


# --------------------------------------------------------------------------
# WATCHLIST TAB
# --------------------------------------------------------------------------
def render_watchlist_tab():
    st.markdown("### Watchlist")
    st.caption(
        "Symbols you want to track without holding. The advisor reads these "
        "via `get_watchlist` so chat answers are aware of your focus list."
    )

    with st.expander("Add a symbol", expanded=False):
        universe = tools.list_universe()["symbols"]
        syms = [s["symbol"] for s in universe]
        current = {it["symbol"] for it in load_watchlist()}
        available = [s for s in syms if s not in current]
        if not available:
            st.info("Every universe symbol is already on your watchlist.")
        else:
            with st.form("add_watch_form", clear_on_submit=True):
                c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
                with c1:
                    sym = st.selectbox("Symbol", available)
                with c2:
                    target = st.number_input("Target price (PKR)",
                                              min_value=0.0, step=1.0,
                                              value=0.0, format="%.2f",
                                              help="Optional. 0 = none.")
                with c3:
                    above = st.number_input("Alert above",
                                             min_value=0.0, step=1.0,
                                             value=0.0, format="%.2f",
                                             help="Optional price alert.")
                with c4:
                    below = st.number_input("Alert below",
                                             min_value=0.0, step=1.0,
                                             value=0.0, format="%.2f",
                                             help="Optional price alert.")
                note = st.text_input("Note (optional)", max_chars=200)
                ok = st.form_submit_button("Add to watchlist",
                                            type="primary")
                if ok and sym:
                    add_to_watchlist(
                        sym,
                        target_price=target or None,
                        alert_above=above or None,
                        alert_below=below or None,
                        note=note,
                    )
                    st.success(f"Added {sym}")
                    time.sleep(0.4)
                    st.rerun()

    # Live watchlist table
    data = tools.get_watchlist()
    items = data.get("items") or []
    if not items:
        st.info("Watchlist is empty.")
        return

    rows = []
    for it in items:
        target = it.get("target_price_pkr")
        upside = it.get("upside_to_target_pct")
        hit_up = it.get("alert_above_hit")
        hit_dn = it.get("alert_below_hit")
        alert = (
            "above" if hit_up
            else "below" if hit_dn
            else ""
        )
        rows.append({
            "Symbol": it["symbol"],
            "Added": it.get("added_date"),
            "Last": it.get("last_price_pkr"),
            "1d %": it.get("ret_1d_pct"),
            "5d %": it.get("ret_5d_pct"),
            "Mom #": it.get("momentum_rank"),
            "Target": target,
            "Upside %": upside,
            "Alert": alert,
            "Note": (it.get("note") or "")[:60],
        })
    df = pd.DataFrame(rows)

    def _style(r):
        styles = [""] * len(r)
        if r["Alert"] in ("above", "below"):
            styles = [
                "background-color: #5a3f15; color: white"
            ] * len(r)
        return styles

    st.dataframe(df.style.apply(_style, axis=1), hide_index=True,
                  use_container_width=True)

    # Remove controls
    st.markdown("**Manage**")
    cols = st.columns(min(6, len(items)))
    for i, it in enumerate(items):
        with cols[i % len(cols)]:
            if st.button(f"Remove {it['symbol']}", key=f"wl_rm_{i}",
                          use_container_width=True):
                remove_from_watchlist(it["symbol"])
                st.rerun()


# --------------------------------------------------------------------------
# SCANNER TAB
# --------------------------------------------------------------------------
def render_scanner_tab():
    st.markdown("### Market scanner")
    st.caption("Universe ranked by 150-day momentum; today's Phase 1 picks "
                "highlighted in green. Would-be picks (when the market filter "
                "is off) in amber.")

    try:
        sig = tools.get_strategy_signal()
        regime = tools.get_market_regime()
    except Exception as e:
        st.error(f"Scanner unavailable: {e}")
        return

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        st.metric("Phase 1 recommendation",
                   sig.get("recommended_action", "—"))
        st.caption(sig.get("rationale", ""))
    with c2:
        picks = sig.get("selected_symbols") or []
        would = sig.get("would_pick_if_market_filter_off") or []
        if picks:
            st.markdown("**Today's picks:** " + ", ".join(picks))
        elif would:
            st.markdown("**Would-be picks (filter off):** "
                         + ", ".join(would))
        else:
            st.markdown("**No picks today.**")
    with c3:
        st.metric(f"Regime: {regime.get('regime')}",
                   f"×{regime.get('exposure_multiplier'):.2f}")
        st.caption(regime.get("reason", ""))

    st.divider()

    df = recs.scanner_table()
    if df.empty:
        st.warning("No ranking data available.")
        return

    def _style(row):
        if row.get("phase1_pick"):
            return ["background-color: #1f4f2f; color: white"] * len(row)
        if row.get("would_be_pick"):
            return ["background-color: #4f3f1f; color: white"] * len(row)
        return [""] * len(row)

    display = df[[
        "rank", "symbol", "sector", "mom_150d_log_ret",
        "rvol_20d_ann", "vol_percentile", "passes_vol_filter",
        "phase1_pick", "would_be_pick",
    ]].copy()
    display.columns = [
        "Rank", "Symbol", "Sector", "Mom (150d log)",
        "RVol (20d ann)", "Vol %ile", "Vol filter OK",
        "Today pick", "Would-be pick",
    ]
    st.dataframe(
        display.style.apply(lambda r: _style(df.iloc[r.name]), axis=1),
        hide_index=True, use_container_width=True,
    )

    st.divider()

    st.markdown("### Top buy ideas (detailed)")
    ideas = recs.top_buys(max_ideas=5)
    if ideas.get("cautious_note"):
        st.warning(ideas["cautious_note"])
    rows = ideas.get("ideas", [])
    if not rows:
        st.info("No buy candidates pass the filters today.")
        return
    idf = pd.DataFrame(rows)
    idf.columns = [c.replace("_", " ").title() for c in idf.columns]
    st.dataframe(idf, hide_index=True, use_container_width=True)


# --------------------------------------------------------------------------
# PREDICTIONS TAB
# --------------------------------------------------------------------------
def render_predictions_tab():
    st.markdown("### Today's predictions (5-day horizon)")
    st.caption(
        "Claude-generated 5-day forecasts stored every morning by the "
        "`predictions` GitHub Action. The `eod` workflow scores them at "
        "close to populate the scorecard at the bottom."
    )

    preds = tools.get_todays_predictions(max_items=30)
    if "error" in preds:
        st.error(preds["error"])
        return

    rt = preds.get("round_trip_cost_pct")
    min_gross = preds.get("minimum_gross_for_trade_pct")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("As of", preds.get("as_of", "—"))
    c2.metric("Total predictions", preds.get("n_total", 0))
    c3.metric("Round-trip cost", f"{rt}%" if rt is not None else "—")
    c4.metric("Min gross to trade", f"{min_gross}%"
              if min_gross is not None else "—")

    rows = preds.get("predictions") or []
    if not rows:
        st.info("No predictions for today yet.")
        return

    df = pd.DataFrame(rows)
    view = df[[
        "symbol", "sector", "direction", "conviction",
        "suggested_action", "entry_price_pkr",
        "suggested_stop_pkr", "suggested_target_pkr",
        "expected_gross_5d_pct", "expected_net_5d_pct",
        "clears_cost_threshold", "rationale",
    ]].copy()
    view.columns = ["Sym", "Sector", "Dir", "Conv", "Action",
                    "Entry", "Stop", "Target",
                    "Gross %", "Net %", "Viable", "Why"]

    def _style(r):
        styles = [""] * len(r)
        if str(r["Action"]) in ("BUY", "ADD") and r["Viable"]:
            styles = ["background-color: #1f4f2f; color: white"] * len(r)
        elif str(r["Action"]) == "SELL":
            styles = ["background-color: #5a1f1f; color: white"] * len(r)
        return styles

    st.dataframe(view.style.apply(_style, axis=1),
                  hide_index=True, use_container_width=True)

    # -- Drill-down per-symbol
    st.markdown("#### Drill into a specific prediction")
    syms = [r["symbol"] for r in rows]
    pick = st.selectbox("Symbol", syms)
    if pick:
        p = next(r for r in rows if r["symbol"] == pick)
        c1, c2, c3 = st.columns(3)
        c1.metric("Entry", f"{p.get('entry_price_pkr')} PKR")
        c2.metric("Stop",  f"{p.get('suggested_stop_pkr')} PKR")
        c3.metric("Target", f"{p.get('suggested_target_pkr')} PKR")
        st.markdown("**Rationale:** " + (p.get("rationale") or "—"))
        drivers = p.get("key_drivers") or []
        risks = p.get("key_risks") or []
        if drivers:
            st.markdown("**Drivers:** " + " · ".join(drivers))
        if risks:
            st.markdown("**Risks:** " + " · ".join(risks))

    st.divider()

    # -- Rolling accuracy scorecard
    st.markdown("### Rolling accuracy")
    pa = dash.load_prediction_log_stats()
    if pa.get("scored_count", 0) == 0:
        st.info(pa.get("note",
                       "No scored predictions yet. The EOD workflow will "
                       "populate actuals after market close."))
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Scored predictions", pa["scored_count"])
    c2.metric("Direction hit (gross)",
               f"{pa['direction_hit_rate_gross_pct']:.1f}%")
    c3.metric("Inside range",
               f"{pa['inside_range_hit_rate_pct']:.1f}%")
    c4.metric("Avg actual (net)",
               f"{pa['avg_actual_return_net_pct']:+.2f}%")


# --------------------------------------------------------------------------
# VALUE TAB — fundamental fair-value vs market price
# --------------------------------------------------------------------------
def render_value_tab():
    st.markdown("### Intrinsic value vs market price")
    st.caption(
        "Sector-aware fair-value model. Banks → DDM. E&P → P/B blend. "
        "Cement → 3y-avg P/E. OMC/Misc → 50/50 P/E + P/B. Power → DDM. "
        "Pharma & Conglomerate use specialised rules. Slow signal, "
        "6-24 month horizon — best combined with momentum/news for entry "
        "timing. Refreshed weekly by the `fundamentals` GitHub Action."
    )

    book = tools.get_universe_value_book()
    if "error" in book:
        st.error(book["error"])
        st.info("Run `python -m connectors.yfinance_fundamentals` to seed "
                "the cache, then reload this tab.")
        return

    counts = book.get("signal_counts", {})
    rows = book.get("rows") or []

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Universe", book.get("n_symbols", 0))
    c2.metric("BUY_VALUE", counts.get("BUY_VALUE", 0))
    c3.metric("FAIR",      counts.get("FAIR", 0))
    c4.metric("SELL_VALUE", counts.get("SELL_VALUE", 0))

    if not rows:
        st.warning("No value rows — fundamentals cache may be empty.")
        return

    # Build display frame
    df_rows = []
    for r in rows:
        df_rows.append({
            "Sym": r.get("symbol"),
            "Sector": r.get("sector", "")[:18],
            "Px": r.get("current_price"),
            "Fair": r.get("fair_value"),
            "Upside %": r.get("upside_pct"),
            "Signal": r.get("signal"),
            "Conf": r.get("confidence", "—"),
            "Method": r.get("method", "")[:60],
            "Warnings": "; ".join(r.get("warnings", [])) or "",
        })
    df = pd.DataFrame(df_rows)

    def _style(r):
        styles = [""] * len(r)
        sig = str(r["Signal"])
        if sig == "BUY_VALUE":
            styles = ["background-color: #1f4f2f; color: white"] * len(r)
        elif sig == "SELL_VALUE":
            styles = ["background-color: #5a1f1f; color: white"] * len(r)
        elif sig == "NO_SIGNAL":
            styles = ["color: #888"] * len(r)
        return styles

    st.dataframe(df.style.apply(_style, axis=1),
                  hide_index=True, use_container_width=True)

    # ------------------------------------------------------- per-stock detail
    st.markdown("#### Inspect a single stock")
    syms = [r.get("symbol") for r in rows if r.get("symbol")]
    pick = st.selectbox("Symbol", syms, key="value_pick")
    if not pick:
        return
    rec = next((r for r in rows if r.get("symbol") == pick), None)
    if not rec:
        return
    if "error" in rec:
        st.error(rec["error"])
        return

    a, b, c, d = st.columns(4)
    a.metric("Current price", f"{rec.get('current_price')} PKR")
    a_fair = rec.get("fair_value")
    b.metric("Fair value", f"{a_fair} PKR" if a_fair is not None else "—")
    up = rec.get("upside_pct")
    c.metric("Upside vs fair", f"{up:+.1f} %" if up is not None else "—")
    d.metric("Signal", rec.get("signal"),
              help=f"Confidence: {rec.get('confidence', '—')}")

    st.markdown(f"**Method:** {rec.get('method', '—')}")
    if rec.get("warnings"):
        st.warning(" · ".join(rec["warnings"]))

    # Show the underlying components
    comps = rec.get("components") or {}
    cols = st.columns(4)
    for col, key, title in zip(
        cols,
        ["pe", "pb", "graham", "ddm"],
        ["P/E method", "P/B method", "Graham number", "DDM (dividend)"],
    ):
        c0 = comps.get(key) or {}
        v = c0.get("value")
        with col:
            st.markdown(f"**{title}**")
            st.markdown(f"`{v}` PKR" if v is not None else "_n/a_")
            for k in ("formula", "method", "eps_3y", "eps_ttm", "bvps",
                      "sector_pe", "sector_pb", "D_ttm", "g", "r", "D1",
                      "quality", "reason"):
                if k in c0 and k != "value":
                    st.caption(f"{k}: `{c0[k]}`")

    # Sector medians used
    secm = rec.get("sector_medians") or {}
    if secm:
        st.caption(
            f"Sector medians used → P/E {secm.get('pe_med')}  ·  "
            f"P/B {secm.get('pb_med')}  ·  n peers {secm.get('n')}"
        )

    asof = rec.get("as_of_fundamentals")
    if asof:
        st.caption(f"Fundamentals last refreshed: `{asof}`")


# --------------------------------------------------------------------------
# NEWS TAB
# --------------------------------------------------------------------------
def render_news_tab():
    st.markdown("### Scored news feed")
    st.caption(
        "Claude-Haiku scores every RSS headline for sentiment, confidence, "
        "category, and affected PSX tickers. The `news_scoring` workflow "
        "updates this cache 3×/day."
    )

    hours = st.slider("Lookback (hours)", min_value=6, max_value=168,
                       value=48, step=6)

    try:
        sent = tools.get_scored_sentiment(hours_macro=hours,
                                             hours_ticker=hours)
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")
        return
    if "error" in sent:
        st.error(sent["error"])
        return

    # Macro tilt
    macro = sent.get("macro") or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("Weighted macro tilt", f"{macro.get('score', 0):+.3f}")
    c2.metric("Articles scored", macro.get("n", 0))
    by_cat = macro.get("by_category") or {}
    top_cat = ""
    if by_cat:
        k, v = max(by_cat.items(), key=lambda kv: abs(kv[1]))
        top_cat = f"{k} ({v:+.2f})"
    c3.metric("Strongest category", top_cat or "—")

    # Scored news table
    from ui.news_sentiment import load_scored_news
    df = load_scored_news(hours)
    if df.empty:
        st.info("No scored headlines in this window.")
        return
    st.markdown("#### Headlines")
    view = df[[
        "published_at", "title", "source", "category",
        "sentiment", "confidence", "affected_symbols", "one_liner",
    ]].sort_values("published_at", ascending=False).head(100).copy()
    view.columns = ["Published", "Title", "Source", "Category",
                    "Sentiment", "Conf", "Affected", "Why matters"]

    def _style(r):
        s = r["Sentiment"]
        if s > 0.3:
            return ["background-color: #1f4f2f; color: white"] * len(r)
        if s < -0.3:
            return ["background-color: #5a1f1f; color: white"] * len(r)
        return [""] * len(r)

    st.dataframe(view.style.apply(_style, axis=1),
                  hide_index=True, use_container_width=True,
                  height=500)


# --------------------------------------------------------------------------
# CHAT TAB
# --------------------------------------------------------------------------
CHAT_EXAMPLES = [
    "I bought MCB at 380 on 2026-03-15, 100 shares. Should I hold?",
    "What are today's top 5 buy candidates and why?",
    "Look at my whole portfolio and tell me which names to trim first.",
    "What's the current market regime and should I be cautious?",
    "Show me the momentum ranking of all 15 stocks.",
    "What's on my watchlist and anything near a target price?",
    "How have my closed trades performed? Any patterns?",
]


def render_chat_tab():
    st.markdown("### Chat with the advisor")
    st.caption(
        "Ask about any ticker, your portfolio, your watchlist, your realized "
        "P&L, or today's picks. The bot calls live data — it cannot invent "
        "prices or recommendations."
    )

    st.markdown("**Example questions:**")
    cols = st.columns(len(CHAT_EXAMPLES[:3]))
    for i, ex in enumerate(CHAT_EXAMPLES[:3]):
        with cols[i]:
            if st.button(ex, use_container_width=True, key=f"ex_{i}"):
                _send_message(ex)

    st.divider()

    for turn in st.session_state.chat_history:
        role = turn["role"]
        with st.chat_message(role):
            content = turn["content"]
            if isinstance(content, list):
                content = "\n".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                )
            st.markdown(content)
            if role == "assistant" and turn.get("trace"):
                with st.expander(f"tools called ({len(turn['trace'])})"):
                    for call in turn["trace"]:
                        st.markdown(f"**{call['tool']}**({call.get('args', {})})")
                        st.json(call.get("result", {}), expanded=False)

    user_msg = st.chat_input("Ask about your portfolio, a symbol, or "
                              "today's picks…")
    if user_msg:
        _send_message(user_msg)

    if st.session_state.chat_history:
        if st.button("Clear chat history"):
            st.session_state.chat_history = []
            st.rerun()


def _send_message(user_msg: str):
    ss = st.session_state
    ss.chat_history.append({"role": "user", "content": user_msg})
    history = [{"role": t["role"], "content": t["content"]}
               for t in ss.chat_history]

    try:
        if ss.provider == "claude":
            if not ss.claude_key:
                _reply_error("No Anthropic API key set.")
                return
            client = get_client("claude", api_key=ss.claude_key,
                                model=ss.claude_model)
        elif ss.provider == "github":
            if not ss.github_key:
                _reply_error("No GitHub token set.")
                return
            client = get_client("github", api_key=ss.github_key,
                                model=ss.github_model)
        else:
            if not ss.gemini_key:
                _reply_error("No Google API key set.")
                return
            client = get_client("gemini", api_key=ss.gemini_key,
                                model=ss.gemini_model)
    except Exception as e:
        _reply_error(f"Client init failed: {e}")
        return

    with st.spinner(f"Thinking with {ss.provider}…"):
        try:
            result = client.run_chat(history)
        except Exception as e:
            _reply_error(f"Chat failed: {type(e).__name__}: {e}")
            return

    ss.chat_history.append({
        "role": "assistant",
        "content": result.get("text", "(no response)"),
        "trace": result.get("trace", []),
    })
    st.rerun()


def _reply_error(msg: str):
    st.session_state.chat_history.append(
        {"role": "assistant", "content": f"⚠ {msg}", "trace": []}
    )
    st.rerun()


# --------------------------------------------------------------------------
# BACKTEST TAB
# --------------------------------------------------------------------------
def render_backtest_tab():
    st.markdown("### Backtest Plan D Phase 1")
    st.caption(
        "End-to-end backtest of the monthly momentum strategy over the full "
        "available price history. First run takes 15–30 seconds."
    )

    c1, c2 = st.columns([1, 3])
    with c1:
        use_overlay = st.toggle("Use regime overlay", value=False,
                                 help="Rule-based regime classifier adjusts "
                                      "exposure (NORMAL/CAUTION/CRISIS).")
    with c2:
        pass

    if st.button("Run backtest", type="primary"):
        with st.spinner("Running backtest…"):
            try:
                from brain.backtest_v2 import simulate
                from brain.strategy import StrategyConfig
                cfg = StrategyConfig()
                wide = tools._wide()
                result = simulate(wide, cfg=cfg,
                                   use_regime_overlay=use_overlay,
                                   include_cost_sensitivity=False)
            except Exception as e:
                st.error(f"Backtest failed: {type(e).__name__}: {e}")
                return

        m = result.metrics
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("CAGR", f"{m.get('cagr', 0) * 100:.2f}%")
        c2.metric("Sharpe", f"{m.get('sharpe', 0):.2f}")
        c3.metric("Max drawdown", f"{m.get('max_drawdown', 0) * 100:.2f}%")
        c4.metric("Calmar", f"{m.get('calmar', 0):.2f}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Sortino", f"{m.get('sortino', 0):.2f}")
        c2.metric("Win rate", f"{m.get('win_rate', 0) * 100:.1f}%")
        c3.metric("Profit factor", f"{m.get('profit_factor', 0):.2f}")
        c4.metric("Turnover", f"{m.get('turnover', 0):.2f}")

        eq = result.equity_curve
        if isinstance(eq, pd.Series) and not eq.empty:
            st.line_chart(eq.rename("Equity"))

        bh = getattr(result, "benchmark_curve", None)
        if isinstance(bh, pd.Series) and not bh.empty:
            df_cmp = pd.DataFrame(
                {"Strategy": eq, "Buy&Hold (universe)": bh})
            st.markdown("### Strategy vs. Buy & Hold")
            st.line_chart(df_cmp)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    render_sidebar()
    st.markdown("# PSX Advisor")
    st.caption("A rules-based trading bot for the Pakistan Stock Exchange, "
                "paired with an LLM advisor that grounds every answer in "
                "live data.")

    render_top_strip()

    tabs = st.tabs([
        "Dashboard", "Portfolio", "Watchlist", "Scanner",
        "Predictions", "Value", "News", "Chat", "Backtest",
    ])
    with tabs[0]:
        render_dashboard_tab()
    with tabs[1]:
        render_portfolio_tab()
    with tabs[2]:
        render_watchlist_tab()
    with tabs[3]:
        render_scanner_tab()
    with tabs[4]:
        render_predictions_tab()
    with tabs[5]:
        render_value_tab()
    with tabs[6]:
        render_news_tab()
    with tabs[7]:
        render_chat_tab()
    with tabs[8]:
        render_backtest_tab()


if __name__ == "__main__":
    main()
