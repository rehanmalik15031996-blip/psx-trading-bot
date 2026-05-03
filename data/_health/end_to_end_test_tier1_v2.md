# End-to-end model test

_Run at 2026-05-03T20:42:30_

Production-mode matcher (MF + macro) walked 313 trading dates (2024-05-01 -> 2026-04-29, ~Mon/Wed/Fri sampling). Every fired case was scored against the actual forward 5d / 21d universe returns.

## Headline accuracy

| Metric | Value |
|---|---|
| Trading dates evaluated | 313 |
| Significant moves (\|fwd_5d\| >= 4% OR \|fwd_21d\| >= 8%) | 115 |
| Dates where the matcher fired >=1 case | 275 (87.9%) |
| **HIT** (case fired AND direction matched) | 206 |
| **MISS** (case fired AND direction wrong) | 69 |
| **GAP** (significant move with NO case fired) | 9 |
| **NULL** (quiet day, no case fired -- correct) | 29 |
| Errors / replay crashes | 0 |
| **Directional precision when matcher fires** | 74.9% |
| **Recall on significant moves** | 92.2% (106/115) |

## Per-case attribution (storage-of-patterns audit)

Cases ordered by fire count. Hit-rate is for the case in isolation (NOT the verdict, which is computed at the date level).

| Case | Cat | Conf | Exp | Fired | HIT | MISS | Hit rate | Mean fwd 5d | Mean fwd 21d | Median fwd 21d |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `volume_confirmation_breakout` | behavi | MEDIUM | UP | 209 | 147 | 62 | 70% | +0.8% | +3.7% | +3.7% |
| `post_cut_cycle_continuation` | macro_ | MEDIUM | UP | 73 | 49 | 24 | 67% | +1.3% | +4.7% | +3.6% |
| `banking_nim_regime_high` | macro_ | HIGH | UP | 58 | 32 | 26 | 55% | +0.3% | +1.1% | +1.1% |
| `mf_universe_distribution_broad` | flow_r | MEDIUM | DOWN | 39 | 23 | 16 | 59% | -1.3% | -2.9% | -1.5% |
| `brent_spike_e_and_p` | sector | HIGH | UP | 35 | 22 | 13 | 63% | +0.4% | +3.4% | +6.4% |
| `mf_initiation_cluster` | flow_r | MEDIUM | UP | 26 | 26 | 0 | 100% | +1.8% | +8.7% | +7.7% |
| `imf_sba_eff_approval` | macro_ | HIGH | UP | 16 | 16 | 0 | 100% | +2.2% | +10.9% | +10.7% |
| `behavioural_panic_3day` | behavi | MEDIUM | UP | 9 | 5 | 4 | 56% | +0.8% | +2.5% | +0.8% |
| `circular_debt_resolution_large` | macro_ | MEDIUM | UP | 4 | 4 | 0 | 100% | +0.9% | +8.1% | +8.2% |
| `rate_cycle_pivot_diagnostic` | macro_ | LOW | FLAT | 4 | 2 | 2 | 50% | +3.9% | +6.2% | +5.5% |
| `sbp_rate_cut_cycle_initiation` | macro_ | HIGH | UP | 4 | 4 | 0 | 100% | +3.9% | +6.2% | +5.5% |
| `imf_review_completed` | macro_ | MEDIUM | UP | 2 | 0 | 2 | 0% | -2.9% | -6.4% | -6.4% |
| `banking_nim_regime_low` | macro_ | HIGH | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `cement_coal_shock` | sector | HIGH | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `circular_debt_worsening_large` | macro_ | MEDIUM | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `earnings_blackout_concentration` | behavi | HIGH | FLAT | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `election_window_chop` | season | MEDIUM | FLAT | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `fipi_capitulation` | flow_r | MEDIUM | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_accumulation_strong` | flow_r | MEDIUM | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_capitulation_with_value` | flow_r | LOW | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_distribution_strong` | flow_r | MEDIUM | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_smart_money_divergence` | flow_r | LOW | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `phase1_cash_in_uptrend` | behavi | MEDIUM | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `pkr_devaluation_shock` | macro_ | HIGH | MIXED | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `sbp_rate_hike_shock` | macro_ | HIGH | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |

**Orphan cases** (never fired in the year): 13 of 25
**Low-confidence cases** (1-2 fires): 1

Orphans: `banking_nim_regime_low`, `cement_coal_shock`, `circular_debt_worsening_large`, `earnings_blackout_concentration`, `election_window_chop`, `fipi_capitulation`, `mf_accumulation_strong`, `mf_capitulation_with_value`, `mf_distribution_strong`, `mf_smart_money_divergence`, `phase1_cash_in_uptrend`, `pkr_devaluation_shock`, `sbp_rate_hike_shock`

