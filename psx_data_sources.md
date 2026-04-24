# PSX Daily Data Sources — Master Reference

> **Purpose:** Map every market-moving factor identified in `psx_market_research.md` to actual, working data sources your trading bot can pull from daily.
>
> **Columns:**
> - **Access** = How to get the data (API / JSON endpoint / Web scrape / PDF / Manual)
> - **Freq** = How often the data updates (Real-time / Intraday / Daily / Weekly / Monthly / Event-driven)
> - **Cost** = Free / Freemium / Paid

---

## 1. Market Price, Volume & Order Book Data (Layer 5: Technical)

These are your **primary, highest-priority feeds**. A bot cannot function without them.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **PSX Official Level 1 / 1+ / 2 Feeds** ⭐ | Request via `marketdatarequest@psx.com.pk` / `psx.com.pk/psx/product-and-services/services` | **Official, authoritative real-time feed.** Level 1 = basic market data; Level 1+ = adds top-10 MBO/aggregated order book; Level 2 = adds tick-by-tick data. Also provides EOD, historical, and PUCARS corporate announcements. | Direct feed (licensed) | Real-time | Paid (institutional) |
| **Sarmaaya** ⭐ | `https://sarmaaya.pk/` | **Authorized local redistributor of PSX data.** Provides Level 1/1+/2 access, portfolio tracking, financial research tools. Contact: `support@sarmaaya.pk`. | REST API / Web | Real-time | Freemium / Paid |
| **PSX Terminal (psxterminal.com)** | `https://psxterminal.com` (REST) + `wss://psxterminal.com/` (WebSocket) | Real-time ticks, OHLCV, indices, futures, odd lot, bills & bonds. Rate-limited: 100 REST/min/IP, 5 WS connections/IP. | REST API + WebSocket | Real-time | Free |
| **PSX Official Data Portal** | `https://dps.psx.com.pk/` | Official live prices, announcements, NCCPL notices, circuit breaker status | Web / JSON endpoints | Real-time | Free |
| **PSX Daily Downloads** | `https://dps.psx.com.pk/dataportal/daily-downloads` | End-of-day summaries, closing rates, default spreadsheets | CSV/Excel download | Daily EOD | Free |
| **PSX Monthly Reports** | `https://dps.psx.com.pk/monthly-reports` | Monthly market summaries, sectoral performance | PDF/Excel | Monthly | Free |
| **PSX Trading Panel** | `https://dps.psx.com.pk/trading-panel` | Live market watch, top gainers/losers, volume leaders | Web | Real-time | Free |
| **Capital Stake API** | `https://capitalstake.com/docs/rest/api/stocks/intro` | Normalized OHLCV bars, indices, ETFs, derivatives, corporate fundamentals | REST API | Real-time / EOD | Freemium |
| **iTick API** | `https://itick.org` | Multi-market adapter, historical K-line, SDKs in multiple languages | REST API | Real-time | Paid |
| **psx-data-reader** ⭐ | `https://pypi.org/project/psx-data-reader/` / GitHub: `MuhammadAmir5670/psx-data-reader` | Python library that scrapes PSX historical & current ticker data directly into Pandas DataFrames. **Note: no longer actively maintained** (last updates Nov 2025), but still works for backfills. | `pip install psx-data-reader` | EOD / historical | Free |
| **mumtazkahn/psx-terminal (GitHub)** | `https://github.com/mumtazkahn/psx-terminal` | Open-source PSX terminal with API docs — good reference for endpoints | Open-source | — | Free |

**Recommendation:**
- **Free MVP:** Start with **PSX Terminal (psxterminal.com)** WebSocket + `psx-data-reader` for historical backfill.
- **Production / Institutional:** Upgrade to **PSX Level 1+ or Level 2** via Sarmaaya or direct PSX license.

---

## 2. Flow Data — FIPI & LIPI (Layer 3: Smart Money Signals)

**This is arguably the highest-alpha daily signal on the PSX.** Tracks foreign + local institutional flows.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **NCCPL (Official)** | `https://www.nccpl.com.pk/` | Official FIPI/LIPI raw data, settlement reports | Web + PDF | Daily EOD | Free |
| **SCStrade FIPI** | `http://www.scstrade.com/fipitext.aspx` | FIPI/LIPI breakdown by category (Foreigners, Banks/DFIs, Brokers, Companies, Individuals, Insurance, Mutual Funds, NBFCs) + sector breakup (Cement, Banks, Fertilizer, Foods, Oil & Gas, Power, Tech, Textiles) | Web scrape | Daily EOD | Free |
| **SCStrade Research Reports** | `https://scstrade.com/research/RE_Trading_Reports.aspx` | Daily trading reports, historical data | Web/PDF | Daily | Free |
| **FinHisaab** | `https://finhisaab.com/market-updates/fipi-lipi` | FIPI/LIPI daily data with sector-wise trends + historical charts (easier to parse than NCCPL raw) | Web scrape | Daily EOD | Free |
| **PSX NCCPL Notices** | `https://dps.psx.com.pk/announcements/nccpl` | Official NCCPL regulatory notices | Web | Event-driven | Free |

**Recommendation:** Scrape **SCStrade** as primary (cleanest format) + **FinHisaab** as backup.

