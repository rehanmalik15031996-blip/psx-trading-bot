# Manual news sentiment runbook (Cursor-as-scorer)

> **Use this when `scripts/score_news_sentiment.py` keeps failing because the
> Anthropic API key is invalid / revoked / out of credit.**
> Workflow: `News sentiment scoring (.github/workflows/news_scoring.yml)`.

This runbook lets the Cursor agent itself stand in for Claude Haiku as the
news scorer. Output goes to the **exact same** `scored_news.parquet` that
the LLM workflow writes to, so every downstream consumer (predictor,
briefing, UI tabs, shock detector) picks it up automatically — no UI or
pipeline change required.

---

## TL;DR — three commands

```bash
# 1. Pull fresh RSS articles -> data/news/_pending_articles.json
python scripts/_pull_news_for_manual_scoring.py

# 2. Open scripts/_apply_manual_news_scores.py, paste/extend the SCORES
#    dict with one entry per new article_id (see "Scoring rules" below),
#    then run:
python scripts/_apply_manual_news_scores.py

# 3. (Optional) Detect HIGH-confidence shocks. Exit code 7 = shock fired.
python scripts/check_news_shocks.py
```

Then commit + push (the standard sentinel will protect the manual scores
when the LLM workflow eventually starts running again):

```bash
git add data/news/scored_news.parquet \
        data/news/shock_log.json \
        data/_health/news_scoring.json \
        data/_health/_history_news_scoring.parquet \
        scripts/_apply_manual_news_scores.py
git commit -m "data: cursor-strategist news sentiment $(Get-Date -Format 'yyyy-MM-ddTHHmmZ')"
git pull --rebase origin main
git push origin main
```

---

## How the pipeline works end-to-end

```
                  +-------------------------+
                  |  scripts/_pull_news...  |   (no LLM — only RSS HTTP)
                  +-------------------------+
                              |
                              v
              data/news/_pending_articles.json   (130-ish unscored articles)
                              |
            ----------------- + ------------------
            |                 |                  |
            v                 v                  v
   you read each title   you assign     you save SCORES[id] in
   and summary in JSON   (sentiment,    scripts/_apply_manual_
                         confidence,    news_scores.py
                         category,
                         affected,
                         one_liner)
                              |
                              v
                  +-------------------------+
                  |  scripts/_apply_manual..|   (writes parquet + health)
                  +-------------------------+
                              |
                              v
              data/news/scored_news.parquet   (canonical cache, dedup'd)
                              |
            ----------------- + -----------------------------------------
            |               |                |                   |
            v               v                v                   v
   UI tabs           Predictor         Master strategist    check_news_shocks.py
   (ui/news_sentiment    (24h news      (cited inside the      (writes
    + ui/app.py +         tilt feature)  rule-based and         shock_log.json
    daily_report etc.)                   LLM master_decision)   on >=HIGH events)
```

**Key files (do not delete):**

| Path | Role |
|---|---|
| `scripts/_pull_news_for_manual_scoring.py` | Pulls RSS + Mettis + intl news, dumps unscored to JSON. **No LLM.** |
| `scripts/_apply_manual_news_scores.py`     | Holds the `SCORES` dict and writes back to parquet. **This is the file you edit per run.** |
| `data/news/_pending_articles.json`         | Ephemeral. Auto-deleted by `_apply` once scored. |
| `data/news/scored_news.parquet`            | Canonical cache. **Single source of truth for sentiment.** |
| `data/news/shock_log.json`                 | Append-only log of HIGH-confidence shocks. |
| `data/_health/news_scoring.json`           | Health stamp consumed by the UI health tile. |
| `scripts/check_news_shocks.py`             | Reads the parquet, fires shocks. Same script the workflow uses. |

---

## Scoring rules (mirrors the Claude Haiku prompt)

For every article the cache row needs:

```python
SCORES["<article_id>"] = (
    sentiment,        # float in [-1.0, +1.0]
    confidence,       # "LOW" | "MED" | "HIGH"
    category,         # "MACRO" | "POLICY" | "COMPANY" | "COMMODITY"
                      # | "GLOBAL" | "GEOPOLITICS" | "OTHER"
    affected_symbols, # list of PSX tickers from UNIVERSE, [] if index-level
    one_liner,        # <=120 chars, why this matters for PSX
)
```

### Sentiment scale

| Magnitude | Meaning | Examples |
|---|---|---|
| `±0.55 .. ±0.80` | Big catalyst | "SBP cuts policy rate 200 bp", "Trump pauses Iran strike", "IMF tranche released" |
| `±0.25 .. ±0.45` | Real news, sector-level | "Current account back to deficit", "FDI -68% MoM", "Cement divestment $102m" |
| `±0.10 .. ±0.20` | Mildly directional | "Exports +14%", "Gold falls 0.5%", "M2 falls Rs451bn" |
| `±0.05` | Barely matters | "Govt retires Rs501m debt", "PM reviews PSDP", "SBP reserves +$23m" |
| `0.0` LOW OTHER | Pure noise | sports, celebrity, foreign tech earnings, unrelated IPOs |

### Confidence

- **HIGH** — Only when the article directly names a documented catalyst
  that moves the index or a named sector: rate decisions, IMF tranche /
  staff-level agreement, FII flow shocks, current-account prints, oil
  shocks, war headlines. Limit yourself to **<= 8 HIGH per day** — they
  trip the shock detector.
- **MED** — Real news, but second-order or already partially priced.
- **LOW** — Color, retrospective coverage, peripheral global headlines,
  noise.

### `affected_symbols`

- Only use tickers from the actual universe printed at the bottom of the
  `_pull` output (currently 35 names). Do not invent.
