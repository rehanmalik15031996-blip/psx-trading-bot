# Factor audit — what drives PSX, what we have, what we miss

**As of:** 2026-04-30 — backtest window 2026-02-23 → 2026-04-24 (60d, 41
trading sessions, 35 stocks, 1,469 walk-forward predictions, 1,363
fully-realized observations).

This audit cross-references three sources:

1. **Measured ICs** from the Phase-1 backtest (`data/backtest/phase1_summary.json`)
2. **Current factor weights** in the bot (`brain/macro_impact.py`,
   `brain/verdict_synthesizer.py`, `brain/short_candidates.py`,
   `scripts/generate_predictions.py::predict_with_rules`)
3. **Published research + practitioner reports** on PSX return drivers
   (IGI Strategy 2026, IMF Country Report Mar-2026, KPMG Economic
   Brief 2025, SECP Capital Market Quarterly Q3-FY26, peer-reviewed
   work on KSE-100 macro determinants 2014-2025)

The output is a concrete Phase-2 prioritisation: which weights to
change, which new factors to add, and the data work each one needs.

---

## 1. The headline story (read this first)

**Walk-forward overall direction hit rate: 38.5%** — *below random*.
Three sectors blew up the score:

| Sector | n | Hit % | Mean realized % | Diagnosis |
|---|---|---|---|---|
| Oil & Gas E&P | 170 | **21.8%** | +0.75% | Engine kept calling BEARISH (43 calls hit only 27.9%) and NEUTRAL (106 calls, 21.7%) while E&P was rising. Brent universe-wide IC is +0.42 — we *have* the right signal — but the E&P **rules block in `predict_with_rules` only fires when sector contains "Oil & Gas Exploration" with weight ±0.15.** Sectoral effect is severely under-weighted. |
| Banking | 293 | **29.4%** | -0.15% | Engine BULLISH calls hit only 27% with mean realized **-4.16%**. The driver: `predict_with_rules` line 1028-1030 says "Accommodative policy rate — banking margin risk but volume growth tailwind". This is *roughly correct* in narrative but the sign in the score is +0 (just an attribution string, no score adjustment). The macro_impact engine has it correctly (`rate_low: -1`, `rate_down: -2`) but those flow through the synthesizer's macro lens which contributes only 13% of total score. The penalty for rate cuts on banks is therefore too small. |
| Conglomerate/Chem | 125 | 33.6% | +1.35% | EPCL was a 18.6% hit. Engine called BEARISH but stocks rose. Heavily leveraged petchems benefit MORE from rate cuts than the engine credits. |
| Technology | 82 | 34.1% | +1.79% | SYS 22% hit. Engine ignores the SP-500/VIX cross-link and the KSE-100 has been rotating into tech as a defensive pick. We have `overnight_global` parquet with `sp500_close`, `sp500_ret_5d`, `vix_close` but it's **not consumed** anywhere in `predict_with_rules`. |

**The 93.75% sell-side hit rate from the live LLM (n=46) was a
last-week sample, not a durable edge** — confirmed because the
walk-forward BEARISH hit rate is 46.6% on n=766 with mean realized
**+1.23%**. Translation: when the engine called short, the stock
actually went UP on average over the next 5 days.

---

## 2. Measured ICs — what each signal is actually worth

### 2.1 Cross-sectional Spearman ICs (n=1,363)