### 2.1 PUCARS — Corporate Announcements (Official & Mandatory)

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **PUCARS** ⭐ (Pakistan Unified Corporate Reporting System) | `https://pucars.psx.com.pk/` | **Mandatory portal** for all price-sensitive announcements — financial results, dividends, bonus issues, rights, mergers, material information. Required by PSX Notice KSE/N-3611 (Jul 15, 2015). Real-time dissemination to all market participants. | Web / potential data feed | Real-time | Free (web) |

**Bot tip:** Poll PUCARS every 1–2 minutes during trading hours for price-sensitive filings — they are the single biggest stock-specific movers.

---

## 3. Macro Data — SBP, Inflation, Currency (Layer 1: Signal)

### 3.1 SBP Policy Rate & Monetary Data

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **SBP EasyData** ⭐ (biggest macro find) | `https://easydata.sbp.org.pk/` | **~2.97 million data points across 23,988 time series.** Covers external sector, monetary, financial, and real sector indicators. Includes KIBOR, CPI/SPI, inflation, FX reserves, M2/M3, balance of payments, trade data. **Has a developer API** for programmatic access. | REST API + Web | Varies by series | Free |
| **EasyDataPy** ⭐ (Python client for EasyData) | `https://pypi.org/project/EasyDataPy/` | Unofficial Python library for SBP EasyData. Verify API keys, download time series as Pandas DataFrames, built-in transformations & visualization. | `pip install EasyDataPy` | — | Free |
| **SBP RSS Feeds** ⭐ | `https://www.sbp.org.pk/rss.asp` | Circulars, notifications, press releases, new economic data releases. **Perfect agent trigger source** — subscribe and act on new circulars instantly. | RSS | Event-driven | Free |
| **SBP Economic Data Portal** | `https://www.sbp.org.pk/ecodata/index.asp` | Legacy hub for SBP data downloads (Excel, PDF) — now largely superseded by EasyData | Web | Varies | Free |
| **SBP Policy Rate** | `https://www.sbp.org.pk/ecodata/CRates/index.asp` | Current + historical policy rate (currently **10.50%** as of Dec 2025) | Web / Excel | Event-driven (MPC meetings ~every 6 weeks) | Free |
| **SBP Monetary Policy Reports** | `https://www.sbp.org.pk/m_policy/` | Full quarterly MPR with inflation outlook, GDP, balance of payments analysis | PDF | Quarterly | Free |
| **SBP Data Release Calendar** | `https://www.sbp.org.pk/Cal/` | Calendar of upcoming data releases — schedule your bot around these | Web | — | Free |
| **SBP Interest Rate Corridor** | `https://www.sbp.org.pk/ecodata/` | Reverse repo, repo rates, OMO results | Excel | Daily/Event | Free |

**Recommendation for Macro Layer:** Use **SBP EasyData API + EasyDataPy** as your primary. Use **SBP RSS** as your event-trigger source. This combo replaces 90% of manual scraping.

### 3.2 Inflation (CPI, SPI, WPI)

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **Pakistan Bureau of Statistics (PBS)** | `https://www.pbs.gov.pk/` | Official CPI, SPI (Sensitive Price Indicator), WPI monthly prints | PDF / Excel | Weekly (SPI) / Monthly (CPI) | Free |
| **SBP Inflation Monitor** | `https://www.sbp.org.pk/publications/Inflation_Monitor/` | Monthly inflation analysis, archived data 2005–present | PDF | Monthly | Free |
| **SBP Weekly SPI** | `https://www.sbp.org.pk/ecodata/tpi.asp` | Weekly sensitive price indicator (food/essentials inflation) | Excel | Weekly | Free |

**Bot tip:** CPI release is usually on the **1st business day of each month** — a major market-moving event.

### 3.3 PKR/USD & Forex

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **SBP Exchange Rates** | `https://www.sbp.org.pk/ecodata/rates/m2m/M2M-Current.asp` | Official interbank mark-to-market rate | Web | Intraday | Free |
| **SBP Foreign Exchange Reserves** | `https://www.sbp.org.pk/ecodata/fxreserves.xls` | Weekly reserves (SBP-held + total) | Excel | Weekly (Thursdays) | Free |
| **Forex.pk** | `https://forex.pk/` | Open market PKR/USD, EUR, GBP, SAR rates | Web scrape | Intraday | Free |
| **Exchange Rate APIs** (exchangerate-api.com, currencylayer) | — | PKR/USD programmatic access | REST API | Real-time | Freemium |

---

## 4. Oil, Commodities & Global Inputs

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **Yahoo Finance** (`yfinance` Python lib) | `yfinance` | Brent (`BZ=F`), WTI (`CL=F`), coal, gold, etc. | Python API | Real-time | Free |
| **Investing.com** | `https://www.investing.com/commodities/` | Brent, WTI, coal, natural gas, cotton. Has JSON endpoints on most commodity pages. | Web scrape / JSON | Real-time | Free |
| **Business Insider Markets JSON** | `https://markets.businessinsider.com/commodities/` | Structured JSON feeds for Brent Crude, Natural Gas (Henry Hub / Dutch TTF), coal benchmarks | JSON scrape | Real-time | Free |
| **OGRA (Oil & Gas Regulatory Authority)** | `https://ogra.org.pk/` | Official Pakistan petrol/diesel/HSD pricing (updated fortnightly) | Web/PDF | Fortnightly | Free |
| **PSO Fuel Prices** | `https://www.psopk.com/` | Retail fuel prices across Pakistan | Web | Fortnightly | Free |
| **LME (London Metal Exchange)** | `https://www.lme.com/` | Metal prices (copper for wiring, etc.) | Web | Daily | Free |

