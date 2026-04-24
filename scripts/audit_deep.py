"""Deep audit: is the 'monthly top-3 by 150d mom' rule real, or a fluke?

Checks:
  1. Recent-window (last 2 years) performance — still work in current regime?
  2. Rolling 1-year performance — stability over time
  3. Per-stock contribution — which names drive returns, which are drag?
  4. Sensitivity to cost (20/40/60 bps), filter params
  5. What days would LLM/news overlay actually trigger?
  6. Sanity — compare against random top-3 picks (lucky monkey baseline)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from data.store import load_ohlcv
from config.universe import symbols

closes = {}
for s in symbols():
    d = load_ohlcv(s).sort_values("date").set_index("date")["close"]
    closes[s] = d
wide = pd.DataFrame(closes).ffill()
wide.index = pd.to_datetime(wide.index)
lr = np.log(wide).diff()
r = wide.pct_change()


def simulate(weights, cost_rt):
    w = weights.ffill().fillna(0)
    dw = w.diff().abs().fillna(w.abs())
    daily_cost = dw.sum(axis=1) * (cost_rt / 2)
    gross = (w.shift(1) * r).sum(axis=1)
    return gross - daily_cost.shift(1).fillna(0)


def monthly_150d_filtered(vol_cap=0.70, market_filter=True, top_n=3,
                          mom_win=150):
    mom = lr.rolling(mom_win).sum()
    vol = lr.rolling(20).std()
    rebal = lr.resample("ME").last().index
    w = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    picks_log = []
    for i, dt in enumerate(rebal):
        if dt not in mom.index: continue
        if market_filter and mom.loc[dt].mean() < 0:
            picks_log.append((dt, "CASH", []))
            continue
        s = mom.loc[dt].copy()
        vr = vol.loc[dt].rank(pct=True)
        s = s.where(vr <= vol_cap).dropna()
        if len(s) < top_n:
            picks_log.append((dt, "INSUFFICIENT", []))
            continue
        top = s.nlargest(top_n).index.tolist()
        picks_log.append((dt, "HOLD", top))
        start = dt + pd.Timedelta(days=1)
        end = rebal[i + 1] if i + 1 < len(rebal) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            w.loc[mask, sym] = 1.0 / top_n
    return w, picks_log


def stats(rets, label=""):
    rets = rets.dropna()
    if len(rets) < 60: return None
    cum = (1 + rets).cumprod()
    yrs = len(rets) / 252
    cagr = cum.iloc[-1] ** (1 / yrs) - 1
    sh = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
    dd = (cum / cum.cummax() - 1).min()
    cal = cagr / abs(dd) if dd < 0 else 99
    return {"cagr": cagr, "sharpe": sh, "calmar": cal, "dd": dd,
            "n_days": len(rets), "years": yrs}


# ============================================================
# 1. Recent-window performance
# ============================================================
print("=" * 70)
print("1. PERIOD-BY-PERIOD: is the edge stable across regimes?")
print("=" * 70)

w_rule, picks = monthly_150d_filtered()
rule_rets = simulate(w_rule, 0.004).dropna()
bh_rets = r.mean(axis=1).dropna()

# Split into 4 periods: 2021, 2022, 2023, 2024, 2025-26
periods = [
    ("2021 (start)", "2021-04-01", "2021-12-31"),
    ("2022 (bear + crisis)", "2022-01-01", "2022-12-31"),
    ("2023 (recovery)", "2023-01-01", "2023-12-31"),
    ("2024 (bull)", "2024-01-01", "2024-12-31"),
    ("2025-26 (recent)", "2025-01-01", "2026-12-31"),
]
print(f"\n{'Period':25s} | {'Rule CAGR':>10s} {'Sh':>5s} {'DD':>7s} | {'B&H CAGR':>10s} {'Sh':>5s} {'DD':>7s}")
print("-" * 90)
for label, s, e in periods:
    r_rule = rule_rets[s:e]
    r_bh = bh_rets[s:e]
    if len(r_rule) < 30 or len(r_bh) < 30: continue
    sr = stats(r_rule)
    sb = stats(r_bh)
    if sr and sb:
        print(f"{label:25s} | {sr['cagr']:+9.1%} {sr['sharpe']:5.2f} {sr['dd']:+7.1%} | "
              f"{sb['cagr']:+9.1%} {sb['sharpe']:5.2f} {sb['dd']:+7.1%}")


# ============================================================
# 2. Rolling 1-year performance
# ============================================================
print("\n" + "=" * 70)
print("2. ROLLING 1-YEAR SHARPE (rule vs B&H)")
print("=" * 70)
# Compute 252-day rolling Sharpe
rule_roll_sh = rule_rets.rolling(252).apply(
    lambda s: s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
)
bh_roll_sh = bh_rets.rolling(252).apply(
    lambda s: s.mean() / s.std() * np.sqrt(252) if s.std() > 0 else 0
)
joined = pd.concat({"rule": rule_roll_sh, "bh": bh_roll_sh}, axis=1).dropna()
n_win = (joined["rule"] > joined["bh"]).sum()
n_tot = len(joined)
print(f"Rolling 1Y Sharpe (rule vs B&H):")
print(f"  Rule mean 1Y Sharpe: {joined['rule'].mean():.2f}")
print(f"  B&H  mean 1Y Sharpe: {joined['bh'].mean():.2f}")
print(f"  % of days rule > B&H: {n_win/n_tot:.1%} ({n_win}/{n_tot})")
print(f"  Rule worst 1Y Sharpe: {joined['rule'].min():.2f}")
print(f"  B&H  worst 1Y Sharpe: {joined['bh'].min():.2f}")


# ============================================================
# 3. Per-stock contribution
# ============================================================
print("\n" + "=" * 70)
print("3. PER-STOCK CONTRIBUTION to rule's P&L")
print("=" * 70)
# For each month's picks, compute next-month return PER PICKED STOCK
contrib = {s: [] for s in symbols()}
n_selected = {s: 0 for s in symbols()}
picks_df = [p for p in picks if p[1] == "HOLD"]
rebal_idx = lr.resample("ME").last().index.tolist()
for i, (dt, _, picks_list) in enumerate(picks_df):
    if i + 1 >= len(picks_df): continue
    next_dt = picks_df[i + 1][0]
    for sym in picks_list:
        px = wide.loc[dt:next_dt, sym]
        if len(px) < 2: continue
        ret = px.iloc[-1] / px.iloc[0] - 1
        contrib[sym].append(ret)
        n_selected[sym] += 1

contrib_summary = []
for s in symbols():
    if n_selected[s] == 0:
        contrib_summary.append((s, 0, 0, 0, 0))
        continue
    arr = np.array(contrib[s])
    contrib_summary.append((s, n_selected[s], arr.mean(), arr.sum(), (arr > 0).mean()))

contrib_summary.sort(key=lambda x: -x[3])
print(f"{'Symbol':7s} {'N picks':>7s}  {'avg %/pick':>11s}  {'sum %':>8s}  {'win%':>6s}")
for s, n, avg, tot, wr in contrib_summary:
    if n == 0:
        print(f"{s:7s} {n:>7d}  {'--':>11s}  {'--':>8s}  {'--':>6s}     NEVER PICKED")
    else:
        print(f"{s:7s} {n:>7d}  {avg:>+10.2%}  {tot:>+7.1%}  {wr:>5.0%}")

# How many stocks are ever picked?
n_ever = sum(1 for _, n, *_ in contrib_summary if n > 0)
print(f"\n→ {n_ever}/{len(symbols())} stocks ever picked over {len(picks_df)} rebalances")
print(f"→ {len([1 for _,_,_,t,_ in contrib_summary if t < 0])} stocks are NET DRAG on P&L")


# ============================================================
# 4. Cost sensitivity
# ============================================================
print("\n" + "=" * 70)
print("4. COST SENSITIVITY (how fragile is the edge?)")
print("=" * 70)
for cost in [0.002, 0.004, 0.006, 0.008, 0.010]:
    rets_c = simulate(w_rule, cost)
    s = stats(rets_c)
    if s:
        print(f"  Round-trip {int(cost*10000)} bps:  CAGR {s['cagr']:+6.1%}  "
              f"Sharpe {s['sharpe']:.2f}  DD {s['dd']:+6.1%}")


# ============================================================
# 5. What would a "LLM overlay" actually do?
# ============================================================
print("\n" + "=" * 70)
print("5. LLM/NEWS OVERLAY IMPACT — when would it trigger?")
print("=" * 70)
# Proxy: a "bad month" is when the universe avg drops >8% in a month
# Could LLM predict these? Check if we could have avoided them
monthly_ret = r.resample("ME").apply(lambda s: (1 + s).prod() - 1)
universe_monthly = monthly_ret.mean(axis=1)
bad_months = universe_monthly[universe_monthly < -0.08]
print(f"Months where universe dropped > 8%: {len(bad_months)}")
for dt, ret in bad_months.items():
    print(f"  {dt.strftime('%Y-%m')}: {ret:+.1%}")
# What did our rule do in those months?
print(f"\nRule's performance in those bad months:")
rule_monthly = rule_rets.resample("ME").apply(lambda s: (1 + s).prod() - 1)
for dt in bad_months.index:
    if dt in rule_monthly.index:
        print(f"  {dt.strftime('%Y-%m')}: rule = {rule_monthly.loc[dt]:+.1%} "
              f"vs universe {bad_months.loc[dt]:+.1%}")
print("\n→ If rule's down-month loss ≈ universe, LLM could cut 50% during these")
print("  BUT only if we can identify them IN ADVANCE (hard)")


# ============================================================
# 6. Lucky monkey baseline
# ============================================================
print("\n" + "=" * 70)
print("6. LUCKY MONKEY (random top-3 monthly)")
print("=" * 70)
np.random.seed(42)
monkey_runs = []
for trial in range(200):
    rebal = lr.resample("ME").last().index
    w_m = pd.DataFrame(0.0, index=lr.index, columns=lr.columns)
    for i, dt in enumerate(rebal):
        top = np.random.choice(lr.columns, 3, replace=False)
        start = dt + pd.Timedelta(days=1)
        end = rebal[i + 1] if i + 1 < len(rebal) else lr.index.max()
        mask = (lr.index > dt) & (lr.index <= end)
        for sym in top:
            w_m.loc[mask, sym] = 1/3
    monkey_runs.append(stats(simulate(w_m, 0.004)))

monkey_cagrs = [m["cagr"] for m in monkey_runs if m]
monkey_sh = [m["sharpe"] for m in monkey_runs if m]
print(f"200 random top-3 monthly portfolios:")
print(f"  CAGR:    mean {np.mean(monkey_cagrs):+.1%}  "
      f"5-95% [{np.percentile(monkey_cagrs, 5):+.1%}, "
      f"{np.percentile(monkey_cagrs, 95):+.1%}]")
print(f"  Sharpe:  mean {np.mean(monkey_sh):.2f}  "
      f"5-95% [{np.percentile(monkey_sh, 5):.2f}, "
      f"{np.percentile(monkey_sh, 95):.2f}]")
s_rule = stats(rule_rets)
pct_beaten_cagr = np.mean([m["cagr"] < s_rule["cagr"] for m in monkey_runs if m])
pct_beaten_sh = np.mean([m["sharpe"] < s_rule["sharpe"] for m in monkey_runs if m])
print(f"\n  Our rule CAGR {s_rule['cagr']:+.1%}, Sharpe {s_rule['sharpe']:.2f}")
print(f"  Rule beats {pct_beaten_cagr:.0%} of monkeys on CAGR, "
      f"{pct_beaten_sh:.0%} on Sharpe")
print(f"\n→ If rule beats >80% of monkeys on Sharpe, the 150d momentum signal is real.")
print(f"→ If < 60%, it's noise.")