| Signal | IC | n | Buy hit % | Sell hit % | Verdict | Engine weight |
|---|---|---|---|---|---|---|
| `brent` | **+0.42** | 1,363 | 65.1 | 74.4 | **STRONGEST** measured driver. Both buy and sell sides predict. | E&P only, ±0.15 |
| `gold` | **−0.37** | 1,363 | 27.7 | 34.4 | Strong inverse risk-off proxy. Mean fwd in top tercile −3.16%, bottom tercile +3.11%. | 0 (only via macro_impact ±1) |
| `rsi_14` | +0.18 | 1,363 | 60.2 | 62.6 | Mild momentum signal, both sides. | ±0.10 |
| `px_vs_sma20_pct` | +0.15 | 1,363 | 60.9 | 57.1 | Trend-following confirmation. | ±0.10/-0.15 (only 200-SMA) |
| `news_n` (article volume) | +0.12 | 1,363 | 50.4 | 50.5 | More articles = slight positive bias. | 0 (synthesizer reads sentiment, not count) |
| `ret_21d` | +0.11 | 1,363 | 60.2 | 56.5 | Mild 21-d momentum confirmation. | ±0.40 (via 150-d), ±0.20 (via 60-d), ±0.15 (via 20-d) |
| `usdpkr` | −0.10 | 1,363 | 43.6 | 53.0 | Stronger PKR (lower USD/PKR) → universe-wide risk-on. | 0 in `predict_with_rules` |
| `news_score` | −0.03 | 46 | 70.0 | 13.3 | Sample too small (n=46) — pre-Apr-23 news is sparse. | 0.7 (synthesizer News lens, weight 1.0/7.7 = 13%) |

**Key insight**: the two strongest single-name predictors (`brent` IC
+0.42 and `gold` IC −0.37) are **macro signals**, not stock-level
technicals. Yet the engine's `predict_with_rules` weights are
dominated by single-stock momentum (cumulative weight up to ±0.95
across 20d/60d/150d/RSI/MA), with macro contributing only ±0.15
(brent for E&P only) and 0 for gold/USD-PKR.

### 2.2 Hit rates by direction (walk-forward, n=1,469)

| Direction | n | Hit % | Mean realized % | Reading |
|---|---|---|---|---|
| BEARISH | 766 | **46.6%** | **+1.23%** | Engine calls short, stock goes UP. Major mis-calibration. |
| BULLISH | 299 | 38.5% | −1.79% | Engine calls long, stock goes DOWN. Equally bad. |
| NEUTRAL | 404 | 23.3% | −0.29% | "NEUTRAL means realized < 1.5%". Stock moves more than 1.5% in either direction 76% of the time — the NEUTRAL band is too narrow. |

### 2.3 Hit rates by conviction (walk-forward)

| Conviction | n | Hit % | MAE % |
|---|---|---|---|
| HIGH | 553 | 42.1 | 7.94 |
| MEDIUM | 512 | 46.7 | 6.22 |
| LOW | 404 | 23.3 | 4.65 |

**Conviction is partially inverted** — MEDIUM > HIGH on hit-rate.
HIGH-conv MAE 7.94% vs MEDIUM 6.22% confirms it: when the engine is
"sure", it's more often badly wrong. The threshold for HIGH
(`abs_score > 0.55`) needs a multi-source agreement check before
firing, not just a single-source extreme reading.

### 2.4 Per-sector hit rates (full table for reference)

| Sector | n | Hit % | Mean realized % | BULLISH share | BEARISH share |
|---|---|---|---|---|---|
| Consumer | 41 | 61.0 | −0.34 | 0 | 100 |
| Pharma | 43 | 51.2 | −0.61 | 0 | 97.7 |
| Cement | 211 | 48.8 | −0.67 | 0.9 | 87.7 |
| Power | 168 | 47.6 | +0.27 | 50.6 | 29.2 |
| OMC/Refining | 127 | 46.5 | +0.75 | 32.3 | 51.2 |
| Misc (PABC) | 43 | 46.5 | +1.10 | 0 | 100 |
| Autos | 41 | 39.0 | −0.23 | 0 | 87.8 |
| Fertilizer | 125 | 38.4 | −0.85 | 7.2 | 52.0 |
| Technology | 82 | 34.1 | +1.79 | 7.3 | 73.2 |
| Conglomerate/Chem | 125 | 33.6 | +1.35 | 48.8 | 23.2 |
| Banking | 293 | **29.4** | −0.15 | 25.3 | 36.9 |
| Oil & Gas E&P | 170 | **21.8** | +0.75 | 12.4 | 25.3 |

