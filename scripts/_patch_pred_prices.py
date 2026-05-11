"""Backfill / sanity-check entry_price_pkr, suggested_stop_pkr, suggested_target_pkr
for prediction rows in data/predictions_log.json.

Why this exists
---------------
The main pipeline `generate_predictions.py` is safe (skips stocks with close <= 0).
But the manual writer `_write_cursor_predictions.py` and any future writers can
silently leave these fields zero/null if their input snapshot lacks `close_pkr`.
When that happens the Forecast tab renders "0 / None / None" and trading limits
are invisible to the user.

This script:
  1. Loads `data/predictions_log.json`
  2. For each row missing or zero on entry/stop/target, pulls the last bar from
     `data/ohlcv/<SYM>.parquet` on or before `data_snapshot.as_of_price_date`
  3. Applies a deterministic stop/target rule based on action + expected returns
  4. Also restores `data_snapshot.close_pkr` if zero
  5. Writes the log back atomically

Run modes
---------
  python scripts/_patch_pred_prices.py              # patch ALL rows that need it
  python scripts/_patch_pred_prices.py --latest     # only the most recent batch
  python scripts/_patch_pred_prices.py --check      # exit 1 if any row is broken
                                                    # (does not write)
  python scripts/_patch_pred_prices.py --dry-run    # show what would change

Importable
----------
  from scripts._patch_pred_prices import backfill_prices
  n = backfill_prices(latest_only=True)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
PRED_LOG = ROOT / "data" / "predictions_log.json"


# ----------------------------------------------------------- price + stop/target


def _last_close_on_or_before(
    sym: str, asof: Optional[str]
) -> tuple[Optional[float], Optional[str]]:
    """Last close on or before `asof` from data/ohlcv/<SYM>.parquet.

    Returns (price, used_date_iso) or (None, None) if no OHLCV available.
    """
    fp = OHLCV_DIR / f"{sym}.parquet"
    if not fp.exists():
        return None, None
    df = pd.read_parquet(fp)
    if df.empty or "close" not in df.columns:
        return None, None
    df = df.sort_values("date")
    if asof:
        try:
            cut = df[df["date"] <= pd.to_datetime(asof)]
            if not cut.empty:
                row = cut.iloc[-1]
                return float(row["close"]), str(row["date"].date())
        except Exception:
            pass
    row = df.iloc[-1]
    return float(row["close"]), str(row["date"].date())


def _compute_stop_target(
    entry: float,
    action: str,
    exp_low: Optional[float],
    exp_mid: Optional[float],
    exp_high: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Deterministic stop / target rule for a 5-day swing."""
    if entry is None or entry <= 0:
        return None, None
    a = (action or "").upper()
    if a in ("BUY", "ADD"):
        stop = entry * (1 - 0.025)
        upside_pct = max(exp_high if exp_high is not None else 3.0, 3.0)
        target = entry * (1 + upside_pct / 100.0)
    elif a in ("HOLD", "WATCH"):
        stop = entry * (1 - 0.030)
        upside_pct = max(exp_mid if exp_mid is not None else 2.0, 2.0)
        target = entry * (1 + upside_pct / 100.0)
    elif a in ("AVOID", "SELL", "TRIM"):
        stop = entry * (1 + 0.025)
        downside_pct = min(exp_low if exp_low is not None else -3.0, -3.0)
        target = entry * (1 + downside_pct / 100.0)
    else:
        stop = entry * (1 - 0.030)
        target = entry * (1 + 0.020)
    return round(stop, 2), round(target, 2)


# ----------------------------------------------------------- row inspection


def _needs_patch(r: dict) -> bool:
    if not isinstance(r, dict):
        return False
    entry = r.get("entry_price_pkr")
    stop = r.get("suggested_stop_pkr")
    tgt = r.get("suggested_target_pkr")
    return (
        entry in (None, 0, 0.0)
        or stop is None
        or tgt is None
    )


def _is_latest_batch(r: dict, latest_date: str) -> bool:
    snap = r.get("data_snapshot") or {}
    asof = snap.get("as_of_price_date") or r.get("as_of_date")
    return asof == latest_date


def _find_latest_asof(preds: list[dict]) -> Optional[str]:
    dates: list[str] = []
    for r in preds:
        if not isinstance(r, dict):
            continue
        snap = r.get("data_snapshot") or {}
        asof = snap.get("as_of_price_date") or r.get("as_of_date")
        if asof:
            dates.append(str(asof))
    if not dates:
        return None
    return max(dates)


# ----------------------------------------------------------- main api


