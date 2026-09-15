"""Refresh Google Trends PSX/KSE-100 retail search-interest signal.

New 2026-09-01 -- see connectors/google_trends.py for the reliability
caveat (pytrends is unofficial, rate-limits are expected occasionally,
not a regression to chase).

Usage:
    python scripts/refresh_google_trends.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import pandas as pd

from connectors.google_trends import GoogleTrendsConnector

OUT_PATH = PROJECT_ROOT / "data" / "sentiment" / "google_trends.parquet"


def main() -> int:
    result = GoogleTrendsConnector().fetch()
    if not result.ok:
        print(f"[google_trends] FAILED (expected occasionally -- pytrends "
              f"rate-limiting): {result.error}")
        return 0  # don't fail the workflow over an unofficial, best-effort source
    new_df = pd.DataFrame(result.records)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.DataFrame()
    if OUT_PATH.exists():
        try:
            existing = pd.read_parquet(OUT_PATH)
        except Exception as e:
            print(f"  WARN: {OUT_PATH} unreadable ({type(e).__name__}: {e}) "
                  f"-- starting a fresh cache instead of crashing.")
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    from data.store import atomic_to_parquet
    atomic_to_parquet(combined, OUT_PATH, index=False)
    print(f"[google_trends] ok -- {result.summary}, cache now {len(combined)} rows")
    for rec in result.records:
        print(f"  {rec['keyword']}: latest={rec['latest_value']} "
              f"7d_mean={rec['trailing_7d_mean']} spike_ratio={rec['spike_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
