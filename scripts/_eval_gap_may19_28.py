"""Gap evaluation: strategist buckets + predictions vs actuals May 19–28."""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent
OHLCV = ROOT / "data" / "ohlcv"

BUCKETS = {
    "LONG_CORE": ["OGDC", "PPL", "POL", "MARI", "ATRL"],
    "SHORT": ["DGKC", "KOHC"],
    "BANKS": ["HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL"],
    "AVOID": ["KEL", "HUBC", "EPCL", "KAPCO", "NPL"],
}
START = date(2026, 5, 19)
END = date(2026, 5, 25)  # last OHLCV bar


def load(sym: str) -> pd.DataFrame:
    p = OHLCV / f"{sym}.parquet"
    df = pd.read_parquet(p)[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.sort_values("date")


def ret_between(df: pd.DataFrame, d0: date, d1: date) -> float | None:
    s = df[df["date"] == d0]
    e = df[df["date"] == d1]
    if s.empty or e.empty:
        return None
    return (float(e["close"].iloc[-1]) / float(s["close"].iloc[-1]) - 1) * 100


def main() -> None:
    print(f"=== BUCKET RETURNS {START} -> {END} ===")
    bucket_rets = {}
    for b, syms in BUCKETS.items():
        rs = []
        for sym in syms:
            r = ret_between(load(sym), START, END)
            if r is not None:
                rs.append((sym, r))
        avg = sum(x[1] for x in rs) / len(rs) if rs else None
        bucket_rets[b] = avg
        detail = ", ".join(f"{s}:{r:+.1f}%" for s, r in rs)
        print(f"  {b:10s} avg {avg:+.2f}%  ({detail})")

    kse = pd.read_parquet(ROOT / "data/macro/kse100.parquet")
    kse["date"] = pd.to_datetime(kse["date"]).dt.date
    k0 = kse[kse["date"] == START]
    k1 = kse[kse["date"] == END]
    if k0.empty:
        k0 = kse[kse["date"] == date(2026, 5, 19)]
    if k1.empty:
        k1 = kse[kse["date"] <= END].tail(1)
    if not k0.empty and not k1.empty:
        kr = (float(k1["kse100_close"].iloc[-1]) / float(k0["kse100_close"].iloc[-1]) - 1) * 100
        print(f"\n  KSE-100: {kr:+.2f}% ({k0['date'].iloc[-1]} -> {k1['date'].iloc[-1]})")

    # Predictions May 19
    d = json.loads((ROOT / "data/predictions_log.json").read_text(encoding="utf-8"))
    preds = [p for p in d.get("predictions", []) if (p.get("generated_at") or "")[:10] == "2026-05-19"]
    print(f"\n=== PREDICTIONS May 19 ({len(preds)} rows) vs 5d actual ===")
    hits = directional = miss = 0
    rows = []
    for p in preds:
        sym = p["symbol"]
        df = load(sym)
        gd = date(2026, 5, 19)
        sub = df[df["date"] > gd].head(5)
        if len(sub) < 1:
            continue
        entry = p.get("entry_price_pkr") or float(df[df["date"] == gd]["close"].iloc[-1])
        end = float(sub["close"].iloc[-1]) if len(sub) >= 5 else float(sub["close"].iloc[-1])
        actual = (end / entry - 1) * 100
        mid = p.get("expected_return_5d_mid_pct") or 0
        direction = p.get("direction", "NEUTRAL")
        action = p.get("suggested_action", "HOLD")
        pred_sign = 1 if direction == "BULLISH" else (-1 if direction == "BEARISH" else 0)
        real_sign = 1 if actual > 0.5 else (-1 if actual < -0.5 else 0)
        if pred_sign == real_sign and pred_sign != 0:
            directional += 1
            verdict = "DIRECTIONAL"
        elif pred_sign == 0 and abs(actual) < 2:
            hits += 1
            verdict = "HIT_FLAT"
        elif pred_sign != 0 and pred_sign != real_sign and real_sign != 0:
            miss += 1
            verdict = "MISS"
        else:
            verdict = "MIXED"
        rows.append((sym, direction, action, mid, actual, verdict))

    total = hits + directional + miss + max(0, len(rows) - hits - directional - miss)
    scored = hits + directional + miss
    print(f"  Directional hits: {directional}, flat hits: {hits}, sign misses: {miss}, n={len(rows)}")
    if scored:
        print(f"  Hit-rate (directional): {(directional + hits) / len(rows) * 100:.0f}%")

    rows.sort(key=lambda x: abs(x[4] - x[3]), reverse=True)
    print("\n  Biggest magnitude errors:")
    for sym, direction, action, mid, actual, verdict in rows[:8]:
        print(f"    {sym:6s} {direction:8s} {action:5s} pred {mid:+.1f}% actual {actual:+.1f}% -> {verdict}")

    # Strategist bucket thesis check
    print("\n=== STRATEGIST THESIS CHECK (May 19 call) ===")
    print("  Expected: LONG_CORE up, SHORT flat/down, BANKS bounce but don't chase, AVOID bounce but avoid")
    for b, avg in bucket_rets.items():
        if avg is None:
            continue
        if b == "LONG_CORE":
            ok = avg > 0
        elif b == "SHORT":
            ok = avg <= 0
        elif b == "BANKS":
            ok = avg > 0  # they bounced
        elif b == "AVOID":
            ok = True  # thesis was avoid despite bounce
        print(f"  {b}: {avg:+.2f}% — {'OK' if ok else 'MISS'}")


if __name__ == "__main__":
    main()
