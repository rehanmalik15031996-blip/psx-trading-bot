"""Phase-1 backtest panel — surfaces the harness output in Streamlit.

Reads the on-disk artefacts produced by ``scripts/phase1_backtest.py``:

  * ``data/backtest/phase1_summary.json`` — aggregate metrics
  * ``data/backtest/phase1_signals.parquet`` — per (symbol, date) panel
  * ``data/backtest/phase1_predictions.parquet`` — every LLM prediction

Renders three blocks:

  1. **Headline scorecard** — green / amber / red badges for the buy-side
     hit rate, sell-side hit rate, conviction calibration, and signal IC
     winners.
  2. **Per-dataset signal table** — IC + buy/sell hit rates per dataset.
  3. **Per-symbol prediction accuracy** — colour-coded ranking.

The panel intentionally mirrors the markdown report so the analyst can
read the same numbers in the UI or the docs/ tree. A "Refresh" button
re-runs the harness on demand (so the analyst can backtest after a new
prediction lands).
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
BT_DIR = ROOT / "data" / "backtest"
SUMMARY_PATH = BT_DIR / "phase1_summary.json"
SIGNALS_PATH = BT_DIR / "phase1_signals.parquet"
PREDS_PATH = BT_DIR / "phase1_predictions.parquet"


# --- helpers ----------------------------------------------------------------


def _load_summary(window_days: int | None = None) -> dict | None:
    """Load a summary; if window_days is given, prefer the archived
    `phase1_summary_{N}d.json` over the rolling latest snapshot.
    """
    if window_days is not None:
        archive = BT_DIR / f"phase1_summary_{int(window_days)}d.json"
        if archive.exists():
            try:
                return json.loads(archive.read_text(encoding="utf-8"))
            except Exception:
                pass
    if not SUMMARY_PATH.exists():
        return None
    try:
        return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _list_archived_windows() -> list[int]:
    """Return calendar-day windows that have archived summaries."""
    out: list[int] = []
    for p in BT_DIR.glob("phase1_summary_*d.json"):
        try:
            n = int(p.stem.replace("phase1_summary_", "")
                          .replace("d", ""))
            out.append(n)
        except Exception:
            continue
    return sorted(out)


def _badge_color(pct: float | None, *, higher_is_better: bool = True
                  ) -> str:
    """Green/amber/red badge background for a hit-rate percentage."""
    if pct is None:
        return "#27272a"
    if higher_is_better:
        if pct >= 65: return "#14532d"   # green
        if pct >= 50: return "#854d0e"   # amber
        return "#7f1d1d"                  # red
    if pct <= 35: return "#14532d"
    if pct <= 50: return "#854d0e"
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


# --- panels -----------------------------------------------------------------


def _render_headline(summary: dict) -> None:
    pred = summary.get("prediction_backtest") or {}
    sig = summary.get("signal_backtest") or {}
    by_dir = pred.get("by_direction") or {}
    by_conv = pred.get("by_conviction") or {}

    bear = by_dir.get("BEARISH") or {}
    bull = by_dir.get("BULLISH") or {}

    cols = st.columns(4)
    with cols[0]:
        st.markdown(_badge(
            "Sell-side hit rate",
            f"{bear.get('direction_hit_rate_pct', 0):.1f}%",
            _badge_color(bear.get("direction_hit_rate_pct"),
                          higher_is_better=True),
            f"n={bear.get('n', 0)} BEARISH calls"
        ), unsafe_allow_html=True)
    with cols[1]:
        st.markdown(_badge(
            "Buy-side hit rate",
            f"{bull.get('direction_hit_rate_pct', 0):.1f}%",
            _badge_color(bull.get("direction_hit_rate_pct"),
                          higher_is_better=True),
            f"n={bull.get('n', 0)} BULLISH calls"
        ), unsafe_allow_html=True)
    with cols[2]:
        st.markdown(_badge(
            "Overall hit rate",
            f"{pred.get('overall_direction_hit_rate_pct', 0):.1f}%",
            _badge_color(pred.get("overall_direction_hit_rate_pct"),
                          higher_is_better=True),
            f"n={pred.get('n_predictions', 0)} predictions"
        ), unsafe_allow_html=True)
    with cols[3]:
        st.markdown(_badge(
            "Mean abs. error",
            f"{pred.get('overall_mae_pct', 0):.2f}%",
            _badge_color(
                100 - (pred.get("overall_mae_pct") or 99),
                higher_is_better=True),
            "expected vs realized"
        ), unsafe_allow_html=True)

    # Universe-wide regime context
    st.caption(
        f"Backtest window **{summary.get('window_start')} → "
        f"{summary.get('window_end')}**. Universe forward 5d mean "
        f"{sig.get('fwd_5d_mean_pct', 0):+.2f}%, "
        f"{sig.get('fwd_5d_pos_pct', 0):.1f}% of stocks finished up."
    )


def _render_findings(summary: dict) -> None:
    """Mirror the markdown executive summary's key bullets."""
    pred = summary.get("prediction_backtest") or {}
    sig  = summary.get("signal_backtest") or {}
    by_dir = pred.get("by_direction") or {}
    by_conv = pred.get("by_conviction") or {}
    sigs = sig.get("signals") or {}

    findings: list[str] = []
    bear, bull = by_dir.get("BEARISH") or {}, by_dir.get("BULLISH") or {}
    if bear.get("n", 0) >= 5:
        findings.append(
            f"**Sell calls work.** BEARISH predictions hit "
            f"{bear['direction_hit_rate_pct']:.1f}% with mean realized "
            f"{bear['mean_realized_pct']:+.2f}% (n={bear['n']}). "
            f"This is the bot's most reliable signal — the Short Ideas "
            f"tab is built on top of it."
        )
    if bull.get("n", 0) >= 3 and bull.get("direction_hit_rate_pct",
                                              0) < 50:
        findings.append(
            f"**Buy calls struggled in this window.** BULLISH "
            f"predictions hit only {bull['direction_hit_rate_pct']:.1f}% "
            f"with mean realized {bull['mean_realized_pct']:+.2f}% "
            f"(n={bull['n']}). The market was in a mean-reversion "
            f"regime, which the long side does not yet exploit."
        )

    strong_neg = sorted(
        ((k, v) for k, v in sigs.items()
         if (v.get("spearman_ic") or 0) <= -0.20
            and v.get("n", 0) >= 50),
        key=lambda kv: kv[1]["spearman_ic"],
    )
    if strong_neg:
        findings.append(
            "**Mean-reversion regime detected.** Top-tercile "
            "(overbought / strong-momentum) names *underperform* the "
            "next 5 days; bottom-tercile names outperform. Strongest "
            "inverse signals: "
            + ", ".join(f"`{k}` (IC {v['spearman_ic']:+.2f})"
                          for k, v in strong_neg[:3])
            + "."
        )
    by_sym = pred.get("by_symbol") or []
    perfect = [r for r in by_sym
                if r.get("hit_pct", 0) >= 99 and r.get("n", 0) >= 2]
    zero    = [r for r in by_sym
                if r.get("hit_pct", 0) <= 1 and r.get("n", 0) >= 2]
    if perfect:
        findings.append(
            "**Perfect-call symbols (100% hit, n≥2):** "
            + ", ".join(f"`{r['symbol']}`" for r in perfect[:6])
            + "."
        )
    if zero:
        findings.append(
            "**Always-wrong symbols (0% hit, n≥2):** "
            + ", ".join(f"`{r['symbol']}`" for r in zero[:6])
            + " — these names need a strategy review."
        )

    if not findings:
        st.info("Not enough realized predictions yet to derive "
                 "headline findings. Check back tomorrow once another "
                 "session is recorded.")
        return
    st.markdown("**Headline findings**")
    for f in findings:
        st.markdown(f"- {f}")


