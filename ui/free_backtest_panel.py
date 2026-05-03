"""Free 24-month playbook backtest panel — surfaces the rules-engine
backtest produced by ``scripts/end_to_end_test.py`` (Mon/Wed/Fri walk
across the past 24 months, no LLM in the loop, $0 cost).

The panel reads the most-recent backtest JSON in ``data/_health/`` (in
priority order: Tier-1 v2 -> Tier-1 -> Tier-0 v2 -> Tier-0 -> baseline
24m -> rolling 11m) and renders four blocks:

  1. **Headline scorecard** — coverage, precision, recall, alpha vs
     buy-and-hold, GAPs avoided. Colour-coded badges.
  2. **Strategy vs Buy-and-Hold equity curve** — cumulative return on
     PKR 100 starting capital, computed from the JSON's weekly_records.
  3. **Per-case attribution** — every case that actually fired in the
     window, sorted by hit count, with mean fwd 5d / 21d returns.
  4. **Patches active** — which Tier-0 / Tier-1 patches the active
     backtest reflects (read from the case docstrings).

The whole panel is read-only; re-running the harness is a separate
GitHub Actions workflow (see ``.github/workflows/playbook_validation.yml``)
or an ad-hoc CLI call (``python scripts/end_to_end_test.py --start ...
--end ...``).

Cost-of-render: pure JSON + light pandas; no LLM, no network.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
HEALTH = ROOT / "data" / "_health"
COMBINED_REPORT = HEALTH / "backtest_24m_2026-05-03.md"

# Priority order: newest / most-patched first. The panel uses the first
# file that exists.
BACKTEST_PATHS: list[Path] = [
    HEALTH / "end_to_end_test_tier1_v2.json",
    HEALTH / "end_to_end_test_tier1.json",
    HEALTH / "end_to_end_test_tier0_v2.json",
    HEALTH / "end_to_end_test_tier0.json",
    HEALTH / "end_to_end_test_24m.json",
    HEALTH / "end_to_end_test.json",
]


# ---------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------
def _load_backtest() -> tuple[dict | None, Path | None]:
    """Return (payload, source_path) for the most-recent backtest."""
    for p in BACKTEST_PATHS:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), p
            except (json.JSONDecodeError, OSError):
                continue
    return None, None


def _file_mtime_str(p: Path) -> str:
    try:
        ts = datetime.fromtimestamp(p.stat().st_mtime)
        return ts.strftime("%Y-%m-%d %H:%M")
    except OSError:
        return "unknown"


# ---------------------------------------------------------------------
# Badge / colour helpers — mirror ui/phase1_backtest.py styling
# ---------------------------------------------------------------------
def _badge_color(pct: float | None, *, higher_is_better: bool = True) -> str:
    if pct is None:
        return "#27272a"
    if higher_is_better:
        if pct >= 75: return "#14532d"   # green
        if pct >= 60: return "#854d0e"   # amber
        return "#7f1d1d"                  # red
    if pct <= 25: return "#14532d"
    if pct <= 40: return "#854d0e"
    return "#7f1d1d"


def _alpha_color(pp: float | None) -> str:
    if pp is None:
        return "#27272a"
    if pp >= 25:  return "#14532d"
    if pp >= 10:  return "#1e3a8a"  # blue (good but not amazing)
    if pp >= 0:   return "#854d0e"
    return "#7f1d1d"


def _badge(label: str, value: str, color: str, sub: str = "") -> str:
    return (
        f'<div style="background:{color};color:#fff;padding:14px 16px;'
        f'border-radius:10px;text-align:center;">'
        f'<div style="font-size:11px;opacity:0.85;letter-spacing:0.5px;'
        f'text-transform:uppercase;">{label}</div>'
        f'<div style="font-size:30px;font-weight:700;line-height:1.1;'
        f'margin-top:4px;">{value}</div>'
        f'<div style="font-size:11px;opacity:0.85;margin-top:4px;">{sub}'
        f'</div></div>'
    )


# ---------------------------------------------------------------------
# Render blocks
# ---------------------------------------------------------------------
def _render_headline(payload: dict) -> None:
    h = payload.get("headline") or {}
    pnl = payload.get("pnl") or {}

    n_dates = h.get("n_dates", 0)
    n_sig = h.get("n_significant", 0)
    n_match = h.get("n_dates_with_match", 0)
    coverage = h.get("match_coverage_pct")
    precision = h.get("precision")
    recall = h.get("recall")
    n_gap = h.get("gap", 0)
    n_hit = h.get("hit", 0)
    n_miss = h.get("miss", 0)

    alpha = pnl.get("alpha_pct")
    sys_cum = pnl.get("system_cum_pct")
    bh_cum = pnl.get("bh_cum_pct")
    n_cash = pnl.get("n_cash_weeks", 0)
    n_def = pnl.get("n_avoided_drawdown_weeks", 0)

    cols = st.columns(4)
    with cols[0]:
        st.markdown(_badge(
            "Coverage",
            f"{coverage:.1f}%" if coverage is not None else "n/a",
            _badge_color(coverage),
            f"matched {n_match} of {n_dates} dates"
        ), unsafe_allow_html=True)
    with cols[1]:
        st.markdown(_badge(
            "Precision",
            f"{precision:.1f}%" if precision is not None else "n/a",
            _badge_color(precision),
            f"{n_hit} hits / {n_hit + n_miss} fires"
        ), unsafe_allow_html=True)
    with cols[2]:
        st.markdown(_badge(
            "Recall on big moves",
            f"{recall:.1f}%" if recall is not None else "n/a",
            _badge_color(recall),
            f"caught {n_sig - n_gap} of {n_sig} significant moves"
        ), unsafe_allow_html=True)
    with cols[3]:
        st.markdown(_badge(
            "Alpha vs Buy & Hold",
            f"+{alpha:.1f}pp" if alpha is not None else "n/a",
            _alpha_color(alpha),
            f"strategy {sys_cum:+.1f}% / B&H {bh_cum:+.1f}%"
            if (sys_cum is not None and bh_cum is not None) else ""
        ), unsafe_allow_html=True)

    # Defensive context — when did the system go to cash, did it work?
    cash_ratio = (100.0 * n_def / n_cash) if n_cash else None
    sub = (f"{n_def} of {n_cash} cash weeks correctly defensive "
            f"({cash_ratio:.0f}%)") if cash_ratio is not None else (
            "system never went to cash in this window")
    st.caption(
        f"24-month walk: **{n_dates} trading dates**, "
        f"**{n_sig} significant moves** (|fwd_5d|≥4% or |fwd_21d|≥8%). "
        f"{sub}."
    )


def _render_pnl_chart(payload: dict) -> None:
    pnl = payload.get("pnl") or {}
    records = pnl.get("weekly_records") or []
    if not records:
        st.info("No weekly P&L records in this backtest payload.")
        return

    df = pd.DataFrame(records)
    if df.empty or "date" not in df.columns:
        st.info("Weekly records lack a `date` column.")
        return
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # Cumulative equity curves on PKR 100 starting capital. The JSON
    # stores per-week percentage returns (sys_ret_pct, bh_ret_pct);
    # convert to multipliers and compound.
    if "sys_ret_pct" not in df.columns or "bh_ret_pct" not in df.columns:
        st.info("Weekly records missing return columns.")
        return
    df["Strategy"] = (1.0 + df["sys_ret_pct"] / 100.0).cumprod() * 100.0
    df["Buy & Hold"] = (1.0 + df["bh_ret_pct"] / 100.0).cumprod() * 100.0

    chart_df = df.set_index("date")[["Strategy", "Buy & Hold"]]
    st.markdown("#### Equity curve — PKR 100 starting capital")
    st.line_chart(chart_df, height=300)
    st.caption(
        "Compounding the playbook's weekly cash-or-long decisions vs a "
        "passive long-only universe. Cash weeks are flat (0% return); "
        "long weeks track the universe Friday-to-Friday."
    )


def _render_case_table(payload: dict, *, top_n: int = 12) -> None:
    cases = payload.get("by_case") or []
    fired = [c for c in cases if (c.get("n_fired") or 0) > 0]
    if not fired:
        st.info("No cases fired in this backtest window.")
        return

    fired.sort(key=lambda c: -(c.get("n_fired") or 0))

    rows = []
    for c in fired[:top_n]:
        rows.append({
            "case_id": c.get("id"),
            "expected": c.get("expected"),
            "fires": c.get("n_fired"),
            "hits": c.get("n_hit"),
            "misses": c.get("n_miss"),
            "hit_rate_%": (round(c.get("hit_rate"), 1)
                            if c.get("hit_rate") is not None else None),
            "mean_fwd_5d_%": (round(c.get("mean_fwd_5d_pct"), 2)
                               if c.get("mean_fwd_5d_pct") is not None else None),
            "mean_fwd_21d_%": (round(c.get("mean_fwd_21d_pct"), 2)
                                if c.get("mean_fwd_21d_pct") is not None else None),
        })
    df = pd.DataFrame(rows)

    st.markdown(f"#### Per-case attribution (top {top_n} most-fired)")
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "case_id":         st.column_config.TextColumn("Case", width="medium"),
            "expected":        st.column_config.TextColumn("Bias", width="small"),
            "fires":           st.column_config.NumberColumn("Fires", format="%d"),
            "hits":            st.column_config.NumberColumn("Hits", format="%d"),
            "misses":          st.column_config.NumberColumn("Misses", format="%d"),
            "hit_rate_%":      st.column_config.ProgressColumn(
                                  "Hit rate", min_value=0, max_value=100,
                                  format="%.1f%%"),
            "mean_fwd_5d_%":   st.column_config.NumberColumn(
                                  "Mean fwd 5d", format="%.2f%%"),
            "mean_fwd_21d_%":  st.column_config.NumberColumn(
                                  "Mean fwd 21d", format="%.2f%%"),
        },
    )

    n_orphan = sum(1 for c in cases if (c.get("n_fired") or 0) == 0)
    st.caption(
        f"{len(fired)} of {len(cases)} cases fired in this window "
        f"({n_orphan} orphan cases — mostly defensive cases that need "
        "specific regimes to trigger, plus FIPI / value-book cases that "
        "are live-only and not backfillable)."
    )


def _render_patches_summary() -> None:
    """Static summary of which Tier-0 / Tier-1 patches are active.
    Mirrors the patch table in
    `data/_health/backtest_24m_2026-05-03.md`."""
    st.markdown("#### Active playbook patches (Tier 0 + Tier 1)")
    rows = [
        # (case, before, after, headline win)
        ("circular_debt_resolution_large", "60-day event decay -> noisy 27 fires (41% hit)",
         "Tier-0: shrunk decay 60->7d", "**41% -> 100% hit; mean fwd 21d -4.1% -> +8.1%**"),
        ("imf_review_completed", "21-day decay -> 18 fires (6% hit)",
         "Tier-0: shrunk decay 21->5d + Tier-1: kse100_21d_lte:0.05 suppressor (now active after kse100 staleness fix)",
         "18 fires -> 2 well-gated fires (-89%)"),
        ("sbp_rate_hike_shock", "25bp threshold -> 4 false fires (0% hit)",
         "Tier-0: added last_hike_bps_gte:100 gate", "4 false fires eliminated"),
        ("imf_sba_eff_approval", "no rally suppressor -> fired on every approval",
         "Tier-1: kse100_21d_lte:0.10 suppressor",
         "20 -> 16 fires; 100% hit preserved"),
        ("volume_confirmation_breakout", "orphan in 24m backtest",
         "Tier-1: replay-safe `as_of` parameter; threshold gte:5 (broad participation)",
         "**0 -> 209 fires at 70% hit, +3.7% mean fwd 21d**"),
        ("fipi_capitulation", "Rs 1.5bn threshold (rare)",
         "Tier-1: loosened to Rs 1.0bn",
         "Live-only signal; effect not visible in 24m backtest"),
        ("mf_capitulation_with_value", "3-month streak + 3 value names (rare)",
         "Tier-1: loosened to 2-month + 2 value names",
         "Live-only signal; effect not visible in 24m backtest"),
    ]
    df = pd.DataFrame(
        rows, columns=["Case", "Was", "Patch", "Result"])
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "Case":   st.column_config.TextColumn(width="medium"),
            "Was":    st.column_config.TextColumn(width="medium"),
            "Patch":  st.column_config.TextColumn(width="large"),
            "Result": st.column_config.TextColumn(width="large"),
        },
    )


# ---------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------
def render() -> None:
    """Render the full free-backtest panel. Safe to call from any tab."""
    payload, source = _load_backtest()
    if payload is None or source is None:
        st.info(
            "No 24-month backtest results found. Run "
            "`python scripts/end_to_end_test.py --start 2024-05-01 "
            "--end 2026-05-01` to generate them."
        )
        return

    st.markdown("### 24-Month Free Playbook Backtest")
    st.caption(
        f"Source: `{source.relative_to(ROOT).as_posix()}` "
        f"(generated {_file_mtime_str(source)}). "
        "Mon/Wed/Fri walk over the past 24 months, no LLM in the loop, "
        "$0 cost. The headline numbers below are what the rules engine "
        "alone produces — no Master Strategist required."
    )

    _render_headline(payload)
    st.divider()
    _render_pnl_chart(payload)
    st.divider()
    _render_case_table(payload)
    st.divider()
    _render_patches_summary()

    if COMBINED_REPORT.exists():
        st.caption(
            f"Full write-up with three-way before/after tables, the "
            f"strategy / data / honest-caveats sections, and the Tier-2 "
            f"backlog: `{COMBINED_REPORT.relative_to(ROOT).as_posix()}`."
        )
