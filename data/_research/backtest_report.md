# Playbook + Overlay Backtest Report

Window: 2021-06-04 → 2026-05-08  (258 weekly samples)

## Portfolio metrics (sum of weekly P&L)

| Metric | Baseline (all-HOLD eq-wt) | With Overlay | Edge |
|--------|--------------------------|--------------|------|
| Σ 5d  return | +52.47% | +60.31% | +7.84% |
| Σ 21d return | +231.21% | +252.45% | +21.24% |
| Max drawdown (cum 5d) | -18.72% | -14.34% | +4.38% |

Average universe forward return:  5d=+0.408%, 21d=+1.799%

## Coverage
- Weeks with **zero** fires:  23 (9%)
- **GAP weeks** (zero fires AND universe -3% 5d): 2
- **MISSED-UP weeks** (universe +3% 5d, no bullish case fired): 2

## Per-case scoreboard (sorted by fire count)

| Case | Dir | Fires | %  | univ5d-when-fired | edge-5d | hit-5d | hit-21d |
|------|-----|-------|----|-------------------|---------|--------|---------|
| volume_confirmation_breakout | UP    |  150 | 58.1% | +0.73% | +0.32% |  54% |  57% |
| banking_nim_regime_high | UP    |   80 | 31.0% | +0.83% | +0.42% |  55% |  61% |
| mf_initiation_cluster | UP    |   31 | 12.0% | +1.16% | +0.75% |  58% |  71% |
| banking_nim_regime_low | DOWN  |   28 | 10.9% | -0.43% | -0.84% |  43% |  54% |
| post_cut_cycle_continuation | UP    |   25 |  9.7% | +1.13% | +0.72% |  60% |  72% |
| brent_spike_cement_margin_squeeze | DOWN  |   23 |  8.9% | -0.11% | -0.52% |  52% |  39% |
| brent_spike_e_and_p | UP    |   20 |  7.8% | +0.76% | +0.35% |  50% |  40% |
| mf_universe_distribution_broad | DOWN  |   19 |  7.4% | -0.39% | -0.80% |  58% |  47% |
| sbp_rate_hike_shock | DOWN  |   12 |  4.7% | -0.25% | -0.65% |  50% |  42% |
| nth_rate_cut_immediate_window | ?     |    9 |  3.5% | +0.09% | -0.31% |   0% |   0% |
| behavioural_panic_3day | UP    |    8 |  3.1% | +3.37% | +2.96% |  75% | 100% |
| pkr_devaluation_shock | MIXED |    8 |  3.1% | +0.73% | +0.33% |  38% |  50% |
| us_iran_oil_spike | MIXED |    8 |  3.1% | +0.46% | +0.05% |  62% | 100% |
| imf_sba_eff_approval | UP    |    7 |  2.7% | +1.41% | +1.00% |  71% |  86% |
| mf_accumulation_strong | UP    |    5 |  1.9% | +0.60% | +0.20% |  60% |  20% |
| election_window_chop | FLAT  |    2 |  0.8% | -0.16% | -0.57% |   0% |  50% |
| imf_review_completed | UP    |    2 |  0.8% | -0.55% | -0.96% |  50% |  50% |
| circular_debt_resolution_large | UP    |    1 |  0.4% | +1.17% | +0.76% | 100% | 100% |
| narrow_breadth_low_turnover_pause | FLAT  |    1 |  0.4% | +0.01% | -0.40% | 100% | 100% |
| rate_cycle_pivot_diagnostic | FLAT  |    1 |  0.4% | +1.49% | +1.08% | 100% | 100% |
| risk_off_universe_session_pause | DOWN  |    1 |  0.4% | +12.36% | +11.95% |   0% |   0% |
| sbp_rate_cut_cycle_initiation | UP    |    1 |  0.4% | +1.49% | +1.08% | 100% | 100% |

## Sector overlay accuracy (sorted by fire count)

| Case | Sector | Action | Fires | sec-vs-univ 5d | accuracy 5d |
|------|--------|--------|-------|----------------|-------------|
| banking_nim_regime_high | Banking | upgrade_one | 560 | +0.35% |  52% |
| post_cut_cycle_continuation | Cement | upgrade_one | 125 | +1.22% |  68% |
| brent_spike_cement_margin_squeeze | Cement | downgrade_one | 115 | +0.18% |  57% |
| brent_spike_e_and_p | Oil & Gas E&P | upgrade_one |  80 | -0.37% |  40% |
| post_cut_cycle_continuation | Conglomerate | upgrade_one |  75 | -0.96% |  40% |
| nth_rate_cut_immediate_window | Banking | upgrade_one |  63 | +2.08% |  78% |
| sbp_rate_hike_shock | Cement | downgrade_one |  60 | -1.04% |  83% |
| sbp_rate_hike_shock | Conglomerate | downgrade_one |  36 | -1.29% |  67% |
| imf_sba_eff_approval | Cement | upgrade_one |  35 | +0.83% |  43% |
| pkr_devaluation_shock | Oil & Gas E&P | upgrade_one |  32 | -1.00% |  38% |
| imf_sba_eff_approval | Oil & Gas E&P | upgrade_one |  28 | +0.43% |  71% |
| imf_sba_eff_approval | Power | downgrade_one |  28 | -1.63% |  57% |
| imf_sba_eff_approval | OMC | upgrade_one |  21 | +2.49% |  71% |
| imf_review_completed | Banking | upgrade_one |  14 | +4.01% | 100% |
| sbp_rate_hike_shock | Autos | downgrade_one |  12 | +0.86% |  25% |
| pkr_devaluation_shock | Autos | downgrade_one |   8 | +1.24% |  38% |
| circular_debt_resolution_large | Banking | upgrade_one |   7 | +0.54% | 100% |
| sbp_rate_cut_cycle_initiation | Banking | downgrade_one |   7 | +3.06% |   0% |
| sbp_rate_cut_cycle_initiation | Cement | upgrade_one |   5 | -3.14% |   0% |
| circular_debt_resolution_large | Oil & Gas E&P | upgrade_one |   4 | +0.57% | 100% |
| circular_debt_resolution_large | Power | upgrade_one |   4 | -3.53% |   0% |
| risk_off_universe_session_pause | Power | downgrade_one |   4 | -4.00% | 100% |
| circular_debt_resolution_large | OMC | upgrade_one |   3 | -0.15% |   0% |
| sbp_rate_cut_cycle_initiation | Conglomerate | upgrade_one |   3 | -1.14% |   0% |
| sbp_rate_cut_cycle_initiation | Autos | upgrade_one |   1 | -2.47% |   0% |

## GAP weeks (drawdown -3% 5d, no playbook fire)

| Date | Univ 5d | Univ 21d |
|------|---------|----------|
| 2022-02-18 | -4.42% | -6.87% |
| 2025-05-02 | -6.71% | +6.46% |

## MISSED-UP weeks (rally +3% 5d, no bullish case fired)

| Date | Univ 5d | Fired (non-bullish) |
|------|---------|---------------------|
| 2024-12-20 | +5.51% | [] |
| 2024-12-27 | +3.64% | ['nth_rate_cut_immediate_window'] |
