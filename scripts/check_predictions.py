"""Daily scorecard for predictions.

For every prediction in data/predictions_log.json:
  * Compute the actual N-trading-day forward return from the stored entry
    price, using the latest OHLCV in data/ohlcv/<SYM>.parquet.
  * Fill in the outcome block (actual_end_price, actual_return_pct,
    direction_hit, inside_range, stop/target triggered) once the horizon
    has elapsed.
  * Print a per-ticker scorecard + overall hit rate broken down by
    conviction.

Run once a day (ideally after market close):
  python scripts/check_predictions.py
  python scripts/check_predictions.py --force   # re-check everything
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ROOT / ".env")

import pandas as pd

from config.costs import net_return_pct, round_trip_cost_pct
from data.store import load_ohlcv

LOG_PATH = ROOT / "data" / "predictions_log.json"
RT_COST = round_trip_cost_pct()


def _return(entry: float, end: float) -> float:
    if entry <= 0:
        return 0.0
    return round((end / entry - 1) * 100, 2)


def score_one(pred: dict, force: bool = False) -> dict:
    """Fill in the outcome block for a single prediction (if possible)."""
    if pred.get("outcome", {}).get("actual_return_pct") is not None and not force:
        return pred

    sym = pred["symbol"]
    horizon = int(pred.get("horizon_trading_days", 5))
    entry = float(pred.get("entry_price_pkr") or 0)
    stop = pred.get("suggested_stop_pkr")
    target = pred.get("suggested_target_pkr")
    as_of = pred.get("data_snapshot", {}).get("as_of_price_date")
    direction = pred.get("direction")
    low = pred.get("expected_return_5d_low_pct")
    high = pred.get("expected_return_5d_high_pct")

    df = load_ohlcv(sym)
    if df.empty or not as_of or entry <= 0:
        return pred

    df = df.sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    start_date = pd.to_datetime(as_of)

    # Find the bar index of the prediction date
    idx = df.index[df["date"] == start_date]
    if len(idx) == 0:
        # fallback: nearest >= date
        idx = df.index[df["date"] >= start_date]
        if len(idx) == 0:
            return pred
    i0 = int(idx[0])
    # Forward horizon bars
    i1 = min(i0 + horizon, len(df) - 1)
    if i1 <= i0:
        return pred  # horizon not reached yet

    fwd = df.iloc[i0 + 1:i1 + 1]
    bars_elapsed = len(fwd)
    end_row = fwd.iloc[-1]
    end_px = float(end_row["close"])
    actual = _return(entry, end_px)

    # Stop/target intraday check (using daily high/low if available)
    hi_col = "high" if "high" in fwd.columns else "close"
    lo_col = "low" if "low" in fwd.columns else "close"
    stop_hit = bool((fwd[lo_col].astype(float) <= float(stop)).any()) if stop else None
    target_hit = bool((fwd[hi_col].astype(float) >= float(target)).any()) if target else None

    # Direction hit logic
    if direction == "BULLISH":
        dir_hit = actual > 0
    elif direction == "BEARISH":
        dir_hit = actual < 0
    else:
        # NEUTRAL = call is correct if the move stayed within ±2% (tight)
        dir_hit = abs(actual) <= 2.0

    inside = (low is not None and high is not None
              and low <= actual <= high)

    actual_net = net_return_pct(actual)
    # Would this trade have been worth taking AFTER costs?
    dir_hit_net = (actual_net > 0 if direction == "BULLISH"
                    else actual_net < 0 if direction == "BEARISH"
                    else abs(actual_net) <= 2.0)

    pred["outcome"] = {
        "checked_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "bars_elapsed": bars_elapsed,
        "end_date": str(end_row["date"].date()),
        "actual_end_price_pkr": round(end_px, 2),
        "actual_return_pct": actual,
        "actual_return_net_pct": actual_net,
        "round_trip_cost_pct": RT_COST,
        "direction_hit": bool(dir_hit),
        "direction_hit_net": bool(dir_hit_net),
        "inside_range": bool(inside),
        "stop_triggered": stop_hit,
        "target_triggered": target_hit,
    }
    return pred


# ==========================================================================
# Scorecard reporting
# ==========================================================================
def print_scorecard(preds: list[dict]):
    settled = [p for p in preds if p.get("outcome", {}).get("actual_return_pct")
               is not None]
    pending = [p for p in preds if p.get("outcome", {}).get("actual_return_pct")
               is None]

    print("\n" + "=" * 78)
    print(f"PREDICTION SCORECARD  (checked {datetime.now().date()})")
    print("=" * 78)
    print(f"Total predictions: {len(preds)}   "
          f"settled: {len(settled)}   pending: {len(pending)}")

    if settled:
        n = len(settled)
        dir_hits = sum(1 for p in settled if p["outcome"].get("direction_hit"))
        dir_hits_net = sum(1 for p in settled if p["outcome"].get("direction_hit_net"))
        in_range = sum(1 for p in settled if p["outcome"].get("inside_range"))
        avg_ret = sum(p["outcome"]["actual_return_pct"] for p in settled) / n
        avg_ret_net = sum(p["outcome"].get("actual_return_net_pct", 0)
                          for p in settled) / n
        print(f"\nDirection hit rate (gross):  {dir_hits}/{n} = {dir_hits/n*100:.1f}%")
        print(f"Direction hit rate (net):    {dir_hits_net}/{n} = "
              f"{dir_hits_net/n*100:.1f}%   "
              f"(after round-trip cost {RT_COST:.2f}% + CGT)")
        print(f"Inside expected range:       {in_range}/{n} = {in_range/n*100:.1f}%")
        print(f"Average realised 5d return:  gross={avg_ret:+.2f}%  "
              f"net={avg_ret_net:+.2f}%")

        # By conviction
        by_conv: dict[str, list[dict]] = {}
        for p in settled:
            by_conv.setdefault(p.get("conviction", "?"), []).append(p)
        print("\nBy conviction:")
        for conv in ("HIGH", "MEDIUM", "LOW"):
            rows = by_conv.get(conv, [])
            if not rows:
                continue
            h = sum(1 for p in rows if p["outcome"].get("direction_hit"))
            a = sum(p["outcome"]["actual_return_pct"] for p in rows) / len(rows)
            print(f"  {conv:<6s} n={len(rows):<3d}  hit={h}/{len(rows)} "
                  f"({h/len(rows)*100:.0f}%)  avg_ret={a:+.2f}%")

        # By symbol
        by_sym: dict[str, list[dict]] = {}
        for p in settled:
            by_sym.setdefault(p["symbol"], []).append(p)
        print("\nBy symbol:")
        for sym in sorted(by_sym):
            rows = by_sym[sym]
            h = sum(1 for p in rows if p["outcome"].get("direction_hit"))
            a = sum(p["outcome"]["actual_return_pct"] for p in rows) / len(rows)
            print(f"  {sym:<6s} n={len(rows):<3d}  hit={h}/{len(rows)} "
                  f"({h/len(rows)*100:.0f}%)  avg_ret={a:+.2f}%")

    # Show pending predictions briefly
    if pending:
        print("\nPending predictions (horizon not yet reached):")
        for p in pending[-20:]:
            print(f"  {p['prediction_id']:<24s} "
                  f"{p.get('direction','?'):<8s} "
                  f"{p.get('conviction','?'):<6s} "
                  f"entry={p.get('entry_price_pkr')} "
                  f"model={p.get('model','?')}")

    # Recent settled in detail
    if settled:
        print("\nMost recent settled predictions:")
        for p in sorted(settled, key=lambda x: x["prediction_id"])[-10:]:
            o = p["outcome"]
            flag = "HIT" if o["direction_hit"] else "MISS"
            print(f"  {p['prediction_id']:<24s} "
                  f"{p['direction']:<8s} "
                  f"mid={p.get('expected_return_5d_mid_pct', 0):+.1f}%  "
                  f"actual={o['actual_return_pct']:+.2f}%  "
                  f"{flag}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Re-evaluate even already-scored predictions")
    args = parser.parse_args()

    if not LOG_PATH.exists():
        print(f"No predictions log at {LOG_PATH}")
        return

    log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    preds = log.get("predictions", [])
    if not preds:
        print("No predictions to check yet.")
        return

    for p in preds:
        score_one(p, force=args.force)

    LOG_PATH.write_text(
        json.dumps(log, indent=2, default=str, ensure_ascii=False),
        encoding="utf-8",
    )

    print_scorecard(preds)
    print(f"\nUpdated {LOG_PATH}")


if __name__ == "__main__":
    main()
