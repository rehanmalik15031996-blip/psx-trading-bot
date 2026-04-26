"""Predicted earnings / corporate-action calendar for the PSX universe.

Strategy
========

PSX listed companies announce results at board meetings; an associated
dividend is usually declared at the same meeting. Yahoo Finance only has
a confirmed ``earningsTimestamp`` for a tiny minority of PSX names, so
we use a hybrid:

1. **yfinance ``next_earnings_date_utc``** when available. *Confidence: HIGH.*
2. **Dividend-cadence prediction** otherwise. We look at the spacing of
   the latest 4-6 dividend dates (already cached). The median spacing is
   our reporting cadence — typically ~90 days for quarterly cement / E&P,
   ~180 days for half-yearly banks. *Confidence: MEDIUM.*
3. **Sector-typical fallback** when neither works. PSX results follow
   roughly:
     * Banks (Dec year-end) — Feb / May / Aug / Oct
     * Cement, E&P, Power, OMC (Jun year-end) — Aug / Oct / Feb / May
   *Confidence: LOW.*

Output schema
=============

Per-symbol record::

    {"symbol": "HUBC",
     "next_event_date_utc": "2026-05-15",
     "days_until": 18,
     "confidence": "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN",
     "source": "yfinance" | "cadence" | "sector_typical" | "none",
     "in_blackout_5d": False,
     "warnings": [...]}

Trading rules
-------------
* ``days_until <= 5`` AND ``confidence in {HIGH, MEDIUM}`` → blackout: do
  not initiate new BUY/ADD positions; existing positions can stay.
* ``days_until <= 14`` → flag in briefing so Claude / chatbot mention it.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone, timedelta
from typing import Optional

from config.universe import sector_of, symbols as universe_symbols
from connectors.yfinance_fundamentals import load_latest, load_universe


SECTOR_TYPICAL_GAPS_DAYS = {
    # Months between successive results announcements (median).
    "Banking":              90,    # Dec FY → Q1, half-year, Q3, annual
    "Cement":               90,    # Jun FY → quarterly cadence
    "Oil & Gas E&P":        90,
    "OMC/Refining":         90,
    "Power":                180,
    "Pharma":               90,
    "Conglomerate/Chem":    90,
    "Misc":                 90,
}


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD' or full ISO into an aware UTC datetime."""
    if not s:
        return None
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s + "T00:00:00+00:00")
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _median_gap_days(dates_iso: list[str]) -> Optional[int]:
    """Median spacing in days between consecutive (sorted) dividend dates."""
    if not dates_iso or len(dates_iso) < 3:
        return None
    parsed = [d for d in (_parse_iso(s) for s in dates_iso) if d is not None]
    if len(parsed) < 3:
        return None
    parsed.sort(reverse=True)  # newest first
    gaps = [(parsed[i] - parsed[i + 1]).days
             for i in range(len(parsed) - 1)]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return None
    return int(round(statistics.median(gaps)))


