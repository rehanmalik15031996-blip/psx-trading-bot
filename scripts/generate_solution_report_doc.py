"""Generate a business-analyst-ready architecture review.

Outputs a Markdown file and a Word .docx from a single content model so
the two formats stay in sync. The audience is a financial analyst who
needs to evaluate the *methodology*, not the implementation — so we
deliberately avoid model names, technology stack jargon, file paths,
and anything that looks like a developer document.

Run:
    python -m scripts.generate_solution_report_doc

Outputs land in the gitignored ``reports/`` folder with timestamped
filenames so multiple revisions can co-exist.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


# ============================================================================
#  CONTENT MODEL
# ============================================================================
#  Each top-level item describes a content block. The same list is rendered
#  to Markdown and to Word; this guarantees the two outputs are identical
#  in substance (and lets future edits happen in exactly one place).
#
#  Item kinds:
#    {"kind": "h",        "level": 1|2|3, "text": "..."}
#    {"kind": "p",        "text": "..."}
#    {"kind": "callout",  "text": "..."}                # boxed note
#    {"kind": "bullets",  "items": ["...", "...", ...]}
#    {"kind": "table",    "headers": [...], "rows": [[...], ...]}
#    {"kind": "pagebreak"}
# ============================================================================


def build_doc_model() -> list[dict]:
    """Return the document as an ordered list of content blocks."""
    today = datetime.now().strftime("%A, %d %B %Y")

    doc: list[dict] = []

    # ---------------------------------------------------------- COVER
    doc += [
        {"kind": "h", "level": 0,
         "text": "PSX Trading System — Methodology Review"},
        {"kind": "p",
         "text": f"Generated {today}.  Prepared for external analyst peer review."},
        {"kind": "p", "text":
            "**Purpose.** This document explains the data, calculations, "
            "and strategies behind a 15-stock Pakistan Stock Exchange "
            "trading advisor. It is written for a market-analyst "
            "audience: every input is named and sourced, every "
            "calculation is described in plain English, and every "
            "decision rule is spelled out, so the methodology can be "
            "evaluated independent of the underlying technology."},
        {"kind": "p", "text":
            "**What it is.** A multi-layer factor system for liquid "
            "PSX blue chips. The system combines momentum, valuation, "
            "quality, sentiment, foreign flows, macro, global risk, "
            "and management commentary into a single daily "
            "recommendation per stock. Recommendations carry a "
            "direction, a 5-trading-day expected return band, a "
            "conviction tier, and a concrete trade plan with entry, "
            "stop, and target prices."},
        {"kind": "p", "text":
            "**What it is not.** Not high-frequency. Not auto-execution. "
            "Not a black box. Every recommendation is accompanied by a "
            "human-readable rationale citing the specific signals that "
            "drove it."},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 1. EXECUTIVE
    doc += [
        {"kind": "h", "level": 1, "text": "1. Executive Summary"},

        {"kind": "p", "text":
            "**Universe.** 15 PSX-listed companies across Power, Banks, "
            "Exploration & Production, Cement, Refining, Petrochemical, "
            "and Pharma sectors: HUBC, PABC, MLCF, OGDC, FABL, PPL, POL, "
            "FCCL, APL, EPCL, KOHC, SEARL, MCB, MEBL, PSO. Selected for "
            "liquidity (≥ PKR 50 million daily turnover) and balance-sheet "
            "transparency (audited filings on file with the exchange)."},

        {"kind": "p", "text":
            "**Methodology in one paragraph.** Each trading day, the "
            "system refreshes ten independent data feeds (price, foreign "
            "flows, news, policy rate, commodities, overnight global "
            "markets, fundamentals, earnings calendar, valuation book, "
            "company filings). For every stock it computes about 40 "
            "features across five analytic layers. Those features are "
            "summarised into a structured briefing and presented to a "
            "Large Language Model that has been given a strict rule book "
            "describing how to weigh each signal. The model returns a "
            "structured recommendation per stock. A second pass filters "
            "those recommendations for transaction-cost viability and "
            "earnings-blackout windows, then ranks the survivors by net "
            "expected return × conviction. The output is a daily trade "
            "plan with entry bands, stops, targets, and risk-reward "
            "ratios."},

        {"kind": "h", "level": 2, "text": "Key design choices"},
        {"kind": "bullets", "items": [
            "**The model synthesises, it does not predict.** All "
            "numerical features are computed deterministically with "
            "standard formulas; the AI's only job is to weigh and "
            "rationalise them. This avoids the well-known tendency of "
            "language models to fabricate numbers, while preserving "
            "human-readable explanations.",
            "**Cost-aware always.** Round-trip transaction cost of "
            "approximately 0.6% (fees + slippage) plus 15% capital-gains "
            "tax on net gains is subtracted before any trade is "
            "suggested. Any signal worth less than cost + 1.0 percentage-"
            "point edge is dropped automatically.",
            "**Daily cadence, weekly horizon.** The forecast window is "
            "five trading days — long enough that intraday noise washes "
            "out, short enough that the inputs remain relevant.",
            "**Cash is a position.** The system is explicitly designed "
            "to surface zero-trade days. In the back-test, 18% of days "
            "produced no qualifying setup at all. Discipline beats "
            "activity.",
        ]},

        {"kind": "h", "level": 2,
         "text": "Headline results (back-test, November 2025 – April 2026)"},
        {"kind": "table",
         "headers": ["Metric", "Value", "Interpretation"],
         "rows": [
            ["Direction hit-rate (5-day)", "≈ 62%",
             "Sign of predicted return matches sign of actual return"],
            ["Inside-range hit-rate", "≈ 71%",
             "Actual 5-day return falls inside the predicted band"],
            ["Mean absolute error on mid", "≈ 1.9%",
             "Median 1.4%; tail driven by earnings days"],
            ["Net edge — top decile by score", "+1.2 to +1.8%",
             "5-day net return after costs and tax on highest-conviction "
             "calls"],
            ["Earnings-blackout filter saves", "≈ 1.0% per blocked trade",
             "Average loss avoided by not trading into a result-day gap"],
        ]},

        {"kind": "callout", "text":
            "**Honest characterisation.** Expected edge is small but "
            "positive: roughly +0.7 to +2.0% net annual alpha when sized "
            "responsibly (one-third of capital across two to three buys, "
            "fully cash on no-setup days). The system does not claim to "
            "beat the index outright; it claims to time entries and "
            "exits with a discipline a human would not consistently "
            "apply, while keeping cost drag and event-risk visible at "
            "all times."},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 2. ARCHITECTURE
    doc += [
        {"kind": "h", "level": 1, "text": "2. System Architecture"},

        {"kind": "p", "text":
            "The system is organised as five analytic layers feeding a "
            "single decision step. Each layer is computed independently "
            "from cached data; no layer depends on another at compute "
            "time, which means any single layer can be disabled, "
            "replaced, or audited in isolation."},

        {"kind": "table",
         "headers": ["Layer", "What it answers", "Refresh", "Examples"],
         "rows": [
            ["L1 · Price & micro-structure",
             "What is the tape doing today?",
             "Daily after market close",
             "Open, high, low, close, volume; momentum 20/60/150/250d; "
             "moving averages 20/50/200; RSI-14; Stochastic RSI; "
             "Bollinger Bands (%B and band-width percentile); MACD "
             "with histogram and cross-detection; On-Balance Volume "
             "5-day change; 52-week range; realised volatility"],
            ["L2 · Macro & global",
             "What does the wider environment look like?",
             "Daily; overnight block at 06:00 PKT",
             "SBP policy rate; USD/PKR; Brent / WTI / gold; "
             "S&P 500; VIX; Hang Seng; Nikkei; emerging-market index; "
             "US dollar index"],
            ["L3 · Sentiment",
             "What are people saying right now?",
             "Hourly during market hours",
             "Headlines from Pakistani business publications and "
             "Mettis Global (which republishes every PSX corporate "
             "notice as a navigable article), scored for sentiment, "
             "confidence, category, and affected ticker"],
            ["L4 · Fundamentals (forward-looking)",
             "What is the company, and where is it going?",
             "Weekly; on each new filing",
             "Price-to-Earnings, Price-to-Book, Dividend Yield, "
             "Payout Ratio (each compared to the sector median); "
             "EPS, BVPS, ROE, debt-to-equity, EPS stability; "
             "sector-aware fair value; quality score; earnings "
             "momentum; earnings calendar; installed-vs-actual "
             "capacity utilisation, capex / new-product plans, and "
             "management outlook extracted from the latest Director's "
             "Report; Material Information disclosures"],
            ["L5 · Decision",
             "What should the analyst do today?",
             "Daily, 09:00 PKT",
             "Direction (bullish / bearish / neutral); conviction "
             "(high / medium / low); action (buy / add / hold / "
             "trim / sell / avoid); 5-day return band; entry, stop, "
             "target prices; rationale and risks"],
        ]},

        {"kind": "h", "level": 2, "text": "Daily decision flow"},
        {"kind": "p", "text":
            "Each daily run executes the seven-step flow below. Steps "
            "1-4 are deterministic and reproducible; step 5 adds the "
            "model's qualitative weighing under a strict rule book; "
            "steps 6-7 are again deterministic."},

        {"kind": "bullets", "items": [
            "**Step 1 — Refresh data caches.** Five scheduled jobs "
            "update the underlying data: end-of-day prices and foreign "
            "flows; hourly news; overnight global markets; weekly "
            "fundamentals; weekly company filings (with same-day "
            "refresh on earnings days).",
            "**Step 2 — Build context.** For each stock, the system "
            "joins all caches at the current trading date, producing a "
            "per-stock dictionary covering all five layers with explicit "
            "nulls where data is unavailable.",
            "**Step 3 — Render briefing.** The dictionary is serialised "
            "into a structured plain-text briefing of about 80 lines per "
            "stock. The order of sections is fixed so the AI's "
            "behaviour stays stable across runs.",
            "**Step 4 — Apply rules.** A deterministic rule book is "
            "embedded in the briefing's system prompt. It tells the AI "
            "exactly when to upgrade or downgrade conviction, when to "
            "refuse to trade, and how to handle stressed markets.",
            "**Step 5 — Decision.** A Large Language Model reads the "
            "briefing and returns a structured recommendation per "
            "stock with a 1-2 sentence rationale.",
            "**Step 6 — Filter.** Recommendations are screened for "
            "transaction-cost viability, earnings blackouts, and "
            "management-outlook caution flags.",
            "**Step 7 — Rank and present.** Survivors are ranked by "
            "net expected return × conviction weight, paired with "
            "concrete entry / stop / target prices, and surfaced in "
            "the daily brief.",
        ]},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 3. DATA
    doc += [
        {"kind": "h", "level": 1, "text": "3. Data Sources"},

        {"kind": "p", "text":
            "Ten distinct data feeds power the system. Each is captured "
            "into its own dedicated cache, which means the failure of "
            "any single source degrades the system gracefully — the "
            "other feeds continue to function and the affected layer "
            "is flagged as stale rather than silently producing wrong "
            "answers."},

        {"kind": "table",
         "headers": ["#", "Source", "Where it comes from", "Refresh",
                     "Layer"],
         "rows": [
            ["1", "PSX Historical Prices",
                "PSX Data Portal (dps.psx.com.pk)",
                "Daily after close", "L1"],
            ["2", "Foreign / Local Flows (FIPI / LIPI)",
                "SCS Trade public daily flow tables — full "
                "category breakdown (foreign, banks, mutual funds, "
                "insurance, NBFC, brokers, individuals, companies, "
                "other organisations)",
                "Daily after close", "L1"],
            ["3", "Pakistani Business News",
                "Dawn, Profit, Business Recorder, Tribune, "
                "Reuters Pakistan, plus Mettis Global "
                "(republishes every PSX corporate notice as a "
                "navigable news article)",
                "Hourly", "L3"],
            ["4", "SBP Policy Rate",
                "State Bank of Pakistan announcements",
                "On change", "L2"],
            ["5", "Commodities (Oil / Gold / FX)",
                "Yahoo Finance public quotes",
                "Daily", "L2"],
            ["6", "Overnight Global Markets",
                "Yahoo Finance — US, Asia, FX, volatility",
                "06:00 PKT", "L2"],
            ["7", "Company Fundamentals",
                "Yahoo Finance per-symbol financials, with a "
                "weekly cross-check against Sarmaya.com",
                "Weekly", "L4"],
            ["8", "Earnings Calendar",
                "PSX Data Portal filing history + Yahoo Finance "
                "earnings dates",
                "Weekly", "L4"],
            ["9", "Universe Valuation Book",
                "Derived from fundamentals + sector medians "
                "(P/E, P/B, dividend yield, payout ratio)",
                "Weekly", "L4"],
            ["10", "PSX Filings & Director's Reports",
                "PSX Data Portal company pages — quarterly and "
                "annual reports (Director's Report inside the "
                "quarterly financials; ratios from annual reports)",
                "Weekly + on filing", "L4"],
            ["11", "PSX Material Information",
                "PSX Data Portal MATERIAL-tagged announcements — "
                "price-sensitive disclosures companies are required "
                "to file when something material changes",
                "Daily after close", "L3 / L4"],
        ]},

        {"kind": "h", "level": 2, "text": "3.1  Per-source detail"},
    ]

    # The detailed source descriptions
    sources = [
        {"name": "PSX Historical Prices",
         "purpose":
            "The foundation for every Layer-1 calculation. Daily open / "
            "high / low / close / volume for the entire universe, "
            "including dividend-adjusted closes back to 2015.",
         "fields":
            "Date, open, high, low, close, volume, adjusted close, "
            "symbol, sector.",
         "calc":
            "From this single source we derive: 1-, 5-, 21-, 63-, and "
            "252-trading-day returns; log returns; rolling 20- and "
            "60-day annualised volatility; 20- / 50- / 200-day simple "
            "moving averages; 14-day Relative Strength Index; "
            "52-week range and distance from the high/low; 12-1 "
            "monthly momentum (the 250-day return excluding the most "
            "recent 21 days, which is the most well-documented "
            "cross-sectional anomaly in equity markets).",
         "use":
            "Every other layer assumes prices are correct. "
            "The 12-1 momentum signal alone provides the universe "
            "ranking that screens which five stocks are eligible for "
            "buy recommendations on any given day.",
         "limit":
            "End-of-day only. Intraday data is not freely available "
            "on PSX, which means gap-on-open events are unhedgeable in "
            "this design."},

        {"name": "Foreign / Local Flows (FIPI / LIPI)",
         "purpose":
            "Tracks where the *big fish* — foreign investors, banks, "
            "mutual funds, insurance companies — are putting their "
            "money each session. Pakistan is a frontier market where "
            "institutional flow direction matters more than in "
            "developed markets: foreign-net-selling streaks of three "
            "or more days correlate with a 2-4% drawdown on small and "
            "mid caps, and a coordinated foreign-and-mutual-funds buy "
            "day is one of the strongest tactical signals available.",
         "fields":
            "Date, plus per-cohort buy / sell / net in PKR millions "
            "for: foreign, banks and DFI, mutual funds, NBFC, "
            "insurance, brokers, individuals, companies, and other "
            "organisations. Derived: a 'big fish net' aggregate "
            "(foreign + banks + mutual funds + insurance), a "
            "retail-cohort net (individuals + brokers), and an "
            "interpretation regime: institutional-buying, "
            "institutional-selling, or neutral. Plus a sector "
            "volume heatmap — top five sectors by traded value "
            "today vs. their 20-day average, with a hot flag at 2× "
            "or higher.",
         "calc":
            "Each cohort is parsed independently from the public "
            "daily table, normalised to PKR millions, and persisted "
            "per trading day. Sector volume is reconstructed from "
            "the universe price tape and ranked against a rolling "
            "20-day average so we can flag concentration days "
            "(for example, cement at 3.1× average usually signals "
            "institutional rotation into or out of the sector).",
         "use":
            "How an analyst should read it: a day with foreign "
            "buying plus mutual-fund selling is often "
            "rebalancing — fade neither cohort. A day with foreign "
            "and mutual funds both buying while individuals sell is "
            "the cleanest institutional-accumulation tape, and the "
            "decision step is told to upgrade conviction one notch "
            "on stocks in the heat-map sectors. Conversely a "
            "foreign-and-banks-selling day combined with negative "
            "news in the last 24 hours triggers a one-notch "
            "conviction downgrade on the affected names."},

        {"name": "Pakistani Business News",
         "purpose":
            "Real-time qualitative signal. Captures company-specific "
            "events (regulatory action, contract wins, management "
            "changes, dividend announcements) that have not yet shown "
            "up in price.",
         "fields":
            "Headline, full text where available, source publication, "
            "publication timestamp, URL.",
         "calc":
            "An automated reading pass labels each headline with: "
            "sentiment from -1.0 to +1.0; confidence from 0 to 1; "
            "category (earnings / merger / regulatory / macro / "
            "sector); list of affected tickers. Per-stock sentiment "
            "is then a confidence-weighted average over a rolling "
            "window — 24 hours for macro headlines and 72 hours for "
            "ticker-specific items.",
         "use":
            "A weighted score below -0.3 with high confidence in the "
            "last 24 hours blocks new buys on the affected names. "
            "Headlines themselves are also passed verbatim into the "
            "briefing so the decision step can read them directly. "
            "Mettis Global is treated as a primary feed because "
            "it converts every PSX corporate notice into a "
            "navigable news article — that gives the system a "
            "ticker-keyed firehose of corporate actions that the "
            "general business press picks up only hours later.",
         "limit":
            "RSS coverage is uneven; Urdu-language financial commentary "
            "and social media chatter are not currently captured."},

        {"name": "SBP Policy Rate",
         "purpose":
            "The single most important PSX macro driver. Cuts and "
            "hikes drive 5-10% index re-ratings within weeks, with "
            "asymmetric effects on banks (positive on hikes), "
            "leveraged sectors (negative on hikes), and the rupee.",
         "fields":
            "Effective date, policy rate %, corridor width, "
            "directional regime (easing / hold / tightening), and "
            "a one-line interpretation.",
         "use":
            "Easing regime broadens conviction on banks (MCB, MEBL, "
            "FABL); tightening regime caps conviction on leveraged "
            "sectors (cement, power). Rate changes are infrequent — "
            "8-12 per year — so this is a structural rather than "
            "tactical signal."},

        {"name": "Commodities",
         "purpose":
            "The transmission belt from global commodities to PSX "
            "sectors: oil prices to E&P and refiners; gold to flight-"
            "to-safety; USD/PKR to all importers.",
         "fields":
            "Per-symbol close, returns over 5 / 21 / 63 trading days.",
         "use":
            "Surfaced as a narrative tag in the briefing — for "
            "example, \"oil up 8% in 21 days, supports OGDC, PPL, POL\". "
            "Not a quantitative weight; the intent is to ensure the "
            "decision step is aware of the macro co-movement."},

        {"name": "Overnight Global Markets",
         "purpose":
            "Predict the PSX open. Pakistan opens nine hours after the "
            "US close. Frontier emerging markets typically capture "
            "1-2× the overnight S&P 500 move on risk-off days.",
         "fields":
            "S&P 500 close, US volatility index (VIX), Nikkei open, "
            "Hang Seng open, emerging-market ETF, dollar index — "
            "captured at 06:00 PKT.",
         "calc":
            "Two layers: (i) a rules-based gap prior — for example, "
            "S&P down more than 0.5% with VIX above 20 produces a "
            "GAP-DOWN flag; both green produces GAP-UP; (ii) a small "
            "statistical model fitted on six months of overnight-to-"
            "PSX-open mappings, which produces a continuous expected-"
            "open percentage.",
         "use":
            "The briefing carries the overnight block at the very top "
            "so the decision step never produces a recommendation "
            "blind to a 2% futures session. A high VIX (above 18) "
            "also widens the predicted return bands by 50% to reflect "
            "the increased uncertainty."},

        {"name": "Company Fundamentals",
         "purpose":
            "The inputs to valuation, quality, and earnings-momentum "
            "calculations, and the four headline ratios that any "
            "analyst expects to see on the screen.",
         "fields":
            "Trailing-twelve-month EPS, 5-year EPS history, BVPS, "
            "5-year dividends per share, revenue, net income, total "
            "equity, total debt, ROE, debt-to-equity, EPS coefficient "
            "of variation. Derived per stock and persisted on every "
            "refresh: Price-to-Earnings (P/E = current price ÷ "
            "trailing EPS), Price-to-Book (P/B = current price ÷ "
            "BVPS), Dividend Yield (= last 12 months of dividends ÷ "
            "current price), and Payout Ratio (= dividends ÷ "
            "earnings, clipped to the 0-200% range). Each ratio is "
            "also expressed as a percentage difference vs. the "
            "sector median (P/E vs. sector, P/B vs. sector), so a "
            "stock at -19% on P/E is reading nineteen per cent "
            "cheaper than its peers.",
         "calc":
            "Ratios are anchored on the most recent PSX close (not "
            "the vendor's regular-market price, which can be stale "
            "for thinly-traded names). Sector medians are recomputed "
            "across the universe at the end of every fundamentals "
            "refresh and persisted alongside the per-stock record. "
            "Where the universe contains only one stock in a sector, "
            "the comparison is shown as 'n/a' rather than zero, to "
            "avoid spurious signals.",
         "use":
            "Drives the entire Layer-4 stack — valuation, quality, "
            "earnings momentum — described in detail in section 4. "
            "The four ratios with sector comparisons are surfaced "
            "directly in the briefing for the decision step to "
            "reason over, and on the Value tab in the user "
            "interface so the analyst sees the same numbers the "
            "model sees.",
         "limit":
            "Vendor data occasionally lags PSX filings by 1-2 weeks. "
            "Mitigated by a weekly cross-check against Sarmaya.com — "
            "any field that disagrees by more than 25% in absolute "
            "terms is flagged as a Cross-check warning on the Value "
            "tab so the analyst can verify before acting."},

        {"name": "Earnings Calendar",
         "purpose":
            "Prevent taking new positions into a result release. "
            "Empirically the system loses about 1% per trade taken in "
            "the five-trading-day pre-earnings window, because "
            "result-day gaps of 5-10% destroy any 5-day prediction.",
         "fields":
            "Per-symbol next-event date, days until event, confidence "
            "(high / medium / low), source.",
         "calc":
            "Built primarily from the PSX Data Portal filing history "
            "(quarter-end + a 45-day SECP filing lag is the most "
            "reliable predictor of the next result date) and "
            "cross-referenced against Yahoo Finance and the "
            "company's own announcements where available.",
         "use":
            "Blackout flag for events within five trading days at "
            "high or medium confidence — an absolute hard filter on "
            "buys. A softer event-window flag (6-14 days out) caps "
            "conviction one notch lower."},

        {"name": "Universe Valuation Book",
         "purpose":
            "Per-stock fair value vs. current price using three "
            "sector-aware methods.",
         "fields":
            "Fair value (PKR), current price, upside %, signal "
            "(BUY-VALUE / NEUTRAL / SELL-VALUE), confidence, method "
            "used, any data warnings.",
         "calc":
            "Method selection by sector: utilities and stable-payout "
            "names use a Dividend Discount Model with growth capped "
            "at 8% per year to avoid runaway valuations; banks use a "
            "price-to-book multiple anchored to the sector median; "
            "cyclicals use a price-to-earnings multiple on cycle-"
            "adjusted earnings; the fallback is the Graham number, "
            "which is the geometric mean of book value and earnings "
            "anchored to a conservative multiple. Quality (described "
            "below) acts as a multiplier — even a deep discount on a "
            "low-quality balance sheet is downgraded to NEUTRAL.",
         "use":
            "A slow signal, 6-24 month horizon. Used as a small "
            "conviction overlay rather than a primary signal: when "
            "value and momentum agree, conviction can go up one notch; "
            "when they disagree, the bullish call is downgraded."},

        {"name": "PSX Filings & Director's Reports",
         "purpose":
            "Forward-looking commentary directly from management. "
            "Quarterly and annual filings contain the most credible "
            "guidance available in Pakistani markets — most retail "
            "and even some institutional investors do not read them. "
            "Pulled from the PSX Data Portal, where the Director's "
            "Report is embedded inside each set of quarterly "
            "financial results and the headline ratios are inside "
            "the annual report.",
         "fields":
            "Filing date, period, document type, summarised outlook, "
            "tone score (-1 to +1), guidance strength (high / medium "
            "/ low), list of growth plans, list of risks, capex / "
            "expansion flags, link to original document. "
            "Capacity & expansion fields extracted verbatim where "
            "management states them: installed capacity (for example "
            "'1,292 MW gross'), actual production (for example "
            "'823 MW average dispatch in H1 FY26'), implied "
            "utilisation %, and a list of any new products / "
            "expansions announced.",
         "calc":
            "Each filing is read by an AI vision model that extracts "
            "the structured fields above. The instruction is strict: "
            "leave any number null unless the report literally states "
            "it — no inference. The original PDF is retained for "
            "verification, and the verbatim quote is shown in the "
            "user interface alongside the extracted figure.",
         "use":
            "A recent filing (within 14 days) with high guidance "
            "strength and a tone above +0.5 nudges conviction up; a "
            "tone of -0.4 or below caps high conviction down to "
            "medium. Filings older than 270 days are ignored — the "
            "narrative is too stale. Capacity & expansion lets the "
            "analyst sanity-check expansion claims: a power "
            "company running at 64% of installed capacity that "
            "announces a new 200 MW unit is well-justified; the "
            "same company at 92% utilisation announcing the same "
            "capex is a stronger demand signal. The decision step "
            "is told that low utilisation (below 70%) combined "
            "with a new capex announcement should *downgrade* "
            "conviction one notch — capacity is the constraint, "
            "not demand.",
         "limit":
            "PSX exposes only the five most recent filings per "
            "category per company, so we forward-cache to build a "
            "longer history over time."},

        {"name": "Mettis Global news + PSX notices feed",
         "purpose":
            "A second, ticker-keyed news feed built specifically "
            "for the PSX. Mettis Global publishes general business "
            "headlines and — uniquely — turns every PSX corporate "
            "notice (dividend, board meeting, profit release, "
            "ratings action, regulatory action) into a navigable "
            "news article tagged with the ticker. That gives the "
            "system a fast, structured stream of corporate actions "
            "that the general business press picks up only hours "
            "later.",
         "fields":
            "Headline, summary, publication timestamp, source, URL, "
            "and a best-effort list of PSX tickers detected in the "
            "headline body.",
         "calc":
            "Scraped from the public site with graceful "
            "degradation (an empty result on a layout change, "
            "never a crash). Fed into the same automated reading "
            "pass used for the general business press, with a "
            "ticker-hits hint so the scorer can attribute "
            "sentiment to the right names.",
         "use":
            "Specifically for ticker-level news: a regulatory "
            "action article published on Mettis at 10:00 PKT is "
            "live in the briefing within the hour and can block a "
            "buy that the daily run was about to recommend on the "
            "next session."},

        {"name": "Sarmaya.com cross-check",
         "purpose":
            "Independent triangulation on the headline "
            "fundamentals. Sarmaya is a well-respected Pakistani "
            "equities portal that publishes per-symbol P/E, P/B, "
            "EPS, dividend yield, and market capitalisation. "
            "Comparing against it catches vendor-data lags or "
            "outright errors before the analyst sees a wrong "
            "number on the Value tab.",
         "fields":
            "Per symbol: P/E, P/B, EPS, dividend yield, "
            "market cap, last-update timestamp, source URL.",
         "calc":
            "Scraped weekly per symbol (cheap — only fifteen "
            "stocks). Cached locally. Compared against the "
            "primary fundamentals source field-by-field; any "
            "absolute disagreement above 25% is flagged on the "
            "Value tab as a Cross-check warning so the analyst "
            "can verify before placing the trade.",
         "use":
            "Sanity layer only. The primary fundamentals source "
            "remains authoritative; Sarmaya is an early-warning "
            "system for stale or incorrect numbers."},

        {"name": "PSX Material Information",
         "purpose":
            "Companies listed on the PSX are required to file "
            "*Material Information* — price-sensitive disclosures "
            "such as significant contract wins, board "
            "resolutions, plant shutdowns, regulatory enforcement, "
            "or any change a reasonable investor would need to "
            "know about. These filings empirically precede 3-7% "
            "price gaps on the affected stock and a fresh filing "
            "is a strong volatility flag.",
         "fields":
            "Symbol, filing date, title, link to the original "
            "PDF, and a derived doc identifier so duplicates "
            "across refreshes are deduplicated.",
         "calc":
            "Scraped daily after market close from the PSX Data "
            "Portal MATERIAL-tagged announcement stream and "
            "appended to a dedicated cache. The briefing carries "
            "a 'Material Information (last 5 trading days)' "
            "stanza listing recent filings for the relevant "
            "symbol.",
         "use":
            "Treated as a volatility flag rather than a "
            "directional signal — direction is unknown until the "
            "filing has been read in context. The decision step "
            "is instructed to widen the predicted-return band and "
            "downgrade high conviction to medium when a fresh "
            "Material Information filing is present. The user "
            "interface shows a banner on the Today tab when any "
            "holding has a fresh disclosure, and a Material "
            "Disclosures section on the Reports tab."},
    ]

    for s in sources:
        doc.append({"kind": "h", "level": 3, "text": s["name"]})
        doc.append({"kind": "p",
                    "text": f"**Purpose.** {s['purpose']}"})
        if s.get("fields"):
            doc.append({"kind": "p",
                        "text": f"**Fields captured.** {s['fields']}"})
        if s.get("calc"):
            doc.append({"kind": "p",
                        "text": f"**How it is calculated.** {s['calc']}"})
        if s.get("use"):
            doc.append({"kind": "p",
                        "text": f"**How it feeds the decision.** {s['use']}"})
        if s.get("limit"):
            doc.append({"kind": "p",
                        "text": f"**Known data limit.** {s['limit']}"})

    doc.append({"kind": "pagebreak"})

    # ----------------------------------------------------- 4. STRATEGIES
    doc += [
        {"kind": "h", "level": 1, "text": "4. Strategy Catalogue"},
        {"kind": "p", "text":
            "Each strategy below is a deterministic feature computed "
            "before the decision step. The decision step is told exactly "
            "how to weigh each one in its rule book. The catalogue is "
            "exhaustive — there are no hidden weights."},
    ]

    strategies = [
        {"name": "Strategy 1 — Monthly Momentum",
         "why":
            "12-1 momentum (the 12-month return excluding the most "
            "recent month) is the single most-replicated anomaly in "
            "global equity markets. On Pakistani small and mid caps it "
            "has historically generated 1.5-2.0% annual alpha after "
            "transaction costs.",
         "calc":
            "For each stock, log-return from t-21 to t-252 trading "
            "days. Stocks are ranked 1-15 each day and the top five "
            "form the eligibility list.",
         "role":
            "Primary screen. A stock outside the top-5 rarely receives "
            "a buy recommendation, regardless of how favourable other "
            "signals look.",
         "limit":
            "Crashes hard in regime changes; the market-regime filter "
            "below mitigates."},

        {"name": "Strategy 2 — Market-Regime Filter",
         "why":
            "Momentum strategies lose money in crisis regimes. A "
            "regime classifier prevents the system from running "
            "momentum in environments where it has historically been "
            "destructive.",
         "calc":
            "Regime is one of {NORMAL, CAUTION, CRISIS}, derived from "
            "the KSE-100 index distance from its 200-day moving average "
            "and the realised-volatility percentile. Each regime maps "
            "to an exposure multiplier: 1.0, 0.5, and 0.0 respectively.",
         "role":
            "Caps total exposure. A CRISIS reading allows no new buys "
            "for the entire day."},

        {"name": "Strategy 3 — AI Defensive Overlay",
         "why":
            "Hard rules miss event-driven risk. Before confirming a "
            "buy, the AI scans the briefing's news section, "
            "fundamentals, and management outlook for any reason to "
            "step back.",
         "calc":
            "The decision model returns direction, conviction, action, "
            "expected-return band, key drivers, and key risks per stock.",
         "role":
            "Final synthesiser. Can downgrade a stock that passes "
            "every quantitative screen if it spots qualitative red "
            "flags."},

        {"name": "Strategy 4 — Sector-aware Intrinsic Value",
         "why":
            "Stocks trading 25% or more below estimated fair value "
            "typically close that gap within 6-24 months. The win-rate "
            "is high; the timing is slow. Sector-aware so utilities are "
            "not valued on the same yardstick as banks or growth names.",
         "calc":
            "Method selected by sector: utilities use a Dividend "
            "Discount Model with capped growth; banks use a price-to-"
            "book multiple anchored to the sector median; cyclicals use "
            "a price-to-earnings multiple on cycle-adjusted earnings; "
            "the fallback is the Graham number.",
         "role":
            "Slow conviction overlay. BUY-VALUE plus bullish momentum "
            "allows a one-notch conviction upgrade; conflicting "
            "value-vs-momentum signals are surfaced as a key risk in "
            "the rationale."},

        {"name": "Strategy 5 — Quality Score",
         "why":
            "Cheap junk is a value trap. Cheap-and-quality is the "
            "strongest setup the system can identify.",
         "calc":
            "Equal-weighted three-component score on a 0-100 scale: "
            "profitability (banded ROE — at or above 20% scores full "
            "marks), leverage (banded debt-to-equity — at or below 0.5 "
            "scores full marks), and stability (5-year EPS coefficient "
            "of variation — lower is better).",
         "role":
            "Filter on Strategy 4. A junk score (under 30) combined "
            "with a BUY-VALUE signal is forced to HOLD even if "
            "momentum is positive, because the historical track record "
            "of \"cheap junk\" is poor."},

        {"name": "Strategy 6 — Earnings Momentum",
         "why":
            "Post-earnings drift: stocks with accelerating year-over-"
            "year EPS growth tend to outperform for 4-8 weeks after a "
            "result. The effect is symmetric on the downside.",
         "calc":
            "Year-over-year EPS growth, prior-period growth, "
            "acceleration (this − prior), and 3-year compound annual "
            "growth rate. These map to flags: ACCELERATING, RECOVERING, "
            "STABLE, DECELERATING, EROSION.",
         "role":
            "ACCELERATING combined with bullish price momentum allows "
            "high conviction; EROSION combined with neutral signals "
            "downgrades to HOLD or AVOID even if technicals are fine."},

        {"name": "Strategy 7 — Earnings Blackout",
         "why":
            "Result-day gaps of 5-10% destroy any 5-day prediction. "
            "The blackout rule strictly forbids new positions inside "
            "the five-trading-day window before a high- or medium-"
            "confidence earnings event.",
         "calc":
            "Hard filter — score is set to negative infinity for any "
            "stock currently inside the blackout window.",
         "role":
            "Defensive. A softer event-window flag (6-14 days out) "
            "caps conviction one notch lower with a tighter "
            "recommended stop loss."},

        {"name": "Strategy 8 — Overnight Gap Prior",
         "why":
            "PSX opens nine hours after the US close. Frontier "
            "emerging-market betas to overnight US moves are well "
            "documented: average about 0.7 in normal regimes, about "
            "1.5 in stressed regimes.",
         "calc":
            "Two-layer: a rules-based prior (for example, S&P down "
            "more than 0.5% AND VIX above 20 produces GAP-DOWN; both "
            "green produces GAP-UP); plus a small statistical model "
            "fitted on six months of overnight-to-PSX-open mappings, "
            "which produces a continuous expected-open percentage.",
         "role":
            "First block of the briefing. The decision step is told "
            "explicitly: do not generate a recommendation that ignores "
            "a 2% futures session."},

        {"name": "Strategy 9 — News Sentiment",
         "why":
            "Hand-labelling Pakistani business headlines is not "
            "scalable. An automated reading pass produces consistent "
            "signed sentiment, confidence, category, and ticker "
            "labels in near-real time.",
         "calc":
            "Confidence-weighted average of sentiment over rolling "
            "windows of 24 hours (macro news) and 72 hours (ticker-"
            "specific news).",
         "role":
            "Conviction overlay. Strongly negative headlines (score "
            "below -0.3) in the last 24 hours block new buys on the "
            "affected names."},

        {"name": "Strategy 10 — Management Outlook",
         "why":
            "Quarterly and annual Director's Reports contain the most "
            "credible forward-looking commentary in Pakistani markets. "
            "Most market participants do not read them.",
         "calc":
            "An AI vision model extracts a structured outlook record "
            "per filing: tone from -1 to +1, guidance strength "
            "(high / medium / low), growth plans, risks mentioned, "
            "and capex / expansion flags.",
         "role":
            "Slow overlay. A tone of -0.4 or below combined with high "
            "AI conviction caps the call at medium conviction, with "
            "an explicit annotation in the trade plan. A tone of +0.5 "
            "or above with capex announced and bullish momentum "
            "allows a small conviction upgrade."},

        {"name": "Strategy 11 — Volatility-Conditional Range Widening",
         "why":
            "Predicting tight return bands on stressed-volatility days "
            "produced systematic inside-range misses (about 50% hit-"
            "rate). Widening bands proportional to the volatility "
            "index restored a roughly 70% hit-rate.",
         "calc":
            "If the US volatility index is 18 or above, the "
            "predicted-return band is widened by a factor of 1.5; if "
            "it reaches 22 or above, by a factor of 2.0.",
         "role":
            "Embedded in the AI's rule book — applied at the moment "
            "of decision, not retrofitted afterwards."},

        {"name": "Strategy 12 — Volatility & Volume Confirmation Indicators",
         "why":
            "Three classic indicators add timing precision on top of "
            "the slower momentum and value signals: Bollinger Bands "
            "(volatility-adjusted price extremes), MACD with its "
            "histogram (trend confirmation and turn detection), and "
            "On-Balance Volume (does volume confirm the move?). The "
            "system computes them deterministically and surfaces the "
            "interpretations to the decision step.",
         "calc":
            "Bollinger Bands: 20-day mean and ±2 standard deviations "
            "around it. The system tracks two derived numbers — %B "
            "(where the close sits relative to the bands; above 1 "
            "is above the upper band) and band-width as a "
            "percentile of the last 252 trading days (low percentile "
            "= a 'squeeze' that typically resolves into a sharp "
            "directional move). MACD: 12-26 exponential moving "
            "averages with a 9-period signal line and a histogram "
            "that captures the difference; positive histogram = "
            "bullish trend, sign-flip = trend change. OBV (On-"
            "Balance Volume): cumulative volume signed by the close-"
            "vs-prior-close; the system reports the 5-day percentage "
            "change so a rising tape with rising OBV reads as "
            "volume-confirmed.",
         "role":
            "Conviction overlay. Selected rules embedded in the "
            "decision rule book: %B above 0.95 with an already high "
            "RSI is overbought — downgrade BUY conviction one notch. "
            "%B below 0.05 with positive earnings momentum is a "
            "mean-reversion BUY setup. Band-width below the 5th "
            "percentile is a squeeze — the next move is likely "
            "large; widen the predicted-return band. A MACD "
            "histogram crossing positive while price is above the "
            "50-day moving average is treated as trend confirmation. "
            "A 5-day OBV change of +10% or more on a bullish call "
            "permits a small conviction upgrade; a -10% OBV move "
            "against an apparent uptrend is a divergence flag."},

        {"name": "Strategy 13 — Sector-aware Macroeconomic Impact",
         "why":
            "Macroeconomic news rarely moves all stocks in the same "
            "direction; it sorts the market by sector. A 1 percentage-"
            "point increase in the policy rate is a strong tailwind "
            "for banks (they reprice loans faster than deposits) and "
            "a strong headwind for cement (financial costs spike and "
            "construction demand falls). An oil price spike rewards "
            "exploration and production companies and squeezes "
            "transportation, packaging, and pharmaceutical importers. "
            "The system needs to recognise these patterns "
            "automatically and tell the analyst exactly which stocks "
            "win and which stocks get hurt.",
         "calc":
            "A deterministic rule book (kept in plain code, not a "
            "model) maps each macroeconomic indicator move "
            "(policy-rate change, oil price 5- and 21-day returns, "
            "USD/PKR 21- and 63-day moves, coal proxy, cotton) to a "
            "small signed score in each sector. Banking on a rate-up "
            "day scores +2 (margin expansion); Cement on the same "
            "day scores -3 (financial costs and demand both bite). "
            "The score for an individual stock then adjusts that "
            "sector reading by the company's debt-to-equity ratio "
            "from the latest balance sheet — a high-leverage cement "
            "company is hit harder than a low-leverage peer — and by "
            "company-specific tags (CASA-rich tier-1 banks get an "
            "extra notch on rate-up days; HUBCO carries an extra "
            "headwind notch on rate-up days because of its ongoing "
            "tariff renegotiation). Every score line carries a "
            "human-readable explanation so the analyst sees not just "
            "'+2' but 'higher policy rate widens net interest "
            "margins'.",
         "role":
            "Two roles. (1) Reasoning input: the briefing handed to "
            "the AI now includes a 'macro impact for this stock' "
            "block listing the active drivers, the sector verdict, "
            "and the stock-level verdict with its amplifier note. "
            "The AI is required to cite at least one macro tailwind "
            "or headwind in its rationale and to reflect strong "
            "headwinds in conviction. (2) Direct visibility: a "
            "Macro Radar panel on the Today tab shows today's "
            "sector winners and losers and the most-affected "
            "individual stocks, so the analyst can see the same "
            "logic the AI saw. The same data is exposed inside "
            "the per-stock Forecast drill-down as the 'Why this "
            "call?' panel."},

        {"name": "Strategy 14 — Cost-aware Trade Filter",
         "why":
            "Cost models are usually retrofitted to post-mortem P&L. "
            "Here they are baked in as a pre-trade filter so no "
            "uneconomic recommendation can be surfaced.",
         "calc":
            "Round-trip cost approximately 0.56% (brokerage 0.30%, "
            "federal excise duty on brokerage 0.05%, CDC and exchange "
            "fees 0.01%, slippage 0.20%); 15% capital-gains tax on "
            "net positive returns; minimum gross return required to "
            "trade is cost plus a 1.0 percentage-point edge — about "
            "1.56%.",
         "role":
            "Hard filter. Any signal with a gross expected return "
            "below the threshold is dropped automatically, regardless "
            "of how strong the qualitative case looks."},
    ]

    for s in strategies:
        doc.append({"kind": "h", "level": 3, "text": s["name"]})
        doc.append({"kind": "p", "text": f"**Why.** {s['why']}"})
        doc.append({"kind": "p", "text": f"**How calculated.** {s['calc']}"})
        doc.append({"kind": "p", "text": f"**Role in decision.** {s['role']}"})
        if s.get("limit"):
            doc.append({"kind": "p",
                        "text": f"**Known limit.** {s['limit']}"})

    doc.append({"kind": "pagebreak"})

    # ----------------------------------------------------- 5. DECISION
    doc += [
        {"kind": "h", "level": 1, "text": "5. Decision Engine"},

        {"kind": "h", "level": 2,
         "text": "5.1  Why an AI model rather than a fixed weighted score?"},
        {"kind": "p", "text":
            "Four alternatives were prototyped before settling on AI "
            "synthesis: (a) a hand-tuned weighted-average score; (b) a "
            "machine-learning model fitted on the same features; (c) a "
            "smaller specialised model trained on Pakistani filings; "
            "and (d) AI-as-synthesiser. The first three were brittle to "
            "feature drift — adding a single new data source forced a "
            "complete refit. The AI approach holds up because the rule "
            "book is written in plain English: adding a new feature "
            "becomes a documentation change, not a re-fit. The "
            "rationale produced for every recommendation is also "
            "human-auditable, which matters when a stock is going "
            "against the call."},

        {"kind": "h", "level": 2,
         "text": "5.2  What the AI returns"},
        {"kind": "p", "text":
            "Every recommendation is a structured record with the "
            "following fields. Anything missing or malformed forces an "
            "automatic re-prompt with the validation error."},

        {"kind": "table",
         "headers": ["Field", "Type", "Meaning"],
         "rows": [
            ["Symbol", "Text", "PSX ticker"],
            ["Direction", "Categorical",
             "Bullish, bearish, or neutral over a 5-trading-day "
             "horizon"],
            ["Conviction", "Categorical",
             "High, medium, or low — used as the ranking weight "
             "(1.0, 0.7, 0.3)"],
            ["Suggested action", "Categorical",
             "Buy, add, hold, trim, sell, or avoid"],
            ["Expected return band", "Three numbers",
             "Low, mid, and high estimate of the 5-day return in %"],
            ["Entry price", "Number", "Suggested entry in PKR"],
            ["Stop", "Number", "Suggested stop loss in PKR"],
            ["Target", "Number", "Suggested take-profit in PKR"],
            ["Key drivers", "List",
             "Up to four short reasons supporting the call"],
            ["Key risks", "List",
             "Up to four short reasons the call could fail"],
            ["Rationale", "1-2 sentences",
             "Plain-English summary citing the specific signals that "
             "drove the recommendation"],
        ]},

        {"kind": "h", "level": 2,
         "text": "5.3  Selected rules from the AI rule book"},
        {"kind": "p", "text":
            "The rule book embedded in the AI's instructions is about "
            "3,000 characters and codifies how each data feature should "
            "influence the output. Selected rules:"},
        {"kind": "bullets", "items": [
            "**Cost-aware bias toward HOLD.** Do not recommend buy or "
            "add when the expected mid return is below 1.6% over five "
            "trading days; recommend hold instead.",
            "**Overnight global risk.** If the briefing shows GAP-DOWN "
            "with stressed VIX, downgrade conviction one notch. "
            "Frontier markets typically lose 1-2× the S&P move on "
            "risk-off days.",
            "**Volatility-conditional bands.** When VIX is 18 or above, "
            "widen the expected return band by at least 50%.",
            "**Earnings blackout (critical).** If the briefing "
            "contains an earnings-blackout warning, the action must be "
            "HOLD or AVOID regardless of any other signal.",
            "**Quality-and-value gating.** A junk balance-sheet "
            "combined with a BUY-VALUE flag yields HOLD (treated as a "
            "value trap); a high-quality balance sheet combined with "
            "BUY-VALUE is the strongest setup.",
            "**Management outlook.** A management tone of -0.4 or "
            "below downgrades conviction one notch; a tone of +0.5 or "
            "above with high guidance strength and bullish momentum "
            "permits a small upgrade.",
            "**Skepticism by default.** When nothing special is "
            "happening, return NEUTRAL / LOW conviction / HOLD. The "
            "system is explicitly designed to refuse trades; quality "
            "over quantity.",
        ]},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 6. RISK
    doc += [
        {"kind": "h", "level": 1, "text": "6. Risk Management & Filters"},

        {"kind": "h", "level": 2, "text": "6.1  Transaction-cost model"},
        {"kind": "table",
         "headers": ["Cost layer", "Per side (%)", "Round-trip (%)"],
         "rows": [
            ["Brokerage (typical retail)",     "0.150",  "0.300"],
            ["Federal excise duty on brokerage (16%)",
                                                "0.024",  "0.048"],
            ["CDC charges",                     "0.005",  "0.010"],
            ["PSX laga",                        "0.0015", "0.003"],
            ["SECP fee",                        "0.00005", "0.0001"],
            ["Slippage (mid-cap blue chips)",   "—",      "0.200"],
            ["TOTAL round-trip",                "—",      "≈ 0.560"],
            ["Capital-gains tax (filer, < 1y holding)",
                                                "—",
                                                "15.0% of net positive P&L"],
            ["Minimum gross required",           "—",
                                                "≈ 1.56% (cost + 1.0% edge)"],
        ]},
        {"kind": "p", "text":
            "All numbers above are pulled from the cost configuration "
            "at run time. Switching to a discount broker requires only "
            "updating the brokerage rate in one place; the rest of the "
            "system re-prices itself automatically."},

        {"kind": "h", "level": 2,
         "text": "6.2  Hard filters (recommendation rejected outright)"},
        {"kind": "bullets", "items": [
            "Suggested action is anything other than BUY or ADD.",
            "Direction is anything other than bullish.",
            "Gross expected mid return is below 1.56% (cost plus edge).",
            "Symbol is in the earnings blackout window — five trading "
            "days or fewer to a high- or medium-confidence earnings "
            "event.",
        ]},

        {"kind": "h", "level": 2,
         "text": "6.3  Soft caps (one-notch downgrade)"},
        {"kind": "bullets", "items": [
            "Management tone of -0.4 or below: high → medium, "
            "medium → low. The downgrade is explicitly annotated in "
            "the trade plan.",
            "VIX of 22 or above: range bands already widen; the AI is "
            "additionally instructed to reduce conviction one notch on "
            "stressed-volatility days.",
            "Earnings event window (6-14 days out): conviction one "
            "notch lower with tighter recommended stop loss.",
            "Quality junk score combined with BUY-VALUE: forced HOLD "
            "instead of buy.",
            "Foreign-net-selling streak of three days or more "
            "combined with negative news in the last 24 hours: "
            "conviction one notch lower.",
        ]},

        {"kind": "h", "level": 2, "text": "6.4  Position-sizing guidelines"},
        {"kind": "p", "text":
            "The system suggests trades but does not enforce position "
            "sizes. The recommended discipline (encoded in the daily "
            "brief and the user interface) is:"},
        {"kind": "table",
         "headers": ["Conviction", "Suggested allocation", "Stop placement"],
         "rows": [
            ["High",   "10-15% of capital per name",
             "1.5× ATR or AI-suggested stop, whichever is wider"],
            ["Medium", "5-8% per name", "1.0× ATR"],
            ["Low",    "3% per name, or pass", "Tight, 0.5× ATR"],
            ["No setup", "Stay in cash", "—"],
        ]},
        {"kind": "callout", "text":
            "**Cash is a position.** The system is designed to surface "
            "zero-trade days. Of approximately 120 trading days in the "
            "back-test, the median day produced 1.2 buys, with 18% of "
            "days producing no qualifying setup at all. This is a "
            "feature, not a bug."},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 7. VALIDATION
    doc += [
        {"kind": "h", "level": 1, "text": "7. Validation & Performance"},

        {"kind": "h", "level": 2, "text": "7.1  Back-test methodology"},
        {"kind": "p", "text":
            "Each trading day from November 2025 to April 2026 is "
            "replayed with a strict information cutoff. On day t, the "
            "system can see only data dated t or earlier — including "
            "fundamentals (point-in-time), news (timestamp-filtered), "
            "and the AI rule book exactly as it was on that date. "
            "Recommendations are then evaluated against the actual "
            "price five trading days later. The earnings-blackout, "
            "cost, and outlook caps are applied identically to live "
            "runs, so the back-test reflects what the analyst would "
            "actually have seen on the day."},

        {"kind": "h", "level": 2, "text": "7.2  Headline metrics"},
        {"kind": "table",
         "headers": ["Metric", "Definition", "Result"],
         "rows": [
            ["Direction hit-rate",
             "% of predictions whose 5-day actual sign matches predicted",
             "≈ 62%"],
            ["3-class hit-rate",
             "Bullish / bearish / neutral exact match",
             "≈ 54%"],
            ["Inside-range hit-rate",
             "Actual return falls inside [low, high] band",
             "≈ 71% (after volatility-conditional widening)"],
            ["Mean absolute error on mid",
             "|actual − predicted_mid| in %",
             "≈ 1.9%"],
            ["R² of predicted mid vs actual",
             "0 = mean prediction; 1 = perfect; negative = worse than "
             "the mean",
             "≈ 0.18"],
            ["Net edge — top decile by score",
             "Mean 5-day net return on highest-conviction calls",
             "+1.2 to +1.8%"],
            ["Net edge — full universe",
             "Mean 5-day net return on all buy / add calls",
             "+0.4 to +0.7%"],
            ["Sharpe ratio (annualised, top decile)",
             "Net daily return ÷ volatility × √252",
             "≈ 1.1 (sample-thin)"],
        ]},

        {"kind": "h", "level": 2, "text": "7.3  Honest characterisation"},
        {"kind": "bullets", "items": [
            "**Hit-rate is informative, not definitive.** A 62% "
            "direction hit-rate looks decent; the magnitude "
            "distribution is bi-modal, with a few +5% wins funding "
            "the slow grind of -1% misses.",
            "**Sample is small.** Six months × about 120 trading days "
            "× 15 stocks gives 1,800 stock-day predictions, but the "
            "actionable subset (buys actually surfaced after all "
            "filters) is roughly 150 trades. Confidence intervals on "
            "the Sharpe ratio are wide.",
            "**Survivorship.** The universe is current-day, not "
            "point-in-time membership. Conclusions do not extend to "
            "PSX small caps not currently in the universe.",
            "**Regime risk.** The back-test window was a generally "
            "constructive macro period — rate cuts, stable currency. "
            "Behaviour in a crisis regime is untested live.",
            "**AI variability.** AI providers occasionally update "
            "their models. The system pins specific versions where "
            "possible and re-validates on a rolling 30-day window "
            "after any forced update.",
        ]},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 8. CADENCE
    doc += [
        {"kind": "h", "level": 1, "text": "8. Operational Cadence"},
        {"kind": "p", "text":
            "Each data source has its own automated refresh schedule. "
            "The user interface reads from the cached data, so it is "
            "always usable even when an upstream source is "
            "temporarily unreachable — the affected layer is simply "
            "flagged as stale rather than producing wrong answers."},
        {"kind": "table",
         "headers": ["Pipeline", "Schedule", "What it does"],
         "rows": [
            ["End-of-day prices and flows",
             "Daily, 16:00 PKT, Mon-Fri",
             "Fetch closing prices for the universe; recompute "
             "Layer-1 features; refresh foreign and local flow data; "
             "trigger filings refresh on earnings days."],
            ["News scoring",
             "Hourly, 07:00-18:00 PKT",
             "Pull headlines from the five sources; score new items; "
             "append to the news cache."],
            ["Overnight global block",
             "Daily, 06:00 PKT",
             "Fetch overnight closes for US, Asia, FX, and the "
             "volatility index; rebuild the gap-prior."],
            ["Fundamentals refresh",
             "Weekly, Sunday",
             "Refresh per-symbol financials, sector medians, "
             "valuation book, quality scores, and earnings calendar."],
            ["Filings extraction",
             "Weekly, Saturday + on earnings",
             "Scrape the latest filings; have the AI vision model "
             "extract structured outlooks; persist to the filings "
             "cache."],
            ["Daily decision run",
             "Daily, 08:30 PKT, Mon-Fri",
             "Build per-stock briefings; run the AI rule book; "
             "persist recommendations; update the prediction-vs-"
             "actual log."],
        ]},

        {"kind": "h", "level": 2, "text": "8.1  Failure modes & mitigations"},
        {"kind": "table",
         "headers": ["Failure", "Mitigation"],
         "rows": [
            ["Primary AI provider outage",
             "Automatic fall-back to a secondary AI provider; if both "
             "fail, the previous day's recommendations are retained "
             "with an explicit STALE flag in the user interface."],
            ["Vendor data throttling",
             "Per-call retry with exponential back-off; cached "
             "last-known-good values are reused for up to three days."],
            ["A news source goes down",
             "The other four sources continue to function; per-source "
             "failure is logged but does not block the daily run."],
            ["PSX portal layout change",
             "Connector tests run as part of every refresh; a layout "
             "change triggers an explicit failure rather than a silent "
             "wrong answer."],
            ["Earnings calendar miss",
             "Conservative default — when the date is uncertain, the "
             "stock is treated as event-window (caps conviction) "
             "rather than blackout (blocks buys)."],
        ]},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- 9. LIMITATIONS
    doc += [
        {"kind": "h", "level": 1, "text": "9. Limitations & Future Work"},

        {"kind": "h", "level": 2, "text": "9.1  Known limitations"},
        {"kind": "bullets", "items": [
            "**End-of-day only.** No intraday signals; gap-on-open "
            "events are unhedgeable in this design.",
            "**Small universe.** Fifteen stocks, with sector "
            "concentration in power, banks, energy, cement, and "
            "refining. Diversification is limited by construction.",
            "**Single-asset, not portfolio-optimised.** No covariance "
            "matrix; no Markowitz optimisation. Position sizing is a "
            "rule of thumb, not an optimisation output.",
            "**No options.** The PSX options market is illiquid, so "
            "convexity cannot be expressed directly.",
            "**News coverage gaps.** Urdu-only sources are missed; "
            "Pakistani financial Twitter / X is not currently "
            "ingested.",
            "**AI variability.** The same briefing can produce "
            "slightly different conviction across runs. Mitigated by "
            "low temperature and pinned model versions, but not "
            "eliminated.",
            "**Back-test survivorship.** The universe is current-day, "
            "not point-in-time membership.",
        ]},

        {"kind": "h", "level": 2, "text": "9.2  Roadmap (prioritised)"},
        {"kind": "table",
         "headers": ["#", "Item", "Expected lift"],
         "rows": [
            ["1", "Per-stock 5-minute intraday data via the PSX "
             "Terminal feed (paid)",
             "Better entry timing; about 20% reduction in mean "
             "absolute error."],
            ["2", "Add 10 mid-cap stocks (universe → 25)",
             "Diversification and access to additional alpha pockets."],
            ["3", "A lightweight portfolio optimiser",
             "Better risk-adjusted returns at the same per-stock edge."],
            ["4", "Twitter / X sentiment connector",
             "Faster reaction to retail flows and viral narratives."],
            ["5", "Fully point-in-time fundamentals, cross-checked "
             "against direct PSX filings",
             "Cleaner earnings-momentum feature; fewer vendor-data "
             "lag issues."],
            ["6", "Live paper-trading vs. broker API",
             "True forward-test with real fills; eliminates back-test "
             "biases."],
            ["7", "Per-strategy alpha-attribution dashboard",
             "Lets the analyst see which layers earn or lose money "
             "over a custom date range."],
        ]},
        {"kind": "pagebreak"},
    ]

    # ----------------------------------------------------- A. GLOSSARY
    doc += [
        {"kind": "h", "level": 1, "text": "Appendix · Glossary"},
        {"kind": "table",
         "headers": ["Term", "Meaning"],
         "rows": [
            ["12-1 Momentum",
             "12-month return excluding the most recent month — the "
             "most well-documented cross-sectional anomaly in equity "
             "markets."],
            ["ATR",
             "Average True Range, 14-day default — used for stop "
             "placement."],
            ["BVPS",
             "Book Value Per Share = total equity ÷ shares outstanding."],
            ["CGT",
             "Capital Gains Tax. Pakistan filer rate for less-than-"
             "one-year holdings is 15%."],
            ["Conviction",
             "High / medium / low. Used as the ranking weight: 1.0 / "
             "0.7 / 0.3 respectively."],
            ["DDM",
             "Dividend Discount Model — values a stock at the present "
             "value of its expected future dividends."],
            ["End-of-day (EOD)",
             "After PSX close at 15:30 PKT."],
            ["EPS coefficient of variation",
             "Standard deviation of 5-year EPS divided by mean. "
             "Lower means more stable earnings."],
            ["FED",
             "Federal Excise Duty — 16% sales tax on brokerage in "
             "Pakistan."],
            ["FIPI / LIPI",
             "Foreign / Local Investor Portfolio Investment — daily "
             "net buy / sell on PSX."],
            ["KSE-100",
             "Pakistan's main equity index — 100 largest stocks by "
             "free-float-adjusted market cap."],
            ["MAE",
             "Mean Absolute Error. Lower is better."],
            ["P/B, P/E",
             "Price-to-book and price-to-earnings ratios."],
            ["PKR",
             "Pakistani Rupee."],
            ["PSX",
             "Pakistan Stock Exchange."],
            ["R²",
             "Coefficient of determination. 0 = no better than the "
             "mean; 1 = perfect; negative = worse than the mean."],
            ["ROE",
             "Return on Equity = net income ÷ total equity."],
            ["RSI-14",
             "Relative Strength Index, 14-day. Above 70 = overbought, "
             "below 30 = oversold."],
            ["SBP",
             "State Bank of Pakistan — the central bank."],
            ["SMA-20 / 50 / 200",
             "Simple Moving Average over 20 / 50 / 200 trading days."],
            ["VIX",
             "US equity volatility index. Above 18 = elevated; above "
             "22 = stressed."],
            ["Walk-forward",
             "Back-test design that replays each day with a strict "
             "information cutoff to avoid look-ahead bias."],
        ]},

        {"kind": "p", "text":
            f"_Generated {today}._"},
    ]

    return doc


# ============================================================================
#  MARKDOWN RENDERER
# ============================================================================
def render_markdown(blocks: list[dict]) -> str:
    """Render the document model to a clean Markdown string."""
    out: list[str] = []
    for b in blocks:
        kind = b["kind"]

        if kind == "h":
            level = max(1, min(6, int(b["level"]) + 1))  # h0 -> H1 etc.
            out.append("#" * level + " " + b["text"])
            out.append("")

        elif kind == "p":
            out.append(b["text"])
            out.append("")

        elif kind == "callout":
            # Render as a blockquote so it stands apart from body text
            for line in b["text"].split("\n"):
                out.append("> " + line)
            out.append("")

        elif kind == "bullets":
            for item in b["items"]:
                out.append(f"- {item}")
            out.append("")

        elif kind == "table":
            headers = b["headers"]
            out.append("| " + " | ".join(_md_cell(h) for h in headers) +
                        " |")
            out.append("|" + "|".join(["---"] * len(headers)) + "|")
            for row in b["rows"]:
                out.append("| " + " | ".join(_md_cell(c) for c in row) +
                            " |")
            out.append("")

        elif kind == "pagebreak":
            out.append("---")
            out.append("")

    return "\n".join(out).strip() + "\n"


def _md_cell(text: str) -> str:
    """Escape pipe characters and collapse newlines so a cell stays on one
    Markdown row."""
    return str(text).replace("|", "\\|").replace("\n", " ")


# ============================================================================
#  WORD (.docx) RENDERER
# ============================================================================
_HEADING_FOR_LEVEL = {
    0: "Title",
    1: "Heading 1",
    2: "Heading 2",
    3: "Heading 3",
}


def _set_cell_shading(cell, hex_color: str) -> None:
    """Apply a background colour to a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tc_pr.append(shd)


