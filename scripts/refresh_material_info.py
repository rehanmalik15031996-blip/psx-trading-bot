"""Refresh the Material Information cache from PSX Data Portal.

PSX-listed companies file Material Information when something happens
that could move the price between scheduled disclosures — board-room
events, big contracts, plant trips, regulatory action. The presence of
fresh Material Information typically precedes a 3-7% gap, which is
exactly the kind of signal a 5-day predictor needs to widen its band
and downgrade conviction on neutral calls.

This script walks the universe, pulls every MATERIAL-type announcement
from the PSX company page, and upserts them into
``data/material_information.parquet`` keyed on ``doc_id``. It is
idempotent — running it twice is a no-op.

Run::

    python scripts/refresh_material_info.py
    python scripts/refresh_material_info.py --since 2026-04-01
    python scripts/refresh_material_info.py --symbols HUBC OGDC
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


from connectors.psx_results import PSXResultsConnector  # noqa: E402

OUT_DIR = ROOT / "data"
OUT_PATH = OUT_DIR / "material_information.parquet"


def refresh(symbols: list[str] | None = None,
             since: str | None = None) -> dict:
    """Pull MATERIAL announcements and upsert into the parquet cache."""
    import pandas as pd

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = PSXResultsConnector()
    fresh = conn.fetch_material_information(symbols=symbols, since=since)

    if not fresh:
        return {
            "ok": True,
            "new_records": 0,
            "total_in_store": _existing_size(),
            "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    df_new = pd.DataFrame(fresh)
    # Add a refresh-time column for staleness tracking
    df_new["scraped_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if OUT_PATH.exists():
        try:
            df_old = pd.read_parquet(OUT_PATH)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()

    if not df_old.empty:
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_all = df_new
    df_all = (df_all
              .sort_values("scraped_at_utc")
              .drop_duplicates(subset=["symbol", "doc_id"], keep="last")
              .sort_values(["date", "symbol"], ascending=[False, True])
              .reset_index(drop=True))
    df_all.to_parquet(OUT_PATH, index=False)

    new_records = len(df_all) - len(df_old)
    return {
        "ok": True,
        "new_records": int(max(0, new_records)),
        "total_in_store": int(len(df_all)),
        "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _existing_size() -> int:
    if not OUT_PATH.exists():
        return 0
    try:
        import pandas as pd
        return int(len(pd.read_parquet(OUT_PATH)))
    except Exception:
        return 0


def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Refresh PSX Material Information cache.")
    p.add_argument("--since", default=None,
                    help="ISO date (YYYY-MM-DD) — only keep filings on or "
                         "after this date.")
    p.add_argument("--symbols", nargs="*", default=None,
                    help="Limit to these symbols (default: full universe).")
    args = p.parse_args()

    res = refresh(symbols=args.symbols, since=args.since)
    print(f"Material Information refresh: "
          f"new={res['new_records']}  total={res['total_in_store']}  "
          f"as_of={res['as_of_utc']}")

    try:
        from scripts._health import write_status
        write_status(
            workflow="material_info",
            ok=bool(res.get("ok")),
            note=(f"new={res.get('new_records', 0)} "
                  f"total={res.get('total_in_store', 0)}"),
            payload=res,
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")

    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
