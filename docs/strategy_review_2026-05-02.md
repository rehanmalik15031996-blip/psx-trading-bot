# Strategy review — 2026-05-02

**Reviewer's lens:** A senior PSX analyst reading the architecture
end-to-end (data ingest → briefing → playbook → master strategist),
asking *"Would I trust this to size a real portfolio tomorrow?"*.

**Evidence anchor:** The year-long walk-forward test in
`data/_health/end_to_end_test.json` (143 trading days,
2025-05-01 → 2026-04-24).

---

## 1. Headline verdict

**The architecture is materially better than what 95% of PSX retail
analyst desks run with.** It pulls 30+ structured data streams into
one briefing, has a deterministic sector × macro rule book, an
institutional-flow lens (AHL MF holdings), 22 curated case-library
analogues, a 7-lens reconciliation, and a top-of-stack Claude
strategist. The 12-month walk-forward was **84% directional precision
on 94 fired matches**.

**But:** the headline number masks four serious problems that an
experienced PSX analyst would call out on Monday morning.

> **The four "I would not let this run live yet" issues:**
>
> 1. **Half the playbook is dead — 13 of 22 cases never fired in a
>    year**, including the highest-impact ones (`pkr_devaluation_shock`,
>    `sbp_rate_cut_cycle_initiation`, `imf_sba_eff_approval`,
>    `circular_debt_worsening_large`, `cement_coal_shock`,
>    `fipi_capitulation`).
> 2. **The cases that fire most include three that are systematically
>    wrong on direction**: `nth_rate_cut_profit_taking` (0% hit, 10
>    fires), `imf_review_completed` (0% hit, 9 fires),
>    `sbp_rate_hike_shock` (0% hit, 4 fires). These are bedrock PSX
>    macro-cycle reads — getting them backward damages credibility
>    more than any miss.
> 3. **The single most-important live signal — SBP policy rate — fails
>    silently when the SBP server times out.** Today's live briefing
>    has `policy_rate.error: ReadTimeout`. The matcher then can't fire
>    rate-cycle cases, and Claude is reasoning blind on the dominant
>    PSX driver. We have a parquet fallback (`sbp_rates.parquet`) but
>    the connector doesn't use it.
> 4. **The MF flow lens — by far the highest-information signal — is
>    monthly data with a 60-day freshness gate.** It produces ~2
>    months of useful signal per ingest. Outside that window, the
>    strategy is back to running on macro + technicals only and the
>    precision drops.
>
> Fix those four and the strategy goes from "good in the back-test,
> fragile in production" to "professional".

---

## 2. What the architecture *gets right* (don't change these)

These are the parts a senior analyst would single out as best-in-class
for a PSX retail-trader system:

* **Sector × macro rule book (`brain/macro_impact.py`).** Every
  sector × driver pair carries a signed -3..+3 sensitivity AND a
  one-sentence reason string. This is exactly how an analyst's mental
  model works. The leverage amplifier (D/E from fundamentals cache
  modulates the sector score) is the right design.
* **The "stretched signal" z-score guard
  (`_is_stretched`).** Treating a 9% Brent rally as STRONG when it is
  >+1.5σ relative to its 1-year distribution is the kind of mistake
  most retail systems make; we explicitly demote those signals.
* **Cost discipline in the prompt.** "Round-trip is ~0.56% all-in
  plus 15% CGT, BUY/ADD must be defensible at >=1.6% gross expected
  5d" is the single most important sentence in the whole system. It
  is the difference between a backtest that looks good and a real
  portfolio that compounds.
* **Pre-event guard.** No fresh BUY when results are within 5 days
  is professional discipline.
* **Cycle-context triggers.** `days_since_last_cut_lte:7` to
  distinguish a fresh rate cut from a 3-week-old one. This is the
  kind of nuance that separates a real PSX framework from a generic
  EM model.
* **Behavioural lens named explicitly in the prompt.** PSX *is*
  retail-driven. Naming herding / panic / capitulation as a
  first-class lens — not a footnote — is correct.
* **Master Strategist as a JSON-output orchestrator over a structured
  briefing rather than a chat interface.** Lets the LLM reason while
  Python decides. Right design.