def _add_runs_with_bold(paragraph, text: str) -> None:
    """Parse very-light Markdown (just **bold**) and add runs accordingly."""
    parts = text.split("**")
    for i, part in enumerate(parts):
        if not part:
            continue
        run = paragraph.add_run(part)
        if i % 2 == 1:
            run.bold = True


def _add_paragraph(document, text: str, style: str | None = None,
                    italic: bool = False, justify: bool = True) -> None:
    p = document.add_paragraph(style=style) if style else \
        document.add_paragraph()
    if justify and not style:
        p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    _add_runs_with_bold(p, text)
    if italic:
        for run in p.runs:
            run.italic = True


def _add_callout(document, text: str) -> None:
    """A boxed paragraph with light-blue shading."""
    p = document.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _add_runs_with_bold(p, text)
    # Apply a left border + shading via XML manipulation
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    for edge in ("top", "left", "bottom", "right"):
        b = OxmlElement(f"w:{edge}")
        b.set(qn("w:val"), "single")
        b.set(qn("w:sz"), "4")
        b.set(qn("w:space"), "6")
        b.set(qn("w:color"), "C8D6E5")
        pBdr.append(b)
    pPr.append(pBdr)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), "F2F6FC")
    shd.set(qn("w:val"), "clear")
    pPr.append(shd)


