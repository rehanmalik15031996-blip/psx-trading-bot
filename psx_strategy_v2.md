# PSX Strategy v2 — Plan D (Evidence-Based Redesign)

## Why we rebuilt

The original ML-heavy architecture (per-stock LightGBM + CatBoost + 70 features +
LLM analyst + daily 5-day binary-direction target + trades on `prob > 0.55`) was
audited in depth (`scripts/audit_*.py`) and failed on the fundamentals:

1. **Rank-averaged "probabilities" were not probabilities.** Predictions at
   nominal `P(up)=0.84` had empirical up-rate of only 57%. Predictions at
   `P(up)=0.16` had 48% — the model had near-zero discriminative power at the
   bearish tail. Entry thresholds were therefore arbitrary, and we crossed
   `0.55` on 45% of all trading days.
2. **Binary 5-day targets with 80% lag-1 autocorrelation.** Targets overlap by
   4 days, violating the iid assumption used by `TimeSeriesSplit`. Effective
   sample size was ~N/5, not N.
3. **Per-stock models on ~1000 rows and 70 features.** Severe overfit regime;
   fold-1 of the walk-forward trained on only ~170 rows.
4. **Transaction costs dominated everything.** At 40 bps round-trip × ~150
   trades/year the ML bot paid ~30% annual cost drag. Any signal weaker than
   that (all of them, once calibrated honestly) is a net loss.
5. **The reference benchmark was cherry-picked.** The +36% CAGR "equal-weight
   universe" used the backtest's sliced window (starting after the 2022 bear).
   Over the full 5-year history, equal-weight buy-and-hold on the 15-stock set
   does **+19.6% CAGR, Sharpe 0.88, MaxDD −32%** — not 36%.

## Universe (current — 35 stocks)

The bot now trades a 35-stock KSE-100-mirroring universe, expanded from the
original 15-stock set on 2026-04-30. Sector composition approximates the live
KSE-100 index:

| Sector | Count | ~Weight |
|---|---|---|
| Banking | 7 | ~22% |
| Cement | 5 | ~14% |
| Oil & Gas E&P | 4 | ~12% |
| Power | 4 | ~11% |
| Fertilizer | 3 | ~9% |
| OMC / Refining | 3 | ~9% |
| Conglomerate / Chem | 3 | ~9% |
| Technology | 2 | ~6% |
| Pharma / Consumer / Auto / Misc | 4 | ~12% |

The 7 user-required tickers (`HUBC, PABC, MLCF, OGDC, FABL, PPL, NPL`) are
locked in `config/candidates.py::REQUIRED_TICKERS`. The remaining 28 slots
were chosen to cover the index's dominant sector rotation themes
(banks for the rate-cut cycle, IPPs for the circular-debt resolution, E&Ps
for the energy crisis premium, fertilizers for the "Nitrogen Shock" lens).

Manual edits to `config/universe.py` are overwritten by the
`scripts/select_universe.py` quarterly refresh.

## Measured performance on the current 35-stock universe

Reproduce with:

```powershell
.venv\Scripts\python.exe scripts\audit_production_config.py
.venv\Scripts\python.exe scripts\audit_low_turnover.py
```

The artefacts land in `data/backtest/audit_production_35.json`.

History window: **2021-04-26 → 2026-04-29 (1240 trading rows, ~5 years).**
Cost assumption: **40 bps round-trip** (charged on each weight delta).

| Strategy | CAGR | Sharpe | Calmar | MaxDD | Turnover/yr |
|---|---|---|---|---|---|
| Equal-weight buy-and-hold | **+21.7%** | **1.04** | 0.77 | −28.2% | 0 |
| Monthly top-3 by 100d mom (no filters) | +15.8% | 0.72 | 0.37 | −42.5% | 36 |
| Monthly top-3 by 150d mom + vol<70% + market filter | +19.2% | **1.18** | **1.47** | **−13.1%** | 12 |
| **PROD: Monthly top-5 by 150d mom + vol<70% + market filter** | **+17.7%** | **1.16** | **1.27** | **−14.0%** | ~20 |
| Quarterly top-3 by 150d mom + 8% trail stop | +14.6% | 1.06 | 1.07 | −13.6% | ~16 |
| Monthly top-7 by 150d mom + market filter (no vol cap) | +22.8% | 1.36 | 1.78 | −12.8% | ~28 |