def backfill_prices(
    *,
    latest_only: bool = False,
    dry_run: bool = False,
    log_path: pathlib.Path = PRED_LOG,
    verbose: bool = True,
) -> dict:
    """Backfill broken prediction rows. Returns a summary dict."""
    log = json.loads(log_path.read_text(encoding="utf-8"))
    preds = log.get("predictions", [])

    latest_asof = _find_latest_asof(preds) if latest_only else None
    if latest_only and verbose:
        print(f"[patcher] latest_only=True, latest_asof={latest_asof}")

    patched: list[str] = []
    skipped_no_ohlcv: list[str] = []
    skipped_ok: int = 0
    skipped_out_of_scope: int = 0

    for r in preds:
        if not isinstance(r, dict):
            continue
        if latest_only and not _is_latest_batch(r, latest_asof or ""):
            skipped_out_of_scope += 1
            continue
        if not _needs_patch(r):
            skipped_ok += 1
            continue

        sym = r.get("symbol")
        if not sym:
            continue
        snap = r.get("data_snapshot") or {}
        asof = snap.get("as_of_price_date") or r.get("as_of_date")
        close, used_date = _last_close_on_or_before(sym, asof)
        if close is None:
            skipped_no_ohlcv.append(sym)
            continue

        action = r.get("suggested_action") or ""
        ex_low = r.get("expected_return_5d_low_pct")
        ex_mid = r.get("expected_return_5d_mid_pct")
        ex_high = r.get("expected_return_5d_high_pct")
        stop, tgt = _compute_stop_target(close, action, ex_low, ex_mid, ex_high)

        if dry_run:
            if verbose:
                print(
                    f"[dry-run] {sym}: entry={close} stop={stop} tgt={tgt} "
                    f"(was entry={r.get('entry_price_pkr')} "
                    f"stop={r.get('suggested_stop_pkr')} tgt={r.get('suggested_target_pkr')})"
                )
            patched.append(sym)
            continue

        r["entry_price_pkr"] = round(close, 2)
        r["suggested_stop_pkr"] = stop
        r["suggested_target_pkr"] = tgt
        if snap.get("close_pkr") in (None, 0, 0.0):
            snap["close_pkr"] = round(close, 2)
            if not snap.get("as_of_price_date"):
                snap["as_of_price_date"] = used_date
            r["data_snapshot"] = snap
        r["_price_backfilled_at"] = datetime.now().isoformat(timespec="seconds")
        r["_price_backfill_source_date"] = used_date

        patched.append(sym)

    if patched and not dry_run:
        log["predictions"] = preds
        tmp = log_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
        tmp.replace(log_path)

    summary = {
        "patched": patched,
        "patched_count": len(patched),
        "skipped_no_ohlcv": sorted(set(skipped_no_ohlcv)),
        "skipped_ok": skipped_ok,
        "skipped_out_of_scope": skipped_out_of_scope,
        "latest_asof": latest_asof,
        "dry_run": dry_run,
    }
    if verbose:
        print(
            f"[patcher] patched={len(patched)} ok={skipped_ok} "
            f"out_of_scope={skipped_out_of_scope} "
            f"missing_ohlcv={len(set(skipped_no_ohlcv))} dry_run={dry_run}"
        )
        if skipped_no_ohlcv:
            print(f"[patcher] missing OHLCV for: {sorted(set(skipped_no_ohlcv))}")
    return summary


def check_only(log_path: pathlib.Path = PRED_LOG, latest_only: bool = True) -> int:
    """Return non-zero exit if any row in scope is broken. Used as a CI guard."""
    log = json.loads(log_path.read_text(encoding="utf-8"))
    preds = log.get("predictions", [])
    latest = _find_latest_asof(preds) if latest_only else None
    broken: list[str] = []
    for r in preds:
        if not isinstance(r, dict):
            continue
        if latest_only and not _is_latest_batch(r, latest or ""):
            continue
        if _needs_patch(r):
            broken.append(r.get("symbol") or "?")
    if broken:
        print(
            f"[patcher] CHECK FAILED: {len(broken)} broken rows in latest batch "
            f"({latest}): {broken}"
        )
        return 1
    print(f"[patcher] CHECK PASSED: latest batch ({latest}) clean")
    return 0


# ----------------------------------------------------------- CLI


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--latest",
        action="store_true",
        help="Only patch rows from the latest as_of_price_date.",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="Read-only check: exit 1 if any latest-batch row is broken.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Show what would change.")
    args = ap.parse_args(argv)

    if args.check:
        return check_only(latest_only=True)

    summary = backfill_prices(
        latest_only=args.latest, dry_run=args.dry_run, verbose=True
    )
    return 0 if summary["patched_count"] >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