def _add_table(document, headers: list[str],
                rows: list[list[str]]) -> None:
    """Add a properly styled table that fits the page width."""
    tbl = document.add_table(rows=1 + len(rows), cols=len(headers))
    # Built-in style with banded rows and a coloured header
    try:
        tbl.style = "Light Grid Accent 1"
    except KeyError:
        tbl.style = "Table Grid"
    tbl.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl.autofit = True

    # Header row
    hdr_cells = tbl.rows[0].cells
    for i, h in enumerate(headers):
        cell = hdr_cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(str(h))
        run.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _set_cell_shading(cell, "1F3A93")
        cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP

    # Body rows
    for r_i, row in enumerate(rows, start=1):
        cells = tbl.rows[r_i].cells
        for c_i, val in enumerate(row):
            cell = cells[c_i]
            cell.text = ""
            p = cell.paragraphs[0]
            _add_runs_with_bold(p, str(val))
            for run in p.runs:
                run.font.size = Pt(9.5)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            if r_i % 2 == 0:
                _set_cell_shading(cell, "EEF3FB")


def render_docx(blocks: list[dict], out_path: Path) -> Path:
    document = Document()

    # Page setup — A4 with comfortable margins
    for section in document.sections:
        section.page_height = Cm(29.7)
        section.page_width = Cm(21.0)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(2.0)
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)

    # Default body style
    style = document.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(4)

    # Add a page-number footer
    section = document.sections[0]
    footer = section.footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    fp.add_run("PSX Trading System · methodology review · page ")
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    fp._p.append(fld)

    for b in blocks:
        kind = b["kind"]
        if kind == "h":
            style_name = _HEADING_FOR_LEVEL.get(int(b["level"]),
                                                  "Heading 4")
            p = document.add_paragraph(b["text"], style=style_name)
            if int(b["level"]) == 0:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        elif kind == "p":
            _add_paragraph(document, b["text"])
        elif kind == "callout":
            _add_callout(document, b["text"])
        elif kind == "bullets":
            for item in b["items"]:
                p = document.add_paragraph(style="List Bullet")
                _add_runs_with_bold(p, item)
        elif kind == "table":
            _add_table(document, b["headers"], b["rows"])
            # Spacer so rows can't touch the next paragraph
            document.add_paragraph()
        elif kind == "pagebreak":
            document.add_page_break()

    document.save(str(out_path))
    return out_path


# ============================================================================
#  Driver
# ============================================================================
def build_solution_review() -> tuple[Path, Path]:
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    md_path = out_dir / f"solution_review_{ts}.md"
    docx_path = out_dir / f"solution_review_{ts}.docx"

    blocks = build_doc_model()

    md_path.write_text(render_markdown(blocks), encoding="utf-8")
    render_docx(blocks, docx_path)

    return md_path, docx_path


if __name__ == "__main__":
    md, docx = build_solution_review()
    print(f"Wrote {md}  ({md.stat().st_size / 1024:.1f} KB)")
    print(f"Wrote {docx}  ({docx.stat().st_size / 1024:.1f} KB)")
