"""PSX Advisor — Streamlit UI.

Run from the repo root:
    streamlit run ui/app.py

Tabs (story-first, beginner friendly):
  1. Today          — narrative morning brief: market mood, what to do,
                      portfolio at a glance, alerts, top movers.
  2. My Holdings    — live positions, sector allocation, trailing stop,
                      close-to-journal flow, realised P&L history.
  3. Forecast       — 5-day predictions for every universe stock with
                      entry / stop / target and the rolling scorecard.
  4. Fair Value     — sector-aware intrinsic value, quality score, and
                      earnings momentum for every stock.
  5. Watchlist      — stocks you're tracking with target prices.
  6. Find Ideas     — universe ranked by momentum strength.
  7. News           — AI-scored PSX news feed with sentiment + tickers.
  8. Ask Advisor    — chat with Claude / Gemini / GitHub Models; every
                      answer is grounded in live tool calls.
  9. Strategy Tester— on-demand Plan-D Phase-1 backtest.

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

from ui import (
    tools, recommendations as recs, dashboard_data as dash, explainers,
)
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
from ui import daily_report

# Where the small persistent UI flags live (onboarding seen, etc.).
# Kept out of git via .gitignore (data/.ui_state.json), so each user has
# their own copy.
_UI_STATE_PATH = PROJECT_ROOT / "data" / ".ui_state.json"


st.set_page_config(
    page_title="PSX Advisor",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
def _hydrate_env_from_st_secrets() -> None:
    """Copy values from ``st.secrets`` into ``os.environ`` so the rest
    of the codebase (which uses ``os.environ.get`` everywhere) picks
    them up transparently when the app runs on Streamlit Cloud.

    Only keys we actually consume are forwarded — we never leak the
    full secrets dict into the process environment. Locally this is a
    no-op because ``st.secrets`` is empty unless ``.streamlit/
    secrets.toml`` exists.
    """
    try:
        if not hasattr(st, "secrets"):
            return
        for key in ("GITHUB_TOKEN", "GH_TOKEN",
                     "ANTHROPIC_API_KEY",
                     "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            try:
                val = st.secrets.get(key) if hasattr(st.secrets, "get") \
                    else None
            except Exception:
                val = None
            if val and not os.environ.get(key):
                os.environ[key] = str(val)
    except Exception:
        pass


def _init_state():
    _hydrate_env_from_st_secrets()
    ss = st.session_state
    ss.setdefault("chat_history", [])

    # GitHub Models token is auto-resolved (Streamlit secrets / env /
    # local file) — the user never has to paste it. We resolve once
    # at startup and store on session state so the providers panel
    # below can show a live status badge.
    auto_gh_token = _read_canonical_token() or ""
    ss.setdefault("github_key", auto_gh_token)
    # Always refresh, even if the slot already exists, so a token
    # added to Streamlit Cloud secrets after the first launch is
    # picked up on the next rerun.
    if auto_gh_token and not ss.get("github_key"):
        ss["github_key"] = auto_gh_token

    has_gh = bool(ss.get("github_key"))
    ss.setdefault("provider", "github" if has_gh else "claude")
    ss.setdefault("claude_key", os.environ.get("ANTHROPIC_API_KEY", ""))
    ss.setdefault("gemini_key", os.environ.get("GEMINI_API_KEY", "")
                  or os.environ.get("GOOGLE_API_KEY", ""))
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
# UI styling — a calm, readable look. We don't override Streamlit's theme,
# only tweak typography, spacing, and a few container affordances.
# --------------------------------------------------------------------------
_CUSTOM_CSS = """
<style>
    /* Comfortable max line length on huge screens */
    .block-container { max-width: 1400px; padding-top: 2rem; }
    /* Tab labels: a touch larger and more breathing room */
    button[data-baseweb="tab"] {
        font-size: 1.05rem !important;
        padding: 0.5rem 1.1rem !important;
    }
    /* Section header card */
    .psx-section-header {
        background: rgba(56, 139, 253, 0.06);
        border-left: 3px solid #2f6feb;
        border-radius: 6px;
        padding: 0.85rem 1.1rem;
        margin: 0.4rem 0 1rem 0;
    }
    .psx-section-header h2 {
        margin: 0 0 0.15rem 0 !important;
        font-weight: 600;
        font-size: 1.45rem !important;
    }
    .psx-section-header p {
        margin: 0 !important;
        color: rgba(180, 195, 215, 0.95);
        font-size: 0.96rem;
    }
    /* Hero "Today" headline */
    .psx-hero {
        padding: 1.1rem 1.4rem;
        border-radius: 12px;
        background: linear-gradient(120deg,
                                     rgba(47,111,235,0.10),
                                     rgba(35,134,54,0.06));
        border: 1px solid rgba(110, 130, 160, 0.20);
        margin-bottom: 1rem;
    }
    .psx-hero h1 { margin: 0 !important; font-size: 1.9rem !important; }
    .psx-hero .mood {
        display: inline-block;
        margin-top: 0.5rem;
        padding: 0.2rem 0.7rem;
        border-radius: 999px;
        font-weight: 600;
        font-size: 0.95rem;
    }
    /* Metric tweaks for hero */
    div[data-testid="stMetric"] {
        background: rgba(120, 140, 170, 0.05);
        border-radius: 8px;
        padding: 0.4rem 0.7rem;
        animation: psx-fade-in 0.55s ease-out;
        /* IMPORTANT: prevent the parent from clipping long values with
           "..." when the column is narrow. Streamlit's default sets
           overflow:hidden on the value which causes the dotted ellipsis
           the user is seeing for amounts like "+411,626 PKR". */
        overflow: visible !important;
    }
    div[data-testid="stMetricValue"] {
        animation: psx-count-in 0.65s cubic-bezier(.2,.7,.2,1) both;
        /* No truncation — let large PKR amounts render fully even in
           narrow columns. We shrink the font slightly and use tabular
           digits so the numbers still align across cards. */
        font-size: 1.55rem !important;
        line-height: 1.18 !important;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
        overflow: visible !important;
        text-overflow: clip !important;
    }
    div[data-testid="stMetricValue"] > div {
        overflow: visible !important;
        text-overflow: clip !important;
        white-space: nowrap !important;
        max-width: none !important;
    }
    /* Streamlit wraps the actual number in a span — make sure THAT
       can't get truncated either. */
    div[data-testid="stMetricValue"] span,
    div[data-testid="stMetricValue"] p {
        overflow: visible !important;
        text-overflow: clip !important;
        white-space: nowrap !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.82rem !important;
        opacity: 0.85;
    }
    div[data-testid="stMetricDelta"] {
        animation: psx-fade-in 0.9s ease-out 0.15s both;
        font-size: 0.95rem !important;
        white-space: nowrap;
    }
    /* Smaller screens: tighten further so 6-digit PKR amounts still fit */
    @media (max-width: 1200px) {
        div[data-testid="stMetricValue"] {
            font-size: 1.35rem !important;
        }
    }
    @keyframes psx-fade-in {
        0%   { opacity: 0; transform: translateY(4px); }
        100% { opacity: 1; transform: translateY(0); }
    }
    @keyframes psx-count-in {
        0%   { opacity: 0; transform: scale(0.84); }
        60%  { opacity: 1; transform: scale(1.04); }
        100% { opacity: 1; transform: scale(1.00); }
    }
    /* Hero gets a subtle slide-in too */
    .psx-hero {
        animation: psx-slide-in 0.55s ease-out;
    }
    @keyframes psx-slide-in {
        0%   { opacity: 0; transform: translateY(-8px); }
        100% { opacity: 1; transform: translateY(0); }
    }
    /* Onboarding "welcome" panel */
    .psx-onboard {
        background: linear-gradient(120deg,
                                     rgba(47,111,235,0.16),
                                     rgba(35,134,54,0.10));
        border: 1px solid rgba(110, 130, 160, 0.30);
        border-radius: 12px;
        padding: 1.1rem 1.4rem;
        margin: 0 0 1rem 0;
    }
    .psx-onboard h2 { margin: 0 0 0.5rem 0 !important;
                      font-size: 1.4rem !important; }
    .psx-onboard p  { margin: 0 0 0.4rem 0 !important; }
    .psx-onboard ul { margin: 0.2rem 0 0.4rem 1.2rem; padding: 0; }
    .psx-onboard li { margin: 0.15rem 0; }
    /* Sparkline panel */
    .psx-spark-wrap {
        background: rgba(120, 140, 170, 0.04);
        border-radius: 10px;
        padding: 0.6rem 0.9rem 0.4rem 0.9rem;
        margin: 0.5rem 0 0.6rem 0;
        border: 1px solid rgba(110, 130, 160, 0.18);
    }
    .psx-spark-title {
        font-size: 0.92rem;
        font-weight: 600;
        color: rgba(180, 195, 215, 0.95);
        margin: 0 0 0.25rem 0;
    }