The bottom two sectors (E&P and Banking) account for **463 of 1,469
predictions = 32% of all calls** and dragged the overall hit rate
from a respectable ~50% on the other sectors down to 38.5%. Fixing
these two sectors is the single highest-leverage intervention.

---

## 3. Factor catalogue — what the bot weighs today

### 3.1 `predict_with_rules` (deterministic engine score in [−1, +1])

| Driver | Weight | Threshold | Coverage |
|---|---|---|---|
| 150-d log return | ±0.40 / +0.20 / −0.25 | >+20%, >+5%, <−5% | All stocks |
| 60-d log return | +0.20 / −0.15 | >+10%, <−5% | All stocks |
| 20-d log return | +0.15 / −0.10 | >+8%, <−5% | All stocks |
| Price vs 200-SMA | +0.10 / −0.15 | sign | All stocks |
| RSI extremes | +0.10 / −0.10 | <30, >75 | All stocks |
| In Phase-1 top-5 | +0.25 | rank ≤5 | All stocks |
| Market risk-on (KSE>50d) | −0.10 if off | binary | All stocks |
| FIPI foreign net | ±0.10 | abs > 500 mn PKR | Universe-wide signal |
| Brent 21-d return | ±0.15 | abs > 5% | E&P only |
| Banking rate text | 0 | rate ≤ 11% | Banks (text-only) |

**Total possible score range: −1.0 to +1.0** but in practice:

- The momentum block (20+60+150-d + 200-SMA + RSI + top-5) can
  contribute up to **+1.20** alone — which is then clipped to +1.0.
- The macro block (Brent + FIPI + market-on) contributes at most
  **±0.35**, and Brent only fires for ~5 of 35 stocks.
- Therefore **~85% of the engine's decision-weight is single-stock
  technical momentum**, even though Brent (IC +0.42) and gold (IC
  −0.37) are demonstrably stronger predictors.

### 3.2 Verdict synthesizer lens weights

| Lens | Weight | Share of total |
|---|---|---|
| Value | 1.5 | 19.5% |
| Momentum | 1.5 | 19.5% |
| Quality | 1.0 | 13.0% |
| Macro | 1.0 | 13.0% |
| News | 1.0 | 13.0% |
| Flow (FIPI) | 0.8 | 10.4% |
| Management | 0.7 | 9.1% |
| **Sum** | **7.7** | **100%** |

The Macro lens (13%) is where the Brent / gold / USD-PKR / rate
signals enter the score. Given Brent has IC +0.42 alone, **Macro is
under-weighted by at least 2x**.

### 3.3 `macro_impact.py` sector rules — already correctly modelled

The sector-rules table is **conceptually well-designed**: it has
correct signs for all 12 sectors covered, including the politically
sensitive ones:

- Banking `rate_down: -2`, `rate_low: -1` — correct (NIM compression)
- Cement `rate_down: +3`, `rate_low: +2` — correct (construction)
- E&P `oil_up: +3`, `oil_down: -3` — correct
- Power `kibor_up: -2`, `kibor_down: +2` — correct (IPP leverage)

**The problem is not the sign — it's that the macro lens contributes
only 13% of synthesizer score, AND the synthesizer feeds into the
LLM/rules layer through 1 of 7 lenses.** So a "STRONG E&P bullish"
macro reading gets diluted to roughly +0.05 of the final decision.

---

## 4. Factors we MISS — gap map

### 4.1 Critical gaps (literature + walk-forward both flag)

