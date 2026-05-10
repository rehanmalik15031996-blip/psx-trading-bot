"""Master Strategist — top-layer Claude reasoning over EVERY signal.

What this module is
-------------------

The bot has dozens of independent signal layers:

    * Phase-1 mechanical strategy (monthly momentum rule)
    * Verdict synthesizer (7-lens reconciliation per stock)
    * Macro-impact engine (sector x macro rule book + leverage amplifier)
    * Predictions log (LLM 5-day forecasts + scorecard)
    * News sentiment, FIPI flows, fundamentals, earnings calendar,
      material information, director's reports, short candidates,
      overnight globals, scored sentiment, ...

Each one is an honest, narrow signal. Reading them all in one head
is the analyst's job. Until now the LLM only saw a sliver of this
context (the 5-day per-ticker prediction prompt or the chat tool
calls one at a time).

The Master Strategist promotes Claude to **the top layer**: it
gathers everything the bot knows, hands it to Claude Sonnet 4.5
running in **extended-thinking mode** (a real chain-of-thought
reasoning pass with a configurable token budget), and asks one
question:

    "Given everything you can see, what should the analyst do today?"

The output is a structured JSON ``MasterDecision`` with:

    * a one-line headline ("STAY DEFENSIVE — let Phase-1 cash hold")
    * a narrative paragraph explaining the call
    * conviction (LOW / MEDIUM / HIGH) and risk-stance bucket
    * a ranked list of concrete actions per bucket (BUY / ADD / HOLD /
      TRIM / AVOID / SHORT) with explicit reasons that name the
      contributing signals
    * the verbatim cross-check against the mechanical Phase-1 rule
      (does the strategist agree, override, or scale down?)
    * Claude's redacted thinking trace (for the audit log)
    * the briefing payload that produced the call (for reproducibility)

Design rules
------------

1. **Top of the stack, not the only stack.** The mechanical
   Phase-1 rule still drives the trade book. The strategist adds
   conviction, sizing nudges, and emergency overrides — it is NOT
   allowed to silently fabricate a buy on a name Phase-1 vetoed; it
   has to *explain* the override.
2. **Claude reasons; Python decides.** Every concrete action the
   strategist proposes must trace back to a tool result already in
   the briefing — no hallucinated tickers, no invented prices.
   A rule-based fallback runs when no API key is set.
3. **One call, one document.** The strategist returns a single
   JSON object suitable for caching to disk and rendering in the
   Today tab + the daily PDF brief. We do NOT call Claude
   repeatedly per stock — that's what the per-ticker predictions
   pipeline already does.
4. **Cost-aware default model.** ``claude-sonnet-4-5`` with a
   12k-token thinking budget is the default — flagship-tier
   reasoning at ~10x lower cost than Opus. Opus is available as a
   "deep dive" override.

Public API
~~~~~~~~~~

    decide_today(deep: bool = False) -> dict
        Build the briefing, call Claude, and return a serialised
        MasterDecision. Use ``deep=True`` to escalate from Sonnet
        to Opus for the heavyweight quarterly read.

    build_briefing() -> dict
        Return the full structured briefing payload (no LLM call).

    cache_path() -> Path
        Where today's decision is persisted on disk.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "_strategist"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------
@dataclass
class StrategistAction:
    """One concrete recommendation the strategist surfaces."""
    symbol: str | None
    bucket: str               # BUY / ADD / HOLD / TRIM / AVOID / SHORT / WATCH
    conviction: str           # LOW / MEDIUM / HIGH
    sector: str = ""
    target_weight_pct: float | None = None
    reason: str = ""
    contributing_signals: list[str] = field(default_factory=list)


@dataclass
class MasterDecision:
    """Single top-of-stack daily call from Claude."""
    as_of: str
    model: str
    thinking_budget: int
    headline: str
    risk_stance: str          # AGGRESSIVE / NORMAL / CAUTIOUS / DEFENSIVE / CASH
    conviction: str           # LOW / MEDIUM / HIGH
    narrative: str            # 3-6 sentence paragraph
    agrees_with_phase1: bool
    phase1_disagreement_note: str
    actions: list[StrategistAction]
    key_drivers: list[str]
    key_risks: list[str]
    macro_lens: str           # one-paragraph macro read
    behavioural_lens: str     # one-paragraph emotional/herding read
    fallback_used: bool       # True when LLM unavailable
    raw_llm_text: str         # verbatim final text (for the audit log)
    thinking_trace: str       # Claude's internal reasoning (may be empty)
    briefing_summary: dict    # the structured inputs the LLM saw

    def as_dict(self) -> dict:
        d = asdict(self)
        d["actions"] = [asdict(a) for a in self.actions]
        return d


# ---------------------------------------------------------------------------
# Briefing builder — gathers EVERYTHING into one structured document
# ---------------------------------------------------------------------------
def _safe(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def build_briefing() -> dict:
    """Pull every signal the bot has into one structured payload.

    This is intentionally heavier than ``ui.dashboard_data.morning_brief``
    — the strategist needs the complete dataset, not just the bits the
    Today tab renders. We pull bot's-verdict for the full universe,
    short candidates, fair-value + quality books, the macro-impact
    engine output, the latest predictions, the FIPI flows, the
    sentiment block, the earnings calendar, and the recent material
    information.

    After the raw signals are gathered, we run ``brain.playbook`` to
    retrieve the top-K most-relevant historical "situation -> reaction"
    analogues from the curated case library. Claude is told (via the
    system prompt) to either name the analogue it is leaning on or
    explain why none fits — that's how the bot uses its institutional
    memory.
    """
    # Imports are deferred so importing this module never accidentally
    # warms a heavy data path before we need it.
    from ui import tools
    from ui.dashboard_data import (
        load_prediction_log_stats, universe_movers, universe_index_history,
        material_information_recent, latest_management_outlook,
    )
    from brain.macro_impact import compute_macro_impact
    from brain.verdict_synthesizer import synthesize_universe
    from brain.short_candidates import rank_shorts
    from brain import playbook as pb
    from brain import mf_flows
    try:
        from ui.trade_journal import journal_stats
    except Exception:
        journal_stats = lambda: {"error": "journal stats unavailable"}

    briefing = {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of_local": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "regime": _safe(tools.get_market_regime),
        "strategy_signal": _safe(tools.get_strategy_signal),
        "universe_ranking": _safe(tools.get_universe_ranking),
        "macro_snapshot": _safe(tools.get_macro_snapshot),
        "policy_rate": _safe(tools.get_policy_rate),
        "macro_impact": _safe(compute_macro_impact),
        "fipi_flows": _safe(tools.get_fipi_flows),
        "overnight": _safe(tools.get_overnight_signals),
        "scored_sentiment": _safe(tools.get_scored_sentiment),
        "industry_kpis": _safe(tools.get_industry_kpis),
        # Per-stock fundamentals + valuation + quality
        "verdict_universe": _safe(synthesize_universe),
        "value_book": _safe(tools.get_universe_value_book),
        "quality_book": _safe(tools.get_universe_quality_book),
        "earnings_momentum": _safe(tools.get_universe_earnings_momentum),
        "earnings_calendar": _safe(tools.get_earnings_calendar, days_ahead=21),
        "material_information": _safe(material_information_recent, days=14),
        "management_outlook": _safe(latest_management_outlook),
        # Today's per-ticker predictions + cumulative scorecard
        "predictions": _safe(tools.get_todays_predictions, max_items=35),
        "prediction_accuracy": _safe(load_prediction_log_stats),
        # Long-side ideas + short-side composite
        "top_buys": _safe(tools.recommend_new_buys, max_ideas=5),
        "short_candidates": _safe(rank_shorts,
                                    min_conviction="LOW", max_results=10),
        # User context
        "portfolio": _safe(tools.get_user_portfolio),
        "watchlist": _safe(tools.get_watchlist),
        "journal_stats": _safe(journal_stats),
        # Light context
        "universe_movers": _safe(universe_movers),
        "universe_index": _safe(universe_index_history, days=60),
    }

    # Institutional flows: aggregate AHL Mutual Funds Equity Holdings
    # data into per-stock and universe-level signals BEFORE we run the
    # playbook matcher (so MF triggers can fire). The lens degrades
    # gracefully when the parquet is missing or stale.
    try:
        mf_payload = mf_flows.universe_summary()
        # Per-stock signals for the universe (Phase-1 selected names)
        per_stock_signals: dict[str, dict] = {}
        sig = briefing.get("strategy_signal") or {}
        sym_list = list((sig.get("selected_symbols") or sig.get("ranked_top") or [])
                          + (sig.get("would_pick_if_market_filter_off") or sig.get("selected") or []))
        seen: set[str] = set()
        for sym in sym_list:
            if not isinstance(sym, str) or sym in seen:
                continue
            seen.add(sym)
            per_stock_signals[sym] = mf_flows.signals_for(sym)
        # Also enrich top accumulated / distributed names so the matcher
        # can fire on them even when they're outside Phase-1 selection.
        for entry in (mf_payload.get("top_accumulated_180d") or []) + \
                      (mf_payload.get("top_distributed_180d") or []):
            sym = entry.get("symbol") if isinstance(entry, dict) else None
            if sym and sym not in seen:
                seen.add(sym)
                per_stock_signals[sym] = mf_flows.signals_for(sym)
        mf_payload["per_stock_signals"] = per_stock_signals
        briefing["mf_holdings"] = mf_payload
    except Exception as e:
        briefing["mf_holdings"] = {"error": f"{type(e).__name__}: {e}"}

    # Phase E: volume confirmation signals (validated 2026-05-02).
    # Universe-level rollup of "confirmed breakout days" in the last
    # 3 trading sessions across the universe symbols.
    try:
        from brain import volume_signals
        sig = briefing.get("strategy_signal") or {}
        vol_syms = list(dict.fromkeys(
            (sig.get("selected_symbols") or sig.get("ranked_top") or [])
            + (sig.get("would_pick_if_market_filter_off") or sig.get("selected") or [])
        ))
        if not vol_syms:
            from config.universe import symbols as universe_symbols
            vol_syms = list(universe_symbols())
        briefing["volume_signals"] = volume_signals.universe_summary(vol_syms)
    except Exception as e:
        briefing["volume_signals"] = {"error": f"{type(e).__name__}: {e}"}

    # Institutional memory: retrieve historical analogues from the
    # playbook BEFORE the LLM call so Claude reasons against named
    # past evidence, not from scratch.
    try:
        briefing["playbook_analogues"] = pb.retrieve_analogues(briefing)
        briefing["playbook_facts"] = pb.summarise_facts(briefing)
    except Exception as e:
        briefing["playbook_analogues"] = []
        briefing["playbook_facts"] = {"error": f"{type(e).__name__}: {e}"}

    # New 2026-05-03 streams — small additions the strategist now has:
    #   * MUFAP industry equity AUMs (24m of upstream MF allocation data)
    #   * PSX universe daily turnover (sentiment z-score)
    #   * Remittances + LSM monthly trends (curated JSON)
    #   * MSCI calendar (next + recent events)
    try:
        briefing["mufap_industry"] = _safe(_load_mufap_industry_summary)
        briefing["psx_turnover"] = _safe(_load_psx_turnover_signal)
        briefing["remittances"] = _safe(_load_remittances_signal)
        briefing["lsm_index"] = _safe(_load_lsm_signal)
        briefing["msci_calendar"] = _safe(_load_msci_calendar_signal)
    except Exception as e:
        briefing["new_streams_error"] = f"{type(e).__name__}: {e}"

    # Compress the heaviest fields (verdict_universe, predictions,
    # value_book, quality_book, earnings_momentum, management_outlook)
    # so per-stock detail only remains for the top-K actionable names.
    # This reduces the LLM input by ~50% (from ~81k tokens to ~35-40k)
    # which mitigates the "lost in the middle" effect at long context.
    try:
        briefing = _compress_heavy_fields(briefing)
    except Exception as e:
        briefing["_compression_error"] = f"{type(e).__name__}: {e}"

    return briefing


# ---------------------------------------------------------------------------
# New data-stream loaders (2026-05-03)
# ---------------------------------------------------------------------------
def _load_mufap_industry_summary() -> dict:
    """Read the MUFAP industry equity-AUMs summary parquet and surface
    the latest 6 months + MoM changes. This is the *upstream* source AHL
    summarises in its monthly Mutual Funds Equity Holdings PDFs."""
    import pandas as pd
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "flows" / "mufap_industry_summary.parquet"
    if not p.exists():
        return {"error": "mufap_industry_summary.parquet missing"}
    df = pd.read_parquet(p).sort_values("as_of_month")
    if df.empty:
        return {"error": "mufap parquet empty"}
    last = df.tail(6).to_dict(orient="records")
    most_recent = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    return {
        "latest_month": str(most_recent["as_of_month"]),
        "total_industry_aum_pkr_mn": float(most_recent["total_industry_aum_pkr_mn"]),
        "equity_aum_pct": float(most_recent["equity_aum_pct"]),
        "pure_equity_aum_pct": float(most_recent["pure_equity_aum_pct"]),
        "mom_equity_aum_pct_change": (
            float(most_recent["equity_aum_pct"] - prev["equity_aum_pct"])
            if prev is not None else None
        ),
        "n_funds": int(most_recent["n_funds"]),
        "trailing_6m": [
            {
                "month": str(r["as_of_month"]),
                "equity_aum_pct": round(float(r["equity_aum_pct"]), 2),
                "pure_equity_aum_pct": round(float(r["pure_equity_aum_pct"]), 2),
                "total_industry_aum_pkr_mn": int(r["total_industry_aum_pkr_mn"]),
            } for r in last
        ],
        "interpretation": (
            "Equity AUMs % rising MoM ⇒ MFs net buying equities (bullish flow); "
            "falling MoM ⇒ MFs rotating to fixed income (cautious flow)."
        ),
    }


def _load_psx_turnover_signal() -> dict:
    """Read the universe-turnover parquet and surface the latest level
    plus the 60d z-score (the spike/dry-up indicator)."""
    import pandas as pd
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "macro" / "psx_universe_turnover.parquet"
    if not p.exists():
        return {"error": "psx_universe_turnover.parquet missing"}
    df = pd.read_parquet(p).sort_values("date")
    if df.empty:
        return {"error": "turnover parquet empty"}
    last = df.iloc[-1]
    last5 = df.tail(5)
    last20 = df.tail(20)
    return {
        "as_of": str(last["date"].date() if hasattr(last["date"], "date") else last["date"]),
        "universe_turnover_pkr": float(last["universe_turnover_pkr"]),
        "turnover_zscore_60d": float(last.get("turnover_zscore_60d", float("nan")) or 0.0),
        "turnover_ratio_20d": float(last.get("turnover_ratio_20d", float("nan")) or 0.0),
        "trailing_5d_avg_pkr": float(last5["universe_turnover_pkr"].mean()),
        "trailing_20d_avg_pkr": float(last20["universe_turnover_pkr"].mean()),
        "interpretation": (
            "z-score > +1.5 ⇒ FOMO / capitulation buying spike; "
            "z-score < -1.5 ⇒ risk-off / dry-up; |z| < 1 ⇒ normal."
        ),
    }


def _load_remittances_signal() -> dict:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "macro" / "remittances_monthly.json"
    if not p.exists():
        return {"error": "remittances_monthly.json missing"}
    data = json.loads(p.read_text(encoding="utf-8"))
    monthly = data.get("monthly_usd_mn", {})
    yoy = data.get("yoy_growth_pct", {})
    months = sorted(monthly.keys())
    if not months:
        return {"error": "no remittance data"}
    last6 = months[-6:]
    return {
        "as_of": months[-1],
        "latest_usd_mn": monthly[months[-1]],
        "latest_yoy_pct": yoy.get(months[-1]),
        "trailing_6m": [{"month": m,
                          "usd_mn": monthly[m],
                          "yoy_pct": yoy.get(m)} for m in last6],
        "interpretation": (
            "Strong remittance inflows (>USD 3bn/mo) support PKR stability and "
            "boost domestic consumption (positive for cement/auto/banks). "
            "YoY decline ⇒ external-account risk."
        ),
    }


def _load_lsm_signal() -> dict:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "macro" / "lsm_index_monthly.json"
    if not p.exists():
        return {"error": "lsm_index_monthly.json missing"}
    data = json.loads(p.read_text(encoding="utf-8"))
    qim = data.get("monthly_qim_index", {})
    yoy = data.get("yoy_change_pct", {})
    months = sorted(qim.keys())
    if not months:
        return {"error": "no LSM data"}
    last6 = months[-6:]
    return {
        "as_of": months[-1],
        "latest_qim": qim[months[-1]],
        "latest_yoy_pct": yoy.get(months[-1]),
        "trailing_6m": [{"month": m,
                          "qim_index": qim[m],
                          "yoy_pct": yoy.get(m)} for m in last6],
        "interpretation": (
            "QIM YoY > 0 ⇒ industrial expansion (positive for cement/steel/banks). "
            "QIM YoY < 0 ⇒ contraction (defensive bias toward staples / fertiliser)."
        ),
    }


def _load_msci_calendar_signal() -> dict:
    import json
    from datetime import date
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "macro" / "msci_calendar.json"
    if not p.exists():
        return {"error": "msci_calendar.json missing"}
    data = json.loads(p.read_text(encoding="utf-8"))
    events = data.get("events", [])
    today = date.today().isoformat()
    past = [e for e in events if e.get("implementation_date", "") <= today]
    upcoming = [e for e in events if e.get("implementation_date", "") > today]
    return {
        "last_event": past[-1] if past else None,
        "next_event": upcoming[0] if upcoming else None,
        "n_past_events": len(past),
        "n_upcoming_events": len(upcoming),
        "interpretation": (
            "Adds to FM main index ⇒ passive-fund inflow on implementation day "
            "(can be PKR 5-25bn for major adds). Deletions ⇒ forced selling. "
            "Effect typically peaks the day before implementation."
        ),
    }


# ---------------------------------------------------------------------------
# Briefing compression (2026-05-03)
# ---------------------------------------------------------------------------
def _compress_heavy_fields(briefing: dict) -> dict:
    """Reduce the 6 heaviest fields (verdict_universe, predictions,
    value_book, quality_book, earnings_momentum, management_outlook)
    by keeping full per-stock detail ONLY for the top-K actionable
    names. The rest collapse into a one-line summary so the LLM still
    sees them but doesn't burn 50% of its context window on them.

    Top-K selection rules (union, deduped):
      * strategy_signal.selected_symbols (Phase-1 picks with market filter)
      * strategy_signal.would_pick_if_market_filter_off (picks ignoring market filter)
      * portfolio.holdings (positions we already own)
      * watchlist
      * mf_holdings.top_accumulated_180d / top_distributed_180d
      * playbook_analogues triggered symbols

    This typically yields 10-15 names from a 35-name universe and
    reduces briefing size by ~50%.
    """
    top_k_syms = _select_top_k_symbols(briefing)

    # Compress each heavy field
    if "verdict_universe" in briefing and isinstance(briefing["verdict_universe"], dict):
        briefing["verdict_universe"] = _compress_per_stock_dict(
            briefing["verdict_universe"], top_k_syms,
            keep_keys_summary=("action", "conviction", "score"),
            field_label="verdict_universe",
        )

    if "predictions" in briefing and isinstance(briefing["predictions"], dict):
        preds = briefing["predictions"].get("predictions") or []
        if isinstance(preds, list):
            kept, summarised = [], []
            for p in preds:
                sym = (p or {}).get("symbol")
                if sym in top_k_syms:
                    kept.append(p)
                else:
                    if isinstance(p, dict):
                        summarised.append({
                            "symbol": sym,
                            "mid_pct": p.get("mid_pct") or p.get("mid"),
                            "bias": p.get("bias") or p.get("direction"),
                        })
            briefing["predictions"]["predictions"] = kept
            briefing["predictions"]["_summarised_others"] = summarised
            briefing["predictions"]["_compression_note"] = (
                f"{len(kept)} full predictions + {len(summarised)} "
                f"summarised (mid + bias only) to save context tokens"
            )

    for fld, summary_keys in (
        ("value_book", ("pe_ttm", "pb", "div_yield_pct", "rating")),
        ("quality_book", ("roe_ttm_pct", "debt_equity", "interest_coverage", "rating")),
        ("earnings_momentum", ("eps_growth_yoy_pct", "rating")),
        ("management_outlook", ("sentiment", "summary_short")),
    ):
        if fld in briefing and isinstance(briefing[fld], dict):
            briefing[fld] = _compress_per_stock_dict(
                briefing[fld], top_k_syms,
                keep_keys_summary=summary_keys,
                field_label=fld,
            )

    # Also compress short_candidates: keep only top-5 with full detail
    if "short_candidates" in briefing and isinstance(briefing["short_candidates"], dict):
        cands = briefing["short_candidates"].get("candidates") or []
        if isinstance(cands, list) and len(cands) > 5:
            briefing["short_candidates"]["candidates"] = cands[:5]
            briefing["short_candidates"]["_compression_note"] = (
                f"showing top 5 of {len(cands)} short candidates"
            )

    briefing["_compression_summary"] = {
        "top_k_full_detail_symbols": sorted(top_k_syms),
        "n_top_k": len(top_k_syms),
        "fields_compressed": [
            "verdict_universe", "predictions", "value_book",
            "quality_book", "earnings_momentum",
            "management_outlook", "short_candidates",
        ],
        "note": (
            "Per-stock full detail kept ONLY for top-K actionable names "
            "above; other names appear as one-line summaries in '_others'. "
            "This compresses the briefing by ~50% to keep critical macro / "
            "playbook context within the LLM's effective attention window."
        ),
    }
    return briefing


def _select_top_k_symbols(briefing: dict) -> set[str]:
    """Build the set of symbols that get full per-stock detail in the
    compressed briefing."""
    syms: set[str] = set()

    sig = briefing.get("strategy_signal") or {}
    if isinstance(sig, dict):
        for k in ("selected_symbols", "would_pick_if_market_filter_off",
                   "selected", "ranked_top"):
            for s in (sig.get(k) or []):
                if isinstance(s, str):
                    syms.add(s)

    port = briefing.get("portfolio") or {}
    if isinstance(port, dict):
        for h in (port.get("holdings") or []):
            if isinstance(h, dict) and isinstance(h.get("symbol"), str):
                syms.add(h["symbol"])

    wl = briefing.get("watchlist") or {}
    if isinstance(wl, dict):
        for s in (wl.get("symbols") or []):
            if isinstance(s, str):
                syms.add(s)
        for s in (wl.get("watchlist") or []):
            if isinstance(s, str):
                syms.add(s)

    mfh = briefing.get("mf_holdings") or {}
    if isinstance(mfh, dict):
        for k in ("top_accumulated_180d", "top_distributed_180d"):
            for entry in (mfh.get(k) or [])[:5]:
                if isinstance(entry, dict) and isinstance(entry.get("symbol"), str):
                    syms.add(entry["symbol"])

    pban = briefing.get("playbook_analogues") or []
    if isinstance(pban, list):
        for an in pban[:5]:
            if isinstance(an, dict):
                for s in (an.get("symbols") or []):
                    if isinstance(s, str):
                        syms.add(s)

    return syms


def _compress_per_stock_dict(payload: dict, top_k: set[str],
                              keep_keys_summary: tuple,
                              field_label: str) -> dict:
    """Generic compressor: a payload dict that maps symbol -> {fields}
    becomes {top_k_sym: full, _others: [one-line summary, ...]}."""
    if not isinstance(payload, dict):
        return payload

    by_symbol: dict | None = None
    other_root_keys = {}

    # Two patterns we see in the wild:
    #   1. payload[symbol] = {fields...}                       -- direct
    #   2. payload['rows'] / payload['verdicts'] = [{symbol, ...}]  -- nested
    if any(isinstance(v, dict) and "symbol" not in v for v in payload.values()) \
       and all(k.isupper() and len(k) <= 8 for k in payload.keys()
                if isinstance(k, str)):
        by_symbol = payload
    else:
        # Look for a known list field
        for list_key in ("verdicts", "rows", "items", "books", "ratings"):
            v = payload.get(list_key)
            if isinstance(v, list) and v and isinstance(v[0], dict) and "symbol" in v[0]:
                by_symbol = {row["symbol"]: row for row in v
                              if isinstance(row.get("symbol"), str)}
                other_root_keys = {k: v for k, v in payload.items() if k != list_key}
                break

    if by_symbol is None:
        return payload

    kept: dict[str, dict] = {}
    summarised: list[dict] = []
    for sym, full in by_symbol.items():
        if sym in top_k:
            kept[sym] = full
        else:
            tiny = {"symbol": sym}
            if isinstance(full, dict):
                for sk in keep_keys_summary:
                    if sk in full:
                        tiny[sk] = full[sk]
            summarised.append(tiny)

    out = {**other_root_keys}
    out.update(kept)
    out["_others"] = summarised
    out["_compression_note"] = (
        f"{len(kept)} symbols full; {len(summarised)} summarised "
        f"({field_label})"
    )
    return out


def _briefing_summary(briefing: dict) -> dict:
    """Compact, JSON-friendly summary of the briefing for the cache.

    The full briefing is enormous (per-stock fundamentals on 35 names);
    we only persist the "interesting" rollups so the cached decision
    file stays well under 1 MB.
    """
    def _pluck(d: dict | None, *keys):
        out = {}
        if not isinstance(d, dict):
            return {"error": "missing"}
        for k in keys:
            v = d.get(k)
            if v is not None:
                out[k] = v
        return out

    analogues = briefing.get("playbook_analogues") or []
    return {
        "regime": _pluck(briefing.get("regime"),
                          "regime", "exposure_multiplier",
                          "universe_ret_5d", "universe_ret_21d", "breadth_pct_up"),
        "strategy_signal": _pluck(briefing.get("strategy_signal"),
                                    "as_of", "market_risk_on",
                                    "selected_symbols", "would_pick_if_market_filter_off",
                                    "recommended_action", "rationale",
                                    # legacy key aliases kept for backwards compat
                                    "selected", "ranked_top"),
        "macro_drivers_count": len((briefing.get("macro_impact") or {})
                                    .get("drivers", []) or []),
        "fipi_5d_net": (briefing.get("fipi_flows") or {}).get("net_5d_pkr_mn"),
        "n_short_candidates": len((briefing.get("short_candidates") or {})
                                    .get("candidates", []) or []),
        "n_buy_ideas": len((briefing.get("top_buys") or {}).get("ideas", []) or []),
        "n_predictions": len((briefing.get("predictions") or {})
                              .get("predictions", []) or []),
        "portfolio_positions": len((briefing.get("portfolio") or {})
                                    .get("positions", []) or []),
        "playbook_analogues_count": len(analogues),
        "playbook_analogue_ids":   [a.get("id") for a in analogues],
        # Compact per-id metadata so the UI can render exactly *why*
        # each case fired without re-running the matcher. Capped at the
        # top 8 fired triggers per case to keep the cached payload small.
        "playbook_analogue_fired": {
            a.get("id"): {
                "fired_triggers": list(a.get("fired_triggers") or [])[:8],
                "match_score":    a.get("match_score"),
                "confidence":     a.get("confidence"),
            }
            for a in analogues if a.get("id")
        },
        "mf_data_freshness_days":  ((briefing.get("mf_holdings") or {})
                                       .get("data_freshness_days")),
        "mf_n_top_accumulated":    len(((briefing.get("mf_holdings") or {})
                                         .get("top_accumulated_180d") or [])),
        "mf_n_top_distributed":    len(((briefing.get("mf_holdings") or {})
                                         .get("top_distributed_180d") or [])),
    }


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
STRATEGIST_SYSTEM = """You are the **Master Strategist** for a rules-first
PSX trading system. You are NOT a generic stock-picker — you sit on top of a
mechanical Phase-1 momentum strategy and a deterministic 7-lens analyst stack
(Value / Quality / Momentum / Macro / News / Flow / Management).

