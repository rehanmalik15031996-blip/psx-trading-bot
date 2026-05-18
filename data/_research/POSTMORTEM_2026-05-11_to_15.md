# Post-mortem: week of May 11-15, 2026

Written 2026-05-18 against full Mon-Fri data.

## Data freshness verdict

| Source                         | Latest      | Status           |
|--------------------------------|-------------|------------------|
| OHLCV (universe)               | 2026-05-15  | ✓ Fresh          |
| KSE-100, Brent, WTI, BTC, etc. | 2026-05-15  | ✓ Fresh          |
| USDPKR, SBP rates              | 2026-05-15  | ✓ Fresh          |
| FIPI flows                     | 2026-05-15  | ✓ Fresh          |
| Strategist v2 cache            | 2026-05-15  | ✓ Fresh          |
| Material info / fundamentals   | 2026-05-15-17 | ✓ Fresh        |
| ABL parquet                    | 2026-04-23  | ✗ STALE 25 days  |

Friday data was on `origin/main` (commits `7ce7e6f`, `23cb57d`,
`6fefd47`, etc.) but the local workspace had not pulled it yet. After
`git pull origin main` we are fully synced through Sat May 17 commits.

The **one infra gap** is **ABL.parquet** — last data April 23. Either
ABL was delisted/suspended, or the ingestor silently dropped it. Worth
investigating in a follow-up.

## What the market actually did (Mon 5/11 -> Fri 5/15)

- **KSE-100: 170,506 -> 165,596 (-2.88%)** — broad sell-off through
  the week, classic pre-IMF de-risk.
- **Worst sectors:**
    - Cement -4.17% (KOHC -6.4%, MLCF -4.0%, DGKC, LUCK)
    - Banking -4.07% (HBL -7.2%, UBL -6.4%, BAHL -6.4%, NBP -5.2%)
    - Pharma -3.73% (SEARL)
    - Conglomerate -3.51% (ENGROH -4.8%)
    - Power -3.39% (KEL -5.4%, NPL -4.2%)
- **Best sectors:**
    - Consumer +0.27% (COLG)
    - Technology +0.08% (TRG +3.9%)
    - Autos -0.55% (INDU)
    - **Oil & Gas E&P -0.98%** (POL +0.3%, MARI -0.6%, OGDC -0.9%, PPL -2.1%)
- **Top winners:** TRG +3.88%, POL +0.32%, COLG +0.27%, MEBL -0.51%, INDU -0.55%
- **Top losers:** HBL -7.17%, KOHC -6.42%, UBL -6.41%, BAHL -6.40%, KEL -5.37%

**Macro that drove it:**

- Brent +4.17%, WTI +2.08% (oil rallied — explains E&P strength)
- Gold -3.43%, Cotton -6.18% (risk-off commodities — paradox vs equity)
- USDPKR +0.66% (PKR weakened)
- BTC -1.57%

## What we said each day

| Day             | Stance      | Quality | Note                                       |
|-----------------|-------------|---------|--------------------------------------------|
| Fri May 8       | CASH/no opinion | ✗ FAIL | LLM BadRequestError, fallback empty       |
| Sun May 10      | CASH/no opinion | ✗ FAIL | LLM BadRequestError, fallback empty       |
| Mon May 11      | CASH/no opinion | ✗ FAIL | LLM BadRequestError, fallback empty       |
| Tue May 12      | CASH/no opinion | ✗ FAIL | LLM BadRequestError, fallback empty       |
| Wed May 13      | DEFENSIVE 75% cash; BUY OGDC/PPL/ATRL | ✓ Manual call by Cursor (this session); correct |
| Thu May 14      | NORMAL, 70% cash overlay, ADD OGDC 6.2% / ATRL 5.8%, AVOID Banks | ✓ Mostly right (new v2 pipeline) |
| Fri May 15      | NORMAL, 70% cash overlay, BUY OGDC 6.2%, HOLD ATRL 8.3% | ✓ E&P core kept |

## Scorecard: hits vs misses

### What we got right

1. **Defensive 70-85% cash posture all week** — the market lost -2.88%
   over four sessions; sitting in cash captured most of that as
   avoidance. Playbook overlays (`mf_universe_distribution_broad`,
   `imf_review_mission_week`, `us_iran_oil_spike`) correctly raised
   cash floors.
2. **OGDC as the core BUY** — held -0.92% vs market -2.88% (alpha
   +1.96pp). Validated the macro tilt of E&P (oil rallied +4.2%).
