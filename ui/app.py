"""PSX Advisor — Streamlit UI.

Run from the repo root:
    streamlit run ui/app.py

Features:
  * Chat tab — Claude-Haiku or Gemini-2.5-Flash with tool access to the whole
    Plan D backend. The LLM can NEVER fabricate numbers; every price,
    momentum, or portfolio figure comes from a tool call into our engine.
  * Portfolio tab — Enter your real holdings; get live P&L, suggested stops,
    and per-position HOLD / SELL / TRIM recommendations derived from the
    Phase 1 rule.
  * Scanner tab — Full 15-stock universe ranked by momentum with today's
    picks highlighted. Use this for new-buy ideas.
  * Backtest tab — On-demand end-to-end backtest of Plan D Phase 1.

API keys: set `ANTHROPIC_API_KEY` and/or `GOOGLE_API_KEY` in your shell before
launching, or paste them in the sidebar for this session only.
"""

from __future__ import annotations

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


# Auto-load .env if present so API keys are picked up without manual export.
def _load_dotenv(path: Path) -> None:
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

from ui import tools, recommendations as recs
from ui.portfolio import (
    load_user_portfolio, save_user_portfolio, add_position, remove_position,
)
from ui.llm_clients import (
    get_client, DEFAULT_CLAUDE_MODEL, DEFAULT_GEMINI_MODEL,
    DEFAULT_GITHUB_MODEL, GITHUB_MODEL_CHOICES,
)


# --------------------------------------------------------------------------
# Page config
# --------------------------------------------------------------------------
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
    # Default to the free GitHub Models option if a GITHUB_TOKEN is in env;
    # otherwise fall back to Claude.
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


_init_state()


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown("## PSX Advisor")
        st.caption("Plan D Phase 1 — Monthly momentum with defensive overlay")
        st.divider()

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
            "Provider",
            providers,
            index=current_idx,
            horizontal=True,
            format_func=lambda p: provider_labels[p],
            label_visibility="collapsed",
            key="provider_radio",
        )
        st.session_state.provider = provider

        if provider == "github":
            st.text_input(
                "GitHub token (PAT with models:read)",
                value=st.session_state.github_key,
                type="password",
                key="github_key",
                help="Fine-grained PAT with the 'models:read' scope. "
                     "Create at https://github.com/settings/tokens. "
                     "Or set GITHUB_TOKEN env var before launching.",
            )
            st.selectbox(
                "GitHub model",
                GITHUB_MODEL_CHOICES,
                index=(GITHUB_MODEL_CHOICES.index(st.session_state.github_model)
                       if st.session_state.github_model in GITHUB_MODEL_CHOICES
                       else 0),
                key="github_model",
                help="Low tier (gpt-4o-mini, gpt-4.1-mini, Llama) = 15 RPM / "
                     "150 RPD on free. High tier (gpt-4o, gpt-4.1) = "
                     "10 RPM / 50 RPD.",
            )
        elif provider == "claude":
            st.text_input(
                "Anthropic API key",
                value=st.session_state.claude_key,
                type="password",
                key="claude_key",
                help="Or set ANTHROPIC_API_KEY env var before launching.",
            )
            st.text_input("Claude model", value=st.session_state.claude_model,
                          key="claude_model")
        else:
            st.text_input(
                "Google API key",
                value=st.session_state.gemini_key,
                type="password",
                key="gemini_key",
                help="Or set GOOGLE_API_KEY env var before launching.",
            )
            st.text_input("Gemini model", value=st.session_state.gemini_model,
                          key="gemini_model")

        st.divider()
        st.markdown("### Market snapshot")
        try:
            regime = tools.get_market_regime()
            color = {"NORMAL": "green", "CAUTION": "orange",
                     "CRISIS": "red"}.get(regime.get("regime", "NORMAL"), "gray")
            st.markdown(
                f"**Regime:** :{color}[{regime.get('regime')}]  "
                f"(×{regime.get('exposure_multiplier'):.2f})"
            )
            ind = regime.get("indicators", {})
            c1, c2 = st.columns(2)
            with c1:
                st.metric("5d avg", _pct(ind.get("universe_ret_5d")))
                st.metric("150d mom", _pct(ind.get("universe_150d_log_ret")))
            with c2:
                st.metric("21d avg", _pct(ind.get("universe_ret_21d")))
                st.metric("Breadth", f"{ind.get('breadth_pct_up_today', 'n/a')}%"
                          if ind.get("breadth_pct_up_today") is not None else "n/a")
            st.caption(f"As of {regime.get('as_of')}")
        except Exception as e:
            st.warning(f"Snapshot unavailable: {e}")

        st.divider()
        if st.button("Refresh price cache", use_container_width=True):
            tools.refresh_cache()
            st.success("Cache cleared. New data will load on next query.")
            time.sleep(0.5)
            st.rerun()

        if st.button("Pull latest data from GitHub",
                       use_container_width=True,
                       help="Runs `git pull` to fetch updates committed by "
                            "the daily CI workflows, then clears the price "
                            "cache so you see the fresh data."):
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "pull", "--ff-only", "--no-rebase"],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(Path(__file__).resolve().parent.parent),
                )
                if result.returncode == 0:
                    tools.refresh_cache()
                    out = (result.stdout or "").strip()
                    if "Already up to date" in out or "up-to-date" in out:
                        st.info("Already up to date.")
                    else:
                        st.success(f"Pulled:\n```\n{out[-400:]}\n```")
                    time.sleep(0.6)
                    st.rerun()
                else:
                    err = (result.stderr or result.stdout or "").strip()
                    st.error(f"git pull failed:\n```\n{err[-400:]}\n```")
            except subprocess.TimeoutExpired:
                st.error("git pull timed out (30s). Check your network.")
            except FileNotFoundError:
                st.error("`git` not found in PATH.")
            except Exception as e:
                st.error(f"{type(e).__name__}: {e}")

        st.caption(
            "Data comes from local Parquet OHLCV files populated by the "
            "existing `psx_dps` pipeline. The bot only trades the 15-stock "
            "universe."
        )


