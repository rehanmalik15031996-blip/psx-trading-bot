"""Quick parameter sweep to verify backtest correctness and find best stop cfg."""
import sys
sys.path.insert(0, ".")

from brain.strategy import StrategyConfig, build_prices_wide
from brain.backtest_v2 import simulate
from config.universe import symbols

wide = build_prices_wide(symbols())

configs = [
    ("no stop", {"use_trailing_stop": False}),
    ("stop -15%", {"use_trailing_stop": True, "trailing_stop_pct": 0.15}),
    ("stop -20%", {"use_trailing_stop": True, "trailing_stop_pct": 0.20}),
    ("stop -25%", {"use_trailing_stop": True, "trailing_stop_pct": 0.25}),
    ("top-5 no stop", {"use_trailing_stop": False, "top_n": 5}),
    ("top-5 stop -20%", {"use_trailing_stop": True, "trailing_stop_pct": 0.20, "top_n": 5}),
]

print(f"{'Config':25s}  {'CAGR':>8s}  {'Sh':>5s}  {'Cal':>5s}  {'MaxDD':>7s}  "
      f"{'Trades':>7s}  {'% beat B&H':>10s}")
for label, kwargs in configs:
    cfg = StrategyConfig(**kwargs)
    res = simulate(wide, cfg, use_regime_overlay=False, include_cost_sensitivity=False)
    m = res.metrics
    print(f"{label:25s}  {m['cagr']:+7.2%}  {m['sharpe']:5.2f}  "
          f"{m['calmar']:5.2f}  {m['max_drawdown']:+7.2%}  "
          f"{len(res.trades):7d}  {m['rolling_1y_beat_bh_pct']:>9.0%}")

print(f"\nB&H reference: CAGR {res.metrics['benchmark_cagr']:+.2%}  "
      f"Sh {res.metrics['benchmark_sharpe']:.2f}  "
      f"MaxDD {res.metrics['benchmark_max_dd']:+.2%}")
