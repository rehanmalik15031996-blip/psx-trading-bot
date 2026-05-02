# Data-gathering plan — closing the INCONCLUSIVE-test gaps

_Generated: 2026-05-02_
_Companion: [`docs/strategy_fixes_evaluation_2026-05-02.md`](strategy_fixes_evaluation_2026-05-02.md) · [`scripts/validate_strategy_fixes.py`](../scripts/validate_strategy_fixes.py)_

The validation harness left **6 INCONCLUSIVE verdicts** because the data we
have on disk is too thin to run the test honestly. Five of them are real
data gaps; one is already mostly solvable from data we already have.

This document is the **operational plan** to close every gap so that, at
the next sprint, we can re-run `scripts/validate_strategy_fixes.py` and
either ship the deferred rules or kill them with confidence.

For every gap, this document gives:
- the **rule** that's deferred
- the **specific data** we need
- the **public source** (URL, frequency)
- the **ingest script to build** (if not already on disk)
- the **time estimate** and the **earliest realistic date** the test will be runnable

---

## Gap 1 — MF freshness weight-decay rule  *(highest impact)*

| | |
|---|---|
| Deferred test | T2 — replace 60-day MF hard veto with weight decay |
| What we need | ≥12 monthly AHL "Mutual Funds Equity Holdings" reports |
| What we have | **2 reports** (June 2025, January 2026) parsed correctly into `data/flows/mf_top_holdings_summary.parquet` |

### Sub-gap 1A — *we have 14 more PDFs on disk that are MISNAMED*

`data/raw/mf_holdings/` contains **16** PDFs but only 2 are real MF
Equity Holdings reports. The other 14 are:

- AHL **Monthly Market Performance** reports (e.g. `MutualFundsEquityHoldings-2025-04_68124e87.pdf` is actually titled "PSX Performance — April 2025")
- AHL **Market Strategy** notes (e.g. the 2024-12 report is "The recent correction is a window of opportunity")
- AHL **Sector outlooks** in disguise

The downloader picked them up because all REP-300 reports share the same
URL pattern. They are **valuable in their own right** (they contain
weekly MF flow PKR amounts, turnover, equity AUM ratios, sector tables)
but they are **not** the data we need to test MF freshness decay.

### Action 1A — fix the AHL discovery / ingester (4h)

1. **Update `scripts/ingest_ahl_mf_holdings.py`** to read the title in the
   PDF metadata or page 1 BEFORE downloading: skip files whose title
   contains "Performance", "Strategy", "Outlook", etc. and only keep
   files whose title contains "Mutual Funds" + "Equity Holdings".
2. **Move the 14 misfiled PDFs** to `data/raw/ahl_market_reports/`
   for the new ingester (Action 1C below).
3. **Re-discover the actual missing months.** AHL publishes Equity
   Holdings ~2 weeks after month-end. We need 2024-05 through 2026-04
   (24 reports). With the misnamed file removal, the discovery list
   needs to be rebuilt from one of:
   - AHL Research portal: https://arifhabibltd.com/research
     (login-walled; needs an AHL Trade account — already free signup)
   - AHL Research API: https://arifhabibltd.com/api/research/open
     filtered for `report_type=178` AND `title contains 'Equity Holdings'`
   - Google search `site:forms.ahletrade.com "MutualFundsEquityHoldings"`
     gives quarterly batches of URLs (this is how the original 16 were
     found).

### Action 1B — backfill 22 missing AHL Equity Holdings reports (3h, after 1A)

After fixing the discovery, re-run with `--download` and parse. Budget
~10 minutes per report (download + pdfplumber parse). Target 22 new
months → 24 total months on disk.

### Action 1C — ingest the 14 misnamed AHL reports as a NEW data stream (6h)

Build `scripts/ingest_ahl_market_performance.py` that parses the
"Monthly Market Performance" reports for:

- **PSX daily turnover** (page 2-3 has 30-day history)
- **MF / SLIC / Banks / Companies / Foreigner / Individual flows
  (PKR-bn weekly)** — this is the local-flow breakdown we don't have
  anywhere else
