# Pakistan Stock Exchange (PSX) — Consolidated Market Knowledge Base

> **Sources merged:** (1) My own web research, (2) Gemini Deep Research report ("Pakistan Stock Market Research Factors.docx"), and (3) Academic papers from *Asian Academy of Management Journal*, *Pakistan Journal of Humanities and Social Sciences*, *Journal of Business and Tourism*, and *Research Consortium Archive*.
>
> **Verification status:** All major claims cross-checked with live sources as of April 23, 2026. Flagged items are noted inline.

---

## 1. Overview of the PSX

- The **Pakistan Stock Exchange (PSX)** was formed in **January 2016** through the consolidation of the Karachi, Lahore, and Islamabad stock exchanges into a single national entity.
- Since consolidation it has undergone significant modernization and regulatory tightening.
- The **benchmark index is the KSE-100**, with the **KSE-30** used for market-wide circuit breaker calculations.
- PSX is classified as a **Frontier Market** by MSCI (downgraded from Emerging Market in 2021).
- Trading hours: Monday–Friday, **9:32 AM – 3:30 PM PKT** (shorter during Ramadan).
- Settlement: **T+2** (trades settle two business days after execution).

---

## 2. The Nature of the Market — Is It "Emotional"?

The short answer: **yes, but more precisely it is an "adaptive" market driven by behavioral biases.**

### 2.1 Weak-Form Inefficiency (EMH Fails on PSX)
- Academic research using ADF tests, ARMA, and GARCH modeling shows the KSE-100 is **weak-form inefficient** at the daily, weekly, and monthly level.
- Returns are often **stationary** and show **autocorrelation**, meaning past prices carry predictive information — unlike a truly efficient market where returns follow a random walk.
- Implication: sophisticated participants with better data/tools can systematically generate alpha. This is precisely the opening that a well-built trading bot exploits.

### 2.2 Adaptive Market Hypothesis (AMH) — The Better Framework
- The PSX alternates between periods of **dependence (predictable)** and **independence (random walk)**.
- During stable regulatory/economic periods, efficiency improves. During political/economic crises, emotion and speculation dominate.
- **Design takeaway:** Your bot should detect regime shifts and switch between strategies (e.g., trend-following during stable periods, mean-reversion during panic periods).

### 2.3 Behavioral Biases That Dominate
Quantitative PLS-SEM research identifies the dominant psychological drivers:

| Bias | Strength | Effect on Market |
|---|---|---|
| **Greed** | Very high (path coefficient ~0.99 in one study) | Fuels speculative bull runs, chasing rapid gains, bubble formation |
| **Overconfidence** | High | More trading, higher costs, lower long-term returns; blinds investors to imminent corrections |
| **Herding** | Moderate | Mimicking "smart money" and institutional flows creates artificial momentum |
| **Over/Under-reaction** | High | Panic selling on bad news (oversold conditions), slow pricing-in of gradual positive news |
| **Sentiment (optimism/pessimism)** | Significant, day-dependent | Optimism lifts volume on Tuesdays/Thursdays; pessimism dampens Wednesday trading |

### 2.4 Retail Panic vs. Smart Money
- **Foreign Portfolio Investment (FPI)** flows act as a confidence signal for domestic institutions, who then trigger local herding.
- Aggressive **foreign outflows → retail panic**: individual investors dump positions regardless of fundamentals.
- Local mutual funds and insurance companies increasingly act as the **"sponge"** that absorbs foreign selling (e.g., in early 2026, mutual funds net-bought $4.6M while foreigners sold $10.5M).

---

## 3. Macroeconomic Drivers (The Structural "Signal")

Research note: short-run studies show inflation + exchange rate + interest rate together explain only **~20%** of KSE-100 variation. The remaining ~80% comes from political, geopolitical, flow, and sentiment factors.

### 3.1 SBP Policy Rate (Most Critical Factor)
- **Strong negative relationship** with stock prices. Rate cuts → rallies; hikes → sell-offs.
- **Recent cycle (verified):** SBP cut rates from **22% peak (2023) → 10.5% (Dec 2025)** — a cumulative **1,150 bps reduction**, with 1,100 bps between June 2024 and May 2025, plus a surprise 50 bps cut on Dec 15, 2025.
- This triggered a historic **"liquidity rotation"** — capital moved from fixed-income and money market funds into equities, driving the KSE-100 to record highs.

