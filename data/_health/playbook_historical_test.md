# Historical playbook test
_As of 2026-05-02T18:20:17_

Three matcher configurations are compared on every test date so the lift from each new data layer is measurable:

1. **Baseline** — only universe-derived signals, the macro KPI parquets are masked, MF parquets are masked.
2. **With macro** — macro KPI levels (KIBOR, T-bills, CPI, FX-reserves) flow into the matcher; MF still off.
3. **With MF + macro** — production setup; both layers on.

## Named (curated)

| Mode | HIT | MISS | GAP | NULL | Precision | Recall on sig moves |
|---|---|---|---|---|---|---|
| Baseline (no macro KPIs, no MF) | 9 | 7 | 0 | 3 | 56.2% | 100.0% (7/7) |
| With macro KPIs only | 9 | 7 | 0 | 3 | 56.2% | 100.0% (7/7) |
| With MF + macro (production) | 9 | 7 | 0 | 3 | 56.2% | 100.0% (7/7) |

## MF-stress

| Mode | HIT | MISS | GAP | NULL | Precision | Recall on sig moves |
|---|---|---|---|---|---|---|
| Baseline (no macro KPIs, no MF) | 1 | 2 | 3 | 2 | 33.3% | 50.0% (3/6) |
| With macro KPIs only | 1 | 2 | 3 | 2 | 33.3% | 50.0% (3/6) |
| With MF + macro (production) | 7 | 0 | 0 | 1 | 100.0% | 100.0% (6/6) |

## Random (unbiased)

| Mode | HIT | MISS | GAP | NULL | Precision | Recall on sig moves |
|---|---|---|---|---|---|---|
| Baseline (no macro KPIs, no MF) | 6 | 5 | 0 | 14 | 54.5% | 100.0% (4/4) |
| With macro KPIs only | 6 | 5 | 0 | 14 | 54.5% | 100.0% (4/4) |
| With MF + macro (production) | 8 | 5 | 0 | 12 | 61.5% | 100.0% (4/4) |

## **Combined**

| Mode | HIT | MISS | GAP | NULL | Precision | Recall on sig moves |
|---|---|---|---|---|---|---|
| Baseline (no macro KPIs, no MF) | 16 | 14 | 3 | 19 | 53.3% | 82.4% (14/17) |
| With macro KPIs only | 16 | 14 | 3 | 19 | 53.3% | 82.4% (14/17) |
| With MF + macro (production) | 24 | 12 | 0 | 16 | 66.7% | 100.0% (17/17) |

## Per-date breakdown (production mode)

