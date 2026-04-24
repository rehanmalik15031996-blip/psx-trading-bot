# PSX Data Sources — Refinement Report

> Generated after sampling every source and auditing the payloads.
> Goal: keep only what is useful for trading-bot decisions and remove
> redundant/irrelevant fields and connectors.

---

## 1. Final Source Inventory (after refinement)

**17 connectors** (down from 19). Live verdicts from the latest `inspect_sources.py` run:

| # | Source | Verdict | Rows | Purpose |
|---|---|---|---|---|
| 1 | PSX Terminal (REST) | GOOD | 8 | Live ticks for a liquid basket + trades + rupee turnover |
| 2 | PSX Indices (DPS) | GOOD | 18 | All indices OHLC: KSE100, KSE30, KMI30, sector indices |
| 3 | PSX Market Watch | GOOD | 482 | Daily OHLCV + sector + index membership for **every** listed symbol |
| 4 | PSX Circuit Breakers | GOOD | 18 | Which symbols hit upper/lower ±10 % today |
| 5 | PSX Announcements | REACH-ONLY | 0 | Corporate filings — JS-rendered (needs Playwright) |
| 6 | SCStrade FIPI/LIPI | GOOD | 9 | Foreign vs local money flows + sector breakdown |
| 7 | SBP Policy Rate + KIBOR | GOOD | 1 | Policy rate, corridor, weighted repo, KIBOR, T-Bill + PIB yields, FX reserves |
| 8 | SBP M2M (PKR/USD) | GOOD | 1 | Official PKR/USD revaluation rate + bid/offer + spread |
| 9 | SBP EasyData | REACH-ONLY | 0 | Needs free API key for full macro series |
| 10 | yfinance (commodities) | GOOD | 5 | Brent, WTI, Cotton, Gold, Copper — 1d + 5d change |
| 11 | FBR Revenue Collections | REACH (event-triggered) | 0 | Monthly revenue press releases (only when published) |
| 12 | MoC Monthly Trade Statements | FAIL | 0 | Cloudflare-blocked; fall back on PBS Trade |
| 13 | PBS Trade Statistics | GOOD | 30 | Monthly export/import XLSX releases |
| 14 | PBS (Bureau of Statistics) | GOOD | 4 | CPI, SPI, LSM, foreign trade advance releases |
| 15 | IMF Pakistan Country Page | FAIL / GOOD (flaky WAF) | — | Program/mission/review links; fall back on RSS news |
| 16 | RSS News Aggregator | GOOD | 25 | Dawn / Business Recorder / Profit / The News / Tribune |
| 17 | CoinGecko (crypto) | GOOD | 2 | BTC + ETH — retail sentiment proxy |

---

## 2. What Was Dropped (and Why)

### 2.1 Connectors removed entirely

| Connector | Reason | Replacement |
|---|---|---|
| `PUCARSConnector` | JS SPA, identical payload to PSX Announcements | Use `PSXAnnouncementsConnector` (needs headless browser regardless) |
| `FinHisaabFIPIConnector` | Client-rendered SPA; couldn't scrape. Redundant with SCStrade | `SCStradeFIPIConnector` is the canonical, scrape-friendly FIPI source |

### 2.2 Fields removed / noisy data dropped

| Source | Field dropped | Reason |
|---|---|---|
| PSX Terminal | indices extras (KSE100/KSE30/KMI30/ALLSHR) | Fully covered by `PSXIndicesConnector` which returns **all 18 indices** |
| PSX Terminal | `change` (rupee) | Redundant with `change_pct` and `price − (price/(1+chg))` |
| PSX Circuit Breakers | `ldcp, open, high, low, current, change` | Identical data already in PSX Market Watch; this view only needs to answer "which symbols are locked and in which direction?" |
| SBP M2M | `pair` | Constant `"PKR/USD"` — zero information |
| CoinGecko | `solana` | Low correlation with PSX retail sentiment; adds noise |
| CoinGecko | `market_cap_usd` | Derivable from price × float, not a signal for a PSX bot |
| yfinance | `"Natural Gas" (NG=F)` | Pakistan domestic gas is OGRA-administered — Henry Hub barely moves PSX |
| yfinance | `open, high, low, volume` | Futures volume is contract count (noisy); OHLC noise > signal for commodity proxies; kept `close + 1d + 5d %` which are the usable features |
| RSS News | `published` (raw RFC822 string) | `published_at` (ISO-8601) is strictly better for any time-series work |

### 2.3 Fields reformatted / normalized

