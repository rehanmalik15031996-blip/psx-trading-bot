"""Mid-session live PSX snapshot.

Closes the intraday data gap exposed by the April 30 audit: PSX
publishes a live MarketWatch table, a circuit-breaker list, and a
refreshable FIPI flows page during the 09:32-15:30 PKT trading
session, but the bot had no scheduled job pulling any of it. Mid-
session calls were stuck reading EOD parquets from yesterday until
the next 16:30 PKT EOD run.

This script is the glue: it calls the three existing connectors and
appends timestamped rows to ``data/intraday/<source>.parquet`` so
analysts (and the System Health tab) can see how the market moved
between predictions time and EOD.

Run twice per session via ``.github/workflows/intraday_session.yml``:
    11:30 PKT — first sweep (90 minutes after open)
    13:30 PKT — second sweep (90 minutes before close)

Output schema is intentionally simple — one row per snapshot per
symbol, with a single ``snapshot_at`` column the dashboard can sort
on. Re-running for the same minute is idempotent (dedup on
``snapshot_at + symbol``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OUT_DIR = ROOT / "data" / "intraday"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_parquet(path: Path, rows: list[dict],
                       dedupe_keys: list[str]) -> int:
    """Append rows to ``path``, dedupe on ``dedupe_keys``, return total
    row count after the merge.

    Idempotent on the dedupe key — two runs at the same minute
    overwrite the older row. Used so a manual re-dispatch of the
    workflow never duplicates a snapshot.
    """
    import pandas as pd

    df_new = pd.DataFrame(rows)
    if df_new.empty:
        return 0
    if path.exists():
        try:
            df_old = pd.read_parquet(path)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()

    df_all = (pd.concat([df_old, df_new], ignore_index=True)
                .drop_duplicates(subset=dedupe_keys, keep="last")
                .reset_index(drop=True))
    df_all.to_parquet(path, index=False)
    return int(len(df_all))


def refresh_market_watch() -> dict:
    """Pull the full PSX Market Watch table and append a timestamped
    row per symbol to ``data/intraday/marketwatch.parquet``.

    Each row carries ``snapshot_at`` (UTC ISO seconds), ``symbol``,
    ``open / high / low / current / change_pct / volume`` plus the
    sector code. The dashboard can compute high-of-day, low-of-day
    and turnover trajectory from this single file.
    """
    from connectors.psx_portal import PSXMarketWatchConnector

    fr = PSXMarketWatchConnector().fetch()
    if not fr.ok or not fr.records:
        return {"ok": False, "source": "marketwatch",
                 "error": fr.error or "no records"}
    snap = _now_iso()
    rows = []
    for r in fr.records:
        if not r.get("symbol"):
            continue
        rows.append({
            "snapshot_at": snap,
            "symbol":      r.get("symbol"),
            "sector_code": r.get("sector_code"),
            "sector_name": r.get("sector_name"),
            "ldcp":        r.get("ldcp"),
            "open":        r.get("open"),
            "high":        r.get("high"),
            "low":         r.get("low"),
            "current":     r.get("current"),
            "change_pct":  r.get("change_pct"),
            "volume":      r.get("volume"),
        })
    n = _append_parquet(OUT_DIR / "marketwatch.parquet", rows,
                          dedupe_keys=["snapshot_at", "symbol"])
    return {"ok": True, "source": "marketwatch",
             "snapshot_at": snap, "added": len(rows), "rows_total": n}


def refresh_circuit_breakers() -> dict:
    """Pull circuit-locked symbols and append timestamped rows to
    ``data/intraday/circuit_breakers.parquet``.

    A 100% circuit lock is a strong intraday momentum signal — the
    bot's strategist can use the table to see which names were
    upper-locked or lower-locked at the snapshot moment.
    """
    from connectors.psx_portal import PSXCircuitBreakersConnector

    fr = PSXCircuitBreakersConnector().fetch()
    if not fr.ok:
        return {"ok": False, "source": "circuit_breakers",
                 "error": fr.error or "fetch failed"}
    snap = _now_iso()
    rows = []
    for r in (fr.records or []):
        if not r.get("symbol"):
            continue
        rows.append({
            "snapshot_at": snap,
            "symbol":      r.get("symbol"),
            "direction":   r.get("direction"),
            "change_pct":  r.get("change_pct"),
            "volume":      r.get("volume"),
        })
    n = _append_parquet(OUT_DIR / "circuit_breakers.parquet", rows,
                          dedupe_keys=["snapshot_at", "symbol",
                                          "direction"])
    return {"ok": True, "source": "circuit_breakers",
             "snapshot_at": snap, "added": len(rows), "rows_total": n}


def refresh_fipi_intraday() -> dict:
    """Pull the SCStrade FIPI page mid-session and append a single
    aggregate row to ``data/intraday/fipi_intraday.parquet``.

    SCStrade publishes the FIPI report shortly after PSX close, but
    the page exposes the running aggregate during the session as
    well. The mid-session row is a directional signal — if foreign
    flow is already strongly negative by 11:30 PKT, that is itself a
    bearish-bias prior for the rest of the session.
    """
    from connectors.flows import SCStradeFIPIConnector

    fr = SCStradeFIPIConnector().fetch()
    if not fr.ok:
        return {"ok": False, "source": "fipi_intraday",
                 "error": fr.error or "fetch failed"}
    snap = _now_iso()
    extras = fr.extras or {}
    row = {
        "snapshot_at":     snap,
        "report_date":     extras.get("report_date"),
        "foreign_net_pkr_mn": extras.get("foreign_net_pkr_mn"),
        "local_net_pkr_mn":   extras.get("local_net_pkr_mn"),
        "n_categories":    len(fr.records or []),
        "n_sectors":       len(extras.get("sectors") or []),
    }
    n = _append_parquet(OUT_DIR / "fipi_intraday.parquet", [row],
                          dedupe_keys=["snapshot_at"])
    return {"ok": True, "source": "fipi_intraday",
             "snapshot_at": snap, "added": 1, "rows_total": n,
             **{k: row[k] for k in ("foreign_net_pkr_mn",
                                       "local_net_pkr_mn")}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip", choices=["marketwatch", "circuits", "fipi"],
        action="append", default=[],
        help="Skip a single source (debug aid).",
    )
    args = parser.parse_args()

    print(f"[refresh_live_market] start={_now_iso()}")
    results: list[dict] = []
    if "marketwatch" not in args.skip:
        results.append(refresh_market_watch())
    if "circuits" not in args.skip:
        results.append(refresh_circuit_breakers())
    if "fipi" not in args.skip:
        results.append(refresh_fipi_intraday())

    n_ok = sum(1 for r in results if r.get("ok"))
    for r in results:
        if r.get("ok"):
            print(f"  [{r['source']:<18}] added={r.get('added', 0):<4} "
                  f"total={r.get('rows_total', 0):<6} "
                  f"snapshot_at={r.get('snapshot_at')}")
        else:
            print(f"  [{r['source']:<18}] FAILED: {r.get('error')}")

    summary = {
        "ok":            n_ok == len(results),
        "sources_ok":    n_ok,
        "sources_total": len(results),
    }
    print(json.dumps(summary))

    # Per-workflow health status.
    try:
        from scripts._health import write_status
        write_status(
            workflow="intraday_session",
            ok=summary["ok"],
            note=(f"{n_ok}/{len(results)} sources refreshed"),
            payload={"sources": [r.get("source") for r in results],
                       "results": results},
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
