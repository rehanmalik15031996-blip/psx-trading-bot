"""System Health tab + freshness strip helpers.

Single source of truth for "is the data pipeline working right now?"
in the Streamlit UI. Reads ``data/_health/<workflow>.json`` files
written by ``scripts/_health.write_status`` and renders:

  - ``render_freshness_strip()`` — a compact 30-pixel coloured row
    shown above every page in :mod:`ui.app`. Surfaces RED / AMBER
    workflows so the analyst notices a stale feed before they act
    on a stale prediction.
  - ``render()``                  — the full System Health tab. One
    row per workflow with last-success timestamp, last note, run
    badge and a 30-day sparkline of run frequency built from
    ``data/_health/_history.parquet``.

The helpers degrade gracefully: if the SLA module or health
directory is missing the strip silently shows nothing rather than
breaking the UI.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
HEALTH_DIR = ROOT / "data" / "_health"


# ----- shared helpers --------------------------------------------------------


def _load_status(workflow: str) -> dict | None:
    p = HEALTH_DIR / f"{workflow}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _age_seconds(as_of_iso: str | None) -> float | None:
    if not as_of_iso:
        return None
    try:
        ts = datetime.fromisoformat(as_of_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except Exception:
        return None


def _humanize(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.0f}m"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}d"


def _is_weekday() -> bool:
    return datetime.now(timezone.utc).weekday() < 5


def _is_intraday_window() -> bool:
    pkt_now = datetime.now(timezone.utc) + timedelta(hours=5)
    if pkt_now.weekday() >= 5:
        return False
    open_t = pkt_now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = pkt_now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_t <= pkt_now <= close_t


def _evaluate_one(sla) -> dict:
    body = _load_status(sla.workflow)
    age = _age_seconds(body.get("as_of") if body else None)
    last_ok = bool(body.get("ok")) if body else False

    if sla.weekday_only and not _is_weekday():
        badge, reason = "GREEN", "weekend (PSX closed)"
    elif sla.intraday_only and not _is_intraday_window():
        badge, reason = "GREEN", "outside trading session"
    elif body is None:
        # Same rationale as scripts/health_check.py — keep this
        # AMBER so the freshness strip does not light up red on a
        # fresh deploy where no workflow has run yet.
        badge, reason = "AMBER", "no health file yet (workflow not " \
                                  "observed since deploy)"
    elif not last_ok:
        badge = "RED"
        reason = f"last run failed: {body.get('note', '')}"
    elif age is None:
        badge, reason = "AMBER", "could not parse timestamp"
    elif age >= sla.red_seconds:
        badge = "RED"
        reason = f"stale {_humanize(age)} (red {_humanize(sla.red_seconds)})"
    elif age >= sla.amber_seconds:
        badge = "AMBER"
        reason = f"stale {_humanize(age)} (amber {_humanize(sla.amber_seconds)})"
    else:
        badge = "GREEN"
        reason = f"{_humanize(age)} ago: {body.get('note', '')}"

    return {
        "workflow": sla.workflow,
        "badge":    badge,
        "reason":   reason,
        "age":      age,
        "as_of":    (body or {}).get("as_of", ""),
        "note":     (body or {}).get("note", ""),
        "ok":       last_ok,
        "amber":    sla.amber_seconds,
        "red":      sla.red_seconds,
        "schedule": sla.note,
    }


def _evaluate_all() -> list[dict]:
    try:
        from config.data_slas import SLAS
    except Exception:
        return []
    return [_evaluate_one(s) for s in SLAS]


# ----- 30px freshness strip --------------------------------------------------


_BADGE_COLOR = {
    "GREEN": "#16a34a",   # emerald-600
    "AMBER": "#d97706",   # amber-600
    "RED":   "#dc2626",   # red-600
}

_BADGE_BG = {
    "GREEN": "rgba(22,163,74,0.10)",
    "AMBER": "rgba(217,119,6,0.10)",
    "RED":   "rgba(220,38,38,0.10)",
}


def render_freshness_strip() -> None:
    """Render a compact one-line freshness banner above the tabs.

    Shows the worst-current-badge, a short summary, and (only when
    AMBER / RED) the offending workflow name(s). Stays out of the
    way when everything is green — the analyst should never have to
    read it on a normal day.
    """
    rows = _evaluate_all()
    if not rows:
        return

    counts = {"GREEN": 0, "AMBER": 0, "RED": 0}
    for r in rows:
        counts[r["badge"]] = counts.get(r["badge"], 0) + 1

    # GitHub Actions scheduled runs can be delayed 1-8 hours during
    # high-load periods. If a single morning workflow is AMBER/RED but
    # everything else is green, it is almost always a scheduling delay —
    # not a pipeline failure. The strip shows an upgrade note in that
    # case so the analyst is not unnecessarily alarmed.
    n_red = counts.get("RED", 0)
    n_amber = counts.get("AMBER", 0)
    if n_red:
        worst = "RED"
        delay_note = (
            " GitHub Actions scheduling delays (up to 8h) are normal "
            "on high-load days — check the System Health tab for details."
            if n_red <= 2 else ""
        )
        msg = (f"{n_red} data source(s) BREACHING freshness SLA — "
               f"predictions may be acting on stale inputs.{delay_note}")
    elif n_amber:
        worst = "AMBER"
        msg = (f"{n_amber} data source(s) approaching freshness "
               "limit — refresh expected soon.")
    else:
        worst = "GREEN"
        msg = "All data sources within freshness SLA."

    color = _BADGE_COLOR[worst]
    bg = _BADGE_BG[worst]

    offenders = [r for r in rows if r["badge"] in ("RED", "AMBER")]
    offenders_html = ""
    if offenders:
        items = " | ".join(
            f"<b>{r['workflow']}</b> ({r['badge'].lower()}, "
            f"{_humanize(r['age'])})"
            for r in offenders[:6]
        )
        offenders_html = f" &middot; {items}"

    html = (
        f"<div style='background:{bg};border-left:4px solid {color};"
        f"padding:6px 12px;font-size:13px;line-height:18px;"
        f"border-radius:4px;margin-bottom:8px'>"
        f"<span style='color:{color};font-weight:600'>"
        f"&#9679; Data freshness:</span> {msg}{offenders_html}"
        f"</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


# ----- full System Health tab ------------------------------------------------


def render() -> None:
    """Full System Health tab.

    Per-workflow card grid, the SLA reference table, and a 30-day
    rolling success-history chart.
    """
    st.header("System Health")
    st.caption(
        "Live freshness check for every data source feeding the bot. "
        "Each card shows the last-success timestamp from "
        "`data/_health/<workflow>.json` and is graded against the "
        "SLA in `config/data_slas.py`. The same logic powers the "
        "freshness banner at the top of every page and the daily "
        "`health_check.yml` build that emails on red breaches."
    )

    rows = _evaluate_all()
    if not rows:
        st.warning(
            "Health files not present yet — first commit of the "
            "freshness pipeline. Wait for the next workflow run on "
            "`main` or run `python scripts/health_check.py` "
            "locally."
        )
        return

    counts = {"GREEN": 0, "AMBER": 0, "RED": 0}
    for r in rows:
        counts[r["badge"]] = counts.get(r["badge"], 0) + 1

    c1, c2, c3 = st.columns(3)
    c1.metric("Green", counts.get("GREEN", 0))
    c2.metric("Amber", counts.get("AMBER", 0))
    c3.metric("Red",   counts.get("RED", 0))

    st.divider()
    _render_grid(rows)

    st.divider()
    _render_sla_table(rows)

    st.divider()
    _render_history_chart()

    st.divider()
    render_universe_manager()


# --------------------------------------------------------------------------
# Universe manager — pick / preview / promote KSE-100 candidate stocks
# --------------------------------------------------------------------------
# The user explicitly asked: "we had a pipeline for best ranking and even
# add new stock if it is under KSE-100" — surface that pipeline as a
# first-class panel so the analyst can:
#   1. See the full candidate pool + which slots are filled today
#   2. Preview the AUC-ranking (dry-run of scripts/select_universe.py)
#   3. Promote a new candidate by re-running the selector


def _read_universe_ranking_cache() -> dict | None:
    """Latest ranking cached at ``data/universe_ranking.json`` (or None)."""
    p = ROOT / "data" / "universe_ranking.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_universe_manager() -> None:
    """Universe-management panel — current universe + candidate pool +
    re-rank action.

    Renders inside the System Health tab so the analyst has one place
    to inspect both data freshness and universe composition. Uses
    ``scripts/select_universe.py --dry-run`` to preview a re-rank
    without overwriting ``config/universe.py``.
    """
    st.markdown("### Universe Manager")
    st.caption(
        "The bot trades a 35-stock KSE-100-mirroring universe — "
        "7 user-required tickers + 28 flex slots picked by AUC ranking. "
        "Use this panel to inspect the candidate pool, preview a "
        "re-rank, and promote a new name into the universe."
    )

    try:
        from config.candidates import (
            CANDIDATE_POOL, REQUIRED_TICKERS, sector_of_candidate,
        )
        from config.universe import symbols as _universe_syms
    except Exception as e:
        st.warning(f"Could not load universe config: {e}")
        return

    current = set(_universe_syms())
    required = set(REQUIRED_TICKERS)
    pool_syms = [c[0] for c in CANDIDATE_POOL]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Universe (live)", len(current))
    c2.metric("Required (locked)", len(required))
    c3.metric("Candidate pool", len(pool_syms))
    in_universe_from_pool = sum(1 for s in pool_syms if s in current)
    c4.metric("Pool → universe", in_universe_from_pool)

    cache = _read_universe_ranking_cache()
    if cache:
        as_of = cache.get("as_of") or "—"
        st.caption(
            f"Last AUC re-ranking: **{as_of}** "
            f"({cache.get('n_evaluated', '?')} candidates evaluated)"
        )
    else:
        st.caption(
            "No cached re-ranking on disk yet. Click **Preview re-rank** "
            "below to generate one."
        )

    # ---- Candidate-pool table — current status per name
    pool_rows = []
    for sym, sec in CANDIDATE_POOL:
        status = ("In universe" if sym in current
                   else "Required" if sym in required
                   else "Waiting")
        rank_info = (cache or {}).get("by_symbol", {}).get(sym, {})
        pool_rows.append({
            "Symbol":        sym,
            "Sector":        sec,
            "Status":        status,
            "AUC":           rank_info.get("auc"),
            "Rank":          rank_info.get("rank"),
            "Reason":        rank_info.get("reason") or "",
        })
    # Required tickers (those NOT in the candidate pool):
    for sym in required:
        if sym not in {p[0] for p in CANDIDATE_POOL}:
            pool_rows.insert(0, {
                "Symbol": sym, "Sector": sector_of_candidate(sym) or "—",
                "Status": "Required", "AUC": None, "Rank": None,
                "Reason": "[user-required]",
            })

    import pandas as pd
    df = pd.DataFrame(pool_rows)
    if "AUC" in df.columns:
        df = df.sort_values(
            ["Status", "AUC"], ascending=[True, False],
            na_position="last",
        )
    st.dataframe(df, hide_index=True, use_container_width=True)

    # ---- Actions
    st.markdown("##### Actions")
    cA, cB = st.columns(2)
    with cA:
        if st.button(
            "Preview re-rank (dry-run)",
            use_container_width=True,
            help="Runs the selector with --dry-run and writes the "
                 "ranking to data/universe_ranking.json. Does NOT "
                 "modify config/universe.py.",
        ):
            _run_universe_select(dry_run=True)
    with cB:
        if st.button(
            "Re-rank AND apply (writes config/universe.py)",
            use_container_width=True,
            type="secondary",
            help="Runs the selector for real. Updates "
                 "config/universe.py with the new flex slots. "
                 "Restart Streamlit afterwards for the change to "
                 "take effect.",
        ):
            _run_universe_select(dry_run=False)


def _run_universe_select(*, dry_run: bool) -> None:
    """Invoke ``scripts/select_universe.py`` and stream output."""
    import subprocess
    import sys as _sys

    args = [_sys.executable, str(ROOT / "scripts" / "select_universe.py")]
    if dry_run:
        args.append("--dry-run")

    title = "Previewing re-rank…" if dry_run else "Re-ranking and applying…"
    with st.spinner(title):
        try:
            res = subprocess.run(
                args, capture_output=True, text=True,
                timeout=900, cwd=str(ROOT),
            )
        except subprocess.TimeoutExpired:
            st.error("Universe selector timed out (>15 min).")
            return
        except FileNotFoundError:
            st.error(
                "scripts/select_universe.py not found — is the repo "
                "properly checked out?"
            )
            return
        except Exception as e:
            st.error(f"{type(e).__name__}: {e}")
            return

    if res.returncode == 0:
        if dry_run:
            st.success(
                "Re-ranking complete (dry-run). Scroll up — the "
                "candidate-pool table now shows AUC + rank for "
                "each name. Click **Re-rank AND apply** when ready."
            )
        else:
            st.success(
                "Universe updated. **Restart Streamlit** for the "
                "change to take effect (the price cache is keyed on "
                "the universe at startup)."
            )
        out_tail = (res.stdout or "").strip().splitlines()[-15:]
        if out_tail:
            st.code("\n".join(out_tail), language="text")
    else:
        err_tail = (
            (res.stderr or res.stdout or "").strip()
            .splitlines()[-15:]
        )
        st.error("Selector failed:\n```\n"
                 + "\n".join(err_tail) + "\n```")


def _render_grid(rows: list[dict]) -> None:
    cols = st.columns(2)
    for i, r in enumerate(rows):
        with cols[i % 2]:
            color = _BADGE_COLOR[r["badge"]]
            bg = _BADGE_BG[r["badge"]]
            st.markdown(
                f"<div style='background:{bg};"
                f"border-left:4px solid {color};"
                f"padding:10px 14px;border-radius:6px;"
                f"margin-bottom:10px'>"
                f"<div style='font-weight:600;font-size:14px'>"
                f"<span style='color:{color}'>&#9679;</span> "
                f"{r['workflow']}"
                f"<span style='float:right;color:{color};"
                f"font-size:12px'>{r['badge']}</span></div>"
                f"<div style='font-size:12px;color:#6b7280;margin-top:3px'>"
                f"Last refresh: {r['as_of'] or '—'} "
                f"({_humanize(r['age'])} ago)</div>"
                f"<div style='font-size:12px;margin-top:4px'>"
                f"{r['note'] or '—'}</div>"
                f"<div style='font-size:11px;color:#9ca3af;margin-top:4px'>"
                f"Schedule: {r['schedule']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


def _render_sla_table(rows: list[dict]) -> None:
    import pandas as pd

    df = pd.DataFrame([
        {
            "Workflow":    r["workflow"],
            "Badge":       r["badge"],
            "Last refresh":  r["as_of"] or "—",
            "Age":         _humanize(r["age"]),
            "Amber after": _humanize(r["amber"]),
            "Red after":   _humanize(r["red"]),
            "Reason":      r["reason"],
        }
        for r in rows
    ])
    st.subheader("SLA reference")
    st.dataframe(df, hide_index=True, use_container_width=True)


def _render_history_chart() -> None:
    st.subheader("30-day run history")
    st.caption(
        "Daily run counts per workflow over the last 30 days, "
        "from `data/_health/_history_<workflow>.parquet` files "
        "(one per workflow to avoid concurrency conflicts). A flat "
        "zero line means the workflow has not been firing — usually "
        "a sign that GitHub deactivated the schedule and a "
        "`gh workflow enable` is needed."
    )
    history_files = sorted(HEALTH_DIR.glob("_history_*.parquet"))
    if not history_files:
        st.info("No history yet — `_history_<workflow>.parquet` files "
                "will populate after the next runs commit health files.")
        return
    try:
        import pandas as pd

        frames: list[pd.DataFrame] = []
        for p in history_files:
            try:
                frames.append(pd.read_parquet(p))
            except Exception:
                continue
        if not frames:
            st.info("History files unreadable.")
            return
        df = pd.concat(frames, ignore_index=True)
        df["ts"] = pd.to_datetime(df["as_of"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        df = df[df["ts"] >= cutoff]
        if df.empty:
            st.info("No runs in the last 30 days.")
            return
        df["day"] = df["ts"].dt.date
        daily = (df.groupby(["day", "workflow"])
                   .size()
                   .reset_index(name="runs"))
        pivot = daily.pivot(index="day", columns="workflow",
                              values="runs").fillna(0)
        st.line_chart(pivot)
    except Exception as e:
        st.warning(f"Could not render history chart: "
                    f"{type(e).__name__}: {e}")
