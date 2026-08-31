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
anything else here. Separately, the system has been running with **three
material infrastructure failures** for weeks to months, silently, because each one
degrades to something that still "looks green." Those are higher priority than any
strategy tweak: a good strategy fed stale data or missing its reasoning layer is worse
than a mediocre strategy running correctly.

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

## 3. The predictions pipeline: two contradictory numbers exist right now

This is worth a standalone section because it's actively load-bearing on how the LLM
layer is instructed to use the signal, and the two numbers disagree by 29 points.

| Source | Method | Sample | Result |
|---|---|---|---|
| `brain/master_strategist.py` rule 14 (uncommitted, held back this session) | Walk-forward simulation, 2025-06-01 → 2026-06-20 | n≈500 | **38.5% hit rate** — "anti-predictive," z≈-8.7 |
| Computed fresh this session from `data/predictions_log.json`'s own `outcome.direction_hit` field | Live logged production predictions | n=2,477, 2026-04-23 → 2026-07-30 | **67.3% gross / 60.8% net** hit rate |

These cannot both be describing the same thing well. The walk-forward figure is a
*simulation* of what predictions would have looked like over 13 months of history; the
67.3% figure is what the *actual deployed* predictor scored on *real* logged calls over
the last ~3.5 months. Two live signals worth flagging inside that 67.3%:

- **It's declining month over month**: Apr 71.2% → May 70.1% → Jun 66.9% → Jul 64.3%
  (n=132/805/770/770). Still comfortably better than a coin flip, but the trend deserves
  watching, not just the headline number.
- The 38.5%/"treat as inverse signal" rule was written into the uncommitted
  `master_strategist.py` diff that this session declined to push (see §5.2) — **it is
  not live in production right now**. Good, because if the 67.3% figure is closer to
  reality, that rule would have been actively harmful (telling Claude to bet against a
  signal that's actually right two-thirds of the time).

**Recommendation:** before reviving any calibration rule based on the 38.5% figure, re-run
that exact walk-forward methodology fresh and reconcile it against the live log. Don't
carry a stale ground-truth number into a new prompt rule just because it's already
written down.

---

## 4. Data infrastructure — what's actually healthy right now

Ran `scripts/health_check.py` fresh this session. Two feeds are RED:

| Feed | Status | Root cause |
|---|---|---|
| `news_scoring` | 🔴 RED — 94.9 days since last success | `ANTHROPIC_API_KEY` invalid (401) — see §5.1 |
| `macro_kpis` | 🔴 RED — "2/3 sub-sources refreshed" | SBP rates scraper returns 403 Forbidden when run locally (GitHub's own runners still succeed — likely an IP/User-Agent block, not a dead source) |
| everything else (eod, overnight, predictions, fundamentals, material_info, financial_results, intraday) | 🟢 GREEN | confirmed fresh this session |

Other data-quality notes surfaced this session:

- `fundamentals` refresh: 34/35 symbols OK, **ENGROH consistently fails** on yfinance's
  side ("possibly delisted; no timezone found") despite trading normally on PSX — a
  ticker-mapping issue on the Yahoo Finance side specifically, not a real delisting.
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
94.9 days). **This is still unresolved** — it requires a human to rotate the key at
console.anthropic.com and update both places it's stored. Until then, every "Master
Strategist" call — including the one manually authored this session
(`reports/master_strategist_2026-09-01.md`) — is either a rule-based fallback or a
one-off manual substitute, not the automated pipeline working as designed.

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

**Now (blocking real value):**
1. Rotate `ANTHROPIC_API_KEY` — nothing else in this list matters as much. Update
   `.env` and the GitHub repo secret. Confirm by checking `data/_strategist/latest.json`
   shows `fallback_used: false` the next trading day.
2. Reconcile the predictions hit-rate discrepancy (§3) before writing or reviving any
   prompt rule that depends on it.

**Soon:**
3. Fix the SBP rates scraper 403 (likely a User-Agent/IP block specific to non-GitHub
   runners) or add a fallback source — it's the one RED feed with no LLM dependency,
   should be the easiest to fix.
4. Investigate the ENGROH yfinance mapping so `fundamentals` reaches 35/35.
5. When re-attempting the IMF-floor / recovery-basket idea, explicitly test it against
   the months that postdate its own construction before considering it validated — the
   existing `validate_strategy_fixes.py` methodology should be extended to require this,
   not left as a manual step someone might skip.
6. Do a dedicated post-mortem on 2026 once the full year is in — it's the one period
   where the strategy is losing more than its benchmark, a different (and more
   concerning) failure mode than the well-understood 2023/2024 beta-capture gap.

**Worth doing, not urgent:**
7. `scripts/local_scheduler/` (added this session) now runs the whole pipeline locally
   on the same cadence as GitHub Actions — consider wiring a simple alert (email/Slack/
   push) off `health_check.py`'s RED badges instead of relying on someone opening the
   dashboard, since that's precisely how the API-key breakage went unnoticed for 3.5
   months in the first place.
8. Decide on `AGENTS.md` / `CLAUDE.md` (drafted this session, still uncommitted) — they
   capture exactly this kind of institutional knowledge (the git-divergence trap, the
   "check for uncommitted brain/ changes" habit) so a future session doesn't have to
   rediscover it from scratch.