def _pct(x, digits: int = 2) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:+.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


# --------------------------------------------------------------------------
# CHAT TAB
# --------------------------------------------------------------------------
CHAT_EXAMPLES = [
    "I bought MCB at 380 on 2026-03-15, 100 shares. Should I hold?",
    "What are today's top 5 buy candidates and why?",
    "Look at my whole portfolio and tell me which names to trim first.",
    "What's the current market regime and should I be cautious?",
    "Show me the momentum ranking of all 15 stocks.",
]


def render_chat_tab():
    st.markdown("### Chat with the advisor")
    st.caption(
        "Ask about any ticker, your portfolio, or today's picks. The bot "
        "calls live data — it cannot make up prices or recommendations."
    )

    # Example prompts
    st.markdown("**Example questions:**")
    cols = st.columns(len(CHAT_EXAMPLES[:3]))
    for i, ex in enumerate(CHAT_EXAMPLES[:3]):
        with cols[i]:
            if st.button(ex, use_container_width=True, key=f"ex_{i}"):
                _send_message(ex)

    st.divider()

    # History
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
                        result = call.get("result", {})
                        st.json(result, expanded=False)

    # Input
    user_msg = st.chat_input("Ask about your portfolio, a symbol, or today's picks…")
    if user_msg:
        _send_message(user_msg)

    # Clear history button
    if st.session_state.chat_history:
        if st.button("Clear chat history"):
            st.session_state.chat_history = []
            st.rerun()


def _send_message(user_msg: str):
    ss = st.session_state
    ss.chat_history.append({"role": "user", "content": user_msg})
    # Build provider-agnostic history to pass to the client
    history = [{"role": t["role"], "content": t["content"]}
               for t in ss.chat_history]

    # Make the client
    try:
        if ss.provider == "claude":
            if not ss.claude_key:
                _reply_error("No Anthropic API key set. Add it in the sidebar "
                             "or set ANTHROPIC_API_KEY in your environment.")
                return
            client = get_client("claude", api_key=ss.claude_key,
                                model=ss.claude_model)
        elif ss.provider == "github":
            if not ss.github_key:
                _reply_error("No GitHub token set. Add a fine-grained PAT "
                             "with the 'models:read' scope in the sidebar, "
                             "or set GITHUB_TOKEN in your environment.")
                return
            client = get_client("github", api_key=ss.github_key,
                                model=ss.github_model)
        else:
            if not ss.gemini_key:
                _reply_error("No Google API key set. Add it in the sidebar "
                             "or set GOOGLE_API_KEY in your environment.")
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
# PORTFOLIO TAB
# --------------------------------------------------------------------------
def render_portfolio_tab():
    st.markdown("### Your portfolio")
    st.caption(
        "Enter real positions you hold. The advisor uses these as context: "
        "live P&L, suggested trailing stops, and per-position HOLD / SELL / "
        "TRIM signals from the Phase 1 rule."
    )

    # --- Add position form
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
                ent_dt = st.date_input("Entry date", value=datetime.now().date())
            notes = st.text_input("Notes (optional)", max_chars=200)
            submitted = st.form_submit_button("Add position", type="primary")
            if submitted and sym and ent_px > 0 and qty > 0:
                add_position(sym, ent_px, qty, str(ent_dt), notes)
                st.success(f"Added {qty:g} × {sym} @ {ent_px:.2f}")
                time.sleep(0.5)
                st.rerun()

    positions = load_user_portfolio()
    if not positions:
        st.info(
            "No positions yet. Add one above, or ask the advisor for BUY "
            "ideas in the Chat tab."
        )
        return

    # --- Analyze all
    analyzed = recs.analyze_all_positions(positions)
    summary = recs.portfolio_summary(analyzed)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Positions", summary["position_count"])
    c2.metric("Cost (PKR)", f"{summary['total_cost_pkr']:,.0f}")
    c3.metric("Market value (PKR)", f"{summary['total_market_value_pkr']:,.0f}")
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

    st.divider()

    # --- Per-position cards
    st.markdown("### Per-position analysis")
    for i, row in enumerate(analyzed):
        _render_position_card(i, row)