def _render_signal_table(summary: dict) -> None:
    sig = summary.get("signal_backtest") or {}
    sigs = sig.get("signals") or {}
    if not sigs:
        return
    rows = []
    for name, s in sigs.items():
        rows.append({
            "Signal":      name,
            "n":           s.get("n"),
            "Spearman IC": s.get("spearman_ic"),
            "Buy hit %":   s.get("bull_bucket_hit_rate_pct"),
            "Buy mean fwd %":  s.get("bull_bucket_mean_fwd_pct"),
            "Sell hit %":  s.get("bear_bucket_hit_rate_pct"),
            "Sell mean fwd %": s.get("bear_bucket_mean_fwd_pct"),
        })
    df = pd.DataFrame(rows).sort_values("Spearman IC", key=abs,
                                            ascending=False)
    st.markdown(
        "**Per-dataset point-in-time accuracy.** Top / bottom "
        "terciles of every signal vs realized 5-day return. Spearman "
        "IC is the rank correlation — positive means signal aligns "
        "with returns; negative means inverse (mean reversion). At "
        f"n={int(sig.get('n_obs', 0))} observations across "
        f"{int(sig.get('n_unique_symbols', 0))} symbols, "
        "ICs above ±0.05 are meaningful."
    )

    def _row_style(row):
        ic = row.get("Spearman IC")
        if ic is None:
            return [""] * len(row)
        if abs(ic) >= 0.20:
            return ["background-color:#1e3a8a;color:#fff"] * len(row)
        if abs(ic) >= 0.05:
            return ["background-color:#3f3f46;color:#e5e7eb"] * len(row)
        return [""] * len(row)

    st.dataframe(df.style.apply(_row_style, axis=1).format({
        "Spearman IC":     "{:+.4f}",
        "Buy mean fwd %":  "{:+.3f}",
        "Sell mean fwd %": "{:+.3f}",
    }), hide_index=True, use_container_width=True)