def next_event(symbol: str, fb: Optional[dict] = None) -> dict:
    """Predicted next earnings / dividend-meeting date for one ticker."""
    fb = fb or load_latest(symbol)
    sec = sector_of(symbol)
    if not fb:
        return {"symbol": symbol, "sector": sec,
                "next_event_date_utc": None, "days_until": None,
                "confidence": "UNKNOWN", "source": "none",
                "in_blackout_5d": False, "in_window_14d": False,
                "warnings": ["no fundamentals cached"]}

    now = datetime.now(timezone.utc)
    warnings: list[str] = []

    # 1) Confirmed yfinance date — but only if it's in the future or last 7d
    confirmed = _parse_iso(fb.get("next_earnings_date_utc") or "")
    if confirmed:
        delta = (confirmed - now).days
        if delta >= -7:
            return _shape(symbol, sec, confirmed,
                           confidence="HIGH", source="yfinance",
                           warnings=warnings)
        # else stale — fall through

    # 2) Dividend-cadence prediction
    last_divs = fb.get("last_dividend_dates") or []
    gap = _median_gap_days(last_divs)
    if gap and gap >= 60 and last_divs:
        last = _parse_iso(last_divs[0])
        if last:
            # Project forward in `gap`-day strides until we land in the future
            projected = last + timedelta(days=gap)
            while (projected - now).days < -3:
                projected += timedelta(days=gap)
            return _shape(symbol, sec, projected,
                           confidence="MEDIUM", source="cadence",
                           warnings=warnings,
                           extra_meta={"cadence_days": gap,
                                       "anchor": last_divs[0]})

    # 3) Sector-typical fallback — coarse guess: assume gap days from the
    #    last known dividend (or, if none, from 90 days ago) and round.
    typical = SECTOR_TYPICAL_GAPS_DAYS.get(sec or "", 90)
    last = _parse_iso(last_divs[0]) if last_divs else None
    if last:
        projected = last + timedelta(days=typical)
        while (projected - now).days < -3:
            projected += timedelta(days=typical)
        return _shape(symbol, sec, projected,
                       confidence="LOW", source="sector_typical",
                       warnings=warnings + ["dividend-cadence too sparse"],
                       extra_meta={"sector_typical_days": typical})

    return {"symbol": symbol, "sector": sec,
            "next_event_date_utc": None, "days_until": None,
            "confidence": "UNKNOWN", "source": "none",
            "in_blackout_5d": False, "in_window_14d": False,
            "warnings": warnings + ["no usable dividend history"]}


def _shape(symbol: str, sec: str | None, when: datetime,
           confidence: str, source: str,
           warnings: list[str],
           extra_meta: dict | None = None) -> dict:
    now = datetime.now(timezone.utc)
    days_until = (when - now).days
    return {
        "symbol": symbol,
        "sector": sec,
        "next_event_date_utc": when.date().isoformat(),
        "days_until": days_until,
        "confidence": confidence,
        "source": source,
        "in_blackout_5d": (0 <= days_until <= 5
                            and confidence in ("HIGH", "MEDIUM")),
        "in_window_14d": 0 <= days_until <= 14,
        "warnings": warnings,
        **(extra_meta or {}),
    }


def universe_calendar(days_ahead: int = 21) -> dict:
    """Return all upcoming events within ``days_ahead``, sorted by date."""
    books = load_universe()
    rows = [next_event(s, fb=books.get(s)) for s in universe_symbols()]

    upcoming = [r for r in rows
                if r.get("days_until") is not None
                and -3 <= r["days_until"] <= days_ahead]
    upcoming.sort(key=lambda r: r["days_until"])

    return {
        "n_symbols": len(rows),
        "n_upcoming": len(upcoming),
        "blackout_now": [r for r in upcoming if r.get("in_blackout_5d")],
        "upcoming": upcoming,
        "all_rows": rows,
    }


if __name__ == "__main__":  # pragma: no cover
    cal = universe_calendar(days_ahead=30)
    print(f"upcoming in next 30d: {cal['n_upcoming']}  "
          f"blackout-now: {len(cal['blackout_now'])}")
    print(f"{'Sym':<6} {'Date':<12} {'Days':>5} {'Conf':<7} {'Source':<16} BlackOut?")
    for r in cal["upcoming"]:
        print(f"  {r['symbol']:<6} {r['next_event_date_utc']:<12} "
              f"{r['days_until']:>5}  {r['confidence']:<7} "
              f"{r['source']:<16} {r['in_blackout_5d']}")
    if cal['n_upcoming'] == 0:
        print("\nFull calendar (all symbols):")
        for r in cal['all_rows']:
            print(f"  {r['symbol']:<6} {r.get('next_event_date_utc') or '-':<12} "
                  f"days={r.get('days_until')} conf={r['confidence']} "
                  f"src={r['source']}")
