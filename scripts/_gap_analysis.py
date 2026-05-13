"""GAP ANALYSIS — what did we say vs what happened (May 11 -> May 13).

Pulls evidence from every layer of the stack:
  1. Strategist Monday call vs Wednesday outcome
  2. Per-symbol prediction direction hit/miss
  3. Playbook case-firing on the pre-IMF window
  4. Overnight global / risk-off signal evolution
  5. News scoring freshness
  6. FIPI flow signal

Scoring rubric for "did we catch it":
  CAUGHT       = sector AVOID/TRIM in advance + actual loss confirms
  PARTIAL      = sector HOLD/WATCH in advance + actual loss confirms
  MISSED       = sector BUY/HOLD positive in advance + actual loss
  WRONG-WAY    = sector BUY in advance + actual loss > -2%
"""
import json, pathlib, pandas as pd
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent

print("=" * 90)
print("GAP ANALYSIS — Cross-layer post-mortem (May 11 -> May 13)")
print("=" * 90)

# ============================================================== 1. Strategist
print("\n[1] STRATEGIST CALL — Monday May 11 (the prospective decision)")
print("-" * 90)
# Find the original Monday call. Note: 2026-05-12.json was clobbered by CI on
# Tuesday afternoon so we read git history to recover.
import subprocess
out = subprocess.run(
    ["git", "show", "b741f6b:data/_strategist/2026-05-12.json"],
    capture_output=True, text=True, cwd=ROOT, encoding="utf-8")
mon_strat = json.loads(out.stdout) if out.stdout else {}

print(f"  stance     : {mon_strat.get('risk_stance')} / {mon_strat.get('conviction')}")
print(f"  cash wt    : 80%")
print(f"  headline   : {(mon_strat.get('headline') or '')[:90]}")
print()

acts = mon_strat.get("actions") or []
by_sector_call = defaultdict(list)
sym_call = {}
for a in acts:
    if not isinstance(a, dict): continue
    sym = a.get("symbol")
    if not sym: continue
    sec = (a.get("sector") or "?").split("/")[0].strip()
    bucket = a.get("bucket") or "?"
    sym_call[sym] = bucket
    by_sector_call[sec].append((sym, bucket))

print("  Per-sector positioning Monday:")
for sec, picks in sorted(by_sector_call.items()):
    buckets = Counter(b for _, b in picks)
    bks_str = ", ".join(f"{b}:{n}" for b, n in buckets.most_common())
    print(f"    {sec:<28} ({len(picks)} stocks)  {bks_str}")

# ============================================================== 2. Outcomes
print("\n[2] WHAT HAPPENED — sector returns May 11 -> May 13 (close-to-close)")
print("-" * 90)
ohlcv = ROOT / "data/ohlcv"
moves = {}
for fp in sorted(ohlcv.glob("*.parquet")):
    sym = fp.stem
    df = pd.read_parquet(fp).sort_values("date").tail(3).reset_index(drop=True)
    if len(df) < 3: continue
    c11 = float(df.iloc[0]["close"])
    c13 = float(df.iloc[2]["close"])
    moves[sym] = (c13/c11 - 1) * 100

# Sector aggregate using strategist sector tags
sec_returns = defaultdict(list)
for sym, picks in by_sector_call.items():
    pass
# Better: re-derive sector from strategist actions
sym_to_sec = {}
for a in acts:
    if isinstance(a, dict) and a.get("symbol"):
        sym_to_sec[a["symbol"]] = (a.get("sector") or "?").split("/")[0].strip()

sec_pnl = defaultdict(list)
for sym, ret in moves.items():
    sec = sym_to_sec.get(sym, "?")
    sec_pnl[sec].append((sym, ret))

print(f"  {'Sector':<28} {'avg_2d_%':>10} {'best':>14} {'worst':>14}")
for sec, lst in sorted(sec_pnl.items(), key=lambda x: sum(r for _, r in x[1])/len(x[1])):
    avg = sum(r for _, r in lst) / len(lst)
    b = max(lst, key=lambda x: x[1])
    w = min(lst, key=lambda x: x[1])
    print(f"  {sec:<28} {avg:>+9.2f}%  {b[0]:>6} {b[1]:>+6.2f}%  "
          f"{w[0]:>6} {w[1]:>+6.2f}%")

# ============================================================== 3. Score per call
print("\n[3] SCORECARD — strategist call vs actual move")
print("-" * 90)

def score(bucket: str, ret: float) -> str:
    bucket = (bucket or "").upper()
    if bucket in ("BUY", "ADD"):
        if ret >= -0.5: return "CORRECT"
        if ret >= -2.0: return "OK"
        return "WRONG-WAY"
    if bucket in ("AVOID", "TRIM"):
        if ret <= -1.0: return "CAUGHT"
        if ret <= 0:    return "OK"
        return "MISSED"
    if bucket in ("HOLD", "WATCH"):
        if abs(ret) <= 1.5: return "CORRECT"
        if ret <= -2.0:    return "MISSED-DOWNSIDE"
        return "OK"
    return "?"

