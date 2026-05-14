# Bull-vs-Bear regime stress test (2026-05-15)

_Generated from `data/_research/backtest_per_date.json` (258 weekly samples, 2021-06-04 -> 2026-05-08)._

## Regime classification

Trailing 21d universe return, combined with the briefing's trailing-5d regime label. Strictly no look-ahead.

| Regime | Definition | Count | Share |
|---|---|---:|---:|
| BULL_strong | trailing 21d ≥ +8% | 42 | 16.3% |
| BULL_mild   | trailing 21d ≥ +3% | 47 | 18.2% |
| NEUTRAL     | -2% < trailing 21d < +3% | 109 | 42.2% |
| BEAR_recent_drop | briefing regime=CAUTION (trailing 5d ≤ -2%) | 31 | 12.0% |
| BEAR_sustained   | trailing 21d ≤ -5% OR briefing=CRISIS | 29 | 11.2% |

## Per-regime performance (forward 5d)

| Regime | n | univ% | base% | overlay% | edge vs base | edge vs univ | win-rate vs base | gross_base | gross_overlay | fires/wk |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **BULL_strong** | 42 | +1.88 | +0.92 | +0.96 | +0.03 | -0.92 | 43% | 0.50 | 0.48 | 2.1 |
| **BULL_mild** | 47 | +1.57 | +0.80 | +0.66 | -0.14 | -0.91 | 45% | 0.50 | 0.49 | 1.7 |
| **NEUTRAL** | 109 | -0.49 | -0.24 | -0.21 | +0.03 | +0.28 | 28% | 0.49 | 0.47 | 1.6 |
| **BEAR_recent_drop** | 31 | +1.25 | +0.63 | +0.55 | -0.08 | -0.70 | 45% | 0.50 | 0.44 | 1.4 |
| **BEAR_sustained** | 29 | -1.15 | -0.58 | -0.18 | +0.40 | +0.97 | 48% | 0.50 | 0.37 | 2.0 |

## Cumulative 5-year equity (start = 100)

| Track | Final | Total return | Max drawdown |
|---|---:|---:|---:|
| Baseline (all-HOLD equal weight) | 164.2 | +64.2% | 17.68% |
| Overlay  (playbook-modified)     | 178.6 | +78.6% | 13.87% |
| Universe (passive equal-weight)  | 255.9 | +155.9% | 32.72% |

## Longest sustained epochs

- **Longest BULL epoch**: 16 weeks, 2025-06-20 -> 2025-10-03. Universe +34.8%, baseline +16.2%, overlay +16.2%. Overlay cost vs baseline: **+0.0pp** (overlay forgoes some upside).
- **Longest BEAR epoch**: 7 weeks, 2026-02-13 -> 2026-03-27. Universe -19.0%, baseline -10.0%, overlay -3.0%. Overlay save vs baseline: **+7.0pp**.