Three observations:

1. **Buy-and-hold improved from Sharpe 0.88 → 1.04** when the universe
   grew 15 → 35. The wider basket diversifies away ~4pp of MaxDD and
   captures the broader 2024-26 rally. The benchmark is now a tougher
   bar than the v1-era audit assumed.
2. **The production rule (top-5 / 150d / vol<70 / market filter) cuts
   the drawdown in half** vs B&H (−14.0% vs −28.2%) and beats it on
   risk-adjusted return (Sharpe 1.16 vs 1.04). It gives up ~4pp of CAGR
   to do that — the explicit cost of drawdown protection.
3. **A wider variant (top-7 / 150d / market filter, no vol cap)** has
   the headline-best metrics on this 5-year window (Sharpe 1.36, Calmar
   1.78) but adds ~40% more turnover and loses the "stay in calmer
   names" property that helps during PSX's emotional sell-offs. We hold
   the top-5/vol-filter rule as production until walk-forward
   re-validation shows the wider variant is robust across regimes.

## Stability tests (`scripts/audit_deep.py`)

Original v1-era findings — re-validated qualitatively on the 35-stock set:

- The production rule beats >85% of random monthly top-5 portfolios on
  Sharpe → signal is statistically real, not a fluke of the universe choice.
- Works in 2025-26 (recent regime). The 1,150 bps SBP rate-cut cycle
  drove broad-based rallies that the 150d momentum filter catches.
- Only "fails" in strong-recovery bull years — the market filter takes
  time to flip back on after a crash. **The cost of drawdown protection
  is missing some rally months. That is acceptable.**
- In 3 of 4 months where the universe dropped >8%, the rule was already
  in cash. The LLM/news overlay adds value primarily in the 4th case
  (mid-cycle shock with 150d momentum still positive).

## Phase 1 architecture

```
Monthly cycle (run on last trading day of month):
  1. Rank 35 stocks by 150-day log-return
  2. Exclude top 30% by 20-day realized volatility  (keep calmer names)
  3. If universe avg 150d return < 0 → GO TO CASH for the month
  4. Select top 5 (equal-weight) from the filtered ranking
  5. LLM regime check on the selected set (brain/overlay.py):
       → "NORMAL"  : 100% exposure (hold all 5 at 1/5 each)
       → "CAUTION" :  75% exposure
       → "CRISIS"  :  50% exposure
  6. Per-pick news check:
       → if any held stock shows a major negative catalyst, drop and go
         to cash in that slot
  7. Rebalance target weights; emit orders

Daily cycle:
  1. Mark-to-market open positions
  2. Trailing stop (DISABLED by default — parameter sweep showed any stop
     band degraded Sharpe vs pure monthly rebalance; rebalance IS the
     risk control)
  3. Check emergency-exit news feed → sell if major negative catalyst
  4. Append daily prediction & write daily report

Quarterly cycle:
  1. Re-run scripts/select_universe.py (refresh the ranked-flex slots)
  2. Re-run scripts/audit_production_config.py + audit_deep.py
  3. Re-run scripts/validate_ranker.py — re-test Phase 2 gate
```

## Files

**Live (Plan D Phase 1)**
- `brain/strategy.py` — deterministic monthly rule, momentum + vol + market filter
- `brain/overlay.py` — LLM defensive overlay (regime multiplier + emergency exits)
- `brain/backtest_v2.py` — honest end-to-end backtest
- `brain/features.py` — momentum/vol helpers also used by ranker
- `brain/paper_portfolio.py` — paper book state
- `scripts/audit_production_config.py` — live-config audit + grid search
- `scripts/audit_low_turnover.py` — broader rule grid (legacy, still useful)
- `scripts/audit_deep.py` — stability + monkey-test
- `scripts/generate_report_v2.py` — daily runner
- `scripts/phase1_backtest.py` — walk-forward harness consumed by the UI panel
- `scripts/validate_ranker.py` — Phase-2 deployment-gate check

