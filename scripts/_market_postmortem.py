"""Quick post-mortem: what happened May 11 -> May 13."""
import pandas as pd, json, pathlib
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent

print("=" * 78)
print("MARKET POST-MORTEM: 2026-05-11 -> 2026-05-13")
print("=" * 78)

# ---- KSE-100 index move
kse = pd.read_parquet(ROOT / "data/macro/kse100.parquet").sort_values("date")
print("\n[A] KSE-100 INDEX (last 6 sessions)")
print("-" * 78)
tail = kse.tail(6).reset_index(drop=True)
prev = None
for _, row in tail.iterrows():
    d = pd.to_datetime(row["date"]).date()
    close = float(row.get("close") or row.get("Close") or 0)
    if prev:
        chg = close - prev
        pct = (chg / prev) * 100 if prev else 0
        arrow = "DOWN" if pct < 0 else "UP"
        print(f"  {d}  close={close:>12,.2f}  {arrow} {pct:+6.2f}%  ({chg:+,.0f} pts)")
    else:
        print(f"  {d}  close={close:>12,.2f}")
    prev = close

# ---- Per-stock damage (May 11 -> May 13)
print("\n[B] PER-STOCK MOVES (close-to-close, last 2 sessions)")
print("-" * 78)
print(f"  {'SYM':<8} {'May11':>10} {'May12':>10} {'May13':>10} {'2d %':>8}")
ohlcv_dir = ROOT / "data/ohlcv"
moves = []
for fp in sorted(ohlcv_dir.glob("*.parquet")):
    sym = fp.stem
    df = pd.read_parquet(fp).sort_values("date").tail(3).reset_index(drop=True)
    if len(df) < 3:
        continue
    c11 = float(df.iloc[0]["close"])
    c12 = float(df.iloc[1]["close"])
    c13 = float(df.iloc[2]["close"])
    pct = (c13 / c11 - 1) * 100
    moves.append((sym, c11, c12, c13, pct))

moves.sort(key=lambda x: x[4])
print("\n  Worst 10:")
for sym, c11, c12, c13, pct in moves[:10]:
    print(f"  {sym:<8} {c11:>10.2f} {c12:>10.2f} {c13:>10.2f} {pct:>7.2f}%")
print("\n  Best 10:")
for sym, c11, c12, c13, pct in moves[-10:][::-1]:
    print(f"  {sym:<8} {c11:>10.2f} {c12:>10.2f} {c13:>10.2f} {pct:>7.2f}%")

# ---- Specifically what we said
print("\n[C] WHAT WE RECOMMENDED (Strategist actions for May 12)")
print("-" * 78)
strat12 = json.loads((ROOT / "data/_strategist/2026-05-12.json").read_text(encoding="utf-8"))
buys12 = [a for a in (strat12.get("actions") or [])
          if a.get("bucket") in ("BUY", "ADD")]
move_map = {m[0]: m for m in moves}
for a in buys12:
    sym = a["symbol"]
    if sym in move_map:
        _, c11, c12, c13, pct = move_map[sym]
        print(f"  {sym:<8} {a['bucket']:<5} wt={a.get('target_weight_pct'):>4}%  "
              f"May11={c11:.2f}  May13={c13:.2f}  {pct:+6.2f}%")

# ---- Today's strategist verdict
print("\n[D] TODAY'S STRATEGIST (May 13)")
print("-" * 78)
strat13 = json.loads((ROOT / "data/_strategist/2026-05-13.json").read_text(encoding="utf-8"))
print(f"  as_of:    {strat13.get('as_of')}")
print(f"  model:    {strat13.get('model')}")
print(f"  fallback: {strat13.get('fallback_used')}")
print(f"  stance:   {strat13.get('risk_stance')} / {strat13.get('conviction')}")
print(f"  headline: {(strat13.get('headline') or '')[:120]}")
acts13 = strat13.get("actions") or []
buys13 = [a for a in acts13 if a.get("bucket") in ("BUY", "ADD")]
avoids13 = [a for a in acts13 if a.get("bucket") == "AVOID"]
trims13 = [a for a in acts13 if a.get("bucket") == "TRIM"]
print(f"  BUY/ADD:  {[(a['symbol'], a['bucket'], a.get('target_weight_pct')) for a in buys13]}")
print(f"  AVOID:    {[a['symbol'] for a in avoids13]}")
print(f"  TRIM:     {[a['symbol'] for a in trims13]}")

# ---- Health badges
print("\n[E] WORKFLOW HEALTH (post-CI)")
print("-" * 78)
hd = ROOT / "data/_health"
for f in sorted(hd.glob("*.json")):
    if f.name.startswith("_"):
        continue
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        ok = d.get("ok") or d.get("status") == "ok"
        last = d.get("last_success_ts") or d.get("last_run_ts") or d.get("as_of") or "?"
        if "test" in f.stem or "backtest" in f.stem or "validation" in f.stem:
            continue
        print(f"  {f.stem:<22} {'GREEN' if ok else 'RED  ':<6} last={str(last)[:19]}")
    except Exception:
        pass

print("\n" + "=" * 78)