| Source | Change |
|---|---|
| SBP Policy Rate | `as_on` → ISO date (`2026-04-23` instead of `23-Apr-26`) |
| SBP Policy Rate | `pib_yields_pct` — `"Bids Rejected"` strings coerced to `null` so the map stays numerically typed |
| SBP M2M | `as_on` → ISO date; added `spread_pkr` (offer − bid) convenience field |
| PSX Terminal | `timestamp` (Unix seconds) → ISO-8601 UTC (`2026-04-23T10:49:01Z`) |
| PSX Terminal | `value` → `value_pkr` (explicit unit) |
| CoinGecko | `last_updated_at` (Unix seconds) → ISO-8601 UTC |
| PSX Market Watch | added `sector_name` resolved via 4-digit sector-code lookup table (`connectors/sectors.py`) |
| PSX Market Watch | `index` (comma-separated string) → `indices` (proper list) |
| PSX Circuit Breakers | added `direction: "upper"/"lower"` so consumers don't have to look at the extras dict |
| SCStrade | category casing normalized (`"BANKS / DFI"` → `"Banks / DFI"`, `"Foreign"` → `"Foreign"`) |
| SCStrade | added aggregate `foreign_net_pkr_mn` and `local_net_pkr_mn` in extras — the single most-used scalar for a flow-based strategy |
| RSS News | HTML tags/entities stripped from `summary` so downstream NLP doesn't choke on `<p><strong>...</strong></p>` |
| FBR | tightened regex: only true revenue-collection releases (Budget Proposals / Finance Act / Anomaly Committee excluded) |
| PBS | excluded census / population / agriculture / labour force noise; require CPI / SPI / LSM / WPI / Monthly Bulletin keywords |
| PBS Trade | strict trade-keyword filter + dedupe — now surfaces Export/Import XLSX files directly |
| IMF | require Pakistan reference (in text **or** URL path) + program keyword; drop Springer journal, FSAP, resident-rep cruft; explicit 403 handling with fallback-to-RSS note |

---

## 3. How Each Remaining Source Is Beneficial

### Layer 1 — Macro

| Source | What it drives in the bot |
|---|---|
| **SBP Policy Rate + KIBOR** | Risk-free curve. Policy rate changes move the entire PSX (banks up, leveraged sectors down on hikes). KIBOR 3M/6M/12M is the discount rate basis. T-Bill/PIB yields let us compute the equity risk premium. FX reserves signal external-account stress. |
| **SBP M2M (PKR/USD)** | PKR depreciation → E&P / textile exporters benefit; importers (auto, OMC, pharma raw materials) suffer. `spread_pkr` is a liquidity-stress indicator. |
| **yfinance commodities** | Brent/WTI ↔ E&P (OGDC/PPL/MARI/POL) and OMCs (PSO/SHEL). Cotton ↔ textile composite. Gold ↔ risk-off / PKR devaluation hedge. Copper ↔ global industrial cycle → cement/engineering. |
| **PBS** | CPI / SPI / LSM are the "real economy" confirmations. CPI drives SBP's next move. LSM confirms or contradicts manufacturing earnings cycles. |
| **PBS Trade** | Monthly export/import numbers → trade deficit → FX pressure → equity risk. |
| **FBR** | Monthly revenue collection headlines → fiscal confidence (or IMF-target breach fears). |

### Layer 2 — Political / Institutional

| Source | Role |
|---|---|
| **IMF Pakistan** | Program status, mission schedule, tranche disbursements — major regime-change events. Flaky WAF, so we have clean fallback via RSS. |

### Layer 3 — Flows

| Source | Role |
|---|---|
| **SCStrade FIPI/LIPI** | Participant-level buy/sell/net in PKR mn + sector-level USD flows. Aggregated `foreign_net_pkr_mn` is the cleanest single-scalar flow signal. |

### Layer 4 — Behavioral / Sentiment

| Source | Role |
|---|---|
| **RSS News** | Real-time headline + summary stream. Feeds the NLP sentiment layer and IMF/political event detection. |
| **CoinGecko** | BTC/ETH 24h change is a well-documented global "risk-on" proxy that local retail traders react to. |

### Layer 5 — Microstructure / Prices

| Source | Role |
|---|---|
| **PSX Market Watch** | Primary daily snapshot: every listed symbol's OHLCV + sector + index membership in one call. Powers backtests and screeners. |
| **PSX Indices (DPS)** | All 18 indices including sector indices (OILGAS, CEMENT, BANKS, TECH …) — needed for sector-rotation strategies. |
| **PSX Terminal (REST)** | Live intraday ticks for a liquid basket; uniquely provides `trades` (trade count) and `value_pkr` (rupee turnover) that MW doesn't expose. |
| **PSX Circuit Breakers** | Who is locked at ±10 % today; momentum / reversal context. |
| **PSX Announcements** | Corporate filings — still JS-only; needs Playwright for real ingestion. Kept reachable-only so monitoring can flag when headless browsing is worth doing. |

### Reach-only (not directly used yet, but monitored)

| Source | Path forward |
|---|---|
| **SBP EasyData** | Register for free API key → unlocks full time-series (M2, CPI history, BoP, reserves history). Single highest-leverage next step for macro. |
| **PSX Announcements** | Add a Playwright connector to pull structured filings if the bot needs event-study features. |

---

## 4. Sample Record Shapes (post-refinement)

### PSX Market Watch
```json
{
  "symbol": "HASCOLNC",
  "sector_code": "0821",
  "sector_name": "Oil & Gas Marketing Companies",
  "indices": ["ALLSHR"],
  "ldcp": 19.39, "open": 19.49,
  "high": 20.93, "low": 19.40,
  "current": 20.51,
  "change_pct": 5.78,
  "volume": 77776966
}
```

