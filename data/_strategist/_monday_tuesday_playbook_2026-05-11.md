# Monday May 11 + Tuesday May 12 — Trading Playbook

Prepared 2026-05-11 04:30 PKT by Cursor strategist (Anthropic credits exhausted).

## ONE-LINE SUMMARY

CAUTIOUS week ahead of IMF May 15. Hold ~80% cash. Deploy 20% selectively in OGDC (8%) + ATRL (7%) + PPL (5%). Watch list (NBP/FATIMA/HUBC/KAPCO) ON HOLD until IMF outcome. Avoid SEARL/TRG/PABC. Trim COLG/LOTCHEM if held.

## MONDAY MAY 11 — HOUR-BY-HOUR

### Pre-open (08:00 - 09:30 PKT)

1. Open the Streamlit dashboard. Wait for the auto-pull (top of every hour) — you should see Cursor-reasoned strategist + Cursor-reasoned predictions for May 11 with 3 BUY signals.
2. Check the **Forecast tab** for prediction_accuracy: should show 307 scored, 76.9% gross hit rate (up from "0 scored" — the UI bug is fixed).
3. Open the **Today tab** Master Strategist card and read:
   - Stance: CAUTIOUS / MEDIUM
   - Headline: "selective BUY in OGDC + ATRL"
   - International lens: S&P +0.84%, EEM +2.03% (5d +5.94%), USD softening — global tape is constructive (mild positive).
   - Playbook fired: imf_review_mission_week
4. Quick read of news headlines for any overnight surprises (Iran/IMF/PKR).

### Market open (09:30 - 10:00 PKT)

- Expect a mildly positive open (gap prior FLAT +0.49%) but with retail-led volatility.
- DO NOT chase the open print. Wait 15-20 minutes for the gap to settle.
- If KSE-100 gaps UP > +0.7%, reduce intended OGDC/ATRL/PPL entry sizes by 25% (chasing strength is the most expensive mistake in this regime).
- If KSE-100 gaps DOWN > -0.5%, this is your entry — fill the BUYs at 60% of intended size, leave 40% for an intraday weakness add.

### Mid-morning (10:00 - 12:00 PKT)

Place orders in this priority order:

1. **OGDC** — 8% target weight. Limit at or below Friday close (329.68). Do NOT pay above 332. Stop at 316 (-4%), target 353 (+7%).
2. **ATRL** — 7% target weight. Limit at or below 905. Do NOT pay above 920. Stop at 868 (-4%), target 958 (+6%).
3. **PPL** — 5% target weight. Limit at Friday close. Stop at -4%, target +6%.

Watch the FIPI flow ticker — if foreign net selling > 2bn PKR by 11:00, pause further additions.

### Afternoon (12:00 - 15:30 PKT)

- Cement / OMC / Bank weakness can drag the index — DON'T panic into AVOID names just because they're down 1-2%; that's expected.
- If any stock on the WATCH list (NBP/FATIMA/HUBC/KAPCO) trades down -2% on flat news, that's NOT a BUY — it's reinforcing the WATCH thesis. Wait for IMF.
- If TRG (results May 14) gaps -3% intraday, that's a normal pre-print blackout move — do nothing.
- Review Phase-1 score updates at 14:00 (refreshed by `eod` workflow trigger). If breadth widens above 30%, that's a regime shift worth noting in your journal.

### Close (15:30 PKT)

- Tally fills vs targets. If any of OGDC/ATRL/PPL didn't fill at limit, decide overnight whether to chase Tuesday or stay below your limit.
- Note KSE-100 close vs gap-prior estimate (+0.49%). If actual close differs by > 1% in either direction, the overnight model needs investigation (but it's a single observation — don't refit weights).
- Open the journal in the dashboard and log the day.

## TUESDAY MAY 12 — PRE-MARKET PREP

### Sunday-night-style review (08:00 PKT)

1. **What to check first:** new overnight pull will run at 04:00 UTC (09:00 PKT) — wait 5 minutes, then check `data/macro/overnight_global.parquet` last_date is 2026-05-12. If not, run `python scripts/fetch_overnight_global.py` manually.
2. **Re-read the strategist card** — if Anthropic credits are topped up before 09:00 PKT, the automated `master_strategist` workflow will overwrite my Cursor-reasoned brief with a fresh Claude run. Compare: does Claude agree with my BUY/WATCH/AVOID list? If yes, conviction goes UP. If Claude disagrees on a name, read the rationale carefully.
3. **News check**: any overnight US-Iran update? IMF spokesperson statement?