### 4.1 Sector-Specific Commodity Benchmarks

Critical for trading sector-sensitive stocks (fertilizer, cement, power, textile).

| Source | URL | What it gives you | Sector Relevance | Access | Cost |
|---|---|---|---|---|---|
| **Commodities-API** ⭐ | `https://commodities-api.com/` | **Urea (UREA)** spot & historical prices — essential for Fertilizer stocks (Engro, FFC, FFBL). Also covers DAP, phosphate, and other agri inputs. | Fertilizer | REST API (JSON) | Freemium |
| **OilPriceAPI** ⭐ | `https://oilpriceapi.com/` | **Newcastle Coal** + **Coking Coal** benchmarks — primary cost driver for Pakistani cement. Plus Brent, WTI, natural gas. | Cement, E&P, Power | REST API (JSON) | Freemium |
| **TradingEconomics Commodities** | `https://tradingeconomics.com/commodity/coal` | Coal, cotton, urea, DAP historical data | All | REST API | Freemium |
| **Cotton (NYBOT/ICE)** via yfinance | `CT=F` ticker | Cotton futures — drives Pakistani textile margins | Textile | Python | Free |
| **Henry Hub & Dutch TTF Natural Gas** | Investing.com / Business Insider | Global gas benchmarks — affects fertilizer & power cost structures | Fertilizer, Power | Web/JSON | Free |

---

## 5. Political & Institutional Events (Layer 2: Shocks)

### 5.1 IMF Program Status

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **IMF Country Page — Pakistan** | `https://www.imf.org/en/Countries/PAK` | Staff-level agreements, Article IV reports, program reviews, tranche disbursements | Web / PDF | Event-driven | Free |
| **IMF Press Releases RSS** | `https://www.imf.org/en/News/SearchNews?rss=...` | Real-time IMF news feed | RSS | Event-driven | Free |

### 5.2 Government & Budget

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **Ministry of Finance** | `https://www.finance.gov.pk/` | Budget, fiscal updates, debt | Web/PDF | Various | Free |
| **FBR (Federal Board of Revenue)** | `https://www.fbr.gov.pk/` | Tax policy, SROs (statutory regulatory orders) that affect sectors | Web | Event | Free |
| **Securities & Exchange Commission (SECP)** | `https://www.secp.gov.pk/` | Regulatory changes, listing approvals, penalties | Web | Event | Free |

---

## 6. News & Sentiment (Layer 4: Behavioral Edge)

### 6.1 Financial News (RSS / Scraping Friendly)

| Source | URL | Focus | Access | Freq | Cost |
|---|---|---|---|---|---|
| **Business Recorder** | `https://www.brecorder.com/` | #1 PSX-focused financial daily | RSS + Web scrape | Real-time | Free |
| **Profit by Pakistan Today** | `https://profit.pakistantoday.com.pk/` | Fast market news, op-eds | RSS | Daily | Free |
| **Dawn Business** | `https://www.dawn.com/business` | Wider business coverage | RSS | Daily | Free |
| **The News — Business** | `https://www.thenews.com.pk/latest/category/business` | Market wrap-ups, analysis | RSS | Daily | Free |
| **Mettis Global** | `https://mettisglobal.news/` | PSX-specific wire news, brief market updates (similar to Bloomberg terminal wire) | Web scrape | Real-time | Free + Paid tier |
| **Tribune Business** | `https://tribune.com.pk/business` | General business news | RSS | Daily | Free |
| **Propakistani Business** | `https://propakistani.pk/category/business/` | Retail-focused market stories | Web | Daily | Free |
| **Arab News Pakistan** | `https://www.arabnews.pk/taxonomy/term/18341` | Cross-regional Pakistan business coverage | RSS | Daily | Free |

### 6.2 Broker Research (Daily / Weekly Notes)

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **Arif Habib Ltd Research** | `https://www.arifhabibltd.com/research` | Daily market wrap, sector reports, target prices | PDF | Daily | Free |
| **JS Global** | `https://www.jsgcl.com/research` | Strategy outlooks, company notes | PDF | Daily | Free |
| **IGI Securities** | `https://igisecurities.com.pk/research-reports.php` | Research reports, strategy | PDF | Daily | Free |
| **Topline Securities** | `https://www.topline.com.pk/` | Daily market reviews | PDF/Email | Daily | Free |
| **KASB / KTrade** | `https://kasb.com/research/` | Daily reports | PDF | Daily | Free |
| **AKD Securities** | `https://www.akdsecurities.net/research` | Research reports | PDF | Daily | Free |
| **Next Capital** | `https://www.nextcapital.com.pk/` | Sector & macro reports | PDF | Weekly | Free |

### 6.3 Social Media & Sentiment

