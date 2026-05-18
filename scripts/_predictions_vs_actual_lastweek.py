"""Compare last week's predictions against actual prices.

For every prediction whose generated_at is between 2026-05-04 and
2026-05-11 (the predictions whose 5d window covers May 11-15 / 18),
we pull:
  - entry price at issuance,
  - expected_return_5d_mid_pct,
  - direction & suggested_action,
  - actual close 5 trading days later,
  - actual realised return,
  - error vs prediction.

Then we score: HIT (direction right + within band), DIRECTIONAL (sign
right but magnitude off), MISS (sign wrong), STOPPED (price went
past suggested_stop), BLOWN (worse than the low-end of the band).
"""
import json
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

START = date(2026, 5, 4)    # predictions issued from this date
END   = date(2026, 5, 13)   # last issuance date that has 5d window <= Fri

OHLCV_DIR = Path("data/ohlcv")

def load_close(sym: str) -> pd.DataFrame | None:
    p = OHLCV_DIR / f"{sym}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def close_n_trading_days_after(df: pd.DataFrame, d: date, n: int):
    if df is None or df.empty:
        return None
    sub = df[df["date"] > d].head(n)
    if len(sub) < n:
        return sub["close"].iloc[-1] if len(sub) else None
    return float(sub["close"].iloc[-1])


def close_on_or_before(df: pd.DataFrame, d: date):
    sub = df[df["date"] <= d]
    if sub.empty:
        return None
    return float(sub["close"].iloc[-1])


def min_low_in_window(df: pd.DataFrame, start: date, n: int):
    """Approximate intra-day low using the next n closes."""
    sub = df[df["date"] > start].head(n)
    if sub.empty:
        return None
    return float(sub["close"].min())