**Layered analyst signals (used by the dashboard, the chatbot tool layer,
and the daily PDF brief)**
- `brain/valuation.py` — sector-aware fair value (DDM / P/B / 3-yr-avg P/E)
- `brain/quality.py` — ROE / leverage / earnings stability score (0-100)
- `brain/macro_impact.py` — sector-level rule book mapping macro variables
  (policy rate, Brent, USD/PKR, coal, cotton, gold, circular-debt) to
  per-sector tailwind/headwind scores, leverage-amplified per stock
- `brain/sector_ratios.py` — sector medians for relative ratio context
- `brain/earnings_calendar.py` — predicted earnings dates + 5-day blackout
- `brain/verdict_synthesizer.py` — reconciles the 7 lenses (Value / Quality /
  Momentum / Macro / News / Flow / Management) into ONE bot's verdict
  per stock with explicit conflict resolution
- `brain/short_candidates.py` — composite 0-100 short score with pre-event
  guards and regime adjustment (bearish picks for shorting / hedging)
- `brain/prediction_critic.py` — deterministic critic that downgrades or
  rewrites internally inconsistent LLM predictions before they reach disk
- `brain/buy_explainer.py` — auditable buy-side rationale block

**Phase 2 (optional, gated, currently disabled)**
- `brain/ranker.py` — cross-sectional LightGBM regressor (~14 features)
- `models/ranker_v2.pkl` — trained booster (kept for re-test cadence)
- `models/ranker_enabled.json` — last gate verdict (currently `enabled=false`)

**Archived / removed**
- `brain/_legacy/` — v1 per-stock models, backtest, risk
- `models/_legacy/*.pkl`, `*.cbm`, `metrics.json`, `economic_gate.json`
- `scripts/_legacy/`

## Realistic expectations

Over a 3-5 year horizon, with the 35-stock universe and 40 bps costs:

- **CAGR: 17-20%** (production config measured at +17.7% on 5y window)
- **Sharpe: 1.10 - 1.20**
- **Max DD: −13% to −18%** (vs B&H −28%)
- **Win rate on closed trades: ~60%**
- **~20-25 trades/year** total (monthly rebalance + occasional stop-outs)

We will **underperform buy-and-hold in strong bull years** like 2024-26
(B&H captured +21.7% vs the rule's +17.7%). That is the price of drawdown
protection. The system is designed to keep the user in the game through
crashes (2022 PKR crisis, 2023-08 shock, 2025-04 drawdown, 2026-Q1 Strait of
Hormuz selloff) where pure buy-and-hold takes 25-32% hits.

## Phase 2 (optional, gated) — NOT deployed

### Design

A single cross-sectional LightGBM regressor on stacked (date, symbol)
samples, predicting `fwd_20d_ret - cross_section_mean` using ~14 features
(momentum at 20/60/120/250d, vol regime, mean-reversion, cross-sectional
rank of mom/vol/ret, universe momentum). Used **only** to re-rank the
volatility-filtered candidate set each month.

Validated with purged walk-forward CV (5 splits, 20-day embargo to respect
the forward-return horizon).

### Deployment gate

Must beat Phase 1 by **all three**:
- CAGR: ≥ +2.0 percentage points
- MaxDD: no worse than −3 percentage points
- Sharpe: ≥ no decrease

See `scripts/validate_ranker.py` for the A/B framework. The
`validate_ranker.yml` GitHub Action re-runs this gate on a quarterly
cadence and writes the verdict to `models/ranker_enabled.json`.

### Result (2026-04-24 run, 15-stock universe)

The ranker had **zero out-of-sample predictive power** on the 15-stock set:

- Information coefficient (pooled):      **−0.0065**
- Information coefficient (daily mean):  **−0.0072** (std 0.31, n=466)

| Metric       | Phase 1 | Ranker  | Δ       |
|---|---:|---:|---:|
| CAGR         | +12.85% | +8.40%  | −4.45pp |
| Sharpe       |  +0.60  |  +0.47  | −0.14   |
| Max DD       | −21.4%  | −21.6%  | −0.15pp |
| Calmar       |  +0.60  |  +0.39  | −0.21   |

**Verdict at the time: DO NOT DEPLOY.** Re-validation on the 35-stock
universe is queued as part of the new quarterly workflow. The hypothesis
is that the wider universe (more rows × more cross-sectional dispersion
per day) may unlock real signal — but the gate is unforgiving on purpose.
Until the gate flips to PASS, **Phase 1 is the live strategy**.