def _render_prediction_breakdowns(summary: dict) -> None:
    pred = summary.get("prediction_backtest") or {}
    if not pred.get("n_predictions"):
        return

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**By predicted direction**")
        rows = []
        for d, s in (pred.get("by_direction") or {}).items():
            rows.append({
                "Direction": d,
                "n":         s.get("n"),
                "Hit %":     s.get("direction_hit_rate_pct"),
                "Mean realized %": s.get("mean_realized_pct"),
                "MAE %":     s.get("mae_pct"),
            })
        df = pd.DataFrame(rows)

        def _dirstyle(r):
            hit = r.get("Hit %") or 0
            if hit >= 65: bg = "#14532d"
            elif hit >= 50: bg = "#854d0e"
            else: bg = "#7f1d1d"
            return [f"background-color:{bg};color:#fff"] * len(r)
        st.dataframe(df.style.apply(_dirstyle, axis=1).format({
            "Mean realized %": "{:+.3f}",
            "MAE %":           "{:.3f}",
        }), hide_index=True, use_container_width=True)
    with c2:
        st.markdown("**By conviction**")
        rows = []
        for c, s in (pred.get("by_conviction") or {}).items():
            rows.append({
                "Conviction": c,
                "n":          s.get("n"),
                "Hit %":      s.get("direction_hit_rate_pct"),
                "MAE %":      s.get("mae_pct"),
            })
        df = pd.DataFrame(rows).sort_values("Hit %", ascending=False)
        st.dataframe(df.style.format({"MAE %": "{:.3f}"}),
                       hide_index=True, use_container_width=True)

    st.markdown("**By symbol** — green = ≥66% hit, amber = 33-65%, red = <33%")
    rows = pred.get("by_symbol") or []
    if rows:
        df = pd.DataFrame(rows)

        def _symstyle(r):
            hit = r.get("hit_pct") or 0
            if hit >= 66: bg = "#14532d"
            elif hit >= 33: bg = "#854d0e"
            else: bg = "#7f1d1d"
            return [f"background-color:{bg};color:#fff"] * len(r)
        st.dataframe(df.style.apply(_symstyle, axis=1).format({
            "hit_pct": "{:.1f}",
            "mean_realized": "{:+.2f}",
        }), hide_index=True, use_container_width=True)


# --- public entrypoint ------------------------------------------------------


