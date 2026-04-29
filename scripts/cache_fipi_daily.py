"""Append today's FIPI flows to a growing parquet cache.

Run this daily (schedule with Windows Task Scheduler / cron after PSX close
~16:00 PKT). Over time the cache grows into a historical FIPI series that
we can add to the overnight-prior model.

Output: data/flows/fipi_daily.parquet

Columns:
  date              (YYYY-MM-DD, PSX calendar)
  foreign_net_pkr_mn
  local_net_pkr_mn
  foreign_regime    ("net_buying" / "net_selling")
  n_participants
  n_sectors
  top_sector_net_usd_mn  (largest abs sector flow, signed)
  top_sector_name

Usage:
    python scripts/cache_fipi_daily.py           # append today
    python scripts/cache_fipi_daily.py --dry-run # just print what would be written
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import pandas as pd

from connectors.flows import SCStradeFIPIConnector


OUT = ROOT / "data" / "flows" / "fipi_daily.parquet"


def _parse_report_date(raw: str | None) -> str | None:
    if not raw:
        return None
    for fmt in ("%d-%b-%Y", "%d/%b/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def snapshot_today() -> dict | None:
    res = SCStradeFIPIConnector().fetch()
    if not res.ok:
        print(f"FIPI fetch failed: {res.error}")
        return None
    extras = res.extras or {}
    sectors = extras.get("sectors") or []
    top_sector = None
    if sectors:
        top_sector = max(sectors, key=lambda s: abs(s.get("net_usd_mn", 0)))
    d = _parse_report_date(extras.get("report_date")) or date.today().isoformat()
    return {
        "date": d,
        "foreign_net_pkr_mn": extras.get("foreign_net_pkr_mn"),
        "local_net_pkr_mn": extras.get("local_net_pkr_mn"),
        "foreign_regime": ("net_buying"
                            if (extras.get("foreign_net_pkr_mn") or 0) > 0
                            else "net_selling"),
        "n_participants": len(res.records or []),
        "n_sectors": len(sectors),
        "top_sector_name": (top_sector or {}).get("sector"),
        "top_sector_net_usd_mn": (top_sector or {}).get("net_usd_mn"),
        "captured_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
    }


def append_row(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.DataFrame()
    if OUT.exists():
        existing = pd.read_parquet(OUT)
    updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    # Dedupe by date — last write wins (so re-runs overwrite today's snapshot)
    updated = updated.drop_duplicates(subset=["date"], keep="last")
    updated = updated.sort_values("date").reset_index(drop=True)
    updated.to_parquet(OUT, index=False)
    print(f"Cache: {len(updated)} rows   range "
          f"{updated['date'].iloc[0]} .. {updated['date'].iloc[-1]}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    row = snapshot_today()
    if not row:
        try:
            from scripts._health import write_status
            write_status(workflow="eod", ok=False,
                          note="FIPI snapshot returned no row",
                          payload={})
        except Exception:
            pass
        sys.exit(1)
    print(f"FIPI snapshot for {row['date']}:")
    for k, v in row.items():
        print(f"  {k:<24s} {v}")
    if args.dry_run:
        print("(dry-run) not writing.")
        return
    append_row(row)

    try:
        from scripts._health import write_status
        write_status(
            workflow="eod",
            ok=True,
            note=(f"FIPI snapshot for {row.get('date')}: "
                  f"foreign_net="
                  f"{row.get('foreign_net_pkr_mn', 0):+.1f} mn"),
            payload=row,
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