### What to do based on Monday's price action:

| Monday close | Tuesday action |
|---|---|
| KSE-100 +0.5% to +1.5% (mild rally) | Hold positions. Do NOT add. |
| KSE-100 > +1.5% (relief rally) | TRIM 25% of OGDC/ATRL/PPL — take some chips off ahead of IMF. |
| KSE-100 -0.5% to +0.5% (flat) | Hold. Add 25% to OGDC/ATRL/PPL if your Monday fills were partial. |
| KSE-100 -0.5% to -1.5% (mild down) | This is the entry day. Fill remaining 40% of OGDC/ATRL/PPL. Consider adding 2% NBP at the close (highest value upside in universe). |
| KSE-100 < -1.5% (sell-off) | DO NOT add. Wait for IMF clarity. Likely the playbook narrow_breadth_low_turnover_pause is firing — protect cash. |

### Don't do these:

- DO NOT short anything. Pakistan retail shorting is venue-restricted and the regime is wait-and-see, not actively bearish.
- DO NOT add to a losing position before IMF.
- DO NOT chase a gap-up on Tuesday — the relief move usually fades by 13:00 PKT.
- DO NOT trade TRG before its May 14 results — gap risk exceeds any 5-day edge.

## WEDNESDAY MAY 13 → THURSDAY MAY 14 — IMF EVE

- Reduce gross exposure by 25% (sell 1/4 of each position) on Wednesday close. This is the playbook's IMF-week defensive bias.
- TRG reports May 14 — close out any TRG exposure (you should have none — it's on AVOID).
- Refresh the strategist run at 09:00 PKT each day. The Cursor-reasoned brief auto-updates at every commit.

## FRIDAY MAY 15 — IMF MISSION ARRIVES

This is the binary day.

- 09:00 PKT: market opens defensive. Foreign flows likely net selling.
- 12:00 PKT (estimated): IMF press conference / readout.
- After the readout:
  - **Successful staff-level signal** → KSE-100 +1-2% relief rally. BUY KSE-100 proxies (MCB, OGDC, FFC) into the close. Lift WATCH list (NBP, FATIMA) to BUY for the following Monday.
  - **Delay or hawkish demands** → KSE-100 -1-3% sell-off. Hold remaining BUYs (you reduced 25% on Wed). Wait for confirmation of fresh mission date before re-entering.
  - **Extended mission with no clear signal** → flat to mildly negative. Position sizing stays defensive next week.

## DATA HEALTH CHECKLIST FOR YOU TODAY

These are upstream issues YOU need to fix; my Cursor reasoning works around them:

1. **Top up Anthropic credits** → unblocks `predictions`, `master_strategist`, `news_scoring` LLM paths so tomorrow runs autonomously without me. Cost is small (~$3-5 per Master Strategist run + ~$0.20 per prediction × 35 = ~$10 / day).
2. **Trigger SECP/MUFAP `mf_holdings` ingestion** → currently 344 days stale. Without this, per-stock institutional-flow signal is dead.
3. **Verify `eod.yml` is running daily** → it IS running (307 scored predictions confirms it), but the dashboard wasn't showing it because of the `actual` vs `outcome` UI bug — now fixed.
4. **Streamlit Cloud secrets** → ensure GITHUB_TOKEN is current, otherwise the in-app workflow trigger buttons fail.

## WHAT'S NEW THIS COMMIT

- UI bug fixed: prediction_accuracy now shows 307 scored / 76.9% gross hit rate (was 0)
- 8 new international tickers (NIFTY, KOSPI, STI, Shanghai, US10Y, USD/INR, USD/CNY, EUR/USD)
- FM frontier ETF surfaced (was fetched but unused)
- 3 new playbook cases (us_iran_oil_spike, imf_review_mission_week, narrow_breadth_low_turnover_pause)
- 2 new IMF/Iran active events (so the new cases can fire)
- 35 fresh Cursor-reasoned predictions for May 11
- Refreshed Master Strategist with all of the above

The dashboard will look noticeably different at next git pull.