### 3.2 Inflation (CPI)
- Peaked at **~38% in May 2023**; now anchored in the **5–7% target range** for FY26/FY27.
- Negative short-term impact on equities (signals future rate hikes, erodes margins).
- Long-term, equities act as an inflation hedge.
- Research shows inflation has one of the largest forecast error variances (~14%) on the KSE-100.

### 3.3 Exchange Rate (PKR/USD)
- **Mixed but mostly negative** impact during devaluations.
- **Hurts:** importers (autos, pharma, chemicals, power generation).
- **Helps:** exporters (textiles, IT services with USD revenues).
- **Foreign investors** especially sensitive — PKR devaluation erodes USD-denominated returns, triggering FIPI outflows.

### 3.4 GDP & Growth
- FY26 real GDP growth projected **3.75%–4.75%**, up from earlier conservative estimates.
- Driven by recovery in agriculture and industrial sectors.
- Positive but statistically weak direct impact on KSE-100 in short-run studies.

### 3.5 Oil Prices (Critical for Pakistan)
- Pakistan is a **net oil importer**, so high crude = weak rupee + wider current account deficit + inflation pressure.
- **Helps:** E&P stocks (OGDC, PPL, POL, MARI) — crisis premium.
- **Hurts:** everyone else, especially OMCs, power, autos.

### 3.6 Other Macro Variables
- **Money Supply (M2)** — positive (more liquidity → higher valuations).
- **FDI** — surprisingly shows negative lagged effects in some studies (possibly a timing/confounding artifact).
- **Unemployment** — significant negative impact.

---

## 4. Political & Institutional Catalysts (The Biggest Shocks)

### 4.1 IMF Program Status (Binary, Massive Impact)
- IMF Staff-Level Agreements (SLAs) are **consistently the single largest rally triggers**.
- **Example:** July 2023 SLA for $3B Stand-By Arrangement → **KSE-100 gained 5.57% in a single day**.
- **Example:** March 2025 deal unlocking $2B → index crossed intra-day 118,000.
- IMF programs typically mandate energy reforms + fiscal discipline, which structurally benefits banks and E&P companies.

### 4.2 Political Stability
- Acts as a **"risk multiplier"**: stability is a valuation floor; instability kills confidence.
- Elections (2018, 2024), political crises, cabinet changes, and military establishment signals all produce sharp directional moves.
- Governance indicators (rule of law, corruption control, regulatory transparency) are directly correlated with higher stock prices.

### 4.3 Geopolitical Events
- **Pakistan–India tensions** — sharp drops on escalation, "peace rallies" on de-escalation.
- **US/China/Saudi/UAE foreign policy** — CPEC announcements, Saudi deposits ($2B), Chinese Panda bonds all fuel rallies.
- **2026 Iran War & Strait of Hormuz crisis (VERIFIED):** Starting Feb 28, 2026, US-Israeli strikes on Iran triggered a functional Hormuz closure. Brent spiked from ~$75 to **$102–$119**. KSE-100 **shed ~10% from January peak** as investors anticipated higher inflation and an end to the rate-cut cycle.

### 4.4 Federal Budget (June every year)
- Extreme volatility around budget day.
- Key watch-items: Capital Gains Tax (CGT), super tax on banks, dividend tax, sector-specific levies.

---

## 5. Flow-Based & Structural Factors

### 5.1 FIPI & LIPI Tracking
- **FIPI** (Foreign Investor Portfolio Investment) and **LIPI** (Local Investor Portfolio Investment) flows are released **daily by NCCPL** (National Clearing Company of Pakistan Limited).
- These are the single most useful daily sentiment indicators on the PSX.
- **Critical data source** for any trading bot. Aggregators like FinHisaab publish sector-wise breakdowns.

### 5.2 MSCI / FTSE Classification
- MSCI downgraded Pakistan from Emerging → Frontier in 2021 — caused significant passive-fund outflows.
- Future upgrades/downgrades would trigger mechanical re-weighting.

### 5.3 Low Free Float & Thin Liquidity
- Many PSX stocks have concentrated ownership and low free float.
- Small trades can move prices significantly.
- Vulnerable to manipulation and squeeze moves.

---