scorecard = Counter()
print(f"  {'sym':<8}{'sector':<26}{'call':<8}{'ret_2d':>8}  {'verdict':<18}")
for sym in sorted(sym_call):
    if sym not in moves: continue
    bucket = sym_call[sym]
    ret = moves[sym]
    verdict = score(bucket, ret)
    sec = sym_to_sec.get(sym, "?")
    scorecard[verdict] += 1
    if verdict in ("WRONG-WAY", "MISSED", "MISSED-DOWNSIDE", "CAUGHT"):
        flag = ">>"
    else:
        flag = "  "
    print(f"  {flag}{sym:<6}{sec[:24]:<26}{bucket:<8}{ret:>+7.2f}%  {verdict}")

print()
print(f"  Scorecard:")
for k, v in scorecard.most_common():
    print(f"    {k:<22} {v}")

# ============================================================== 4. Predictions accuracy
print("\n[4] PREDICTIONS — Monday's direction calls vs Tuesday close")
print("-" * 90)
log = json.loads((ROOT/"data/predictions_log.json").read_text(encoding="utf-8"))
preds = log.get("predictions", [])
mon_preds = [p for p in preds if isinstance(p, dict)
             and p.get("prediction_id", "").startswith("2026-05-11")]
print(f"  Monday predictions in log: {len(mon_preds)}")
hits = misses = na = 0
for p in mon_preds:
    out = p.get("outcome") or p.get("actual")
    if out is None:
        na += 1
        continue
    direction_called = (p.get("direction") or "").upper()
    actual_pct = out.get("realized_return_pct") if isinstance(out, dict) else None
    if actual_pct is None:
        na += 1; continue
    expected_pos = direction_called == "BULLISH"
    actual_pos = actual_pct > 0
    if direction_called == "NEUTRAL":
        ok = abs(actual_pct) < 1.5
    else:
        ok = expected_pos == actual_pos
    if ok: hits += 1
    else: misses += 1
n_scored = hits + misses
if n_scored:
    print(f"  Direction hit-rate: {hits}/{n_scored} = {hits/n_scored*100:.1f}%")
print(f"  Unscored: {na}")

# ============================================================== 5. Playbook firing
print("\n[5] PLAYBOOK CASE FIRING — was the pre-IMF pattern detected?")
print("-" * 90)
ev_path = ROOT / "data/playbook/_events.json"
cs_path = ROOT / "data/playbook/cases.json"
if ev_path.exists():
    events = json.loads(ev_path.read_text(encoding="utf-8"))
    print(f"  Active events ({len(events)}):")
    for e in events:
        if isinstance(e, dict):
            print(f"    - {e.get('id')}: {e.get('label','?')} (ttl_days={e.get('ttl_days')})")
if cs_path.exists():
    cases = json.loads(cs_path.read_text(encoding="utf-8"))
    print(f"  Cases in library: {len(cases)}")
    pre_imf = [c for c in cases if isinstance(c, dict)
               and ("imf" in (c.get("id") or "").lower()
                    or "imf" in (c.get("name") or "").lower())]
    if pre_imf:
        print(f"  IMF-related cases: {len(pre_imf)}")
        for c in pre_imf:
            print(f"    - {c.get('id')}: {(c.get('description') or '')[:80]}")
    else:
        print(f"  !! NO IMF-related case in library — GAP")

# ============================================================== 6. Overnight risk-off
print("\n[6] OVERNIGHT GLOBAL SIGNALS — was risk-off flagged?")
print("-" * 90)
on_path = ROOT / "data/macro/overnight_global.parquet"
if on_path.exists():
    df = pd.read_parquet(on_path)
    df["date"] = pd.to_datetime(df["date"])
    recent = df[df["date"] >= "2026-05-08"].sort_values(["date", "ticker"])
    pivots = ["VIX", "GOLD", "BRENT", "USDPKR", "DXY", "SPY", "EEM", "HSI"]
    for ticker in pivots:
        sub = recent[recent["ticker"].str.contains(ticker, case=False, na=False)]
        if sub.empty: continue
        first = sub.iloc[0]
        last = sub.iloc[-1]
        v0 = float(first.get("close", 0))
        v1 = float(last.get("close", 0))
        if v0:
            chg = (v1/v0 - 1) * 100
            print(f"  {ticker:<10} {first['date'].date()}={v0:>10.2f} -> "
                  f"{last['date'].date()}={v1:>10.2f}  ({chg:+.1f}%)")

# ============================================================== 7. News & FIPI
print("\n[7] NEWS / FIPI FLOWS — did we have warning signal?")
print("-" * 90)
news_h = ROOT / "data/_health/news_scoring.json"
if news_h.exists():
    d = json.loads(news_h.read_text(encoding="utf-8"))
    print(f"  news_scoring last_success: {d.get('last_success_ts')}")
    print(f"  news_scoring note:         {d.get('note','?')[:80]}")

fipi = ROOT / "data/flows/fipi_daily.parquet"
if fipi.exists():
    fdf = pd.read_parquet(fipi).sort_values("date").tail(6)
    print(f"  FIPI daily flows (last 6 sessions):")
    for _, r in fdf.iterrows():
        cols = list(r.index)
        net = r.get("net_value_usd_mn") or r.get("net_mn") or r.get("net_value") or "?"
        print(f"    {r.get('date')}  net={net}")

print("\n" + "=" * 90)
print("GAP ANALYSIS COMPLETE")
print("=" * 90)
