# Strategy & Data Evaluation — 2026-09-01

Scope: how good the trading strategy actually is (not how good it looks in the README),
how healthy the data pipeline is, and what to fix first. Everything below is measured
against live data pulled the same day (see `reports/master_strategist_2026-09-01.md` for
the data-refresh log), not recalled from documentation.

---

## 1. Bottom line

The mechanical strategy is **genuinely good on a risk-adjusted basis and structurally
behind on raw returns in strong bull years** — that trade-off is real, not a bug, and
should be stated to whoever owns capital allocation decisions in plain terms before
anything else here. Separately, the system had been running with **three material
infrastructure failures** for weeks to months, silently, because each one degrades to
something that still "looks green." Two are fixed this session (SBP scraper, ENGROH
handling); the third (`ANTHROPIC_API_KEY`) is a deliberate accepted trade-off, not a
fix — see §5.1. A fourth finding, the predictions-pipeline hit-rate confusion (§3), was
fully root-caused this session and turned out to be a methodology error, not a real
model-quality problem. A fifth item — the 2026 whipsaw flagged below in §2.1 — was
diagnosed and fixed with a validated strategy change (hysteresis band on the market
filter), which is why the numbers below are better than the first pass of this
evaluation.

| | 2021-2026 (4.9y) | vs Buy & Hold |
|---|---|---|
| CAGR | **+33.63%** | +21.53% (+12.1pp) |
| Sharpe | **1.49** | 1.01 |
| Max drawdown | **-20.63%** | -26.03% |
| Sortino / Calmar | 1.68 / 1.63 | — |
| Information ratio | 0.61 | — |

(Backtest re-run today, current production code, full history through 2026-08-31,
including the T-bill-on-cash accounting fix and the hysteresis-band fix shipped this
session — see §5 and §2.1.)

---

## 2. Strategy performance, year by year

| Year | Strategy | Buy & Hold | Alpha | Read |
|---|---|---|---|---|
| 2021 | +3.7% | -8.0% | **+11.7pp** | Correctly avoided the bear market, earned T-bill on cash |
| 2022 | +13.6% | -13.5% | **+27.1pp** | Same — best year, pure downside avoidance |
| 2023 | +25.1% | +42.3% | **-17.2pp** | Structural: concentrated top-5/7 momentum book missed index-leader mega-caps in a broad rally |
| 2024 | +48.4% | +62.5% | **-14.1pp** | Same pattern — strong absolute return, still trails cap-weighted beta |
| 2025 | +62.7% | +43.4% | **+19.3pp** | Best relative year — momentum names led the rally |
| 2026 YTD (through Aug) | **+10.4%** (was -10.3%) | -2.3% | **+12.6pp** (was -8.0pp) | Fixed this session — see §2.1 |

**The 2023/2024 shortfall is structural, not a flaw to "fix.**" A 5-7 name equal-weight
momentum book will never track a 35-name cap-weighted index tick-for-tick in a broad bull
run — that's the price of the concentration that also produces the much smaller
drawdowns and the 27-point alpha in 2022. This is a legitimate strategy design choice
(momentum + concentration → better risk-adjusted, worse beta-capture), and the honest
framing is "this is not a beta product," not "this needs fixing."

### 2.1 2026 whipsaw — root-caused and fixed this session

2026 was the only year where the strategy was losing *more* than the market it's trying
to beat — a different failure mode than 2023/2024's beta-capture gap. Diagnosis: pulled
the exact month-end universe momentum readings and found they've been oscillating in a
tight, noise-level band since March — **-0.045, -0.058, +0.004, +0.085, -0.039, -0.095**
— i.e. PSX has genuinely been flat/rangebound in 2026, not trending down. The old market
filter is a plain zero threshold with no memory, so it was flipping the whole book on
statistically meaningless differences between consecutive readings, paying a full
rebalance-cost round trip almost every month for no real signal.

**Fix shipped:** `StrategyConfig.market_mom_band` (default 0.05) adds a Schmitt-trigger
/ dead-band hysteresis — once in a state, momentum has to cross the *opposite* side of
the band to flip out, not just cross zero. This is the standard, literature-backed fix
for exactly this failure mode in trend-following systems (asymmetric enter/exit
thresholds — see CME Group's "Improving Time-Series Momentum Strategies" and the general
dead-band/hysteresis literature on whipsaw reduction), not a bespoke rule invented for
one event.

