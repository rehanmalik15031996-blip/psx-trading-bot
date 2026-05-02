"""Playbook — institutional memory of PSX situation/reaction patterns.

Layer-1 of the knowledge-base initiative (see README -> 'The Playbook
(institutional memory)'). Reads ``data/playbook/cases.json`` and, given
a live Master Strategist briefing, returns the top-K most relevant
historical analogues so Claude can reason from past evidence rather
than from first principles every day.

Why this exists
---------------

The PSX research doc (Pakistan Stock Market Research Factors.docx)
makes the case explicitly: PSX is weak-form-inefficient and
behaviour-driven, which means historical situation/reaction patterns
*recur*. Rate-cut rallies, circular-debt resolution Power rallies,
IMF-approval bumps, FX shocks, FIPI capitulations — these aren't
noise; they're the structural edge a small operator gets in a
non-US emerging market. Encoding them as a curated case library
turns "Claude reasons from scratch every day" into "Claude reasons
from scratch + named past evidence with citations."

Public API
~~~~~~~~~~

    load_cases(path: Path | None = None) -> list[Case]
        Parse and validate the on-disk JSON. Skips invalid entries
        with a warning rather than failing the whole load.

    retrieve_analogues(briefing: dict, top_k: int | None = None,
                       min_score: float = 1.0) -> list[dict]
        Score every case against the briefing's active drivers,
        regime, FIPI, sentiment, breadth, and Phase-1 status, and
        return the top-K as compact dicts ready to inject into the
        strategist's Claude prompt.

    validate_file(path: Path) -> tuple[int, list[str]]
        Type-check the on-disk JSON and return (n_cases, errors).
        Used by ``python -m brain.playbook --validate`` and CI.

Notes for curators
------------------

* Reactions in ``historical_instances`` are FRACTIONAL returns
  (``0.071`` = +7.1%), NOT percentages. The matcher does not
  validate magnitudes — garbage-in / garbage-out.
* New cases are picked up immediately on the next strategist run.
* The matcher is intentionally simple (deterministic tag matching +
  recency / confidence weighting). No ML, no embeddings — for
  ~100 cases this is faster and more debuggable.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = ROOT / "data" / "playbook" / "cases.json"
DEFAULT_EVENTS_PATH = ROOT / "data" / "playbook" / "_events.json"
ISLAMIC_CALENDAR_PATH = ROOT / "data" / "playbook" / "_islamic_calendar.json"
SBP_RATES_PATH = ROOT / "data" / "macro" / "sbp_rates.parquet"
POLICY_RATE_HISTORY_PATH = ROOT / "data" / "macro" / "_policy_rate_history.json"

VALID_CATEGORIES = {"macro_event", "sector_event", "flow_regime",
                     "behavioural", "seasonal", "valuation", "technical"}
VALID_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
DEFAULT_EVENT_DECAY_DAYS = 14

# Valid trigger-kind prefixes — checked by validate_file() to catch
# typos (e.g. 'driver_rate_down' instead of 'driver:rate_down').
# Grouped by intent for readability:
VALID_TRIGGER_KINDS = {
    # Existing — driver / regime / flow / sentiment / breadth / phase1
    "driver", "regime", "fipi_5d_lt", "fipi_5d_gt",
    "sentiment_lt", "sentiment_gt", "breadth_lt", "breadth_gt",
    "universe_5d_lt", "universe_5d_gt",
    "phase1", "earnings_blackouts_gte", "event", "sector",
    # Phase C — macro levels (not just direction)
    "policy_rate_lte", "policy_rate_gte",
    "kibor3m_lte", "kibor3m_gte", "tbill3m_lte", "tbill3m_gte",
    "usdpkr_lte", "usdpkr_gte",
    "brent_lte", "brent_gte", "gold_lte", "gold_gte",
    "cpi_yoy_lte", "cpi_yoy_gte",
    "fx_reserves_lt_bn", "fx_reserves_gt_bn",
    "kse100_5d_lte", "kse100_5d_gte",
    "kse100_21d_lte", "kse100_21d_gte",
    # Phase C — cycle context (multi-decision lookbacks)
    "rate_cuts_in_window", "rate_hikes_in_window",
    "kibor3m_drop_in_window", "kibor3m_rise_in_window",
    # Cycle context — count comparators (eq / gte / lte)
    "rate_cuts_180d_eq", "rate_cuts_180d_gte", "rate_cuts_180d_lte",
    "rate_hikes_180d_eq", "rate_hikes_180d_gte", "rate_hikes_180d_lte",
    "rate_cuts_90d_gte", "rate_hikes_90d_gte",
    # Cycle context — freshness gates ("days since last decision")
    "days_since_last_cut_lte", "days_since_last_cut_gte",
    "days_since_last_hike_lte", "days_since_last_hike_gte",
    "days_since_last_rate_change_lte", "days_since_last_rate_change_gte",
    # Phase C — valuation aggregates (universe / sector level)
    "universe_pe_lte", "universe_pe_gte",
    "universe_pb_lte", "universe_pb_gte",
    "n_below_fair_value_gte", "n_buy_value_gte",
    "n_quality_high_gte", "n_quality_low_gte",
    # Phase C — earnings momentum aggregates
    "n_eps_accelerating_gte", "n_eps_decelerating_gte",
    # Phase C — calendar (Gregorian)
    "month_in", "day_of_week_in", "last_n_trading_days_of_month",
    # Phase C — calendar (Hijri / Islamic)
    "ramadan", "pre_ramadan_window", "post_eid_window",
    "high_vol_islamic_month",
    # Phase D — mutual-fund "smart money" flows (universe-level).
    # Per-stock variants (mf_*_for) accept SYMBOL,N format and check
    # whether AT LEAST ONE stock in the briefing's MF book matches.
    "mf_universe_n_funds_increasing_gte",
    "mf_universe_n_top_accumulated_gte",
    "mf_universe_n_top_distributed_gte",
    "mf_accumulation_streak_gte",
    "mf_distribution_streak_gte",
    "mf_n_funds_initiating_30d_gte",
    "mf_n_funds_increasing_30d_gte",
    "mf_holding_change_30d_pct_gte",
    "mf_holding_change_30d_pct_lte",
    "mf_holding_change_180d_pct_gte",
    "mf_holding_change_180d_pct_lte",
    "mf_data_freshness_lte",
    # Phase E — volume confirmation (validated 2026-05-02 against PSX
    # history: +1.5% day on >=1.5x median 20d volume → +0.80% fwd 5d
    # vs +0.23% on low-volume up days, n=4,657 vs 734).
    "volume_breakout_count_gte",
    "volume_data_freshness_lte",
}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
@dataclass
class HistoricalInstance:
    date: str
    context: str = ""
    reactions: dict = field(default_factory=dict)
    source: str = ""


@dataclass
class Case:
    id: str
    category: str
    title: str
    pattern: str
    trigger_signals: list[str]
    historical_instances: list[HistoricalInstance]
    playbook: str
    what_breaks_it: str = ""
    confidence: str = "MEDIUM"
    research_basis: str = ""
    tags: list[str] = field(default_factory=list)
    min_triggers: int | None = None  # None = require all triggers

    def required_triggers(self) -> int:
        return self.min_triggers or len(self.trigger_signals)


# ---------------------------------------------------------------------------
# Loader / validator
# ---------------------------------------------------------------------------
def _coerce_instance(raw: dict) -> HistoricalInstance | None:
    if not isinstance(raw, dict) or not raw.get("date"):
        return None
    reactions = raw.get("reactions") or {}
    if not isinstance(reactions, dict):
        reactions = {}
    return HistoricalInstance(
        date=str(raw["date"]),
        context=str(raw.get("context") or ""),
        reactions=reactions,
        source=str(raw.get("source") or ""),
    )


def _coerce_case(raw: dict) -> Case | None:
    if not isinstance(raw, dict):
        return None
    cid = raw.get("id")
    triggers = raw.get("trigger_signals") or []
    if not cid or not isinstance(triggers, list) or not triggers:
        return None
    instances = [_coerce_instance(x) for x in (raw.get("historical_instances") or [])]
    instances = [x for x in instances if x is not None]
    return Case(
        id=str(cid),
        category=str(raw.get("category") or "macro_event"),
        title=str(raw.get("title") or cid),
        pattern=str(raw.get("pattern") or ""),
        trigger_signals=[str(t) for t in triggers],
        historical_instances=instances,
        playbook=str(raw.get("playbook") or "")[:1500],
        what_breaks_it=str(raw.get("what_breaks_it") or "")[:600],
        confidence=str(raw.get("confidence") or "MEDIUM").upper(),
        research_basis=str(raw.get("research_basis") or "")[:400],
        tags=[str(t) for t in (raw.get("tags") or [])],
        min_triggers=raw.get("min_triggers"),
    )


def load_cases(path: Path | None = None) -> list[Case]:
    """Parse the case library. Invalid entries are skipped with a
    warning printed to stderr; we never let a single malformed case
    take down the whole strategist run.
    """
    p = path or DEFAULT_CASES_PATH
    if not p.exists():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[playbook] cases.json invalid JSON: {e}", file=sys.stderr)
        return []

    raw_cases = payload.get("cases") or []
    out: list[Case] = []
    for raw in raw_cases:
        c = _coerce_case(raw)
        if c is None:
            print(f"[playbook] skipping malformed case: {raw}", file=sys.stderr)
            continue
        out.append(c)
    return out


def validate_file(path: Path | None = None) -> tuple[int, list[str]]:
    """Strict validation pass for CI. Returns (n_valid, errors)."""
    p = path or DEFAULT_CASES_PATH
    errors: list[str] = []
    if not p.exists():
        return 0, [f"cases file missing: {p}"]
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return 0, [f"invalid JSON: {e}"]

    raw_cases = payload.get("cases") or []
    seen_ids: set[str] = set()
    n_valid = 0
    for i, raw in enumerate(raw_cases):
        loc = f"cases[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{loc}: not an object"); continue
        cid = raw.get("id")
        if not cid or not isinstance(cid, str):
            errors.append(f"{loc}: missing/invalid id"); continue
        if cid in seen_ids:
            errors.append(f"{loc}: duplicate id {cid!r}"); continue
        seen_ids.add(cid)
        cat = raw.get("category", "macro_event")
        if cat not in VALID_CATEGORIES:
            errors.append(f"{cid}: bad category {cat!r}")
        conf = (raw.get("confidence") or "MEDIUM").upper()
        if conf not in VALID_CONFIDENCE:
            errors.append(f"{cid}: bad confidence {conf!r}")
        triggers = raw.get("trigger_signals") or []
        if not isinstance(triggers, list) or not triggers:
            errors.append(f"{cid}: trigger_signals must be a non-empty list")
        else:
            for t in triggers:
                if not isinstance(t, str) or ":" not in t:
                    errors.append(f"{cid}: bad trigger {t!r} (must be 'kind:value')")
                    continue
                kind = t.split(":", 1)[0].lower()
                if kind not in VALID_TRIGGER_KINDS:
                    errors.append(
                        f"{cid}: unknown trigger kind {kind!r} in {t!r} — "
                        f"valid kinds: {sorted(VALID_TRIGGER_KINDS)}")
        # historical_instances reactions sanity: all values 0..1 fractional
        for inst in (raw.get("historical_instances") or []):
            if not isinstance(inst, dict):
                errors.append(f"{cid}: instance not an object"); continue
            if not inst.get("date"):
                errors.append(f"{cid}: instance missing date")
            for sym, r in (inst.get("reactions") or {}).items():
                if not isinstance(r, dict):
                    errors.append(f"{cid}/{inst.get('date')}/{sym}: reaction not object"); continue
                for k, v in r.items():
                    if not isinstance(v, (int, float)):
                        errors.append(f"{cid}/{inst.get('date')}/{sym}/{k}: not numeric")
                    elif abs(v) > 1.0:
                        errors.append(f"{cid}/{inst.get('date')}/{sym}/{k}={v}: looks like a percentage; reactions must be FRACTIONAL (0.05 = +5%)")
        if _coerce_case(raw) is not None:
            n_valid += 1
    return n_valid, errors


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------
def _load_active_events(path: Path | None = None) -> set[str]:
    """Return the set of event keys whose date is within their decay
    window today. Future-dated and past-decayed events are filtered
    out. Missing / malformed file → empty set (silent)."""
    p = path or DEFAULT_EVENTS_PATH
    if not p.exists():
        return set()
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    today = date.today()
    active: set[str] = set()
    for ev in (payload.get("events") or []):
        if not isinstance(ev, dict):
            continue
        key = str(ev.get("key") or "").strip()
        if not key:
            continue
        try:
            d = datetime.strptime(str(ev["date"]), "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        decay = int(ev.get("decay_days") or DEFAULT_EVENT_DECAY_DAYS)
        if d > today:
            continue
        if (today - d).days <= decay:
            active.add(key.lower())
    return active


def _count_earnings_blackouts(briefing: dict) -> int:
    """How many universe names have earnings within the next 5 trading
    days. Reads briefing['earnings_calendar'] which the strategist
    builder populates from get_earnings_calendar(days_ahead=21)."""
    cal = briefing.get("earnings_calendar") or {}
    if not isinstance(cal, dict):
        return 0
    blackout = cal.get("blackout_now")
    if isinstance(blackout, list):
        return len(blackout)
    rows = cal.get("upcoming") or cal.get("rows") or []
    if not isinstance(rows, list):
        return 0
    return sum(1 for r in rows
               if isinstance(r, dict) and r.get("in_blackout_5d"))


# ---------------------------------------------------------------------------
# Phase C: macro level / valuation / earnings / technical / calendar facts
# ---------------------------------------------------------------------------
def _macro_level_facts(briefing: dict) -> dict:
    """Pull macro LEVELS (not just directional changes) out of the briefing.
    These are what feed the new policy_rate_*, kibor3m_*, usdpkr_*, brent_*,
    cpi_yoy_*, fx_reserves_*, kse100_*d_* trigger families."""
    out: dict = {
        "policy_rate_pct": None, "kibor3m_pct": None, "tbill3m_pct": None,
        "usdpkr": None, "brent_usd_bbl": None, "gold_usd_oz": None,
        "cpi_yoy_pct": None, "fx_reserves_total_usd_bn": None,
        "kse100_ret_5d": None, "kse100_ret_21d": None,
    }
    pr = briefing.get("policy_rate") or {}
    if isinstance(pr, dict) and pr.get("policy_rate_pct") is not None:
        try:
            out["policy_rate_pct"] = float(pr["policy_rate_pct"])
        except (TypeError, ValueError):
            pass
    kpis = ((briefing.get("industry_kpis") or {}).get("kpis")
            or briefing.get("industry_kpis") or {})
    if isinstance(kpis, dict):
        for src, dst in (("kibor_3m_pct", "kibor3m_pct"),
                           ("tbill_3m_pct", "tbill3m_pct"),
                           ("cpi_yoy_pct", "cpi_yoy_pct"),
                           ("kse100_ret_5d", "kse100_ret_5d"),
                           ("kse100_ret_21d", "kse100_ret_21d")):
            v = kpis.get(src)
            if v is not None:
                try:
                    out[dst] = float(v)
                except (TypeError, ValueError):
                    pass
        rsv = kpis.get("reserves_total_usd_mn")
        if rsv is not None:
            try:
                out["fx_reserves_total_usd_bn"] = float(rsv) / 1000.0
            except (TypeError, ValueError):
                pass
    snap = briefing.get("macro_snapshot") or {}
    indicators = (snap or {}).get("indicators") if isinstance(snap, dict) else None
    if isinstance(indicators, dict):
        for src, dst in (("usdpkr", "usdpkr"),
                           ("brent", "brent_usd_bbl"),
                           ("gold", "gold_usd_oz")):
            block = indicators.get(src) or {}
            if isinstance(block, dict) and block.get("last") is not None:
                try:
                    out[dst] = float(block["last"])
                except (TypeError, ValueError):
                    pass
    return out


def _valuation_facts(briefing: dict) -> dict:
    """Counts and aggregates over the value/quality/earnings books.
    Universe averages exclude rows with missing data."""
    out = {
        "universe_pe_avg": None, "universe_pb_avg": None,
        "n_below_fair_value": 0, "n_buy_value": 0,
        "n_quality_high": 0, "n_quality_low": 0,
        "n_eps_accelerating": 0, "n_eps_decelerating": 0,
    }

    val = briefing.get("value_book") or {}
    rows = val.get("rows") or val.get("book") or val.get("results") or []
    pe_vals: list[float] = []
    pb_vals: list[float] = []
    if isinstance(rows, list):
        for r in rows:
            if not isinstance(r, dict):
                continue
            band = (r.get("band") or r.get("verdict") or "").upper()
            if band in ("BUY_VALUE", "BUY", "UNDERVALUED", "DEEP_VALUE"):
                out["n_buy_value"] += 1
                out["n_below_fair_value"] += 1
            elif band in ("FAIR_VALUE", "FAIR"):
                pass
            else:
                upside = r.get("upside_pct")
                if isinstance(upside, (int, float)) and upside > 0:
                    out["n_below_fair_value"] += 1
            # Pull PE and PB independently — a row with both should
            # contribute to BOTH averages, not just the first one
            # encountered. Each metric stops at its first valid hit.
            for src in ("pe_ttm", "pe"):
                v = r.get(src)
                if isinstance(v, (int, float)) and 0 < v < 200:
                    pe_vals.append(float(v))
                    break
            for src in ("pb", "pb_ratio"):
                v = r.get(src)
                if isinstance(v, (int, float)) and 0 < v < 200:
                    pb_vals.append(float(v))
                    break
    if pe_vals:
        out["universe_pe_avg"] = round(sum(pe_vals) / len(pe_vals), 2)
    if pb_vals:
        out["universe_pb_avg"] = round(sum(pb_vals) / len(pb_vals), 2)

    qual = briefing.get("quality_book") or {}
    qrows = qual.get("rows") or qual.get("book") or qual.get("results") or []
    if isinstance(qrows, list):
        for r in qrows:
            if not isinstance(r, dict):
                continue
            band = (r.get("band") or r.get("verdict") or "").upper()
            if band in ("HIGH", "GOOD", "QUALITY"):
                out["n_quality_high"] += 1
            elif band in ("LOW", "POOR", "JUNK"):
                out["n_quality_low"] += 1

    em = briefing.get("earnings_momentum") or {}
    erows = em.get("rows") or em.get("book") or em.get("results") or []
    if isinstance(erows, list):
        for r in erows:
            if not isinstance(r, dict):
                continue
            flag = (r.get("flag") or r.get("band") or "").upper()
            if flag in ("ACCELERATING", "RECOVERING"):
                out["n_eps_accelerating"] += 1
            elif flag in ("DECELERATING", "EROSION"):
                out["n_eps_decelerating"] += 1
    return out


def _cycle_context(window_days: int = 180, *,
                     as_of: date | None = None,
                     history_entries: list[tuple[date, float]] | None = None
                     ) -> dict:
    """Count SBP rate cuts / hikes (>=25 bps) in the last
    ``window_days`` ending at ``as_of`` (default: today). Also returns
    ``days_since_last_cut`` and ``days_since_last_hike`` so cases can
    require freshness on rate-decision drivers.

    Reads data/macro/_policy_rate_history.json which the macro engine
    keeps current. Callers (e.g. the historical replay test) can pass
    ``history_entries`` directly to bypass disk I/O.
    """
    out = {"rate_cuts_180d": 0, "rate_hikes_180d": 0,
           "rate_cuts_90d": 0, "rate_hikes_90d": 0,
           "kibor3m_change_30d_bps": None,
           "days_since_last_cut": None,
           "days_since_last_hike": None,
           "days_since_last_rate_change": None}

    parsed: list[tuple[date, float]] = list(history_entries or [])
    if not parsed and POLICY_RATE_HISTORY_PATH.exists():
        try:
            payload = json.loads(POLICY_RATE_HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                entries = payload
            elif isinstance(payload, dict):
                entries = payload.get("entries") or payload.get("history") or []
            else:
                entries = []
            if isinstance(entries, list):
                for e in entries:
                    try:
                        d = datetime.strptime(str(e.get("date")), "%Y-%m-%d").date()
                        r = float(e.get("rate_pct"))
                    except (TypeError, ValueError):
                        continue
                    parsed.append((d, r))
        except (json.JSONDecodeError, OSError):
            parsed = []

    parsed.sort(key=lambda x: x[0])
    if parsed:
        from datetime import timedelta
        anchor = as_of or date.today()
        cut_180 = anchor - timedelta(days=180)
        cut_90  = anchor - timedelta(days=90)
        last_cut: date | None = None
        last_hike: date | None = None
        last_change: date | None = None
        for i in range(1, len(parsed)):
            d, r = parsed[i]
            prev_r = parsed[i - 1][1]
            if d > anchor:
                break  # don't peek into the future when replaying
            delta = r - prev_r
            if abs(delta) >= 0.25:
                last_change = d
            if d < cut_180:
                if delta <= -0.25:
                    last_cut = d
                elif delta >= 0.25:
                    last_hike = d
                continue
            if delta <= -0.25:
                out["rate_cuts_180d"] += 1
                last_cut = d
                if d >= cut_90:
                    out["rate_cuts_90d"] += 1
            elif delta >= 0.25:
                out["rate_hikes_180d"] += 1
                last_hike = d
                if d >= cut_90:
                    out["rate_hikes_90d"] += 1
        if last_cut is not None:
            out["days_since_last_cut"] = (anchor - last_cut).days
        if last_hike is not None:
            out["days_since_last_hike"] = (anchor - last_hike).days
        if last_change is not None:
            out["days_since_last_rate_change"] = (anchor - last_change).days

    if SBP_RATES_PATH.exists() and history_entries is None:
        try:
            import pandas as pd
            df = pd.read_parquet(SBP_RATES_PATH).sort_values("date")
            if len(df) >= 22 and "kibor_3m_pct" in df.columns:
                last = float(df.iloc[-1]["kibor_3m_pct"])
                old = float(df.iloc[-22]["kibor_3m_pct"])
                out["kibor3m_change_30d_bps"] = round((last - old) * 100, 1)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# Calendar (Gregorian + Hijri)
# ---------------------------------------------------------------------------
def _load_islamic_calendar() -> dict:
    if not ISLAMIC_CALENDAR_PATH.exists():
        return {}
    try:
        return json.loads(ISLAMIC_CALENDAR_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _calendar_facts(today: date | None = None) -> dict:
    """Return structured calendar context: month, day-of-week, days
    remaining in the month, and Hijri-window flags."""
    today = today or date.today()
    cal = _load_islamic_calendar()
    facts = {
        "month": today.month,
        "day_of_week": today.weekday(),  # Mon=0 .. Sun=6
        "trading_days_left_in_month": _trading_days_remaining_this_month(today),
        "in_ramadan": False,
        "days_to_ramadan_start": None,
        "days_since_eid_ul_fitr": None,
        "in_high_vol_islamic_month": None,  # 'Safar' / 'Zil-Qad' / 'Zil-Hajj' or None
    }
    for w in (cal.get("ramadan_windows") or []):
        try:
            s = datetime.strptime(w["start"], "%Y-%m-%d").date()
            e = datetime.strptime(w["end"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if s <= today <= e:
            facts["in_ramadan"] = True
        if today < s and (facts["days_to_ramadan_start"] is None
                            or (s - today).days < facts["days_to_ramadan_start"]):
            facts["days_to_ramadan_start"] = (s - today).days
    for s in (cal.get("eid_ul_fitr") or []):
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            continue
        if 0 <= (today - d).days <= 60:
            facts["days_since_eid_ul_fitr"] = (today - d).days
    for w in (cal.get("high_vol_islamic_months_gregorian_starts") or []):
        try:
            s = datetime.strptime(w["start"], "%Y-%m-%d").date()
            e = datetime.strptime(w["end"], "%Y-%m-%d").date()
        except (KeyError, ValueError):
            continue
        if s <= today <= e:
            facts["in_high_vol_islamic_month"] = w.get("name")
            break
    return facts


def _trading_days_remaining_this_month(today: date) -> int:
    """Approximate trading days remaining in the current month
    (counts Mon-Fri only; ignores PSX holidays for simplicity since
    the granularity we need is 'last 5' / 'last 10')."""
    from datetime import timedelta
    n = 0
    d = today
    while d.month == today.month:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n - 1  # exclude today's count contribution to "remaining"


def _briefing_facts(briefing: dict) -> dict:
    """Extract the small set of fields the matcher cares about into a
    flat dict. Tolerates missing / malformed inputs from the
    briefing builder."""
    facts = {
        "drivers": [],          # list of (tag, magnitude) tuples
        "regime": None,         # NORMAL / CAUTION / CRISIS
        "fipi_5d": None,        # PKR mn
        "sentiment": None,      # [-1, +1]
        "breadth": None,        # 0-100 (% breadth above x)
        "universe_5d": None,    # fractional return
        "phase1_risk_on": None, # bool
        "earnings_blackouts": 0,
        "active_events": set(), # lowercased event keys currently in window
    }

    mi = briefing.get("macro_impact") or {}
    for d in (mi.get("drivers") or []):
        if not isinstance(d, dict):
            continue
        tag = str(d.get("tag") or "").strip().lower()
        mag = str(d.get("magnitude") or "").strip().upper()
        if tag:
            facts["drivers"].append((tag, mag))

    reg = briefing.get("regime") or {}
    facts["regime"] = (reg.get("regime") or "").upper() or None
    if reg.get("breadth_pct_up") is not None:
        try:
            facts["breadth"] = float(reg["breadth_pct_up"])
        except (TypeError, ValueError):
            pass
    if reg.get("universe_ret_5d") is not None:
        try:
            facts["universe_5d"] = float(reg["universe_ret_5d"])
        except (TypeError, ValueError):
            pass

    fipi = briefing.get("fipi_flows") or {}
    if fipi.get("net_5d_pkr_mn") is not None:
        try:
            facts["fipi_5d"] = float(fipi["net_5d_pkr_mn"])
        except (TypeError, ValueError):
            pass

    sent = briefing.get("scored_sentiment") or {}
    if sent.get("tilt") is not None:
        try:
            facts["sentiment"] = float(sent["tilt"])
        except (TypeError, ValueError):
            pass

    sig = briefing.get("strategy_signal") or {}
    if "market_risk_on" in sig:
        facts["phase1_risk_on"] = bool(sig["market_risk_on"])

    facts["earnings_blackouts"] = _count_earnings_blackouts(briefing)
    facts["active_events"] = _load_active_events()

    # Phase C: macro levels, valuation aggregates, cycle context, calendar.
    facts["macro_levels"] = _macro_level_facts(briefing)
    facts["valuation"]    = _valuation_facts(briefing)
    # Briefing may pre-populate cycle context — used by the historical
    # replay to inject as-of-date cycle state without disk I/O.
    facts["cycle"]        = (briefing.get("_cycle_override")
                              or _cycle_context())
    facts["calendar"]     = (briefing.get("_calendar_override")
                              or _calendar_facts())
    # Phase D: mutual-fund flows. The briefing carries them under
    # ``mf_holdings`` (populated by master_strategist.build_briefing).
    # Per-stock signals are embedded too so per-stock triggers can
    # filter the universe.
    facts["mf"]           = _mf_facts(briefing)
    # Phase E: volume confirmation -- universe count of confirmed
    # breakout days in the last few sessions (set by build_briefing
    # via brain.volume_signals.universe_summary). Falls back to
    # zero/None when unavailable so triggers silently fail.
    facts["volume"]       = _volume_facts(briefing)

    return facts


def _volume_facts(briefing: dict) -> dict:
    """Extract volume-confirmation signals from the briefing.

    Reads ``briefing['volume_signals']`` (set by
    ``master_strategist.build_briefing``). Returns sane defaults so
    the trigger evaluator never raises on missing data."""
    out = {
        "n_confirmed_breakouts": 0,
        "data_freshness_days":   None,
        "breakout_names":        [],
    }
    payload = briefing.get("volume_signals") or {}
    if not isinstance(payload, dict):
        return out
    try:
        out["n_confirmed_breakouts"] = int(
            payload.get("n_confirmed_breakouts_3d") or 0)
    except (TypeError, ValueError):
        pass
    if payload.get("data_freshness_days") is not None:
        try:
            out["data_freshness_days"] = int(payload["data_freshness_days"])
        except (TypeError, ValueError):
            pass
    names = payload.get("breakout_names") or []
    if isinstance(names, list):
        out["breakout_names"] = [str(s).upper() for s in names]
    return out


def _mf_facts(briefing: dict) -> dict:
    """Aggregate the mutual-fund flow signals from the briefing into
    a flat dict for trigger evaluation.

    Looks at ``briefing['mf_holdings']`` if present (set by
    ``master_strategist.build_briefing``) and falls back to direct
    ``brain.mf_flows`` lookups when not. Always returns a dict; missing
    inputs ⇒ all-None values."""
    out = {
        "data_freshness_days":         None,
        "n_funds_increasing_universe": None,
        "n_top_accumulated":           0,
        "n_top_distributed":           0,
        "max_accumulation_streak":     None,
        "max_distribution_streak":     None,
        "max_n_funds_initiating_30d":  None,
        "max_n_funds_increasing_30d":  None,
        "max_holding_change_30d_pct":  None,
        "min_holding_change_30d_pct":  None,
        "max_holding_change_180d_pct": None,
        "min_holding_change_180d_pct": None,
        "per_stock":                   {},
    }
    payload = briefing.get("mf_holdings") or {}
    if not isinstance(payload, dict):
        return out
    if payload.get("data_freshness_days") is not None:
        try:
            out["data_freshness_days"] = int(payload["data_freshness_days"])
        except (TypeError, ValueError):
            pass
    if payload.get("n_funds_increasing_universe") is not None:
        try:
            out["n_funds_increasing_universe"] = int(
                payload["n_funds_increasing_universe"])
        except (TypeError, ValueError):
            pass
    # Universe-level top counts: use whichever lookback yields more
    # entries so the 30-day MoM column kicks in when the parquet is
    # too shallow for a clean 180-day join.
    out["n_top_accumulated"] = max(
        len(payload.get("top_accumulated_180d") or []),
        len(payload.get("top_accumulated_30d")  or []),
    )
    out["n_top_distributed"] = max(
        len(payload.get("top_distributed_180d") or []),
        len(payload.get("top_distributed_30d")  or []),
    )

    # Per-stock signals (used by per-stock triggers) -- the briefing
    # may include them under 'per_stock_signals' as a {SYM: {...}} map.
    # Stocks whose own per-stock signals are >60 days stale are still
    # surfaced under ``out['per_stock']`` for inspection but are NOT
    # rolled into the universe-level max/min aggregates -- otherwise
    # we'd keep firing 6-month-old initiation clusters forever.
    per_stock = payload.get("per_stock_signals") or {}
    FRESH_GATE_DAYS = 60
    if isinstance(per_stock, dict):
        for sym, sig in per_stock.items():
            if not isinstance(sig, dict):
                continue
            out["per_stock"][str(sym).upper()] = sig
            try:
                stock_freshness = sig.get("mf_data_freshness_days")
                if stock_freshness is not None and float(stock_freshness) > FRESH_GATE_DAYS:
                    continue
            except (TypeError, ValueError):
                pass
            for src, agg_key, op in (
                ("mf_accumulation_streak",
                  "max_accumulation_streak",     "max"),
                ("mf_distribution_streak",
                  "max_distribution_streak",     "max"),
                ("mf_n_funds_initiating_30d",
                  "max_n_funds_initiating_30d",  "max"),
                ("mf_n_funds_increasing_30d",
                  "max_n_funds_increasing_30d",  "max"),
                ("mf_holding_change_30d_pct",
                  "max_holding_change_30d_pct",  "max"),
                ("mf_holding_change_30d_pct",
                  "min_holding_change_30d_pct",  "min"),
                ("mf_holding_change_180d_pct",
                  "max_holding_change_180d_pct", "max"),
                ("mf_holding_change_180d_pct",
                  "min_holding_change_180d_pct", "min"),
            ):
                v = sig.get(src)
                if v is None:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    continue
                cur = out[agg_key]
                if cur is None:
                    out[agg_key] = v
                elif op == "max" and v > cur:
                    out[agg_key] = v
                elif op == "min" and v < cur:
                    out[agg_key] = v
    return out


def _eval_trigger(trigger: str, facts: dict) -> bool:
    """Evaluate one trigger string against the briefing facts.
    Unknown trigger kinds return False so a typo never silently
    fires a case."""
    try:
        kind, value = trigger.split(":", 1)
    except ValueError:
        return False
    kind = kind.strip().lower()
    value = value.strip()

    if kind == "driver":
        # 'driver:rate_down' or 'driver:rate_down:STRONG'
        if ":" in value:
            tag, mag = value.split(":", 1)
            tag = tag.lower(); mag = mag.upper()
            return any(t == tag and m == mag for (t, m) in facts["drivers"])
        tag = value.lower()
        return any(t == tag for (t, _m) in facts["drivers"])

    if kind == "regime":
        return facts["regime"] == value.upper()

    if kind == "fipi_5d_lt":
        try:
            thr = float(value)
        except ValueError:
            return False
        return facts["fipi_5d"] is not None and facts["fipi_5d"] < thr
    if kind == "fipi_5d_gt":
        try:
            thr = float(value)
        except ValueError:
            return False
        return facts["fipi_5d"] is not None and facts["fipi_5d"] > thr

    if kind == "sentiment_lt":
        try:
            thr = float(value)
        except ValueError:
            return False
        return facts["sentiment"] is not None and facts["sentiment"] < thr

    if kind == "breadth_lt":
        try:
            thr = float(value)
        except ValueError:
            return False
        return facts["breadth"] is not None and facts["breadth"] < thr

    if kind == "universe_5d_lt":
        try:
            thr = float(value)
        except ValueError:
            return False
        return facts["universe_5d"] is not None and facts["universe_5d"] < thr

    if kind == "phase1":
        v = value.upper()
        if v == "CASH":
            return facts["phase1_risk_on"] is False
        if v == "RISK_ON":
            return facts["phase1_risk_on"] is True
        return False

    if kind == "earnings_blackouts_gte":
        try:
            thr = int(value)
        except ValueError:
            return False
        return int(facts.get("earnings_blackouts") or 0) >= thr

    if kind == "event":
        # Active iff the event key is in the current decay window
        # (loaded from data/playbook/_events.json).
        return value.lower() in (facts.get("active_events") or set())

    if kind == "sector":
        # Pure context tag — never fires on its own. The case has to
        # have at least one other trigger that fires.
        return False

    # ---- Phase C: numeric extras ------------------------------------
    if kind in ("sentiment_gt", "breadth_gt", "universe_5d_gt"):
        # symmetric '_gt' counterparts to the existing _lt triggers
        attr = {"sentiment_gt": "sentiment", "breadth_gt": "breadth",
                "universe_5d_gt": "universe_5d"}[kind]
        try:
            thr = float(value)
        except ValueError:
            return False
        return facts.get(attr) is not None and facts[attr] > thr

    macro_levels = facts.get("macro_levels") or {}
    valuation    = facts.get("valuation")    or {}
    cycle        = facts.get("cycle")        or {}
    cal          = facts.get("calendar")     or {}

    # Single-value numeric thresholds — table-driven for compactness.
    NUMERIC_TRIGGERS: dict[str, tuple[dict, str, str]] = {
        # kind                    -> (source dict, key, op)
        "policy_rate_lte":        (macro_levels, "policy_rate_pct",        "<="),
        "policy_rate_gte":        (macro_levels, "policy_rate_pct",        ">="),
        "kibor3m_lte":            (macro_levels, "kibor3m_pct",            "<="),
        "kibor3m_gte":            (macro_levels, "kibor3m_pct",            ">="),
        "tbill3m_lte":            (macro_levels, "tbill3m_pct",            "<="),
        "tbill3m_gte":            (macro_levels, "tbill3m_pct",            ">="),
        "usdpkr_lte":             (macro_levels, "usdpkr",                 "<="),
        "usdpkr_gte":             (macro_levels, "usdpkr",                 ">="),
        "brent_lte":              (macro_levels, "brent_usd_bbl",          "<="),
        "brent_gte":              (macro_levels, "brent_usd_bbl",          ">="),
        "gold_lte":               (macro_levels, "gold_usd_oz",            "<="),
        "gold_gte":               (macro_levels, "gold_usd_oz",            ">="),
        "cpi_yoy_lte":            (macro_levels, "cpi_yoy_pct",            "<="),
        "cpi_yoy_gte":            (macro_levels, "cpi_yoy_pct",            ">="),
        "fx_reserves_lt_bn":      (macro_levels, "fx_reserves_total_usd_bn","<"),
        "fx_reserves_gt_bn":      (macro_levels, "fx_reserves_total_usd_bn",">"),
        "kse100_5d_lte":          (macro_levels, "kse100_ret_5d",          "<="),
        "kse100_5d_gte":          (macro_levels, "kse100_ret_5d",          ">="),
        "kse100_21d_lte":         (macro_levels, "kse100_ret_21d",         "<="),
        "kse100_21d_gte":         (macro_levels, "kse100_ret_21d",         ">="),
        "universe_pe_lte":        (valuation,    "universe_pe_avg",        "<="),
        "universe_pe_gte":        (valuation,    "universe_pe_avg",        ">="),
        "universe_pb_lte":        (valuation,    "universe_pb_avg",        "<="),
        "universe_pb_gte":        (valuation,    "universe_pb_avg",        ">="),
        "n_below_fair_value_gte": (valuation,    "n_below_fair_value",     ">="),
        "n_buy_value_gte":        (valuation,    "n_buy_value",            ">="),
        "n_quality_high_gte":     (valuation,    "n_quality_high",         ">="),
        "n_quality_low_gte":      (valuation,    "n_quality_low",          ">="),
        "n_eps_accelerating_gte": (valuation,    "n_eps_accelerating",     ">="),
        "n_eps_decelerating_gte": (valuation,    "n_eps_decelerating",     ">="),
    }
    if kind in NUMERIC_TRIGGERS:
        try:
            thr = float(value)
        except ValueError:
            return False
        src, key, op = NUMERIC_TRIGGERS[kind]
        v = src.get(key) if isinstance(src, dict) else None
        if v is None:
            return False
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False
        return ((op == "<=" and v <= thr) or (op == ">=" and v >= thr)
                or (op == "<"  and v <  thr) or (op == ">"  and v >  thr))

    # ---- Phase C: cycle context ('count,window' triggers) ----------
    if kind in ("rate_cuts_in_window", "rate_hikes_in_window",
                "kibor3m_drop_in_window", "kibor3m_rise_in_window"):
        try:
            count_str, window_str = value.split(",")
            min_count = int(count_str.strip())
            window = int(window_str.strip())
        except (ValueError, AttributeError):
            return False
        if kind in ("rate_cuts_in_window", "rate_hikes_in_window"):
            # Map window-days -> the only two precomputed buckets we keep
            bucket = "180d" if window > 90 else "90d"
            key = ("rate_cuts_" if "cuts" in kind else "rate_hikes_") + bucket
            return int(cycle.get(key) or 0) >= min_count
        # KIBOR drop / rise: min_count is bps over the rolling 30d window.
        kchg = cycle.get("kibor3m_change_30d_bps")
        if kchg is None:
            return False
        if "drop" in kind:
            return kchg <= -float(min_count)
        return kchg >= float(min_count)

    # ---- Cycle: count comparators (rate_cuts_180d_eq:1, _gte:2 etc) -
    # Encodes "exactly N" / "at least N" without the comma syntax.
    if kind in ("rate_cuts_180d_eq", "rate_cuts_180d_gte",
                "rate_cuts_180d_lte", "rate_hikes_180d_eq",
                "rate_hikes_180d_gte", "rate_hikes_180d_lte",
                "rate_cuts_90d_gte", "rate_hikes_90d_gte"):
        try:
            n = int(value)
        except ValueError:
            return False
        bucket_key = kind.rsplit("_", 1)[0]  # e.g. "rate_cuts_180d"
        op = kind.rsplit("_", 1)[1]
        actual = int(cycle.get(bucket_key) or 0)
        return ((op == "eq"  and actual == n) or
                 (op == "gte" and actual >= n) or
                 (op == "lte" and actual <= n))

    # ---- Cycle: freshness gates (days_since_last_cut_lte:5 etc) -----
    # Critical for distinguishing "rate cut today" from "rate cut 3
    # weeks ago that is already priced in". Returns False when the
    # field is None (no decision in the lookback) -- this is the
    # right behaviour because the gate only fires when there IS a
    # recent decision.
    if kind in ("days_since_last_cut_lte", "days_since_last_cut_gte",
                "days_since_last_hike_lte", "days_since_last_hike_gte",
                "days_since_last_rate_change_lte",
                "days_since_last_rate_change_gte"):
        try:
            n = int(value)
        except ValueError:
            return False
        field = "_".join(kind.split("_")[:-1])  # strip trailing _lte/_gte
        v = cycle.get(field)
        if v is None:
            return False
        return (v <= n) if kind.endswith("_lte") else (v >= n)

    # ---- Phase C: calendar (Gregorian) -----------------------------
    if kind == "month_in":
        try:
            wanted = {int(x.strip()) for x in value.split(",")}
        except ValueError:
            return False
        return cal.get("month") in wanted
    if kind == "day_of_week_in":
        try:
            wanted = {int(x.strip()) for x in value.split(",")}
        except ValueError:
            return False
        return cal.get("day_of_week") in wanted
    if kind == "last_n_trading_days_of_month":
        try:
            n = int(value)
        except ValueError:
            return False
        left = cal.get("trading_days_left_in_month")
        return left is not None and left <= n

    # ---- Phase C: calendar (Hijri) ---------------------------------
    if kind == "ramadan":
        # Boolean trigger; value is ignored.
        return bool(cal.get("in_ramadan"))
    if kind == "pre_ramadan_window":
        try:
            n = int(value)
        except ValueError:
            return False
        d = cal.get("days_to_ramadan_start")
        return d is not None and 0 <= d <= n
    if kind == "post_eid_window":
        try:
            n = int(value)
        except ValueError:
            return False
        d = cal.get("days_since_eid_ul_fitr")
        return d is not None and 0 <= d <= n
    if kind == "high_vol_islamic_month":
        # value can be a comma list of names: 'Safar,Zil-Qad,Zil-Hajj'.
        # Empty value matches any high-vol month.
        active = cal.get("in_high_vol_islamic_month")
        if active is None:
            return False
        if not value.strip():
            return True
        wanted = {x.strip().lower() for x in value.split(",")}
        return active.lower() in wanted

    # ---- Phase D: mutual-fund flow triggers ------------------------
    mf = facts.get("mf") or {}

    # Universe-level MF triggers compare aggregate values directly.
    MF_NUMERIC: dict[str, tuple[str, str]] = {
        "mf_universe_n_funds_increasing_gte":
            ("n_funds_increasing_universe", ">="),
        "mf_universe_n_top_accumulated_gte":
            ("n_top_accumulated",           ">="),
        "mf_universe_n_top_distributed_gte":
            ("n_top_distributed",           ">="),
        "mf_data_freshness_lte":
            ("data_freshness_days",         "<="),
        # Per-stock max/min aggregates: the trigger fires when AT LEAST
        # ONE stock in the briefing's MF book passes the threshold.
        "mf_accumulation_streak_gte":
            ("max_accumulation_streak",     ">="),
        "mf_distribution_streak_gte":
            ("max_distribution_streak",     ">="),
        "mf_n_funds_initiating_30d_gte":
            ("max_n_funds_initiating_30d",  ">="),
        "mf_n_funds_increasing_30d_gte":
            ("max_n_funds_increasing_30d",  ">="),
        "mf_holding_change_30d_pct_gte":
            ("max_holding_change_30d_pct",  ">="),
        "mf_holding_change_30d_pct_lte":
            ("min_holding_change_30d_pct",  "<="),
        "mf_holding_change_180d_pct_gte":
            ("max_holding_change_180d_pct", ">="),
        "mf_holding_change_180d_pct_lte":
            ("min_holding_change_180d_pct", "<="),
    }
    if kind in MF_NUMERIC:
        try:
            thr = float(value)
        except ValueError:
            return False
        key, op = MF_NUMERIC[kind]
        v = mf.get(key)
        if v is None:
            return False
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False
        # Stale-data veto: every MF trigger except the freshness gate
        # itself silently returns False when the latest report is more
        # than 90 days old. Otherwise we re-fire historical patterns
        # forever even when nothing fresh has arrived.
        # Threshold raised from 60 -> 90 on 2026-05-03 because the
        # end-to-end test showed Aug-Sep 2025 GAPs were caused by
        # legitimately-actionable MF signals being vetoed at exactly
        # the 60-day boundary while we wait for the next report.
        if kind != "mf_data_freshness_lte":
            freshness = mf.get("data_freshness_days")
            if freshness is not None and float(freshness) > 90.0:
                return False
        return ((op == "<=" and v <= thr) or (op == ">=" and v >= thr)
                or (op == "<"  and v <  thr) or (op == ">"  and v >  thr))

    # ---- Phase E: volume confirmation triggers ---------------------
    vol = facts.get("volume") or {}
    if kind == "volume_breakout_count_gte":
        try:
            thr = int(value)
        except ValueError:
            return False
        # Stale-data veto: only fire if OHLCV is reasonably fresh
        # (<= 5 trading days). Otherwise we'd fire forever on the
        # last cached breakout.
        freshness = vol.get("data_freshness_days")
        if freshness is None or float(freshness) > 5.0:
            return False
        return int(vol.get("n_confirmed_breakouts") or 0) >= thr
    if kind == "volume_data_freshness_lte":
        try:
            thr = float(value)
        except ValueError:
            return False
        v = vol.get("data_freshness_days")
        return v is not None and float(v) <= thr

    return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
_CONF_WEIGHT = {"HIGH": 1.5, "MEDIUM": 1.0, "LOW": 0.6}


def _recency_bonus(case: Case) -> float:
    """Prefer cases with a recent historical instance (within ~3y)."""
    if not case.historical_instances:
        return 0.0
    today = date.today()
    most_recent: date | None = None
    for inst in case.historical_instances:
        try:
            d = datetime.strptime(inst.date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if most_recent is None or d > most_recent:
            most_recent = d
    if most_recent is None:
        return 0.0
    days = (today - most_recent).days
    if days < 0:
        return 0.0
    if days <= 365:
        return 0.6
    if days <= 365 * 3:
        return 0.3
    return 0.1


def _score_case(case: Case, facts: dict) -> tuple[float, int, list[str]]:
    """Return (score, n_triggers_matched, fired_triggers).

    Score = #fired_triggers * confidence_weight + recency_bonus.
    """
    fired: list[str] = []
    for t in case.trigger_signals:
        if _eval_trigger(t, facts):
            fired.append(t)
    if len(fired) < case.required_triggers():
        return 0.0, len(fired), fired

    base = float(len(fired)) * _CONF_WEIGHT.get(case.confidence, 1.0)
    base += _recency_bonus(case)
    return base, len(fired), fired


def _serialise(case: Case, score: float, fired: list[str]) -> dict:
    """Compact JSON-friendly view of a case for the strategist briefing.

    We keep the playbook + what_breaks_it + confidence + research_basis,
    plus a maximum of 3 historical instances (most recent first), each
    with reactions truncated to ~6 symbols. The whole serialised case
    stays well under 2 KB so injecting 4-6 cases costs little context.
    """
    insts = sorted(
        case.historical_instances,
        key=lambda i: i.date,
        reverse=True,
    )[:3]
    inst_payload = []
    for inst in insts:
        rx = inst.reactions or {}
        # Trim to top 6 by absolute d21 (or d5) magnitude — these are
        # the names worth pointing Claude at.
        ranked = sorted(
            rx.items(),
            key=lambda kv: abs((kv[1] or {}).get("d21")
                                or (kv[1] or {}).get("d5") or 0.0),
            reverse=True,
        )[:6]
        inst_payload.append({
            "date": inst.date,
            "context": inst.context[:240],
            "reactions": dict(ranked),
            "source": inst.source[:160],
        })
    return {
        "id": case.id,
        "title": case.title,
        "category": case.category,
        "confidence": case.confidence,
        "match_score": round(score, 2),
        "fired_triggers": fired,
        "playbook": case.playbook,
        "what_breaks_it": case.what_breaks_it,
        "research_basis": case.research_basis,
        "tags": case.tags,
        "historical_instances": inst_payload,
    }


# ---------------------------------------------------------------------------
# Public retrieval
# ---------------------------------------------------------------------------
def retrieve_analogues(briefing: dict,
                        top_k: int | None = None,
                        min_score: float = 1.0,
                        cases: list[Case] | None = None) -> list[dict]:
    """Match the briefing against the case library, return top-K dicts.

    Parameters
    ----------
    briefing : the same payload ``brain.master_strategist.build_briefing``
        produces.
    top_k : how many cases to return. Defaults to 4 (overridable via
        env var ``PSX_PLAYBOOK_TOP_K``).
    min_score : minimum score threshold. Cases scoring below this
        are filtered out (avoids returning irrelevant cases just to
        fill the K slots).
    cases : pre-loaded list (optional — useful for tests). If None
        we read from disk.
    """
    if top_k is None:
        try:
            top_k = int(os.environ.get("PSX_PLAYBOOK_TOP_K", "4"))
        except ValueError:
            top_k = 4

    cases = cases if cases is not None else load_cases()
    if not cases:
        return []

    facts = _briefing_facts(briefing)
    scored: list[tuple[float, int, list[str], Case]] = []
    for c in cases:
        score, n_fired, fired = _score_case(c, facts)
        if score >= min_score and n_fired > 0:
            scored.append((score, n_fired, fired, c))

    # Sort: score desc, then n_fired desc, then HIGH-confidence first
    conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    scored.sort(key=lambda x: (-x[0], -x[1],
                               conf_order.get(x[3].confidence, 1)))

    out: list[dict] = []
    for score, _n, fired, c in scored[:top_k]:
        out.append(_serialise(c, score, fired))
    return out


# ---------------------------------------------------------------------------
# Live fact summary (handy for the UI / logs)
# ---------------------------------------------------------------------------
def summarise_facts(briefing: dict) -> dict:
    """Return the flat facts dict the matcher uses. Useful in the
    UI so the analyst can see *why* a particular case did/didn't
    fire ("oh, sentiment_lt:-0.4 didn't fire because tilt is -0.32")."""
    f = _briefing_facts(briefing)
    return {
        "drivers": [f"{t}:{m or '-'}" for (t, m) in f["drivers"]],
        "regime": f["regime"],
        "fipi_5d": f["fipi_5d"],
        "sentiment": f["sentiment"],
        "breadth": f["breadth"],
        "universe_5d": f["universe_5d"],
        "phase1_risk_on": f["phase1_risk_on"],
        "earnings_blackouts": f["earnings_blackouts"],
        "active_events": sorted(f["active_events"]),
        "macro_levels": f.get("macro_levels"),
        "valuation": f.get("valuation"),
        "cycle": f.get("cycle"),
        "calendar": f.get("calendar"),
    }


# ---------------------------------------------------------------------------
# CLI: validate / inspect
# ---------------------------------------------------------------------------
def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--validate", action="store_true",
                    help="Type-check cases.json and exit non-zero on errors.")
    ap.add_argument("--list", action="store_true",
                    help="Print every loaded case (id + title + triggers + confidence).")
    ap.add_argument("--match", type=str, default=None,
                    help=("Path to a JSON briefing file; print the analogues "
                          "the matcher would return for it."))
    args = ap.parse_args()

    if args.validate:
        n, errs = validate_file()
        if errs:
            print(f"FAIL: {len(errs)} error(s) across {n} cases")
            for e in errs:
                print(f"  - {e}")
            return 1
        print(f"OK: {n} case(s) validated")
        return 0

    if args.list:
        for c in load_cases():
            triggers = ", ".join(c.trigger_signals)
            req = c.required_triggers()
            print(f"  [{c.confidence:>6}]  {c.id:<36}  "
                  f"({req}/{len(c.trigger_signals)})  {triggers}")
            print(f"               {c.title}")
        return 0

    if args.match:
        briefing = json.loads(Path(args.match).read_text(encoding="utf-8"))
        analogues = retrieve_analogues(briefing)
        print(json.dumps(analogues, indent=2, default=str))
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
