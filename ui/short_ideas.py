"""Short Ideas tab — PSX stocks the bot expects to fall.

Surfaces the output of :mod:`brain.short_candidates` with three
distinct sections:

1. **Eligibility disclaimer + regime banner** — non-negotiable.
   Pakistan retail shorting is restricted; the user must verify
   borrow / SSF eligibility before acting. The regime banner makes
   it loud when the broader index is in a clean uptrend (a
   regime in which retail shorts tend to lose money even when the
   single-name thesis is correct).

2. **Strong / Watch tier table** — every candidate above the cutoff,
   ranked by composite ``short_score``. Conviction-coloured
   highlighting makes HIGH-conviction shorts pop visually.

3. **Per-stock drill-down** — for the user-selected name, the full
   bucket breakdown, suggested entry / stop / target geometry, the
   bearish drivers, and the eligibility note.

Live data hookup: every refresh of the tab calls
:func:`brain.short_candidates.rank_shorts` which itself queries the
verdict synthesizer, predictions log, scored news, technical
snapshot, macro impact engine, and intraday circuit breakers — i.e.
all the same live feeds the long side already uses.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent


_CONV_BG = {
    "HIGH":   "#7f1d1d",   # red-900 (strong short)
    "MEDIUM": "#9a3412",   # orange-800 (viable short)
    "LOW":    "#3f3f46",   # zinc-700 (watch only)
}
_CONV_FG = {"HIGH": "#fff", "MEDIUM": "#fff", "LOW": "#e5e7eb"}


def _section_header() -> None:
    st.markdown(
        '<div class="psx-section-header"><h2>Short Ideas</h2>'
        '<p>Stocks the bot thinks are likely to fall over the next '
        '~5 sessions — ranked by a composite short_score combining '
        'the verdict synthesizer, the 5-day forecast, news '
        'sentiment, technical breakdown patterns, sector macro '
        'headwinds, and intraday lower-circuit hits.</p></div>',
        unsafe_allow_html=True,
    )
    with st.expander("How to read this", expanded=False):
        st.markdown(
            "- **short_score (0-100)** — the higher, the more "
            "bearish signals are stacking. Above 70 is a clean "
            "multi-signal short; 45-70 is a viable single-name "
            "short; 10-44 is a *watch* candidate worth monitoring.\n"
            "- **Conviction** — HIGH / MEDIUM / LOW. The bot "
            "automatically downgrades one notch when the broader "
            "regime is RISK_ON, because shorting a bull market is "
            "the most common retail mistake.\n"
            "- **Drivers** — the specific bearish signals firing for "
            "this name (verdict, prediction, news, technical, "
            "macro, intraday).\n"
            "- **Suggested entry / stop / target** — short geometry "
            "is mirrored from the long side: enter on a bounce "
            "(slightly above current), stop above entry (the bounce "
            "continuing), target below entry (sized to the "
            "predicted move).\n"
            "- **Eligibility** — Pakistan retail shorting only "
            "works via PSX Single Stock Futures or NCCPL Securities "
            "Lending & Borrowing. The bot keeps a conservative "
            "likely-eligible list but you MUST verify with your "
            "broker before sizing a position."
        )


def _render_disclaimer(disclaimer: str) -> None:
    st.warning(
        "Pakistan retail shorting is restricted. Verify borrow "
        "availability, SSF margin, and venue with your broker "
        "before acting on any recommendation here. The "
        "short_score is a research signal, not a trade order."
    )


def _render_regime_banner(regime: dict) -> None:
    if not regime:
        return
    aligned = bool(regime.get("shorts_aligned"))
    name = regime.get("regime", "UNKNOWN")
    note = regime.get("note", "")
    if aligned:
        st.info(f"**Regime: {name}** — {note}")
    else:
        st.error(f"**Regime: {name}** — {note}")


def _render_table(candidates: list[dict]) -> str | None:
    """Render the ranked candidates table; return the user-selected
    symbol (or None)."""
    if not candidates:
        return None
    rows = []
    for c in candidates:
        rows.append({
            "Symbol":    c["symbol"],
            "Sector":    c.get("sector") or "—",
            "Score":     c["short_score"],
            "Conviction": c.get("conviction"),
            "Verdict":   c.get("verdict_action") or "—",
            "5d pred":   (f"{c.get('predicted_return_5d_pct'):+.1f}%"
                          if c.get("predicted_return_5d_pct")
                              is not None else "—"),
            "Price":     (f"{c.get('current_price_pkr'):.2f}"
                          if c.get("current_price_pkr") else "—"),
            "Eligible?": ("Likely"
                          if c.get("eligibility", {})
                              .get("likely_eligible")
                          else "Verify"),
            "Top driver": (c.get("drivers") or ["—"])[0][:80],
        })
    df = pd.DataFrame(rows)

    def _style(row):
        conv = (row.get("Conviction") or "LOW").upper()
        bg = _CONV_BG.get(conv, "#27272a")
        fg = _CONV_FG.get(conv, "#e5e7eb")
        return [f"background-color: {bg}; color: {fg}"] * len(row)

    st.dataframe(
        df.style.apply(_style, axis=1),
        hide_index=True,
        use_container_width=True,
    )

    syms = [c["symbol"] for c in candidates]
    return st.selectbox(
        "Drill into a candidate:",
        options=syms,
        index=0,
        help="Pick a ticker to see the full bucket breakdown, "
             "suggested entry / stop / target, and eligibility "
             "notes.",
    )


def _render_drilldown(c: dict) -> None:
    if not c:
        return
    st.divider()
    st.markdown(f"### {c['symbol']} — short drill-down")

    col1, col2, col3 = st.columns([1.1, 1.3, 1.6])
    with col1:
        st.metric("short_score", f"{c['short_score']:.0f} / 100",
                   delta=c.get("conviction"))
        cp = c.get("current_price_pkr")
        if cp:
            st.caption(f"Current price: PKR {cp:.2f}")
        st.caption(f"Sector: {c.get('sector') or '—'}")
    with col2:
        sub = c.get("subscores") or {}
        st.markdown("**Score breakdown**")
        st.markdown(
            f"- Synthesizer: {sub.get('synth', 0):.1f} / 30\n"
            f"- Prediction: {sub.get('prediction', 0):.1f} / 25\n"
            f"- News: {sub.get('news', 0):.1f} / 15\n"
            f"- Technical: {sub.get('technical', 0):.1f} / 15\n"
            f"- Macro: {sub.get('macro', 0):.1f} / 10\n"
            f"- Intraday: {sub.get('intraday', 0):.1f} / 5"
        )
    with col3:
        if c.get("suggested_entry_pkr"):
            st.markdown("**Suggested short geometry**")
            st.markdown(
                f"- Entry: PKR "
                f"**{c['suggested_entry_pkr']:.2f}** "
                f"(slightly above current — wait for a bounce)\n"
                f"- Stop: PKR "
                f"**{c['suggested_stop_pkr']:.2f}**  (bounce "
                f"continues against you)\n"
                f"- Target: PKR "
                f"**{c['suggested_target_pkr']:.2f}** (mid forecast)\n"
                f"- Reward / risk: **{c.get('risk_reward', 0):.2f}**"
            )

    cw = c.get("concentration_warning")
    if cw:
        st.warning(f"Concentration cap: {cw}")

    st.markdown("**Bearish drivers**")
    drivers = c.get("drivers") or []
    if drivers:
        for d in drivers:
            st.markdown(f"- {d}")
    else:
        st.caption("No specific drivers — score is from a single "
                    "weak signal.")

    elig = c.get("eligibility") or {}
    st.markdown("**Eligibility hint**")
    st.caption(elig.get("disclaimer", ""))
    notes = elig.get("notes") or []
    for n in notes:
        st.markdown(f"- {n}")


# --- public entrypoint -------------------------------------------------------


def render() -> None:
    _section_header()

    try:
        from brain.short_candidates import rank_shorts
    except Exception as e:
        st.error(f"Short Ideas module failed to import: "
                  f"{type(e).__name__}: {e}")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        min_conv = st.selectbox(
            "Minimum conviction",
            options=["LOW", "MEDIUM", "HIGH"],
            index=0,
            help="LOW shows everything (including weak watch-list "
                 "candidates). MEDIUM filters to viable shorts. "
                 "HIGH filters to multi-signal strong shorts only.",
        )
    with c2:
        max_n = st.slider("Max results", min_value=5, max_value=30,
                            value=20, step=5)

    with st.spinner("Ranking shorts across the universe..."):
        try:
            res = rank_shorts(min_conviction=min_conv,
                                max_results=int(max_n))
        except Exception as e:
            st.error(f"Could not compute short candidates: "
                      f"{type(e).__name__}: {e}")
            return

    _render_disclaimer(res.get("disclaimer", ""))
    _render_regime_banner(res.get("regime", {}))

    candidates = res.get("candidates") or []
    if not candidates:
        st.info(
            "**No short candidates today.** Either the bot's signals "
            "are not pointing bearish on any universe name, or the "
            "minimum-conviction filter is too high. Try lowering it "
            "to LOW to see watch-list candidates."
        )
        return

    high = [c for c in candidates
            if (c.get("conviction") or "").upper() == "HIGH"]
    med = [c for c in candidates
           if (c.get("conviction") or "").upper() == "MEDIUM"]
    low = [c for c in candidates
           if (c.get("conviction") or "").upper() == "LOW"]

    cmh = st.columns(3)
    cmh[0].metric("High conviction shorts", len(high))
    cmh[1].metric("Medium conviction shorts", len(med))
    cmh[2].metric("Watch list (low)", len(low))

    st.divider()
    st.markdown(f"### Ranked candidates  ({len(candidates)} total)")
    sym = _render_table(candidates)
    if sym:
        chosen = next((c for c in candidates if c["symbol"] == sym),
                       None)
        _render_drilldown(chosen)


# --- chatbot helper ----------------------------------------------------------


def get_short_candidates(min_conviction: str = "LOW",
                            max_results: int = 10) -> dict:
    """Tool-friendly wrapper for the chatbot.

    Returns the same payload as :func:`brain.short_candidates.rank_shorts`
    so the advisor can answer "what stocks should I short today?" in
    plain English with sourced drivers.
    """
    from brain.short_candidates import rank_shorts
    return rank_shorts(min_conviction=min_conviction,
                          max_results=max_results)
