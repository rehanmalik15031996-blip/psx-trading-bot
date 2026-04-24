# PSX Bot — Data Readiness Report

**Generated:** 23 April 2026
**Tooling:** `inspect_sources.py` (pulls real data from every source, validates schema, samples records)
**Total sources inspected:** 19
**Result:** 14 GOOD · 4 REACH-ONLY · 1 FAIL (expected)

All sources returning structured records have been verified to match a declared schema.
Raw payloads are saved to `full_fetch_report.json` for anyone to audit.

---

## 1. Bot-Ready Sources (14)

These connectors return clean, parsed, typed records you can feed directly into the trading engine.

### 1.1 Layer 5 — Market Microstructure / Prices

| Source | Rows | Sample Fields | Latency |
|---|---|---|---|
| **PSX Terminal (REST)** | 8 stocks + 4 indices | `symbol, price, change_pct, volume, trades, value, high, low, timestamp` | ~12 s (per-symbol call) |
| **PSX Indices (DPS)** | 18 indices | `index, high, low, current, change, change_pct` | ~3 s |
| **PSX Market Watch** | **482 symbols** | `symbol, sector_code, index, ldcp, open, high, low, current, change, change_pct, volume` | ~4 s |
| **PSX Circuit Breakers** | 16 upper-locked + 2 lower-locked | `symbol, ldcp, open, high, low, current, change, change_pct, volume` | ~3 s |

**Sample — PSX Terminal:**
```json
{"symbol": "OGDC", "price": 319.34, "change_pct": -0.00551,
 "volume": 2642398, "trades": 6237, "high": 321.2, "low": 315.84}
```

**Sample — PSX Market Watch (all 482 listed symbols in one call):**
```json
{"symbol": "FNEL", "sector_code": "0813", "index": "ALLSHR",
 "ldcp": 1.38, "current": 1.70, "change_pct": 23.19, "volume": 286823348}
```

**Sample — PSX Circuit Breakers (stocks hitting ±10% limits):**
```json
{"symbol": "ADMM", "ldcp": 76.19, "current": 83.81, "change_pct": 10.00,
 "volume": 1849445}
```

This combination gives us **complete daily market coverage** — every listed stock, every index,
and a real-time read on which names are hitting circuit limits.

### 1.2 Layer 3 — Behavioral Flows

| Source | Rows | Sample Fields |
|---|---|---|
| **SCStrade FIPI/LIPI** | 9 participant categories + 10 sector flows | `category, buy_pkr_mn, sell_pkr_mn, net_pkr_mn` (participants); `sector, buy_usd_mn, sell_usd_mn, net_usd_mn` (sectors) |

**Sample — participant-level flows (who is buying/selling):**
```json
[
  {"category": "Foreign",                     "buy_pkr_mn": 15.48, "sell_pkr_mn": -15.21, "net_pkr_mn": 0.27},
  {"category": "BANKS / DFI",                 "buy_pkr_mn": 2.29,  "sell_pkr_mn": -0.93,  "net_pkr_mn": 1.36},
  {"category": "BROKER PROPRIETARY TRADING",  "buy_pkr_mn": 21.25, "sell_pkr_mn": -21.75, "net_pkr_mn": -0.50},
  {"category": "INDIVIDUALS",                 "buy_pkr_mn": 153.68, "sell_pkr_mn": -150.54, "net_pkr_mn": 3.14},
  {"category": "MUTUAL FUNDS",                "buy_pkr_mn": 5.54,  "sell_pkr_mn": -8.38,  "net_pkr_mn": -2.84}
]
```

This is the **single most important flow signal** in PSX — it tells us whether foreigners,
domestic institutions, or individuals drove the day's move. Data is as of 22-Apr-2026
(SCStrade publishes with a T+1 lag).

### 1.3 Layer 1 — Macro / Monetary

| Source | Extracted Fields |
|---|---|
| **SBP Policy Rate + KIBOR** (single page, many fields) | `policy_rate_pct`, `ceiling_rate_pct`, `floor_rate_pct`, `weighted_on_repo_pct`, `kibor[3-M/6-M/12-M]`, `tbill_yields_pct[1-M/3-M/6-M/12-M]`, `pib_yields_pct[2-Y/3-Y/5-Y/10-Y/15-Y]`, `reserves_usd_mn[sbp/banks/total]` |
| **SBP M2M (PKR/USD)** | `m2m_rate, weighted_avg_bid, weighted_avg_offer, as_on` |
| **yfinance commodities** | `commodity, ticker, open, high, low, close, volume, change_5d_pct` |

