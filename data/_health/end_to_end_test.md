# End-to-end model test

_Run at 2026-05-03T01:29:05_

Production-mode matcher (MF + macro) walked 143 trading dates (2025-05-02 -> 2026-03-30, ~Mon/Wed/Fri sampling). Every fired case was scored against the actual forward 5d / 21d universe returns.

## Headline accuracy

| Metric | Value |
|---|---|
| Trading dates evaluated | 143 |
| Significant moves (\|fwd_5d\| >= 4% OR \|fwd_21d\| >= 8%) | 69 |
| Dates where the matcher fired >=1 case | 89 (62.2%) |
| **HIT** (case fired AND direction matched) | 80 |
| **MISS** (case fired AND direction wrong) | 9 |
| **GAP** (significant move with NO case fired) | 21 |
| **NULL** (quiet day, no case fired -- correct) | 33 |
| Errors / replay crashes | 0 |
| **Directional precision when matcher fires** | 89.9% |
| **Recall on significant moves** | 69.6% (48/69) |

## Per-case attribution (storage-of-patterns audit)

Cases ordered by fire count. Hit-rate is for the case in isolation (NOT the verdict, which is computed at the date level).

| Case | Cat | Conf | Exp | Fired | HIT | MISS | Hit rate | Mean fwd 5d | Mean fwd 21d | Median fwd 21d |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `mf_universe_distribution_broad` | flow_r | MEDIUM | DOWN | 38 | 23 | 15 | 61% | -1.5% | -3.1% | -3.2% |
| `circular_debt_resolution_large` | macro_ | MEDIUM | UP | 27 | 11 | 16 | 41% | +0.2% | -4.1% | -1.5% |
| `mf_initiation_cluster` | flow_r | MEDIUM | UP | 26 | 26 | 0 | 100% | +1.8% | +8.7% | +7.7% |
| `brent_spike_e_and_p` | sector | HIGH | UP | 23 | 16 | 7 | 70% | -0.6% | +4.3% | +10.2% |
| `post_cut_cycle_continuation` | macro_ | MEDIUM | UP | 12 | 12 | 0 | 100% | +0.6% | +7.1% | +8.4% |
| `behavioural_panic_3day` | behavi | MEDIUM | UP | 9 | 5 | 4 | 56% | +0.8% | +2.5% | +0.8% |
| `imf_review_completed` | macro_ | MEDIUM | UP | 9 | 0 | 9 | 0% | -0.2% | -5.7% | -6.4% |
| `sbp_rate_hike_shock` | macro_ | HIGH | DOWN | 4 | 0 | 4 | 0% | +0.9% | +8.1% | +8.2% |
| `banking_nim_regime_high` | macro_ | HIGH | ? | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `banking_nim_regime_low` | macro_ | HIGH | ? | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `cement_coal_shock` | sector | HIGH | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `circular_debt_worsening_large` | macro_ | MEDIUM | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `earnings_blackout_concentration` | behavi | HIGH | FLAT | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `election_window_chop` | season | MEDIUM | FLAT | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `fipi_capitulation` | flow_r | MEDIUM | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `imf_sba_eff_approval` | macro_ | HIGH | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_accumulation_strong` | flow_r | MEDIUM | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_capitulation_with_value` | flow_r | LOW | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_distribution_strong` | flow_r | MEDIUM | DOWN | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `mf_smart_money_divergence` | flow_r | LOW | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `phase1_cash_in_uptrend` | behavi | MEDIUM | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `pkr_devaluation_shock` | macro_ | HIGH | MIXED | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `rate_cycle_pivot_diagnostic` | macro_ | LOW | ? | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `sbp_rate_cut_cycle_initiation` | macro_ | HIGH | UP | 0 | 0 | 0 | n/a | n/a | n/a | n/a |
| `volume_confirmation_breakout` | behavi | MEDIUM | ? | 0 | 0 | 0 | n/a | n/a | n/a | n/a |

**Orphan cases** (never fired in the year): 17 of 25
**Low-confidence cases** (1-2 fires): 0

Orphans: `banking_nim_regime_high`, `banking_nim_regime_low`, `cement_coal_shock`, `circular_debt_worsening_large`, `earnings_blackout_concentration`, `election_window_chop`, `fipi_capitulation`, `imf_sba_eff_approval`, `mf_accumulation_strong`, `mf_capitulation_with_value`, `mf_distribution_strong`, `mf_smart_money_divergence`, `phase1_cash_in_uptrend`, `pkr_devaluation_shock`, `rate_cycle_pivot_diagnostic`, `sbp_rate_cut_cycle_initiation`, `volume_confirmation_breakout`

## Per-month rollup

| Month | Dates | HIT | MISS | GAP | NULL | Sig moves | Precision | Recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025-05 | 13 | 7 | 0 | 2 | 4 | 4 | 100% | 50% |
| 2025-06 | 13 | 13 | 0 | 0 | 0 | 12 | 100% | 100% |
| 2025-07 | 13 | 13 | 0 | 0 | 0 | 2 | 100% | 100% |
| 2025-08 | 13 | 0 | 0 | 3 | 10 | 3 | n/a | 0% |
| 2025-09 | 13 | 0 | 2 | 3 | 8 | 3 | 0% | 0% |
| 2025-10 | 14 | 1 | 7 | 1 | 5 | 2 | 12% | 50% |
| 2025-11 | 12 | 0 | 0 | 8 | 4 | 8 | n/a | 0% |
| 2025-12 | 14 | 8 | 0 | 4 | 2 | 10 | 100% | 60% |
| 2026-01 | 13 | 13 | 0 | 0 | 0 | 5 | 100% | 100% |
| 2026-02 | 12 | 12 | 0 | 0 | 0 | 12 | 100% | 100% |
| 2026-03 | 13 | 13 | 0 | 0 | 0 | 8 | 100% | 100% |

## LLM predictions log (per-symbol 5-day forecasts)

Direct comparison of LLM-generated 5-day predictions vs realised 5-day returns. Outcomes are produced by `scripts/check_predictions.py` and stored alongside the predictions.

| Metric | Value |
|---|---|
| Predictions in log | 62 |
| Predictions scored | 46 |
| Direction hit-rate | 73.9% |
| Mean absolute error vs mid forecast (pp) | 2.00 |

**By predicted direction:**

| Direction | n | scored | hit % | mean actual | mean |err| |
|---|---:|---:|---:|---:|---:|
| BEARISH | 21 | 16 | 75.0% | -1.22% | 2.14pp |
| BULLISH | 10 | 6 | 33.3% | -1.38% | 4.72pp |
| NEUTRAL | 31 | 24 | 83.3% | +0.04% | 1.23pp |

**By model:**

| Model | n | scored | hit % |
|---|---:|---:|---:|
| `claude-haiku-4-5` | 59 | 44 | 77.3% |
| `rule-based-v1` | 3 | 2 | 0.0% |

## Per-date breakdown

| Date | Verdict | Fwd 5d | Fwd 21d | Cases fired |
|---|---|---:|---:|---|
| 2025-05-02 | **GAP** | -6.7% | +6.5% | _(none)_ |
| 2025-05-05 | **NULL** | +2.2% | +7.9% | _(none)_ |
| 2025-05-07 | **GAP** | +7.4% | +12.4% | _(none)_ |
| 2025-05-09 | **HIT** | +12.4% | +17.6% | behavioural_panic_3day |
| 2025-05-12 | **NULL** | +2.7% | +5.6% | _(none)_ |
| 2025-05-14 | **NULL** | +2.4% | +4.7% | _(none)_ |
| 2025-05-16 | **NULL** | +0.0% | +1.5% | _(none)_ |
| 2025-05-19 | **HIT** | -0.2% | +1.5% | post_cut_cycle_continuation |
| 2025-05-21 | **HIT** | -0.5% | +2.4% | post_cut_cycle_continuation |
| 2025-05-23 | **HIT** | +0.5% | +3.1% | post_cut_cycle_continuation |
| 2025-05-26 | **HIT** | +1.8% | +5.6% | post_cut_cycle_continuation |
| 2025-05-28 | **HIT** | +2.8% | +6.2% | post_cut_cycle_continuation |
| 2025-05-30 | **HIT** | +1.8% | +9.0% | post_cut_cycle_continuation |
| 2025-06-02 | **HIT** | +4.5% | +10.2% | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-06-04 | **HIT** | -0.1% | +9.2% | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-06-06 | **HIT** | +0.4% | +9.5% | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-06-09 | **HIT** | +0.4% | +9.5% | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-06-11 | **HIT** | -2.7% | +7.8% | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-06-13 | **HIT** | -1.3% | +11.5% | post_cut_cycle_continuation, brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-16 | **HIT** | -5.4% | +10.2% | brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-18 | **HIT** | +1.9% | +13.5% | brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-20 | **HIT** | +3.7% | +13.6% | brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-23 | **HIT** | +8.6% | +18.7% | behavioural_panic_3day, brent_spike_e_and_p, mf_initiation_cluster |
| 2025-06-25 | **HIT** | +5.8% | +10.7% | mf_initiation_cluster |
| 2025-06-27 | **HIT** | +5.7% | +9.4% | mf_initiation_cluster |
| 2025-06-30 | **HIT** | +5.9% | +7.4% | mf_initiation_cluster |
| 2025-07-02 | **HIT** | +1.8% | +4.8% | mf_initiation_cluster |
| 2025-07-04 | **HIT** | +1.7% | +6.0% | mf_initiation_cluster |
| 2025-07-07 | **HIT** | +1.9% | +5.6% | mf_initiation_cluster |
| 2025-07-09 | **HIT** | +1.7% | +7.5% | mf_initiation_cluster |
| 2025-07-11 | **HIT** | +1.8% | +6.9% | mf_initiation_cluster |
| 2025-07-14 | **HIT** | +0.5% | +5.7% | mf_initiation_cluster |
| 2025-07-16 | **HIT** | +1.5% | +6.0% | mf_initiation_cluster |
| 2025-07-18 | **HIT** | -0.1% | +7.3% | mf_initiation_cluster |
| 2025-07-21 | **HIT** | +0.1% | +8.1% | mf_initiation_cluster |
| 2025-07-23 | **HIT** | -1.1% | +6.9% | mf_initiation_cluster |
| 2025-07-25 | **HIT** | +1.4% | +6.8% | mf_initiation_cluster |
| 2025-07-28 | **HIT** | +2.5% | +5.8% | mf_initiation_cluster |
| 2025-07-30 | **HIT** | +5.2% | +7.7% | mf_initiation_cluster |
| 2025-08-01 | **NULL** | +2.6% | +7.1% | _(none)_ |
| 2025-08-04 | **NULL** | +2.4% | +6.4% | _(none)_ |
| 2025-08-06 | **NULL** | +0.5% | +6.4% | _(none)_ |
| 2025-08-08 | **GAP** | +2.1% | +8.1% | _(none)_ |
| 2025-08-11 | **NULL** | +2.1% | +7.8% | _(none)_ |
| 2025-08-13 | **NULL** | +2.3% | +6.3% | _(none)_ |
| 2025-08-15 | **NULL** | +2.2% | +7.1% | _(none)_ |
| 2025-08-18 | **NULL** | +0.7% | +6.6% | _(none)_ |
| 2025-08-20 | **NULL** | -1.9% | +5.7% | _(none)_ |
| 2025-08-22 | **NULL** | -0.3% | +6.8% | _(none)_ |
| 2025-08-25 | **NULL** | +1.4% | +7.4% | _(none)_ |
| 2025-08-27 | **GAP** | +3.0% | +9.4% | _(none)_ |
| 2025-08-29 | **GAP** | +3.9% | +10.9% | _(none)_ |
| 2025-09-01 | **GAP** | +3.2% | +10.3% | _(none)_ |
| 2025-09-03 | **GAP** | +3.6% | +11.6% | _(none)_ |
| 2025-09-05 | **GAP** | +0.4% | +8.6% | _(none)_ |
| 2025-09-08 | **NULL** | +0.5% | +7.0% | _(none)_ |
| 2025-09-10 | **NULL** | -0.2% | +4.5% | _(none)_ |
| 2025-09-12 | **NULL** | +2.6% | +2.1% | _(none)_ |
| 2025-09-15 | **NULL** | +1.7% | +5.8% | _(none)_ |
| 2025-09-17 | **NULL** | +1.9% | +4.6% | _(none)_ |
| 2025-09-19 | **NULL** | +2.8% | +3.2% | _(none)_ |
| 2025-09-22 | **NULL** | +3.7% | +3.5% | _(none)_ |
| 2025-09-24 | **NULL** | +3.9% | +0.8% | _(none)_ |
| 2025-09-26 | **MISS** | +3.8% | -2.9% | imf_review_completed |
| 2025-09-29 | **MISS** | +1.9% | -5.3% | imf_review_completed |
| 2025-10-01 | **MISS** | -0.2% | -8.0% | imf_review_completed |
| 2025-10-03 | **MISS** | -3.8% | -6.8% | imf_review_completed |
| 2025-10-06 | **MISS** | -5.5% | -7.0% | imf_review_completed |
| 2025-10-08 | **MISS** | -0.1% | -7.0% | imf_review_completed |
| 2025-10-10 | **MISS** | -0.4% | -4.0% | imf_review_completed |
| 2025-10-13 | **MISS** | +3.5% | -3.7% | behavioural_panic_3day, imf_review_completed |
| 2025-10-15 | **MISS** | -1.0% | -6.4% | imf_review_completed |
| 2025-10-17 | **NULL** | -1.9% | -3.6% | _(none)_ |
| 2025-10-20 | **NULL** | -3.2% | -5.0% | _(none)_ |
| 2025-10-22 | **GAP** | -5.9% | -4.0% | _(none)_ |
| 2025-10-24 | **NULL** | -1.6% | -2.0% | _(none)_ |
| 2025-10-27 | **NULL** | -0.2% | -2.0% | _(none)_ |
| 2025-10-29 | **HIT** | +0.4% | +2.9% | behavioural_panic_3day |
| 2025-10-31 | **NULL** | -1.3% | +2.8% | _(none)_ |
| 2025-11-03 | **NULL** | -1.0% | +1.7% | _(none)_ |
| 2025-11-05 | **NULL** | -1.5% | +4.4% | _(none)_ |
| 2025-11-07 | **NULL** | +1.1% | +5.8% | _(none)_ |
| 2025-11-10 | **NULL** | +0.1% | +5.6% | _(none)_ |
| 2025-11-12 | **GAP** | +2.8% | +9.7% | _(none)_ |
| 2025-11-14 | **GAP** | +0.1% | +8.8% | _(none)_ |
| 2025-11-17 | **GAP** | -0.5% | +8.0% | _(none)_ |
| 2025-11-19 | **GAP** | -0.1% | +9.0% | _(none)_ |
| 2025-11-21 | **GAP** | +2.0% | +9.8% | _(none)_ |
| 2025-11-24 | **GAP** | +3.2% | +9.5% | _(none)_ |
| 2025-11-26 | **GAP** | +2.5% | +9.8% | _(none)_ |
| 2025-11-28 | **GAP** | +1.5% | +8.6% | _(none)_ |
| 2025-12-01 | **NULL** | +1.5% | +7.0% | _(none)_ |
| 2025-12-03 | **GAP** | +3.8% | +9.4% | _(none)_ |
| 2025-12-05 | **GAP** | +4.0% | +12.3% | _(none)_ |
| 2025-12-08 | **GAP** | +3.7% | +12.1% | _(none)_ |
| 2025-12-10 | **GAP** | +1.6% | +8.9% | _(none)_ |
| 2025-12-12 | **NULL** | +1.2% | +7.8% | _(none)_ |
| 2025-12-15 | **HIT** | +0.8% | +6.6% | sbp_rate_hike_shock, circular_debt_resolution_large |
| 2025-12-17 | **HIT** | +0.2% | +8.1% | sbp_rate_hike_shock, circular_debt_resolution_large |
| 2025-12-19 | **HIT** | +1.2% | +9.3% | sbp_rate_hike_shock, circular_debt_resolution_large |
| 2025-12-22 | **HIT** | +1.3% | +8.3% | sbp_rate_hike_shock, circular_debt_resolution_large |
| 2025-12-24 | **HIT** | +2.7% | +10.2% | circular_debt_resolution_large |
| 2025-12-26 | **HIT** | +2.6% | +8.4% | circular_debt_resolution_large |
| 2025-12-29 | **HIT** | +3.6% | +7.5% | circular_debt_resolution_large |
| 2025-12-31 | **HIT** | +6.6% | +5.0% | circular_debt_resolution_large |
| 2026-01-02 | **HIT** | +3.2% | +4.3% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-05 | **HIT** | +0.9% | +3.5% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-07 | **HIT** | -1.6% | -0.8% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-09 | **HIT** | +0.7% | -0.9% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-12 | **HIT** | +2.8% | +0.7% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-14 | **HIT** | +2.6% | -1.5% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-16 | **HIT** | +2.3% | -6.1% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-19 | **HIT** | +0.4% | -4.9% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-21 | **HIT** | +0.3% | -8.7% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-23 | **HIT** | -2.0% | -13.1% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-26 | **HIT** | -1.6% | -13.9% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-01-28 | **HIT** | -0.1% | -11.5% | mf_universe_distribution_broad, brent_spike_e_and_p, circular_debt_resolution_large |
| 2026-01-30 | **HIT** | -1.2% | -16.1% | mf_universe_distribution_broad, brent_spike_e_and_p, circular_debt_resolution_large |
| 2026-02-02 | **HIT** | -1.7% | -17.3% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-02-04 | **HIT** | -3.8% | -17.2% | mf_universe_distribution_broad, brent_spike_e_and_p, circular_debt_resolution_large |
| 2026-02-06 | **HIT** | -2.4% | -21.1% | mf_universe_distribution_broad, brent_spike_e_and_p, circular_debt_resolution_large |
| 2026-02-09 | **HIT** | -4.5% | -15.2% | mf_universe_distribution_broad, brent_spike_e_and_p, circular_debt_resolution_large |
| 2026-02-11 | **HIT** | -2.9% | -16.4% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-02-13 | **HIT** | -4.8% | -17.6% | mf_universe_distribution_broad, circular_debt_resolution_large |
| 2026-02-16 | **HIT** | -5.6% | -15.2% | mf_universe_distribution_broad |
| 2026-02-18 | **HIT** | -9.1% | -15.5% | mf_universe_distribution_broad |
| 2026-02-20 | **HIT** | -2.8% | -8.9% | mf_universe_distribution_broad |
| 2026-02-23 | **HIT** | -8.8% | -8.6% | mf_universe_distribution_broad, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-02-25 | **HIT** | -5.5% | -10.6% | mf_universe_distribution_broad, behavioural_panic_3day |
| 2026-02-27 | **HIT** | -6.6% | -7.3% | mf_universe_distribution_broad |
| 2026-03-02 | **HIT** | -3.4% | +0.8% | mf_universe_distribution_broad, behavioural_panic_3day |
| 2026-03-04 | **HIT** | +1.5% | -0.9% | mf_universe_distribution_broad, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-03-06 | **HIT** | -1.7% | +6.4% | mf_universe_distribution_broad, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-03-09 | **HIT** | +2.2% | +13.9% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-11 | **HIT** | -1.7% | +3.4% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-13 | **HIT** | -1.1% | +9.9% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-16 | **HIT** | +5.3% | +14.6% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-18 | **HIT** | -1.9% | +12.0% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-20 | **HIT** | -4.0% | +13.7% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-23 | **HIT** | -4.0% | +13.7% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-25 | **HIT** | -1.1% | +7.2% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-27 | **HIT** | +0.3% | +11.4% | mf_universe_distribution_broad, brent_spike_e_and_p |
| 2026-03-30 | **HIT** | +4.7% | +14.5% | mf_universe_distribution_broad, brent_spike_e_and_p |