def main() -> None:
    d = json.loads(open("data/predictions_log.json", encoding="utf-8").read())
    preds = d.get("predictions") or []

    rows = []
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if not (START <= gd <= END):
            continue

        sym = p["symbol"]
        df_close = load_close(sym)
        if df_close is None:
            continue

        # entry price = price at issuance (use stored)
        entry = p.get("entry_price_pkr")
        # if missing or weird, derive from close on issuance date
        if entry is None:
            entry = close_on_or_before(df_close, gd)
        if entry is None or entry == 0:
            continue

        horizon = int(p.get("horizon_trading_days", 5) or 5)
        end_close = close_n_trading_days_after(df_close, gd, horizon)
        if end_close is None:
            continue
        realised = (end_close / entry - 1.0) * 100.0

        mid  = p.get("expected_return_5d_mid_pct")
        low  = p.get("expected_return_5d_low_pct")
        high = p.get("expected_return_5d_high_pct")
        action = p.get("suggested_action")
        direction = p.get("direction")
        stop = p.get("suggested_stop_pkr")
        target = p.get("suggested_target_pkr")

        # Did we get stopped out (worst close in window crossed stop)
        worst = min_low_in_window(df_close, gd, horizon)
        stopped = False
        if stop is not None and worst is not None and worst <= stop:
            stopped = True

        # Score
        if direction == "UP":
            pred_sign = +1
        elif direction == "DOWN":
            pred_sign = -1
        else:
            pred_sign = 0
        if realised > 0.5:
            real_sign = +1
        elif realised < -0.5:
            real_sign = -1
        else:
            real_sign = 0

        # error
        err = None
        if mid is not None:
            err = realised - mid

        # outcome label
        if pred_sign == 0 and abs(realised) < 2.0:
            verdict = "HIT (flat call, flat outcome)"
        elif pred_sign != 0 and pred_sign == real_sign:
            if mid is not None and low is not None and high is not None:
                if low <= realised <= high:
                    verdict = "HIT (within band)"
                else:
                    verdict = "DIRECTIONAL (sign right, magnitude off)"
            else:
                verdict = "DIRECTIONAL"
        elif pred_sign != 0 and real_sign != 0 and pred_sign != real_sign:
            verdict = "MISS (sign wrong)"
        elif pred_sign != 0 and real_sign == 0:
            verdict = "WHIFF (direction call, flat outcome)"
        else:
            verdict = "FLAT vs MOVE"

        if stopped:
            verdict = "STOPPED OUT"

        rows.append({
            "gen_date":   gd.isoformat(),
            "symbol":     sym,
            "sector":     p.get("sector"),
            "direction":  direction,
            "action":     action,
            "conviction": p.get("conviction"),
            "entry":      round(entry, 2),
            "end_close":  round(end_close, 2),
            "mid_pred%":  mid,
            "low_pred%":  low,
            "high_pred%": high,
            "actual%":    round(realised, 2),
            "err%":       round(err, 2) if err is not None else None,
            "stop":       stop,
            "stopped":    stopped,
            "verdict":    verdict,
        })

    if not rows:
        print("No predictions found in window")
        return

    df = pd.DataFrame(rows).sort_values(["gen_date", "symbol"])

    # Summary stats
    print(f"Predictions issued {START} to {END} with 5d window matured: {len(df)}")
    print()

    # By verdict
    print("Outcome distribution:")
    print(df["verdict"].value_counts().to_string())
    print()

    # By action / conviction
    print("\nBy suggested_action:")
    g = df.groupby("action").agg(
        n=("symbol", "count"),
        avg_actual=("actual%", "mean"),
        avg_mid=("mid_pred%", "mean"),
        avg_err=("err%", "mean"),
    ).round(2)
    print(g.to_string())

    print("\nBy conviction:")
    g = df.groupby("conviction").agg(
        n=("symbol", "count"),
        avg_actual=("actual%", "mean"),
        avg_mid=("mid_pred%", "mean"),
        avg_err=("err%", "mean"),
    ).round(2)
    print(g.to_string())

    print("\nBy sector (top 10 by N):")
    g = df.groupby("sector").agg(
        n=("symbol", "count"),
        avg_actual=("actual%", "mean"),
        avg_mid=("mid_pred%", "mean"),
        avg_err=("err%", "mean"),
    ).round(2).sort_values("n", ascending=False).head(10)
    print(g.to_string())

    # 5 biggest misses (sign wrong with biggest abs error)
    print("\n--- 10 biggest misses (largest negative err for UP calls or largest positive err for DOWN calls) ---")
    df_miss = df[df["verdict"].isin(["MISS (sign wrong)", "STOPPED OUT", "WHIFF (direction call, flat outcome)"])].copy()
    df_miss["abs_err"] = df_miss["err%"].abs()
    print(df_miss.sort_values("abs_err", ascending=False).head(10)[
        ["gen_date", "symbol", "sector", "direction", "action",
          "entry", "end_close", "mid_pred%", "actual%", "err%", "verdict"]
    ].to_string(index=False))

    # 5 best hits
    print("\n--- 10 best calls (correct direction + closest to mid) ---")
    df_hit = df[df["verdict"].str.startswith("HIT")].copy()
    df_hit["abs_err"] = df_hit["err%"].abs()
    print(df_hit.sort_values("abs_err").head(10)[
        ["gen_date", "symbol", "sector", "direction", "action",
          "entry", "end_close", "mid_pred%", "actual%", "err%"]
    ].to_string(index=False))

    # All BUY/ADD calls — review
    print("\n--- All BUY / ADD calls issued in window ---")
    df_buy = df[df["action"].isin(["BUY", "ADD"])].copy()
    print(df_buy[
        ["gen_date", "symbol", "sector", "conviction", "entry", "end_close",
          "mid_pred%", "actual%", "verdict"]
    ].sort_values(["gen_date", "symbol"]).to_string(index=False))

    # All AVOID/EXIT/SHORT calls — review
    print("\n--- All AVOID / EXIT / SHORT calls issued in window ---")
    df_avoid = df[df["action"].isin(["AVOID", "EXIT", "SHORT", "REDUCE"])].copy()
    if not df_avoid.empty:
        print(df_avoid[
            ["gen_date", "symbol", "sector", "conviction", "entry", "end_close",
              "mid_pred%", "actual%", "verdict"]
        ].sort_values(["gen_date", "symbol"]).to_string(index=False))
    else:
        print("(none)")

    # Save full review
    out = Path("data/_research/PREDICTIONS_vs_ACTUAL_2026-05-11_to_15.csv")
    df.to_csv(out, index=False)
    print(f"\nSaved full review to {out}")


if __name__ == "__main__":
    main()
