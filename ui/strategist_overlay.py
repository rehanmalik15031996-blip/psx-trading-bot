"""Shared "Master Strategist says…" overlay helpers.

Used by every per-symbol UI surface (My Holdings position cards,
Forecast drill-down, Find Ideas buy cards, Short Ideas drill-down,
Fair Value table) so the canonical top-of-stack call is visible
alongside whatever lens-specific call the tab is showing.

Keeping this in its own module avoids a circular import between
``ui/app.py`` (which mounts the overlay on most tabs) and
``ui/short_ideas.py`` (which is a sibling tab module).
"""
from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# Lookup — cached for the whole render so every per-symbol card on every tab
# can overlay the strategist's view without reloading the JSON.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60)  # type: ignore[misc]
def actions_by_symbol() -> dict[str, dict]:
    """Return ``{symbol: action_dict}`` for the cached strategist run.

    Returns an empty dict when no strategist run exists yet (the UI
    then silently skips the overlay)."""
    try:
        from brain import master_strategist as ms
        decision = ms.load_cached() or {}
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for a in (decision.get("actions") or []):
        sym = (a or {}).get("symbol")
        if sym:
            out[sym.upper()] = a
    return out


def view_for(sym: str) -> dict | None:
    """Return the strategist's per-symbol view, or ``None`` if absent."""
    return actions_by_symbol().get((sym or "").upper())


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
_STRATEGIST_BUCKET_COLORS = {
    "BUY":   ("#136f3a", "#d6f5e1"),
    "ADD":   ("#136f3a", "#d6f5e1"),
    "HOLD":  ("#0f5cad", "#dceaff"),
    "WATCH": ("#0f5cad", "#dceaff"),
    "TRIM":  ("#a5751c", "#fff1cf"),
    "AVOID": ("#a83c1a", "#ffe1d5"),
    "SHORT": ("#7a1010", "#ffd5d5"),
}


def render(sym: str,
           local_action: str = "",
           *,
           inline: bool = False) -> None:
    """Render a 'Master Strategist says…' badge for one symbol.

    Use on every per-symbol card so the user always sees the
    canonical (top-of-stack) call alongside the per-tab signal.
    Silently no-ops when no strategist run exists yet.

    When ``local_action`` is provided and conflicts with the
    strategist's bucket in direction, an inline yellow "the
    strategist disagrees" warning is rendered so the user is never
    left wondering which answer to act on.
    """
    sv = view_for(sym)
    if not sv:
        return
    bucket = (sv.get("bucket") or "").upper() or "HOLD"
    conv = (sv.get("conviction") or "").upper() or "MEDIUM"
    reason = (sv.get("reason") or "").strip()
    fg, bg = _STRATEGIST_BUCKET_COLORS.get(bucket, ("#222", "#eee"))
    badge = (
        f'<span style="background:{bg};color:{fg};padding:3px 10px;'
        f'border-radius:12px;font-weight:600;font-size:0.85em;">'
        f'Strategist: {bucket} · {conv}</span>'
    )
    if inline:
        st.markdown(badge, unsafe_allow_html=True)
    else:
        st.markdown(
            f'{badge}'
            + (f' &nbsp;<span style="opacity:0.78">— {reason}</span>'
               if reason else ''),
            unsafe_allow_html=True,
        )

    if local_action and bucket:
        local_dir = action_to_direction(local_action)
        strat_dir = bucket_to_direction(bucket)
        if local_dir and strat_dir and local_dir != strat_dir:
            st.warning(
                f"This tab says **{local_action}** but the Master "
                f"Strategist says **{bucket}**. The Strategist is the "
                f"top-of-stack call (it sees flows, playbook analogues "
                f"and macro context this tab does not). When in doubt, "
                f"follow the Strategist."
            )


def action_to_direction(action: str) -> str:
    """Coarse direction label for a per-tab action string."""
    a = (action or "").upper()
    if a.startswith("BUY") or a == "ADD":
        return "BULL"
    if "SELL" in a or a in ("AVOID", "SHORT"):
        return "BEAR"
    if "TRIM" in a or "CAUTION" in a:
        return "BEAR"
    return "NEUTRAL"


def bucket_to_direction(bucket: str) -> str:
    b = (bucket or "").upper()
    if b in ("BUY", "ADD"):
        return "BULL"
    if b in ("AVOID", "SHORT", "TRIM"):
        return "BEAR"
    return "NEUTRAL"