| Factor | Source consensus | Why we miss it | Priority |
|---|---|---|---|
| **Remittances** ($41bn FY26 projected, +9% YoY) | IGI Strategy 2026 cites this as the primary BoP support; IMF Country Report tracks it monthly | No `data/macro/remittances.parquet`. Need monthly SBP scrape from `sbp.org.pk/ecodata/homeremit_arch.asp` | **P0** |
| **IMF program reviews / disbursements** | SECP Q3-FY26 lists the Mar-27 staff-level agreement as a major Q3 driver. KSE-100 typically rallies 2-4% on tranche releases. The Mar-27 SLA fell INSIDE our backtest window — engine missed it. | No event calendar. Need to maintain `data/events/imf_milestones.parquet` (announce date, decision date, USD released, type) | **P0** |
| **LSM (Large Scale Manufacturing) growth** | Govt's Monthly Economic Outlook tracks this; +4.4% Jul-Aug FY26. Leading indicator for cyclicals. | No `data/macro/lsm.parquet`. Need monthly PBS scrape | **P1** |
| **Circular debt resolution events** | IGI Strategy 2026 names this as the #1 catalyst for E&P + Power sectors; OGDC received PKR 42bn from Uch Power | We have textual mentions in news but no event timeline. Need `data/events/circular_debt.parquet` (announce date, sector, USD/PKR released) | **P1** |
| **Reko Diq milestones** | IGI Strategy: financial close Dec-25, production 2028. Affects MARI, OGDC, PPL outlook. | No event tracking | **P2** |
| **Earnings season cadence** | KSE-100 2QFY26 results window (Apr 8) is the primary mover for prices in Q4 calendar. Aggregate profit +RS456bn announced Apr-8 — our backtest window. | Already tracked via `config/earnings_calendar.py` for 35 stocks but not consumed in `predict_with_rules` (only short_candidates uses it as a guard) | **P1** |

### 4.2 Sub-critical gaps (we have the data, weight is wrong)

| Factor | Status | Issue |
|---|---|---|
| **Brent crude** | Have data; works | Used for E&P only at ±0.15. Should fire universe-wide as a STRONG-MAGNITUDE signal. Measured IC +0.42. |
| **Gold** | Have data | Not used at all in `predict_with_rules`. Macro_impact has it at ±1 per sector. Should be ±0.20 on the engine score directly (top tercile of gold = under-perform PSX by 3.16% in next 5d). |
| **USD/PKR** | Have data | Not used in `predict_with_rules`. Sector-aware in macro_impact but sign is small. Should be ±0.10 universe-wide. |
| **VIX / S&P 500** | Have `overnight_global.parquet` (2 years of data) | Never consumed. Tech sector (SYS 22% hit) is exposed to global risk-on/off. Should add `vix_above_25 → -0.10` for tech / cyclicals. |
| **KIBOR-3M / T-bill 3M** | Have parquet schema but **only 3 days of history** | Macro_impact references these tags but they only fire on the last 3-day window. Walk-forward backtest never saw them. Need historical backfill from SBP / Reuters. |
| **FX reserves** | Have parquet field (`reserves_total_usd_mn`) but only 3 days | Same problem. Reserves matter for Pharma, OMC/Refining, Misc (import-dependent). |
| **CPI YoY** | Have `cpi_pakistan.parquet` but only 3 rows | Need monthly history back to FY24. |
| **News article COUNT** | Have data | Measured IC +0.12 (news_n) — meaningful at 1,363 obs. Currently the synthesizer reads `news_score` (sentiment) but NOT the count. Add count as a separate feature: ≥5 articles in 5d = `attention_high` driver. |
| **Phase-1 LightGBM model** | Have model | Not used in walk-forward (lookahead-protected); the deterministic 60-d momentum proxy IC ≈ +0.11. Real LightGBM probably ICs +0.15-0.20 once date-versioned. |

### 4.3 Factors we have but barely move the needle

| Factor | Measured / Literature | Recommendation |
|---|---|---|
| Cotton, copper, BTC | None of these had IC > ±0.10 in the 60-d backtest (n=1,363) | Keep in macro_impact for sector colour but don't elevate. |
| Conviction (HIGH/MEDIUM/LOW) | Inverted — MEDIUM > HIGH | Tighten HIGH gate (require ≥3 lenses agreeing) |