def _action_color(action: str) -> str:
    a = str(action or "").upper()
    if "SELL" in a:
        return "red"
    if "TRIM" in a:
        return "orange"
    if "CAUTION" in a or "caution" in a.lower():
        return "orange"
    if "HOLD" in a:
        return "green"
    return "gray"


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
        hdr_c1, hdr_c2, hdr_c3 = st.columns([2, 2, 1])
        with hdr_c1:
            st.markdown(f"### {sym} — :{color}[{action}]")
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
            if st.button("Remove", key=f"rm_{idx}"):
                remove_position(idx)
                st.rerun()

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Entry", f"{row.get('entry_price_pkr', 0):.2f} PKR")
            st.caption(f"on {row.get('entry_date', '—')} "
                       f"({row.get('days_held', '—')} days held)")
        with c2:
            st.metric("Current", f"{row.get('current_price_pkr', 0):.2f} PKR")
            st.caption(f"peak since entry: {row.get('peak_since_entry_pkr', 0):.2f}")
        with c3:
            st.metric("Suggested stop",
                      f"{row.get('suggested_trailing_stop_pkr', 0):.2f} PKR")
            st.caption(f"{row.get('suggested_trailing_stop_pct', 12)}% "
                       f"trailing from peak")
        with c4:
            rank = row.get("momentum_rank")
            st.metric("Momentum rank", f"#{rank}" if rank else "—")
            st.caption(
                f"{'in top-5' if row.get('in_current_top5') else 'not in top-5'} "
                f"today"
            )


# --------------------------------------------------------------------------
# SCANNER TAB
# --------------------------------------------------------------------------
def render_scanner_tab():
    st.markdown("### Market scanner")
    st.caption("Universe ranked by 150-day momentum; today's Phase 1 picks highlighted.")

    try:
        sig = tools.get_strategy_signal()
        regime = tools.get_market_regime()
    except Exception as e:
        st.error(f"Scanner unavailable: {e}")
        return

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        st.metric(
            "Phase 1 recommendation",
            sig.get("recommended_action", "—"),
        )
        st.caption(sig.get("rationale", ""))
    with c2:
        picks = sig.get("selected_symbols") or []
        would = sig.get("would_pick_if_market_filter_off") or []
        if picks:
            st.markdown("**Today's picks:** " + ", ".join(picks))
        elif would:
            st.markdown("**Would-be picks (filter off):** " + ", ".join(would))
        else:
            st.markdown("**No picks today.**")
    with c3:
        st.metric(
            f"Regime: {regime.get('regime')}",
            f"×{regime.get('exposure_multiplier'):.2f}",
        )
        st.caption(regime.get("reason", ""))

    st.divider()

    # --- Ranking table
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
        display.style.apply(
            lambda r: _style(df.iloc[r.name]), axis=1),
        hide_index=True, use_container_width=True,
    )

    st.divider()

    # --- Top buy ideas with technical context
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
# BACKTEST TAB
# --------------------------------------------------------------------------
def render_backtest_tab():
    st.markdown("### Backtest Plan D Phase 1")
    st.caption(
        "Run the honest end-to-end backtest of the monthly momentum strategy "
        "over the full available price history. First run takes ~15-30 seconds."
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
                result = simulate(wide, cfg=cfg, use_regime_overlay=use_overlay,
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

        # Equity curve
        eq = result.equity_curve
        if isinstance(eq, pd.Series) and not eq.empty:
            st.line_chart(eq.rename("Equity"))

        # Buy & hold baseline for context
        bh = getattr(result, "benchmark_curve", None)
        if isinstance(bh, pd.Series) and not bh.empty:
            df_cmp = pd.DataFrame({"Strategy": eq, "Buy&Hold (universe)": bh})
            st.markdown("### Strategy vs. Buy & Hold")
            st.line_chart(df_cmp)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    render_sidebar()
    st.markdown("# PSX Advisor")
    st.caption(
        "A rules-based trading bot for the Pakistan Stock Exchange, "
        "paired with an LLM advisor that grounds every answer in live data."
    )

    tabs = st.tabs(["Chat", "Portfolio", "Scanner", "Backtest"])
    with tabs[0]:
        render_chat_tab()
    with tabs[1]:
        render_portfolio_tab()
    with tabs[2]:
        render_scanner_tab()
    with tabs[3]:
        render_backtest_tab()


if __name__ == "__main__":
    main()