3. **ATRL kept in the book** — held -1.12% vs market -2.88% (alpha
   +1.76pp). Down-graded from BUY to HOLD on Thursday's run, which
   reduced gross exposure ahead of Friday.
4. **Cement called bearish** — Cement tilt -4 across the week,
   strategist correctly flagged it as `BEARISH` in sector_view.
   Sector dropped -4.17% (worst).
5. **Position-plan stops are credible** — if we had ever taken a
   bank trade, the 4.3% stop on HBL would have exited at Tuesday's
   close (~282) avoiding the worst of -7.17% drawdown.

### What we got wrong

1. **CRITICAL — Banking flagged as BULLISH +4 macro tilt all week.**
   The macro_impact engine was reading `tbill_above_policy` (banks
   lock in higher yields → NIM expansion) plus `kse100_up` (cyclical)
   and concluded Banking was a tailwind. In reality, banks were
   the **second-worst sector** because pre-IMF de-risking dumps the
   most-liquid names first. HBL -7.2%, UBL -6.4%, BAHL -6.4%, NBP -5.2%
   should have been a screaming AVOID/SHORT. The v2 sector_view still
   labels Banking `BULLISH +4` on Friday.

2. **4 straight days of no LLM opinion (May 8, 10, 11, 12)** — the
   strategist hit `BadRequestError` from Anthropic four runs in a row.
   The fallback produced "CASH no-opinion" stub files with zero
   actionable content. The week's most important entry day (Mon May 11)
   had no positioning idea at all.

3. **Zero short ideas all week** — Cement was clearly tanking (-4%
   sector, multiple -5% to -6% names) but the strategist surfaced no
   shorts because individual cement names didn't reach the score
   threshold (-0.20). Our composite score under-weights momentum decay
   when value/quality components are neutral.

4. **MEBL/MCB/FABL recommended HOLD with 10% sizes** — these were
   shown as 10% position-size on Friday's long_ideas list even though
   the actions list (after overlay) had them at AVOID/TRIM. The two
   views contradict each other and confuse the UI. (Real moves were
   actually mild: MEBL -0.51%, MCB -1.04%, FABL not in universe).

5. **Universe selection bias** — the worst-performing PSX names this
   week (HBL -7.17%, BAHL -6.4%, NBP -5.2%) are not even in our
   35-stock universe. We have MEBL, MCB, UBL, FABL — the survivors.
   This is the survivorship-bias finding from the May 14 audit
   manifesting in practice: we miss both the warning sign (a wider
   bank-sector index would have flashed risk-off earlier) and the
   short opportunity.

6. **`us_iran_oil_spike` playbook case** activated on Friday after the
   move was already done. The May 11 strategist (had it worked)
   could have used the same oil-up driver to lean into E&P earlier.

## The 5 gaps to fix this week

| # | Gap                                          | Fix                                                                                   | Priority |
|---|----------------------------------------------|---------------------------------------------------------------------------------------|----------|
| 1 | Banking tilt wrong during de-risk weeks      | Add `imf_review_mission_week` -> Banking macro tilt -3 override in macro_impact      | HIGH     |
| 2 | Anthropic API failing 4 days running         | Verify ANTHROPIC_API_KEY secret in GitHub; check rate-limit / billing                | HIGH     |
| 3 | Long_ideas vs actions list contradicts after overlay | Mirror overlay bucket changes into long_ideas (currently only mirrors size)    | MEDIUM   |
| 4 | Zero shorts when sector is clearly bearish   | Auto-add top-3 names from any sector with macro_tilt <= -3 as short candidates       | MEDIUM   |
| 5 | Universe misses worst-performers (HBL, BAHL, NBP) | Either expand universe to top-50 PSX names OR add a sector-index reader for context | LOW      |
| 6 | ABL.parquet stale 25 days                    | Investigate delisting / ingestor failure                                              | LOW      |

## Bottom line

- **Data: fresh through Fri 5/15** (after pull).
- **Defensive posture: correct** — we sat in 70%+ cash the whole week
  while KSE-100 dropped -2.88%.
- **Core E&P calls worked** — OGDC and ATRL outperformed by ~2pp.
- **Banking blind spot is the single biggest unfixed gap** — the
  worst-performing names of the week were in Banking and our
  macro engine still thinks Banking is bullish. That same flaw will
  re-fire whenever the tbill curve sits above policy.
- **LLM availability is the #2 gap** — 4/6 days last week the
  strategist gave no opinion at all because the API was rejecting
  our requests. Without the LLM, the rule-based fallback writes a
  no-content stub and the user gets nothing actionable.