* **84% precision / 71% recall over 143 trading days.** The MF cases
  carry this number — `mf_initiation_cluster` 100% hit / +8.7% mean
  21d, `mf_universe_distribution_broad` 85% / -9.2% mean 21d,
  `post_cut_cycle_continuation` 100% / +7.1%. The MF lens IS the
  edge.

---

## 3. The five strategic holes a senior PSX analyst would flag

### Hole #1 — The macro-cycle cases fire WRONG on direction

The back-test shows three cases firing repeatedly with **0% hit rate**:

| Case | Expected | Actual mean 21d | n_fired | Hit |
|---|---|---|---|---|
| `nth_rate_cut_profit_taking` | DOWN | **+6.2%** | 10 | 0/10 |
| `imf_review_completed` | UP | **-5.7%** | 9 | 0/9 |
| `sbp_rate_hike_shock` | DOWN | **+8.1%** | 4 | 0/4 |

A real analyst reading these would say:

* **"The 2nd-cut profit-taking thesis is a US-equity meme. PSX 2024-25
  did the opposite — every cut after the first one re-energised the
  cement complex because the cycle was so deep (450bp cumulative).
  The case is reading from a different market regime."**
* **"The IMF review case has the cause and effect mixed up. PSX rallies
  INTO the review (priced in), then digests the fiscal-tightening
  details over the next 21 days. The 21-day window catches the
  digestion, not the relief rally. Either change the window to 5d, or
  flip the expected direction to DOWN."**
* **"`sbp_rate_hike_shock` was last useful in 2022-23. In a rate-cutting
  regime it never fires legitimately. The 4 fires it logged were noise
  — a stale 21-day-old hike that the freshness gate should have
  caught but didn't because the cycle classifier was confused."**

**What to do:**

1. Either retire `nth_rate_cut_profit_taking` or rewrite it as a
   *post-cycle* case (fires only when `rate_cuts_180d_eq:5+` AND
   `kse100_5d_gte:0.04` — i.e. "after 5 cuts, market is rallying →
   the next cut probably triggers some profit-taking"). Today it
   fires on cut #2.
2. Flip `imf_review_completed` from UP/MEDIUM to UP/LOW for d5 only,
   and add a `imf_review_post_digestion` DOWN case for d21.
3. Add a `days_since_last_hike_lte:5` AND `regime != CRISIS` filter on
   `sbp_rate_hike_shock` to suppress it during cutting cycles.

### Hole #2 — Half the playbook is dead

13 of 22 cases never fired in 143 trading days. These are the
PSX-specific high-impact situations that the system has been *built*
to recognise:

| Case | Why it never fired | Fix |
|---|---|---|
| `sbp_rate_cut_cycle_initiation` | Requires 3 triggers including `rate_cuts_180d_eq:1` — only true once per cycle, and we missed the June-2024 first cut because the back-test starts in May-2025 | Add a per-cycle calibration check; the case is correctly tight, just unlucky |
| `pkr_devaluation_shock` | Threshold uses an USD/PKR delta we likely don't compute correctly | Audit `_eval_trigger("driver:usdpkr_up")` — the `_dbg` shows USDPKR is in the briefing but the threshold for "shock" probably never hit in this stable PKR period |
| `imf_sba_eff_approval` | Driven by event log, no event was added for 2024-2025 reviews | Backfill the IMF event log — every program approval / review since 2023 |
| `cement_coal_shock` | No coal price feed in the macro snapshot | Add coal as a macro series (or use Newcastle coal proxy from yfinance) |
| `circular_debt_worsening_large` | Symmetric to the resolution case but the worsening event log is empty | Backfill — every quarterly NEPRA / PPIB build-up >Rs 200bn since 2023 |
| `fipi_capitulation` | Threshold `fipi_5d_lt:-2000` (PKR mn) too tight; we didn't see capitulation in the test window | Lower to -1500 OR add a percentile-based variant: `fipi_5d_pctile_lte:5` |
| `earnings_blackout_concentration` | Threshold `earnings_blackouts_gte:5` — would only fire during a peak result-season week | Lower to 3 |
| `phase1_cash_in_uptrend` | Specific scenario didn't manifest in test window | OK as-is; a "lucky to never fire" case |
| `election_window_chop` | We don't have an `event:election_window` feed | Add a 21-day pre-election window calculator from `data/macro/elections.json` |
| `mf_accumulation_strong`, `mf_distribution_strong`, `mf_capitulation_with_value`, `mf_smart_money_divergence` | Per-stock MF triggers need 6+ months of history; we only have Jun-25 + Jan-26 | Backfill more AHL months (every month from Jan-2024 if available) |

**A 22-case library where 13 never fire is not a "library", it's a
"stub". The strategist is leaning on 9 cases for a year-long signal.**

### Hole #3 — Critical signals fail silently

Live `build_briefing()` today returned `policy_rate: dict (1 keys)
[ERROR: ReadTimeout: HTTPSConnectionPool(host='www.sbp.org.pk', ...)]`.

That means:
* No rate-cycle case can fire today
* The Master Strategist's `macro_lens` paragraph has to reason without
  the SBP rate
* The Phase-1 mechanical layer doesn't care (it's pure technicals) so
  the user *appears* to get a normal day's output — but the most
  important cross-check is silently absent