**Sample — SBP dashboard (one scrape → entire rate + yield curve):**
```json
{
  "as_on": "23-Apr-26",
  "policy_rate_pct": 10.5,
  "ceiling_rate_pct": 11.5,
  "floor_rate_pct": 9.5,
  "weighted_on_repo_pct": 9.79,
  "kibor": {"3-M": {"bid": 11.01, "offer": 11.26},
            "6-M": {"bid": 11.19, "offer": 11.44},
            "12-M": {"bid": 11.45, "offer": 11.95}},
  "tbill_yields_pct": {"1-M": 10.6982, "3-M": 11.438, "6-M": 11.1549, "12-M": 11.89},
  "pib_yields_pct":   {"2-Y": 12.5, "3-Y": 12.5, "5-Y": 12.5, "10-Y": "Bids Rejected", "15-Y": 12.4},
  "reserves_usd_mn":  {"sbp_usd_mn": 15079.5, "banks_usd_mn": 5445.0, "total_usd_mn": 20524.5}
}
```

**Sample — PKR/USD:**
```json
{"as_on": "23-Apr-26", "m2m_rate": 278.8614,
 "weighted_avg_bid": 278.594, "weighted_avg_offer": 279.0191}
```

**Sample — commodities (5-day OHLC):**
```json
{"commodity": "Brent", "ticker": "BZ=F", "date": "2026-04-23",
 "open": 101.58, "high": 106.10, "close": 97.36, "change_5d_pct": 1.97}
```

### 1.4 Layer 1 — Fiscal / Real-Economy (links-only)

These sources return **PDF/landing-page link lists**, not structured time-series. For the
actual numeric values (FBR collection totals, CPI %, LSM growth) you need to download
the linked PDFs and parse them (or wait for SBP EasyData API registration).

| Source | Returns |
|---|---|
| **FBR Revenue Collections** | 9 PDF / press-release links on FBR landing page |
| **PBS Trade Statistics** | 50+ trade-related links / PDFs |
| **PBS (Bureau of Statistics)** | 9 CPI / LSM / price-release links |
| **IMF Pakistan Country Page** | 10 Pakistan program / mission / review links |

Each record is `{title, url}` — good enough for a "new release detected" trigger, but the
numbers still live inside the linked PDFs.

### 1.5 Layer 4 — News / Sentiment

| Source | Rows | Sample Fields |
|---|---|---|
| **RSS News Aggregator** | 25 articles from 5 feeds | `source, title, published, published_parsed, link, summary` |
| **CoinGecko (crypto)** | 3 coins | `coin, usd, change_24h_pct, volume_24h_usd, market_cap_usd, last_updated_at` |

**Sample — RSS timeline (sorted newest first):**
```json
{"source": "Business Recorder — Markets",
 "title": "Rupee inches up against US dollar",
 "published_parsed": "2026-04-23T11:00:51",
 "link": "https://www.brecorder.com/news/40417922/rupee-inches-up-against-us-dollar"}
```

Feeds live: Business Recorder Markets, Profit Pakistan Today, Dawn Business,
The News Business, Tribune Business. (IMF / SBP RSS blocked — covered via HTML scrape
of their country/press pages.)

**Sample — CoinGecko (retail risk-on proxy):**
```json
{"coin": "bitcoin", "usd": 77495, "change_24h_pct": -1.016,
 "volume_24h_usd": 45497902217.69, "market_cap_usd": 1551223759500.10}
```

---

## 2. Reach-Only Sources (4)

These sources are **reachable** but return HTML shells or blocked content with plain HTTP.
The connectors are wired and healthy; to actually extract structured data from them we
need one of: (a) a headless browser (Playwright), (b) a free API key, or (c) reverse-
engineering the internal XHR API.

| Source | Why reach-only | Path forward |
|---|---|---|
| **PSX Announcements** | JS-rendered SPA at `dps.psx.com.pk/announcements` | Playwright OR capture XHR the page fires on load |
| **PUCARS (Corporate Filings)** | JS-rendered SPA | Same as above; also: PSX emails the same filings in their daily bulletin |
| **FinHisaab FIPI/LIPI** | Client-rendered SPA | Not needed — **SCStrade already covers FIPI/LIPI** with full detail |
| **SBP EasyData (portal reach)** | Requires free registration to access time-series API | Register on https://easydata.sbp.org.pk → unlocks M2, CPI, reserves, FX as clean JSON |

**Recommendation:** Register for SBP EasyData API key today. That single API replaces our
HTML-scraping of 3-4 pages and gives clean historical time-series. Playwright for PSX
filings is a separate mini-project.

---

## 3. Failing Sources (1)

| Source | Status | Notes |
|---|---|---|
| **MoC Monthly Trade Statements** | HTTP 403 (Cloudflare WAF) | **Expected failure** — documented in `psx_data_sources.md`. PBS publishes the same trade data freely (already wired, working). No action required. |

---

## 4. Data Quality Findings

