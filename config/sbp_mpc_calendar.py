"""SBP Monetary Policy Committee meeting calendar — published in
advance by the State Bank of Pakistan.

Why this lives in code (not a connector)
---------------------------------------
The SBP publishes its MPC schedule once per fiscal year on the
``sbp.org.pk`` press-release page. The format is HTML and is not
worth a connector — six dates a year that we can hand-maintain.
What matters is that the bot has these dates **before** each meeting
so it can soft-cap conviction on rate-sensitive sectors during the
72-hour window leading into a decision.

Failure mode this protects against
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
On 28 April 2026 the bot had a +9 BUY on a banking name (rate-sensitive)
the night before an SBP meeting. The MPC announced a surprise hike at
11:45 PKT mid-session and the position lost 4% intraday. With this
calendar, the bot would have flagged the meeting in the briefing,
capped MEBL/FABL conviction to LOW for the day, and told the analyst
to wait for the post-MPC re-prediction triggered by the news-shock
detector.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

# SBP MPC meetings — official dates from sbp.org.pk press releases.
# Hand-maintained: the schedule is published once per fiscal year.
# Dates are PKT calendar dates (announcement is typically 16:30 PKT).
MPC_MEETINGS: list[date] = [
    date(2026, 1, 27),
    date(2026, 3, 10),
    date(2026, 4, 28),
    date(2026, 6, 16),
    date(2026, 7, 30),
    date(2026, 9, 15),
    date(2026, 11, 3),
    date(2026, 12, 22),
]

# How many calendar days before / after an MPC meeting count as the
# "alert window" (72h pre, 24h post).
PRE_WINDOW_DAYS  = 3
POST_WINDOW_DAYS = 1


def next_mpc(today: date | None = None) -> date | None:
    """Return the next MPC date >= today, or None if none scheduled."""
    today = today or datetime.now(timezone.utc).date()
    upcoming = [d for d in MPC_MEETINGS if d >= today]
    return min(upcoming) if upcoming else None


def days_until_next_mpc(today: date | None = None) -> int | None:
    n = next_mpc(today)
    if n is None:
        return None
    today = today or datetime.now(timezone.utc).date()
    return (n - today).days


def is_in_pre_window(today: date | None = None) -> bool:
    """True iff today is within ``PRE_WINDOW_DAYS`` of the next MPC."""
    d = days_until_next_mpc(today)
    return d is not None and 0 <= d <= PRE_WINDOW_DAYS


def is_in_post_window(today: date | None = None) -> bool:
    """True iff today is within ``POST_WINDOW_DAYS`` *after* an MPC."""
    today = today or datetime.now(timezone.utc).date()
    for d in MPC_MEETINGS:
        diff = (today - d).days
        if 0 < diff <= POST_WINDOW_DAYS:
            return True
    return False


def mpc_alert_state(today: date | None = None) -> dict:
    """Single dict the macro engine, predictions pipeline, and UI all
    consume.

    Returns
    -------
    {
       "in_pre_window":  bool,
       "in_post_window": bool,
       "next_mpc":       "YYYY-MM-DD" | None,
       "days_until":     int | None,
       "rate_sensitive_sectors": [list of sector names],
       "label":          short banner string for UI,
    }
    """
    today = today or datetime.now(timezone.utc).date()
    nxt = next_mpc(today)
    days = days_until_next_mpc(today)
    pre = is_in_pre_window(today)
    post = is_in_post_window(today)

    # Sectors whose stock prices are most sensitive to a policy-rate
    # surprise on PSX. Banking is positive-leveraged to higher rates;
    # cement / power IPPs / textile / autos are negative-leveraged.
    rate_sensitive = [
        "Banking", "Cement", "Power", "Auto", "Textile",
        "Conglomerate/Chem",
    ]

    if pre:
        label = (f"SBP MPC in {days} day(s) — rate-sensitive sectors "
                 f"on conviction cap")
    elif post:
        label = "SBP MPC announced yesterday — re-pricing in progress"
    else:
        label = ""

    return {
        "in_pre_window":  pre,
        "in_post_window": post,
        "next_mpc":       nxt.isoformat() if nxt else None,
        "days_until":     days,
        "rate_sensitive_sectors": rate_sensitive,
        "label":          label,
    }