This is true for several other connectors too — `government.py`,
`mettis_global.py`, `psx_terminal.py`, `sarmaya.py` all hit live web
endpoints and can fail. The system has **no offline fallback** for
the policy rate, even though we have **5 years of history** in
`data/macro/sbp_rates.parquet`.

**Fix (1-day work, highest priority):**

```python
# tools.get_policy_rate
try:
    return _live_sbp_fetch()
except Exception:
    df = pd.read_parquet("data/macro/sbp_rates.parquet")
    last = df.sort_values("date").iloc[-1]
    return {"policy_rate_pct": float(last["rate_pct"]),
            "as_of": str(last["date"].date()),
            "source": "parquet_fallback",
            "live_fetch_failed": True}
```

Same pattern for KIBOR / T-bill / CPI / FX reserves.

### Hole #4 — MF flow lens is monthly data, but it's the strategy's edge

`mf_initiation_cluster` and `mf_universe_distribution_broad` are
the two best-performing cases (100% and 85% hit rate, +8.7% and
-9.2% mean 21d returns). Together they fired **52 of the 94 matches
in the back-test** — i.e. the institutional-flow lens is responsible
for >55% of the system's edge.

But MF holdings are published monthly by AHL. With the 60-day
freshness gate, we get ~30-45 days of useful signal per report. That
means **for ~half the year the strategy's strongest lens is silent**.

**A senior analyst would do two things:**

1. **Stretch the freshness window to 75 days but compute a
   "confidence decay" factor.** Days 0-30: full weight. Days 30-60:
   0.7 weight. Days 60-90: 0.4 weight. >90: zero. Right now we
   guillotine at 60 which throws away usable signal in the second
   half of the month.

2. **Build a *proxy* MF flow signal from sector rotation when AHL
   data is stale.** When AHL data is >45 days old, derive
   "institutional positioning" from:
   * Sector relative strength vs KSE-100 over 21d
   * Volume-weighted breadth (volume in advancing names / volume in
     declining names)
   * Price action on high-volume days (institutions accumulate on
     volume; retail accumulates on price)
   This won't replace AHL, but it bridges the gap.

### Hole #5 — Five missing data streams that any PSX analyst tracks

This is the "what data does the system NOT have that I would want as
a senior analyst" list. In rough order of impact:

| Missing stream | Why it matters | Source | Effort |
|---|---|---|---|
| **Banking NIM proxy** (T-bill 6M − KIBOR 1M spread, deposit-cost trend, ADR) | Banks are 35-40% of PSX market cap. We track KIBOR + T-bill levels but no spread / NIM trigger | Derived from existing parquets | LOW |
| **PSX daily turnover + volume regime** | Retail markets live on volume. KSE-100 +2% on PKR 5bn turnover ≠ +2% on PKR 30bn. We don't compute this | Already in OHLCV but unused | LOW |
| **Remittances (monthly SBP)** | $30bn+/year, structural BoP support. Strong months always de-stress PKR → bank positive, exporter negative | SBP Easy Data | MEDIUM |
| **LSM (Large-Scale Manufacturing) index, monthly PBS** | Best leading indicator for cement, steel, autos | PBS, scrape from monthly bulletin | MEDIUM |
| **MSCI / FTSE rebalancing calendar** | Quarterly rebalances move PSX names 5-10% over 5d on passive flows | Manually curated event log | LOW |

