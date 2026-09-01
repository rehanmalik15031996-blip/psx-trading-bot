"""Refresh the upcoming-dividend/bonus/rights calendar (azeetrade.com).

Was built (connectors/dividend_calendar.py) but never given a refresh
script or schedule -- data/dividends/upcoming.parquet never existed.
See connectors/dividend_calendar.py::_parse_azee for the 2026-09-01 parser
fix (pandas 3.0 StringIO break + stale column names).

Usage:
    python scripts/refresh_dividend_calendar.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from connectors.dividend_calendar import DividendCalendarConnector


def main() -> int:
    result = DividendCalendarConnector().fetch()
    print(f"[dividend_calendar] ok={result.ok} {result.summary}")
    if not result.ok:
        print(f"  error: {result.error}")
        print(f"  extras: {result.extras}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
