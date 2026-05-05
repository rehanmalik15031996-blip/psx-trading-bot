"""Per-workflow data freshness SLAs.

Single source of truth for "how old is too old?" — consumed by both
``scripts/health_check.py`` (the daily build-failure gate that emails
the repo owner) and ``ui/system_health.py`` (the dashboard panel that
shows green / amber / red badges per source).

Each entry maps a workflow key (matching the filename written to
``data/_health/<workflow>.json`` by ``scripts/_health.write_status``)
to a tuple:

    (max_age_seconds_amber, max_age_seconds_red, weekday_only, note)

where:
    * ``max_age_seconds_amber`` — beyond this the badge turns amber.
    * ``max_age_seconds_red``   — beyond this the badge turns red AND
                                    ``health_check.py`` exits non-zero
                                    (failing the build and triggering
                                    GitHub's automatic failure email).
    * ``weekday_only`` — if True the SLA is only enforced on weekdays;
                          weekends are always green (PSX is closed).
    * ``note`` — one-line description of when the workflow runs.

Numbers are PKT-based but stored in UTC seconds. 26 hours covers a
single missed run (weekday → weekend handover or a one-day glitch);
beyond that we want a real alert.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sla:
    workflow: str
    amber_seconds: int
    red_seconds: int
    weekday_only: bool
    note: str
    intraday_only: bool = False


HOUR = 3600
DAY = 24 * HOUR


SLAS: list[Sla] = [
    Sla("macro_series",  26 * HOUR,  48 * HOUR, True,
         "Brent / WTI / gold / copper / cotton / BTC / USD-PKR — "
         "weekdays 06:55 PKT pre-open."),
    Sla("macro_kpis",    26 * HOUR,  48 * HOUR, True,
         "SBP rates / KIBOR / KSE-100 / CPI — weekdays 08:30 PKT "
         "and 17:00 PKT."),
    Sla("overnight",     26 * HOUR,  48 * HOUR, True,
         "S&P / Nikkei / FTSE / VIX / DXY — weekdays 09:00 PKT."),
    Sla("news_scoring",   7 * HOUR,  12 * HOUR, False,
         "Claude-scored news + shock check — weekdays 07:00 / "
         "13:00 / 18:00 PKT."),
    # Red threshold is 80h (not 48h) for these three daily weekday jobs.
    # The Mon-morning health check runs at ~08:40 PKT and sees the last
    # successful run from Friday (09:20 / 16:30 PKT), which is ~64-72h
    # old — safely under 80h. Anything over 80h means at least one full
    # weekday was skipped, which is a genuine alert.
    Sla("predictions",   26 * HOUR,  80 * HOUR, True,
         "5-day stock predictions — weekdays 09:20 PKT."),
    Sla("eod",           26 * HOUR,  80 * HOUR, True,
         "OHLCV + final FIPI + scorecard — weekdays 16:30 PKT."),
    Sla("intraday_session", 4 * HOUR, 6 * HOUR, True,
         "Live MarketWatch + circuit breakers + FIPI proxy — "
         "weekdays 11:30 PKT and 13:30 PKT.",
         intraday_only=True),
    Sla("material_info", 26 * HOUR,  80 * HOUR, True,
         "PSX corporate notices — weekdays 17:30 PKT."),
    Sla("fundamentals",   8 * DAY,   14 * DAY,  False,
         "yfinance fundamentals — Sundays 07:00 PKT."),
    Sla("financial_results", 8 * DAY, 14 * DAY, False,
         "Director's reports + filings — Saturdays 11:00 PKT + "
         "earnings-trigger event."),
]


def by_key() -> dict[str, Sla]:
    return {s.workflow: s for s in SLAS}