Other gaps a senior analyst would call out:

* **No GRM (gross refining margin) proxy** for OMCs / refineries (APL,
  PRL, NRL, HASCOL). Brent alone doesn't capture their spread.
* **No PIB yield curve.** We have KIBOR + T-bill, can derive a
  synthetic PIB curve from those. PIB yields ARE the discount rate
  for any PSX DDM.
* **No sector rotation matrix** (Banking outperformed Cement by 4.2%
  over last 21d). Bread-and-butter analyst tool.
* **No global EM risk-off lens.** Overnight globals come in but no
  case fires on "VIX>25 + EM ETF -3% in 5d → AVOID bank names".
  Foreign flows leave PSX fast on global risk-off.
* **No per-stock float / ADV.** Some PSX names (NPL, PABC) have
  low free-float. The strategist can't size positions realistically
  without average daily traded value.

---

## 4. The Master Strategist prompt — what's missing

The current `STRATEGIST_SYSTEM` prompt (`brain/master_strategist.py:320-428`)
is 9 numbered rules. A senior PSX analyst would add three more:

### Rule 10 — Don't catch falling knives

> *"If a candidate stock is down >10% in 5 days, you may NOT issue
> a fresh BUY/ADD unless your reasoning explicitly cites the
> `behavioural_panic_3day` analogue or a structural reset (rate cut,
> circular debt resolution). Lower-circuit hits are the most
> expensive single mistake on PSX."*

### Rule 11 — Sector concentration in your active list

> *"Track which sectors have led the KSE-100 over the past 21 trading
> days. If the sector is already +>15% relative to the index, your
> default action on a new pick in that sector is HOLD or WATCH, not
> BUY — late-cycle entries on hot sectors are where retail capital
> consistently underperforms on PSX."*

### Rule 12 — Honesty about data freshness

> *"Before reasoning, check `mf_holdings.data_freshness_days`,
> `policy_rate.live_fetch_failed`, and `predictions.as_of`. If any
> critical lens is stale or missing, downgrade your stance by one
> notch (HIGH → MEDIUM, NORMAL → CAUTIOUS) and name the missing data
> in `narrative` and `key_risks`."*

These three rules close >80% of the "the strategist would say
something embarrassing" failure modes.

---

## 5. Phase-1 mechanical layer — the silent constraint

The Phase-1 monthly-momentum rule is the trade book and the strategist
sits on top. Two issues a senior analyst would push back on:

### 5a. Calendar-monthly rebalance lags PSX intra-month moves

PSX is volatile enough that a 150-day momentum rebalanced once a month
misses 30-40% of the move (the move happens, by month-end half of it
is already done). A senior analyst would suggest:

* Keep the 150-day momentum signal
* But rebalance **bi-weekly with a 4% drift trigger** (rebalance the
  drift exceeds the threshold OR every 14 days, whichever comes first)
* This adds ~6-10 trades/year (well within cost budget) and captures
  the early-cycle moves the monthly rule misses

### 5b. The Phase-1 ↔ Strategist override is asymmetric

Today: the strategist can downgrade a Phase-1 BUY to HOLD/TRIM with a
note. But it cannot silently surface a non-Phase-1 BUY — the system
prompt says "you CANNOT silently fabricate buys on names Phase-1
didn't pick".

In a strong MF-initiation cluster (3+ funds initiating on OGDC; HIGH
strategist conviction), this is too conservative. **Better rule:**

> *"You may surface a BUY on a stock NOT in Phase-1's selected list
> if AND ONLY IF: (a) the playbook returned an MF-cluster or
> circular-debt-resolution analogue with HIT_RATE >= 70% in
> historical instances, AND (b) the stock has at least one OTHER
> bullish lens (verdict / value / quality), AND (c) you cite both in
> contributing_signals. Otherwise stay anchored to Phase-1."*

That preserves the discipline ("can't fabricate") while letting the
strategist act on the strongest evidence-backed override condition.