- **Equity AUMs %** (the percentage of total mutual-fund AUMs in equity vs debt)
- **Sector ownership tables** (MF / Banks / SLIC / Companies / Foreign
  share by sector)

Output to:
- `data/flows/local_flows_weekly.parquet` (weekly net flows by investor type)
- `data/flows/equity_aums_monthly.parquet` (MF equity allocation history)
- `data/flows/sector_ownership_monthly.parquet`

This gives us **monthly + weekly** flow data going back to ~2023 with no
extra data discovery — just smarter parsing of files we already have.

### Action 1D — re-run TEST 2 (1h, after 1A-1C)

After 24 months are on disk, re-run `scripts/validate_strategy_fixes.py`
and let TEST 2 produce a verdict on the weight-decay rule. **Expected
date: end of May 2026 if Actions 1A-1C are done in this sprint**.

---

## Gap 2 — Rate-cut profit-taking rewrite

| | |
|---|---|
| Deferred test | T1b — proposed rule fires on (5+ cuts AND universe up >=4% in 5d) |
| What we need | More rate-cut cycle history than the 26 SBP decisions since 2020 |
| What we have | 26 decisions covering 6 years (4 cycles); only 1 historical event satisfies both conditions |

### Action 2 — backfill SBP rate decisions to 2010 (4h)

Source: SBP **Monetary Policy Statements** archive
https://www.sbp.org.pk/m_policy/index.asp

The SBP MPS list goes back to 2009. Each statement is a PDF with the
decision date and the new policy rate. We have ~26 since 2020; backfill
**~50 more from 2009-2019** to get a 17-year sample.

Build `scripts/backfill_sbp_rate_decisions.py`:

```
1. Crawl https://www.sbp.org.pk/m_policy/index.asp listing of MPS announcements.
2. For each year 2009-2019, fetch each MPS PDF.
3. Parse: decision date + new rate (regex: r"polic.*rate.*?(\d+\.\d+)\s*%").
4. Append to data/macro/_policy_rate_history.json (idempotent).
```

**Caveat:** the 2009-2014 cycle was very different (post-IMF SBA, very
high rates). Including it broadens N but also adds regime-different
observations — segment results by sub-decade in the validator.

**Earliest realistic verdict:** end of May 2026 (data-gathering only).

---

## Gap 3 — IMF d5/d21 reaction validation

| | |
|---|---|
| Deferred test | T-NEW (not in original harness) — verify IMF review d5 reaction empirically |
| What we need | The IMF event log we just shipped |
| What we have | **DONE** — `data/macro/imf_events.json` now lists 12 events from 2019 onward |

### Action 3 — extend the validation harness with an IMF test (3h)

Add `test_imf_review_horizon` to `scripts/validate_strategy_fixes.py`:

```python
For each event in data/macro/imf_events.json with type in
('review_sla','review_board_approval'), compute:
  - fwd 1d / 5d / 10d / 21d universe-eq-weighted return
  - fwd 1d / 5d / 10d / 21d bank-basket return
  - fwd 1d / 5d / 10d / 21d E&P-basket return
Bucket by macro-context-at-the-time:
  - benign  (no concurrent rate-up / pkr-weak driver)
  - hostile (concurrent macro shock)
PASS verdict if d5 mean is +1.5pp ABOVE baseline AND benign d21 is positive.
```

This is something we can **build and run in this sprint** (no new data
needed — we have OHLCV + the event log).

**Earliest realistic verdict:** this week (after we add the test).

---

## Gap 4 — Bi-weekly rebalance with low transaction costs

| | |
|---|---|
| Deferred test | T7 — bi-weekly with 4% drift trigger only worth it if costs drop |
| What we need | Round-trip costs <30bps |
| What we have | 100bps round-trip (PSX broker commission + CGT + slippage) |

### Action 4 — defer indefinitely; revisit only when costs change