## Per-month rollup

| Month | Dates | HIT | MISS | GAP | NULL | Sig moves | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2024-05 | 14 | 12 | 2 | 0 | 0 | 0 | 86% | n/a |
| 2024-06 | 12 | 11 | 1 | 0 | 0 | 4 | 92% | 100% |
| 2024-07 | 14 | 1 | 13 | 0 | 0 | 0 | 7% | n/a |
| 2024-08 | 13 | 5 | 8 | 0 | 0 | 0 | 38% | n/a |
| 2024-09 | 13 | 11 | 2 | 0 | 0 | 2 | 85% | 100% |
| 2024-10 | 13 | 13 | 0 | 0 | 0 | 12 | 100% | 100% |
| 2024-11 | 13 | 13 | 0 | 0 | 0 | 13 | 100% | 100% |
| 2024-12 | 13 | 4 | 5 | 2 | 2 | 7 | 44% | 71% |
| 2025-01 | 14 | 3 | 10 | 0 | 1 | 2 | 23% | 100% |
| 2025-02 | 12 | 10 | 1 | 0 | 1 | 0 | 91% | n/a |
| 2025-03 | 13 | 1 | 11 | 0 | 1 | 0 | 8% | n/a |
| 2025-04 | 13 | 4 | 4 | 1 | 4 | 2 | 50% | 50% |
| 2025-05 | 13 | 10 | 0 | 2 | 1 | 4 | 100% | 50% |
| 2025-06 | 13 | 13 | 0 | 0 | 0 | 12 | 100% | 100% |
| 2025-07 | 13 | 13 | 0 | 0 | 0 | 2 | 100% | 100% |
| 2025-08 | 13 | 9 | 0 | 1 | 3 | 3 | 100% | 67% |
| 2025-09 | 13 | 8 | 2 | 0 | 3 | 3 | 80% | 100% |
| 2025-10 | 14 | 1 | 6 | 0 | 7 | 2 | 14% | 100% |
| 2025-11 | 12 | 6 | 0 | 3 | 3 | 8 | 100% | 62% |
| 2025-12 | 14 | 14 | 0 | 0 | 0 | 10 | 100% | 100% |
| 2026-01 | 13 | 12 | 1 | 0 | 0 | 5 | 92% | 100% |
| 2026-02 | 12 | 12 | 0 | 0 | 0 | 12 | 100% | 100% |
| 2026-03 | 13 | 13 | 0 | 0 | 0 | 8 | 100% | 100% |
| 2026-04 | 13 | 7 | 3 | 0 | 3 | 4 | 70% | 100% |

## 'Follow the playbook' P&L vs buy-and-hold

Strategy: default 100% long the universe; go to **cash** for the next 5 trading days when any case with expected direction DOWN or FLAT fires on the Friday close. Sampled on consecutive Fridays so windows do not overlap.

| Metric | Value |
|---|---|
| Weekly windows | 103 |
| **Buy-and-hold cumulative return** | +100.6% |
| **Playbook strategy cumulative return** | +143.1% |
| **Alpha (system - BH)** | +42.4% |
| Time invested (% of weeks long) | 86.4% |
| Cash weeks (defensive) | 14 |
| Cash weeks where market actually fell | 9 |
| System weekly hit-rate (>0 return) | 59.2% |

_Caveat: this is one path on one strategy on one slice. It is illustrative, not a significance test. Drawdown-avoidance count is the most decision-relevant number._

## LLM predictions log (per-symbol 5-day forecasts)

Direct comparison of LLM-generated 5-day predictions vs realised 5-day returns. Outcomes are produced by `scripts/check_predictions.py` and stored alongside the predictions.

| Metric | Value |
|---|---|
| Predictions in log | 132 |
| Predictions scored | 62 |
| Direction hit-rate | 69.4% |
| Mean absolute error vs mid forecast (pp) | 2.28 |

**By predicted direction:**

| Direction | n | scored | hit % | mean actual | mean |err| |
|---|---:|---:|---:|---:|---:|
| BEARISH | 39 | 21 | 76.2% | -1.16% | 2.24pp |
| BULLISH | 18 | 10 | 30.0% | -1.58% | 5.06pp |
| NEUTRAL | 75 | 31 | 77.4% | -0.36% | 1.41pp |

**By model:**

| Model | n | scored | hit % |
|---|---:|---:|---:|
| `claude-haiku-4-5` | 128 | 59 | 71.2% |
| `rule-based-v1` | 4 | 3 | 33.3% |

## Per-date breakdown