Your job: read EVERY signal in the briefing below, reason carefully across
them (use your extended-thinking budget liberally — this is exactly the kind
of multi-source reasoning task it exists for), and then publish a single
top-of-stack call for today.

Anchor truths you must respect:

1. **Phase-1 is the trade book.** If the mechanical rule says CASH, the
   default action is CASH. You may downgrade individual picks when the
   evidence is overwhelming, but you CANNOT silently fabricate buys on
   names Phase-1 didn't pick. If you disagree with Phase-1, set
   ``agrees_with_phase1=false`` and write a one-sentence note in
   ``phase1_disagreement_note`` explaining exactly which signal forced
   the override.
2. **Cite your sources.** Every action's ``contributing_signals`` array
   must list at least 2 signals from the briefing (e.g. "macro_impact:
   Power +3 STRONG TAILWIND on circular_debt_resolution",
   "verdict_universe: HUBC action=BUY conviction=HIGH",
   "predictions: HUBC mid +2.4%"). No vague hand-waves.
3. **Behavioural lens.** Pakistan's stock market is well-documented as
   weak-form-inefficient and emotion-driven. Read the FIPI flows, the
   regime drawdown / breadth, and the news sentiment together — when
   they line up bearish, herding can amplify a 5% drop into a 10% one
   inside a week. Your ``behavioural_lens`` paragraph names this risk
   explicitly when present, OR confirms it is absent today.