This is **structural**, not a data-gap. PSX retail commissions are
~25-50bps one-way; CGT on gains is 15%; slippage is ~5-10bps on the
liquid names. The 100bps round-trip estimate is realistic for our retail
size. Costs can only drop if:
- We move to an institutional rate (would need ≥Rs 100mn AUM)
- CGT regime changes (likely not soon)
- We grow into top-10 names with 20bps slippage

**Action:** Document this as "structurally deferred — re-test when
broker comms < 25bps OR portfolio AUM > Rs 50mn." Add a check in the
validation harness that prints "still uneconomic at <COST>%" so we
revisit at the right time.

---

## Gap 5 — Banking NIM via spread (T-bill 6M − KIBOR 6M)

| | |
|---|---|
| Deferred test | T3a — failed because we only have 3M points, not the 6M we'd want |
| What we need | T-bill 6M and 12M; KIBOR 6M; PIB 3Y/5Y/10Y daily history |
| What we have | Only T-bill 3M and KIBOR 3M (the 6M/12M/PIB columns are all-null) |

### Action 5 — backfill the full SBP yield curve (4h)

Sources (in priority order):
- **SBP M2M dashboard** — has all maturities daily, but only the
  current snapshot (no archive). Need to scrape over time, OR:
- **SBP archive** — `https://www.sbp.org.pk/ecodata/rates/m2m/index.asp`
  has historical M2M tables on a per-month basis. Each month is one
  HTML page with all the maturities.
- **SBP T-Bill auctions** — `https://www.sbp.org.pk/dmmd/T-Bill/index.asp`
  has every auction's cut-off yield since 1998. Three maturities
  (3M/6M/12M) every fortnight. Cleanest way to backfill 6M / 12M
  T-bill history.

Build `scripts/backfill_yield_curve.py`:
```
1. Crawl https://www.sbp.org.pk/dmmd/T-Bill/<year>/<month>.asp listing per-auction yields.
2. For PIB (3Y/5Y/10Y): https://www.sbp.org.pk/dmmd/pib/index.asp same structure.
3. For KIBOR 6M/12M: PSX terminal API has these but they're paywalled;
   the MUFAP daily pricing PDFs include them as a benchmark.
4. Append to data/macro/sbp_rates.parquet (the columns already exist; just NaN today).
```

After this, **re-run TEST 3a** (NIM via spread) — likely PASS once the
6M / 12M data is populated, because the (T-bill 6M − KIBOR 3M) spread
DOES move materially around SBP cycle pivots.

**Earliest realistic verdict:** end of June 2026.

---

## Gap 6 — Hot-sector concentration (Rule 11) needed a clean test

| | |
|---|---|
| Verdict was | WEAK-EFFECT (+0.44pp edge for hot, n=823 vs 618) |
| What we need | More sectors mapped + multi-cycle baseline |
| What we have | 11 sectors mapped over 5 years |

The verdict was already **strong enough** to justify the decision
(framing the rule as a *risk cap*, not a *return prediction*). No
extra data needed; the rule is already shipped in Tier B.7.

---

## Brand-NEW data streams (not in original validation, but proposed)

These are the data streams the strategy review (`docs/strategy_review_2026-05-02.md`)
flagged as missing but that we never built ingesters for. Each one is a
small project on its own.

### A. Daily PSX turnover (volume in PKR-mn)

| | |
|---|---|
| Why we want it | Universe-level liquidity proxy; turnover spikes confirm regime changes |
| Best source | PSX EOD API: `https://dps.psx.com.pk/historical` (free, daily JSON) |
| Backfill cost | ~2h for the ingester + 30 min to backfill 5 years |
| Validation | After ingest, add `test_volume_regime_universe` to validation harness — already shown as +0.57pp 5d edge per-stock; the universe rollup likely amplifies |

### B. Remittances (monthly USD-bn)

| | |
|---|---|
| Why we want it | Core PKR-supply driver; high remittance month → bank deposit growth → bank trade |
| Best source | SBP Statistical Bulletin → "Workers' Remittances" monthly PDF (free, ~21st of next month) |
| Backfill cost | ~3h for the parser + 30 min to backfill 5 years |
| Validation | New `test_remittances_bank_lift` — does a +20% YoY remittance month lift banks over the next 21d? |