| Date | Label | Fwd 5d | Fwd 21d | Baseline / +Macro / +MF | Cases fired (production) |
|---|---|---|---|---|---|
| 2022-03-08 | Russia-Ukraine: Brent +20%/21d, KSE -8% | +1.8% | -0.0% | **MISS / MISS / MISS** | brent_spike_e_and_p |
| 2022-04-08 | Emergency 250bp SBP hike (PKR/IMF stress) | +5.0% | -3.2% | **HIT / HIT / HIT** | sbp_rate_hike_shock |
| 2022-07-12 | Brent rolling over after invasion peak | -2.4% | +6.0% | **MISS / MISS / MISS** | sbp_rate_hike_shock |
| 2023-01-30 | PKR cap removal: ~10% drop in 3 days | +3.4% | +0.4% | **MISS / MISS / MISS** | sbp_rate_hike_shock, pkr_devaluation_shock |
| 2023-03-06 | 300bp emergency hike to 20% | +1.0% | -3.3% | **HIT / HIT / HIT** | sbp_rate_hike_shock |
| 2023-06-28 | Cycle peak: 22% policy rate | +7.3% | +17.6% | **MISS / MISS / MISS** | sbp_rate_hike_shock |
| 2023-07-13 | IMF $3bn SBA approved | +0.1% | +6.0% | **HIT / HIT / HIT** | imf_sba_eff_approval, brent_spike_e_and_p |
| 2023-08-15 | Post-IMF rally + FIPI inflows | -2.8% | -8.0% | **HIT / HIT / HIT** | pkr_devaluation_shock |
| 2024-02-09 | Election week (contested results) | -5.2% | +2.6% | **HIT / HIT / HIT** | election_window_chop |
| 2024-06-11 | FIRST RATE CUT of cycle (22% -> 20.5%) | +7.6% | +11.4% | **HIT / HIT / HIT** | sbp_rate_cut_cycle_initiation |
| 2024-07-30 | Second cut (20.5% -> 19.5%) | -2.2% | -0.2% | **MISS / MISS / MISS** | nth_rate_cut_profit_taking |
| 2024-09-26 | IMF $7bn EFF + 200bp cut chain | +0.2% | +9.6% | **HIT / HIT / HIT** | post_cut_cycle_continuation, nth_rate_cut_profit_taking, imf_sba_eff_approval |
| 2024-12-17 | 5th consecutive cut: 200bp to 13% | -2.0% | -3.3% | **HIT / HIT / HIT** | nth_rate_cut_profit_taking |
| 2025-01-28 | 6th cut: 100bp to 12% | +0.4% | +2.3% | **MISS / MISS / MISS** | nth_rate_cut_profit_taking |
| 2025-05-06 | 8th cut: 100bp to 11% — bottom of cycle | +3.6% | +8.3% | **MISS / MISS / MISS** | nth_rate_cut_profit_taking |
| 2025-12-15 | Rs 1.225trn circular-debt resolution | +0.8% | +6.6% | **HIT / HIT / HIT** | sbp_rate_hike_shock, circular_debt_resolution_large |
| 2021-08-16 | CONTROL: nothing happening | +1.8% | -1.0% | **NULL / NULL / NULL** | _(none)_ |
| 2024-04-15 | CONTROL: mid-cycle quiet period | +0.9% | +3.6% | **NULL / NULL / NULL** | _(none)_ |
| 2025-09-08 | CONTROL: post-rate-cut quiet | +0.5% | +7.0% | **NULL / NULL / NULL** | _(none)_ |
| 2025-06-30 | MF: post Jun-25 AHL pub (14 new entrants vs May-25) | +5.9% | +7.4% | **GAP / GAP / HIT** | mf_initiation_cluster |
| 2025-07-17 | MF: Jun-25 AHL report publication day | -0.5% | +5.7% | **NULL / NULL / HIT** | mf_initiation_cluster |
| 2025-07-21 | MF: 1 trading day after Jun-25 publication | +0.1% | +8.1% | **GAP / GAP / HIT** | mf_initiation_cluster |
| 2025-08-04 | MF: 2 weeks after Jun-25 publication | +2.4% | +6.4% | **NULL / NULL / NULL** | _(none)_ |
| 2026-02-15 | MF: post Jan-26 AHL pub (PSO -0.9pp dist, FFC -0.8pp) | -4.8% | -17.6% | **GAP / GAP / HIT** | mf_universe_distribution_broad |
| 2026-02-19 | MF: Jan-26 AHL report publication day | -2.2% | -11.2% | **MISS / MISS / HIT** | mf_universe_distribution_broad, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-02-23 | MF: 1 trading day after Jan-26 publication | -8.8% | -8.6% | **MISS / MISS / HIT** | mf_universe_distribution_broad, behavioural_panic_3day, brent_spike_e_and_p |
| 2026-03-09 | MF: 3 weeks after Jan-26 publication | +2.2% | +13.9% | **HIT / HIT / HIT** | brent_spike_e_and_p |
| 2024-12-31 | RANDOM (Tue) | -1.5% | -4.6% | **HIT / HIT / HIT** | post_cut_cycle_continuation, nth_rate_cut_profit_taking |
| 2021-07-22 | RANDOM (Thu) | -1.3% | +0.1% | **NULL / NULL / NULL** | _(none)_ |
| 2025-07-28 | RANDOM (Mon) | +2.5% | +5.8% | **NULL / NULL / HIT** | mf_initiation_cluster |
| 2022-12-16 | RANDOM (Fri) | -3.5% | -3.7% | **NULL / NULL / NULL** | _(none)_ |
| 2022-09-01 | RANDOM (Thu) | -0.8% | -3.9% | **NULL / NULL / NULL** | _(none)_ |
| 2025-07-18 | RANDOM (Fri) | -0.1% | +7.3% | **NULL / NULL / HIT** | mf_initiation_cluster |
| 2021-12-27 | RANDOM (Mon) | +2.2% | +2.4% | **NULL / NULL / NULL** | _(none)_ |
| 2025-03-17 | RANDOM (Mon) | +0.3% | -0.0% | **MISS / MISS / MISS** | post_cut_cycle_continuation |
| 2024-06-21 | RANDOM (Fri) | -0.5% | +0.8% | **NULL / NULL / NULL** | _(none)_ |
| 2021-11-26 | RANDOM (Fri) | -2.1% | +1.2% | **HIT / HIT / HIT** | sbp_rate_hike_shock, behavioural_panic_3day |
| 2023-10-13 | RANDOM (Fri) | +1.4% | +12.1% | **HIT / HIT / HIT** | imf_review_completed |
| 2021-08-05 | RANDOM (Thu) | -1.0% | -2.1% | **NULL / NULL / NULL** | _(none)_ |
| 2021-12-09 | RANDOM (Thu) | +1.1% | +5.2% | **NULL / NULL / NULL** | _(none)_ |
| 2022-08-22 | RANDOM (Mon) | -0.7% | -4.3% | **NULL / NULL / NULL** | _(none)_ |
| 2022-09-20 | RANDOM (Tue) | +0.9% | +0.2% | **MISS / MISS / MISS** | pkr_devaluation_shock |
| 2024-10-15 | RANDOM (Tue) | +2.0% | +10.4% | **HIT / HIT / HIT** | post_cut_cycle_continuation, imf_sba_eff_approval |
| 2024-07-24 | RANDOM (Wed) | -2.4% | -1.6% | **NULL / NULL / NULL** | _(none)_ |
| 2022-07-13 | RANDOM (Wed) | -3.8% | +5.9% | **MISS / MISS / MISS** | sbp_rate_hike_shock |
| 2025-06-06 | RANDOM (Fri) | +0.4% | +9.5% | **HIT / HIT / HIT** | post_cut_cycle_continuation, mf_initiation_cluster |
| 2025-01-21 | RANDOM (Tue) | -2.1% | +0.5% | **HIT / HIT / HIT** | post_cut_cycle_continuation |
| 2025-05-07 | RANDOM (Wed) | +7.4% | +12.4% | **MISS / MISS / MISS** | nth_rate_cut_profit_taking |
| 2022-08-26 | RANDOM (Fri) | -0.1% | -3.8% | **NULL / NULL / NULL** | _(none)_ |
| 2023-12-07 | RANDOM (Thu) | +0.7% | -0.5% | **NULL / NULL / NULL** | _(none)_ |
| 2024-09-19 | RANDOM (Thu) | +0.1% | +3.2% | **MISS / MISS / MISS** | nth_rate_cut_profit_taking |
| 2022-12-22 | RANDOM (Thu) | +0.5% | -4.0% | **NULL / NULL / NULL** | _(none)_ |