4. **Macro is the master.** Interest-rate cycle, IMF posture,
   USD/PKR, Brent/coal cycles, and the circular-debt complex drive the
   broad regime. Your ``macro_lens`` paragraph reconciles the macro
   reading from ``macro_impact`` against ``policy_rate``,
   ``industry_kpis``, and ``overnight``.

4b. **Institutional flows lens.** The briefing carries
   ``mf_holdings`` — Pakistani mutual-fund "smart money" positioning
   from Arif Habib Limited's monthly Equity Holdings reports.
   Read ``top_accumulated_180d`` and ``top_distributed_180d``
   together with ``per_stock_signals`` (per-stock 30-day MoM and
   3+/6+ month accumulation streaks). Names with 3+ months of
   accumulation OR 3+ funds initiating new positions in the latest
   report are higher-conviction adds even when momentum looks tired;
   conversely, 3+ months of distribution caps any rally even when
   technicals look constructive. State the freshness of the MF data
   (``data_freshness_days``) — anything older than 45 days is stale
   and should be down-weighted. If ``mf_holdings.error`` is set,
   note that the lens is unavailable and proceed without it.
5. **Cost discipline.** A round-trip on PSX is ~0.56% all-in plus
   15% CGT on gains. Any BUY/ADD action must be defensible at >=1.6%
   gross expected 5-day return; otherwise downgrade to HOLD or WATCH.
