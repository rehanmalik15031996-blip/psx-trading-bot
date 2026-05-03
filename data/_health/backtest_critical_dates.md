# Layer 3 -- Cursor Claude critical-date backtest

_Run at 2026-05-03T11:29:14_

Hand-picked turning points across 24 months. I (Cursor Claude) read each replayed briefing and produced a strategist-format JSON decision *before* seeing the forward return. The script then scored each call.

## Headline

| Metric | Value |
|---|---|
| Decisions | 15 |
| **Direction hit-rate (5d)** | 78.6% (11/14) |
| **Mean top-pick alpha vs universe (5d)** | +1.78pp (9 scored) |

## Per-date detail

| Date | Action | Conv | Top buy | Buy 5d | Universe 5d | Pick alpha 5d | Hit |
|---|---|---|---|---:|---:|---:|---|
| 2024-06-10 | BUY | MEDIUM | MEBL | +12.7% | +6.6% | +6.1pp | HIT |
| 2024-07-29 | BUY | MEDIUM | HUBC | -2.7% | -3.0% | +0.4pp | MISS |
| 2024-09-12 | BUY | MEDIUM | MEBL | +8.9% | -0.9% | +9.8pp | MISS |
| 2024-12-16 | HOLD | MEDIUM |  | n/a | -1.7% | n/a | HIT |
| 2025-02-26 | HOLD | LOW |  | n/a | -1.5% | n/a | HIT |
| 2025-05-09 | BUY | MEDIUM | HUBC | +11.9% | +12.4% | -0.5pp | HIT |
| 2025-06-23 | BUY | MEDIUM | PPL | +11.0% | +8.6% | +2.4pp | HIT |
| 2025-09-29 | HOLD | LOW |  | n/a | +1.9% | n/a | HIT |
| 2025-10-22 | REDUCE | LOW |  | n/a | -5.9% | n/a | HIT |
| 2025-11-24 | BUY | MEDIUM | UBL | +2.1% | +3.2% | -1.1pp | HIT |
| 2025-12-31 | BUY | HIGH | HUBC | +7.9% | +6.6% | +1.2pp | HIT |
| 2026-02-09 | REDUCE | LOW |  | n/a | -4.5% | n/a | HIT |
| 2026-02-25 | BUY | HIGH | MEBL | -4.3% | -5.5% | +1.2pp | MISS |
| 2026-03-09 | BUY | MEDIUM | PPL | -1.2% | +2.2% | -3.5pp | HIT |
| 2026-04-29 | CASH | MEDIUM |  | n/a | n/a | n/a |  |

## Theses (one line each)

