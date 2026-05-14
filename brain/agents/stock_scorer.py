"""Agent B: Per-Stock Scorer.

Combines the briefing's already-computed per-stock signals into a
single compact ranking table for the Master Strategist (Agent C) and
the UI tabs to consume.

Inputs (all already in the briefing):
  - predictions.predictions     [list of per-stock 5d forecasts]
  - top_buys.ideas              [BUY ideas with thesis]
  - universe_ranking.ranking    [momentum + vol filter]
  - value_book[sym]             [BUY_VALUE/FAIR/SELL_VALUE classification]
  - quality_book[sym]           [quality score]
  - verdict_universe[sym]       [7-lens reconciled verdict]
  - volume_signals              [breakout / accumulation flags]
  - mf_holdings                 [institutional flow signals]
  - macro_impact.by_sector      [sector-level tailwind / headwind score]

Output (one entry per universe stock):
  {
    "symbol": ...,
    "sector": ...,
    "score": float in [-1, +1] composite,
    "action": "BUY"|"ADD"|"HOLD"|"WATCH"|"TRIM"|"AVOID"|"SHORT",
    "conviction": "LOW"|"MEDIUM"|"HIGH",
    "expected_5d_net_pct": float,
    "expected_21d_pct": float | null,
    "entry_price": float,
    "components": {
      "predict": float in [-1, +1],
      "value":   float in [-1, +1],
      "quality": float in [-1, +1],
      "momentum":float in [-1, +1],
      "macro":   float in [-1, +1],
      "flows":   float in [-1, +1],
      "volume":  float in [-1, +1],
    },
    "why":  "1-line explanation of biggest contributor(s)",
    "key_drivers": [...],
    "key_risks":   [...],
    "tags":        [...],
  }

Fallback-first: every column is derived deterministically from the
briefing's existing rule-based outputs (predictions, value_book, etc.
are themselves rule-based fallbacks when the LLM is unavailable, so
this layer is fully rule-based and ALWAYS produces output).
"""
from __future__ import annotations

import math
import os
from typing import Any


# ---------------------------------------------------------------------------
# Component scorers (each returns a float in [-1, +1])
# ---------------------------------------------------------------------------
def _score_prediction(pred: dict | None) -> tuple[float, str]:
    """Map predictions.{direction,conviction,expected_net_5d_pct} -> score."""
    if not pred:
        return 0.0, ""
    dir_ = (pred.get("direction") or "").upper()
    conv = (pred.get("conviction") or "MEDIUM").upper()
    net  = pred.get("expected_net_5d_pct")
    sign = +1 if dir_ == "BULLISH" else (-1 if dir_ == "BEARISH" else 0)
    mag  = {"HIGH": 0.9, "MEDIUM": 0.55, "LOW": 0.25}.get(conv, 0.4)
    sc   = sign * mag
    # Pull magnitude from expected_net if available
    if net is not None and sign != 0:
        # cap at +-1 over a +-5% expected move
        sc = max(-1.0, min(1.0, (sign * mag) + (float(net) / 10.0)))
    why = ""
    if pred.get("key_drivers"):
        why = pred["key_drivers"][0]
    return sc, why


def _score_value(value_entry: dict | None) -> tuple[float, str]:
    if not value_entry:
        return 0.0, ""
    band = (value_entry.get("signal") or value_entry.get("verdict") or "").upper()
    map_ = {"BUY_VALUE": +0.8, "FAIR": 0.0,
            "SELL_VALUE": -0.6, "NO_SIGNAL": 0.0}
    return map_.get(band, 0.0), (f"value:{band}" if band else "")


def _score_quality(quality_entry: dict | None) -> tuple[float, str]:
    if not quality_entry:
        return 0.0, ""
    score = quality_entry.get("score") or quality_entry.get("composite_score")
    if score is None:
        return 0.0, ""
    try:
        s = float(score)
    except (ValueError, TypeError):
        return 0.0, ""
    # quality is typically 0-100; normalise to [-1, +1] centred at 50
    return max(-1.0, min(1.0, (s - 50) / 50)), f"quality:{s:.0f}"


def _score_momentum(rank_entry: dict | None) -> tuple[float, str]:
    """High mom_150d_log_ret + passes_vol_filter = positive score."""
    if not rank_entry:
        return 0.0, ""
    mom = rank_entry.get("mom_150d_log_ret")
    passes_vol = rank_entry.get("passes_vol_filter", True)
    if mom is None:
        return 0.0, ""
    try:
        m = float(mom)
    except (ValueError, TypeError):
        return 0.0, ""
    # Squash via tanh: mom of +0.3 => ~+0.29, mom of -0.3 => ~-0.29
    sc = math.tanh(m * 1.5)
    if not passes_vol:
        sc *= 0.5
    note = f"mom150d={m*100:+.1f}%"
    if not passes_vol:
        note += " (high-vol)"
    return sc, note


