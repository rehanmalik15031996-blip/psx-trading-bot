"""Backfill entry_price_pkr / suggested_stop_pkr / suggested_target_pkr for
Cursor-generated predictions that have zero/null prices.

Pulls the most recent close from data/ohlcv/<SYM>.parquet and applies a simple,
deterministic stop/target rule based on the prediction's action + expected return.

Rules
-----
- entry_price_pkr = last close on or before as_of_price_date (fallback: latest bar)
- For BUY / ADD:
    stop   = entry * (1 - 0.025)            # 2.5% below entry
    target = entry * (1 + max(exp_high, 3))/100  -- use the high-end expected return
- For HOLD / WATCH:
    stop   = entry * (1 - 0.030)
    target = entry * (1 + max(exp_mid, 2))/100
- For AVOID / SELL / BEARISH:
    stop   = entry * (1 + 0.025)             # above entry (short stop)
    target = entry * (1 + min(exp_low, -3))/100

Also patches `data_snapshot.close_pkr` if it is zero.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, date

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
OHLCV_DIR = ROOT / "data" / "ohlcv"
PRED_LOG = ROOT / "data" / "predictions_log.json"

# ------------------------------------------------------------------------- helpers

def _last_close_on_or_before(sym: str, asof: str | None) -> tuple[float | None, str | None]:
    fp = OHLCV_DIR / f"{sym}.parquet"
    if not fp.exists():
        return None, None
    df = pd.read_parquet(fp)
    if df.empty or "close" not in df.columns:
        return None, None
    df = df.sort_values("date")
    if asof:
        cut = df[df["date"] <= pd.to_datetime(asof)]
        if not cut.empty:
            row = cut.iloc[-1]
            return float(row["close"]), str(row["date"].date())
    row = df.iloc[-1]
    return float(row["close"]), str(row["date"].date())


def _compute_stop_target(entry: float, action: str, exp_low: float | None,
                         exp_mid: float | None, exp_high: float | None) -> tuple[float | None, float | None]:
    if entry is None or entry <= 0:
        return None, None
    a = (action or "").upper()
    if a in ("BUY", "ADD"):
        stop = entry * (1 - 0.025)
        # take expected_high but at least +3 to avoid degenerate targets
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
    # round to 2 dp
    return round(stop, 2), round(target, 2)


# ------------------------------------------------------------------------- main

def main():
    log = json.loads(PRED_LOG.read_text(encoding="utf-8"))
    preds = log.get("predictions", [])
    patched = 0
    skipped_no_ohlcv: list[str] = []
    for r in preds:
        if not isinstance(r, dict):
            continue
        # Only patch rows that look broken (entry 0 or stop/target None)
        entry_now = r.get("entry_price_pkr")
        stop_now = r.get("suggested_stop_pkr")
        tgt_now = r.get("suggested_target_pkr")
        needs_patch = (
            entry_now in (None, 0, 0.0)
            or stop_now is None
            or tgt_now is None
        )
        if not needs_patch:
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

        r["entry_price_pkr"] = round(close, 2)
        r["suggested_stop_pkr"] = stop
        r["suggested_target_pkr"] = tgt
        # also fix the snapshot close if zero
        if snap.get("close_pkr") in (None, 0, 0.0):
            snap["close_pkr"] = round(close, 2)
            if not snap.get("as_of_price_date"):
                snap["as_of_price_date"] = used_date
            r["data_snapshot"] = snap
        # leave a breadcrumb for future debugging
        r["_price_backfilled_at"] = datetime.now().isoformat(timespec="seconds")
        r["_price_backfill_source_date"] = used_date

        patched += 1

    log["predictions"] = preds
    PRED_LOG.write_text(json.dumps(log, indent=2, default=str), encoding="utf-8")
    print(f"Patched {patched} predictions.")
    if skipped_no_ohlcv:
        print(f"Skipped (no OHLCV): {sorted(set(skipped_no_ohlcv))}")


if __name__ == "__main__":
    main()