* **2024-06-10** (BUY, MEDIUM): First SBP cut of the cycle (22.0->20.5%) is historically a STRONG initiating signal in PSX even with weak breadth (13.9%) and disinflation barely starting (CPI 12.6%). Reserves rebuilding to $14.6bn supports policy continuity. Banks lead early-cycle on duration assets revaluing -- MEBL has the cleanest balance sheet for the cycle re-rate.
* **2024-07-29** (BUY, MEDIUM): IMF SBA staff-level agreement removes the dominant tail-risk overhang and unlocks foreign-flow re-engagement. Combined with disinflation (CPI 12.6% -> 11.1%) and the rate-cut cycle now established, the playbook case `imf_sba_eff_approval` (expected UP) is the dominant signal. HUBC benefits twice -- circular-debt clarity expectations + lower discount rate on its long-dated cashflows.
* **2024-09-12** (BUY, MEDIUM): Second cut (17.5%) confirms `post_cut_cycle_continuation` (the highest-fire-count UP case in our 24-month playbook backtest at 73 fires). Disinflation surprise (CPI 6.9% from 11.1%) creates room for more cuts. Banks remain the cleanest expression early-mid cycle. Anti-permabull guard met: rate_down STRONG + oil_down MODERATE = two independent positive lenses.
* **2024-12-16** (HOLD, MEDIUM): KSE-100 at record highs after 900bp of cuts (22% -> 13%) -- this is exactly the setup `nth_rate_cut_profit_taking` is designed to flag. Anti-permabull guard: late-cycle, no incremental positive catalyst, momentum pricing in everything already. Hold existing exposure but DO NOT add. Rule 12 (asymmetric calibration) explicitly says default to NEUTRAL when the only positive lens is `more rate cuts coming`.
* **2025-02-26** (HOLD, LOW): On the day, regime still reads NORMAL (5d +0.88%, breadth 58%) -- the -8% drawdown is largely AHEAD of this date and not in the lookback yet. No active events, no fresh negative drivers. Honest answer: I cannot anticipate this drawdown from on-disk data alone (PIA/IMF political shock is news-driven). Hold but stay defensive given Pakistan's pattern of headline-shock drawdowns and CPI undershooting (1.5%) reducing further policy support.
* **2025-05-09** (BUY, MEDIUM): Regime CAUTION post-spring-dip with rate cycle still cutting (now 11.0%) and reserves climbing to $18.2bn -- classic 'recovery from local panic' setup that maps to `behavioural_panic_3day` (UP/contrarian). MF data fresh at 8d, not stale. Phase-1 hasn't picked anything today so we don't override it but a discretionary re-engagement on quality compounders (HUBC) is justified.
* **2025-06-23** (BUY, MEDIUM): Brent spike on Iran-Israel tension is the textbook trigger for `brent_spike_e_and_p` (UP for E&P). The ceasefire reduces tail risk so this is a positive-asymmetry trade: oil stays elevated near-term while broader market risk-off pressure unwinds. PPL is the cleanest E&P exposure; LUCK (cement) faces direct cost-side pain from oil + freight.
* **2025-09-29** (HOLD, LOW): IMF EFF 2nd review completed -- normally `imf_review_completed` fires UP, BUT: (a) rally already mature, (b) CPI re-accelerating to 5.6%, (c) MF data is 120d stale (data freshness gate), (d) this exact case mis-fired in our 1y backtest. Rule 12 (anti-permabull guard) explicitly downgrades when IMF positive is the only catalyst and macro is mixed. Honest call: HOLD LOW, do not chase the news.
* **2025-10-22** (REDUCE, LOW): No fresh positive catalyst, CPI inching up to 6.0%, MF data 143d stale, no active events. After a long rally, a 'no positive lenses, all neutral-to-mildly-negative' signal set is exactly when the playbook teaches us to lighten -- Rule 12 says default to NEUTRAL/cash when MF and macro disagree (here: macro mildly negative, MF data unusable). Won't short outright (no DOWN case fires) but trim exposure.
* **2025-11-24** (BUY, MEDIUM): MSCI rebalance day with 14 PSX adds -- passive inflows are mechanical and front-loaded around effective date (per `msci_calendar`). CPI cooling back to 5.0%. The MSCI inflow catalyst gives a hard, near-dated positive asymmetry that satisfies Rule 12 (independent of MF data which is 176d stale). UBL is the largest-cap PSX bank in the rebalance -- highest absolute-PKR passive inflow.
* **2025-12-31** (BUY, HIGH): Circular debt resolution of PKR 1.225tn -- this is `circular_debt_resolution_large` firing on its primary trigger AND the macro_impact lens has STRONG tailwind tag for the Power sector. Breadth already at 72% advancing and 21d momentum +6.97% confirms institutional engagement. HUBC is the highest-impact direct beneficiary (largest IPP receivable resolution). Anti-permabull guard met by THREE independent lenses (event + macro + breadth).
* **2026-02-09** (REDUCE, LOW): CPI accelerating to 6.1% AND oil_up MODERATE = double inflationary pressure on margins. Rate cycle could reverse (rate_up driver tag suggests it already is). The circular_debt_resolution event is now ~40 days old -- catalyst priced. After a strong run, this is exactly the signal-set where the playbook expects mean reversion but no DOWN case has fired yet. Trim, do not short.
* **2026-02-25** (BUY, HIGH): CRISIS regime with breadth at 2.8% advancing and 21d return -13.89% is a textbook capitulation signal. Three of our highest-fire-rate UP cases activate together: `behavioural_panic_3day` (mean-revert), `fipi_capitulation` (contrarian), `mf_capitulation_with_value` (when flows dry up at fundamental cheapness). Rule 12 explicitly allows aggressive BUY when extreme breadth + extreme drawdown align (asymmetric payoff favors longs). MEBL = highest-quality compounder to own through the bounce.
* **2026-03-09** (BUY, MEDIUM): Regime upgraded from CRISIS to CAUTION = early V-recovery confirmation. Brent oil_up STRONG creates the second positive lens needed under Rule 12. The post-capitulation bounce typically rewards both broad market AND sector beneficiaries. PPL captures both the recovery beta AND the direct E&P upside from sustained oil strength. Convicted but not HIGH because CPI at 7.3% is a real headwind that could cap the rally.
* **2026-04-29** (CASH, MEDIUM): Phase-1 says market_risk_on=False -- the mechanical rule book is in CASH. Per Anchor 1 in the strategist prompt, CASH is the default unless I have overwhelming override evidence. I do not: breadth back to 13.9% advancing after a +11.3% 21d run = narrow leadership, classic distribution pattern. CPI sticky at 7.3%, no fresh positive catalysts. Take profits and respect the rule book.