| Date | Verdict | Fwd 5d | Fwd 21d | Cases fired |
|---|---|---:|---:|---|
| 2024-05-01 | **HIT** | +1.3% | +5.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-03 | **HIT** | +2.2% | +3.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-06 | **HIT** | +2.0% | +1.9% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-08 | **HIT** | +2.6% | +1.7% | banking_nim_regime_high |
| 2024-05-10 | **MISS** | +2.1% | -1.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-13 | **MISS** | +0.7% | -1.9% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-15 | **HIT** | -0.2% | +2.2% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-17 | **HIT** | +0.9% | +3.9% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-20 | **HIT** | +0.6% | +3.3% | banking_nim_regime_high |
| 2024-05-22 | **HIT** | +0.5% | +3.9% | banking_nim_regime_high |
| 2024-05-24 | **HIT** | -0.2% | +2.5% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-27 | **HIT** | -1.0% | +3.3% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-29 | **HIT** | -0.6% | +5.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-05-31 | **HIT** | -2.2% | +5.2% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-06-03 | **HIT** | -3.0% | +5.6% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-06-05 | **HIT** | -1.8% | +9.0% | banking_nim_regime_high |
| 2024-06-07 | **HIT** | +3.1% | +8.3% | banking_nim_regime_high |
| 2024-06-10 | **HIT** | +6.6% | +9.0% | sbp_rate_cut_cycle_initiation, rate_cycle_pivot_diagnostic, banking_nim_regime_high |
| 2024-06-12 | **HIT** | +6.2% | +11.6% | sbp_rate_cut_cycle_initiation, rate_cycle_pivot_diagnostic, banking_nim_regime_high |
| 2024-06-14 | **HIT** | +1.5% | +2.1% | sbp_rate_cut_cycle_initiation, volume_confirmation_breakout, rate_cycle_pivot_diagnostic, banking_nim_regime_high |
| 2024-06-17 | **HIT** | +1.5% | +2.1% | sbp_rate_cut_cycle_initiation, volume_confirmation_breakout, rate_cycle_pivot_diagnostic, banking_nim_regime_high |
| 2024-06-19 | **HIT** | +1.5% | +2.1% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-06-21 | **HIT** | -0.5% | +0.8% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-06-24 | **HIT** | +0.6% | +0.8% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-06-26 | **HIT** | +2.7% | +1.3% | banking_nim_regime_high |
| 2024-06-28 | **MISS** | +2.7% | -1.0% | banking_nim_regime_high |
| 2024-07-01 | **MISS** | +3.4% | -1.3% | banking_nim_regime_high |
| 2024-07-03 | **MISS** | +0.7% | -4.4% | volume_confirmation_breakout, banking_nim_regime_high, brent_spike_e_and_p |
| 2024-07-05 | **MISS** | +0.0% | -4.7% | volume_confirmation_breakout, banking_nim_regime_high, brent_spike_e_and_p |
| 2024-07-08 | **MISS** | +0.3% | -4.6% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-07-10 | **MISS** | -0.6% | -4.5% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-07-12 | **MISS** | -2.0% | -4.2% | banking_nim_regime_high |
| 2024-07-15 | **MISS** | -2.4% | -5.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-07-17 | **MISS** | -2.4% | -5.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-07-19 | **MISS** | -2.9% | -4.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-07-22 | **MISS** | +0.6% | -1.2% | banking_nim_regime_high |
| 2024-07-24 | **MISS** | -2.4% | -1.6% | banking_nim_regime_high |
| 2024-07-26 | **MISS** | +0.0% | -0.2% | banking_nim_regime_high |
| 2024-07-29 | **MISS** | -3.0% | -1.7% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-07-31 | **HIT** | -1.1% | +0.9% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-02 | **MISS** | +0.0% | +0.2% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-05 | **HIT** | +0.6% | +2.3% | banking_nim_regime_high |
| 2024-08-07 | **HIT** | +0.6% | +2.7% | banking_nim_regime_high |
| 2024-08-09 | **HIT** | -1.5% | +1.3% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-12 | **HIT** | -0.4% | +1.7% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-14 | **HIT** | +0.9% | +2.4% | banking_nim_regime_high |
| 2024-08-16 | **MISS** | +1.7% | -0.7% | banking_nim_regime_high |
| 2024-08-19 | **MISS** | +1.7% | +0.1% | banking_nim_regime_high |
| 2024-08-21 | **MISS** | +0.1% | +0.3% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-23 | **MISS** | -0.0% | -1.7% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-26 | **MISS** | -0.1% | -0.3% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-08-28 | **MISS** | +0.8% | -1.0% | banking_nim_regime_high |
| 2024-08-30 | **MISS** | +0.5% | -1.2% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-09-02 | **MISS** | +0.4% | -0.7% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-09-04 | **MISS** | -0.1% | +0.3% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-09-06 | **HIT** | +0.1% | +2.4% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-09-09 | **HIT** | -2.3% | +2.7% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-09-11 | **HIT** | -1.1% | +2.9% | volume_confirmation_breakout, banking_nim_regime_high |
| 2024-09-13 | **HIT** | -1.3% | +2.4% | volume_confirmation_breakout |
| 2024-09-16 | **HIT** | +1.0% | +6.1% | volume_confirmation_breakout |
| 2024-09-18 | **HIT** | +1.2% | +4.5% | volume_confirmation_breakout |
| 2024-09-20 | **HIT** | -1.5% | +3.7% | volume_confirmation_breakout |
| 2024-09-23 | **HIT** | -1.0% | +5.3% | volume_confirmation_breakout |
| 2024-09-25 | **HIT** | -0.6% | +8.0% | imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-09-27 | **HIT** | +1.8% | +11.0% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-09-30 | **HIT** | +3.3% | +12.0% | post_cut_cycle_continuation, imf_sba_eff_approval |
| 2024-10-02 | **HIT** | +3.2% | +8.2% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-04 | **HIT** | +2.3% | +11.1% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-07 | **HIT** | +0.9% | +10.3% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout, brent_spike_e_and_p |
| 2024-10-09 | **HIT** | +1.0% | +9.2% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout, brent_spike_e_and_p |
| 2024-10-11 | **HIT** | -0.4% | +10.5% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-14 | **HIT** | +0.9% | +10.0% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-16 | **HIT** | +2.5% | +10.3% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-18 | **HIT** | +6.3% | +13.1% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-21 | **HIT** | +5.6% | +12.6% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-23 | **HIT** | +3.0% | +10.9% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-25 | **HIT** | +1.2% | +8.7% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-10-28 | **HIT** | +2.1% | +3.6% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-10-30 | **HIT** | +2.6% | +11.0% | post_cut_cycle_continuation, imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-11-01 | **HIT** | +3.1% | +13.9% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-11-04 | **HIT** | +1.7% | +13.6% | volume_confirmation_breakout |
| 2024-11-06 | **HIT** | +1.0% | +17.6% | imf_sba_eff_approval, volume_confirmation_breakout |
| 2024-11-08 | **HIT** | +1.7% | +18.1% | volume_confirmation_breakout |
| 2024-11-11 | **HIT** | +1.9% | +17.4% | volume_confirmation_breakout |
| 2024-11-13 | **HIT** | +2.7% | +22.6% | volume_confirmation_breakout |
| 2024-11-15 | **HIT** | +2.1% | +21.8% | volume_confirmation_breakout |
| 2024-11-18 | **HIT** | +2.4% | +19.9% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-11-20 | **HIT** | +2.9% | +10.3% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-11-22 | **HIT** | +4.7% | +17.1% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-11-25 | **HIT** | +6.1% | +14.9% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-11-27 | **HIT** | +7.3% | +13.1% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-11-29 | **HIT** | +7.5% | +13.2% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-02 | **HIT** | +6.9% | +12.8% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-04 | **HIT** | +6.2% | +8.9% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-06 | **HIT** | +4.8% | +3.4% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-09 | **MISS** | +4.8% | +0.4% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-11 | **MISS** | -1.3% | -1.9% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-13 | **MISS** | -4.3% | -2.0% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2024-12-16 | **MISS** | -1.7% | -3.4% | volume_confirmation_breakout |
| 2024-12-18 | **HIT** | -0.4% | +1.3% | volume_confirmation_breakout |
| 2024-12-20 | **GAP** | +5.5% | +2.8% | _(none)_ |
| 2024-12-23 | **NULL** | +1.2% | -1.9% | _(none)_ |
| 2024-12-25 | **GAP** | +4.1% | +0.2% | _(none)_ |
| 2024-12-27 | **NULL** | +3.6% | +0.0% | _(none)_ |
| 2024-12-30 | **MISS** | -1.9% | -4.5% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-01-01 | **MISS** | -4.7% | -4.7% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-01-03 | **MISS** | -4.3% | -3.7% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-01-06 | **MISS** | -1.8% | -2.3% | post_cut_cycle_continuation |
| 2025-01-08 | **MISS** | +0.5% | -2.2% | post_cut_cycle_continuation |
| 2025-01-10 | **HIT** | +1.9% | +1.3% | post_cut_cycle_continuation, brent_spike_e_and_p |
| 2025-01-13 | **MISS** | +2.2% | +0.4% | post_cut_cycle_continuation, brent_spike_e_and_p |
| 2025-01-15 | **MISS** | -0.2% | -1.2% | post_cut_cycle_continuation, brent_spike_e_and_p |
| 2025-01-17 | **MISS** | +0.6% | -1.2% | post_cut_cycle_continuation, brent_spike_e_and_p |
| 2025-01-20 | **MISS** | -1.9% | -1.3% | post_cut_cycle_continuation, volume_confirmation_breakout, brent_spike_e_and_p |
| 2025-01-22 | **MISS** | -1.6% | +0.4% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-01-24 | **HIT** | -0.5% | +0.8% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-01-27 | **HIT** | -0.3% | +1.5% | volume_confirmation_breakout |
| 2025-01-29 | **NULL** | -0.9% | +2.2% | _(none)_ |
| 2025-01-31 | **MISS** | -2.3% | -0.6% | volume_confirmation_breakout |
| 2025-02-03 | **MISS** | +0.6% | +0.2% | volume_confirmation_breakout |
| 2025-02-05 | **HIT** | +1.0% | +2.0% | volume_confirmation_breakout |
| 2025-02-07 | **NULL** | +1.5% | +4.0% | _(none)_ |
| 2025-02-10 | **HIT** | -0.3% | +2.7% | post_cut_cycle_continuation |
| 2025-02-12 | **HIT** | +0.3% | +2.2% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-02-14 | **HIT** | +1.4% | +3.7% | post_cut_cycle_continuation |
| 2025-02-17 | **HIT** | +3.5% | +5.2% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-02-19 | **HIT** | +0.9% | +4.0% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-02-21 | **HIT** | +0.1% | +2.5% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-02-24 | **HIT** | -2.3% | +1.1% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-02-26 | **HIT** | -1.5% | +2.5% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-02-28 | **HIT** | +1.7% | +4.0% | post_cut_cycle_continuation |
| 2025-03-03 | **HIT** | +2.1% | +1.1% | post_cut_cycle_continuation |
| 2025-03-05 | **MISS** | +1.6% | +0.3% | post_cut_cycle_continuation |
| 2025-03-07 | **MISS** | +0.1% | -1.3% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-03-10 | **MISS** | +1.2% | +0.4% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-03-12 | **MISS** | +3.0% | -0.5% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-03-14 | **MISS** | +1.9% | -0.5% | post_cut_cycle_continuation |
| 2025-03-17 | **MISS** | +0.3% | -0.0% | post_cut_cycle_continuation |
| 2025-03-19 | **MISS** | -0.5% | -3.2% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-03-21 | **MISS** | +0.5% | -4.3% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-03-24 | **MISS** | +1.5% | -4.5% | post_cut_cycle_continuation |
| 2025-03-26 | **MISS** | -2.3% | -7.6% | post_cut_cycle_continuation, volume_confirmation_breakout, imf_review_completed |
| 2025-03-28 | **MISS** | -3.5% | -5.2% | post_cut_cycle_continuation, imf_review_completed |
| 2025-03-31 | **NULL** | -3.5% | -5.2% | _(none)_ |
| 2025-04-02 | **NULL** | -3.5% | -5.2% | _(none)_ |
| 2025-04-04 | **MISS** | -3.3% | -5.6% | volume_confirmation_breakout |
| 2025-04-07 | **MISS** | +1.3% | -5.2% | volume_confirmation_breakout |
| 2025-04-09 | **MISS** | +0.8% | -7.8% | volume_confirmation_breakout |
| 2025-04-11 | **HIT** | +0.6% | +0.7% | volume_confirmation_breakout |
| 2025-04-14 | **MISS** | +0.5% | -0.1% | volume_confirmation_breakout |
| 2025-04-16 | **HIT** | -0.0% | +2.1% | volume_confirmation_breakout |
| 2025-04-18 | **NULL** | -1.9% | +1.3% | _(none)_ |
| 2025-04-21 | **HIT** | -4.0% | +1.8% | volume_confirmation_breakout |
| 2025-04-23 | **HIT** | -4.8% | +2.1% | volume_confirmation_breakout |
| 2025-04-25 | **NULL** | -1.1% | +4.0% | _(none)_ |
| 2025-04-28 | **NULL** | +0.0% | +5.7% | _(none)_ |
| 2025-04-30 | **GAP** | -7.7% | +8.0% | _(none)_ |
| 2025-05-02 | **GAP** | -6.7% | +6.5% | _(none)_ |
| 2025-05-05 | **NULL** | +2.2% | +7.9% | _(none)_ |
| 2025-05-07 | **GAP** | +7.4% | +12.4% | _(none)_ |
| 2025-05-09 | **HIT** | +12.4% | +17.6% | volume_confirmation_breakout, behavioural_panic_3day |
| 2025-05-12 | **HIT** | +2.7% | +5.6% | volume_confirmation_breakout |
| 2025-05-14 | **HIT** | +2.4% | +4.7% | volume_confirmation_breakout |
| 2025-05-16 | **HIT** | +0.0% | +1.5% | volume_confirmation_breakout |
| 2025-05-19 | **HIT** | -0.2% | +1.5% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-05-21 | **HIT** | -0.5% | +2.4% | post_cut_cycle_continuation, volume_confirmation_breakout |
| 2025-05-23 | **HIT** | +0.5% | +3.1% | post_cut_cycle_continuation |
| 2025-05-26 | **HIT** | +1.8% | +5.6% | post_cut_cycle_continuation |
| 2025-05-28 | **HIT** | +2.8% | +6.2% | post_cut_cycle_continuation |
| 2025-05-30 | **HIT** | +1.8% | +9.0% | post_cut_cycle_continuation |
| 2025-06-02 | **HIT** | +4.5% | +10.2% | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-06-04 | **HIT** | -0.1% | +9.2% | post_cut_cycle_continuation, volume_confirmation_breakout, mf_initiation_cluster |
| 2025-06-06 | **HIT** | +0.4% | +9.5% | post_cut_cycle_continuation, volume_confirmation_breakout, mf_initiation_cluster |
| 2025-06-09 | **HIT** | +0.4% | +9.5% | post_cut_cycle_continuation, volume_confirmation_breakout, mf_initiation_cluster |
| 2025-06-11 | **HIT** | -2.7% | +7.8% | post_cut_cycle_continuation, volume_confirmation_breakout, mf_initiation_cluster |
| 2025-06-13 | **HIT** | -1.3% | +11.5% | post_cut_cycle_continuation, volume_confirmation_breakout, brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-16 | **HIT** | -5.4% | +10.2% | brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-18 | **HIT** | +1.9% | +13.5% | brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-20 | **HIT** | +3.7% | +13.6% | brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-23 | **HIT** | +8.6% | +18.7% | behavioural_panic_3day, brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-25 | **HIT** | +5.8% | +10.7% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-06-27 | **HIT** | +5.7% | +9.4% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-06-30 | **HIT** | +5.9% | +7.4% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-02 | **HIT** | +1.8% | +4.8% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-04 | **HIT** | +1.7% | +6.0% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-07 | **HIT** | +1.9% | +5.6% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-09 | **HIT** | +1.7% | +7.5% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-11 | **HIT** | +1.8% | +6.9% | mf_initiation_cluster |
| 2025-07-14 | **HIT** | +0.5% | +5.7% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-16 | **HIT** | +1.5% | +6.0% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-18 | **HIT** | -0.1% | +7.3% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-21 | **HIT** | +0.1% | +8.1% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-23 | **HIT** | -1.1% | +6.9% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-25 | **HIT** | +1.4% | +6.8% | mf_initiation_cluster |
| 2025-07-28 | **HIT** | +2.5% | +5.8% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-07-30 | **HIT** | +5.2% | +7.7% | volume_confirmation_breakout, mf_initiation_cluster |
| 2025-08-01 | **HIT** | +2.6% | +7.1% | volume_confirmation_breakout |
| 2025-08-04 | **HIT** | +2.4% | +6.4% | volume_confirmation_breakout |
| 2025-08-06 | **HIT** | +0.5% | +6.4% | volume_confirmation_breakout |
| 2025-08-08 | **HIT** | +2.1% | +8.1% | volume_confirmation_breakout |
| 2025-08-11 | **HIT** | +2.1% | +7.8% | volume_confirmation_breakout |
| 2025-08-13 | **HIT** | +2.3% | +6.3% | volume_confirmation_breakout |
| 2025-08-15 | **NULL** | +2.2% | +7.1% | _(none)_ |
| 2025-08-18 | **HIT** | +0.7% | +6.6% | volume_confirmation_breakout |
| 2025-08-20 | **HIT** | -1.9% | +5.7% | volume_confirmation_breakout |
| 2025-08-22 | **NULL** | -0.3% | +6.8% | _(none)_ |
| 2025-08-25 | **NULL** | +1.4% | +7.4% | _(none)_ |
| 2025-08-27 | **GAP** | +3.0% | +9.4% | _(none)_ |
| 2025-08-29 | **HIT** | +3.9% | +10.9% | volume_confirmation_breakout |
| 2025-09-01 | **HIT** | +3.2% | +10.3% | volume_confirmation_breakout |
| 2025-09-03 | **HIT** | +3.6% | +11.6% | volume_confirmation_breakout |
| 2025-09-05 | **HIT** | +0.4% | +8.6% | volume_confirmation_breakout |
| 2025-09-08 | **HIT** | +0.5% | +7.0% | volume_confirmation_breakout |
| 2025-09-10 | **HIT** | -0.2% | +4.5% | volume_confirmation_breakout |
| 2025-09-12 | **NULL** | +2.6% | +2.1% | _(none)_ |
| 2025-09-15 | **NULL** | +1.7% | +5.8% | _(none)_ |
| 2025-09-17 | **NULL** | +1.9% | +4.6% | _(none)_ |
| 2025-09-19 | **HIT** | +2.8% | +3.2% | volume_confirmation_breakout |
| 2025-09-22 | **HIT** | +3.7% | +3.5% | volume_confirmation_breakout |
| 2025-09-24 | **HIT** | +3.9% | +0.8% | volume_confirmation_breakout |
| 2025-09-26 | **MISS** | +3.8% | -2.9% | volume_confirmation_breakout |
| 2025-09-29 | **MISS** | +1.9% | -5.3% | volume_confirmation_breakout |
| 2025-10-01 | **MISS** | -0.2% | -8.0% | volume_confirmation_breakout |
| 2025-10-03 | **MISS** | -3.8% | -6.8% | volume_confirmation_breakout |
| 2025-10-06 | **MISS** | -5.5% | -7.0% | volume_confirmation_breakout |
| 2025-10-08 | **NULL** | -0.1% | -7.0% | _(none)_ |
| 2025-10-10 | **NULL** | -0.4% | -4.0% | _(none)_ |
| 2025-10-13 | **MISS** | +3.5% | -3.7% | behavioural_panic_3day |
| 2025-10-15 | **MISS** | -1.0% | -6.4% | volume_confirmation_breakout |
| 2025-10-17 | **NULL** | -1.9% | -3.6% | _(none)_ |
| 2025-10-20 | **NULL** | -3.2% | -5.0% | _(none)_ |
| 2025-10-22 | **MISS** | -5.9% | -4.0% | volume_confirmation_breakout |
| 2025-10-24 | **NULL** | -1.6% | -2.0% | _(none)_ |
| 2025-10-27 | **NULL** | -0.2% | -2.0% | _(none)_ |
| 2025-10-29 | **HIT** | +0.4% | +2.9% | behavioural_panic_3day |
| 2025-10-31 | **NULL** | -1.3% | +2.8% | _(none)_ |
| 2025-11-03 | **HIT** | -1.0% | +1.7% | volume_confirmation_breakout |
| 2025-11-05 | **NULL** | -1.5% | +4.4% | _(none)_ |
| 2025-11-07 | **NULL** | +1.1% | +5.8% | _(none)_ |
| 2025-11-10 | **NULL** | +0.1% | +5.6% | _(none)_ |
| 2025-11-12 | **GAP** | +2.8% | +9.7% | _(none)_ |
| 2025-11-14 | **HIT** | +0.1% | +8.8% | volume_confirmation_breakout |
| 2025-11-17 | **HIT** | -0.5% | +8.0% | volume_confirmation_breakout |
| 2025-11-19 | **HIT** | -0.1% | +9.0% | volume_confirmation_breakout |
| 2025-11-21 | **HIT** | +2.0% | +9.8% | volume_confirmation_breakout |
| 2025-11-24 | **GAP** | +3.2% | +9.5% | _(none)_ |
| 2025-11-26 | **GAP** | +2.5% | +9.8% | _(none)_ |
| 2025-11-28 | **HIT** | +1.5% | +8.6% | volume_confirmation_breakout |
| 2025-12-01 | **HIT** | +1.5% | +7.0% | volume_confirmation_breakout |
| 2025-12-03 | **HIT** | +3.8% | +9.4% | volume_confirmation_breakout |
| 2025-12-05 | **HIT** | +4.0% | +12.3% | volume_confirmation_breakout |
| 2025-12-08 | **HIT** | +3.7% | +12.1% | volume_confirmation_breakout |
| 2025-12-10 | **HIT** | +1.6% | +8.9% | volume_confirmation_breakout |
| 2025-12-12 | **HIT** | +1.2% | +7.8% | volume_confirmation_breakout |
| 2025-12-15 | **HIT** | +0.8% | +6.6% | volume_confirmation_breakout, circular_debt_resolution_large |
| 2025-12-17 | **HIT** | +0.2% | +8.1% | volume_confirmation_breakout, circular_debt_resolution_large |
| 2025-12-19 | **HIT** | +1.2% | +9.3% | volume_confirmation_breakout, circular_debt_resolution_large |
| 2025-12-22 | **HIT** | +1.3% | +8.3% | volume_confirmation_breakout, circular_debt_resolution_large |
| 2025-12-24 | **HIT** | +2.7% | +10.2% | volume_confirmation_breakout |
| 2025-12-26 | **HIT** | +2.6% | +8.4% | volume_confirmation_breakout |
| 2025-12-29 | **HIT** | +3.6% | +7.5% | volume_confirmation_breakout |
| 2025-12-31 | **HIT** | +6.6% | +5.0% | volume_confirmation_breakout |
| 2026-01-02 | **HIT** | +3.2% | +4.3% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-05 | **HIT** | +0.9% | +3.5% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-07 | **HIT** | -1.6% | -0.8% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-09 | **HIT** | +0.7% | -0.9% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-12 | **MISS** | +2.8% | +0.7% | mf_universe_distribution_broad |
| 2026-01-14 | **HIT** | +2.6% | -1.5% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-16 | **HIT** | +2.3% | -6.1% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-19 | **HIT** | +0.4% | -4.9% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-21 | **HIT** | +0.3% | -8.7% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-23 | **HIT** | -2.0% | -13.1% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-26 | **HIT** | -1.6% | -13.9% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-01-28 | **HIT** | -0.1% | -11.5% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-01-30 | **HIT** | -1.2% | -16.1% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-02-02 | **HIT** | -1.7% | -17.3% | mf_universe_distribution_broad |
| 2026-02-04 | **HIT** | -3.8% | -17.2% | mf_universe_distribution_broad, volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-02-06 | **HIT** | -2.4% | -21.1% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-02-09 | **HIT** | -4.5% | -15.2% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-02-11 | **HIT** | -2.9% | -16.4% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-02-13 | **HIT** | -4.8% | -17.6% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-02-16 | **HIT** | -5.6% | -15.2% | mf_universe_distribution_broad |
| 2026-02-18 | **HIT** | -9.1% | -15.5% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-02-20 | **HIT** | -2.8% | -8.9% | mf_universe_distribution_broad |
| 2026-02-23 | **HIT** | -8.8% | -8.6% | mf_universe_distribution_broad, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-02-25 | **HIT** | -5.5% | -10.6% | mf_universe_distribution_broad, behavioural_panic_3day |
| 2026-02-27 | **HIT** | -6.6% | -7.3% | mf_universe_distribution_broad, volume_confirmation_breakout |
| 2026-03-02 | **HIT** | -3.4% | +0.8% | mf_universe_distribution_broad, volume_confirmation_breakout, behavioural_panic_3day |
| 2026-03-04 | **HIT** | +1.5% | -0.9% | mf_universe_distribution_broad, volume_confirmation_breakout, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-03-06 | **HIT** | -1.7% | +6.4% | mf_universe_distribution_broad, volume_confirmation_breakout, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-03-09 | **HIT** | +2.2% | +13.9% | mf_universe_distribution_broad, volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-03-11 | **HIT** | -1.7% | +3.4% | mf_universe_distribution_broad, volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-03-13 | **HIT** | -1.1% | +9.9% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-16 | **HIT** | +5.3% | +14.6% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-18 | **HIT** | -1.9% | +12.0% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-20 | **HIT** | -4.0% | +13.7% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-23 | **HIT** | -4.0% | +13.7% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-25 | **HIT** | -1.1% | +7.2% | mf_universe_distribution_broad, volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-03-27 | **HIT** | +0.3% | +11.7% | mf_universe_distribution_broad, volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-03-30 | **HIT** | +4.7% | +14.8% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-04-01 | **HIT** | +7.1% | +4.2% | mf_universe_distribution_broad, volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-04-03 | **HIT** | +11.8% | n/a | volume_confirmation_breakout, brent_spike_e_and_p |
| 2026-04-06 | **HIT** | +6.2% | n/a | brent_spike_e_and_p |
| 2026-04-08 | **HIT** | +1.7% | n/a | volume_confirmation_breakout |
| 2026-04-10 | **HIT** | +3.6% | n/a | volume_confirmation_breakout |
| 2026-04-13 | **HIT** | +6.6% | n/a | volume_confirmation_breakout |
| 2026-04-15 | **HIT** | +1.2% | n/a | volume_confirmation_breakout |
| 2026-04-17 | **MISS** | -2.5% | n/a | volume_confirmation_breakout |
| 2026-04-20 | **MISS** | -2.2% | n/a | volume_confirmation_breakout |
| 2026-04-22 | **MISS** | -3.8% | n/a | volume_confirmation_breakout |
| 2026-04-24 | **NULL** | n/a | n/a | _(none)_ |
| 2026-04-27 | **NULL** | n/a | n/a | _(none)_ |
| 2026-04-29 | **NULL** | n/a | n/a | _(none)_ |