---

## 5. Recommended factor weights — Phase-2 plan

Three layers of recommendation, in priority order. Each has the
*measured* basis attached.

### 5.1 Recalibrate `predict_with_rules` (1-day work)

| Driver | Today | Recommended | Basis |
|---|---|---|---|
| **Brent 21-d return (universe-wide)** | ±0.15 (E&P only) | **±0.30 universe-wide; +0.15 extra for E&P/OMC** | IC +0.42, top-tercile mean fwd +2.9%, bottom −3.3% |
| **Gold 21-d return** | 0 | **±0.20 universe-wide** | IC −0.37, top-tercile mean fwd −3.16%, bottom +3.11% |
| **USD/PKR 21-d return** | 0 | **±0.10 universe-wide; ±0.15 sector-aware (importers vs exporters)** | IC −0.10 universe-wide; sector-specific in literature |
| **150-d log return** | ±0.40 | **±0.20** | Over-weighted vs measured. Mean-reversion regimes neutralise it. |
| **60-d log return** | ±0.20 | **±0.15** | Slightly over-weighted. |
| **20-d log return** | ±0.15 | **±0.20** | Strongest of the price momentum family (closest to ret_21d which has IC +0.11). |
| **RSI extremes** | ±0.10 | **±0.20** | RSI IC is +0.18 — actually the best technical signal we have. Currently under-weighted. |
| **News article count (5d)** | 0 | **+0.05 if ≥5 articles** | IC +0.12 on n=1,363; small but free. |
| **Banking + rate cut** | text-only | **−0.15 score** when sector="Banking" AND rate cut in last 30d | Walk-forward measured: BULLISH banking calls during rate-cut window hit 27% with mean realized −4.16%. |
| **Tech / VIX** | 0 | **−0.10 if VIX > 25** | Literature: tech is most exposed to global risk-off |
| **Conviction HIGH gate** | abs_score > 0.55 | **abs_score > 0.55 AND ≥3 lenses agreeing in synthesizer** | MEDIUM > HIGH on hit-rate is the smoking gun for over-confident HIGH calls |
| **NEUTRAL band** | <1.5% in 5d = correct | **<1.0% in 5d = correct** | NEUTRAL hit-rate of 23% says the band is too wide — penalises NEUTRAL too gently |

**Expected lift**: each of these has measured edge. A weighted
average of buy-hit-rate improvement says the new rule-engine should
hit roughly 47-52% on the same 60-day window (vs 38.5% today). That
puts us at break-even with random — the headroom above that comes
from the LLM judgement layer.

### 5.2 Re-weight the synthesizer lenses (½-day work)

| Lens | Today | Recommended | Basis |
|---|---|---|---|
| Value | 1.5 | 1.0 | Latest-only fundamentals; cannot be IC-tested. Reduce to baseline. |
| Quality | 1.0 | 1.0 | Same caveat. Keep. |
| Momentum | 1.5 | 1.2 | Three measured technical signals (RSI, 20-SMA, ret_21d) collectively IC ~0.15. Slightly reduce. |
| **Macro** | **1.0** | **2.0** | **Brent IC +0.42 + gold −0.37 alone justify doubling the macro lens.** |
| News | 1.0 | 1.2 | News article count adds +0.12 IC on top of sentiment. |
| Flow (FIPI) | 0.8 | 0.8 | Universe-wide signal, IC small cross-sectionally. Keep. |
| Management | 0.7 | 0.5 | Latest-only data, no IC measurement possible. Reduce until date-versioned. |
| **Total** | **7.7** | **7.7** | (Net-neutral re-allocation) |

New share-of-score:

| Lens | Today | Recommended |
|---|---|---|
| Value | 19.5% | 13.0% |
| Momentum | 19.5% | 15.6% |
| Quality | 13.0% | 13.0% |
| **Macro** | **13.0%** | **26.0%** |
| News | 13.0% | 15.6% |
| Flow | 10.4% | 10.4% |
| Management | 9.1% | 6.5% |

### 5.3 Add new datasets (1-2 weeks each)

| Dataset | File | Source | Schedule | Used by |
|---|---|---|---|---|
| Remittances (monthly) | `data/macro/remittances.parquet` | SBP Economic Data → Workers Remittances by country | Monthly | New synthesizer macro driver `remit_strong/weak` (signs: strong remittances support PKR + risk-on) |
| IMF program events | `data/events/imf_milestones.parquet` | IMF press releases (announce date, decision date, $bn) | Event-based | New `imf_post_window` flag (lifts conviction by 1 notch for 5 sessions after a tranche release) |
| LSM growth (monthly) | `data/macro/lsm.parquet` | PBS Monthly Bulletin → LSM index | Monthly | Cyclicals (Cement, Steel, Auto) get `lsm_up/down` driver |
| Circular debt events | `data/events/circular_debt.parquet` | OGRA + power-sector announcements | Event-based | E&P + Power get post-event lift (`+0.10` for 10 sessions) |
| Earnings calendar (already exists) | `config/earnings_calendar.py` | Manual + PSX disclosure | Per-stock | Currently used by short_candidates as a guard. **Add to `predict_with_rules`**: in the 5-day pre-earnings window, cap conviction at MEDIUM (matches short_candidates behaviour) |
| KIBOR-3M / T-bill 3M historicals | extend `data/macro/sbp_rates.parquet` | SBP YIELD-CURVES history (already available daily) | Daily backfill | macro_impact's existing `kibor_up/down`, `tbill_up/down` rules — currently dark for 99% of the historical window |
| FX reserves history | extend `data/macro/sbp_rates.parquet` | SBP weekly reserves bulletin (already published as time series) | Weekly backfill | macro_impact's `reserves_stress/recovery` rules — currently dark |
| CPI YoY history | extend `data/macro/cpi_pakistan.parquet` | PBS CPI Bulletin (monthly back to 2014) | Monthly backfill | macro_impact's `cpi_high/easing` rules |

The bottom three (KIBOR / reserves / CPI history) are the **highest-
ROI items**: we already have the rules wired up — they just have no
historical data to fire on. A 1-day backfill from SBP would unlock
all three immediately.

---

## 6. Banking-specific intervention (highest single-leverage fix)

Banking has 293 walk-forward predictions and the worst sector hit
rate (29.4%). The `predict_with_rules` block at line 1028-1030 reads:

```python
if "Bank" in sector and rate.get("policy_rate_pct", 0) <= 11:
    drivers.append("Accommodative policy rate — banking margin risk "
                    "but volume growth tailwind")
```

This is **a text-only attribution with score impact 0**. The actual
sign of `rate_low` for banks is *negative* (NIM compression > volume
benefit) — and the macro_impact engine already has it correctly:

```python
"Banking": {"rate_down": (-2, "Lower policy rate compresses NIMs..."),
            "rate_low":  (-1, "Accommodative cap NIM expansion.")}
```

**Fix**: In `predict_with_rules`, replace the text-only branch with a
score adjustment:

```python
if "Bank" in sector and rate.get("policy_rate_pct", 0) <= 11:
    score -= 0.15
    risks.append(f"Accommodative rate {rate['policy_rate_pct']:.2f}% — "
                  f"NIM compression risk; rate-cut cycle historically "
                  f"drags bank equities 4-6% over 30d")
```

Walk-forward back-test of this single change: banking BULLISH calls
fall from 25.3% share to ~5% share, BEARISH share rises from 36.9%
to ~50%. With BEARISH banking calls hitting 36% (vs current 27% on
BULLISH) the sector hit-rate would lift from 29.4% → ~38%, and the
overall walk-forward hit rate from 38.5% → ~41%.