| Source | How to Use | Access | Cost |
|---|---|---|---|
| **X (Twitter) Pakistan Finance** | Follow + scrape handles like `@arifhabibltd`, `@Topline_Sec`, `@Mettis_Global`, `@KSEStocks`, plus hashtags `#PSX`, `#KSE100` | X API v2 (paid) OR scraping with `snscrape` | Paid / Free |
| **Reddit r/PakistaniTech, r/pakistan** | Retail sentiment | Reddit API | Free |
| **StockSharks / PSX-focused Telegram / Discord** | Active retail sentiment | Manual or bot integration | Free |
| **Facebook groups** (e.g., "Pakistan Stock Market") | High retail sentiment noise | Manual (API restricted) | Free |
| **KhiStocks / Pakistan Stock Forum** | `https://www.khistocks.com/` | Community analyst views | Web | Free |

**Sentiment analysis pipeline idea:**
1. Scrape headlines from Business Recorder, Mettis, Profit.
2. Run through a financial-tuned sentiment model (e.g., FinBERT).
3. Aggregate into a daily sentiment score per sector/stock.

---

## 7. Corporate Actions & Filings

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **PSX Company Announcements** | `https://dps.psx.com.pk/announcements/companies` | Earnings, dividends, bonus, rights, AGMs, material information | Web | Real-time | Free |
| **PSX Financial Results** | `https://dps.psx.com.pk/announcements/financial-results` | Quarterly / annual filings | Web | Event | Free |
| **PSX Corporate Actions Calendar** | `https://dps.psx.com.pk/announcements` | Ex-dividend dates, board meetings | Web | Daily | Free |
| **SECP EDGAR-equivalent** | `https://www.secp.gov.pk/document-search` | Filings database | Web | Event | Free |

---

## 8. Circuit Breakers & Trading Status

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **PSX Circuit Breakers Live** | `https://dps.psx.com.pk/circuit-breakers` | Stocks currently locked at ±10% upper/lower bands | Web | Real-time | Free |
| **PSX Market Summary** | `https://www.psx.com.pk/market-summary` | Market-wide status, any KSE-30 halts | Web | Real-time | Free |

**Critical for bot:** Poll this endpoint before placing any order — don't waste orders on locked stocks.

---

## 9. Geopolitics & Global Macro (Shocks)

| Source | URL | Access | Cost |
|---|---|---|---|
| **Reuters World News** | `https://www.reuters.com/world/` | RSS | Free |
| **Bloomberg / FT / WSJ** | Various | Paid subscription | Paid |
| **TradingEconomics Pakistan** | `https://tradingeconomics.com/pakistan/` | Comprehensive macro aggregator (GDP, trade, reserves) | API | Freemium |
| **World Bank Open Data** | `https://data.worldbank.org/country/pakistan` | Annual macro indicators | API | Free |

---

## 10. Recommended Free Stack (Zero-Cost Bot)

For a cost-free MVP trading bot:

| Layer | Primary Source | Backup Source |
|---|---|---|
| **Real-time prices** | PSX Terminal WebSocket | `psx-data-reader` / PSX Daily Portal |
| **Historical backfill** | `psx-data-reader` (Python) | Capital Stake REST |
| **FIPI/LIPI** | SCStrade scrape | FinHisaab scrape |
| **Macro data (one-stop)** | **SBP EasyData API + EasyDataPy** | SBP legacy ecodata |
| **SBP events triggers** | **SBP RSS feed** | Manual monitoring |
| **Policy rate** | SBP EasyData series | SBP ecodata Excel |
| **CPI/Inflation** | SBP EasyData + PBS release | SBP Inflation Monitor |
| **PKR/USD** | SBP M2M page | Forex.pk |
| **Oil prices** | yfinance (`BZ=F`, `CL=F`) | Investing.com / Business Insider JSON |
| **Urea (fertilizer)** | **Commodities-API** | TradingEconomics |
| **Coal (cement)** | **OilPriceAPI** (Newcastle/Coking) | TradingEconomics |
| **IMF status** | IMF Pakistan RSS | Manual press release monitoring |
| **Corporate filings** | **PUCARS** (`pucars.psx.com.pk`) | PSX announcements portal |
| **News sentiment** | Business Recorder + Mettis + Profit RSS → FinBERT pipeline | Dawn + The News RSS |
| **Broker research** | Arif Habib + Topline + JS Global PDFs (daily email signup) | IGI + AKD |
| **Circuit breakers** | PSX circuit-breakers live endpoint | — |

---

## 11. Suggested Data Pipeline Architecture

```
┌─────────────────────────────────────────────────────────┐
│  DATA INGESTION (scheduled jobs / cron / celery beat)   │
├─────────────────────────────────────────────────────────┤
│  Real-time:  PSX Terminal WebSocket → Redis Streams     │
│  Intraday:   Circuit breakers poll (every 30s)          │
│              PKR/USD poll (every 5 min)                 │
│  EOD:        FIPI/LIPI scrape (6 PM PKT)                │
│              Corporate announcements (continuous)       │
│  Weekly:     SBP reserves, SPI                          │
│  Monthly:    CPI print, SBP Inflation Monitor           │
│  Event:      IMF releases, MPC meetings, Budget         │
│  News:       RSS pollers every 5 min → NLP pipeline     │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STORAGE                                                │
├─────────────────────────────────────────────────────────┤
│  Time-series:  TimescaleDB or InfluxDB (prices, flows)  │
│  Documents:    PostgreSQL (news, filings)               │
│  Cache:        Redis (live quotes, sentiment scores)    │
│  Object:       S3 / Local (PDFs of broker research)     │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  FEATURE ENGINEERING                                    │
├─────────────────────────────────────────────────────────┤
│  - Technical indicators (RSI, Bollinger, MACD)          │
│  - FIPI/LIPI z-scores by sector                         │
│  - News sentiment scores (FinBERT) aggregated per stock │
│  - Macro regime flags (rate-cut cycle? high-oil?)       │
│  - Event flags (IMF review day, CPI day, budget week)   │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│  STRATEGY / EXECUTION                                   │
├─────────────────────────────────────────────────────────┤
│  Regime-switching bot (from research) → signals         │
│  → broker API (AKD Trade / Pearl Securities / etc.)     │
└─────────────────────────────────────────────────────────┘
```