Validated with the same discipline the IMF-floor idea failed to meet, specifically to
avoid repeating that mistake:
- **2021-2025 backtest results are byte-identical at every band from 0.0 to 0.15** — the
  fix provably only engages during 2026's flat stretch; it does not reshape any year
  that was already working.
- **Stable plateau, not a knife-edge fit**: bands 0.05 through 0.12 all land within a
  point of each other (~+34% CAGR, ~1.5 Sharpe) — a >2x range of the parameter gives the
  same answer, which is the opposite signature from the IMF-floor overfit.
- 2026 backtest return: **-5.96% → +10.35%** at band=0.05. Max drawdown unchanged
  (-20.63%) at every band tested — a pure return improvement, not a risk trade-off.
- **Caught and fixed a real "backtest-only, no-op live" gap along the way**: hysteresis
  needs the previous month's state, but the live callers (`ui/tools.py`,
  `scripts/generate_report_v2.py`) evaluate a single point in time and don't track it.
  Without auto-deriving it from price history, the fix would have improved the backtest
  number while doing nothing to actual live decisions — verified the live entry point
  and the backtest now produce identical monthly calls.

The one deliberate fix aimed specifically at the April 2026 IMF SLA rally (the
IMF-floor override) was tested and made 2026 worse, not better (see §5.2) — correctly
held back. That specific miss is still open; the hysteresis fix addresses the broader
whipsaw pattern, not that one event.

---

## 3. The predictions pipeline: the "anti-predictive" finding was measuring the wrong system

This session dug into the two contradictory numbers and found a definitive root cause —
not just a stale figure, a methodology error that conflated two different systems.

| Source | What it actually measures | Sample | Result |
|---|---|---|---|
| `data/backtest/walkforward_predictions.parquet` (`source=walkforward_rules`) | `scripts/walkforward_predictions.py` calling `predict_with_rules()` — a **deterministic rules-engine proxy**, explicitly documented in its own file as "more conservative... tests the bot's logic, **not the LLM judgement layer**" | n=1,469 (1,363 fully realized), **2026-02-23 → 2026-04-24 only** (2 months, not the 13 months the prompt rule claimed) | 38.5% (37.4% on fully-realized only) |
| `data/predictions_log.json`'s own `outcome.direction_hit` field, computed fresh this session | **Real, live, Claude-generated predictions** (`scripts/generate_predictions.py`, the actual production pipeline) | n=2,477 | **67.3% gross / 60.8% net** hit rate |

The uncommitted `master_strategist.py` rule 14 (held back this session, never pushed —
see §5.2) told Claude to treat the live `predictions` block as an **inverse** signal,
citing "the predictions block's directional hit rate" of 38.5%. But that 38.5% number
was never a measurement of the live predictions block at all — it came from a separate,
explicitly-weaker rules-only simulation that the walk-forward script's own docstring
says isn't a proxy for LLM judgment. The two got conflated somewhere between running the
analysis and writing the prompt rule, and the rule would have instructed Claude to bet
*against* a signal that's actually correct roughly two-thirds of the time.