### PSX Circuit Breakers
```json
{"symbol": "ADMM", "direction": "upper", "change_pct": 10.00, "volume": 1849445}
```

### PSX Terminal (REST)
```json
{
  "symbol": "OGDC", "price": 319.34, "change_pct": -0.00551,
  "volume": 2642398, "trades": 6237, "value_pkr": 841783449.75,
  "high": 321.20, "low": 315.84,
  "timestamp": "2026-04-23T10:49:01Z", "status": "SUS"
}
```

### SCStrade FIPI/LIPI
```json
// records[]
{"category": "Foreign", "buy_pkr_mn": 15.48, "sell_pkr_mn": -15.21, "net_pkr_mn": 0.27}
// extras
{
  "foreign_net_pkr_mn": 0.27,
  "local_net_pkr_mn": -0.26,
  "sectors": [ /* 10 items with USD mn */ ],
  "report_date": "22-Apr-2026"
}
```

### SBP Policy Rate + KIBOR
```json
{
  "as_on": "2026-04-23",
  "policy_rate_pct": 10.5,
  "ceiling_rate_pct": 11.5,
  "floor_rate_pct": 9.5,
  "weighted_on_repo_pct": 9.79,
  "kibor": {"3-M": {"bid": 11.01, "offer": 11.26},
            "6-M": {"bid": 11.19, "offer": 11.44},
            "12-M": {"bid": 11.45, "offer": 11.95}},
  "tbill_yields_pct": {"1-M": 10.6982, "3-M": 11.438, "6-M": 11.1549, "12-M": 11.89},
  "pib_yields_pct": {"2-Y": 12.5, "3-Y": 12.5, "5-Y": 12.5, "10-Y": null, "15-Y": 12.4},
  "reserves_usd_mn": {"sbp_usd_mn": 15079.5, "banks_usd_mn": 5445.0, "total_usd_mn": 20524.5}
}
```

### SBP M2M (PKR/USD)
```json
{
  "as_on": "2026-04-23",
  "m2m_rate": 278.8614,
  "weighted_avg_bid": 278.594,
  "weighted_avg_offer": 279.0191,
  "spread_pkr": 0.4251
}
```

### yfinance commodities
```json
{"commodity": "Brent", "ticker": "BZ=F", "date": "2026-04-23",
 "close": 97.47, "change_1d_pct": -4.36, "change_5d_pct": 2.08}
{"commodity": "Copper", "ticker": "HG=F", "date": "2026-04-23",
 "close": 6.03, "change_1d_pct": -1.43, "change_5d_pct": -0.06}
```

### CoinGecko
```json
{"coin": "bitcoin", "usd": 77557, "change_24h_pct": -0.842,
 "volume_24h_usd": 45606680113.12, "last_updated_at": "2026-04-23T11:43:54Z"}
```

### RSS News
```json
{
  "source": "Dawn Business",
  "title": "KSE-100 plunges below 170,000-mark as bears maintain control of PSX",
  "published_at": "2026-04-23T11:31:47",
  "link": "https://www.dawn.com/news/1994338/...",
  "summary": "The Pakistan Stock Exchange (PSX)'s benchmark index KSE-100 continued its bearish momentum on Thursday, losing over 2,400 points. ..."
}
```

### PBS
```json
{"title": "Weekly Sensitive Price Indicator (SPI) for the week ended on 16-04-2026", "url": "https://www.pbs.gov.pk/..."}
{"title": "Monthly Advance releases on Foreign Trade Statistics for March 2026", "url": "https://www.pbs.gov.pk/..."}
{"title": "Quantum Index of Large Scale Manufacturing Industries (QIM) for Feb 2026", "url": "https://www.pbs.gov.pk/..."}
```

### PBS Trade
```json
{"title": "Export_March_ 2026.xlsx", "url": "https://www.pbs.gov.pk/wp-content/uploads/2020/07/Export_March_%202026.xlsx"}
{"title": "Import _March_ 2026.xlsx", "url": "https://www.pbs.gov.pk/wp-content/uploads/2020/07/Import_%20March_%202026.xlsx"}
```

---

## 5. Next Steps

1. **Parse the PBS XLSX releases** — we have the URLs; build a simple fetcher that downloads `Export_<Month>_<Year>.xlsx` and extracts the headline trade balance figure.
2. **Register for SBP EasyData API key** — unlocks full historical time series for every macro variable (single biggest improvement available).
3. **Add a Playwright-based announcement fetcher** — only if event-study features are needed; otherwise RSS news covers major announcements with a 30-60 min lag.
4. **Build a snapshot runner** — orchestrate all 17 GOOD connectors on a daily cron (17 calls takes ~10–15 s serialized; parallel is ~3 s). Persist one JSON-lines file per day.
5. **Sector code cross-check** — compare `sector_name` resolution coverage against current Market Watch output; confirm sector 0838 (6 symbols: TPLP, PACE, JVDC, etc.) mapping is accurate.