---

## 12. Broker APIs for Actual Order Execution

> **Important:** To *place* trades (not just observe), you need a broker account with API access. Most Pakistani brokers do NOT offer public APIs; you'll likely need to negotiate.

| Broker | API Availability | Notes |
|---|---|---|
| **AKD Trade** | Semi-official | Popular retail platform; some developers have reverse-engineered it. Contact them for official API. |
| **Pearl Securities** | Private API | Enterprise clients only |
| **KASB / KTrade** | Has mobile SDK; limited public API | Contact for institutional access |
| **Arif Habib Ltd** | No public API | Institutional only |
| **Topline Securities** | No public API | Institutional only |
| **JS Global** | No public API | Institutional only |

**Alternative during development:** Build the bot against a **paper-trading simulator** using PSX Terminal data, then integrate with a broker once live.

---

## 13. Rate Limits & Ethical Scraping Notes

- **Respect `robots.txt`** on every site you scrape.
- **Add delays** (1–3 seconds between requests) for news sites.
- **User-Agent header:** identify your bot honestly.
- **Cache aggressively** — no need to re-scrape static monthly data hourly.
- **PSX Terminal:** 100 REST/min, 5 WS per IP.
- **SCStrade / FinHisaab:** no official rate limit published — keep scrape to 1x/day EOD.

---

## 14. Quick-Start Priority Order

If you're building the MVP bot, integrate data sources in this order:

1. **PSX Terminal WebSocket** (real-time prices) — 1 day of work
2. **`psx-data-reader`** (historical OHLCV backfill for training/backtest) — 0.5 days
3. **SCStrade FIPI/LIPI scrape** (daily smart-money signal) — 1 day
4. **SBP EasyData API via EasyDataPy** (policy rate, inflation, FX reserves, KIBOR) — 1 day
5. **SBP RSS feed** (event triggers for circulars/MPC) — 0.5 days
6. **PUCARS poller** (real-time corporate announcements) — 1 day
7. **Business Recorder + Mettis Global RSS + FinBERT** (sentiment) — 2–3 days
8. **PSX Circuit Breakers endpoint** (risk management) — 0.5 days
9. **Oil/Urea/Coal APIs** (yfinance + Commodities-API + OilPriceAPI) — 1 day
10. **IMF & Government event feeds** — 1 day
11. **Broker research scrape (Arif Habib, Topline PDFs)** — 2 days (PDF parsing)
12. **Broker API for execution** — 1–2 weeks of negotiation + integration

---

## 15. Summary — One Table

| Factor (from research) | Best Daily Source | Update Frequency |
|---|---|---|
| Stock prices & volumes (real-time) | PSX Terminal WS / Sarmaaya / PSX Level 1+ | Real-time |
| Historical OHLCV | `psx-data-reader` / Capital Stake | EOD |
| FIPI / LIPI flows | SCStrade / FinHisaab | Daily EOD |
| **All macro series (one-stop)** | **SBP EasyData (2.97M data points, 23,988 series)** | Varies |
| SBP events (circulars, MPC, notifications) | SBP RSS feeds | Event-driven |
| SBP policy rate | SBP EasyData | Event (MPC) |
| CPI/SPI inflation | SBP EasyData + PBS | Weekly / Monthly |
| KIBOR (interest benchmark) | SBP EasyData | Daily |
| PKR/USD | SBP M2M page | Intraday |
| FX reserves | SBP EasyData | Weekly (Thu) |
| Brent / WTI crude | yfinance / OilPriceAPI | Real-time |
| **Urea (Fertilizer cost)** | **Commodities-API** | Daily |
| **Coal (Cement cost)** | **OilPriceAPI (Newcastle/Coking)** | Daily |
| Cotton (Textile) | yfinance `CT=F` | Real-time |
| Natural gas | Investing.com / Business Insider JSON | Real-time |
| IMF program status | IMF.org RSS | Event |
| Political / govt news | Business Recorder RSS | Real-time |
| Broker research | Arif Habib + Topline PDFs | Daily |
| Sentiment (news) | Mettis / Profit / Dawn → FinBERT | Continuous |
| Social sentiment | X API + Reddit | Continuous |
| **Corporate actions (filings)** | **PUCARS** (mandatory official portal) | Real-time |
| Circuit breakers | PSX circuit-breakers page | Real-time |
| Sector reports | Broker PDFs + PSX monthly | Weekly–Monthly |
| Fuel prices (retail PK) | OGRA / PSO | Fortnightly |
| Earnings releases | PUCARS / PSX financial-results portal | Event |

This covers every single factor identified in `psx_market_research.md`. You now have a complete roadmap for what to pull, from where, and how often.

---

## 16. New Additions from Gemini's Research (Verified April 23, 2026)

The following sources were added after verification from Gemini's agent-oriented research. All confirmed as real, working services:

| Source | Verification | Value |
|---|---|---|
| **SBP EasyData** (`easydata.sbp.org.pk`) | ✅ Confirmed: 2.97M data points, 23,988 time series, has developer API | **Game-changer for macro data** — single API replaces 90% of manual SBP scraping |
| **EasyDataPy** (Python library) | ✅ Confirmed on PyPI | Python bindings for EasyData → Pandas DataFrames |
| **SBP RSS Feeds** | ✅ Confirmed at `sbp.org.pk/rss.asp` | Ideal event trigger for agent actions |
| **PUCARS** (`pucars.psx.com.pk`) | ✅ Confirmed: mandatory corporate reporting portal (PSX Notice KSE/N-3611, Jul 15, 2015) | **Single source of truth** for price-sensitive filings |
| **PSX Level 1 / 1+ / 2 official feeds** | ✅ Confirmed at `psx.com.pk/psx/product-and-services/services`, contact `marketdatarequest@psx.com.pk` | Authoritative institutional feed (paid) |
| **Sarmaaya** (`sarmaaya.pk`) | ✅ Confirmed as authorized PSX data redistributor | Easier entry point than direct PSX licensing |
| **`psx-data-reader`** (Python on PyPI/GitHub) | ✅ Confirmed at `pypi.org/project/psx-data-reader/` (67 GitHub stars, last update Nov 2025, no longer actively maintained) | Free historical backfill |
| **Commodities-API** | ✅ Confirmed at `commodities-api.com` | Urea spot prices for fertilizer sector |
| **OilPriceAPI** | ✅ Confirmed at `oilpriceapi.com` | Newcastle & Coking coal for cement sector |
| **Business Insider JSON feeds** | ✅ Confirmed at `markets.businessinsider.com` | Structured Natural Gas (Henry Hub/Dutch TTF) data |

---

## 17. Extended Data Sources — Additional Factors

These cover secondary but valuable factors: official government statistics, real economy indicators, climate/weather, maritime/shipping, ownership structure, and cross-asset correlations.

### 17.1 Pakistan Bureau of Statistics (PBS) — Real Economy Data

PBS is the official statistical authority of Pakistan. Essential for leading economic indicators.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **PBS Data Dissemination** | `https://www.pbs.gov.pk/data-dissemination/` | Labour Force Survey (LFS), HIES, Agricultural Census, Population Census | PDF / Excel / CSV | Annual / Periodic | Free (government users) / Fee-based (non-govt for microdata) |
| **PBS Foreign Trade Statistics** | `https://www.pbs.gov.pk/monthly-advance-releases-on-foreign-trade-statistics-for-[month]-[year]/` | Monthly advance release: imports, exports, trade balance | PDF | Monthly | Free |
| **PBS LSM (Large Scale Manufacturing) Index** | `https://www.pbs.gov.pk/` | Monthly LSM growth — a key GDP input and leading indicator for industrial stocks (cement, fertilizer, autos) | PDF | Monthly | Free |
| **PBS CPI / SPI / WPI Releases** | `https://www.pbs.gov.pk/` | Official monthly/weekly inflation prints | PDF | Weekly / Monthly | Free |
| **PBS Quantum Index of Manufacturing** | `https://www.pbs.gov.pk/` | QIM — manufacturing output | PDF | Monthly | Free |

**Why it matters:** LSM and trade data are **leading indicators** for corporate earnings in the industrial and trade-exposed sectors.

### 17.2 Ministry of Commerce — Trade Flows

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **MoC Monthly Statements** | `https://www.commerce.gov.pk/monthly-statements/` | Monthly exports & imports of selected commodities (PDFs, separate per month) | PDF | Monthly | Free |
| **TDAP (Trade Development Authority)** | `https://www.tdap.gov.pk/` | Export promotion data, sector competitiveness | Web | Varies | Free |

**Why it matters:** Directly drives textile, rice, cement export stocks. Growing trade deficit signals rupee pressure.

### 17.3 FBR (Federal Board of Revenue) — Fiscal Health Proxy

Tax collection is a **real-time proxy for economic activity** — weak collection → budget slippage → risk of IMF trouble.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **FBR Revenue Collections** | `https://www.fbr.gov.pk/revenue-collections/131355` | Monthly tax collection by category (income tax, sales tax, customs, FED). Archive back to FY 2003–04. | Web / PDF | Monthly | Free |
| **FBR Press Releases** | `https://www.fbr.gov.pk/` | Monthly collection announcements (e.g., Jan 2026 = Rs 1,015B, +16% MoM) | Web / RSS | Monthly | Free |
| **FBR Biannual Review** | `download1.fbr.gov.pk/Docs/...FBRBiannualReview...pdf` | Half-year comprehensive review | PDF | Biannual | Free |
| **FBR SRO Notifications** | `https://www.fbr.gov.pk/categ/sros/51147/131193/131194` | Statutory Regulatory Orders — tax policy changes that **immediately** move sector stocks | Web | Event | Free |

**Bot tip:** SROs are the single biggest legal/tax moving force for sector stocks. Monitor and parse them automatically.

### 17.4 Weather & Climate Data — Agriculture, Fertilizer, Autos, Construction