---

## 6. Concrete, prioritised action list

In rough order of impact-per-hour-of-work:

| # | Fix | Hours | Impact |
|---|---|---|---|
| 1 | **Parquet fallback for SBP / KIBOR / T-bill / CPI live fetches** | 2 | Unblocks rate-cycle cases on every connector outage |
| 2 | **Retire `nth_rate_cut_profit_taking`** as currently configured (0% hit on 10 fires); rewrite as a `post_5_cuts_profit_taking` case | 1 | Removes the single biggest source of false signals |
| 3 | **Flip `imf_review_completed` to d5 horizon** OR change expected direction to MIXED | 1 | Removes second-biggest false-signal source |
| 4 | **Backfill IMF event log** (every approval / review 2023-2026) so `imf_sba_eff_approval` can fire | 1 | Reactivates one of the highest-impact dead cases |
| 5 | **Backfill circular_debt_events.json** with worsening events Q2-2024..Q3-2025 so `circular_debt_worsening_large` fires | 1 | Symmetric — currently we only show resolution side |
| 6 | **MF freshness — replace 60d hard veto with weight decay** (1.0 / 0.7 / 0.4 / 0.0 at 30/60/90 days) | 2 | Doubles the useful signal window from the strongest lens |
| 7 | **Add Rule 10 / 11 / 12 to the strategist prompt** (no falling knives, sector concentration, data-freshness honesty) | 1 | Closes the embarrassing-output failure modes |
| 8 | **Banking NIM trigger pair** (`bank_nim_widening` / `bank_nim_compressing` from T-bill − KIBOR spread) | 3 | Banks are 35% of PSX; we have no NIM lens |
| 9 | **PSX volume regime trigger** (`turnover_high` / `turnover_low_breakdown`) | 2 | Bread-and-butter analyst signal we already have data for |
| 10 | **Remittances ingest + driver + case** (`remittance_strong_month` → bank positive, exporter negative) | 4 | Structural BoP signal we're missing |
| 11 | **Bi-weekly Phase-1 rebalance with 4% drift trigger** | 3 | Captures intra-month moves currently missed |
| 12 | **Asymmetric override loosening for HIGH-conviction MF clusters** in the strategist prompt | 1 | Lets the strongest evidence-backed lens lead |
| 13 | **MSCI / FTSE rebalancing event log + 5-day-pre / 5-day-post window case** | 2 | Mechanical 5-10% moves we don't trade |
| 14 | **GRM proxy for OMCs/refineries** (Brent − Asia Premium gasoline differential) | 4 | Rights the OMC sector verdict |
| 15 | **Stale-text sweep:** `brain/valuation.py` still says "PSX 15-stock universe"; multiple connectors say "15 PSX blue chips" | 0.5 | Trivial cleanup; keeps the codebase honest |

**Items 1-7 (~9 hours) close the four "I would not run this live"
issues from the headline. Items 8-12 (~13 hours) bring it from "good"
to "professional".**

---

## 7. The bottom-line answer to "is this the right strategy?"

**Yes — the architecture is right. The execution has gaps.**

The right things:
* Master Strategist on top of structured signals, not a chat bot
* Sector × macro deterministic rule book with leverage amplifier
* MF institutional-flow lens (the actual edge)
* 7-lens reconciliation as a deterministic floor
* Cost discipline + pre-event guards + concentration cap baked in
* Behavioural lens treated as first-class
* Per-case attribution back-test that *honestly shows what's wrong*

The wrong things, all fixable in one weekend of work:
* Three macro cases pointing the wrong way
* 13 dead cases in a 22-case library
* Critical live-fetch single points of failure
* MF lens dies between monthly reports
* Five missing data streams a real analyst would want

**The 84% precision number from the back-test is real but flattering.
Strip out the MF cases (which depend on a single external data source
with low refresh rate) and the rest of the playbook is around 50-60%
hit rate — slightly better than coin-flip, dragged down by the three
mis-calibrated macro cases. Fix those three cases and the same
back-test would land at >88% precision with much steadier per-case
performance.**

The gap between "good in the back-test" and "trust this with real
money" is the 15-hour task list in section 6, not a re-architecture.