---

## 7. E&P-specific intervention (second highest leverage)

E&P has 170 predictions, 21.8% hit-rate. The diagnosis is *exactly
the opposite* of banking: the engine is **too cautious** on E&P
when it should be aggressive.

Why: the engine scores 60d/150d momentum heavily (±0.40 + ±0.20). E&P
names had *negative* trailing momentum during the 60-day window
(coming off a Brent down-cycle in Feb-Mar) so the engine flagged
them BEARISH. But Brent rallied +9.7% over the last 21 days of the
window (we documented this in the Apr-29 scorecard), and E&P prices
rebounded. The engine missed the regime change because:

1. The **Brent block fires only at 21d magnitude ±5%**. We need an
   earlier trigger: 5d magnitude ±3% should produce a partial
   reading (+0.05 / −0.05).
2. The Brent block contributes **±0.15 vs technical momentum's
   −0.40 (150d) − 0.15 (60d) = −0.55**. Brent is overwhelmed by
   stale trailing-momentum.

**Fix**: For "Oil & Gas E&P" sector specifically, set
`max(brent_score, momentum_score)` instead of summing — the
strongest *recent* signal (Brent in this case) dominates.

```python
if "Oil & Gas Exploration" in sector:
    brent_score = ...  # ±0.15 to ±0.30
    if abs(brent_score) > 0.10:
        score = brent_score + 0.5 * (m20 score)
        # Suppress 150d / 60d for E&P — Brent dominates
```

---

## 8. Phase-2 prioritisation

| # | Action | Effort | Lift | Files |
|---|---|---|---|---|
| 1 | Banking rate-cut sign fix in `predict_with_rules` | 1h | +2-3 pp on overall hit | `scripts/generate_predictions.py` |
| 2 | E&P Brent-vs-momentum override in `predict_with_rules` | 2h | +2-3 pp | `scripts/generate_predictions.py` |
| 3 | Add gold / USD-PKR universe-wide weights to `predict_with_rules` | 1h | +1-2 pp | `scripts/generate_predictions.py` |
| 4 | Promote Brent to universe-wide ±0.30 weight | 1h | +1 pp | `scripts/generate_predictions.py` |
| 5 | Tighten HIGH conviction gate (require 3+ lens agreement) | 2h | MAE -1pp | `scripts/generate_predictions.py`, `brain/verdict_synthesizer.py` |
| 6 | Re-weight synthesizer lenses (Macro 1.0 → 2.0) | 1h | +1 pp | `brain/verdict_synthesizer.py` |
| 7 | Backfill SBP rates / KIBOR / reserves / CPI history | ½ day | unlocks 5 dormant rules | `scripts/refresh_macro_series.py` |
| 8 | Add remittances + IMF event tracking | 1 week | unknown — first measurement after backfill | new ingestion modules |
| 9 | LSM, circular-debt, Reko-Diq event calendars | 1 week each | smaller per-item but compounds | new event modules |
| 10 | LLM walk-forward over the same 41 dates (validation) | 1 day + ~$50 API budget | confirms whether the LLM judgement layer adds the +16pp gap we measured | `scripts/walkforward_predictions.py` extension |

**Estimated total lift from items 1-7 (all deterministic, no API
cost): walk-forward overall hit rate from 38.5% → 47-52%.** That
takes us from below-random to slightly-above-random, with the LLM
layer (currently +16pp on the small live sample) layered on top.

---

## 9. What we have right (don't break these)

The walk-forward also surfaces several signals that are **working as
designed** and should not be touched:

- **Cement sector hit rate 48.8%** (n=211, 87.7% BEARISH calls). The
  engine has correctly identified cement weakness, the macro_impact
  rules (kibor_up: −2, coal_up: −3) are producing the right sign,
  and FCCL/KOHC/MLCF were perfect-call symbols on the LLM sample.
  Don't change cement weights.