def _engine2_source_picker(summary: dict) -> tuple[str, dict]:
    """Let the analyst pick which Engine-2 sample drives the headline
    scorecard / breakdowns: live LLM log (small but real LLM calls),
    walk-forward rules (large but deterministic), or combined.

    Returns ``(label, summary_view)`` where ``summary_view`` is a
    *shallow-copied* summary with ``prediction_backtest`` rewritten to
    the chosen sample so the existing render helpers don't need to
    change.
    """
    wf = summary.get("walkforward_backtest") or {}
    combined = summary.get("prediction_backtest_combined") or {}
    pred = summary.get("prediction_backtest") or {}

    # Only show the picker when at least one alternative source exists
    if not (wf.get("n_predictions") or combined.get("n_predictions")):
        return "Live LLM log", summary

    options: list[str] = []
    if pred.get("n_predictions"):
        options.append(f"Live LLM log (n={pred['n_predictions']})")
    if wf.get("n_predictions"):
        options.append(f"Walk-forward rules (n={wf['n_predictions']})")
    if combined.get("n_predictions"):
        options.append(f"Combined (n={combined['n_predictions']})")

    default_idx = 0
    if wf.get("n_predictions") and wf.get("n_predictions", 0) >= 200:
        default_idx = next(
            (i for i, o in enumerate(options)
             if o.startswith("Walk-forward")), 0)

    picked = st.radio(
        "Engine 2 source",
        options=options,
        index=default_idx,
        horizontal=True,
        help=("Choose which prediction sample drives the scorecard "
              "and breakdowns. The live LLM log is small (n~46) "
              "because the bot only started logging on 2026-04-23. "
              "The walk-forward rules sample is much larger "
              "(n~1,400) — every (date, symbol) pair is scored "
              "deterministically against point-in-time data."),
        key="phase1_engine2_source")

    summary_view = dict(summary)
    if picked.startswith("Walk-forward"):
        summary_view["prediction_backtest"] = wf
        st.info(
            "**Walk-forward rules backtest active.** Every (date, "
            "symbol) pair in the window scored by the deterministic "
            "rules engine against point-in-time inputs. The LLM "
            "judgement layer is **not** exercised in this sample — "
            "this measures the bot's deterministic logic only. "
            "Caveats: latest-only fundamentals, sparse historical "
            "news (rules engine ignores news so this only affects "
            "Engine 1's news IC), Phase-1 LightGBM signal replaced "
            "with a deterministic 60-day momentum cross-section to "
            "avoid training-data lookahead.",
            icon="⚙",
        )
        return "Walk-forward rules", summary_view
    if picked.startswith("Combined"):
        summary_view["prediction_backtest"] = combined
        st.info(
            "**Combined view.** Live LLM predictions and walk-forward "
            "rules predictions are pooled for the headline scorecard. "
            "Useful for a one-number summary; for like-for-like "
            "comparisons pick a single source.",
            icon="🔀",
        )
        return "Combined", summary_view
    return "Live LLM log", summary


def _window_picker() -> tuple[int | None, dict | None]:
    """Render the window picker and return (window_days, loaded_summary)."""
    archived = _list_archived_windows()
    options = ["Latest run"] + [f"{n} days" for n in archived]
    picked = st.radio("Backtest window",
                       options=options,
                       index=0 if not archived else (
                           options.index("60 days") if 60 in archived
                           else 0),
                       horizontal=True,
                       help="Pick a previously-archived window or the "
                            "most recent harness run.",
                       key="phase1_window_picker")
    if picked == "Latest run":
        return None, _load_summary()
    n = int(picked.replace(" days", ""))
    return n, _load_summary(window_days=n)


def _render_comparison(archived: list[int]) -> None:
    """Two-column side-by-side IC table for any two archived windows."""
    if len(archived) < 2:
        return
    a, b = archived[0], archived[-1]
    sa = _load_summary(window_days=a)
    sb = _load_summary(window_days=b)
    if not sa or not sb:
        return
    sigs_a = ((sa.get("signal_backtest") or {}).get("signals") or {})
    sigs_b = ((sb.get("signal_backtest") or {}).get("signals") or {})
    keys = sorted(set(sigs_a) | set(sigs_b),
                   key=lambda k: -abs((sigs_b.get(k) or {})
                                        .get("spearman_ic") or 0))
    rows = []
    for k in keys:
        ic_a = (sigs_a.get(k) or {}).get("spearman_ic")
        ic_b = (sigs_b.get(k) or {}).get("spearman_ic")
        flip = (ic_a is not None and ic_b is not None
                and (ic_a * ic_b) < 0
                and (abs(ic_a) >= 0.10 or abs(ic_b) >= 0.10))
        rows.append({
            "Signal":   k,
            f"{a}d IC": ic_a,
            f"{b}d IC": ic_b,
            "Flag":     ("REGIME FLIP" if flip
                          else ""),
        })
    df = pd.DataFrame(rows)

    def _flagstyle(r):
        if r.get("Flag") == "REGIME FLIP":
            return ["background-color:#7f1d1d;color:#fff"] * len(r)
        return [""] * len(r)
    st.markdown(
        f"**Side-by-side: last {a} days vs last {b} days.** "
        "Signals where the IC sign flipped (and the magnitude is "
        "meaningful in at least one window) are flagged in red — "
        "those are the regime-change indicators."
    )
    st.dataframe(df.style.apply(_flagstyle, axis=1).format({
        f"{a}d IC": "{:+.4f}",
        f"{b}d IC": "{:+.4f}",
    }), hide_index=True, use_container_width=True)