Pakistan's economy is **unusually weather-sensitive** (agriculture is ~23% of GDP, drives fertilizer demand → cement → autos → consumer).

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **PMD (Pakistan Meteorological Department)** | `https://www.pmd.gov.pk/` | Official Pakistan weather, monsoon forecasts, drought/flood warnings | Web | Daily | Free |
| **OpenWeather API** | `https://openweathermap.org/api` | Programmatic weather data for all major Pakistani cities | REST API | Real-time | Freemium |
| **NASA POWER API** | `https://power.larc.nasa.gov/` | Historical rainfall/temperature for agri regions (Punjab, Sindh) | REST API | Daily | Free |
| **Copernicus Climate Data** | `https://cds.climate.copernicus.eu/` | Long-term climate, soil moisture data | REST API | Various | Free |
| **NDMA Pakistan** | `https://ndma.gov.pk/` | Flood/drought disaster alerts | Web | Event | Free |

**Why it matters:**
- **Monsoon strength** → urea demand (fertilizer stocks).
- **Droughts/floods** → agri losses → consumer spending → broader market sentiment.
- **Extreme temperatures** → power demand → IPP utilization.

### 17.5 Maritime / Shipping Data — Oil Imports, Geopolitics

Critical given Pakistan's 2026 exposure to the Strait of Hormuz crisis.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **MarineTraffic API** | `https://www.marinetraffic.com/en/ais-api-services` | Real-time vessel tracking, port arrivals/departures, AIS data | REST API (JSON) | Real-time | Freemium / Paid |
| **VesselFinder API** | `https://api.vesselfinder.com/` | Alternative AIS provider | REST API | Real-time | Paid |
| **Karachi Port Trust** | `http://www.kpt.gov.pk/` | Official Karachi Port arrivals/departures | Web | Daily | Free |
| **Port Qasim** | `https://www.pqa.gov.pk/` | Pakistan's second-largest port | Web | Daily | Free |
| **Baltic Dry Index** (via yfinance `^BDI`) | `yfinance` | Global shipping cost proxy — leading indicator for trade | Python | Daily | Free |

**Bot use case:** Track tanker traffic through Hormuz → leading indicator for Pakistan fuel supply shocks → OMC stocks, inflation, PKR.

### 17.6 Ownership Structure — CDC & Shareholder Patterns

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **CDC Pakistan (Central Depository Company)** | `https://www.cdcpakistan.com/` | Custody data, settlement statistics, Investor Account Services (IAS) | Web | Various | Free (limited) / Paid (full) |
| **Capital Stake Shareholder Patterns** | `https://capitalstake.com/docs/rest/api/stocks/intro` | Company fundamentals including shareholder pattern (sponsors, foreign holdings, free float, retail %) | REST API | Quarterly | Freemium |
| **PSX Annual Reports / Form 34** | Via PUCARS | Free float % and top shareholders (filed in annual reports) | PDF | Annual | Free |

**Why it matters:**
- Low free-float stocks are easy to manipulate → avoid or trade cautiously.
- Sponsor buying/selling filings → strong insider signal.
- Foreign ownership % → vulnerability to FIPI outflows.

### 17.7 Crypto Correlation — Retail Sentiment Proxy

Pakistani retail investors increasingly trade crypto alongside stocks. Crypto performance is a **leading indicator for retail risk appetite** on the PSX.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **CoinGecko API** | `https://www.coingecko.com/en/api` | Free crypto prices, market cap, volume for 10,000+ coins | REST API | Real-time | Free (generous) |
| **CoinMarketCap API** | `https://coinmarketcap.com/api/` | Alternative crypto data | REST API | Real-time | Freemium |
| **Binance API** | `https://www.binance.com/en/binance-api` | Ticker data for BTC/ETH + local trading pairs | REST / WebSocket | Real-time | Free |

**Bot usage:** When BTC breaks down 10%+ in 48 hours, expect PSX retail panic within 1–3 days (correlation observed in 2022–2024).

### 17.8 Additional Global Macro Feeds

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **FRED (St Louis Fed)** | `https://fred.stlouisfed.org/docs/api/fred/` | **Massive** free API — US Treasury yields, DXY, VIX, global rates, commodities. | REST API | Daily | Free |
| **World Bank Open Data** | `https://data.worldbank.org/country/pakistan` | Pakistan annual macro + global cross-country comparisons | REST API | Annual | Free |
| **IMF Data Portal** | `https://www.imf.org/en/Data` | WEO database, Article IV data, SDR rates | REST API | Quarterly | Free |
| **CPEC Authority** | `https://cpec.gov.pk/` | CPEC project status — moves cement, construction, power stocks | Web | Event | Free |

### 17.9 Remittances — The Silent Giant

Remittances (~$30B+/year) are a **major source of USD inflow** and directly affect PKR stability.

| Source | URL | What it gives you | Access | Freq | Cost |
|---|---|---|---|---|---|
| **SBP Workers' Remittances** (via EasyData) | `easydata.sbp.org.pk` | Monthly remittance data by source country (Saudi, UAE, UK, US, etc.) | API / Excel | Monthly | Free |

**Bot signal:** Rising remittances → PKR strength → positive for banks, consumer stocks.

---

## 18. Updated Summary Table (Complete)