## 6. Sector-Specific Drivers

The KSE-100 is heavily weighted toward a handful of sectors — movements in these largely dictate the index.

| Sector | Key Tickers | Primary Drivers |
|---|---|---|
| **Banking** (largest weight) | BAHL, MCB, UBL, Meezan, HBL | Policy rate, NPLs, super tax, advances/deposits ratio. Top performer 2025 (~104% return). |
| **Cement** | Lucky, DGKC, Bestway, Fauji, Attock | International coal prices, construction activity, interest rates. 88% return in 2025. |
| **Oil & Gas E&P** | OGDC, PPL, POL, MARI | Crude prices, circular debt, gas pricing policy. |
| **OMCs** | PSO, APL, SHEL | Retail fuel margins, inventory gains/losses. |
| **Fertilizer** | Engro, FFC, FFBL | Urea/DAP prices, gas subsidies, agri cycles. Affected by 2026 "Nitrogen Shock" (global prices +26–65%). |
| **Power / IPPs** | HUBCO, KEL, KAPCO | **Circular debt resolution is the key catalyst.** In late 2025, Rs 1.225 trillion ($4.29B) circular debt resolution with 18 banks caused major rallies here. |
| **Autos** | Indus Motors, Pak Suzuki, Honda Atlas | Interest rates, rupee, import restrictions. |
| **Textiles** | Nishat, Interloop, Gul Ahmed | Cotton prices, USD/PKR, US/EU export demand. |
| **Technology** | Systems Ltd, Avanceon, NetSol | Global tech trends, USD strength, expected 23% annual earnings growth. |

### 6.1 Special Case: Circular Debt & Power Sector
- Circular debt (~Rs 2.4 trillion by late 2025) has been a chronic liquidity crisis in power sector.
- Rs 1.225 trillion resolution (Rs 660B loan restructuring + Rs 565B fresh financing) in late 2025 was the **largest banking transaction in Pakistan's history**.
- Boosted IPPs (HUBCO, KEL) and reduced systemic bank risk.

---

## 7. Market Microstructure & Rules (Critical for Bot Design)

- **Individual Stock Circuit Breakers:** ±10% daily from prior close. Once a stock hits the limit, trading **locks** at that price for the rest of the day (orders allowed only within band).
- **Market-Wide Circuit Breaker:** If KSE-30 moves ±5% from prior close and holds for 5 minutes, the **entire exchange halts temporarily**.
- **Settlement:** T+2.
- **Trading hours:** 9:32 AM – 3:30 PM PKT (shorter in Ramadan).
- **Implication for the bot:**
  - Must respect and detect locked stocks (avoid wasting orders).
  - Account for T+2 cash availability when sizing trades.
  - Handle market halts gracefully.
  - Expect thinner liquidity → use limit orders, avoid large market orders.

---

## 8. Calendar & Seasonal Effects

- **Budget Day (June):** Extreme volatility — many traders sit out.
- **Ramadan:** Reduced hours, lower volumes.
- **Earnings seasons:** Quarterly results (esp. banks, E&Ps) cause stock-specific moves.
- **Day-of-week effects:** Optimism drives volume on Tuesdays/Thursdays; pessimism peaks on Wednesdays (research-based pattern).
- **Ex-dividend dates:** Predictable price adjustments.

---

## 9. Valuation Context (As of Early 2026)

- **KSE-100 P/E:** ~10.4x (vs. 3-year historical average of ~6.9x).
- Still attractively valued vs. other emerging markets.
- **Brokerage targets for end-2026:**
  - Moderate: 215,000–225,000 (29–33% total return scenario)
  - Breakout: 250,000–300,000 (if privatization + energy crisis managed well)
- Low P/E in cyclical sectors (like cement) can indicate **peak earnings**, not undervaluation — context matters.

---

## 10. Implications for Your Trading Bot

A successful algorithmic trading bot for the PSX must monitor **all five layers simultaneously**:

### Layer 1 — Macro Signals (slow, directional)
- SBP policy rate decisions & minutes
- Monthly CPI prints
- PKR/USD daily rate
- Brent/WTI crude prices

### Layer 2 — Political/Institutional Events (shock-makers)
- IMF reviews & tranche releases
- Federal Budget (June)
- Election/political news feed
- Major diplomatic events

