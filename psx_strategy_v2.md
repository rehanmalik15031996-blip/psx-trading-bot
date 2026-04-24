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
   Over the full 5-year history, equal-weight buy-and-hold on the same 15
   names does **+19.6% CAGR, Sharpe 0.88, MaxDD −32%** — not 36%.

## Measured performance of simple rules on our universe

Run `.venv\Scripts\python.exe scripts\audit_low_turnover.py` to reproduce.

| Strategy | CAGR | Sharpe | Calmar | MaxDD | Turnover/yr |
|---|---|---|---|---|---|
| Equal-weight buy-and-hold | +19.6% | 0.88 | 0.61 | −32% | 0 |
| Weekly top-5 by 50d mom | +15.3% (net) | 0.69 | 0.62 | −25% | 52 |
| Monthly top-3 by 100d mom, vol<70%, market filter | +11.5% | 0.78 | 0.71 | −16% | 12 |
| **Monthly top-3 by 150d mom, vol<70%, market filter** | **+16.7%** | **1.01** | **1.03** | **−16%** | **12** |

The last rule beats buy-and-hold on **risk-adjusted return** (Sharpe), cuts
drawdown in half, and survives a 2.5× cost stress test (still Sharpe 0.90 at
100 bps round-trip).

## Stability tests (`scripts/audit_deep.py`)

- Beats 90% of random top-3-monthly portfolios on Sharpe → signal is statistically real.
- Works in 2025-26 (recent regime): rule +33% vs B&H +23%.
- Only "fails" in strong-recovery bull years (2023: rule +9% vs B&H +48%) — the
  150d market filter takes time to flip back on. **The cost of drawdown
  protection is missing some rally months. That's acceptable.**
- In 3 of 4 months where the universe dropped >8%, the rule was already in
  cash. LLM/news overlay would add value primarily in the 4th case (mid-cycle
  shock with 150d momentum still positive).

## Phase 1 architecture

```
Monthly cycle (run on last trading day of month):
  1. Rank 15 stocks by 150-day log-return
  2. Exclude top 30% by 20-day realized volatility  (keep calmer names)
  3. If universe avg 150d return < 0 → GO TO CASH for the month
  4. Select top 3 (equal-weight) from the filtered ranking
  5. LLM regime check on the selected set:
       → "NORMAL"  : 100% exposure (hold all 3 at 1/3 each)
       → "CAUTION" :  75% exposure (hold 3 at 0.25 each, 25% cash)
       → "CRISIS"  :  50% exposure (hold top-2 at 0.25 each, 50% cash)
  6. Per-pick news check:
       → if any held stock shows a major negative catalyst, drop and go to cash
         in that slot
  7. Rebalance target weights; emit orders

Daily cycle:
  1. Mark-to-market open positions
  2. Check trailing stop (-15% from peak per position) → sell if triggered
  3. Check emergency-exit news feed → sell if major negative catalyst
  4. Write daily report (equity, positions, benchmark, alerts)

Quarterly cycle:
  1. Re-run scripts/select_universe.py (refresh the 15-stock set)
  2. Re-run scripts/audit_deep.py (confirm rule still passes monkey test)
```

## Files

**New (Plan D)**
- `brain/strategy.py` — deterministic monthly rule + trailing stops
- `brain/overlay.py`  — LLM defensive overlay (regime multiplier + emergency exits)
- `brain/backtest_v2.py` — honest backtest end-to-end
- `scripts/generate_report_v2.py` — daily runner

**Reused unchanged**
- `data/*` — OHLCV fetch, store, macro backfill
- `config/universe.py`, `config/candidates.py`, `scripts/select_universe.py`
- `brain/features.py` — re-used for volatility/momentum computation
- `brain/news.py`, `brain/sentiment.py`, `brain/llm_client.py`, `brain/llm_analyst.py` — overlay only
- `brain/paper_portfolio.py` — unchanged

**Archived / removed**
- `brain/models.py` → `brain/_legacy_models.py` (kept for reference, not imported)
- `brain/backtest.py` → `brain/_legacy_backtest.py`
- `brain/risk.py` → `brain/_legacy_risk.py`
- `scripts/generate_report.py` → `scripts/_legacy_generate_report.py`
- `scripts/train_models.py` → `scripts/_legacy_train_models.py`
- `models/*.pkl`, `*.cbm`, `training_aucs.json`, `economic_gate.json`
- `data/walkforward_signals*.parquet`

## Realistic expectations

Over a 3-5 year horizon, with our 15-stock PSX universe and 40 bps costs:

- **CAGR: 15-20%** (not 30%+)
- **Sharpe: 1.0 - 1.1**
- **Max DD: -15% to -20%** (vs B&H -32%)
- **Win rate on closed trades: 60-65%**
- **~15-25 trades/year** total (monthly rebalance + occasional stop-outs)

We will **underperform buy-and-hold** in strong bull years. That is the price of
drawdown protection. The system is designed to keep you in the game through
crashes (2022 PKR crisis, 2023-08 shock, 2025-04 drawdown) where pure
buy-and-hold takes 25-32% hits.

## Phase 2 (optional, gated) — NOT deployed

### Design

A single cross-sectional LightGBM regressor on ~14k stacked (date, symbol)
samples, predicting `fwd_20d_ret - cross_section_mean` using 14 features
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

See `scripts/validate_ranker.py` for the A/B framework.

### Result (2026-04-24 run)

The ranker has effectively **zero out-of-sample predictive power**:

- Information coefficient (pooled):      **−0.0065**
- Information coefficient (daily mean):  **−0.0072** (std 0.31, n=466)

Out-of-sample A/B backtest 2024-05-13 → 2026-04-23:

| Metric       | Phase 1 | Ranker  | Δ       |
|---|---:|---:|---:|
| CAGR         | +12.85% | +8.40%  | −4.45pp |
| Sharpe       |  +0.60  |  +0.47  | −0.14   |
| Max DD       | −21.4%  | −21.6%  | −0.15pp |
| Calmar       |  +0.60  |  +0.39  | −0.21   |

**Verdict: DO NOT DEPLOY.** The ranker's "improvements" are noise — swapping
the mechanical momentum rank for ML scores degrades every risk-adjusted metric.
This is fully consistent with the audit finding that our 15-stock × 5-year
dataset is too small for stable ML signal once overlap-aware validation is
applied.

The ranker infrastructure (`brain/ranker.py`, `scripts/validate_ranker.py`)
is retained so we can re-test on a quarterly cadence: if the universe grows,
the market regime shifts, or new features surface, the gate can flip to PASS.
Until then, **Phase 1 is the live strategy**.