- Leave `[]` for index-level / macro / commodity stories — the macro tilt
  is applied separately by the aggregator.
- Always populate when the article is explicitly about one of:
  `OGDC / PPL / POL / MARI` (E&P), `HBL / UBL / NBP / MEBL / MCB / BAHL /
  FABL` (banks), `DGKC / KOHC / LUCK / FCCL / MLCF` (cement),
  `KEL / HUBC / KAPCO / NPL` (IPP), `TRG / SYS` (tech),
  `PSO / APL / ATRL` (refining/marketing), etc.

### `one_liner`

One sentence answering "**why does this matter to PSX in the next 1-5
trading days?**" Keep it <= 120 chars. This is what the LLM strategist
reads back when it builds tomorrow's briefing.

### Macro tilt heuristic for the index

Use this when you're not sure which way to score:

| Tilt | Drivers |
|---|---|
| **Bullish (+)** | rate cut, IMF tranche, oil drop, US-Iran ceasefire, FII net-buy, current-account surplus, MSCI up-weight, reserves up |
| **Bearish (-)** | rate hike, IMF mission stalled, oil spike, geopolitical escalation, FII net-sell streak, current-account deficit, FDI drop, sukuk auction failure |

---

## Daily / intraday cadence

The LLM workflow runs **3x per trading day** at:

- `02:00 UTC` = 07:00 PKT (pre-open)
- `08:00 UTC` = 13:00 PKT (midday)
- `13:00 UTC` = 18:00 PKT (post-close)

You can mirror the same cadence manually, or run only at moments when
something material is happening. After scoring, the predictor's 24h
news-tilt picks it up on its next run automatically — no other commands
required.

---

## Sentinel protection — never get overwritten

`_apply_manual_news_scores.py` stamps every row with:

```
model = "cursor-claude-sonnet-4-5-manual"
```

When the real LLM workflow eventually starts running again with a valid
API key, `scripts/score_news_sentiment.py` uses
`drop_duplicates("article_id", keep="last")` and the workflow always runs
**after** your manual rows already exist in the cache. The LLM will only
score **new** articles (anything not already in the cache is skipped),
so your manual scores stay frozen forever.

> Practical: if you re-score the same article later, just bump the
> `scored_at` (the apply script does this automatically using
> `datetime.now(timezone.utc)`) and re-run. The dedup keeps your newest.

For the strategist JSON, the parallel sentinel lives in
`brain/agents/pipeline.py::_human_override_present()` — anything with
`model: cursor-*` or `human_override: true` is preserved and the
workflow's would-be output goes to `*_workflow_autorun.json`.

---

## Shock detector contract

`check_news_shocks.py` exit codes:

- `0` — no shock
- `7` — shock detected (CI dispatches `predictions.yml`; locally we just
  log + commit)
- non-zero non-7 — script crashed

It fires on either:

- one article with `confidence == HIGH` and `|sentiment| >= 0.30`, **or**
- a broad market shock: `>= 5 HIGH-confidence articles in a single day`
  with avg `|sentiment| >= 0.4`.

If exit code 7 fires, the predictor will get triggered earlier than its
normal schedule next time the workflow runs. Locally just commit the
updated `shock_log.json` along with everything else.

---

## Where it shows up in the UI

Once the parquet is updated and pushed:

- **`ui/app.py` → Briefing tab** — re-renders `macro_sentiment(24h)` and
  `ticker_sentiment(symbol, 24h)` automatically on the next page load.
- **`ui/news_sentiment.py`** — the standalone Sentiment tab now shows
  the new scored rows with sources, one-liners, and the rolling 24h
  weighted score.
- **`ui/daily_report.py`** — pulls fresh news into the morning report.
- **`ui/short_ideas.py`** — recomputes the news-tilt component on the
  short candidate ranks.
- **`ui/overnight.py`** — the overnight panel re-aggregates and tags
  symbols.
- **Health tile** — shows `news_scoring: ok=true` again instead of red.

Streamlit Cloud picks up the new commit on `main` within ~30 seconds of
the push; no rebuild needed.

---

## Quick checklist before pushing

- [ ] `_pending_articles.json` no longer exists in `data/news/`
- [ ] `data/news/scored_news.parquet` got `git status` flagged as Modified
- [ ] `data/_health/news_scoring.json` shows `ok: true` and a fresh
      `last_run_at`
- [ ] `check_news_shocks.py` ran without crash (exit code 0 or 7)
- [ ] Any new HIGH-confidence shocks make narrative sense vs the
      strategist call for the day
- [ ] You committed with a message that starts with `data: cursor-strategist
      news sentiment <UTC timestamp>` so it's grep-able later

---

## Worked example — 2026-05-19

The first manual run scored 130 fresh articles:

- 128 substantive, 2 neutral
- average sentiment **-0.011** (mixed: Iran-pause bullish vs
  current-account / FDI bearish)
- breakdown: MACRO 39 · POLICY 19 · COMPANY 17 · COMMODITY 10 ·
  GLOBAL 9 · GEOPOLITICS 4 · OTHER 11

Two HIGH-confidence shocks fired:

1. **Banks -0.35 HIGH** — "Current account back to $324m deficit" hits
   HBL, UBL, NBP, MEBL, MCB, BAHL, FABL.
2. **Broad market +0.59 HIGH** — 5 articles confirming Trump paused the
   Iran strike → KSE-100 +2,400 pts in early Tue trade.

Cache went from `1,625` rows (with dupes) → `1,397` dedup'd rows.

Commit: `3bd19bf` ("data: cursor-strategist news sentiment 2026-05-19T09:40Z").
