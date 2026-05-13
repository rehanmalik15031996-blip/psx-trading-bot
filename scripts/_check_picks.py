"""Quick perf check on Tuesday picks."""
import pandas as pd, pathlib, json

ROOT = pathlib.Path(__file__).resolve().parent.parent
print("=== TUESDAY STRATEGIST PICKS — performance ===")
print()
s = json.loads((ROOT/"data/_strategist/2026-05-12.json").read_text(encoding="utf-8"))
buys = [a for a in s.get("actions", []) if a.get("bucket") in ("BUY","ADD")]
print(f"{'SYM':<8}{'TYPE':<6}{'wt':<6}{'May11':>9}{'May12':>9}{'May13':>9}{'1d%':>8}{'2d%':>8}  thesis")
print("-"*120)
for a in buys:
    sym = a["symbol"]
    fp = ROOT/"data/ohlcv"/f"{sym}.parquet"
    if not fp.exists(): continue
    df = pd.read_parquet(fp).sort_values("date").tail(3).reset_index(drop=True)
    if len(df)<3: continue
    c11,c12,c13 = float(df.iloc[0]["close"]), float(df.iloc[1]["close"]), float(df.iloc[2]["close"])
    one = (c13/c12-1)*100
    two = (c13/c11-1)*100
    th = (a.get("thesis") or "")[:60]
    print(f"{sym:<8}{a['bucket']:<6}{a.get('target_weight_pct',0):<6}{c11:>9.2f}{c12:>9.2f}{c13:>9.2f}{one:>+7.2f}%{two:>+7.2f}%  {th}")

print()
print("=== USER'S ACTUAL POSITIONS ===")
print(f"{'SYM':<8}{'shares':>8}{'avg':>9}{'May13':>9}{'pnl_pkr':>12}{'pnl_%':>8}")
print("-"*80)
positions = [("POL", 120, 660.0)]
for sym, shares, avg in positions:
    fp = ROOT/"data/ohlcv"/f"{sym}.parquet"
    df = pd.read_parquet(fp).sort_values("date").tail(1).reset_index(drop=True)
    c = float(df.iloc[0]["close"])
    pnl = (c - avg) * shares
    pct = (c/avg - 1) * 100
    print(f"{sym:<8}{shares:>8}{avg:>9.2f}{c:>9.2f}{pnl:>+12.0f}{pct:>+7.2f}%")