def _score_macro_tilt(sector: str, sector_tilts: dict) -> tuple[float, str]:
    if not sector or not sector_tilts:
        return 0.0, ""
    t = sector_tilts.get(sector, 0)
    if t == 0:
        return 0.0, ""
    sc = max(-1.0, min(1.0, t / 5.0))
    return sc, f"macro({sector}{t:+d})"


def _score_flows(symbol: str, mf_payload: dict | None) -> tuple[float, str]:
    if not mf_payload:
        return 0.0, ""
    per_stock = mf_payload.get("per_stock_signals") or {}
    sig = per_stock.get(symbol) or {}
    if not sig:
        # Check top accumulated / distributed lists
        top_acc = {it.get("symbol", "").upper()
                   for it in (mf_payload.get("top_accumulated_180d") or [])}
        top_dist = {it.get("symbol", "").upper()
                    for it in (mf_payload.get("top_distributed_180d") or [])}
        if symbol.upper() in top_acc:
            return +0.4, "mf_accumulation_180d"
        if symbol.upper() in top_dist:
            return -0.4, "mf_distribution_180d"
        return 0.0, ""
    streak = sig.get("mf_accumulation_streak") or 0
    dist   = sig.get("mf_distribution_streak") or 0
    sc = 0.0
    parts = []
    if streak >= 2:
        sc += min(0.7, 0.25 * streak)
        parts.append(f"mf_accum_{streak}m")
    if dist >= 2:
        sc -= min(0.7, 0.25 * dist)
        parts.append(f"mf_dist_{dist}m")
    return max(-1.0, min(1.0, sc)), ",".join(parts)


def _score_volume(symbol: str, vol_payload: dict | None) -> tuple[float, str]:
    if not vol_payload:
        return 0.0, ""
    breakout = vol_payload.get("breakout_symbols") or []
    accum    = vol_payload.get("accumulation_symbols") or []
    decay    = vol_payload.get("distribution_symbols") or []
    s_upper = symbol.upper()
    if s_upper in (str(x).upper() for x in breakout):
        return +0.5, "vol_breakout"
    if s_upper in (str(x).upper() for x in accum):
        return +0.3, "vol_accumulation"
    if s_upper in (str(x).upper() for x in decay):
        return -0.3, "vol_distribution"
    return 0.0, ""


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------
COMPONENT_WEIGHTS = {
    "predict":  0.30,   # the 5d forecast (highest weight; combines everything)
    "value":    0.10,
    "quality":  0.10,
    "momentum": 0.15,
    "macro":    0.20,   # sector-level tailwind / headwind
    "flows":    0.10,
    "volume":   0.05,
}


def _composite_score(components: dict[str, float]) -> float:
    out = 0.0
    for k, w in COMPONENT_WEIGHTS.items():
        out += components.get(k, 0) * w
    return max(-1.0, min(1.0, out))