## Detailed per-date

### 2022-03-08 — Russia-Ukraine: Brent +20%/21d, KSE -8%

- Fwd 5d: `0.017519146537171213`, Fwd 21d: `-0.0003443584451875857`
- Regime: `CAUTION`, Policy rate: `9.75`
- Drivers fired: `['oil_up']`
- Active events: `['brent_shock_event']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

### 2022-04-08 — Emergency 250bp SBP hike (PKR/IMF stress)

- Fwd 5d: `0.05036609691887649`, Fwd 21d: `-0.031658805601565455`
- Regime: `NORMAL`, Policy rate: `12.25`
- Drivers fired: `['rate_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

### 2022-07-12 — Brent rolling over after invasion peak

- Fwd 5d: `-0.02420278404893332`, Fwd 21d: `0.06037152750249132`
- Regime: `NORMAL`, Policy rate: `15.0`
- Drivers fired: `['rate_up', 'oil_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

### 2023-01-30 — PKR cap removal: ~10% drop in 3 days

- Fwd 5d: `0.03390769065495319`, Fwd 21d: `0.004392678093828789`
- Regime: `NORMAL`, Policy rate: `17.0`
- Drivers fired: `['rate_up', 'pkr_weak']`
- Active events: `['pkr_devaluation_event']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **MISS** on triggers ['driver:pkr_weak']

  **With macro KPIs only** — verdict `MISS`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **MISS** on triggers ['driver:pkr_weak']

  **With MF + macro (production)** — verdict `MISS`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **MISS** on triggers ['driver:pkr_weak']

### 2023-03-06 — 300bp emergency hike to 20%

- Fwd 5d: `0.009960948637380125`, Fwd 21d: `-0.03342350714341567`
- Regime: `NORMAL`, Policy rate: `20.0`
- Drivers fired: `['rate_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

### 2023-06-28 — Cycle peak: 22% policy rate

- Fwd 5d: `0.07276366250300954`, Fwd 21d: `0.17629282565815277`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `['rate_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

### 2023-07-13 — IMF $3bn SBA approved

- Fwd 5d: `0.0007176480122661631`, Fwd 21d: `0.06031612550006725`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `['rate_up', 'pkr_strong', 'oil_up']`
- Active events: `['imf_sba_or_eff_approval']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 2 analogue(s)
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:oil_up']

  **With macro KPIs only** — verdict `HIT`, 2 analogue(s)
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:oil_up']

  **With MF + macro (production)** — verdict `HIT`, 2 analogue(s)
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:oil_up']

### 2023-08-15 — Post-IMF rally + FIPI inflows

- Fwd 5d: `-0.028491427019577173`, Fwd 21d: `-0.08019531251407777`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `['pkr_weak']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **HIT** on triggers ['driver:pkr_weak']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **HIT** on triggers ['driver:pkr_weak']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **HIT** on triggers ['driver:pkr_weak']

### 2024-02-09 — Election week (contested results)

- Fwd 5d: `-0.052187735075438425`, Fwd 21d: `0.026216294329627493`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `[]`
- Active events: `['election_window']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `election_window_chop` (expected `FLAT`, score `1.3`) -> **HIT** on triggers ['event:election_window']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `election_window_chop` (expected `FLAT`, score `1.3`) -> **HIT** on triggers ['event:election_window']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `election_window_chop` (expected `FLAT`, score `1.3`) -> **HIT** on triggers ['event:election_window']

### 2024-06-11 — FIRST RATE CUT of cycle (22% -> 20.5%)

- Fwd 5d: `0.07638111981838692`, Fwd 21d: `0.11385349657578253`
- Regime: `CAUTION`, Policy rate: `20.5`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_cut_cycle_initiation` (expected `UP`, score `4.8`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:7', 'rate_cuts_180d_eq:1']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_cut_cycle_initiation` (expected `UP`, score `4.8`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:7', 'rate_cuts_180d_eq:1']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `sbp_rate_cut_cycle_initiation` (expected `UP`, score `4.8`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:7', 'rate_cuts_180d_eq:1']

### 2024-07-30 — Second cut (20.5% -> 19.5%)

- Fwd 5d: `-0.022493020705660352`, Fwd 21d: `-0.0020742205534004365`
- Regime: `NORMAL`, Policy rate: `19.5`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2024-09-26 — IMF $7bn EFF + 200bp cut chain

- Fwd 5d: `0.0022892913000903494`, Fwd 21d: `0.09550647753027193`
- Regime: `NORMAL`, Policy rate: `17.5`
- Drivers fired: `['rate_down']`
- Active events: `['imf_sba_or_eff_approval']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 3 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']

  **With macro KPIs only** — verdict `HIT`, 3 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']

  **With MF + macro (production)** — verdict `HIT`, 3 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']

### 2024-12-17 — 5th consecutive cut: 200bp to 13%

- Fwd 5d: `-0.02046765859730788`, Fwd 21d: `-0.032574789688389616`
- Regime: `NORMAL`, Policy rate: `13.0`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2025-01-28 — 6th cut: 100bp to 12%

- Fwd 5d: `0.004267348455802267`, Fwd 21d: `0.02311816694445181`
- Regime: `CAUTION`, Policy rate: `12.0`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2025-05-06 — 8th cut: 100bp to 11% — bottom of cycle

- Fwd 5d: `0.03584028450216508`, Fwd 21d: `0.08347328837845154`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2025-12-15 — Rs 1.225trn circular-debt resolution

- Fwd 5d: `0.007683314866934628`, Fwd 21d: `0.06614035977431366`
- Regime: `NORMAL`, Policy rate: `11.5`
- Drivers fired: `['rate_up', 'circular_debt_resolution']`
- Active events: `['circular_debt_resolution_event']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `circular_debt_resolution_large` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:circular_debt_resolution']

  **With macro KPIs only** — verdict `HIT`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `circular_debt_resolution_large` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:circular_debt_resolution']

  **With MF + macro (production)** — verdict `HIT`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `circular_debt_resolution_large` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:circular_debt_resolution']

### 2021-08-16 — CONTROL: nothing happening

- Fwd 5d: `0.018341510010781447`, Fwd 21d: `-0.010133835728210673`
- Regime: `NORMAL`, Policy rate: `7.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `35`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2024-04-15 — CONTROL: mid-cycle quiet period

- Fwd 5d: `0.009427575131051278`, Fwd 21d: `0.03582608092818942`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2025-09-08 — CONTROL: post-rate-cut quiet

- Fwd 5d: `0.005038334988799702`, Fwd 21d: `0.06963428506935893`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2025-06-30 — MF: post Jun-25 AHL pub (14 new entrants vs May-25)

- Fwd 5d: `0.0588338853741353`, Fwd 21d: `0.07360713275545477`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `GAP`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `GAP`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `mf_initiation_cluster` (expected `UP`, score `1.0`) -> **HIT** on triggers ['mf_n_funds_initiating_30d_gte:3']

### 2025-07-17 — MF: Jun-25 AHL report publication day

- Fwd 5d: `-0.005378736138755292`, Fwd 21d: `0.05742205024701576`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `mf_initiation_cluster` (expected `UP`, score `1.0`) -> **HIT** on triggers ['mf_n_funds_initiating_30d_gte:3']

### 2025-07-21 — MF: 1 trading day after Jun-25 publication

- Fwd 5d: `0.0006159580466514757`, Fwd 21d: `0.08114714534876964`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `['oil_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `GAP`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `GAP`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `mf_initiation_cluster` (expected `UP`, score `1.0`) -> **HIT** on triggers ['mf_n_funds_initiating_30d_gte:3']

### 2025-08-04 — MF: 2 weeks after Jun-25 publication

- Fwd 5d: `0.02419706352541945`, Fwd 21d: `0.06393072349820317`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2026-02-15 — MF: post Jan-26 AHL pub (PSO -0.9pp dist, FFC -0.8pp)

- Fwd 5d: `-0.04822484911215679`, Fwd 21d: `-0.1755309535492708`
- Regime: `CAUTION`, Policy rate: `11.5`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `GAP`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `GAP`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `mf_universe_distribution_broad` (expected `DOWN`, score `2.6`) -> **HIT** on triggers ['mf_universe_n_top_distributed_gte:5', 'mf_data_freshness_lte:60']

### 2026-02-19 — MF: Jan-26 AHL report publication day

- Fwd 5d: `-0.021648728096000255`, Fwd 21d: `-0.11241076590071983`
- Regime: `CAUTION`, Policy rate: `11.5`
- Drivers fired: `['oil_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 2 analogue(s)
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **MISS** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

  **With macro KPIs only** — verdict `MISS`, 2 analogue(s)
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **MISS** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

  **With MF + macro (production)** — verdict `HIT`, 3 analogue(s)
  - `mf_universe_distribution_broad` (expected `DOWN`, score `2.6`) -> **HIT** on triggers ['mf_universe_n_top_distributed_gte:5', 'mf_data_freshness_lte:60']
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **MISS** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

### 2026-02-23 — MF: 1 trading day after Jan-26 publication

- Fwd 5d: `-0.08768272887516204`, Fwd 21d: `-0.08555696856959147`
- Regime: `CAUTION`, Policy rate: `11.5`
- Drivers fired: `['oil_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 2 analogue(s)
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **MISS** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

  **With macro KPIs only** — verdict `MISS`, 2 analogue(s)
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **MISS** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

  **With MF + macro (production)** — verdict `HIT`, 3 analogue(s)
  - `mf_universe_distribution_broad` (expected `DOWN`, score `2.6`) -> **HIT** on triggers ['mf_universe_n_top_distributed_gte:5', 'mf_data_freshness_lte:60']
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **MISS** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **MISS** on triggers ['driver:oil_up']

### 2026-03-09 — MF: 3 weeks after Jan-26 publication

- Fwd 5d: `0.022266494717813414`, Fwd 21d: `0.1389526366381706`
- Regime: `CAUTION`, Policy rate: `11.5`
- Drivers fired: `['oil_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:oil_up']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:oil_up']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `brent_spike_e_and_p` (expected `UP`, score `1.6`) -> **HIT** on triggers ['driver:oil_up']

### 2024-12-31 — RANDOM (Tue)

- Fwd 5d: `-0.015189420803942684`, Fwd 21d: `-0.04589643719181229`
- Regime: `NORMAL`, Policy rate: `13.0`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **MISS** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **MISS** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **MISS** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **HIT** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2021-07-22 — RANDOM (Thu)

- Fwd 5d: `-0.012995128879169069`, Fwd 21d: `0.001140689859491793`
- Regime: `NORMAL`, Policy rate: `7.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `35`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2025-07-28 — RANDOM (Mon)

- Fwd 5d: `0.02475730922381505`, Fwd 21d: `0.05811731570928284`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `mf_initiation_cluster` (expected `UP`, score `1.0`) -> **HIT** on triggers ['mf_n_funds_initiating_30d_gte:3']

### 2022-12-16 — RANDOM (Fri)

- Fwd 5d: `-0.03530382679940002`, Fwd 21d: `-0.03718499426182314`
- Regime: `CAUTION`, Policy rate: `16.0`
- Drivers fired: `['rate_up', 'oil_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2022-09-01 — RANDOM (Thu)

- Fwd 5d: `-0.00791242883128814`, Fwd 21d: `-0.039021648853804713`
- Regime: `NORMAL`, Policy rate: `15.0`
- Drivers fired: `['pkr_strong']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2025-07-18 — RANDOM (Fri)

- Fwd 5d: `-0.0006212887558550676`, Fwd 21d: `0.07267585890426963`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `mf_initiation_cluster` (expected `UP`, score `1.0`) -> **HIT** on triggers ['mf_n_funds_initiating_30d_gte:3']

### 2021-12-27 — RANDOM (Mon)

- Fwd 5d: `0.021544545629175522`, Fwd 21d: `0.023699274675231287`
- Regime: `NORMAL`, Policy rate: `9.75`
- Drivers fired: `['rate_up']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2025-03-17 — RANDOM (Mon)

- Fwd 5d: `0.003344187431847797`, Fwd 21d: `-0.00043590153506218373`
- Regime: `NORMAL`, Policy rate: `12.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **MISS** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **MISS** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **MISS** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

### 2024-06-21 — RANDOM (Fri)

- Fwd 5d: `-0.005310864224936938`, Fwd 21d: `0.00848216565591504`
- Regime: `NORMAL`, Policy rate: `20.5`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2021-11-26 — RANDOM (Fri)

- Fwd 5d: `-0.02096266618871158`, Fwd 21d: `0.012090876922125487`
- Regime: `CAUTION`, Policy rate: `8.75`
- Drivers fired: `['rate_up', 'oil_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **HIT** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']

  **With macro KPIs only** — verdict `HIT`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **HIT** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']

  **With MF + macro (production)** — verdict `HIT`, 2 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']
  - `behavioural_panic_3day` (expected `UP`, score `2.3`) -> **HIT** on triggers ['universe_5d_lt:-0.05', 'breadth_lt:30']

### 2023-10-13 — RANDOM (Fri)

- Fwd 5d: `0.01387999441902092`, Fwd 21d: `0.1208804349148563`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `['pkr_strong']`
- Active events: `['imf_review_completed']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `imf_review_completed` (expected `UP`, score `1.3`) -> **HIT** on triggers ['event:imf_review_completed']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `imf_review_completed` (expected `UP`, score `1.3`) -> **HIT** on triggers ['event:imf_review_completed']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `imf_review_completed` (expected `UP`, score `1.3`) -> **HIT** on triggers ['event:imf_review_completed']

### 2021-08-05 — RANDOM (Thu)

- Fwd 5d: `-0.01027695889306022`, Fwd 21d: `-0.021164833306683256`
- Regime: `NORMAL`, Policy rate: `7.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `35`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2021-12-09 — RANDOM (Thu)

- Fwd 5d: `0.010542699773553259`, Fwd 21d: `0.05204902501773026`
- Regime: `NORMAL`, Policy rate: `8.75`
- Drivers fired: `['rate_up', 'oil_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2022-08-22 — RANDOM (Mon)

- Fwd 5d: `-0.00735355353790589`, Fwd 21d: `-0.043337632893665066`
- Regime: `NORMAL`, Policy rate: `15.0`
- Drivers fired: `['pkr_strong']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2022-09-20 — RANDOM (Tue)

- Fwd 5d: `0.00949737503512877`, Fwd 21d: `0.0021188804682755746`
- Regime: `CAUTION`, Policy rate: `15.0`
- Drivers fired: `['pkr_weak']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **MISS** on triggers ['driver:pkr_weak']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **MISS** on triggers ['driver:pkr_weak']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `pkr_devaluation_shock` (expected `MIXED`, score `1.6`) -> **MISS** on triggers ['driver:pkr_weak']

### 2024-10-15 — RANDOM (Tue)

- Fwd 5d: `0.019653144125433716`, Fwd 21d: `0.10354808575007526`
- Regime: `NORMAL`, Policy rate: `17.5`
- Drivers fired: `[]`
- Active events: `['imf_sba_or_eff_approval']`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']

  **With macro KPIs only** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']

  **With MF + macro (production)** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `imf_sba_eff_approval` (expected `UP`, score `1.8`) -> **HIT** on triggers ['event:imf_sba_or_eff_approval']

### 2024-07-24 — RANDOM (Wed)

- Fwd 5d: `-0.024153741892712997`, Fwd 21d: `-0.01590361427131418`
- Regime: `CAUTION`, Policy rate: `20.5`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2022-07-13 — RANDOM (Wed)

- Fwd 5d: `-0.038045173108828136`, Fwd 21d: `0.05948428734744907`
- Regime: `NORMAL`, Policy rate: `15.0`
- Drivers fired: `['rate_up', 'oil_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `sbp_rate_hike_shock` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_up', 'days_since_last_hike_lte:7']

### 2025-06-06 — RANDOM (Fri)

- Fwd 5d: `0.0036102193634331733`, Fwd 21d: `0.09532043851937932`
- Regime: `NORMAL`, Policy rate: `11.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

  **With MF + macro (production)** — verdict `HIT`, 2 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']
  - `mf_initiation_cluster` (expected `UP`, score `1.0`) -> **HIT** on triggers ['mf_n_funds_initiating_30d_gte:3']

### 2025-01-21 — RANDOM (Tue)

- Fwd 5d: `-0.021380045779328925`, Fwd 21d: `0.005042321112247696`
- Regime: `NORMAL`, Policy rate: `13.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `HIT`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

  **With macro KPIs only** — verdict `HIT`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

  **With MF + macro (production)** — verdict `HIT`, 1 analogue(s)
  - `post_cut_cycle_continuation` (expected `UP`, score `3.6`) -> **HIT** on triggers ['rate_cuts_180d_gte:3', 'days_since_last_cut_gte:14', 'days_since_last_cut_lte:60']

### 2025-05-07 — RANDOM (Wed)

- Fwd 5d: `0.074131718196957`, Fwd 21d: `0.12377230943578447`
- Regime: `CAUTION`, Policy rate: `11.0`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2022-08-26 — RANDOM (Fri)

- Fwd 5d: `-0.0012890146269436656`, Fwd 21d: `-0.03846957911961514`
- Regime: `NORMAL`, Policy rate: `15.0`
- Drivers fired: `['pkr_strong']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2023-12-07 — RANDOM (Thu)

- Fwd 5d: `0.006899670095843798`, Fwd 21d: `-0.005101273925438358`
- Regime: `NORMAL`, Policy rate: `22.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

### 2024-09-19 — RANDOM (Thu)

- Fwd 5d: `0.0010984203218684836`, Fwd 21d: `0.03228379046177164`
- Regime: `NORMAL`, Policy rate: `17.5`
- Drivers fired: `['rate_down']`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With macro KPIs only** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

  **With MF + macro (production)** — verdict `MISS`, 1 analogue(s)
  - `nth_rate_cut_profit_taking` (expected `DOWN`, score `3.3`) -> **MISS** on triggers ['driver:rate_down', 'days_since_last_cut_lte:21', 'rate_cuts_180d_gte:2']

### 2022-12-22 — RANDOM (Thu)

- Fwd 5d: `0.004949333371941678`, Fwd 21d: `-0.04042159853088222`
- Regime: `CAUTION`, Policy rate: `16.0`
- Drivers fired: `[]`
- Active events: `[]`
- Universe size in OHLCV: `36`

  **Baseline (no macro KPIs, no MF)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With macro KPIs only** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

  **With MF + macro (production)** — verdict `NULL`, 0 analogue(s)
  - _(no analogues matched)_