def render() -> None:
    st.markdown("### Phase-1 backtest — accuracy review")
    st.caption(
        "Rigorous accuracy review of the bot's predictions vs realized "
        "PSX prices. Engines covered: (1) per-dataset signal IC across "
        "all 35 stocks point-in-time, (2) live LLM prediction hit-rate "
        "on the 16 stocks with logged predictions, and (2b) **walk-"
        "forward rules backtest** — every (date, symbol) pair scored "
        "deterministically against point-in-time data (~1,400 sample "
        "vs ~46 from the LLM log). Re-run the harness after each new "
        "prediction batch to refresh; re-run "
        "`scripts/walkforward_predictions.py` to refresh Engine 2b."
    )

    archived = _list_archived_windows()
    if archived:
        window_days, summary = _window_picker()
    else:
        summary = _load_summary()
        window_days = None
    if summary is None:
        st.warning(
            "No backtest artefacts on disk yet. Run "
            "`python scripts/phase1_backtest.py` once to populate "
            "`data/backtest/`."
        )
        if st.button("Run backtest now", type="primary",
                       key="phase1_run_first"):
            with st.spinner("Running phase-1 backtest (~45 sec)…"):
                try:
                    res = subprocess.run(
                        [sys.executable,
                         str(ROOT / "scripts" / "phase1_backtest.py")],
                        capture_output=True, text=True,
                        timeout=300, cwd=str(ROOT))
                    if res.returncode == 0:
                        st.success("Backtest complete. Reloading…")
                        st.rerun()
                    else:
                        st.error(f"Backtest failed:\n{res.stderr}")
                except Exception as e:
                    st.error(f"Could not run backtest: "
                              f"{type(e).__name__}: {e}")
        return

    asof = summary.get("as_of") or "?"
    cols = st.columns([3, 1])
    with cols[0]:
        st.caption(f"Last refreshed: **{asof}**.  Window: "
                    f"{summary.get('window_start')} → "
                    f"{summary.get('window_end')}.")
    with cols[1]:
        if st.button("Refresh backtest", key="phase1_refresh"):
            with st.spinner("Re-running phase-1 backtest…"):
                try:
                    res = subprocess.run(
                        [sys.executable,
                         str(ROOT / "scripts" / "phase1_backtest.py")],
                        capture_output=True, text=True,
                        timeout=300, cwd=str(ROOT))
                    if res.returncode == 0:
                        st.success("Refreshed.")
                        st.rerun()
                    else:
                        st.error(f"Refresh failed:\n{res.stderr}")
                except Exception as e:
                    st.error(f"Could not refresh: "
                              f"{type(e).__name__}: {e}")

    st.divider()

    # Pick which Engine-2 sample drives the scorecard / breakdowns.
    # Returns the (possibly rewritten) summary that downstream render
    # helpers consume.
    source_label, summary_for_engine2 = _engine2_source_picker(summary)

    _render_headline(summary_for_engine2)
    st.divider()
    _render_findings(summary)
    st.divider()
    _render_signal_table(summary)

    # If we have multiple archived windows, show the side-by-side
    # comparison so the analyst can spot regime changes (e.g.
    # 14-day MEAN-REVERSION inside a 60-day MOMENTUM regime).
    if archived and len(archived) >= 2:
        st.divider()
        _render_comparison(archived)

    st.divider()
    st.markdown(f"#### Engine 2 breakdowns — *{source_label}*")
    _render_prediction_breakdowns(summary_for_engine2)
    st.divider()

    st.caption(
        "Full markdown reports at `docs/phase1_backtest_<window>.md` "
        "and the side-by-side write-up at "
        "`docs/phase1_backtest_comparison.md`. Raw artefacts at "
        "`data/backtest/phase1_*.parquet`."
    )