def _score_to_action(score: float, conviction_hint: str = "") -> tuple[str, str]:
    """Map composite score -> action + conviction."""
    s = score
    if s >= 0.55:
        return "BUY",  "HIGH"
    if s >= 0.30:
        return "ADD",  "MEDIUM"
    if s >= 0.10:
        return "HOLD", "MEDIUM"
    if s >= -0.10:
        return "HOLD", "LOW"
    if s >= -0.30:
        return "WATCH","LOW"
    if s >= -0.55:
        return "TRIM", "MEDIUM"
    return "AVOID", "HIGH"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_universe(briefing: dict,
                    macro_summary: dict | None = None,
                    *, use_llm: bool = False) -> list[dict]:
    """Run Agent B. Returns one row per universe stock, ranked by
    composite score (descending)."""
    from config.universe import UNIVERSE
    syms_ordered = [u.symbol for u in UNIVERSE]
    sym_sector = {u.symbol: u.sector for u in UNIVERSE}

    # Index briefing inputs
    preds_list = (briefing.get("predictions") or {}).get("predictions") or []
    preds_by_sym = {p.get("symbol"): p for p in preds_list if p.get("symbol")}

    rankings = (briefing.get("universe_ranking") or {}).get("ranking") or []
    rank_by_sym = {r.get("symbol"): r for r in rankings if isinstance(r, dict)}

    value_book = briefing.get("value_book") or {}
    quality_book = briefing.get("quality_book") or {}
    verdict_universe = briefing.get("verdict_universe") or {}
    volume_signals = briefing.get("volume_signals") or {}
    mf_holdings = briefing.get("mf_holdings") or {}

    sector_tilts = (macro_summary or {}).get("sector_tilts") or {}

    out: list[dict] = []
    for sym in syms_ordered:
        pred = preds_by_sym.get(sym)
        sector = sym_sector.get(sym, "")

        # Component scoring
        s_pred, why_pred  = _score_prediction(pred)
        s_val,  why_val   = _score_value(value_book.get(sym) if isinstance(value_book, dict) else None)
        s_qual, why_qual  = _score_quality(quality_book.get(sym) if isinstance(quality_book, dict) else None)
        s_mom,  why_mom   = _score_momentum(rank_by_sym.get(sym))
        s_mac,  why_mac   = _score_macro_tilt(sector, sector_tilts)
        s_flo,  why_flo   = _score_flows(sym, mf_holdings)
        s_vol,  why_vol   = _score_volume(sym, volume_signals)

        components = {
            "predict":  s_pred, "value":    s_val,
            "quality":  s_qual, "momentum": s_mom,
            "macro":    s_mac,  "flows":    s_flo,
            "volume":   s_vol,
        }
        score = _composite_score(components)
        action, conv = _score_to_action(score)

        # Build "why" string: pick top 2 positive + 1 negative contributor
        contrib_strs = []
        items = [(k, components[k], note) for k, note in
                 [("predict", why_pred), ("value", why_val),
                  ("quality", why_qual), ("momentum", why_mom),
                  ("macro", why_mac), ("flows", why_flo),
                  ("volume", why_vol)] if note]
        items.sort(key=lambda x: -abs(x[1]))
        for k, v, note in items[:3]:
            sign = "+" if v > 0 else ("-" if v < 0 else "")
            contrib_strs.append(f"{sign}{note}")
        why = "; ".join(contrib_strs) if contrib_strs else "no strong signal"

        # Pull entry / expected returns from predictions
        entry = (pred or {}).get("entry_price_pkr")
        exp5  = (pred or {}).get("expected_net_5d_pct")
        exp21 = (pred or {}).get("expected_net_21d_pct") or (pred or {}).get("expected_21d_pct")

        key_drivers = ((pred or {}).get("key_drivers")
                       or [k for k in [why_mac, why_mom, why_val] if k])
        key_risks   = ((pred or {}).get("key_risks") or [])

        # Tag bookmarks
        tags = []
        if (value_book.get(sym) or {}).get("signal") == "BUY_VALUE":
            tags.append("BUY_VALUE")
        if s_mac > 0.2:
            tags.append(f"MACRO_TAILWIND")
        elif s_mac < -0.2:
            tags.append(f"MACRO_HEADWIND")
        if s_flo > 0.3:
            tags.append("MF_ACCUMULATION")
        elif s_flo < -0.3:
            tags.append("MF_DISTRIBUTION")
        if s_vol > 0.3:
            tags.append("VOLUME_BREAKOUT")

        out.append({
            "symbol":            sym,
            "sector":            sector,
            "score":             round(score, 3),
            "action":            action,
            "conviction":        conv,
            "expected_5d_net_pct": exp5,
            "expected_21d_pct":  exp21,
            "entry_price":       entry,
            "components":        {k: round(v, 3) for k, v in components.items()},
            "why":               why,
            "key_drivers":       key_drivers[:3],
            "key_risks":         key_risks[:2],
            "tags":              tags,
        })

    out.sort(key=lambda r: r["score"], reverse=True)
    return out


def short_candidates(scored: list[dict], top_n: int = 5) -> list[dict]:
    """Return top-N negative-score names as short candidates."""
    bears = [r for r in scored if r["score"] < -0.10]
    bears.sort(key=lambda r: r["score"])
    return bears[:top_n]


def long_candidates(scored: list[dict], top_n: int = 10) -> list[dict]:
    """Return top-N positive-score names as long candidates."""
    bulls = [r for r in scored if r["score"] > 0.10]
    bulls.sort(key=lambda r: -r["score"])
    return bulls[:top_n]


if __name__ == "__main__":
    import json
    from pathlib import Path
    p = sorted(Path("data/_strategist").glob("_briefing_*.json"))[-1]
    briefing = json.loads(p.read_text(encoding="utf-8"))
    from brain.agents.macro_reader import read_macro
    macro = read_macro(briefing, use_llm=False)
    scored = score_universe(briefing, macro_summary=macro, use_llm=False)
    print(f"Scored {len(scored)} universe stocks (showing top 10 + bottom 5):\n")
    print(f"{'#':>3} {'symbol':<7} {'sector':<22} {'score':>6} "
          f"{'action':<6} {'conv':<7} {'why':<60}")
    print("-" * 115)
    for i, r in enumerate(scored[:10], 1):
        print(f"{i:>3} {r['symbol']:<7} {r['sector'][:20]:<22} "
              f"{r['score']:>+6.3f} {r['action']:<6} {r['conviction']:<7} "
              f"{r['why'][:60]}")
    print("  ...")
    for i, r in enumerate(scored[-5:], len(scored)-4):
        print(f"{i:>3} {r['symbol']:<7} {r['sector'][:20]:<22} "
              f"{r['score']:>+6.3f} {r['action']:<6} {r['conviction']:<7} "
              f"{r['why'][:60]}")

    shorts = short_candidates(scored, 5)
    longs  = long_candidates(scored, 10)
    print(f"\nLong candidates: {len(longs)}")
    print(f"Short candidates: {len(shorts)}")