### C. Large-Scale Manufacturing (LSM) index

| | |
|---|---|
| Why we want it | Real-economy nowcast; Cement / Auto demand correlate with LSM growth |
| Best source | Pakistan Bureau of Statistics: `https://pbs.gov.pk/lsm` (monthly, ~6 weeks lag) |
| Backfill cost | ~3h ingester + parse historical PDFs |
| Validation | New `test_lsm_cyclical_lift` — does positive LSM growth lift cement/auto/conglomerate? |

### D. Banking NIM proxy from quarterly results

| | |
|---|---|
| Why we want it | The clean version — each bank's actual reported NIM, not a yield-curve proxy |
| Best source | KSE listed-company financials (quarterly PDFs on PSX); HBL/UBL/MCB/MEBL/FABL all report NIM |
| Backfill cost | ~6h for the bank-quarterly parser + 1h to backfill 5y of 5 banks |
| Validation | Replace the policy-rate-level NIM proxy with actual NIM; should TIGHTEN the existing T3b PASS verdict |

### E. MSCI / FTSE rebalancing calendar

| | |
|---|---|
| Why we want it | PSX index inclusion / exclusion drives 5-15% moves in the affected names over the inclusion month |
| Best source | MSCI: monthly press release + quarterly review schedule on `msci.com` |
| Backfill cost | ~1h ingester (events are infrequent — ~4/year) |
| Validation | Add `imf_or_msci_rebalancing_event` playbook case — already validated in academic literature on EM index inclusion |

---

## Prioritized work list — what to do this sprint

### Tier I — biggest leverage (~13h total)

1. **Action 1A** — fix AHL discovery + filename validation (4h)
2. **Action 1B** — backfill 22 missing AHL Equity Holdings reports (3h)
3. **Action 1C** — build the new AHL Market Performance ingester (6h)
   → unlocks weekly MF flow data we don't have anywhere else

### Tier II — strong validation, no new ingester (~3h)

4. **Action 3** — add the IMF d5/d21 horizon test to the validator (3h)
   → confirms or refutes the d5-flip we just shipped on Tier B.8

### Tier III — needed for the deferred rule (~4h)

5. **Action 5** — backfill 6M / 12M T-bill yields + PIB curve (4h)
   → re-validates the failed T3a (NIM via spread) test

### Tier IV — multi-day projects (defer to next sprint)

6. **Daily PSX turnover ingester** (2h, then re-run volume tests)
7. **Remittances ingester** (3h)
8. **LSM index ingester** (3h)
9. **Bank quarterly NIM ingester** (6h)
10. **MSCI/FTSE rebalancing ingester** (1h)

### Tier V — structural / no work needed

11. **Action 4** — bi-weekly rebal — DOC ONLY: re-test when costs <25bps
12. **Action 2** — backfill 2009-2019 SBP decisions (4h, low priority)
    → improves rate-cut sample size but those cycles were regime-different

---

## What I recommend for the NEXT message

Pick one of these three plays:

1. **"Run Tier I" (~13h)** — fix AHL discovery, backfill the missing
   Equity Holdings reports, and build the AHL Market Performance
   ingester. By the end you'll have 24 months of MF data + 36 months of
   weekly MF/SLIC/Banks/Foreign flows. The single highest-leverage
   move; closes the biggest INCONCLUSIVE gap and unlocks a brand-new
   data stream.
2. **"Run Tier I + Tier II" (~16h)** — same as above, plus add the IMF
   horizon test to confirm the d5 flip we just shipped is sound. Ends
   the sprint with one more validated rule.
3. **"Just Tier II" (~3h)** — quickest win: add the IMF test to the
   harness so the d5 flip we just shipped has a backtest behind it.
   No new data ingestion. Defers the AHL backfill.

I'd default to **option 1** because the AHL data is the strategy's
single biggest edge and the parser fix alone is what's been blocking
us — once that's done, every future month auto-ingests with the
existing `monthly_mf_ingest.yml` GitHub Action and the freshness
problem fixes itself.

Tell me which and I'll start.
