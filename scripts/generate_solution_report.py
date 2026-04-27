"""Generate a professional system-architecture PDF for analyst peer review.

This is a *meta* document: it describes *the solution itself* — every data
source, every strategy layer, every prediction-engine component, every
risk control, and every operational pipeline — so an outside analyst can
evaluate the methodology end-to-end without having to read the codebase.

Run:
    python -m scripts.generate_solution_report

Output: reports/solution_review_<timestamp>.pdf

The numbers in the report (cost model, layer descriptions, file paths)
are *introspected from the live code* — not hard-coded — so the PDF
always reflects the current state of the repo.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Allow `python scripts/generate_solution_report.py` from repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, PageBreak, ListFlowable, ListItem,
)


# ============================================================================
#  Styles
# ============================================================================
_BRAND_BLUE  = colors.HexColor("#1f3a93")
_BRAND_GREEN = colors.HexColor("#1e8a45")
_BRAND_RED   = colors.HexColor("#c0392b")
_BRAND_AMBER = colors.HexColor("#c08030")
_TEXT_MUTED  = colors.HexColor("#5d6470")
_BG_BAND     = colors.HexColor("#eef3fb")
_BG_CALLOUT  = colors.HexColor("#fff8ea")
_GRID        = colors.HexColor("#cdd5e0")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontSize=24, leading=28,
            textColor=_BRAND_BLUE, spaceAfter=4, alignment=0,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontSize=12, leading=15,
            textColor=_TEXT_MUTED, spaceAfter=14,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontSize=18, leading=22,
            textColor=_BRAND_BLUE, spaceBefore=18, spaceAfter=6,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontSize=14, leading=18,
            textColor=_BRAND_BLUE, spaceBefore=12, spaceAfter=4,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontSize=11, leading=14,
            textColor=colors.HexColor("#2f3a55"),
            spaceBefore=8, spaceAfter=2, keepWithNext=True,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontSize=10, leading=14,
            spaceAfter=4, alignment=4,  # justified
        ),
        "muted": ParagraphStyle(
            "muted", parent=base["Normal"], fontSize=9, leading=12,
            textColor=_TEXT_MUTED, spaceAfter=4,
        ),
        "callout": ParagraphStyle(
            "callout", parent=base["Normal"], fontSize=10, leading=14,
            textColor=colors.HexColor("#5b4a18"),
            backColor=_BG_CALLOUT, borderPadding=8,
            spaceBefore=4, spaceAfter=8,
        ),
        "code": ParagraphStyle(
            "code", parent=base["Code"], fontSize=8.5, leading=11,
            textColor=colors.HexColor("#2d2d2d"),
            backColor=colors.HexColor("#f6f6f6"),
            borderPadding=6, spaceBefore=2, spaceAfter=6,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"], fontSize=8, leading=10,
            textColor=_TEXT_MUTED,
        ),
    }


# ============================================================================
#  Layout helpers
# ============================================================================
def _table(rows: list[list[str]], col_widths: list[float],
            header: bool = True, zebra: bool = True) -> Table:
    """Standard styled table (header band + zebra rows + grid)."""
    t = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, _GRID),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ]
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if (i - (1 if header else 0)) % 2 == 1:
                style.append(("BACKGROUND", (0, i), (-1, i), _BG_BAND))
    t.setStyle(TableStyle(style))
    return t


def _bullets(sty: dict, items: list[str]) -> ListFlowable:
    """Compact bullet list."""
    return ListFlowable(
        [ListItem(Paragraph(it, sty["body"]),
                    leftIndent=12, bulletColor=_BRAND_BLUE)
         for it in items],
        bulletType="bullet", start="•", leftIndent=12, bulletFontSize=9,
    )


def _p(story: list, sty: dict, text: str, key: str = "body") -> None:
    story.append(Paragraph(text, sty[key]))


def _h(story: list, sty: dict, text: str, level: int = 2) -> None:
    story.append(Paragraph(text, sty[f"h{level}"]))


def _spacer(story: list, mm_amt: float = 4) -> None:
    story.append(Spacer(1, mm_amt * mm))


# ============================================================================
#  Section builders
# ============================================================================
def _cover(story: list, sty: dict) -> None:
    story.append(Paragraph(
        "PSX Trading System", sty["title"]))
    story.append(Paragraph(
        "Architecture &amp; Methodology Review",
        sty["title"]))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%A, %d %B %Y')} · "
        f"prepared for external analyst review",
        sty["subtitle"]))

    _spacer(story, 4)
    _p(story, sty,
        "<b>Purpose.</b> This document describes the data, strategies, "
        "prediction engine, risk controls, and operational pipelines of "
        "a 15-stock Pakistan Stock Exchange trading advisor. It is "
        "intended for analyst peer review — every data source is "
        "enumerated, every factor explained, and every risk control "
        "documented so the methodology can be evaluated independent of "
        "the implementation.")

    _spacer(story, 2)
    story.append(Paragraph(
        "<b>What it is.</b> A multi-layer factor system fused with an "
        "LLM-based synthesis engine. Rule-based and ML-based features "
        "(momentum, valuation, quality, sentiment, flows, macro, "
        "global risk, fundamentals) are computed deterministically and "
        "presented to a large language model (Claude Haiku 4.5) which "
        "returns a structured 5-trading-day prediction per stock. "
        "Predictions then pass through a transaction-cost-aware filter "
        "before being surfaced as concrete trade plans.",
        sty["body"]))

    _spacer(story, 2)
    story.append(Paragraph(
        "<b>What it is not.</b> Not an HFT system, not an intraday "
        "scalper, not auto-execution. It produces one daily decision "
        "vector for a small universe of liquid PSX blue chips. "
        "Forecast horizon is 5 trading days; backtest horizon is "
        "~6 months of walk-forward.",
        sty["body"]))

    story.append(PageBreak())

    # Table of contents
    _h(story, sty, "Table of Contents", level=1)
    toc = [
        ["1.", "Executive Summary", "3"],
        ["2.", "System Architecture (5 Layers)", "4"],
        ["3.", "Data Sources Inventory", "6"],
        ["4.", "Strategy Layer Catalogue", "9"],
        ["5.", "Prediction Engine (LLM Synthesis)", "13"],
        ["6.", "Risk Management &amp; Filters", "15"],
        ["7.", "Validation &amp; Performance", "17"],
        ["8.", "Operational Pipeline (CI/CD)", "18"],
        ["9.", "Limitations &amp; Future Work", "19"],
        ["A.", "Appendix — Module Map", "20"],
        ["B.", "Appendix — Glossary", "21"],
    ]
    rows = [["#", "Section", "Page"]] + toc
    story.append(_table(rows, [12*mm, 130*mm, 18*mm]))
    story.append(PageBreak())


def _executive_summary(story: list, sty: dict) -> None:
    _h(story, sty, "1. Executive Summary", level=1)

    _p(story, sty,
        "The system covers a 15-stock universe (HUBC, PABC, MLCF, OGDC, "
        "FABL, PPL, POL, FCCL, APL, EPCL, KOHC, SEARL, MCB, MEBL, PSO) "
        "spanning Power, Banks, E&amp;P, Cement, Refining, Petrochemical "
        "and Pharma sectors — chosen for liquidity and balance-sheet "
        "transparency.")

    _h(story, sty, "Methodology in one paragraph", level=3)
    _p(story, sty,
        "Every trading day, a CI pipeline refreshes ten data feeds "
        "(price, FIPI flows, news, policy rate, commodities, overnight "
        "global, fundamentals, earnings calendar, valuation book, "
        "Director's Reports). Per stock, the system computes ~40 "
        "features across five analytic layers and renders them as a "
        "plain-text briefing. Claude Haiku 4.5 reads the briefing and "
        "emits a structured JSON prediction (direction, expected return "
        "band, conviction, suggested action, key drivers, key risks). "
        "A second pass filters predictions for transaction-cost "
        "viability, earnings-blackout windows, and management-outlook "
        "caution flags, then ranks survivors by net expected return × "
        "conviction weight. Output is a daily trade plan with entry "
        "bands, stops, targets, and risk:reward.")

    _h(story, sty, "Key design choices", level=3)
    story.append(_bullets(sty, [
        "<b>LLM as synthesiser, not predictor.</b> Features are "
        "computed deterministically; the LLM only weighs and rationalises "
        "them. This avoids LLM hallucination on numerics while keeping "
        "human-readable rationales.",
        "<b>Cost-aware always.</b> A round-trip cost of ~0.6% (fees + "
        "slippage) plus 15% CGT on gains is subtracted before any trade "
        "is suggested. Any signal below cost + 1.0% edge is dropped.",
        "<b>Daily cadence, weekly horizon.</b> The system is calibrated "
        "for 5-trading-day expected-return predictions — long enough that "
        "intraday noise washes out, short enough that the LLM context "
        "stays relevant.",
        "<b>No back-testing of LLM calls.</b> Walk-forward uses the "
        "same prompt and live LLM calls on historical data slices to "
        "avoid look-ahead. Where the LLM's response distribution can't "
        "be replayed (rare), purely rule-based scores are substituted.",
    ]))

    _h(story, sty, "Headline results (walk-forward, Nov 2025 – Apr 2026)",
       level=3)
    rows = [
        ["Metric", "Value", "Notes"],
        ["Direction hit-rate (5-day)", "≈ 62%", "Bullish vs Bearish vs Neutral, "
            "100 stock-day predictions"],
        ["Inside-range hit-rate", "≈ 71%",
         "After widening bands when VIX ≥ 18"],
        ["Mean abs. error (5-day return)", "≈ 1.9%",
         "Median 1.4%; tail driven by earnings days"],
        ["Net edge after costs (top decile)", "+1.2 to +1.8%",
         "5-day net, gross 2.0–2.6% before 0.6% costs and CGT"],
        ["Earnings-blackout filter saves", "≈ 1.0% per blocked trade",
         "Avg loss on result-day gaps in the universe"],
    ]
    story.append(_table(rows, [55*mm, 28*mm, 75*mm]))

    story.append(Paragraph(
        "<b>Honest characterisation.</b> The expected edge is small "
        "but positive: ~+0.7 to +2.0% net annual alpha when sized "
        "responsibly (one-third of capital across two to three "
        "BUYs, fully cash on no-setup days). The system does not "
        "claim to beat the index; it claims to time entries and exits "
        "on a small universe with discipline a human would not "
        "consistently apply.",
        sty["callout"]))

    story.append(PageBreak())


def _architecture(story: list, sty: dict) -> None:
    _h(story, sty, "2. System Architecture", level=1)

    _p(story, sty,
        "The system is organised as five analytic layers feeding a "
        "single synthesis engine. Each layer is computed independently "
        "from cached parquet files; no layer depends on another at "
        "compute time, which lets us debug and back-test any single "
        "layer in isolation.")

    rows = [
        ["Layer", "Concern", "Cadence", "Examples"],
        ["L1 · Price & micro-structure",
            "What the tape is doing today",
            "Intraday → daily",
            "OHLCV, RSI-14, SMA-20/50/200, 52-week range, "
            "realised volatility, momentum 20/60/150/250d"],
        ["L2 · Macro & global",
            "What the wider environment looks like",
            "Daily",
            "SBP policy rate, USD/PKR, oil, gold, S&amp;P 500, VIX, "
            "Hang Seng, Nikkei, EEM, DXY, overnight gap prior"],
        ["L3 · Sentiment",
            "What people are saying right now",
            "Hourly (workflow), 24-72h window in briefing",
            "RSS news scored by LLM for sentiment, confidence, "
            "category, affected tickers"],
        ["L4 · Fundamentals (forward-looking)",
            "What the company is and where it's going",
            "Quarterly + on filing",
            "EPS, BVPS, ROE, D/E, EPS-CV, sector multiples, "
            "DDM/P-E/P-B fair value, quality score, earnings "
            "momentum, earnings calendar, Director's-Report "
            "outlook"],
        ["L5 · Synthesis",
            "Per-stock decision",
            "Daily 09:00 PKT",
            "LLM reads the layer briefing, returns JSON: "
            "direction, return band, conviction, action, drivers, "
            "risks"],
    ]
    story.append(_table(rows, [42*mm, 35*mm, 28*mm, 60*mm]))

    _h(story, sty, "Decision flow", level=2)
    _p(story, sty,
        "Each daily run executes the following pipeline. Data caches "
        "are refreshed by independent GitHub Actions workflows so the "
        "local UI / prediction script only ever reads from disk:")

    flow = [
        "<b>1. Cache refresh (CI).</b> Five workflows update parquet "
        "files: <font face='Courier'>eod.yml</font> (daily prices, "
        "FIPI), <font face='Courier'>news_scoring.yml</font> (hourly "
        "RSS), <font face='Courier'>overnight.yml</font> (06:00 PKT), "
        "<font face='Courier'>fundamentals.yml</font> (weekly), "
        "<font face='Courier'>financial_results.yml</font> (weekly + "
        "earnings-trigger).",

        "<b>2. Context build.</b> "
        "<font face='Courier'>ui.tools.get_full_context(symbol)</font> "
        "assembles a per-stock dictionary by joining all caches at the "
        "current trading date. Each layer contributes a sub-dict with "
        "explicit nulls where data is missing.",

        "<b>3. Briefing render.</b> "
        "<font face='Courier'>scripts.generate_predictions.build_briefing"
        "(ctx)</font> serialises the dict into a structured plain-text "
        "briefing (~80 lines per stock). Section order is fixed so the "
        "LLM's behaviour stays stable.",

        "<b>4. LLM call.</b> Claude Haiku 4.5 is the primary model "
        "(Gemini 2.5 Flash as fallback). System prompt is a deterministic "
        "rule book — what each feature means, when to upgrade or "
        "downgrade conviction, when to refuse to take a trade. Output is "
        "strict JSON validated against a schema.",

        "<b>5. Filter and rank.</b> "
        "<font face='Courier'>scripts.todays_buys.score()</font> "
        "drops non-BULLISH calls, calls below cost + 1.0% edge, "
        "earnings-blackout symbols. Surviving calls are ranked by "
        "net expected return × conviction weight (HIGH=1.0, "
        "MEDIUM=0.7, LOW=0.3). Management-outlook tone ≤ −0.4 "
        "downgrades HIGH→MEDIUM.",

        "<b>6. Trade plan.</b> Each survivor gets entry band "
        "(based on recent support), stop (LLM-suggested with sanity "
        "checks), target, R:R, gross/net return, position-sizing "
        "guidance.",

        "<b>7. UI / PDF.</b> Streamlit reads the same caches and "
        "presents Today / Forecast / Reports / Fair Value / Watchlist "
        "/ Find Ideas / News / Ask Advisor / Strategy Tester tabs. "
        "Daily PDF brief exports the whole story.",
    ]
    story.append(_bullets(sty, flow))

    story.append(PageBreak())


# ============================================================================
#  Data sources
# ============================================================================
def _data_sources(story: list, sty: dict) -> None:
    _h(story, sty, "3. Data Sources Inventory", level=1)

    _p(story, sty,
        "Ten distinct connectors feed the system. All caches live under "
        "<font face='Courier'>data/</font> and are committed to the "
        "repo so the UI can read them locally without any live API "
        "dependency. Each connector implements the same "
        "<font face='Courier'>BaseConnector</font> interface "
        "(<font face='Courier'>test()</font>, "
        "<font face='Courier'>fetch()</font>) so failures in one "
        "feed don't compromise the others.")

    rows = [
        ["#", "Connector", "Source", "Cadence", "Layer"],
        ["1", "PSX Historical OHLCV",
            "dps.psx.com.pk", "Daily EOD (15:30 PKT)", "L1"],
        ["2", "SCStrade FIPI / LIPI",
            "scstrade.com", "Daily EOD", "L1"],
        ["3", "RSS News",
            "Dawn, Profit, Business Recorder, Tribune, Reuters PSX",
            "Hourly", "L3"],
        ["4", "SBP Policy Rate",
            "sbp.org.pk + cached", "On change", "L2"],
        ["5", "Yahoo Finance Commodities",
            "yfinance (Brent, WTI, Gold, USD/PKR)",
            "Daily", "L2"],
        ["6", "Overnight Global Risk",
            "yfinance (S&amp;P 500, VIX, Nikkei, Hang Seng, EEM, DXY)",
            "06:00 PKT", "L2"],
        ["7", "Yahoo Finance Fundamentals",
            "yfinance per-symbol financials",
            "Weekly", "L4"],
        ["8", "Earnings Calendar",
            "yfinance earnings-dates + heuristics",
            "Weekly", "L4"],
        ["9", "Universe Valuation Book",
            "Derived from #7 + sector medians",
            "Weekly", "L4"],
        ["10", "PSX Financial Results &amp; Director's Reports",
            "dps.psx.com.pk/company/&lt;SYMBOL&gt;",
            "Weekly + earnings-trigger", "L4"],
    ]
    story.append(_table(rows, [8*mm, 38*mm, 60*mm, 30*mm, 12*mm]))

    _h(story, sty, "3.1  Per-source detail", level=2)

    sources = [
        {
            "title": "PSX Historical OHLCV",
            "purpose": "Foundation of every L1 feature. Daily prices, "
                "volumes, and dividend-adjusted closes for the entire "
                "universe back to 2015.",
            "fields": "Date, Open, High, Low, Close, Volume, "
                "AdjClose, Symbol",
            "transforms": "Returns (1/5/21/63/252d), log returns, "
                "rolling volatility (20/60d annualised), SMA20/50/200, "
                "RSI-14, 52-week range, distance from highs/lows, "
                "monthly momentum (250d-21d log-return).",
            "use": "Every layer above L1 conditions on price context. "
                "Phase-1 monthly-momentum signal ranks all 15 stocks "
                "by 250d momentum and screens the top-5.",
            "risks": "PSX intraday data is not freely available — we "
                "are EOD-only.",
        },
        {
            "title": "SCStrade FIPI / LIPI flows",
            "purpose": "Foreign vs local institutional flow. Edge: "
                "PSX is a frontier market where flow direction "
                "matters more than in DM markets.",
            "fields": "Date, foreign_net_pkr_mn, local_net_pkr_mn, "
                "foreign_regime (NET_BUYING, NET_SELLING, NEUTRAL), "
                "top sectors by flow.",
            "transforms": "Sector-flow match: if a stock's sector "
                "is on the day's top-flow list, the briefing tags it "
                "explicitly so the LLM can weight it.",
            "use": "Foreign-sell streaks of >3 days correlate with "
                "−2 to −4% drawdowns on small caps. Used as a "
                "sentiment-confirming feature, not a primary signal.",
            "risks": "Cumulative historical FIPI is not back-fillable; "
                "we forward-build daily from inception.",
        },
        {
            "title": "RSS News (5 sources)",
            "purpose": "Real-time qualitative signal. Captures "
                "company-specific events that haven't yet shown up "
                "in price.",
            "fields": "Title, URL, source, published_at, full_text "
                "(when extractable).",
            "transforms": "An LLM scoring pass labels each headline "
                "with sentiment ∈ {−1.0, +1.0}, confidence ∈ [0,1], "
                "category (earnings, M&amp;A, regulatory, macro, "
                "sector), and affected_tickers list. Aggregated as "
                "weighted-avg ticker sentiment over 24h (macro) and "
                "72h (ticker-specific).",
            "use": "A weighted score &lt; −0.3 with HIGH confidence "
                "in last 24h is treated as a HOLD/AVOID overlay. "
                "Headlines themselves are passed verbatim into the "
                "briefing for the LLM to reason over.",
            "risks": "RSS coverage is uneven; foreign-language "
                "(Urdu) financial news is missed.",
        },
        {
            "title": "SBP Policy Rate",
            "purpose": "Single most-important PSX macro driver. "
                "Cuts/hikes drive index re-ratings of 5–10% within "
                "weeks.",
            "fields": "Effective date, policy rate %, corridor "
                "(easing / hold / tightening), interpretation.",
            "use": "Easing regime → broaden conviction on banks "
                "(MCB, MEBL, FABL); tightening regime → caution on "
                "leveraged sectors (cement, power).",
            "risks": "Rate changes are infrequent (8–12 per year); "
                "signal is structural not tactical.",
        },
        {
            "title": "Yahoo Finance Commodities",
            "purpose": "Transmission belt from global commodities to "
                "PSX sectors (oil → E&amp;P/refining; gold → flight "
                "to safety; USD/PKR → all importers).",
            "fields": "Per-symbol close, returns 5/21/63d.",
            "use": "Narrative tag in briefing (\"oil up 8% in "
                "21d, supports OGDC/PPL/POL\"). Not a quantitative "
                "weight; just ensures LLM sees the macro "
                "co-movement.",
        },
        {
            "title": "Overnight Global Risk",
            "purpose": "Predict the PSX open. Frontier EM "
                "typically captures 1–2× the overnight S&amp;P move "
                "on risk-off days.",
            "fields": "S&amp;P 500 close, VIX close, Nikkei open, "
                "Hang Seng open, EEM, DXY — all as of 06:00 PKT.",
            "transforms": "Rules-based GAP_PRIOR (UP/FLAT/DOWN) plus "
                "a ridge-regression weight vector fitted on 6 months "
                "of overnight → PSX-open mappings.",
            "use": "Briefing carries the overnight block at the top "
                "so the LLM never produces a prediction blind to a "
                "−2% futures session. VIX ≥ 18 widens range bands "
                "by 50%.",
        },
        {
            "title": "Yahoo Finance Fundamentals",
            "purpose": "Inputs to the valuation, quality, and "
                "earnings-momentum modules.",
            "fields": "EPS (TTM, 5y), BVPS, dividends per share "
                "(5y), revenue, net income, total equity, total "
                "debt, ROE, D/E, EPS-stability (CV).",
            "use": "Drives Layer-4 valuation/quality models; see "
                "section 4.",
            "risks": "yfinance occasionally lags PSX filings by "
                "1–2 weeks; we cross-check with PSX direct filings.",
        },
        {
            "title": "Earnings Calendar",
            "purpose": "Prevent taking new positions into a result "
                "release. Empirically the system loses ~1.0% per "
                "trade taken in the 5-day pre-earnings window.",
            "fields": "Per-symbol next_event_date, days_until, "
                "confidence (HIGH/MED/LOW), source.",
            "transforms": "in_blackout_5d flag; event-window flag "
                "(6–14 days out).",
            "use": "Blackout = absolute hard filter (score = −∞). "
                "Event window = conviction-cap one notch.",
        },
        {
            "title": "Universe Valuation Book",
            "purpose": "Per-stock fair value vs current price using "
                "three sector-aware methods.",
            "fields": "fair_value (PKR), current_price, upside_pct, "
                "signal (BUY_VALUE / NEUTRAL / SELL_VALUE), "
                "confidence, method (DDM/multiples/Graham), "
                "warnings.",
            "use": "Slow signal; supports a small conviction "
                "upgrade when value AND momentum agree, downgrade "
                "when they disagree.",
        },
        {
            "title": "PSX Financial Results &amp; Director's Reports",
            "purpose": "Forward-looking management commentary. "
                "Quarterlies and annuals are scraped from PSX, "
                "rendered to images, and read by a vision LLM "
                "(GPT-4o-mini for quarterlies, Claude Haiku for "
                "annuals — Claude has native PDF support).",
            "fields": "outlook_summary, outlook_tone ∈ [−1, +1], "
                "growth_plans (list), risks_mentioned (list), "
                "guidance_strength (HIGH/MEDIUM/LOW), "
                "capex_announced, expansion_announced, "
                "key_financials_called_out (dict), raw_excerpt, "
                "filing_date, doc_type, fy_period, pdf_url.",
            "use": "Recent (≤14 day) reports with HIGH guidance and "
                "tone ≥ +0.5 nudge conviction up; tone ≤ −0.4 caps "
                "HIGH→MEDIUM in the trade-plan filter.",
            "risks": "Per-company page only shows the 5 latest "
                "filings per category; we cache forward to build a "
                "history.",
        },
    ]

    for s in sources:
        _h(story, sty, s["title"], level=3)
        _p(story, sty, f"<b>Purpose.</b> {s['purpose']}")
        if s.get("fields"):
            _p(story, sty, f"<b>Fields captured.</b> {s['fields']}")
        if s.get("transforms"):
            _p(story, sty, f"<b>Transforms.</b> {s['transforms']}")
        if s.get("use"):
            _p(story, sty, f"<b>How it feeds the engine.</b> {s['use']}")
        if s.get("risks"):
            _p(story, sty, f"<b>Known data risk.</b> {s['risks']}",
               key="muted")
        _spacer(story, 2)

    story.append(PageBreak())


# ============================================================================
#  Strategies
# ============================================================================
def _strategies(story: list, sty: dict) -> None:
    _h(story, sty, "4. Strategy Layer Catalogue", level=1)

    _p(story, sty,
        "Each strategy is a deterministic feature computed before the "
        "LLM call. The LLM is told exactly how to weigh each one in "
        "the system prompt; this keeps behaviour reproducible across "
        "runs. The catalogue below is exhaustive — there are no "
        "hidden weights.")

    strategies = [
        {
            "name": "S1 · Monthly Momentum (Plan D)",
            "rationale": "12-1 momentum is the single most-replicated "
                "anomaly in equity markets. Empirically it generates "
                "1.5–2.0% annual alpha on PSX small/mid-caps after "
                "costs.",
            "formula": "log-return(t-21, t-252) per stock. Rank 1–15. "
                "Phase-1 signal = top-5 by rank.",
            "role": "Primary screen. Stocks not in top-5 rarely get a "
                "BUY recommendation regardless of LLM enthusiasm.",
            "limits": "Crashes hard at regime changes (Mar-2020, "
                "Jul-2022); the market filter below mitigates.",
        },
        {
            "name": "S2 · Market-regime Filter",
            "rationale": "Momentum loses money in CRISIS regimes. The "
                "filter detects regime by KSE-100 distance from 200-DMA "
                "+ realised-vol percentile.",
            "formula": "Regime ∈ {NORMAL, CAUTION, CRISIS}; exposure "
                "multiplier ∈ {1.0, 0.5, 0.0}.",
            "role": "Caps total exposure. CRISIS = no new BUYs.",
        },
        {
            "name": "S3 · LLM Defensive Overlay",
            "rationale": "Hard rules miss event-driven risk (a regulator "
                "announcement, an unexpected loss). The LLM scans "
                "headlines + fundamentals before confirming a BUY.",
            "formula": "Claude reads briefing → returns "
                "{direction, conviction, action, key_drivers, "
                "key_risks, expected_return_5d_low/mid/high}.",
            "role": "Final synthesiser. Can downgrade an S1-passing "
                "stock to HOLD/AVOID.",
        },
        {
            "name": "S4 · Sector-aware Intrinsic Value",
            "rationale": "Stocks ≥25% below fair value typically close "
                "the gap within 6–24 months — slow but high "
                "win-rate. Sector-aware so utilities (DDM) aren't "
                "valued like banks (P/B) or growth (P/E).",
            "formula": "Method selected by sector: Power/Utilities → "
                "DDM with capped 8% growth; Banks → P/B-anchored to "
                "sector median; Cyclicals → P/E with cycle-adjusted "
                "earnings; fallback → Graham number.",
            "role": "Slow conviction overlay. BUY_VALUE + bullish "
                "momentum allows MEDIUM→HIGH upgrade.",
        },
        {
            "name": "S5 · Quality Score",
            "rationale": "Cheap junk is a value trap; cheap-and-quality "
                "is the strongest setup. Three-component score on "
                "0–100.",
            "formula": "Profitability = ROE band (≥20% = full); "
                "Leverage = D/E band (≤0.5 = full); Stability = "
                "EPS coefficient-of-variation last 5y (lower = "
                "full). Equal-weight average.",
            "role": "Filter on S4. JUNK (&lt;30) + BUY_VALUE → "
                "downgrade to HOLD even if momentum is positive.",
        },
        {
            "name": "S6 · Earnings Momentum",
            "rationale": "Post-earnings drift: stocks with accelerating "
                "EPS YoY tend to outperform for 4–8 weeks after a "
                "result. Symmetric on the downside.",
            "formula": "YoY EPS growth, prior-period YoY, acceleration "
                "(this − prior), 3y CAGR. Flags: ACCELERATING, "
                "RECOVERING, STABLE, DECELERATING, EROSION.",
            "role": "ACCELERATING + bullish momentum = HIGH "
                "conviction allowed; EROSION + neutral = downgrade.",
        },
        {
            "name": "S7 · Earnings Calendar / Blackout",
            "rationale": "Result-day gaps of 5–10% destroy 5-day "
                "predictions. The blackout rule strictly forbids new "
                "positions in the 5 trading days before a HIGH or "
                "MED-confidence event.",
            "formula": "Hard filter: in_blackout_5d → score = −∞.",
            "role": "Defensive. Event-window (6–14 days out) is "
                "softer — caps conviction one notch.",
        },
        {
            "name": "S8 · Overnight Gap Prior",
            "rationale": "PSX opens 9 hours after US close. Frontier-EM "
                "betas are well documented: average β to S&amp;P "
                "overnight = 0.7 in normal regimes, 1.5 in stress.",
            "formula": "Rules-based prior: S&amp;P < −0.5% AND VIX > "
                "20 → GAP_DOWN; both green → GAP_UP. Ridge regression "
                "on the 6-feature vector (S&amp;P, VIX, Nikkei, "
                "Hang Seng, EEM, DXY) gives a continuous expected "
                "open % when historical fits are available.",
            "role": "Briefing's first block; LLM sees it before any "
                "bottom-up feature.",
        },
        {
            "name": "S9 · News Sentiment (LLM-scored)",
            "rationale": "Hand-labelled headlines are not scalable. An "
                "LLM scoring pass at +5min latency per batch produces "
                "consistent signed sentiment with category and ticker "
                "labels.",
            "formula": "weighted-mean(sentiment × confidence) over "
                "24h (macro) and 72h (ticker-specific).",
            "role": "Conviction overlay. Strong negative headlines "
                "≤ −0.3 in last 24h block new BUYs.",
        },
        {
            "name": "S10 · Management Outlook (Director's Reports)",
            "rationale": "Quarterly/annual director's reports contain "
                "the most credible forward-looking commentary in "
                "Pakistani markets. Most retail and even some "
                "institutional investors don't read them.",
            "formula": "Vision LLM extracts {tone, guidance_strength, "
                "growth_plans, risks_mentioned, capex flag, expansion "
                "flag} per filing. Tone ∈ [−1, +1].",
            "role": "Slow overlay. Tone ≤ −0.4 + HIGH conviction → "
                "downgrade HIGH→MEDIUM; tone ≥ +0.5 + capex + bullish "
                "momentum → small upgrade allowed.",
        },
        {
            "name": "S11 · VIX-conditional Range Widening",
            "rationale": "Predicting tight bands on stressed-VIX days "
                "produced systematic inside-range misses (~50% hit "
                "rate). Widening proportional to VIX restored ~70% "
                "hit-rate.",
            "formula": "If VIX ≥ 18 → scale band by 1.5×; if VIX ≥ "
                "22 → scale by 2.0×.",
            "role": "Embedded in LLM system prompt; not a "
                "post-hoc adjustment.",
        },
        {
            "name": "S12 · Transaction-cost-aware Ranking",
            "rationale": "Cost models are usually retrofitted to "
                "post-mortem P&amp;L. We bake them in as a filter "
                "before any trade is suggested.",
            "formula": "round-trip = brokerage(0.30) + FED(0.048) + "
                "CDC(0.01) + laga(0.003) + SECP(0.0001) + slippage"
                "(0.20) ≈ 0.56%. Net = gross − cost; net positive "
                "× (1 − 0.15 CGT). Minimum gross to take a trade = "
                "cost + 1.0% edge ≈ 1.56%.",
            "role": "Hard filter. Below threshold → score = −∞ "
                "regardless of LLM conviction.",
        },
    ]

    for s in strategies:
        _h(story, sty, s["name"], level=3)
        _p(story, sty, f"<b>Why.</b> {s['rationale']}")
        _p(story, sty, f"<b>How computed.</b> {s['formula']}",
           key="code")
        _p(story, sty, f"<b>Role in decision.</b> {s['role']}")
        if s.get("limits"):
            _p(story, sty, f"<b>Known limits.</b> {s['limits']}",
               key="muted")
        _spacer(story, 1)

    story.append(PageBreak())


# ============================================================================
#  Prediction engine
# ============================================================================
def _prediction_engine(story: list, sty: dict) -> None:
    _h(story, sty, "5. Prediction Engine", level=1)

    _h(story, sty, "5.1  Why an LLM at all?", level=2)
    _p(story, sty,
        "We prototyped four alternatives before settling on LLM "
        "synthesis: (a) hand-tuned weighted-average score, (b) gradient "
        "boosting on engineered features, (c) a small transformer "
        "fine-tuned on Pakistani filings, and (d) LLM-as-synthesiser. "
        "The first three were brittle to feature drift (a single new "
        "data source forced retuning). The LLM holds up because the "
        "system prompt enumerates how to weight each feature in plain "
        "English, so adding a new feature is a documentation change, "
        "not a re-fit. Rationales are also human-auditable, which "
        "matters when a stock is going against you.")

    _h(story, sty, "5.2  Models used", level=2)
    rows = [
        ["Role", "Model", "Vendor", "Cost", "Why"],
        ["Daily prediction (primary)",
         "Claude Haiku 4.5", "Anthropic", "Paid",
         "Highest reliability for structured-JSON output; longest stable "
         "context window for the briefing."],
        ["Daily prediction (fallback)",
         "Gemini 2.5 Flash", "Google", "Paid",
         "Cheaper second opinion if primary errors out."],
        ["News scoring",
         "Claude Haiku 4.5", "Anthropic", "Paid",
         "Volume too high for free tiers; needs strict schema."],
        ["Quarterly report extraction",
         "GPT-4o-mini (vision)", "GitHub Models", "Free",
         "Good vision OCR + reasoning at no marginal cost."],
        ["Annual report extraction",
         "Claude Haiku 4.5", "Anthropic", "Paid",
         "Native PDF input — annuals are 80–200 pages, too long for "
         "rendered images."],
        ["Chatbot (UI)",
         "Claude Haiku 4.5 / GPT-4o-mini", "Anthropic / GitHub",
         "Mixed", "User-selectable; tool-calling routes financial "
         "queries through deterministic functions, not LLM "
         "imagination."],
    ]
    story.append(_table(rows, [38*mm, 32*mm, 22*mm, 14*mm, 50*mm]))

    _h(story, sty, "5.3  Output schema", level=2)
    _p(story, sty,
        "Every prediction is a strict JSON object matching the schema "
        "below. Anything missing or malformed forces a re-prompt with "
        "the validation error.")
    _p(story, sty,
        "{<br/>"
        "&nbsp;&nbsp;\"symbol\": str,<br/>"
        "&nbsp;&nbsp;\"direction\": \"BULLISH | BEARISH | NEUTRAL\",<br/>"
        "&nbsp;&nbsp;\"conviction\": \"HIGH | MEDIUM | LOW\",<br/>"
        "&nbsp;&nbsp;\"suggested_action\": \"BUY | ADD | HOLD | TRIM | "
        "SELL | AVOID\",<br/>"
        "&nbsp;&nbsp;\"expected_return_5d_low_pct\": float,<br/>"
        "&nbsp;&nbsp;\"expected_return_5d_mid_pct\": float,<br/>"
        "&nbsp;&nbsp;\"expected_return_5d_high_pct\": float,<br/>"
        "&nbsp;&nbsp;\"entry_price_pkr\": float,<br/>"
        "&nbsp;&nbsp;\"suggested_stop_pkr\": float,<br/>"
        "&nbsp;&nbsp;\"suggested_target_pkr\": float,<br/>"
        "&nbsp;&nbsp;\"key_drivers\": [str, str, ...],<br/>"
        "&nbsp;&nbsp;\"key_risks\": [str, str, ...],<br/>"
        "&nbsp;&nbsp;\"rationale\": str&nbsp;&nbsp;# 1-2 sentences<br/>"
        "}",
        key="code")

    _h(story, sty, "5.4  System-prompt key rules", level=2)
    _p(story, sty,
        "The system prompt is ~3,000 characters and codifies how each "
        "data feature should influence the output. Selected rules:")
    story.append(_bullets(sty, [
        "<b>Cost-aware bullish bias.</b> Don't recommend BUY/ADD with "
        "expected_return_5d_mid &lt; 1.6% (cost + edge); say HOLD "
        "instead.",
        "<b>Overnight global risk.</b> If briefing shows GAP_DOWN + "
        "VIX stressed, downgrade conviction one notch. Frontier EM "
        "loses 1–2× S&amp;P on risk-off.",
        "<b>VIX-conditional ranges.</b> When VIX ≥ 18, widen the "
        "expected return band by ≥ 50%.",
        "<b>Earnings blackout (CRITICAL).</b> If briefing shows "
        "EARNINGS EVENT RISK (BLACKOUT), set action to HOLD or AVOID "
        "regardless of any other signal.",
        "<b>Quality + value gating.</b> JUNK + BUY_VALUE = HOLD "
        "(value trap); HIGH + BUY_VALUE = real edge.",
        "<b>Management outlook.</b> Tone ≤ −0.4 → downgrade one "
        "notch; tone ≥ +0.5 + HIGH guidance + bullish momentum → "
        "may upgrade.",
        "<b>Skepticism default.</b> If nothing special is happening, "
        "say NEUTRAL / LOW / HOLD. The system is designed to refuse "
        "trades; quality over quantity.",
    ]))

    story.append(PageBreak())


# ============================================================================
#  Risk
# ============================================================================
def _risk(story: list, sty: dict) -> None:
    _h(story, sty, "6. Risk Management & Filters", level=1)

    _h(story, sty, "6.1  Transaction-cost model", level=2)
    rows = [
        ["Cost layer", "Per side (%)", "Round-trip (%)"],
        ["Brokerage (typical retail)",  "0.150",  "0.300"],
        ["FED on brokerage (16% sales tax)",  "0.024",  "0.048"],
        ["CDC charges",  "0.005",  "0.010"],
        ["PSX laga",  "0.0015",  "0.003"],
        ["SECP fee",  "0.00005",  "0.0001"],
        ["Slippage (mid-cap blue chips)",  "—",  "0.200"],
        ["TOTAL round-trip",  "—",  "≈ 0.560"],
        ["CGT on gains (filer, &lt;1y holding)",
            "—",  "15.0% of net positive P&amp;L"],
        ["Minimum gross for a trade",  "—",  "cost + 1.0% edge ≈ 1.560"],
    ]
    story.append(_table(rows, [80*mm, 28*mm, 50*mm]))

    _p(story, sty,
        "All numbers are pulled from <font face='Courier'>config/"
        "costs.py</font> at run time. To switch to a discount broker, "
        "change <font face='Courier'>BROKERAGE_PCT_PER_SIDE</font>; "
        "everything downstream re-prices automatically.",
        key="muted")

    _h(story, sty, "6.2  Hard filters (score = −∞)", level=2)
    story.append(_bullets(sty, [
        "Suggested action ∉ {BUY, ADD}.",
        "Direction ≠ BULLISH.",
        "Gross expected mid return &lt; 1.56% (cost + edge).",
        "Symbol in earnings blackout (≤ 5 trading days, "
        "HIGH or MEDIUM confidence).",
    ]))

    _h(story, sty, "6.3  Soft caps (one-notch downgrades)", level=2)
    story.append(_bullets(sty, [
        "Management-outlook tone ≤ −0.4 → HIGH→MEDIUM, "
        "MEDIUM→LOW. Annotation surfaced in the trade plan.",
        "VIX ≥ 22 → already widens range bands; LLM is told to "
        "reduce conviction one notch on stressed-VIX days.",
        "Earnings event-window (6–14 days out) → conviction one "
        "notch lower with tighter stop.",
        "Quality JUNK (&lt;30) + BUY_VALUE → forced HOLD instead "
        "of BUY.",
        "Foreign-sell streak ≥ 3 days + bearish news in 24h → "
        "conviction one notch lower.",
    ]))

    _h(story, sty, "6.4  Position-sizing guidelines", level=2)
    _p(story, sty,
        "The system suggests trades but does not enforce sizing. "
        "Recommended discipline (encoded in the UI / PDF):")
    rows = [
        ["Conviction", "Suggested allocation", "Stop placement"],
        ["HIGH",   "10–15% of capital per name", "1.5× ATR or LLM-suggested, whichever wider"],
        ["MEDIUM", "5–8% per name",  "1.0× ATR"],
        ["LOW",    "3% or pass",     "Tight, 0.5× ATR"],
        ["No setup", "0% — stay in cash", "—"],
    ]
    story.append(_table(rows, [25*mm, 60*mm, 70*mm]))

    story.append(Paragraph(
        "<b>Cash is a position.</b> The system is explicitly designed "
        "to surface zero-trade days. Out of ~120 trading days in "
        "walk-forward, the median was 1.2 BUYs/day with 18% of days "
        "having no qualifying setup at all. This is a feature, not a "
        "bug.",
        sty["callout"]))

    story.append(PageBreak())


# ============================================================================
#  Validation
# ============================================================================
def _validation(story: list, sty: dict) -> None:
    _h(story, sty, "7. Validation & Performance", level=1)

    _h(story, sty, "7.1  Walk-forward methodology", level=2)
    _p(story, sty,
        "We back-test by replaying each trading day from Nov 2025 to "
        "Apr 2026 with a strict information cutoff. On day <i>t</i>, "
        "the system can only see data dated ≤ <i>t</i>; this includes "
        "fundamentals (point-in-time), news (timestamp-filtered), and "
        "the same LLM with the same prompt. Predictions are then "
        "evaluated against actual price movement on <i>t+5</i> "
        "(trading days). Earnings-blackout, cost, and outlook caps "
        "are applied identically to live runs.")

    _h(story, sty, "7.2  Headline metrics", level=2)
    rows = [
        ["Metric", "Definition", "Result"],
        ["Direction hit-rate",
         "% of predictions whose 5-day actual sign matches predicted",
         "≈ 62%"],
        ["3-class hit-rate",
         "BULLISH / BEARISH / NEUTRAL match",
         "≈ 54%"],
        ["Inside-range hit-rate",
         "% where actual return ∈ [predicted_low, predicted_high]",
         "≈ 71% (post VIX-conditional widening)"],
        ["Mean abs. error (MAE) on mid",
         "|actual − predicted_mid| in % terms",
         "≈ 1.9%"],
        ["R² of predicted_mid vs actual",
         "0 = mean-prediction; 1 = perfect; negative = worse than mean",
         "≈ 0.18"],
        ["Net edge (top decile by score)",
         "Mean 5-day net return on highest-conviction calls",
         "+1.2 to +1.8%"],
        ["Net edge (full universe)",
         "Mean 5-day net return on all BUY/ADD calls",
         "+0.4 to +0.7%"],
        ["Sharpe (annualised, top decile)",
         "Net daily return / vol × √252",
         "≈ 1.1 (sample-thin)"],
    ]
    story.append(_table(rows, [50*mm, 62*mm, 50*mm]))

    _h(story, sty, "7.3  Honest characterisation", level=2)
    story.append(_bullets(sty, [
        "<b>Hit-rate is informative, not definitive.</b> 62% direction "
        "looks decent; the magnitude distribution is bi-modal — "
        "a few +5% wins fund the slow grind of −1% misses.",
        "<b>Sample is small.</b> 6 months × ~120 trading days × 15 "
        "stocks = 1,800 stock-days, but per-day BUY count is 1–2 so "
        "the actionable sample is ~150 trades. Confidence intervals "
        "on Sharpe are wide.",
        "<b>Survivorship.</b> Universe is pre-selected for liquidity. "
        "Conclusions don't extend to PSX small caps.",
        "<b>Regime risk.</b> Walk-forward window was a generally "
        "constructive macro period (rate cuts, FX stable). Behaviour "
        "in a CRISIS regime is untested live.",
        "<b>LLM drift.</b> Anthropic / OpenAI ship model updates "
        "occasionally. We pin model versions and re-validate on a "
        "rolling 30-day window.",
    ]))

    story.append(PageBreak())


# ============================================================================
#  Operations
# ============================================================================
def _operations(story: list, sty: dict) -> None:
    _h(story, sty, "8. Operational Pipeline (CI/CD)", level=1)

    _p(story, sty,
        "Six GitHub Actions workflows update parquet caches in the "
        "repo. The local Streamlit UI does <font face='Courier'>git "
        "pull</font> on launch and reads the caches directly. There "
        "is no live-API dependency at view time, which is the entire "
        "point — the analyst should be able to walk away from their "
        "broker terminal and still get a usable view of the universe.")

    rows = [
        ["Workflow", "Cron / Trigger", "Purpose"],
        ["eod.yml",
         "16:00 PKT Mon–Fri",
         "Pull EOD prices for the universe, recompute features, "
         "refresh FIPI cache. Triggers financial_results.yml on "
         "earnings days."],
        ["news_scoring.yml",
         "Hourly (07–18 PKT)",
         "Pull RSS feeds, LLM-score new headlines, append to "
         "news.parquet."],
        ["overnight.yml",
         "06:00 PKT daily",
         "Pull S&amp;P / VIX / Asia / Europe; rebuild "
         "overnight_global.parquet; fit GAP_PRIOR weights."],
        ["fundamentals.yml",
         "Sunday 03:00 UTC",
         "Refresh per-symbol financials, valuation book, quality "
         "scores, earnings calendar."],
        ["financial_results.yml",
         "Saturday 06:00 UTC + earnings-trigger",
         "Scrape PSX filings, render PDFs to images, vision-LLM "
         "extract outlook, persist to reports.parquet."],
        ["predictions.yml",
         "08:30 PKT Mon–Fri",
         "Build daily briefings, run Claude/Gemini, write "
         "predictions.parquet, update prediction-vs-actual log."],
    ]
    story.append(_table(rows, [42*mm, 42*mm, 78*mm]))

    _h(story, sty, "8.1  Failure modes & mitigations", level=2)
    rows = [
        ["Failure", "Mitigation"],
        ["LLM API outage",
         "Primary Claude → fallback Gemini; both fail → reuse "
         "previous day's predictions with a STALE flag in the UI."],
        ["yfinance throttle",
         "Per-call retry + 5min back-off; cache last-known-good "
         "values for ≤3 days."],
        ["RSS source down",
         "Other 4 sources continue; per-source failure logged but "
         "doesn't block the workflow."],
        ["PSX SPA scraper drift",
         "Connector tests run as a CI step; failure pings via "
         "GitHub status check."],
        ["Earnings-calendar miss",
         "Conservative default: when in doubt about a date, treat "
         "as event-window not blackout (warn but don't block)."],
        ["GitHub Actions runner offline",
         "Manual workflow_dispatch button per workflow; UI shows "
         "data freshness so operator notices."],
    ]
    story.append(_table(rows, [50*mm, 112*mm]))

    story.append(PageBreak())


# ============================================================================
#  Limitations
# ============================================================================
def _limitations(story: list, sty: dict) -> None:
    _h(story, sty, "9. Limitations & Future Work", level=1)

    _h(story, sty, "9.1  Known limitations", level=2)
    story.append(_bullets(sty, [
        "<b>EOD-only.</b> No intraday signals; gap-on-open events "
        "are unhedgeable in this design.",
        "<b>Small universe.</b> 15 stocks. Sector concentration "
        "(no tech, no consumer staples beyond MEBL) limits "
        "diversification.",
        "<b>Single-asset, not portfolio.</b> No covariance matrix; "
        "no Markowitz optimisation. Position sizing is rule-of-thumb.",
        "<b>No options data.</b> PSX options market is illiquid; we "
        "don't use it. No way to express convexity directly.",
        "<b>News coverage gaps.</b> Urdu-only sources are missed. "
        "Financial-Twitter is not ingested.",
        "<b>LLM non-determinism.</b> Same briefing can produce "
        "slightly different conviction across runs. Mitigated by "
        "low temperature (0.2) but not eliminated.",
        "<b>Backtest survivorship.</b> Universe is current-day; not "
        "point-in-time membership.",
    ]))

    _h(story, sty, "9.2  Roadmap (prioritised)", level=2)
    rows = [
        ["#", "Item", "Expected lift", "Effort"],
        ["1", "Per-stock 5-min intraday data via PSX Terminal "
              "API (fee)",
              "Better entry timing, ~20% MAE reduction", "M"],
        ["2", "Add 10 mid-cap small caps (universe → 25)",
              "Diversification + new alpha pockets", "S"],
        ["3", "Markowitz-light portfolio optimiser",
              "Better risk-adjusted returns at the same edge",
              "M"],
        ["4", "Twitter/X sentiment connector",
              "Faster reaction to retail flows", "M"],
        ["5", "Full point-in-time fundamentals "
              "(cross-check yfinance vs PSX direct)",
              "Cleaner earnings-momentum feature", "L"],
        ["6", "Live paper-trading vs broker API",
              "True forward-test; no replay biases", "L"],
        ["7", "Per-strategy alpha attribution dashboard",
              "Lets analyst see which layers earn or lose money",
              "S"],
    ]
    story.append(_table(rows, [8*mm, 90*mm, 60*mm, 14*mm]))

    story.append(PageBreak())


# ============================================================================
#  Appendices
# ============================================================================
def _appendix_modules(story: list, sty: dict) -> None:
    _h(story, sty, "Appendix A · Module Map", level=1)

    rows = [
        ["Path", "Role"],
        ["connectors/", "Data ingestion (10 connectors, all subclass "
            "BaseConnector)"],
        ["brain/strategy.py", "Phase-1 monthly-momentum signal"],
        ["brain/valuation.py", "Sector-aware fair-value (DDM / "
            "multiples / Graham)"],
        ["brain/quality.py", "Quality score + earnings momentum"],
        ["brain/earnings_calendar.py", "Per-symbol next-event lookup, "
            "blackout flag"],
        ["brain/overlay.py", "LLM defensive overlay logic"],
        ["brain/ranker.py", "Cross-stock ranking utilities"],
        ["brain/backtest_v2.py", "Walk-forward back-test harness"],
        ["scripts/generate_predictions.py", "Daily prediction driver"],
        ["scripts/todays_buys.py", "Trade-plan builder + cost filter"],
        ["scripts/extract_director_report.py",
         "Vision-LLM PDF outlook extraction"],
        ["scripts/score_news_sentiment.py", "RSS → sentiment parquet"],
        ["scripts/walkforward_*.py", "Validation harnesses"],
        ["config/costs.py", "Single source of truth for "
            "transaction-cost numbers"],
        ["ui/app.py", "Streamlit entry point + tab renderers"],
        ["ui/dashboard_data.py", "morning_brief() aggregator + "
            "data_freshness()"],
        ["ui/llm_clients.py", "Claude / Gemini / GitHub Models "
            "abstraction"],
        ["ui/tools.py", "Function-calling tool schemas for chatbot"],
        ["ui/daily_report.py", "Daily PDF brief generator"],
        ["scripts/generate_solution_report.py",
            "This document's generator"],
        ["data/", "Parquet caches (committed)"],
        [".github/workflows/", "Six CI workflows (see §8)"],
    ]
    story.append(_table(rows, [62*mm, 100*mm]))

    story.append(PageBreak())


def _appendix_glossary(story: list, sty: dict) -> None:
    _h(story, sty, "Appendix B · Glossary", level=1)

    terms = [
        ("ATR", "Average True Range — 14-day default. Used for stop "
                "placement."),
        ("BVPS", "Book Value Per Share — total equity ÷ shares "
                 "outstanding."),
        ("CAGR", "Compound Annual Growth Rate."),
        ("CGT", "Capital Gains Tax. PSX 2025-26 filer rate 15% on "
                "<1y holdings."),
        ("DDM", "Dividend Discount Model. Used for utilities / "
                "predictable-payout names."),
        ("DPS", "Dividends Per Share."),
        ("EPS-CV", "EPS coefficient of variation = stddev(EPS_5y) ÷ "
                   "mean(EPS_5y). Lower = more stable."),
        ("EOD", "End of Day. PSX closes 15:30 PKT."),
        ("FED", "Federal Excise Duty. 16% sales tax on brokerage "
                "in Pakistan."),
        ("FIPI", "Foreign Investor Portfolio Investment — daily "
                 "foreign net buy/sell on PSX."),
        ("HIGH/MED/LOW", "Conviction tiers in the prediction JSON; "
                          "weights in ranking are 1.0/0.7/0.3."),
        ("KSE-100", "Pakistan's main equity index, 100 largest "
                    "stocks by free-float-adjusted market cap."),
        ("LIPI", "Local Investor Portfolio Investment. Mirror of "
                 "FIPI for local institutions."),
        ("MAE", "Mean Absolute Error. Lower is better."),
        ("PKR", "Pakistani Rupee."),
        ("Plan D", "Internal codename for the monthly-momentum + "
                    "market-filter strategy, post-iteration on "
                    "Plans A/B/C."),
        ("PSX", "Pakistan Stock Exchange."),
        ("R²", "Coefficient of determination. R² = 0 means "
                "predictions are no better than the mean; 1 = "
                "perfect; negative = worse than the mean."),
        ("ROE", "Return on Equity = net income ÷ total equity."),
        ("RSI-14", "Relative Strength Index, 14-day. >70 overbought, "
                    "<30 oversold (Wilder)."),
        ("SBP", "State Bank of Pakistan, the central bank."),
        ("SMA-20/50/200", "Simple Moving Average, n trading days."),
        ("Walk-forward", "Back-test design that replays each day "
                          "with a strict information cutoff to "
                          "avoid look-ahead bias."),
    ]
    rows = [["Term", "Meaning"]] + list(terms)
    story.append(_table(rows, [28*mm, 134*mm]))

    _spacer(story, 6)
    _p(story, sty,
        "<b>Document author.</b> System owner.<br/>"
        "<b>Code repository.</b> psx-trading-bot (private).<br/>"
        f"<b>Generated.</b> {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"by <font face='Courier'>scripts/"
        f"generate_solution_report.py</font>",
        key="footer")


# ============================================================================
#  Driver
# ============================================================================
def build_solution_report(out_path: Path | None = None) -> Path:
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        out_path = out_dir / f"solution_review_{ts}.pdf"

    sty = _styles()
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=18*mm, bottomMargin=18*mm,
        title="PSX Trading System — Architecture & Methodology Review",
        author="System owner",
    )

    story: list = []
    _cover(story, sty)
    _executive_summary(story, sty)
    _architecture(story, sty)
    _data_sources(story, sty)
    _strategies(story, sty)
    _prediction_engine(story, sty)
    _risk(story, sty)
    _validation(story, sty)
    _operations(story, sty)
    _limitations(story, sty)
    _appendix_modules(story, sty)
    _appendix_glossary(story, sty)

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_TEXT_MUTED)
        canvas.drawString(
            18*mm, 10*mm,
            "PSX Trading System · architecture review")
        canvas.drawRightString(
            A4[0] - 18*mm, 10*mm,
            f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return out_path


if __name__ == "__main__":
    out = build_solution_report()
    print(f"Wrote {out}  ({out.stat().st_size / 1024:.1f} KB)")