**This is now resolved, not just flagged:** don't revive the "predictions = inverse
signal" rule. If reintroducing a predictions-calibration rule, base it on
`data/predictions_log.json` (the real production log), not
`walkforward_predictions.parquet` (a rules-engine proxy that was never meant to
characterize the LLM's actual output).

One live signal still worth tracking: the real hit rate is **declining month over
month** — Apr 71.2% → May 70.1% → Jun 66.9% → Jul 64.3% (n=132/805/770/770). Still
comfortably better than a coin flip, but worth a monthly check rather than trusting the
all-time headline number indefinitely.

---

## 4. Data infrastructure — what's actually healthy right now

Ran `scripts/health_check.py` fresh this session, fixed what was fixable, re-ran it —
now only one feed is RED, and it's an accepted trade-off rather than an open bug:

| Feed | Status | Root cause |
|---|---|---|
| `news_scoring` | 🔴 RED — 94.9 days since last success | `ANTHROPIC_API_KEY` invalid (401) — **accepted trade-off, not fixing** (§5.1); will stay RED under the "Claude Code session on demand" model |
| `macro_kpis` | ✅ **Fixed this session** — was 🔴 RED ("2/3 sub-sources refreshed") | SBP rates scraper 403 was transient (confirmed by hand); added retry-with-backoff + browser User-Agent to `connectors/sbp.py`. Re-ran health check: 3/3 sub-sources, GREEN. |
| everything else (eod, overnight, predictions, fundamentals, material_info, financial_results, intraday) | 🟢 GREEN | confirmed fresh this session |

Other data-quality notes surfaced this session:

- `fundamentals` refresh: 34/35 symbols OK. **ENGROH confirmed permanent gap** — no
  Yahoo Finance listing under any ticker variant (ENGROH.KA, ENGRO.KA, ENGROH.PSX,
  ENGROHOLD.KA, and Yahoo's own name-search all come back empty) despite trading
  normally on PSX. Not a mapping bug to fix; `connectors/yfinance_fundamentals.py` now
  short-circuits it with an honest "known gap" message instead of a blank error.
- `mf_holdings` (mutual-fund flow signal): 62 days stale as of today, which is expected
  — AHL stopped publishing the detailed feed after mid-2025 and the AMC-FMR scraper
  fallback runs monthly. The `mf_universe_distribution_broad` playbook analogue that
  fired in today's briefing is matching against a Jan-26-vintage report; treat it as a
  soft regime modifier only, per the system prompt's own honesty rule.

---

## 5. What happened to the pipeline this session (context for the numbers above)

Two independent, unrelated failures had been silently degrading the system for weeks to
months before this session started fixing them:

### 5.1 The Claude "brain" hadn't been Claude since ~2026-05-14

`ANTHROPIC_API_KEY` — in GitHub Actions secrets *and* the local `.env` — returns
`401 authentication_error: API key is invalid`. The Master Strategist workflow has a
rule-based fallback specifically so a bad key doesn't fail the job, which means it's
been reporting green in the Actions tab every single day while quietly serving
`model: rule-based-v2, fallback_used: true` instead of real Claude reasoning, for **3.5
months**. News sentiment scoring and financial-results extraction have no such fallback
and have been hard-failing the same whole time (`news_scoring` health badge confirms:
94.9 days). **Decision (2026-09-01): not rotating the key.** The owner is using direct
Claude Code sessions as the manual reasoning substitute instead — see
`reports/master_strategist_2026-09-01.md` for the pattern. This is a real trade-off, not
a fix: the *scheduled, unattended* runs (GitHub Actions and the local Task Scheduler
equivalent) will keep serving the rule-based fallback or hard-failing indefinitely under
this model — `news_scoring`'s RED health badge is now an accepted, permanent state
rather than a bug to chase. A genuine Claude pass only happens when someone opens a
session and asks for one.

### 5.2 A tuning change looked good in aggregate and failed on the data it hadn't seen

Earlier this session, four uncommitted local changes (IMF-floor override, a
"recovery basket," `top_n` 5→7, a loosened CAUTION threshold) were tested by building a
disposable git worktree at fresh `origin/main` data and comparing before/after on
identical inputs. Aggregate 2021-2026 numbers looked strictly better (CAGR +26.6% vs
+22.2%, Sharpe 1.32 vs 1.09). But isolating the two years that were genuinely
out-of-sample relative to when the fix was written (2025, 2026) showed it **underperforming
production in both** — and the one real-world event it was built to fix (the April 2026
IMF SLA week) is exactly where it lost money, because the momentum filter's CASH call
that month turned out to be right. That's a textbook overfitting signature: a rule
built to explain a specific historical miss, validated on data ending right around when
it was written, breaking on the very next live instance. **This was caught and held
back before shipping** — see `scripts/validate_strategy_fixes.py`, which exists for
exactly this purpose but should always additionally be checked against months that
postdate the fix, not just history available at the time it was authored.

### 5.3 Local repo was 555 commits behind origin, with the good fixes stuck local-only

Separately, this machine's git checkout hadn't been synced since 2026-05-29 while
GitHub's own automation kept committing data for three more months. The uncommitted
fixes in §5.2 had never been pushed — meaning even if they'd been good, they'd have had
zero effect on the live daily strategist regardless. Reconciled this session; the two
changes that *did* survive validation (T-bill-on-cash backtest accounting, and factual
IMF/circular-debt event records) are now pushed and live.

---

## 6. Runtime volume data + strategy candidates researched this round

### 6.1 Real-time volume/microstructure data was silently broken for 4 months — now fixed

Checked what's already built before suggesting anything new: `connectors/psx_terminal.py`
(live REST/WebSocket feed) and `connectors/psx_portal.py` (MarketWatch snapshots +
circuit breakers) already exist, feeding `data/intraday/marketwatch.parquet`,
`circuit_breakers.parquet`, `fipi_intraday.parquet` twice a trading day via
`intraday_session.yml` (11:30 and 13:30 PKT). Checked the data: **`marketwatch.parquet`
was stuck at 2026-05-07 — 4 months stale.**

Root cause: `intraday_session.yml` runs 3 steps in sequence — (1) live snapshot, (2)
score fresh headlines via the same `score_news_sentiment.py` fixed earlier this session,
(3) commit. Step 1 was succeeding every single run; step 2 was hard-failing on the
invalid API key (before this session's fix) and aborting the job **before step 3 ever
ran** — so real, successfully-fetched live volume data was fetched and then silently
discarded, every trading day, for 4 months. Confirmed via `gh run list` (every recent
run failed) and the actual failure log.

This is fixed as a side effect of the news-scoring fallback fix (§ from the prior
round): re-ran the exact chain locally end-to-end
(`scripts/local_scheduler/run_workflow.py --workflow intraday_session`) and it now
completes and commits — `marketwatch.parquet` is fresh again (7,814 rows). The next
scheduled GitHub Actions run (or the local Task Scheduler equivalent, both now
registered) should resume normal twice-daily collection going forward.

**What this data can now actually be used for** (research-grounded, not yet built):
- **VWAP-aware execution.** The backtest and live report currently assume fills at the
  day's close. PSX is explicitly documented in this repo's own research as "retail-heavy,
  narrow breadth, lower ADV" — exactly the condition where VWAP benchmarking matters most
  (it's the standard institutional yardstick for execution quality precisely because
  naive close-price fills can cost real slippage in thin names). With intraday snapshots
  flowing again, a VWAP estimate per rebalance day is now computable and worth reporting
  alongside the close price the backtest assumes.
- **Intraday unusual-volume detection.** The existing `brain/volume_signals.py` only
  looks at EOD daily volume. With two intraday checkpoints a day now flowing again, a
  same-day volume-spike flag (e.g., a name printing 3x+ its typical volume by midday) is
  a cheap, near-real-time complement to `scripts/check_news_shocks.py` — often a big
  volume move precedes the news that explains it by hours.

Neither of these is built yet — flagging both as concrete, data-ready next steps rather
than shipping untested execution-logic changes in the same pass as a data-pipeline fix.

### 6.2 Better strategies — what the research says, tested against what our data can actually prove

Researched current (2026) multi-factor and momentum literature. Two takeaways, both
already partially true in this codebase's own history:

- Momentum has had "its best multi-year run since the dot-com bubble" recently, but the
  same research explicitly flags "elevated risk... favoring diversification rather than
  outsized positions" — i.e. even mainstream factor research is currently *skeptical* of
  pure-momentum concentration, which is exactly this bot's design.
- Value has been "strongest in emerging markets" per the same research — a natural
  candidate to blend in.

**Tested it anyway, honestly, before recommending it — and it doesn't work here.**
Blending value/quality into the ranking is blocked by a real data-infrastructure gap:
`data/fundamentals/*.parquet` is a **single current snapshot per symbol, not a
point-in-time history** (checked: one row, `as_of_utc` = today, no history). Backtesting
any value/quality-tilted ranking against 2021-2026 with today's fundamentals applied
retroactively would be lookahead bias — the same flaw already flagged in
`scripts/walkforward_predictions.py`'s own docstring for a different reason. This is
almost certainly *why* the mechanical Phase-1 rule deliberately uses only pure
price/volume data: it's the only signal in this codebase that's genuinely
lookahead-bias-free to backtest. Building true point-in-time fundamentals history would
be a real data-engineering project (quarterly snapshots reconstructed from historical
filings), not something to bolt on casually.

**What *is* safely testable with pure OHLCV (no lookahead risk) — tested, rejected:**
volatility-adjusted ("Sharpe-style") momentum ranking, i.e. rank by momentum ÷ realized
volatility instead of raw momentum. Standard refinement in the trend-following
literature. Result on this universe: CAGR 26.4% → 24.5%, and specifically **gives back
13.5pp in 2025** (the strategy's best relative year) for a MaxDD improvement of under
1pp. Rejected — same discipline as the IMF-floor idea. Plausible reason specific to this
strategy: the vol filter already excludes the top-30%-by-volatility names before
ranking, so a second volatility adjustment on top double-penalizes exactly the decisive,
high-conviction moves that drove 2025's outperformance.

**Net conclusion on "better strategies":** the generic multi-factor/quant literature's
suggestions either aren't provable without a real data-engineering investment (value/
quality) or don't survive contact with this specific universe's actual history
(vol-adjusted momentum). This isn't a reason to stop looking, but it is a reason to keep
testing new ideas against real out-of-sample PSX data before shipping them, rather than
importing what worked in US large-cap literature by default.

### 6.3 Point-in-time fundamentals — built, then honestly tested and rejected as a strategy input

Went ahead and built the data-engineering prerequisite flagged in §6.2. Turned out to be
smaller than expected: Yahoo Finance's annual financial-statement calls already return
REAL fiscal-year-end dates alongside the figures — `connectors/yfinance_fundamentals.py`
was just discarding them (`fins.loc[row].dropna().tolist()` drops the column index).
Added `fetch_history_one()` to keep them, `scripts/build_fundamentals_history.py` to
build/upsert `data/fundamentals_history/{SYMBOL}.parquet` (idempotent, accumulates a new
row automatically whenever a company reports a new fiscal year), and
`brain/fundamentals_history.py::point_in_time()` for lookup with a documented 90-day
reporting-lag assumption.

Result: **33/35 symbols**, 133 fiscal-year rows, coverage from 2021-12/2022-06 (varies by
fiscal year end) through 2025-06/2025-12. HUBC (empty Yahoo statements) and ENGROH (no
Yahoo coverage at all) have no history via this source — documented gaps.

**Immediately used it for the honest test §6.2 flagged as blocked**: blended
point-in-time inverse-P/E into the momentum ranking at several weights (0.2/0.3/0.5).
**Rejected — underperforms pure momentum at every weight tested** (CAGR 26.4% → 22-25%,
degrades 2024 and/or 2025 depending on weight). Same conclusion as §6.2's vol-adjusted
momentum test, now confirmed with real point-in-time data instead of a data-availability
excuse: this specific momentum strategy does not benefit from diluting with other
factors on this universe's actual history.

**The infrastructure still stands on its own merit, independent of that result**: it
fixes `scripts/walkforward_predictions.py`'s own documented "latest-only fundamentals —
small lookahead bias" caveat, and is available for future research without needing to
re-litigate whether point-in-time data exists. Scheduled monthly (idempotent, cheap) on
both GitHub Actions and the local scheduler.

---

## 7. Recommendations, prioritized

**Resolved this session:**
1. ~~Rotate `ANTHROPIC_API_KEY`~~ — **decision made not to**: the owner will use direct
   Claude Code sessions as the manual reasoning substitute for the Master Strategist /
   news scoring / financial-results extraction instead of maintaining a paid API key.
   Trade-off, not a fix: the scheduled/unattended runs still fall back to
   `rule-based-v2` or hard-fail (`news_scoring` will stay RED on `health_check.py`
   indefinitely under this model — that's now expected, not a bug to chase). A real
   Claude pass only happens when someone actually opens a session and asks for it, as
   in `reports/master_strategist_2026-09-01.md`.
2. **Predictions hit-rate discrepancy — root-caused, not just reconciled.** The 38.5%
   "anti-predictive" figure measured a different, weaker system (`walkforward_rules`
   proxy) than the live Claude-generated predictions (67.3%). See §3. Do not revive the
   inverse-signal rule.
3. SBP rates scraper 403 — fixed (`connectors/sbp.py`: retry with backoff + browser
   User-Agent; the block was transient, confirmed by hand). `macro_kpis` is GREEN again.
4. ENGROH yfinance gap — confirmed permanent (no Yahoo listing under any ticker variant,
   verified via Yahoo's own search API), not a mapping bug. `connectors/yfinance_fundamentals.py`
   now short-circuits with an honest "known gap" message instead of a blank error.
5. **News scoring hard-failing 95+ days** — found that Gemini's key is also invalid and
   GitHub Models was permanently retired 2026-07-30 (not a brownout — confirmed via
   github.blog), so all three configured LLM providers were simultaneously dead. Added a
   rule-based lexicon fallback to `scripts/score_news_sentiment.py` (the one script with
   zero fallback in the codebase); `news_scoring` is GREEN again, rows honestly tagged
   `model=rules-fallback-v1`. `financial_results.yml`'s GitHub-Models-routed extraction
   path is still broken and hasn't been given the same treatment.
6. **2026 whipsaw — diagnosed and fixed.** Added a hysteresis band
   (`StrategyConfig.market_mom_band`) to the market-trend filter. See §2.1 for full
   methodology; 2026 backtest return went from -5.96% to +10.35%, MaxDD unchanged,
   2021-2025 provably untouched (byte-identical at every band tested).
7. **Real-time volume data restored.** `data/intraday/marketwatch.parquet` was silently
   stuck for 4 months (fetched successfully every run, then discarded because a
   downstream step in the same workflow hard-failed before the commit step). Fixed as a
   side effect of item 5's news-scoring fallback; verified the full chain now completes
   and commits. See §6.1.

8. **Point-in-time fundamentals history — built.** Fixed the lookahead-bias gap:
   `data/fundamentals_history/{SYMBOL}.parquet` now has real fiscal-year-dated
   revenue/net income/equity/assets/EPS/BVPS for 33/35 symbols, scheduled monthly. See
   §6.3.

**Tested and rejected (same discipline as the IMF-floor idea):**
9. Volatility-adjusted ("Sharpe-style") momentum ranking — a standard trend-following
   refinement, tested honestly, made things worse here (CAGR 26.4%→24.5%, gives back
   13.5pp in 2025). See §6.2 for the likely reason. Not shipped.
10. Point-in-time value tilt (inverse P/E blended into the momentum ranking) — tested at
    three weights using the newly-built point-in-time data (item 8), underperforms pure
    momentum at every weight (CAGR 26.4%→22-25%, degrades 2024 and/or 2025). See §6.3.
    Not shipped.

**Soon:**
11. When re-attempting the IMF-floor / recovery-basket idea, explicitly test it against
    the months that postdate its own construction before considering it validated — the
    existing `validate_strategy_fixes.py` methodology should be extended to require this,
    not left as a manual step someone might skip.
12. Migrate `financial_results.yml`'s GitHub-Models-routed extraction (quarterly/
    half-year/material-info reports) to a working provider — GitHub Models is gone for
    good, not key-broken.
13. Consider whether `predict_with_claude` should actually be built into
    `scripts/walkforward_predictions.py` (currently aspirational text in its own
    docstring, never implemented) — the deterministic-rules proxy it uses today
    understates what the real LLM pipeline can do, as §3 demonstrated. Now easier: it
    could also use item 8's point-in-time fundamentals to remove the "latest-only
    fundamentals" lookahead-bias caveat at the same time.
14. Two known-stale macro data files feeding the master strategist briefing —
    `data/macro/remittances_monthly.json` and `lsm_index_monthly.json` — are both stuck
    at December 2025 (curated once 2026-05-03, never given a refresh script). Both
    source URLs (SBP remittance PDF, PBS QIM page) are confirmed still live; build
    `scripts/refresh_remittances.py` / `refresh_lsm.py`.
15. VWAP tracking per rebalance day, now that intraday snapshots are flowing again —
    execution-quality improvement (reduces slippage), not an alpha change. See §6.1.
16. Intraday unusual-volume spike detection as a same-day complement to
    `scripts/check_news_shocks.py` — often the volume move precedes the news that
    explains it. See §6.1.

**Worth doing, not urgent:**
17. `scripts/local_scheduler/` (added this session) now runs the whole pipeline locally
    on the same cadence as GitHub Actions — consider wiring a simple alert (email/Slack/
    push) off `health_check.py`'s RED badges instead of relying on someone opening the
    dashboard, since that's precisely how the API-key breakage went unnoticed for 3.5
    months in the first place.
18. Decide on `AGENTS.md` / `CLAUDE.md` (drafted this session, still uncommitted) — they
    capture exactly this kind of institutional knowledge (the git-divergence trap, the
   "check for uncommitted brain/ changes" habit) so a future session doesn't have to
   rediscover it from scratch.