6. **Pre-event guards.** If a name has earnings within 5 days
   (``earnings_calendar.in_blackout_5d=true``), do NOT issue a fresh
   BUY/ADD on it. Existing positions can stay.
7. **Concentration is a TAIL-RISK CAP, not a return prediction.** PSX
   sectors do NOT mean-revert (validated 2026-05-02 against 5y of
   history: hot sectors keep modestly trending). The reason we cap
   concentration is **single-event blow-up risk** — one SBP shock,
   one circular-debt worsening, one IMF stall, can take an entire
   sector down 8-15% in a week. So if your action list ends up with
   more than 2 BUY/ADD names in the same sector, downgrade the
   lowest-conviction one to HOLD with a "concentration: <sector> —
   single-event blow-up cap" note. This is risk management, not a view
   on the sector's forward returns.
8. **Use the playbook.** The briefing has a ``playbook_analogues``
   array — a curated list of historical "situation -> reaction"
   cases that the deterministic matcher decided are analogous to
   today. Each entry has a ``playbook`` paragraph (what to do given
   the analogue) and ``historical_instances`` with realised d1/d5/
   d21 sector reactions. You MUST do one of the following:
     (a) Lean on at least one analogue and cite it by ``id`` in the
         relevant action's ``contributing_signals`` (e.g.
         ``"playbook: circular_debt_resolution_large d21=+11.8% on HUBC"``);
     (b) OR explicitly state in ``narrative`` why none of the
         retrieved analogues fits today (e.g. "today's setup looks
         like circular_debt_resolution_large but the size is half
         the prior instance, so I'm halving the expected move").
   If ``playbook_analogues`` is empty, that's fine — the matcher
   simply found no analogue for today's situation. Note that fact
   in ``narrative`` and reason from first principles.
9. **Honesty about the analogue.** If a case's
   ``what_breaks_it`` condition is also active in today's briefing,
   say so explicitly and downgrade the analogue's weight in your
   reasoning.
10. **Honesty about data freshness.** If a critical input is stale,
   acknowledge it and lower conviction accordingly:
     - **Mutual-fund holdings** (``mf_holdings.data_freshness_days``):
       fresh (<=30d) -> full weight; ageing (30-60d) -> mention the
       age and treat MF triggers as confirming, not initiating;
       stale (>60d) -> the playbook silently vetoes MF triggers, so
       do NOT cite MF flows as a reason for any action.
     - **SBP / KIBOR / T-bill** (``policy_rate.fallback_used``): if
       the offline parquet fallback was used, note it
       ("policy rate from offline fallback as of <as_of>") and
       avoid claiming knowledge of decisions made after that date.
     - **News sentiment**: scored news older than 24h on a market day
       is stale; do not anchor a directional call on >24h-old
       sentiment.
   Do not pretend a stale signal is fresh; the user trusts the
   strategist precisely because it is honest about what it does and
   does not know today.
11. **PSX-specific behaviours validated against history (2026-05-02).**
   These are NOT generic equity rules — they were tested directly on
   PSX OHLCV and the generic ones were dropped:
     - **Knives BOUNCE on PSX**, they don't keep falling. Names down
       21d>=10% returned +3.0% over the next 21d on average vs +1.8%
       baseline; deep knives (<-20%) bounced +5.6%. Do NOT add a
       "drawdown veto" to your BUY criteria — wait for one stable
       session, but do not refuse the trade for the drawdown alone.
     - **Volume confirms direction on PSX** (despite being retail
       heavy): +1.5% days on >=1.5x median volume returned +0.8% over
       next 5d vs +0.2% on low-volume up days. If you see a per-stock
       breakout signal in the briefing, lean on it.
     - **Banking NIM is policy-rate-regime driven, not spread driven.**
       Top-quartile policy rate (>=18%) = bank tailwind (+13.6pp 90d
       edge); bottom-quartile (<=9%) = bank headwind. Use the
       ``banking_nim_regime_high/low`` playbook analogues when they
       fire.

12. **Asymmetric calibration (anti-permabull guard, 2026-05-03).** The
    end-to-end test on the last year of decisions showed BULLISH calls
    hit 33% of the time, while BEARISH (75%) and NEUTRAL (83%) were
    well-calibrated. To correct the upward bias:
      - A BUY / overweight call requires AT LEAST TWO independent
        positive lenses (e.g. flow + macro, or playbook analogue +
        management outlook). A single positive lens defaults to
        NEUTRAL or LOW conviction.
      - When the briefing's MF and macro signals disagree, default to
        NEUTRAL — do NOT pick the bullish side just because the
        verdict_universe lens leans long (verdict_universe inherits
        Phase-1's momentum bias and is not an independent lens).
      - Conviction HIGH on a BUY requires either (i) a 100%-hit-rate
        playbook case (mf_initiation_cluster, post_cut_cycle_continuation)
        firing today, or (ii) BOTH macro_impact tailwind AND a flow
        analogue. Otherwise cap at MEDIUM.
      - Bearish calls do NOT need this extra gate — they have already
        been shown to be well-calibrated.

Output format
-------------

Return ONLY one JSON object, no prose, no markdown fences. The schema:

    {
      "headline": "<one-line top-of-stack call (<=120 chars)>",
      "risk_stance": "AGGRESSIVE" | "NORMAL" | "CAUTIOUS" | "DEFENSIVE" | "CASH",
      "conviction": "LOW" | "MEDIUM" | "HIGH",
      "narrative": "<3-6 sentence plain-English paragraph an analyst can read aloud>",
      "agrees_with_phase1": true | false,
      "phase1_disagreement_note": "<empty string if agrees, else one sentence>",
      "macro_lens":       "<one paragraph reconciling macro signals>",
      "behavioural_lens": "<one paragraph reading PSX emotion / flows / herding>",
      "key_drivers": ["<bullet citing a signal>", ...],
      "key_risks":   ["<bullet citing a signal>", ...],
      "actions": [
        {
          "symbol": "HUBC" | null,
          "bucket": "BUY" | "ADD" | "HOLD" | "TRIM" | "AVOID" | "SHORT" | "WATCH",
          "conviction": "LOW" | "MEDIUM" | "HIGH",
          "sector": "Power",
          "target_weight_pct": 20.0 | null,
          "reason": "<one sentence>",
          "contributing_signals": ["macro_impact: ...", "verdict_universe: ..."]
        },
        ...
      ]
    }
"""


# ---------------------------------------------------------------------------
# Rule-based fallback (no API key)
# ---------------------------------------------------------------------------
def _fallback_decision(briefing: dict, model: str) -> MasterDecision:
    """Deterministic best-effort call when no Claude key is present.

    Reads the mechanical Phase-1 signal, the verdict synthesizer's
    BUY-bucket names, and the LLM-free regime classifier. We do not try
    to reproduce the strategist's reasoning here — the goal is just to
    keep the surface usable when the API is unavailable.
    """
    sig = briefing.get("strategy_signal") or {}
    selected = sig.get("selected_symbols") or sig.get("selected") or []
    market_on = bool(sig.get("market_risk_on"))
    regime = briefing.get("regime") or {}
    regime_name = (regime.get("regime") or "NORMAL").upper()
    multiplier = float(regime.get("exposure_multiplier") or 1.0)

    actions: list[StrategistAction] = []
    if not market_on:
        risk_stance = "CASH"
        headline = "Phase-1 in CASH — universe momentum negative"
    else:
        if regime_name == "CRISIS":
            risk_stance = "DEFENSIVE"
        elif regime_name == "CAUTION":
            risk_stance = "CAUTIOUS"
        else:
            risk_stance = "NORMAL"
        headline = (f"Phase-1 holds {len(selected)} names "
                    f"(regime={regime_name}, exposure x{multiplier:.2f})")
        per_w = (multiplier / max(len(selected), 1)) * 100.0
        for sym in selected:
            actions.append(StrategistAction(
                symbol=sym, bucket="HOLD", conviction="MEDIUM",
                target_weight_pct=round(per_w, 1),
                reason=f"Phase-1 selected (mechanical), regime={regime_name}",
                contributing_signals=[
                    f"strategy_signal: selected list = {selected}",
                    f"regime: {regime_name} (x{multiplier:.2f} exposure)",
                ],
            ))

    return MasterDecision(
        as_of=briefing.get("as_of") or datetime.now(timezone.utc).isoformat(),
        model=f"{model}+fallback",
        thinking_budget=0,
        headline=headline,
        risk_stance=risk_stance,
        conviction="LOW",
        narrative=("LLM strategist unavailable (no ANTHROPIC_API_KEY). "
                   "This is the rule-based fallback — it mirrors the "
                   "Phase-1 mechanical signal scaled by the regime "
                   "exposure multiplier and surfaces no opinion of its own."),
        agrees_with_phase1=True,
        phase1_disagreement_note="",
        actions=actions,
        key_drivers=[
            f"Phase-1 picks: {', '.join(selected) if selected else 'CASH'}",
            f"Regime: {regime_name} (multiplier x{multiplier:.2f})",
        ],
        key_risks=[
            "LLM strategist unavailable — qualitative risks not surfaced.",
        ],
        macro_lens=("Macro lens not available without LLM reasoning. See "
                    "data/_health/macro_kpis.json for the raw KPIs."),
        behavioural_lens=("Behavioural lens not available without LLM "
                          "reasoning. FIPI net 5d = "
                          f"{(briefing.get('fipi_flows') or {}).get('net_5d_pkr_mn')}."),
        fallback_used=True,
        raw_llm_text="",
        thinking_trace="",
        briefing_summary=_briefing_summary(briefing),
    )


# ---------------------------------------------------------------------------
# JSON parser (tolerant of fences and prose preamble)
# ---------------------------------------------------------------------------
def _parse_json(raw: str) -> dict | None:
    """Extract the first valid JSON object from the LLM response.

    Handles three common patterns:
    1. Clean JSON: ``{...}``
    2. Fenced JSON: ``` ```json\\n{...}\\n``` ```
    3. Truncated JSON (max_tokens hit mid-response): tries each ``}``
       from right-to-left until a valid parse succeeds, so a long
       narrative field cut off mid-string doesn't discard the whole
       decision.
    """
    if not raw:
        return None
    s = raw.strip()
    # Strip code fences
    if s.startswith("```"):
        # str.strip("`") removes ALL backtick chars from both ends
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    i = s.find("{")
    if i < 0:
        return None
    # Fast path: try the outermost braces first (complete response)
    j = s.rfind("}")
    if j > i:
        try:
            return json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            pass
    # Slow path: response may have been truncated by max_tokens.
    # Walk ``}`` positions from right to left and try each prefix so
    # a cut-off narrative string doesn't prevent us from recovering the
    # structural fields (headline, risk_stance, actions, etc.).
    for j in sorted(
        [k for k in range(len(s)) if s[k] == "}"], reverse=True
    ):
        if j <= i:
            break
        try:
            return json.loads(s[i:j + 1])
        except json.JSONDecodeError:
            continue
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def decide_today(
    deep: bool = False,
    thinking_budget: int | None = None,
    max_tokens: int = 8_000,
    write_cache: bool = True,
) -> dict:
    """Build the briefing, call Claude (extended-thinking on), parse,
    and return a serialised :class:`MasterDecision`.

    Parameters
    ----------
    deep : when True, escalate from Sonnet 4.5 to Opus 4.5 and double
        the thinking budget. Used for the (optional) end-of-quarter
        deep-dive run.
    thinking_budget : override the default 12k token reasoning budget.
    max_tokens : upper bound on Claude's response (per turn). Anthropic
        requires ``thinking_budget < max_tokens``; we automatically
        bump it if needed.
    write_cache : when True, persist the decision to
        ``data/_strategist/YYYY-MM-DD.json`` so the UI / PDF brief can
        pick it up without re-calling Claude.
    """
    from ui.llm_clients import (
        ClaudeClient, MASTER_STRATEGIST_MODEL, MASTER_STRATEGIST_DEEP_MODEL,
        MASTER_STRATEGIST_THINKING_BUDGET,
    )

    model = MASTER_STRATEGIST_DEEP_MODEL if deep else MASTER_STRATEGIST_MODEL
    budget = thinking_budget or (
        MASTER_STRATEGIST_THINKING_BUDGET * 2 if deep
        else MASTER_STRATEGIST_THINKING_BUDGET
    )

    briefing = build_briefing()

    # No API key → return the rule-based fallback immediately.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        decision = _fallback_decision(briefing, model)
        out = decision.as_dict()
        if write_cache:
            _persist(out)
        return out

    client = ClaudeClient(model=model)

    # Highlight the playbook analogues + facts at the top of the
    # message so they aren't lost in the giant JSON dump below them.
    analogues = briefing.get("playbook_analogues") or []
    facts = briefing.get("playbook_facts") or {}
    if analogues:
        playbook_block = (
            "## Playbook analogues (deterministic match against the "
            "curated PSX case library)\n\n"
            "The matcher saw these live facts:\n\n"
            "```json\n" + json.dumps(facts, default=str, indent=2) + "\n```\n\n"
            f"Top {len(analogues)} analogous historical cases — lean on "
            "at least one in your `contributing_signals`, OR explain in "
            "`narrative` why none fits.\n\n"
            "```json\n"
            + json.dumps(analogues, default=str, indent=2)
            + "\n```\n\n"
        )
    else:
        playbook_block = (
            "## Playbook analogues\n\n"
            "_Matcher returned no analogues for today's situation. "
            "Reason from first principles and note this in `narrative`._\n\n"
        )

    history = [{
        "role": "user",
        "content": (
            playbook_block
            + "## Today's full briefing (every signal the bot has)\n\n"
            "```json\n"
            + json.dumps(briefing, default=str, indent=2)[:120_000]
            + "\n```\n\n"
            "Reason carefully across every section of the briefing, then "
            "return ONE JSON object that matches the schema in the system "
            "prompt. No prose outside the JSON object."
        ),
    }]

    try:
        result = client.run_chat(
            history=history,
            system=STRATEGIST_SYSTEM,
            max_tokens=max_tokens,
            thinking_budget=budget,
            # The strategist almost never needs a tool call (the
            # briefing already contains every tool's output) — keep
            # the loop short.
            max_tool_iterations=2,
        )
    except Exception as e:
        decision = _fallback_decision(briefing, model)
        decision.headline = f"LLM strategist failed ({type(e).__name__}); fallback active"
        decision.fallback_used = True
        out = decision.as_dict()
        if write_cache:
            _persist(out)
        return out

    parsed = _parse_json(result.get("text") or "")
    if not parsed:
        decision = _fallback_decision(briefing, model)
        decision.headline = "LLM returned unparseable response — fallback active"
        decision.raw_llm_text = (result.get("text") or "")[:6000]
        decision.thinking_trace = (result.get("thinking") or "")[:4000]
        out = decision.as_dict()
        if write_cache:
            _persist(out)
        return out

    actions: list[StrategistAction] = []
    for a in (parsed.get("actions") or []):
        if not isinstance(a, dict):
            continue
        actions.append(StrategistAction(
            symbol=a.get("symbol"),
            bucket=str(a.get("bucket") or "HOLD").upper(),
            conviction=str(a.get("conviction") or "MEDIUM").upper(),
            sector=str(a.get("sector") or ""),
            target_weight_pct=a.get("target_weight_pct"),
            reason=str(a.get("reason") or "")[:400],
            contributing_signals=list(a.get("contributing_signals") or [])[:8],
        ))

    decision = MasterDecision(
        as_of=briefing.get("as_of") or datetime.now(timezone.utc).isoformat(),
        model=model,
        thinking_budget=int(budget),
        headline=str(parsed.get("headline") or "")[:200],
        risk_stance=str(parsed.get("risk_stance") or "NORMAL").upper(),
        conviction=str(parsed.get("conviction") or "MEDIUM").upper(),
        narrative=str(parsed.get("narrative") or "")[:2000],
        agrees_with_phase1=bool(parsed.get("agrees_with_phase1", True)),
        phase1_disagreement_note=str(parsed.get("phase1_disagreement_note") or "")[:400],
        actions=actions,
        key_drivers=list(parsed.get("key_drivers") or [])[:8],
        key_risks=list(parsed.get("key_risks") or [])[:8],
        macro_lens=str(parsed.get("macro_lens") or "")[:1500],
        behavioural_lens=str(parsed.get("behavioural_lens") or "")[:1500],
        fallback_used=False,
        raw_llm_text=(result.get("text") or "")[:4000],
        thinking_trace=(result.get("thinking") or "")[:8000],
        briefing_summary=_briefing_summary(briefing),
    )
    out = decision.as_dict()
    out["usage"] = result.get("usage", {})
    out["stop_reason"] = result.get("stop_reason", "")
    if write_cache:
        _persist(out)
    return out


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
def cache_path(d: datetime | None = None) -> Path:
    d = d or datetime.now()
    return CACHE_DIR / f"{d.strftime('%Y-%m-%d')}.json"


def _persist(decision: dict) -> None:
    try:
        cache_path().write_text(
            json.dumps(decision, default=str, indent=2), encoding="utf-8")
        # Also keep an "always-latest" file the dashboard can read
        # without knowing today's date.
        (CACHE_DIR / "latest.json").write_text(
            json.dumps(decision, default=str, indent=2), encoding="utf-8")
    except Exception:
        # Never let cache write failures break the call.
        pass


def load_cached(d: datetime | None = None) -> dict | None:
    p = cache_path(d)
    if not p.exists():
        # Fall back to the always-latest file (covers weekends / holidays when
        # the workflow doesn't produce a date-specific file).
        p = CACHE_DIR / "latest.json"
        if not p.exists():
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover
    import sys
    deep = "--deep" in sys.argv
    out = decide_today(deep=deep)
    print(f"\nHeadline:  {out['headline']}")
    print(f"Stance:    {out['risk_stance']}  (conviction {out['conviction']})")
    print(f"Model:     {out['model']}  thinking={out['thinking_budget']}")
    print(f"Actions:   {len(out['actions'])}")
    for a in out["actions"]:
        print(f"  - {a['bucket']:>5}  {a['symbol'] or '-':<7}  "
              f"{a['conviction']:<6}  {a['reason'][:80]}")
    print(f"\nNarrative: {out['narrative']}")
    print(f"\nCached to: {cache_path()}")