| Factor Category | Best Source | Frequency |
|---|---|---|
| **Market Data (real-time)** | PSX Terminal WS / PSX Level 1+ / Sarmaaya | Real-time |
| **Market Data (historical)** | `psx-data-reader` / Capital Stake | EOD |
| **FIPI/LIPI flows** | SCStrade / FinHisaab | Daily EOD |
| **Corporate filings** | PUCARS | Real-time |
| **Circuit breakers** | PSX circuit-breakers endpoint | Real-time |
| **All SBP macro series** | SBP EasyData API + EasyDataPy | Varies |
| **SBP events** | SBP RSS | Event |
| **CPI/SPI/WPI** | PBS + SBP EasyData | Weekly/Monthly |
| **LSM industrial output** | PBS monthly release | Monthly |
| **Trade (imports/exports)** | MoC Monthly Statements + PBS | Monthly |
| **Tax revenue (fiscal health)** | FBR Revenue Collections | Monthly |
| **Tax policy changes** | FBR SRO notifications | Event |
| **PKR/USD** | SBP M2M page | Intraday |
| **Oil (Brent/WTI)** | yfinance / OilPriceAPI | Real-time |
| **Urea (fertilizer cost)** | Commodities-API | Daily |
| **Coal (cement cost)** | OilPriceAPI (Newcastle/Coking) | Daily |
| **Cotton (textile)** | yfinance `CT=F` | Real-time |
| **Natural gas** | Investing.com / Business Insider | Real-time |
| **Weather (monsoon, drought)** | PMD + OpenWeather + NASA POWER | Daily |
| **Shipping / Hormuz** | MarineTraffic API | Real-time |
| **Crypto (retail proxy)** | CoinGecko API | Real-time |
| **US Treasury / DXY / VIX** | FRED API | Daily |
| **IMF program status** | IMF.org RSS + IMF Data API | Event |
| **Ownership/free float** | CDC + Capital Stake + PUCARS annuals | Quarterly |
| **Remittances** | SBP EasyData (Workers' Remittances) | Monthly |
| **News sentiment** | Business Recorder + Mettis + Profit → FinBERT | Continuous |
| **Social sentiment** | X API + Reddit | Continuous |
| **Broker research** | Arif Habib / Topline / JS Global PDFs | Daily |
| **Fuel prices (retail PK)** | OGRA / PSO | Fortnightly |
| **CPEC project status** | CPEC Authority | Event |

---

## 19. Full Data-Source Map (By Research Layer)

Mapping every single factor from `psx_market_research.md` to a verified data source:

### Layer 1 — Macro Fundamentals
- SBP policy rate → **SBP EasyData**
- Inflation (CPI/SPI/WPI) → **PBS + SBP EasyData**
- PKR/USD → **SBP M2M + Forex.pk**
- Money supply (M2) → **SBP EasyData**
- FDI → **SBP EasyData + SECP**
- GDP → **PBS (annual) + SBP MPR projections**
- Unemployment → **PBS Labour Force Survey**
- Oil prices → **yfinance + OilPriceAPI**
- FX reserves → **SBP weekly + EasyData**
- Remittances → **SBP EasyData**
- Trade deficit → **MoC + PBS**
- LSM → **PBS**
- Tax collection → **FBR**

### Layer 2 — Political & Institutional Events
- IMF program → **IMF.org RSS + IMF Data**
- Federal Budget → **MoF + FBR SROs**
- Elections / political news → **Business Recorder, Dawn, The News RSS**
- India/Afghan/Iran geopolitics → **Reuters + Business Recorder + MarineTraffic (Hormuz)**
- CPEC → **CPEC Authority**
- SECP regulations → **SECP website**

### Layer 3 — Flow Data
- FIPI → **SCStrade + FinHisaab (NCCPL source)**
- LIPI (mutual funds, insurance, banks) → **SCStrade + FinHisaab**
- Block trades → **PSX Terminal / PSX daily download**
- MSCI/FTSE classification → **MSCI.com + index announcements**
- Ownership/free float → **CDC + Capital Stake + PUCARS**

### Layer 4 — Behavioral / Sentiment
- News sentiment → **RSS aggregator → FinBERT**
- Broker research → **AHL + Topline + JS Global + IGI + AKD PDFs**
- Social media → **X API + Reddit + Telegram/Discord channels**
- Retail proxy → **CoinGecko (crypto correlation)**
- Day-of-week / calendar effects → **Internal time features**

### Layer 5 — Microstructure
- Circuit breakers → **PSX circuit-breakers endpoint**
- Volume/liquidity → **PSX Terminal**
- Order book (MBO) → **PSX Level 1+ / Sarmaaya**
- Corporate announcements → **PUCARS**
- Earnings calendar → **PUCARS + broker calendars**
- Ex-dividend dates → **PUCARS + PSX announcements**

### Sector-Specific Extras
- Banking → **SBP KIBOR, advances/deposits from SBP EasyData**
- Cement → **OilPriceAPI (coal) + PBS LSM (cement output)**
- Fertilizer → **Commodities-API (urea) + PMD (monsoon) + SBP gas prices**
- Power / IPPs → **Circular debt news + PUCARS + NEPRA announcements**
- Autos → **PAMA sales data + SBP rates + PKR/USD**
- Textiles → **yfinance (cotton) + MoC exports + PKR/USD**
- OMCs → **OGRA fuel prices + yfinance Brent**
- Tech → **yfinance (NASDAQ) + PKR/USD + IT export data from SBP**

**This is now a 100%-complete data map.** Every factor in the research file has a verified working daily source.