### Layer 3 — Flow Data (daily, actionable)
- **FIPI/LIPI daily data from NCCPL** — highest priority signal
- Mutual fund flow data
- Block trades

### Layer 4 — Sentiment (behavioral edge)
- News scraping: Profit.pk, Mettis Global, Business Recorder, Dawn, The News, Arif Habib research
- Financial Twitter/X Pakistan
- Local broker WhatsApp/Telegram channel sentiment (if accessible)

### Layer 5 — Technical & Microstructure
- RSI & Bollinger Bands (detect overbought/oversold due to emotion)
- Mean reversion on over-reaction
- Trend following during stable macro regimes
- Circuit breaker detection
- Volume & liquidity filters (avoid illiquid names)

### Strategy Recommendations
- **Regime-switching:** Use AMH insight — switch between mean-reversion (crisis periods) and momentum/trend-following (stable periods).
- **Bias-exploiting mean reversion:** Market over-reacts to bad news — buy panic dips in fundamentally sound sectors.
- **Event-driven trades:** IMF SLAs, circular debt announcements, budget day hedging.
- **Sector rotation:** Follow the interest rate cycle (banks/cement benefit from cuts; E&P benefits from oil spikes).
- **Risk management:** Always size with awareness of circuit breakers and T+2 settlement; never over-leverage into a potentially locked stock.

---

## 11. Final Verdict

The PSX is **not** a "purely emotional" market — it is an **adaptive market** where:

1. **Macro fundamentals** (rates, inflation, currency, oil) set the multi-month direction.
2. **Political & geopolitical events** (IMF, elections, India tensions, Iran war) deliver the biggest short-term shocks.
3. **Capital flows** (FIPI/LIPI) amplify and accelerate moves.
4. **Behavioral biases** (greed, overconfidence, over-reaction) dominate intra-day/intra-week noise.
5. **Structural features** (circuit breakers, low float, thin liquidity) dictate *how* prices actually move.

Your bot must be aware of **all five** — a model built on price/volume alone will systematically underperform.

---

## 12. Evaluation Summary — Gemini Deep Research vs. Independent Verification

| Claim | Source | Verdict |
|---|---|---|
| PSX formed 2016 from KSE/LSE/ISE consolidation | Gemini | ✅ Verified |
| Weak-form inefficiency (EMH fails) | Gemini | ✅ Verified via academic sources |
| Adaptive Market Hypothesis framework | Gemini | ✅ Verified (valuable addition) |
| Greed (β≈0.99) as dominant bias | Gemini | ⚠️ Plausible but coefficient is near-max; context suggests this is from a specific 2024 PLS-SEM study |
| Overconfidence & herding as major biases | Both | ✅ Verified |
| SBP 1,150 bps cut (22% → 10.5%) June 2024–Dec 2025 | Gemini | ✅ Verified (Reuters, Dec 15, 2025) |
| Inflation peaked 38% May 2023 | Gemini | ✅ Verified |
| KSE-100 reached 170,741 (Dec 2025) and 191,032 intraday (Jan 2026) | Gemini | ✅ Verified |
| July 2023 IMF SLA → 5.57% single-day gain | Gemini | ✅ Verified (Business Recorder) |
| Circular debt resolution Rs 1.225T with 18 banks (late 2025) | Gemini | ✅ Verified (Arab News) |
| 2026 Iran War / Strait of Hormuz blockade | Gemini | ✅ Verified (started Feb 28, 2026) |
| KSE-100 -10% correction from Jan 2026 peak | Gemini | ✅ Verified |
| P/E ~10.4x vs 6.9x historical | Gemini | ✅ Plausible, matches investing.com data |
| Circuit breakers (±10% stock, ±5% KSE-30) | My research | ✅ Verified from PSX directly |
| T+2 settlement, trading hours, Ramadan | My research | ✅ Verified |
| FIPI/LIPI daily data via NCCPL | Both | ✅ Verified |
| Sector returns 2025 (Banking 104%, Cement 88%) | My research | ✅ Verified |
| Day-of-week sentiment effects | My research | ✅ Verified (Policy Research Journal) |

**Overall: Gemini's research is high-quality and well-sourced. My research contributed the market microstructure and bot-implementation specifics that Gemini missed. The merged file above represents the verified, consolidated knowledge base.**
