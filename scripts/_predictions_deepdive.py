"""Extended analysis: did the stopped-out names recover by T+10?
Also breaks out the BUY/ADD/HIGH-conviction calls into clusters
to surface the doubling-down pattern.
"""
import json
from datetime import date
from pathlib import Path
import pandas as pd

START = date(2026, 5, 4)
END   = date(2026, 5, 13)

OHLCV_DIR = Path("data/ohlcv")


def load_close(sym):
    p = OHLCV_DIR / f"{sym}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date").reset_index(drop=True)


def close_n(df, d, n):
    sub = df[df["date"] > d].head(n)
    if len(sub) < 1:
        return None
    return float(sub["close"].iloc[-1])


def min_close(df, d, n):
    sub = df[df["date"] > d].head(n)
    if sub.empty:
        return None
    return float(sub["close"].min())


def max_close(df, d, n):
    sub = df[df["date"] > d].head(n)
    if sub.empty:
        return None
    return float(sub["close"].max())


def main():
    d = json.loads(open("data/predictions_log.json", encoding="utf-8").read())
    preds = d.get("predictions") or []

    # Recurrence count per symbol in window
    by_sym: dict[str, list[dict]] = {}
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if not (START <= gd <= END):
            continue
        by_sym.setdefault(p["symbol"], []).append({**p, "gen_date": gd})

    # ---------- Recurring losers ----------
    print("=== Symbols predicted >= 3 times in window — doubling-down risk ===\n")
    for sym, lst in sorted(by_sym.items(), key=lambda x: -len(x[1])):
        if len(lst) < 3:
            continue
        df = load_close(sym)
        if df is None:
            continue
        lst.sort(key=lambda r: r["gen_date"])
        print(f"  {sym} ({lst[0]['sector']})  — {len(lst)} predictions:")
        for r in lst:
            gd = r["gen_date"]
            entry = r.get("entry_price_pkr") or 0
            mid = r.get("expected_return_5d_mid_pct")
            action = r.get("suggested_action")
            conviction = r.get("conviction")
            t5 = close_n(df, gd, 5)
            t5_pct = (t5 / entry - 1) * 100 if t5 and entry else None
            tag = ""
            if action in ("BUY", "ADD"):
                tag = " [LONG]"
            elif action in ("AVOID", "SHORT", "EXIT", "REDUCE"):
                tag = " [BEARISH]"
            t5_s = f"{t5:7.2f}" if t5 is not None else "    N/A"
            t5p_s = f"{t5_pct:+6.2f}%" if t5_pct is not None else "    N/A"
            mid_s = f"{mid:+5.2f}%" if mid is not None else "  N/A"
            print(f"    {gd}  {action:<5} {conviction:<6} "
                  f"entry={entry:>7.2f}  mid={mid_s}  "
                  f"T+5={t5_s} ({t5p_s}){tag}")
        print()

    # ---------- Stopped-out names: T+10 recovery? ----------
    print("\n=== STOPPED OUT names — did T+10 close recover? ===\n")
    stopped = []
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if not (START <= gd <= END):
            continue
        stop = p.get("suggested_stop_pkr")
        if not stop:
            continue
        df = load_close(p["symbol"])
        if df is None:
            continue
        worst = min_close(df, gd, 5)
        if worst is None or worst > stop:
            continue
        # Stopped: compare to T+10
        t10 = close_n(df, gd, 10)
        t5  = close_n(df, gd, 5)
        if t5 is None or t10 is None:
            continue
        entry = p.get("entry_price_pkr") or 0
        stopped.append({
            "gen_date": gd.isoformat(),
            "symbol":   p["symbol"],
            "sector":   p.get("sector"),
            "action":   p.get("suggested_action"),
            "entry":    round(entry, 2),
            "stop":     stop,
            "worst_5d": round(worst, 2),
            "t5":       round(t5, 2),
            "t10":      round(t10, 2),
            "t5_ret%":  round((t5 / entry - 1) * 100, 2) if entry else None,
            "t10_ret%": round((t10 / entry - 1) * 100, 2) if entry else None,
            "ret_t5_to_t10%": round((t10 / t5 - 1) * 100, 2),
        })
    df_s = pd.DataFrame(stopped).sort_values(["symbol", "gen_date"])
    print(df_s.to_string(index=False))

    # ---------- Best calls: where did we leave money? ----------
    print("\n=== HOLD/NEUTRAL calls that left money — symbols that rallied >+3% ===\n")
    left_money = []
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if not (START <= gd <= END):
            continue
        if p.get("direction") != "NEUTRAL":
            continue
        if p.get("suggested_action") != "HOLD":
            continue
        df = load_close(p["symbol"])
        if df is None:
            continue
        entry = p.get("entry_price_pkr") or 0
        t5 = close_n(df, gd, 5)
        if not t5 or not entry:
            continue
        ret = (t5 / entry - 1) * 100
        if ret < 3.0:
            continue
        left_money.append({
            "gen_date": gd.isoformat(),
            "symbol":   p["symbol"],
            "sector":   p.get("sector"),
            "entry":    round(entry, 2),
            "t5":       round(t5, 2),
            "actual%":  round(ret, 2),
            "mid_pred%": p.get("expected_return_5d_mid_pct"),
        })
    df_lm = pd.DataFrame(left_money).sort_values("actual%", ascending=False)
    if not df_lm.empty:
        print(df_lm.to_string(index=False))
    else:
        print("(none)")

    # ---------- Sector tilt vs actual return ----------
    print("\n=== Banking deep-dive — every Banking call this window ===\n")
    bank_rows = []
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if not (START <= gd <= END):
            continue
        if p.get("sector") != "Banking":
            continue
        df = load_close(p["symbol"])
        if df is None:
            continue
        entry = p.get("entry_price_pkr") or 0
        t5 = close_n(df, gd, 5)
        if not t5 or not entry:
            continue
        bank_rows.append({
            "gen_date": gd.isoformat(),
            "symbol":   p["symbol"],
            "action":   p.get("suggested_action"),
            "conviction": p.get("conviction"),
            "mid%":     p.get("expected_return_5d_mid_pct"),
            "actual%":  round((t5 / entry - 1) * 100, 2),
        })
    df_b = pd.DataFrame(bank_rows).sort_values(["symbol", "gen_date"])
    print(df_b.to_string(index=False))


if __name__ == "__main__":
    main()
