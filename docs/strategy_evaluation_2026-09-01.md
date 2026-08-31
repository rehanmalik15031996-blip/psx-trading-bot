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
model-quality problem.

| | 2021-2026 (4.9y) | vs Buy & Hold |
|---|---|---|
| CAGR | **+29.35%** | +21.53% (+7.8pp) |
| Sharpe | **1.37** | 1.01 |
| Max drawdown | **-20.63%** | -26.03% |
| Sortino / Calmar | 1.50 / 1.42 | — |
| Information ratio | 0.37 | — |

(Backtest re-run today, current production code, full history through 2026-08-31,
including the T-bill-on-cash accounting fix shipped this session — see §5.)

---

## 2. Strategy performance, year by year

| Year | Strategy | Buy & Hold | Alpha | Read |
|---|---|---|---|---|
| 2021 | +3.7% | -8.0% | **+11.7pp** | Correctly avoided the bear market, earned T-bill on cash |
| 2022 | +13.6% | -13.5% | **+27.1pp** | Same — best year, pure downside avoidance |
| 2023 | +25.1% | +42.3% | **-17.2pp** | Structural: concentrated top-5/7 momentum book missed index-leader mega-caps in a broad rally |
| 2024 | +48.4% | +62.5% | **-14.1pp** | Same pattern — strong absolute return, still trails cap-weighted beta |
| 2025 | +62.7% | +43.4% | **+19.3pp** | Best relative year — momentum names led the rally |
| 2026 YTD (through Aug) | -10.3% | -2.3% | **-8.0pp** | Concerning — see §2.1 |

**The 2023/2024 shortfall is structural, not a flaw to "fix.**" A 5-7 name equal-weight
momentum book will never track a 35-name cap-weighted index tick-for-tick in a broad bull
run — that's the price of the concentration that also produces the much smaller
drawdowns and the 27-point alpha in 2022. This is a legitimate strategy design choice
(momentum + concentration → better risk-adjusted, worse beta-capture), and the honest
framing is "this is not a beta product," not "this needs fixing."

### 2.1 2026 is the one year that should worry you

2026 is the only year where the strategy is losing *more* than the market it's trying to
beat, on a much smaller magnitude — that's a different failure mode than 2023/2024 (which
lost less than a raging bull market). A few contributing threads, all confirmed in this
session:

- The momentum filter has been in and out of CASH repeatedly this year (Jan/Feb invested,
  Mar/Apr CASH, May/Jun invested, Jul CASH again) — a choppy, sideways 2026 is close to
  the worst case for a 150-day-momentum + monthly-rebalance design: it's fast enough to
  get whipsawed but slow enough to always be a month late to the turn.
- The one deliberate fix aimed at this year's biggest known miss (the April 2026 IMF
  SLA rally) was tested this session against data it was never tuned on and **made 2026
  worse, not better** (see §5.2) — it was correctly held back, but it means the miss is
  still open and unaddressed.
- 2026's benchmark itself is down (-2.3%) — a genuinely difficult tape, not just a bot
  problem. Still, losing 8 points of alpha in a down year is the pattern most worth a
  dedicated post-mortem once a full year of 2026 data exists.

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

## 6. Recommendations, prioritized

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

**Soon:**
5. When re-attempting the IMF-floor / recovery-basket idea, explicitly test it against
   the months that postdate its own construction before considering it validated — the
   existing `validate_strategy_fixes.py` methodology should be extended to require this,
   not left as a manual step someone might skip.
6. Do a dedicated post-mortem on 2026 once the full year is in — it's the one period
   where the strategy is losing more than its benchmark, a different (and more
   concerning) failure mode than the well-understood 2023/2024 beta-capture gap.
7. Now that `news_scoring` will run manually/on-demand rather than automatically,
   consider whether `predict_with_claude` should actually be built into
   `scripts/walkforward_predictions.py` (currently aspirational text in its own
   docstring, never implemented) — the deterministic-rules proxy it uses today
   understates what the real LLM pipeline can do, as §3 just demonstrated.

**Worth doing, not urgent:**
8. `scripts/local_scheduler/` (added this session) now runs the whole pipeline locally
   on the same cadence as GitHub Actions — consider wiring a simple alert (email/Slack/
   push) off `health_check.py`'s RED badges instead of relying on someone opening the
   dashboard, since that's precisely how the API-key breakage went unnoticed for 3.5
   months in the first place.
9. Decide on `AGENTS.md` / `CLAUDE.md` (drafted this session, still uncommitted) — they
   capture exactly this kind of institutional knowledge (the git-divergence trap, the
   "check for uncommitted brain/ changes" habit) so a future session doesn't have to
   rediscover it from scratch.