| Finding | Severity | Action Taken |
|---|---|---|
| PSX Terminal sample used `ENGRO` (not a valid ticker) | Low | Swapped to `ENGROH` (Engro Holdings); now 8/8 symbols OK. |
| SCStrade sector parser grabbed junk prefix `mn Foriegn Sector-wise Breakup All other Sectors` as the first sector | Low | Added junk-token filter + "max 5 words" heuristic on sector name. First sector is now clean `"All other Sectors"`. |
| SBP dashboard T-Bill yields and PIB yields were missed by v1 regex | Medium | Rewrote block-anchored regex. Now extracts T-Bill (1-M / 3-M / 6-M / 12-M) and PIB (2-Y / 3-Y / 5-Y / 10-Y / 15-Y) with "Bids Rejected" handled as string, numeric yields as floats. |
| PSX Announcements / FinHisaab / SBP EasyData showed as FAIL (no fetch implemented) | Low | Added graceful reach-only `fetch()` methods with actionable guidance in the summary. |
| RSS source encoding (`—` displaying as `ù` in Windows console) | Cosmetic | Does not affect data — `published_parsed` field provides a clean ISO-8601 datetime. |

---

## 5. What We Can Actually Feed The Bot Today

Stitching the working sources together gives us, **per day**, a complete PSX snapshot:

```
  ┌──────────────────────────────── Per-day snapshot ──────────────────────────────────┐
  │                                                                                     │
  │  MACRO        Policy rate, KIBOR (3/6/12-M), T-Bill (1/3/6/12-M),                    │
  │               PIB (2/3/5/10/15-Y), PKR/USD M2M + bid/offer,                         │
  │               SBP reserves (SBP + banks + total),                                   │
  │               Brent / WTI / Cotton / Nat-Gas / Gold 5-day OHLC                      │
  │                                                                                     │
  │  FLOWS        FIPI net flow + participant-category flows                            │
  │               (foreign / banks / individuals / mutual funds / insurance / brokers)  │
  │               + sector-level USD flows (10 sectors: cement, banks, oil&gas, etc.)   │
  │                                                                                     │
  │  PRICES       All 482 PSX symbols (OHLCV + change%)                                 │
  │               18 indices (KSE100 / KSE30 / KMI30 / ALLSHR + sector indices)         │
  │               Circuit-locked stocks (upper and lower, live)                         │
  │                                                                                     │
  │  NEWS         25+ curated headlines/day with ISO-8601 timestamps                    │
  │               across 5 financial press outlets                                      │
  │                                                                                     │
  │  SENTIMENT    BTC / ETH / SOL price + 24h change + 24h volume                       │
  │               (retail risk-on / risk-off proxy)                                     │
  │                                                                                     │
  │  LINKS        Daily new PDFs from FBR, PBS, IMF — use as "release detected"         │
  │  (triggers)   triggers (e.g. FBR posts monthly collection → fetch PDF → extract)    │
  │                                                                                     │
  └─────────────────────────────────────────────────────────────────────────────────────┘
```

This is **more than sufficient** to build a working trading bot that reacts to:
- Rate decisions & yield-curve shifts
- Currency moves (PKR/USD)
- Foreign vs. domestic flow imbalances
- Global commodity shocks (oil, cotton)
- News-driven sentiment spikes
- Individual-stock circuit breakers (volatility filter)

---

## 6. Next Steps (in priority order)

1. **Persist to a local database.** Right now every run re-fetches. Wire a SQLite layer
   (one table per connector, keyed by date) so we build a time-series history automatically.
2. **Register for SBP EasyData API key.** Replaces 3 HTML scrapes with one clean JSON API
   and unlocks historical M2 / CPI / reserves / balance-of-payments series.
3. **Schedule the fetchers.** Cron / Windows Task Scheduler / `APScheduler`: run end-of-day
   (after 3:30 PM PKT close) and once more overnight for FX + commodities.
4. **Build a feature-engineering step** that joins everything on `date` and produces
   bot-ready features: `{date, KSE100_close, KSE100_ret, FIPI_net, policy_rate, PKR_USD,
   brent_5d_chg, btc_24h_chg, stocks_upper_locked, stocks_lower_locked, news_count}`.
5. **(Optional)** Stand up Playwright for PUCARS / PSX Announcements so we can react to
   corporate filings (dividend declarations, earnings, block trades).

---

## Appendix — How to reproduce

```bash
.venv\Scripts\python.exe inspect_sources.py           # human-readable tables + panels
.venv\Scripts\python.exe inspect_sources.py --json    # also writes full_fetch_report.json
.venv\Scripts\python.exe inspect_sources.py --source psx   # filter by name
```

Files written:
- `connection_report.json` — health-check results (from `test_connections.py`)
- `full_fetch_report.json` — full structured data samples (from `inspect_sources.py --json`)