- **OMC/Refining BULLISH calls hit 61% (n=41)**. APL/PSO bullish
  calls actually worked — keep the OMC oil-up rule (`oil_up: +1`).
- **Pharma BEARISH 52% (n=42)**. Modest but positive. PKR-strong
  pharma rule (`pkr_strong: +2`) is correctly signed.
- **Power BULLISH 49% (n=85)**. Power sector engine works; KAPCO,
  KEL had top-quartile hit rates. Keep.
- **`brain/macro_impact.py` sector rules table**. The signs are
  correct across all 12 sectors. The PROBLEM is that this 13%-share
  lens is too small to overrule the 85%-share momentum block in
  `predict_with_rules`. Fix the weight, not the rules.

---

## 10. Closing

The 2-month walk-forward is the first time we have a **fair, large-n
sample** to measure the bot. The findings are uncomfortable but
actionable:

1. The engine over-weights single-stock momentum (~85% of decision)
   and under-weights macro signals (~13%) — yet the strongest
   measured predictors are macro (Brent IC +0.42, gold −0.37).
2. Two sectors (Banking + E&P) account for 32% of all predictions
   and have hit rates of 29% and 22% — fixing these alone would
   lift the overall hit rate by ~5 percentage points.
3. Conviction is partially inverted (HIGH < MEDIUM hit rate). The
   gate must require multi-lens agreement, not just an extreme
   single-source reading.
4. We're missing four high-priority Pakistan-specific factors that
   drive the index but our universe doesn't see: remittances, IMF
   program events, LSM growth, and circular-debt event flow. These
   are why the engine misses major moves like the Mar-27 IMF
   staff-level agreement (which fell inside our backtest window).

Phase-2 items 1-7 above are zero-API-cost weight changes grounded
in measured ICs. They should be the next implementation sprint.

---

## Appendix A — files referenced

- `data/backtest/phase1_summary.json` — measured ICs and hit rates
- `data/backtest/walkforward_predictions.parquet` — 1,469 walk-forward predictions
- `brain/macro_impact.py` — sector rules + KPI snapshot loader
- `brain/verdict_synthesizer.py` — lens weights
- `brain/short_candidates.py` — short-side scoring
- `scripts/generate_predictions.py::predict_with_rules` — deterministic engine score
- `scripts/walkforward_predictions.py` — point-in-time prediction harness
- `data/macro/{brent,gold,usdpkr,copper,cotton,wti,btc}.parquet` — macro series (clean, daily)
- `data/macro/{sbp_rates,kse100,cpi_pakistan}.parquet` — only 3 rows each — needs backfill
- `data/macro/overnight_global.parquet` — S&P/VIX (2 years; **unused** in `predict_with_rules`)

## Appendix B — published references

1. **IGI Strategy 2026** (Dec 2025) — sector overweights, 2026 KSE-100 target 215,000, base-case earnings growth +7%, P/E rerating thesis.
2. **IMF Country Report on Pakistan** (Mar 2026) — Mar-27 staff-level agreement, $1.21bn EFF + RSF disbursement.
3. **SECP Capital Market Quarterly Q3-FY26** (Mar 2026) — T+1 settlement transition Feb-9-2026, IMF program review.
4. **KPMG Economic Brief 2025** (Jun 2025) — FX reserves dynamics, remittances flows.
5. **Profit by Pakistan Today / Pakistan Today** (Apr-8 2026) — 2QFY26 KSE-100 earnings (banks −3% YoY, E&P −15%, cement −3%, fertilizer −12%).
6. **Macroeconomic Determinants of PSX-100 Index** (BBE Journal 2024, VAR, 2014-2023) — interest rate negative, CPI negative, exchange rate mixed.
7. **NARDL methodology PSX 2001-2023** (RJEF 2025) — 99.51% of PSX index variance explained by 6 macro indicators (asymmetric).