</style>
"""


def inject_css() -> None:
    st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)


def section_header(title: str, what: str,
                   how_to_read: list[str] | None = None,
                   expander_label: str = "How to read this") -> None:
    """Standardised tab introduction.

    Renders a coloured banner with a one-line description plus an optional
    "How to read this" expander so the deep mechanics are available on
    demand without crowding the screen.
    """
    st.markdown(
        f'<div class="psx-section-header"><h2>{title}</h2>'
        f'<p>{what}</p></div>',
        unsafe_allow_html=True,
    )
    if how_to_read:
        with st.expander(expander_label, expanded=False):
            for line in how_to_read:
                st.markdown(f"- {line}")


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
        ohlcv = fresh.get("OHLCV directory", {}) or {}
        preds = fresh.get("Predictions log", {}) or {}

        def _short(info: dict) -> str:
            if not info.get("exists"):
                return ":red[missing]"
            tdb = info.get("trading_days_behind")
            if tdb is None:
                return f"{info.get('age_hours', 0)}h"
            color = ("green" if tdb <= 1 else "orange"
                     if tdb <= 3 else "red")
            latest = info.get("latest_data_date") or "?"
            return f":{color}[{latest}]"

        st.markdown(
            "**Latest data**  \n"
            f"Prices:  {_short(ohlcv)}  \n"
            f"Predictions:  {_short(preds)}"
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
            # GitHub Models is the free-tier provider and the chatbot
            # auto-resolves the token from (1) Streamlit Cloud
            # secrets, (2) the local ``.env``, or (3) the canonical
            # token file on disk. The user never pastes it here —
            # showing a status badge instead avoids accidentally
            # baking a token into a screenshot or browser history.
            tok = st.session_state.get("github_key") or ""
            if tok:
                preview = tok[:8] + "…" if len(tok) > 12 else "(short)"
                st.success(
                    f"GitHub token auto-loaded ({preview}) — "
                    f"the chatbot is ready to go."
                )
            else:
                st.error(
                    "No GitHub token available. Add **GITHUB_TOKEN** "
                    "to Streamlit Cloud secrets (Settings → Secrets) "
                    "or to your local ``.env`` file."
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
                          help="Or set GEMINI_API_KEY in .env.")
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
                      help="Pulls today's OHLCV for the full universe "
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
            st.caption(
                "The **green/orange/red dot** is based on how recent "
                "the actual data inside the file is (in trading days), "
                "not just when the file was last touched. PSX is "
                "closed on Sat/Sun, so being 1 trading day behind on "
                "a Monday morning is normal."
            )
            for name, info in dash.data_freshness().items():
                if not info.get("exists"):
                    st.markdown(f"- **{name}** — _missing_")
                    continue
                # Color code by trading-days behind, falling back to
                # mtime age if we couldn't infer a data date.
                tdb = info.get("trading_days_behind")
                if tdb is None:
                    age = info.get("age_hours", 0)
                    color = ("green" if age < 6 else "orange"
                             if age < 24 else "red")
                    label = (f":{color}[file: {info['updated_at']}]"
                              f"  ({age}h ago)")
                else:
                    color = ("green" if tdb <= 1 else "orange"
                             if tdb <= 3 else "red")
                    latest = info.get("latest_data_date") or "?"
                    if tdb == 0:
                        gap = "today"
                    elif tdb == 1:
                        gap = "1 trading day ago"
                    else:
                        gap = f"{tdb} trading days ago"
                    label = (
                        f":{color}[data through **{latest}** ({gap})]  \n"
                        f"  &nbsp;&nbsp;_file written {info['updated_at']} "
                        f"({info.get('age_hours', 0)}h ago)_"
                    )
                st.markdown(f"- **{name}**  \n  {label}")

        st.divider()
        st.markdown("### Help")
        if st.button("Show welcome tour again", use_container_width=True,
                      help="Re-display the first-run onboarding panel above "
                           "the tabs."):
            reset_onboarding()
            st.rerun()
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


def _read_canonical_token() -> str | None:
    """Token resolution order, most-trusted first:
    1. ``st.secrets["GITHUB_TOKEN"]`` (Streamlit Cloud injects this)
    2. ``GITHUB_TOKEN`` / ``GH_TOKEN`` env vars (local ``.env``)
    3. ``scripts/git token new trading bot`` (the user's canonical
        local file — never committed to the repo)
    4. ``.git/github-credentials`` (what command-line git uses)

    Returns ``None`` if no usable token is found. The first two
    sources cover Streamlit Cloud automatically; the last two only
    matter when running locally.
    """
    # ---- 1. Streamlit Cloud secrets (highest trust on the cloud) ----
    try:
        if hasattr(st, "secrets"):
            tok = st.secrets.get("GITHUB_TOKEN") if hasattr(
                st.secrets, "get") else None
            if tok and isinstance(tok, str) and tok.startswith(
                    ("ghp_", "github_pat_", "ghs_", "gho_")):
                return tok.strip()
    except Exception:
        pass

    # ---- 2. Environment vars (local ``.env`` or CI export) ---------
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(var)
        if val and val.startswith(
                ("ghp_", "github_pat_", "ghs_", "gho_")):
            return val.strip()

    # ---- 3. Repo-local token files (for laptop development) --------
    candidates = [
        PROJECT_ROOT / "scripts" / "git token new trading bot",
        PROJECT_ROOT / "scripts" / "git_token_new_trading_bot",
        PROJECT_ROOT / "scripts" / "github_token.txt",
    ]
    for p in candidates:
        try:
            if p.exists():
                txt = p.read_text(encoding="utf-8").strip()
                # File may be the bare token, or in URL form.
                if "@" in txt:
                    seg = txt.split("@", 1)[0]
                    if ":" in seg:
                        return seg.rsplit(":", 1)[-1].strip()
                if txt.startswith(("ghp_", "github_pat_", "ghs_", "gho_")):
                    return txt.splitlines()[0].strip()
        except Exception:
            continue

    # ---- 4. Git credential file (last resort) ----------------------
    cred_file = PROJECT_ROOT / ".git" / "github-credentials"
    try:
        if cred_file.exists():
            for line in cred_file.read_text(encoding="utf-8").splitlines():
                if "github.com" in line and "@" in line:
                    seg = line.split("@", 1)[0]
                    if ":" in seg:
                        tok = seg.rsplit(":", 1)[-1].strip()
                        if tok and tok != "x-access-token":
                            return tok
    except Exception:
        pass

    return None


def _do_git_pull():
    import subprocess
    tok = _read_canonical_token()
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

        # Strip any token already embedded in the remote URL, then re-embed
        # the canonical one. This guarantees the freshest token is used,
        # regardless of what was baked into .git/config historically.
        clean_remote = remote
        if remote.startswith("https://") and "@" in remote:
            clean_remote = "https://" + remote.split("@", 1)[1]

        if tok and clean_remote.startswith("https://"):
            auth_url = ("https://x-access-token:" + tok + "@"
                        + clean_remote[len("https://"):])
            # Disable inherited credential helpers (Windows Credential
            # Manager often holds a stale credential for a different
            # GitHub account, which would override the embedded token).
            cmd = ["git", "-c", "credential.helper=",
                   "pull", auth_url, branch,
                   "--ff-only", "--no-rebase"]
        else:
            # Fallback: rely on the local store helper configured under
            # [credential "https://github.com"] in .git/config.
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
# Onboarding tour — shown once on first launch
# --------------------------------------------------------------------------
def _load_ui_state() -> dict:
    import json
    try:
        if _UI_STATE_PATH.exists():
            return json.loads(_UI_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_ui_state(state: dict) -> None:
    import json
    try:
        _UI_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _UI_STATE_PATH.write_text(
            json.dumps(state, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:
        pass


def render_onboarding() -> None:
    """First-run welcome panel. Renders only if the user has never
    dismissed it. Persisted via data/.ui_state.json so it stays
    hidden after the first 'Got it'."""
    state = _load_ui_state()
    if state.get("onboarding_seen") and \
            not st.session_state.get("force_onboarding"):
        return

    st.markdown(
        '<div class="psx-onboard">'
        '<h2>Welcome to PSX Advisor</h2>'
        '<p>This tool watches 15 PSX stocks for you, scores them with '
        'a 13-layer model (price action, fundamentals, news, '
        'overnight global cues, intrinsic value, quality, earnings '
        'momentum, and an LLM strategist on top), and tells you what '
        'to do — in plain English.</p>'
        '<p><b>A 30-second tour:</b></p>'
        '<ul>'
        '<li><b>Today</b> — your morning brief: one screen, one '
        'paragraph, one top action. Start here every day.</li>'
        '<li><b>My Holdings</b> — paste your portfolio, get '
        'position-aware advice and trailing stops.</li>'
        '<li><b>Forecast</b> — 5-day predictions for every '
        'universe stock + a rolling scorecard of how the bot has '
        'actually been doing.</li>'
        '<li><b>Fair Value</b> — sector-aware intrinsic value, '
        'quality score, and earnings momentum — the analyst-grade '
        'fundamentals layer.</li>'
        '<li><b>Ask Advisor</b> — chat with Claude / Gemini / GitHub '
        'Models; every answer is grounded in live tool calls into the '
        'pipelines, not generic LLM text.</li>'
        '</ul>'
        '<p>Tip: hit <b>Download daily report (PDF)</b> on the Today '
        'tab to share the morning brief with someone else (WhatsApp, '
        'email, print).</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    cols = st.columns([1, 1, 4])
    with cols[0]:
        if st.button("Got it — let's go", type="primary",
                      use_container_width=True, key="onboard_dismiss"):
            state["onboarding_seen"] = True
            state["onboarding_seen_at"] = datetime.now().isoformat()
            _save_ui_state(state)
            st.session_state["force_onboarding"] = False
            st.rerun()
    with cols[1]:
        st.button("Show again later", use_container_width=True,
                  key="onboard_later")


def reset_onboarding() -> None:
    """Helper exposed in the sidebar: re-show the tour on next render."""
    state = _load_ui_state()
    state["onboarding_seen"] = False
    _save_ui_state(state)
    st.session_state["force_onboarding"] = True


# --------------------------------------------------------------------------
# Daily PDF brief — download button helper
# --------------------------------------------------------------------------
def _render_pdf_download(brief: dict, mood: dict, narrative: str,
                          action: dict, alerts: list) -> None:
    """Build the PDF on demand and offer it as a download.

    We build lazily (only when the user expands or clicks) so the Today
    tab stays snappy on first paint."""
    with st.expander("Download daily report (PDF)", expanded=False):
        st.caption(
            "**Analyst-ready PDF** of today's brief: market mood, top "
            "action, **Macro Radar** (industry KPIs + sector verdicts), "
            "full forecast table, **top news in the last 24h**, "
            "**Material Information** disclosures, "
            "**per-stock detail cards** (rationale, key drivers, key "
            "risks, recent news headlines with sentiment scores, "
            "fundamentals vs sector medians, macro impact), "
            "management outlook, portfolio, quality leaders, and "
            "earnings calendar. Share via email / WhatsApp / print."
        )
        if st.button("Generate PDF", key="gen_pdf",
                      use_container_width=False):
            try:
                with st.spinner("Building your daily brief…"):
                    pdf_bytes = daily_report.build_daily_report(
                        brief=brief, mood=mood, narrative=narrative,
                        action=action, alerts=alerts,
                    )
                st.session_state["_pdf_bytes"] = pdf_bytes
                st.session_state["_pdf_filename"] = (
                    daily_report.default_filename())
                st.success(f"Generated ({len(pdf_bytes):,} bytes).")
            except Exception as e:
                st.error(f"PDF build failed: {type(e).__name__}: {e}")
        if st.session_state.get("_pdf_bytes"):
            st.download_button(
                "Download PDF",
                data=st.session_state["_pdf_bytes"],
                file_name=st.session_state.get(
                    "_pdf_filename", "psx-daily-brief.pdf"),
                mime="application/pdf",
                use_container_width=False,
                key="dl_pdf",
            )


# --------------------------------------------------------------------------
# Universe sparkline — small chart on Today
# --------------------------------------------------------------------------
def _render_universe_sparkline(idx: dict) -> None:
    if not idx or "error" in idx:
        return
    values = idx.get("values") or []
    dates = idx.get("dates") or []
    if len(values) < 5:
        return

    pct = idx.get("pct_change_pct", 0.0)
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "–"
    color = "rgb(120,200,140)" if pct > 0 else (
            "rgb(220,120,120)" if pct < 0 else "rgb(170,170,170)")
    span = (f"{idx.get('as_of_first', '?')} → "
            f"{idx.get('as_of_last', '?')}")

    st.markdown(
        '<div class="psx-spark-wrap">'
        f'<div class="psx-spark-title">'
        f'PSX universe equal-weighted index '
        f'<span style="color:{color}">{arrow} {pct:+.2f}%</span> '
        f'<span style="opacity:0.6;font-weight:400">'
        f'· {span} · base 100</span></div>',
        unsafe_allow_html=True,
    )
    df = pd.DataFrame({"index": values}, index=pd.to_datetime(dates))
    st.line_chart(df, height=130, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------
# DASHBOARD TAB
# --------------------------------------------------------------------------
def render_today_tab():
    """The story-first landing page.

    Goal: any normal person opens this and within 10 seconds knows
    (a) what's happening in the market, (b) how it looks, and
    (c) what to do today.
    """
    brief = dash.morning_brief()
    mood = explainers.market_mood(brief)
    narrative = explainers.daily_narrative(brief)
    action = explainers.top_action_today(brief)
    alerts = explainers.alert_lines(brief)

    today_str = datetime.now().strftime("%A, %d %b %Y")
    greeting = explainers.time_of_day_greeting()

    # ---------------------------------------------------- Hero
    st.markdown(
        f'<div class="psx-hero">'
        f'<h1>Good {greeting} — {today_str}</h1>'
        f'<div class="mood" style="background:rgba(50,150,80,0.18);'
        f'color:white;">'
        f'<span style="color:rgb(120,200,140)">●</span>&nbsp;'
        f'{mood["label"]} '
        f'<span style="opacity:0.7">· market mood {mood["score"]}/100</span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown(narrative)

    # ---------------------------------------------------- Universe sparkline
    _render_universe_sparkline(brief.get("universe_index", {}))

    # ---------------------------------------------------- Fresh reports
    mo = brief.get("management_outlook") or {}
    fresh_n = int(mo.get("fresh_this_week") or 0)
    if fresh_n > 0:
        rows_mo = mo.get("rows") or []
        from datetime import timedelta as _td
        cutoff = (datetime.now() - _td(days=7)).date()
        fresh_syms = [r["symbol"] for r in rows_mo
                       if r.get("filing_date")
                       and datetime.strptime(r["filing_date"],
                                              "%Y-%m-%d").date() >= cutoff]
        if fresh_syms:
            st.success(
                f"**{fresh_n} new Director's Report{'s' if fresh_n > 1 else ''} "
                f"this week** ({', '.join(fresh_syms[:5])}). "
                f"Open the **Forecast** tab and pick the symbol to read "
                f"management's outlook."
            )

    # ---------------------------------------------------- Material info banner
    mi = brief.get("material_information") or {}
    mi_rows = mi.get("rows") or []
    if mi_rows:
        # Highlight only filings from the last 2 trading days — those
        # are the high-volatility flags.
        from datetime import timedelta as _td2
        recent_cut = (datetime.now() - _td2(days=2)).date()
        fresh_mi = [
            r for r in mi_rows
            if r.get("date")
            and datetime.strptime(r["date"], "%Y-%m-%d").date()
                >= recent_cut
        ]
        if fresh_mi:
            symbols_hit = ", ".join(sorted({r["symbol"]
                                              for r in fresh_mi}))[:120]
            st.warning(
                f"⚡ **Material Information filed in the last 2 days** "
                f"({len(fresh_mi)} disclosures across {symbols_hit}). "
                f"These typically precede 3-7% gaps — check the "
                f"**Reports** tab before placing new orders."
            )

    # ---------------------------------------------------- PDF download
    _render_pdf_download(brief, mood, narrative, action, alerts)

    # ---------------------------------------------------- 3 hero columns
    c1, c2, c3 = st.columns([1.1, 1, 1])
    with c1:
        _today_action_card(action)
    with c2:
        _today_mood_card(mood)
    with c3:
        _today_portfolio_card(brief.get("portfolio", {}),
                               brief.get("journal_stats", {}))

    # ---------------------------------------------------- Macro Radar
    # Sector-aware reading of today's macro drivers (rates, oil, FX,
    # etc.). The analyst asked for an explicit sector winners/losers
    # view: "today interest rates increased by 1% — show me which
    # banks benefit and which cement names get hurt".  This panel
    # answers that directly.
    _today_macro_radar(brief.get("macro_impact", {}))

    # ---------------------------------------------------- Bot's Verdict
    # The unified, conflict-resolved call across all seven lenses
    # (Value / Quality / Momentum / Macro / News / Flow / Management).
    # Surfaces the ONE answer the analyst should act on, plus a
    # transparent breakdown so they can audit the reasoning.
    _today_bots_verdict()

    # ---------------------------------------------------- Alerts
    if alerts:
        st.markdown("#### Things to watch")
        for a in alerts[:6]:
            if a["level"] == "warning":
                st.warning(a["text"])
            else:
                st.info(a["text"])

    # ---------------------------------------------------- Movers + value
    st.markdown("#### What's moving today")
    c1, c2 = st.columns([1, 1])
    with c1:
        _today_movers_card(brief.get("universe_movers", {}))
    with c2:
        _today_top_picks_card(brief.get("predictions", {}))

    # ---------------------------------------------------- Optional drill-down
    with st.expander(
        "Show me the underlying signals (regime, overnight, news, "
        "value, quality, calendar)",
        expanded=False,
    ):
        c1, c2, c3 = st.columns([1, 1, 1.3])
        with c1: _card_regime(brief.get("regime", {}))
        with c2: _card_strategy(brief.get("strategy_signal", {}))
        with c3: _card_overnight(brief.get("overnight", {}))

        c1, c2 = st.columns([1.2, 1])
        with c1: _card_sentiment(brief.get("sentiment", {}))
        with c2: _card_prediction_accuracy(
            brief.get("prediction_accuracy", {}))

        _card_value_book(brief.get("value_book", {}))

        c1, c2 = st.columns([1.2, 1])
        with c1: _card_earnings_calendar(brief.get("earnings_calendar", {}))
        with c2: _card_quality_leaders(brief.get("quality_book", {}))

        # Big-fish flows + sector heatmap (analyst-requested)
        _card_big_fish_flows()


# ----------------------------- Today-tab cards (plain English)
def _today_action_card(action: dict) -> None:
    with st.container(border=True):
        st.markdown("### What to do today")
        sym = action.get("symbol")
        if not sym:
            st.markdown(":blue[**Stay patient.**] No high-conviction "
                         "setups today.")
            st.caption(action.get("reason", ""))
            return
        conv = (action.get("conviction") or "").upper()
        word = explainers.conviction_word(conv)
        net = action.get("net")
        st.markdown(
            f":green[**Top idea: {sym}** — {action['action']}]  "
            f"_({word})_"
        )
        if net is not None:
            st.markdown(
                f"Expected net return next 5 days: **{net:+.2f}%**"
            )
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("Buy near",   f"{action.get('entry') or '—'}")
        cc2.metric("Stop loss",  f"{action.get('stop') or '—'}")
        cc3.metric("Target",     f"{action.get('target') or '—'}")
        # ---- Why this call?  (analyst-mandatory explainer)
        # The analyst said: "Investor should know the rationale behind
        # decision making". Show the full LLM rationale plus the top
        # drivers, risks, and macro tailwinds/headwinds.
        if action.get("reason"):
            st.markdown("**Why this call?**")
            st.markdown(f"_{action['reason']}_")
        drivers = action.get("key_drivers") or []
        risks = action.get("key_risks") or []
        tailwinds = action.get("macro_tailwinds") or []
        headwinds = action.get("macro_headwinds") or []
        if drivers or risks or tailwinds or headwinds:
            with st.expander("Drivers, risks and macro context",
                              expanded=False):
                if drivers:
                    st.markdown("**Drivers (positive signals)**")
                    for d in drivers[:5]:
                        st.markdown(f"- {d}")
                if risks:
                    st.markdown("**Risks (what could break this)**")
                    for r in risks[:5]:
                        st.markdown(f"- {r}")
                if tailwinds:
                    st.markdown("**Macro tailwinds**")
                    for t in tailwinds[:4]:
                        st.markdown(f"- {t}")
                if headwinds:
                    st.markdown("**Macro headwinds**")
                    for h in headwinds[:4]:
                        st.markdown(f"- {h}")
                # If we stored the deterministic macro snapshot at
                # prediction time, surface the dominant driver.
                mi_snap = action.get("macro_impact") or {}
                drivers_active = mi_snap.get("drivers") or []
                if drivers_active:
                    top = drivers_active[0]
                    st.caption(
                        f"Dominant macro driver at the time of the call: "
                        f"**{top.get('name')}** "
                        f"({top.get('move')}, {top.get('magnitude')})."
                    )


def _today_mood_card(mood: dict) -> None:
    with st.container(border=True):
        st.markdown("### How the market looks")
        st.markdown(f":{mood['color']}[**{mood['label']}**] "
                     f"· score {mood['score']}/100")
        st.progress(min(int(mood['score']), 100) / 100.0)
        for r in mood.get("reasons", [])[:5]:
            st.markdown(f"- {r}")


def _today_portfolio_card(pf: dict, js: dict) -> None:
    with st.container(border=True):
        st.markdown("### Your portfolio")
        if pf.get("note") or (pf.get("position_count", 0) == 0
                                and not pf.get("positions")):
            st.markdown(":gray[No positions yet.]")
            st.caption("Add holdings under **My Holdings** to track P&L "
                       "and get position-aware advice.")
            return
        cc1, cc2 = st.columns(2)
        ret = pf.get("total_unrealized_pnl_pct")
        pnl = pf.get("total_unrealized_pnl_pkr", 0)
        mv = pf.get("total_market_value_pkr", 0)
        cc1.metric(
            "Live value", f"{mv:,.0f} PKR",
            delta=f"{ret:+.2f}%" if ret is not None else None,
        )
        cc2.metric("Unrealized P&L", f"{pnl:+,.0f} PKR")
        cc1.metric("Positions", pf.get("position_count", 0))
        cc2.metric(
            "Win rate (closed)",
            f"{js.get('win_rate_pct', 0):.0f}%"
            if js.get("count") else "—",
        )


def _today_bots_verdict() -> None:
    """The Bot's Verdict — a unified, conflict-resolved call across
    all seven lenses (Value, Quality, Momentum, Macro, News, Flow,
    Management).

    The analyst's repeated complaint was: 'every tab tells a different
    story — Value says SELL on the same stock Momentum says BUY'.
    This panel runs the deterministic synthesiser in
    ``brain.verdict_synthesizer`` to produce ONE call per stock, with
    a transparent breakdown so the conflict resolution is visible.
    """
    try:
        from brain.verdict_synthesizer import synthesize_universe
        out = synthesize_universe()
    except Exception as e:
        st.warning(f"Bot's Verdict unavailable: {type(e).__name__}: {e}")
        return
    rows = out.get("rows") or []
    if not rows:
        return

    st.markdown("### The Bot's Verdict")
    st.caption(
        "One unified call per stock, blending **seven lenses** "
        "(Value · Quality · Momentum · Macro · News · Flow · "
        "Management). When lenses disagree, the conflict is "
        "highlighted and resolved with an explicit rule. This is the "
        "answer to use when different tabs seem to tell different "
        "stories — the synthesiser already did the reconciliation. "
        "Click any row to see the full lens breakdown."
    )

    # ----- Top summary table -------------------------------------------
    import pandas as pd
    summary_rows = []
    for r in rows:
        n_conflicts = len(r.get("conflicts") or [])
        summary_rows.append({
            "Symbol":     r["symbol"],
            "Sector":     r["sector"],
            "Action":     r["action"],
            "Direction":  r["direction"],
            "Conviction": r["conviction"],
            "Score":      r["score"],
            "Conflicts":  n_conflicts,
        })
    df = pd.DataFrame(summary_rows)
    st.dataframe(df, hide_index=True, use_container_width=True,
                  column_config={
                      "Score": st.column_config.NumberColumn(
                          "Score", help="Composite score across lenses",
                          format="%+d"),
                      "Conflicts": st.column_config.NumberColumn(
                          "Conflicts",
                          help=("Number of lens-pair disagreements; "
                                "0 means full agreement"),
                          format="%d"),
                  })

    # ----- Drill-down for each stock -----------------------------------
    pick = st.selectbox(
        "Show full lens breakdown for:",
        options=[r["symbol"] for r in rows],
        key="verdict_drilldown",
    )
    if pick:
        chosen = next((r for r in rows if r["symbol"] == pick), None)
        if chosen:
            _render_verdict_card(chosen)


def _render_verdict_card(v: dict) -> None:
    """Compact verdict card for one stock — shared between the Today
    tab drill-down and the per-stock page."""
    sym = v["symbol"]
    action = v["action"]
    color = ("#1e8a45" if action in ("BUY", "ADD")
             else "#c0392b" if action in ("SELL", "AVOID", "TRIM")
             else "#666666")
    st.markdown(
        f"#### {sym} — "
        f"<span style='color:{color}'><b>{action}</b></span>  "
        f"<span style='color:#888'>· {v['direction']} · "
        f"{v['conviction']} conviction · score {v['score']:+d}</span>",
        unsafe_allow_html=True,
    )
    st.caption(f"Sector: {v.get('sector') or '—'}")

    cols = st.columns(7)
    for i, c in enumerate(v["contributions"]):
        with cols[i]:
            score = int(c["score"])
            badge = ("🟢" if score >= 1 else "🔴" if score <= -1 else "⚪")
            sign = ("+" if score > 0 else "")
            st.metric(c["name"], f"{badge} {sign}{score}",
                       help=c["reason"])

    # Itemised lens reasons (full text, no truncation)
    with st.expander("Why each lens scored what it did", expanded=False):
        for c in v["contributions"]:
            sign = "+" if c["score"] > 0 else (
                "−" if c["score"] < 0 else " ")
            st.markdown(
                f"- **{c['name']}** ({sign}{abs(int(c['score']))}, "
                f"weight {c['weight']}): {c['reason']}"
            )

    # Conflicts + resolution
    conflicts = v.get("conflicts") or []
    log = v.get("resolution_log") or []
    if conflicts:
        st.markdown("**Lens conflicts detected:**")
        for cf in conflicts:
            st.warning(cf)
        if log:
            st.markdown("**Resolution applied:**")
            for line in log:
                st.info(line)
    else:
        st.success("All lenses agree — no conflict resolution needed.")


def _today_macro_radar(mi: dict) -> None:
    """Macro Radar — today's drivers + per-sector winners and losers.

    The analyst's exact request: "today interest rates increased by 1%
    — show me which banks benefit most and which cement names get hurt
    most". This panel is the visual answer.
    """
    if not mi or mi.get("error"):
        with st.container(border=True):
            st.markdown("### Macro Radar")
            st.caption("Macro impact engine unavailable today "
                        f"({mi.get('error', 'no data')}).")
        return
    drivers = mi.get("drivers") or []
    by_sector = mi.get("by_sector") or {}
    by_symbol = mi.get("by_symbol") or {}
    kpis = mi.get("kpis") or {}

    with st.container(border=True):
        st.markdown("### Macro Radar — today's sector winners & losers")
        st.caption(
            "How today's macro environment (policy rate, oil, USD/PKR, "
            "T-bills, FX reserves, KSE-100, CPI) reads across PSX "
            "sectors. Each sector and stock gets a deterministic "
            "tailwind / headwind score so every call this app makes "
            "can cite a specific reason."
        )

        # ---- Pre-MPC alert banner (rate-sensitive sectors capped)
        mpc = mi.get("mpc_alert") or {}
        if mpc.get("in_pre_window"):
            sectors = ", ".join(mpc.get("rate_sensitive_sectors")
                                  or [])[:120]
            st.warning(
                f"**SBP MPC alert** — meeting on **"
                f"{mpc.get('next_mpc')}** "
                f"({mpc.get('days_until')} day(s) away). "
                f"Conviction is capped one notch on rate-sensitive "
                f"sectors ({sectors}). The bot will re-predict "
                f"automatically when the post-meeting press release "
                f"is scored as a news shock."
            )
        elif mpc.get("in_post_window"):
            st.info(
                f"**Post-MPC re-pricing window** — the SBP announced "
                f"on {mpc.get('next_mpc')}. Today's predictions "
                f"already incorporate the new rate; expect higher "
                f"intraday volatility on banks / IPPs / cements."
            )

        # ---- Industry KPI dashboard (live numbers)
        if kpis:
            st.markdown("**Industry KPIs (today)**")
            k_cols = st.columns(5)
            tbill = kpis.get("tbill_3m_pct")
            kibor = kpis.get("kibor_3m_pct")
            rsv   = kpis.get("reserves_sbp_usd_mn")
            kse   = kpis.get("kse100_close")
            cpi   = kpis.get("cpi_yoy_pct")
            cpi_p = kpis.get("cpi_period") or ""
            with k_cols[0]:
                st.metric("T-bill 3M",
                          f"{tbill:.2f}%" if tbill is not None else "—",
                          f"{kpis.get('tbill_3m_change_5d')*100:+.0f} bps (5d)"
                          if kpis.get("tbill_3m_change_5d") is not None else None)
            with k_cols[1]:
                st.metric("KIBOR 3M",
                          f"{kibor:.2f}%" if kibor is not None else "—",
                          f"{kpis.get('kibor_3m_change_5d')*100:+.0f} bps (5d)"
                          if kpis.get("kibor_3m_change_5d") is not None else None)
            with k_cols[2]:
                st.metric("SBP reserves",
                          f"${rsv/1000:.1f} bn" if rsv is not None else "—",
                          f"{kpis.get('reserves_change_30d')/1000:+.1f} bn (30d)"
                          if kpis.get("reserves_change_30d") is not None else None)
            with k_cols[3]:
                st.metric("KSE-100",
                          f"{kse:,.0f}" if kse is not None else "—",
                          f"{kpis.get('kse100_ret_5d')*100:+.1f}% (5d)"
                          if kpis.get("kse100_ret_5d") is not None else None)
            with k_cols[4]:
                st.metric(f"CPI YoY ({cpi_p})" if cpi_p else "CPI YoY",
                          f"{cpi:.1f}%" if cpi is not None else "—",
                          f"{kpis.get('cpi_yoy_change_pp'):+.1f} pp"
                          if kpis.get("cpi_yoy_change_pp") is not None else None)

        # ---- Active drivers
        if not drivers:
            st.info(
                "No major macro drivers active today — markets are in a "
                "quiet macro regime. Stock-specific signals dominate."
            )
        else:
            st.markdown("**Active macro drivers**")
            d_rows = [
                {
                    "Driver":     d.get("name"),
                    "Move":       d.get("move"),
                    "Magnitude":  d.get("magnitude"),
                    "Context":    d.get("context") or "",
                }
                for d in drivers[:8]
            ]
            st.dataframe(d_rows, hide_index=True,
                          use_container_width=True)

        # ---- Sector winners / losers
        if by_sector:
            sec_rows = sorted(
                [
                    {
                        "Sector":  s,
                        "Score":   v.get("score", 0),
                        "Verdict": v.get("verdict") or "NEUTRAL",
                        "Top reason": (
                            ((v.get("tailwinds") or [None])[0]
                             if (v.get("score") or 0) > 0
                             else (v.get("headwinds") or [None])[0])
                            or "—"
                        ),
                    }
                    for s, v in by_sector.items()
                ],
                key=lambda r: r["Score"], reverse=True,
            )
            cw, ch = st.columns(2)
            with cw:
                st.markdown(":green[**Tailwind sectors**]")
                wins = [r for r in sec_rows if r["Score"] > 0]
                if wins:
                    st.dataframe(wins, hide_index=True,
                                  use_container_width=True)
                else:
                    st.caption("No clear tailwind sectors today.")
            with ch:
                st.markdown(":red[**Headwind sectors**]")
                losers = [r for r in sec_rows if r["Score"] < 0]
                if losers:
                    st.dataframe(losers, hide_index=True,
                                  use_container_width=True)
                else:
                    st.caption("No clear headwind sectors today.")

        # ---- Most affected stocks (with leverage amplifier)
        if by_symbol:
            sym_rows = sorted(
                [
                    {
                        "Symbol":         s,
                        "Sector":         v.get("sector"),
                        "Stock score":    v.get("stock_score", 0),
                        "Sector score":   v.get("sector_score", 0),
                        "Verdict":        v.get("verdict") or "NEUTRAL",
                        "Amplifier":      v.get("amplifier_note") or "—",
                    }
                    for s, v in by_symbol.items()
                ],
                key=lambda r: r["Stock score"], reverse=True,
            )
            cw2, ch2 = st.columns(2)
            with cw2:
                st.markdown(":green[**Stocks most likely to benefit**]")
                top = [r for r in sym_rows if r["Stock score"] > 0][:5]
                if top:
                    st.dataframe(top, hide_index=True,
                                  use_container_width=True)
                else:
                    st.caption("No stock currently in clear macro tailwind.")
            with ch2:
                st.markdown(":red[**Stocks most exposed to headwinds**]")
                bot = [r for r in sym_rows if r["Stock score"] < 0]
                bot = sorted(bot, key=lambda r: r["Stock score"])[:5]
                if bot:
                    st.dataframe(bot, hide_index=True,
                                  use_container_width=True)
                else:
                    st.caption("No stock currently in clear macro headwind.")
        st.caption(
            "Scores are signed integers from the deterministic rule "
            "book in `brain/macro_impact.py` (sector base sensitivity "
            "+/- a leverage amplifier). Higher absolute value = more "
            "confident the move helps or hurts the name."
        )


def _today_movers_card(m: dict) -> None:
    with st.container(border=True):
        st.markdown("### Today's biggest moves")
        if "error" in m:
            st.caption(m["error"])
            return
        gainers = m.get("gainers", [])
        losers = m.get("losers", [])
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown(":green[**Up most**]")
            for g in gainers:
                st.markdown(
                    f"- **{g['symbol']}** {_pct_raw(g.get('ret_1d_pct'))}"
                )
        with cc2:
            st.markdown(":red[**Down most**]")
            for l in losers:
                st.markdown(
                    f"- **{l['symbol']}** {_pct_raw(l.get('ret_1d_pct'))}"
                )
        st.caption(
            "Universe = your 15 tracked stocks. These are 1-day moves; "
            "use the **Forecast** tab for what comes next."
        )


def _today_top_picks_card(preds: dict) -> None:
    with st.container(border=True):
        st.markdown("### Stocks the bot likes most")
        if "error" in preds:
            st.caption(preds["error"])
            return
        actionable = [
            p for p in (preds.get("predictions") or [])
            if p.get("suggested_action") in ("BUY", "ADD")
            and p.get("clears_cost_threshold")
        ]
        if not actionable:
            st.markdown(":gray[Nothing clears the cost+edge threshold "
                         "today.]")
            st.caption(
                "The system needs an expected gross return ≥ "
                f"{preds.get('minimum_gross_for_trade_pct', '?')}% "
                "(after estimated brokerage + slippage + tax) before it "
                "calls a trade. Cash is a position."
            )
            return
        # Show top 5 short rows
        for p in actionable[:5]:
            conv = (p.get("conviction") or "").upper()
            word = explainers.conviction_word(conv)
            net = p.get("expected_net_5d_pct")
            st.markdown(
                f"- **{p['symbol']}** — {p.get('suggested_action')}  "
                f":green[net {net:+.2f}%]  · _{word}_"
            )
        st.caption(
            "Source: stored daily forecasts. See **Forecast** tab "
            "for the full table with entry / stop / target."
        )


def _card_earnings_calendar(cal: dict):
    if "error" in cal:
        st.container(border=True).warning(
            f"Earnings calendar: {cal['error']}"
        )
        return
    upcoming = cal.get("upcoming") or []
    blackouts = cal.get("blackout_now") or []
    with st.container(border=True):
        title_color = "red" if blackouts else "blue"
        st.markdown(
            f"**Upcoming events (next 21 days)** — "
            f":{title_color}[{len(blackouts)} in blackout] · "
            f"{len(upcoming)} total"
        )
        st.caption(
            "Hybrid prediction: yfinance for confirmed dates + dividend-"
            "cadence model for the rest. Blackout = ≤5 days with "
            "HIGH/MED confidence (no new BUY/ADD)."
        )
        if not upcoming:
            st.markdown("_No events in the next 21 days._")
            return
        for ev in upcoming[:8]:
            d = ev.get("days_until", 0)
            color = ("red" if ev.get("in_blackout_5d")
                     else "orange" if d <= 14 else "blue")
            badge = ("**BLACKOUT**" if ev.get("in_blackout_5d")
                     else "WINDOW" if d <= 14 else "")
            st.markdown(
                f"- :{color}[**{ev['symbol']}**] · "
                f"{ev.get('next_event_date_utc')}  ·  "
                f"**{d}d**  ·  conf=`{ev.get('confidence')}`  ·  "
                f"src=`{ev.get('source')}` {badge}"
            )


def _card_big_fish_flows():
    """Today-tab panel: where the institutional money went today.

    Combines the FIPI/LIPI big-fish breakdown (foreign + banks +
    mutual funds + insurance) with the sector volume heatmap so the
    user can see at a glance whether institutions are net buyers or
    sellers and which sectors are trading hot.
    """
    try:
        flow = tools.get_fipi_flows()
        heat = tools.get_sector_volume_heatmap(top_k=5,
                                                  lookback_days=20)
    except Exception as e:
        st.container(border=True).caption(
            f"Big-fish flows unavailable: {type(e).__name__}: {e}"
        )
        return

    with st.container(border=True):
        st.markdown("### Where the big money went today")
        st.caption(
            "Institutional activity (foreign + banks + mutual funds + "
            "insurance) drives multi-day moves. Retail flow (Individuals "
            "+ Brokers) tends to chase. Sector-volume leaders show which "
            "industries the day's action concentrated in."
        )
        if "error" in flow:
            st.warning(flow["error"])
        else:
            bf_net = flow.get("big_fish_net_pkr_mn") or 0
            retail_net = flow.get("retail_net_pkr_mn") or 0
            regime = flow.get("big_fish_regime") or "neutral"
            colour = ("green" if regime == "institutional_buying"
                      else "red" if regime == "institutional_selling"
                      else "gray")
            kc1, kc2, kc3 = st.columns(3)
            kc1.metric("Big fish net (PKR mn)", f"{bf_net:+.1f}")
            kc2.metric("Retail net (PKR mn)", f"{retail_net:+.1f}")
            kc3.markdown(f"**Regime**\n\n:{colour}[{regime.replace('_',' ').title()}]")
            comps = flow.get("big_fish_components") or []
            if comps:
                rows_df = pd.DataFrame([
                    {"Cohort": c.get("category"),
                      "Buy (PKR mn)":  c.get("buy_pkr_mn"),
                      "Sell (PKR mn)": c.get("sell_pkr_mn"),
                      "Net (PKR mn)":  c.get("net_pkr_mn")}
                    for c in comps
                ])
                st.dataframe(
                    rows_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Buy (PKR mn)":  st.column_config.NumberColumn(format="%.1f"),
                        "Sell (PKR mn)": st.column_config.NumberColumn(format="%.1f"),
                        "Net (PKR mn)":  st.column_config.NumberColumn(format="%+.1f"),
                    },
                )

        # Sector-volume heatmap
        sec_top = (heat.get("top") or []) if isinstance(heat, dict) else []
        if sec_top:
            st.markdown("**Sector volume leaders today (vs 20-day average)**")
            heat_rows = []
            for s in sec_top:
                ratio = s.get("ratio_vs_avg")
                heat_rows.append({
                    "Sector": s.get("sector"),
                    "Today (PKR mn)": s.get("today_pkr_mn"),
                    "20d avg (PKR mn)": s.get("avg_pkr_mn"),
                    "Ratio vs avg": ratio,
                    "🔥": "🔥" if s.get("is_hot") else "",
                })
            st.dataframe(
                pd.DataFrame(heat_rows),
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Today (PKR mn)": st.column_config.NumberColumn(format="%.1f"),
                    "20d avg (PKR mn)": st.column_config.NumberColumn(format="%.1f"),
                    "Ratio vs avg": st.column_config.NumberColumn(format="%.2fx"),
                },
            )
            st.caption(
                "🔥 = traded value ≥ 2× the 20-day average. That kind "
                "of volume spike usually flags institutional rotation "
                "into or out of the sector."
            )


def _card_quality_leaders(qb: dict):
    if "error" in qb:
        st.container(border=True).warning(
            f"Quality book: {qb['error']}"
        )
        return
    rows = qb.get("rows") or []
    if not rows:
        return
    counts = qb.get("band_counts", {})
    with st.container(border=True):
        st.markdown(
            f"**Quality leaders** — "
            f":green[HIGH {counts.get('HIGH', 0)}] · "
            f"MED {counts.get('MEDIUM', 0)} · "
            f"LOW {counts.get('LOW', 0)} · "
            f":red[JUNK {counts.get('JUNK', 0)}]"
        )
        st.caption(
            "ROE + leverage + EPS stability + growth. Use as a filter on "
            "value picks: HIGH+BUY = real edge, JUNK+BUY = trap."
        )
        for r in rows[:5]:
            sc = r.get("quality_score")
            if sc is None:
                continue
            band = r.get("band", "?")
            color = ("green" if band == "HIGH" else
                     "orange" if band == "MEDIUM" else
                     "red")
            roe_v = r.get("components", {}).get("profitability", {}).get("value")
            st.markdown(
                f"- **{r['symbol']}** ({r.get('sector','?')[:14]})  "
                f":{color}[{sc:.1f}/100  {band}]  ·  "
                f"ROE `{roe_v}%`"
            )


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
    section_header(
        "My Holdings",
        "What you own, what it's worth right now, and how each "
        "position is doing.",
        how_to_read=[
            "**Live value** is what your positions are worth at "
            "yesterday's close. Updates daily after PSX close.",
            "**Unrealized P&L** is paper profit/loss — only realised "
            "when you sell.",
            "**Trailing stop** is the price below which the bot "
            "suggests you exit to lock in gains.",
            "Click *Close position* to move a trade to the journal "
            "and record the realised P&L.",
        ],
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
    section_header(
        "Watchlist",
        "Stocks you're keeping an eye on but don't own yet. Set a target "
        "price and the bot will tell you when it's hit.",
        how_to_read=[
            "Add any of your 15 universe stocks here.",
            "Set a **target price** to get a visual cue when the stock "
            "trades through your level.",
            "The chatbot reads this list — ask *'how is my watchlist "
            "doing?'* or *'should I buy any of my watched stocks today?'*",
        ],
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
    section_header(
        "Find Ideas",
        "Every stock in the universe ranked by recent strength. The bot's top picks "
        "are highlighted in green.",
        how_to_read=[
            "**Momentum** = how strongly a stock has trended over the "
            "last ~150 trading days. Higher is stronger.",
            "**Highlighted in green** = the strategy currently wants "
            "to buy this stock today.",
            "**Highlighted in amber** = would be picked if the overall "
            "market filter wasn't blocking new entries.",
            "Use this to compare stocks side-by-side before opening "
            "the **Forecast** tab for the deeper view.",
        ],
    )

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
    section_header(
        "Forecast",
        "Where the bot thinks each stock will be in 5 trading days "
        "(roughly one week), with entry / stop / target.",
        how_to_read=[
            "**Direction** — bullish, bearish, or neutral over the "
            "next ~5 days.",
            "**Conviction** — high / medium / low. Only HIGH and "
            "MEDIUM signals turn into trade plans.",
            "**Entry / Stop / Target** — the actual price levels you "
            "would use if you took the trade.",
            "**Net %** — expected return AFTER brokerage, slippage, "
            "and capital-gains tax. The bot only flags BUY/ADD when "
            "this beats a 1% edge over costs.",
            "**Scorecard at the bottom** — how the bot's calls have "
            "actually performed (rolling 30 days). Be sceptical until "
            "you have at least 60 scored predictions.",
        ],
    )
    st.caption(
        "Generated daily by the `predictions` GitHub Action. The `eod` "
        "workflow scores each prediction once results are in."
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

        # -------------------------------------------------------- Why this call?
        # Analyst-mandatory: every bullish/bearish call must show the
        # reasoning behind it.
        direction = (p.get("direction") or "").upper()
        conviction = (p.get("conviction") or "").upper()
        action_str = (p.get("suggested_action") or "").upper()
        dir_color = ("green" if direction == "BULLISH"
                      else "red" if direction == "BEARISH" else "orange")
        net_pct = p.get("expected_net_5d_pct")
        st.markdown(
            f"### Why this call?  "
            f":{dir_color}[**{direction}**] · "
            f"conviction **{conviction}** · "
            f"action **{action_str}**"
            + (f" · net **{net_pct:+.2f}%**" if net_pct is not None else "")
        )
        if p.get("rationale"):
            st.markdown(f"_{p['rationale']}_")

        cw1, cw2 = st.columns(2)
        with cw1:
            drivers = p.get("key_drivers") or []
            if drivers:
                st.markdown(":green[**Drivers (positive signals)**]")
                for d in drivers[:6]:
                    st.markdown(f"- {d}")
            tailwinds = p.get("macro_tailwinds") or []
            if tailwinds:
                st.markdown(":green[**Macro tailwinds**]")
                for t in tailwinds[:5]:
                    st.markdown(f"- {t}")
        with cw2:
            risks = p.get("key_risks") or []
            if risks:
                st.markdown(":red[**Risks (what could break this)**]")
                for r in risks[:6]:
                    st.markdown(f"- {r}")
            headwinds = p.get("macro_headwinds") or []
            if headwinds:
                st.markdown(":red[**Macro headwinds**]")
                for h in headwinds[:5]:
                    st.markdown(f"- {h}")

        # ---- Stored macro impact snapshot at prediction time
        mi_snap = p.get("macro_impact") or {}
        if mi_snap:
            with st.expander(
                "Macro context at the time of this call "
                "(deterministic rule book)",
                expanded=False,
            ):
                drivers_snap = mi_snap.get("drivers") or []
                if drivers_snap:
                    st.markdown("**Active drivers**")
                    st.dataframe(
                        [
                            {
                                "Driver": d.get("name"),
                                "Move": d.get("move"),
                                "Magnitude": d.get("magnitude"),
                                "Context": d.get("context") or "",
                            }
                            for d in drivers_snap
                        ],
                        hide_index=True, use_container_width=True,
                    )
                else:
                    st.caption("No major drivers active at prediction "
                                "time.")
                sym_block = mi_snap.get("by_symbol") or {}
                if sym_block:
                    cs1, cs2, cs3 = st.columns(3)
                    cs1.metric("Sector score",
                                f"{sym_block.get('sector_score', 0):+d}")
                    cs2.metric("Stock score",
                                f"{sym_block.get('stock_score', 0):+d}")
                    cs3.metric("Verdict",
                                sym_block.get("verdict") or "NEUTRAL")
                    if sym_block.get("amplifier_note"):
                        st.caption(
                            f"_Stock-specific amplifier: "
                            f"{sym_block['amplifier_note']}_"
                        )
                sec_block = mi_snap.get("by_sector") or {}
                if sec_block:
                    if sec_block.get("tailwinds"):
                        st.markdown(":green[**Sector-level tailwinds**]")
                        for t in sec_block["tailwinds"]:
                            st.markdown(f"- {t}")
                    if sec_block.get("headwinds"):
                        st.markdown(":red[**Sector-level headwinds**]")
                        for h in sec_block["headwinds"]:
                            st.markdown(f"- {h}")

        # ---- Management outlook panel (latest Director's Report) -----
        outlook = dash.latest_management_outlook(symbol=pick)
        rows_out = (outlook or {}).get("rows") or []
        if rows_out:
            mo = rows_out[0]
            tone = mo.get("outlook_tone", 0.0)
            tone_label = (":green[+ bullish]" if tone > 0.15
                           else ":red[\u2212 bearish]" if tone < -0.15
                           else ":orange[\u2014 neutral]")
            with st.expander(
                f"Management Outlook — {mo['fy_period'] or mo['doc_type']}"
                f"  ({mo['filing_date']}) · tone {tone_label}",
                expanded=True,
            ):
                st.caption(
                    "Plain-English forward-looking commentary extracted "
                    "from the company's latest filing on PSX. This is "
                    "what management itself is telling investors about "
                    "the next 6\u201312 months. Higher tone + concrete "
                    "plans = forward-looking tailwind; risks listed "
                    "below = what management itself is worried about."
                )
                st.markdown(f"**Outlook.** {mo['outlook_summary']}")
                cs1, cs2, cs3 = st.columns(3)
                cs1.metric("Tone (-1\u2026+1)", f"{tone:+.2f}")
                cs2.metric("Guidance strength", mo["guidance_strength"])
                cs3.metric(
                    "New plans",
                    "Capex" if mo["capex_announced"]
                    else "Expansion" if mo["expansion_announced"]
                    else "—",
                )
                if mo["growth_plans"]:
                    st.markdown("**Growth plans management called out:**")
                    for plan in mo["growth_plans"]:
                        st.markdown(f"- {plan}")
                if mo["risks_mentioned"]:
                    st.markdown("**Risks management is flagging:**")
                    for risk in mo["risks_mentioned"]:
                        st.markdown(f"- {risk}")
                if mo.get("pdf_url"):
                    st.markdown(
                        f"[Read the original PDF on PSX \u2197]"
                        f"({mo['pdf_url']})"
                    )
                st.caption(
                    f"_Extracted by `{mo['extracted_by_model']}` "
                    f"from {mo.get('title') or 'PSX filing'}._"
                )
        else:
            st.caption(
                "_No Director's Report cached for this stock yet — the "
                "weekly `Financial results` workflow ingests new filings "
                "from PSX every Saturday._"
            )

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
# REPORTS TAB — Director's Report / management outlook per stock
# --------------------------------------------------------------------------
def _tone_badge(tone: float) -> str:
    """Return a coloured pill rendering the management tone score."""
    if tone is None:
        tone = 0.0
    if tone >= 0.5:
        return f":green[●● bullish (+{tone:.2f})]"
    if tone >= 0.15:
        return f":green[● mildly bullish (+{tone:.2f})]"
    if tone <= -0.5:
        return f":red[●● bearish ({tone:+.2f})]"
    if tone <= -0.15:
        return f":red[● mildly bearish ({tone:+.2f})]"
    return f":orange[— neutral ({tone:+.2f})]"


def _strength_badge(s: str) -> str:
    s = (s or "LOW").upper()
    return {"HIGH": ":green[**HIGH**]",
            "MEDIUM": ":orange[**MEDIUM**]",
            "LOW": ":gray[LOW]"}.get(s, s)


def _staleness(filing_date: str | None) -> str:
    if not filing_date:
        return ":gray[—]"
    try:
        d = datetime.strptime(filing_date, "%Y-%m-%d")
        days = (datetime.now() - d).days
        if days <= 14:  return f":green[Fresh • {days}d ago]"
        if days <= 90:  return f":orange[{days}d ago]"
        if days <= 270: return f":gray[{days}d ago]"
        return f":red[Stale • {days}d ago]"
    except Exception:
        return f":gray[{filing_date}]"


def render_reports_tab():
    """Detailed Director's Report viewer with universe overview + per-stock
    drill-down. The user picks a stock and sees the full extracted outlook,
    growth plans, risks, key financials, and historical filings — exactly
    what they would read if they downloaded the PDF themselves, but
    indexed and searchable."""
    section_header(
        "Reports & Outlook",
        "What management is actually saying. Every quarter and annual "
        "report from your universe is read by an LLM and distilled into "
        "outlook, growth plans, and risks — the part of the report most "
        "investors skip.",
        how_to_read=[
            "**Tone score** (-1 to +1) measures how bullish/bearish the "
            "narrative is. ≥+0.15 is constructive; ≤-0.15 means caution.",
            "**Guidance strength** (HIGH/MEDIUM/LOW) is how committed and "
            "specific management is — HIGH means concrete plans with "
            "numbers, LOW is platitudes.",
            "**Fresh** filings (≤14 days) carry the most signal. Anything "
            "older than ~6 months is mostly stale.",
            "Use this in combination with **Forecast** and **Fair Value**: "
            "a HIGH-tone outlook on a cheap, momentum-rising stock is the "
            "strongest setup the system can identify.",
        ],
    )

    rows = dash.latest_management_outlook(top_k=20).get("rows") or []
    if not rows:
        st.info(
            "No Director's Reports cached yet. The weekly workflow "
            "(`Financial results & Director's reports`) extracts new "
            "filings every Saturday at 11:00 PKT, and the EOD pipeline "
            "auto-triggers it within ~24 hours of an earnings event. "
            "Once that runs, this tab will populate."
        )
        return

    # ------------------------------------------------------------------
    # 1) Universe overview — heatmap of every covered stock
    # ------------------------------------------------------------------
    st.subheader("Universe outlook at a glance")
    st.caption(
        "Latest filing per stock. Click a row in the table below to "
        "see the full extract; or pick a symbol from the drill-down "
        "selector underneath."
    )

    # KPI strip
    fresh_n = sum(1 for r in rows
                    if (datetime.now() -
                        datetime.strptime(r["filing_date"], "%Y-%m-%d")).days
                    <= 14)
    bull_n = sum(1 for r in rows if (r.get("outlook_tone") or 0) > 0.15)
    bear_n = sum(1 for r in rows if (r.get("outlook_tone") or 0) < -0.15)
    high_g = sum(1 for r in rows
                   if (r.get("guidance_strength") or "").upper() == "HIGH")
    avg_tone = (sum(float(r.get("outlook_tone") or 0) for r in rows) /
                  max(len(rows), 1))

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Stocks covered", len(rows))
    k2.metric("Fresh (≤14d)", fresh_n)
    k3.metric("Bullish tone", bull_n, delta=f"{bear_n} bearish")
    k4.metric("HIGH guidance", high_g)
    k5.metric("Avg tone", f"{avg_tone:+.2f}")

    # Universe table
    import pandas as _pd
    table_rows = []
    for r in sorted(rows,
                      key=lambda x: float(x.get("outlook_tone") or 0),
                      reverse=True):
        table_rows.append({
            "Symbol": r["symbol"],
            "Period": r.get("fy_period") or r.get("doc_type") or "",
            "Filed": r.get("filing_date") or "—",
            "Tone": float(r.get("outlook_tone") or 0),
            "Guidance": (r.get("guidance_strength") or "LOW").upper(),
            "Plans": len(r.get("growth_plans") or []),
            "Risks": len(r.get("risks_mentioned") or []),
            "Capex": "✔" if r.get("capex_announced") else "",
            "Expansion": "✔" if r.get("expansion_announced") else "",
            "Summary": (r.get("outlook_summary") or "")[:100] +
                ("…" if len(r.get("outlook_summary") or "") > 100 else ""),
        })
    df_tbl = _pd.DataFrame(table_rows)
    st.dataframe(
        df_tbl,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Tone": st.column_config.NumberColumn(
                "Tone", format="%+.2f",
                help="−1 (bearish) to +1 (bullish). Computed by LLM "
                     "from the narrative.",
            ),
            "Plans": st.column_config.NumberColumn(
                "# Plans", help="Forward-looking growth/capex initiatives "
                                "mentioned"),
            "Risks": st.column_config.NumberColumn(
                "# Risks", help="Headwinds called out by management"),
            "Summary": st.column_config.TextColumn(
                "What management is saying", width="large"),
        },
    )

    st.divider()

    # ------------------------------------------------------------------
    # 2) Per-stock drill-down — full filing detail
    # ------------------------------------------------------------------
    st.subheader("Drill into one stock")
    available = sorted({r["symbol"] for r in rows})
    pick = st.selectbox(
        "Stock",
        options=available,
        index=0,
        key="reports_pick",
        help="Pick a symbol to see the full extracted outlook, every "
             "growth plan, every risk, and the filing history.",
    )

    history = dash.management_outlook_history(pick)
    if not history:
        st.warning(f"No reports cached for {pick} yet.")
        return

    latest = history[0]
    older = history[1:]

    # ---- Header card -------------------------------------------------
    st.markdown(f"### {pick} — {latest['fy_period'] or latest['doc_type']}")
    h1, h2, h3, h4 = st.columns([2, 1, 1, 1])
    h1.markdown(
        f"**Filing**: {latest['title'] or latest['doc_type']}  \n"
        f"**Date**: {latest['filing_date']}  ({_staleness(latest['filing_date'])})"
    )
    h2.markdown(f"**Tone**  \n{_tone_badge(latest['outlook_tone'])}")
    h3.markdown(f"**Guidance**  \n{_strength_badge(latest['guidance_strength'])}")
    flags = []
    if latest.get("capex_announced"):     flags.append("Capex")
    if latest.get("expansion_announced"): flags.append("Expansion")
    h4.markdown(
        f"**Signals**  \n{', '.join(flags) if flags else ':gray[—]'}"
    )

    # ---- Outlook summary --------------------------------------------
    st.markdown("#### Outlook")
    st.info(latest["outlook_summary"] or
              "_(LLM did not extract a narrative)_")

    # ---- Growth plans + risks side-by-side --------------------------
    cL, cR = st.columns(2)
    with cL:
        st.markdown("#### Growth plans / forward initiatives")
        plans = latest.get("growth_plans") or []
        if plans:
            for i, plan in enumerate(plans, 1):
                st.success(f"**{i}.** {plan}")
        else:
            st.caption("_None highlighted in this filing._")
    with cR:
        st.markdown("#### Risks management is calling out")
        risks = latest.get("risks_mentioned") or []
        if risks:
            for i, risk in enumerate(risks, 1):
                st.warning(f"**{i}.** {risk}")
        else:
            st.caption("_None highlighted in this filing._")

    # ---- Capacity & expansion (analyst-requested) -------------------
    inst_cap = latest.get("installed_capacity")
    act_prod = latest.get("actual_production")
    util_pct = latest.get("capacity_utilization_pct")
    new_prods = latest.get("new_products") or []
    if inst_cap or act_prod or util_pct is not None or new_prods:
        st.markdown("#### Capacity & expansion")
        st.caption(
            "Pulled verbatim from the Director's Report. Use this to "
            "judge whether announced capex is justified — high "
            "utilisation + expansion = real demand; low utilisation + "
            "expansion = capacity-led, demand may not absorb it."
        )
        cap_cols = st.columns(3)
        cap_cols[0].metric(
            "Installed capacity", str(inst_cap) if inst_cap else "—",
            help="Verbatim quote from management.",
        )
        cap_cols[1].metric(
            "Actual production", str(act_prod) if act_prod else "—",
            help="Verbatim quote from management.",
        )
        if util_pct is not None:
            badge = ("🟢 healthy" if util_pct >= 80
                     else "🟡 partial" if util_pct >= 60
                     else "🔴 underutilised")
            cap_cols[2].metric(
                "Utilisation", f"{util_pct:.0f}%",
                help=("Computed from the two verbatim figures above."),
            )
            cap_cols[2].caption(badge)
        else:
            cap_cols[2].metric("Utilisation", "—")
        if new_prods:
            st.markdown("**New products in the next 12 months**")
            for nprod in new_prods[:5]:
                st.success(f"• {nprod}")
        # Analyst-flagged interpretation cues
        if util_pct is not None and util_pct < 70 and (
            latest.get("capex_announced")
            or latest.get("expansion_announced")
        ):
            st.warning(
                f"Capacity utilisation is **only {util_pct:.0f}%**, "
                f"yet management has announced capex/expansion. The "
                f"binding constraint is demand, not capacity — be "
                f"cautious about extrapolating expansion as a positive."
            )
        elif util_pct is not None and util_pct >= 90 and (
            latest.get("capex_announced")
            or latest.get("expansion_announced")
        ):
            st.success(
                f"Running at **{util_pct:.0f}%** of installed capacity "
                f"— announced expansion is well-justified by demand."
            )

    # ---- Key financials ---------------------------------------------
    fin = latest.get("key_financials_called_out") or {}
    if fin:
        st.markdown("#### Numbers management chose to highlight")
        cols = st.columns(min(len(fin), 4))
        for i, (k, v) in enumerate(list(fin.items())[:8]):
            cols[i % len(cols)].metric(
                k.replace("_", " ").title(),
                str(v) if v is not None else "—",
            )

    # ---- Verbatim excerpt + PDF -------------------------------------
    with st.expander("Read the verbatim excerpt the LLM relied on",
                       expanded=False):
        st.text(latest.get("raw_excerpt") or
                  "_(no verbatim excerpt persisted)_")
    if latest.get("pdf_url"):
        st.markdown(
            f"[Open the original PDF on PSX ↗]({latest['pdf_url']})"
        )
    st.caption(
        f"Extracted by `{latest.get('extracted_by_model','?')}` in "
        f"{latest.get('extraction_seconds') or 0:.1f}s · "
        f"persisted to `data/results/reports.parquet`."
    )

    # ---- Filing history ---------------------------------------------
    if older:
        st.markdown("#### Older filings for this stock")
        st.caption(
            "Useful for spotting tone reversals — e.g. management was "
            "bullish three quarters ago and has been turning cautious."
        )
        for past in older:
            with st.expander(
                f"{past['fy_period'] or past['doc_type']} — "
                f"{past['filing_date']}  ·  tone "
                f"{past['outlook_tone']:+.2f}",
                expanded=False,
            ):
                st.markdown(f"**Tone**: {_tone_badge(past['outlook_tone'])}  "
                              f"·  **Guidance**: "
                              f"{_strength_badge(past['guidance_strength'])}")
                st.markdown(f"**Outlook.** {past['outlook_summary']}")
                if past.get("growth_plans"):
                    st.markdown("**Plans:** " +
                                  "; ".join(past["growth_plans"][:4]))
                if past.get("risks_mentioned"):
                    st.markdown("**Risks:** " +
                                  "; ".join(past["risks_mentioned"][:4]))
                if past.get("pdf_url"):
                    st.markdown(f"[Original PDF ↗]({past['pdf_url']})")


# --------------------------------------------------------------------------
# VALUE TAB — fundamental fair-value vs market price
# --------------------------------------------------------------------------
def render_value_tab():
    section_header(
        "Fair Value",
        "Are these stocks cheap or expensive vs what they're really "
        "worth? A long-term lens (6–24 months), not a short-term call.",
        how_to_read=[
            "**Looks cheap** = trading ≥25% below estimated fair value. "
            "**Looks expensive** = trading ≥10% above. **Fairly priced** "
            "is everything in between.",
            "**Quality score (0–100)** — how strong the underlying "
            "business is (profitability, debt, earnings stability). "
            "TIP: cheap + high quality = real edge; cheap + JUNK "
            "quality = a value trap to avoid.",
            "**EPS Mom** — earnings momentum: are profits growing, "
            "stable, or shrinking?",
            "**Next Event** — expected results-day. If shown in red, "
            "wait until after the announcement before adding.",
            "Refreshed weekly by the `fundamentals` GitHub Action.",
        ],
    )

    book = tools.get_universe_value_book()
    if "error" in book:
        st.error(book["error"])
        st.info("Run `python -m connectors.yfinance_fundamentals` to seed "
                "the cache, then reload this tab.")
        return
    qbook = tools.get_universe_quality_book()
    embook = tools.get_universe_earnings_momentum()
    cal = tools.get_earnings_calendar(days_ahead=21)

    q_by_sym = {r["symbol"]: r for r in (qbook.get("rows") or [])}
    em_by_sym = {r["symbol"]: r for r in (embook.get("rows") or [])}
    ev_by_sym = {r["symbol"]: r for r in (cal.get("all_rows") or [])}

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

    # Build display frame: value + quality + earnings momentum + event
    df_rows = []
    for r in rows:
        sym = r.get("symbol")
        q = q_by_sym.get(sym, {})
        em = em_by_sym.get(sym, {})
        ev = ev_by_sym.get(sym, {})
        d = ev.get("days_until")
        ev_str = (f"{ev.get('next_event_date_utc')} ({d}d)"
                  if d is not None and d <= 21 else "—")
        df_rows.append({
            "Sym": sym,
            "Sector": r.get("sector", "")[:14],
            "Px": r.get("current_price"),
            "Fair": r.get("fair_value"),
            "Upside %": r.get("upside_pct"),
            "Value Sig": r.get("signal"),
            "V-Conf": r.get("confidence", "—"),
            "Q Score": q.get("quality_score"),
            "Q Band": q.get("band", "—"),
            "EPS Mom": em.get("flag", "—"),
            "Next Event": ev_str,
            "Method": r.get("method", "")[:50],
        })
    df = pd.DataFrame(df_rows)

    def _style(r):
        styles = [""] * len(r)
        sig = str(r["Value Sig"])
        if sig == "BUY_VALUE":
            styles = ["background-color: #1f4f2f; color: white"] * len(r)
        elif sig == "SELL_VALUE":
            styles = ["background-color: #5a1f1f; color: white"] * len(r)
        elif sig == "NO_SIGNAL":
            styles = ["color: #888"] * len(r)
        return styles

    st.dataframe(df.style.apply(_style, axis=1),
                  hide_index=True, use_container_width=True)
    st.caption(
        "Combined view: **Value Sig** = fair-value signal, **Q Score** = "
        "quality 0-100 (HIGH/MED/LOW/JUNK), **EPS Mom** = earnings "
        "trajectory, **Next Event** = predicted result-day. The classic "
        "edge play is BUY_VALUE + HIGH quality + ACCELERATING/RECOVERING "
        "earnings + no event window. Avoid BUY_VALUE + JUNK = trap."
    )

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

    # ---- Why BUY_VALUE / SELL_VALUE / FAIR? (analyst-mandatory)
    # The analyst said: "every suggestion must be explained". This block
    # constructs a plain-English explanation by combining the value
    # signal, the quality gate, the earnings-momentum trajectory, the
    # sector cheapness, and any nearby earnings event.
    sig = (rec.get("signal") or "").upper()
    sig_color = ("green" if sig == "BUY_VALUE"
                  else "red" if sig == "SELL_VALUE"
                  else "blue" if sig == "FAIR" else "gray")
    st.markdown(
        f"### Why this **:{sig_color}[{rec.get('signal')}]** call?"
    )
    why_lines: list[str] = []
    method = rec.get("method") or "n/a"
    conf = rec.get("confidence") or "—"
    # Sector medians are referenced both in the "Why this call?" Step-5
    # narrative below and in the method breakdown further down. Resolve
    # once here so the variable is defined before either consumer.
    secm = rec.get("sector_medians") or {}
    if up is not None:
        if sig == "BUY_VALUE":
            why_lines.append(
                f"**Step 1 — Fair value gap.** Estimated fair value is "
                f"**{a_fair} PKR** vs market **{rec.get('current_price')} "
                f"PKR**, so the stock trades **{up:+.1f}%** below what "
                f"the underlying earnings and book value support. Any "
                f"upside above 25% qualifies as BUY_VALUE."
            )
        elif sig == "SELL_VALUE":
            why_lines.append(
                f"**Step 1 — Fair value gap.** Estimated fair value is "
                f"**{a_fair} PKR**, but the market price is "
                f"**{rec.get('current_price')} PKR**. The stock is "
                f"**{abs(up):.1f}%** above fair — anything below -10% "
                f"upside flags as SELL_VALUE."
            )
        elif sig == "FAIR":
            why_lines.append(
                f"**Step 1 — Fair value gap.** Estimated fair value is "
                f"**{a_fair} PKR** vs market **{rec.get('current_price')} "
                f"PKR** ({up:+.1f}%). That is inside the FAIR band "
                f"(-10% to +25%) — neither a clear buy nor a clear sell."
            )
    why_lines.append(
        f"**Step 2 — How fair value was estimated.** {method}.  "
        f"Confidence: **{conf}** "
        + (f"(method warnings: {', '.join(rec['warnings'])})"
           if rec.get("warnings") else "")
        + "."
    )
    # Quality gate (value trap detector)
    qrec = q_by_sym.get(pick) or {}
    if qrec.get("quality_score") is not None:
        qband = (qrec.get("band") or "—").upper()
        why_lines.append(
            f"**Step 3 — Quality gate.** Quality score "
            f"**{qrec['quality_score']}/100** ({qband}). "
            + (
                "HIGH quality + BUY_VALUE = the highest-edge setup."
                if qband == "HIGH" and sig == "BUY_VALUE"
                else "JUNK quality + BUY_VALUE = textbook value trap; "
                      "stay away even though the screen flags BUY."
                if qband == "JUNK" and sig == "BUY_VALUE"
                else "Quality is filter, not signal — only acts as a "
                      "veto when both ends agree."
            )
        )
    # Earnings momentum
    em = em_by_sym.get(pick) or {}
    if em.get("flag") and em["flag"] != "INSUFFICIENT_DATA":
        why_lines.append(
            f"**Step 4 — Earnings trajectory.** **{em['flag']}** "
            f"(YoY {em.get('yoy_growth_pct')}%, prior YoY "
            f"{em.get('prior_yoy_growth_pct')}%, 3y CAGR "
            f"{em.get('cagr_3y_pct')}%). "
            + (
                "Accelerating earnings + cheap valuation = strong setup."
                if em["flag"] == "ACCELERATING" and sig == "BUY_VALUE"
                else "Eroding earnings + apparent cheapness = often a "
                      "trap — stay sceptical of the BUY signal."
                if em["flag"] in ("EROSION", "DECELERATING")
                     and sig == "BUY_VALUE"
                else "Adds context but does not override the value call."
            )
        )
    # Sector cheapness
    if secm and (secm.get("pe_med") or secm.get("pb_med")):
        why_lines.append(
            f"**Step 5 — Sector context.** Sector medians used: "
            f"P/E **{secm.get('pe_med')}**, P/B **{secm.get('pb_med')}** "
            f"across **{secm.get('n')} peers**. The fair-value model "
            f"benchmarks the stock against this cohort, not the whole "
            f"market — so a 'cheap' read is cheap *relative to peers*."
        )
    # Nearby earnings event
    ev = ev_by_sym.get(pick) or {}
    if ev.get("days_until") is not None and ev["days_until"] <= 14:
        why_lines.append(
            f"**Step 6 — Nearby earnings event.** Likely results day "
            f"**{ev.get('next_event_date_utc')}** "
            f"(in **{ev['days_until']} days**). Hold off on adding "
            f"until after the announcement — results-day moves of "
            f"5-10% routinely overwhelm the value gap."
        )
    for line in why_lines:
        st.markdown(line)

    # Long-horizon vs short-horizon caveat
    st.info(
        "Fair-value calls have a **6-24 month horizon**. They sit "
        "next to — not on top of — the short-term Forecast tab. A "
        "cheap stock can stay cheap for many months; a SELL_VALUE "
        "stock can keep rallying on momentum. Use this view to "
        "weight position sizing, not as a 5-day timing tool."
    )

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

    # Sector medians used (variable already resolved at the top of the
    # function — see the "Why this call?" stanza above).
    if secm:
        st.caption(
            f"Sector medians used → P/E {secm.get('pe_med')}  ·  "
            f"P/B {secm.get('pb_med')}  ·  n peers {secm.get('n')}"
        )

    # ---- Snapshot ratios + sector comparison (analyst-requested) ---
    try:
        from connectors.yfinance_fundamentals import load_latest as _lf
        from brain.sector_ratios import load_sector_medians as _lsm
        f = _lf(pick) or {}
        sec = f.get("sector") or rec.get("sector") or ""
        sec_block = (_lsm().get("by_sector") or {}).get(sec, {})

        st.markdown("#### Snapshot ratios vs sector")
        st.caption(
            "Anchored on the latest PSX close. The sector column is "
            "the median across the universe sector cohort — peers "
            "sample shown beneath the table."
        )
        ratio_rows = [
            {"Metric": "P/E (Price ÷ EPS TTM)",
              "Stock":  f.get("pe_ratio"),
              "Sector median": sec_block.get("pe_med"),
              "vs sector": (f.get("pe_vs_sector_pct")
                              if f.get("pe_vs_sector_pct") is not None
                              else None)},
            {"Metric": "P/B (Price ÷ Book value)",
              "Stock":  f.get("pb_ratio"),
              "Sector median": sec_block.get("pb_med"),
              "vs sector": (f.get("pb_vs_sector_pct")
                              if f.get("pb_vs_sector_pct") is not None
                              else None)},
            {"Metric": "Dividend yield %",
              "Stock":  f.get("dividend_yield_pct"),
              "Sector median": sec_block.get("yield_med"),
              "vs sector": None},
            {"Metric": "Payout ratio % (dividend ÷ EPS)",
              "Stock":  f.get("payout_ratio_pct"),
              "Sector median": sec_block.get("payout_med"),
              "vs sector": None},
        ]
        st.dataframe(
            pd.DataFrame(ratio_rows),
            hide_index=True,
            use_container_width=True,
            column_config={
                "Stock": st.column_config.NumberColumn(format="%.2f"),
                "Sector median": st.column_config.NumberColumn(format="%.2f"),
                "vs sector": st.column_config.NumberColumn(
                    "vs sector %",
                    format="%+.0f",
                    help=("Positive = above peers (potentially "
                          "expensive on P/E or P/B)."),
                ),
            },
        )
        if sec_block.get("members"):
            st.caption(
                f"Peers ({sec}, n={sec_block.get('n', '—')}): "
                + ", ".join(sec_block["members"])
            )

        # Sarmaya.com cross-check
        try:
            from connectors.sarmaya import crosscheck as _cc
            cc = _cc(pick)
            if cc.get("sarmaya_present"):
                st.markdown("#### Cross-check vs Sarmaya.com")
                if cc.get("flags"):
                    st.warning(
                        f"⚠️ {cc['n_flags']} field(s) disagree with "
                        f"Sarmaya by more than {cc['tolerance_pct']}%. "
                        "yfinance is treated as authoritative."
                    )
                    st.dataframe(pd.DataFrame(cc["flags"]),
                                  hide_index=True,
                                  use_container_width=True)
                else:
                    st.success(
                        "✅ Sarmaya values agree within tolerance — "
                        "ratios above are corroborated."
                    )
                if cc.get("sarmaya_source_url"):
                    st.caption(
                        f"Sarmaya source: {cc['sarmaya_source_url']}"
                    )
            elif cc.get("yfinance_present"):
                st.caption(
                    "Sarmaya cross-check: no cached snapshot yet. "
                    "Run a Sarmaya refresh (`python -m "
                    "connectors.sarmaya`) to enable."
                )
        except Exception as e:
            st.caption(f"Sarmaya cross-check unavailable: "
                        f"{type(e).__name__}")
    except Exception as e:
        st.caption(f"Snapshot ratios unavailable: {type(e).__name__}: {e}")

    asof = rec.get("as_of_fundamentals")
    if asof:
        st.caption(f"Fundamentals last refreshed: `{asof}`")


# --------------------------------------------------------------------------
# NEWS TAB
# --------------------------------------------------------------------------
def render_news_tab():
    section_header(
        "News & Sentiment",
        "Pakistan business news, scored by AI for how positive or "
        "negative it is for the market and your stocks.",
        how_to_read=[
            "Each headline gets a sentiment score from −1 (very "
            "negative) to +1 (very positive).",
            "**Macro tilt** is the weighted average across all "
            "recent headlines — your single number for 'is the news "
            "supportive or hostile right now?'",
            "Filter by symbol to see headlines specific to your "
            "holdings.",
            "Refreshed three times a day by the `news_scoring` "
            "GitHub Action.",
        ],
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
    section_header(
        "Ask the Advisor",
        "Ask anything in plain English. The bot pulls live data and "
        "answers from your portfolio, news, predictions, and "
        "fundamentals — no guessing.",
        how_to_read=[
            "Try natural questions: *'should I hold MCB?'*, *'what's "
            "cheap right now?'*, *'when does PPL report?'*, *'is "
            "PSO a value trap?'*.",
            "Pick a model in the sidebar: **GitHub Models** is free "
            "(rate-limited), **Claude** is the most accurate, **Gemini** "
            "is the fastest.",
            "The bot ALWAYS uses tool calls under the hood — it can't "
            "invent prices, P&L, or news. If a number looks wrong, ask "
            "*'where did that come from?'*",
        ],
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
    section_header(
        "Strategy Tester",
        "How would the bot's strategy have performed in the past? "
        "Run it over real PSX history and see the equity curve.",
        how_to_read=[
            "**Equity curve** = how PKR 100 invested at the start "
            "would have grown over time.",
            "**CAGR** = annualised return. **Sharpe** = return per "
            "unit of risk (>1 is good, >2 is excellent).",
            "**Max drawdown** = the worst peak-to-trough loss. Real "
            "trading needs to live through these.",
            "Toggle *Use regime overlay* to see how the rule-based "
            "market filter (NORMAL / CAUTION / CRISIS) affects results.",
            "Past performance is not a guarantee of future returns.",
        ],
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
    inject_css()
    render_sidebar()
    st.markdown("# PSX Advisor")
    st.caption(
        "Your AI-powered Pakistan Stock Exchange research desk — built "
        "around a rules-based trading bot, plain-English explanations, "
        "and an advisor that grounds every answer in live data."
    )

    render_top_strip()
    render_onboarding()

    # Plain-English tab labels with the most-used items first.
    tabs = st.tabs([
        "Today",          # narrative landing
        "My Holdings",    # portfolio
        "Forecast",       # predictions
        "Reports",        # Director's reports / management outlook
        "Fair Value",     # intrinsic value + quality + earnings momentum
        "Watchlist",      # tracked symbols
        "Find Ideas",     # scanner / momentum ranking
        "News",           # scored news feed
        "Ask Advisor",    # chatbot
        "Strategy Tester",  # backtest
    ])
    with tabs[0]: render_today_tab()
    with tabs[1]: render_portfolio_tab()
    with tabs[2]: render_predictions_tab()
    with tabs[3]: render_reports_tab()
    with tabs[4]: render_value_tab()
    with tabs[5]: render_watchlist_tab()
    with tabs[6]: render_scanner_tab()
    with tabs[7]: render_news_tab()
    with tabs[8]: render_chat_tab()
    with tabs[9]: render_backtest_tab()


if __name__ == "__main__":
    main()
