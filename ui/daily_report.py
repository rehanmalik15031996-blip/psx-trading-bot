"""Daily PDF brief generator (analyst-facing edition).

Builds a single-file PDF you can download from the Today tab and forward
to anyone (WhatsApp, email, print) or hand to an analyst — every
recommendation in the document carries an explicit reason that traces
back to a specific data point.

Sections (in order):
  1.  Header: date, market mood, one-line narrative
  2.  What to do today: top action card
  3.  Things to watch: alerts
  4.  Macro Radar: industry-KPI snapshot (T-bill, KIBOR, FX reserves,
      KSE-100, CPI), active macro drivers, per-sector tailwind /
      headwind verdicts with one-line reasons
  5.  Bot's Verdict: ONE unified call per stock, reconciling all
      seven lenses (Value / Quality / Momentum / Macro / News / Flow /
      Management) with explicit conflict-resolution rules
  6.  Forecast table: every universe stock with action / direction /
      conviction / 5d net %
  7.  Top news in last 24h: highest-impact scored articles
  8.  Material Information: PSX disclosures in the last 14 days
  9.  Per-stock detail: one card per universe stock with rationale,
      key drivers, key risks, macro reading, recent news, material
      disclosures, fundamental ratios vs sector medians
  10. Management outlook: latest Director's Reports
  11. Portfolio snapshot: positions + P&L
  12. Watchlist
  13. Top movers (universe)
  14. Quality leaders
  15. Earnings calendar (next 21 days)
  16. Footer: data freshness + disclaimers

Public API:
    build_daily_report(brief=None, mood=None, narrative=None,
                       action=None, alerts=None) -> bytes
        Returns the PDF as raw bytes ready for st.download_button.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    KeepTogether, PageBreak,
)


# ---------- styling ----------------------------------------------------
_BRAND_BLUE = colors.HexColor("#2f6feb")
_BRAND_GREEN = colors.HexColor("#1e8a45")
_BRAND_RED = colors.HexColor("#c0392b")
_BRAND_AMBER = colors.HexColor("#c08030")
_BG_BAND = colors.HexColor("#eef3fb")
_TEXT_MUTED = colors.HexColor("#5d6470")
_GRID = colors.HexColor("#cdd5e0")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    out = {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontSize=20, leading=24, textColor=_BRAND_BLUE,
            spaceAfter=4, alignment=0,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"],
            fontSize=11, leading=14, textColor=_TEXT_MUTED,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontSize=13, leading=16, textColor=_BRAND_BLUE,
            spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontSize=10, leading=14, spaceAfter=4,
        ),
        # Italic, lightly tinted "what this section means" caption that
        # sits under each section header — turns a plain table into
        # something a non-quant can read.
        "explainer": ParagraphStyle(
            "explainer", parent=base["Normal"],
            fontSize=9, leading=12,
            textColor=_TEXT_MUTED,
            backColor=_BG_BAND,
            borderPadding=6,
            spaceBefore=2, spaceAfter=6,
            leftIndent=0, rightIndent=0,
            fontName="Helvetica-Oblique",
        ),
        "body_muted": ParagraphStyle(
            "body_muted", parent=base["Normal"],
            fontSize=9, leading=12, textColor=_TEXT_MUTED,
            spaceAfter=4,
        ),
        "callout_green": ParagraphStyle(
            "callout_green", parent=base["Normal"],
            fontSize=11, leading=15, textColor=_BRAND_GREEN,
            spaceAfter=4,
        ),
        "callout_red": ParagraphStyle(
            "callout_red", parent=base["Normal"],
            fontSize=11, leading=15, textColor=_BRAND_RED,
            spaceAfter=4,
        ),
        "callout_amber": ParagraphStyle(
            "callout_amber", parent=base["Normal"],
            fontSize=11, leading=15, textColor=_BRAND_AMBER,
            spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "footer", parent=base["Normal"],
            fontSize=8, leading=10, textColor=_TEXT_MUTED,
        ),
    }
    return out


def _color_for_mood(label: str) -> colors.Color:
    label = (label or "").lower()
    if "bullish" in label or "positive" in label:
        return _BRAND_GREEN
    if "bearish" in label or "negative" in label or "risk-off" in label:
        return _BRAND_RED
    if "mixed" in label or "neutral" in label:
        return _BRAND_AMBER
    return _TEXT_MUTED


# ---------- section builders -------------------------------------------
def _explain(story: list, sty: dict, text: str) -> None:
    """Render a plain-English caption under a section header so a
    non-technical reader knows what they're looking at."""
    story.append(Paragraph(text, sty["explainer"]))


def _hero_section(story: list, sty: dict, mood: dict, narrative: str) -> None:
    today = datetime.now().strftime("%A, %d %B %Y")
    story.append(Paragraph(f"PSX Daily Brief — {today}", sty["title"]))

    mood_label = (mood or {}).get("label", "Neutral")
    mood_score = (mood or {}).get("score", 0)
    mood_color = _color_for_mood(mood_label)
    badge = (
        f'<para><font color="{mood_color.hexval()}"><b>● {mood_label}</b>'
        f'</font> &nbsp;·&nbsp; market mood {mood_score}/100</para>'
    )
    story.append(Paragraph(badge, sty["subtitle"]))
    _explain(story, sty,
        "<b>Market mood</b> is a 0–100 score combining four things: "
        "the rule-based regime classifier (NORMAL / CAUTION / CRISIS), "
        "tonight's overnight global cues (S&amp;P 500, VIX, Asia "
        "futures), the last 24 hours of scored PSX news, and any "
        "earnings-blackout warnings. Higher = more constructive backdrop "
        "for taking risk. Below 40 = step back; 40–60 = mixed / "
        "selective; above 60 = supportive."
    )
    if narrative:
        story.append(Paragraph(narrative, sty["body"]))
    story.append(Spacer(1, 4))


def _action_section(story: list, sty: dict, action: dict) -> None:
    if not action:
        return
    sym = action.get("symbol")
    story.append(Paragraph("Top action today", sty["h2"]))
    _explain(story, sty,
        "The single highest-conviction trade idea from today's 5-day "
        "forecasts <i>after</i> deducting estimated PSX round-trip "
        "transaction costs (brokerage + FED + slippage + CGT). If "
        "nothing clears the cost-and-edge threshold, the bot will tell "
        "you to stay patient — cash is a position. <b>Buy near</b> = "
        "the recent close used as the anchor; treat it as a guide, not "
        "a limit. <b>Stop loss</b> = where the thesis is wrong; close "
        "the trade if hit. <b>Target</b> = where to take profit if the "
        "5-day forecast plays out."
    )
    if not sym:
        story.append(Paragraph(
            "<b>Stay patient.</b> No high-conviction setups today.",
            sty["callout_amber"],
        ))
        if action.get("reason"):
            story.append(Paragraph(action["reason"], sty["body_muted"]))
        return

    word = action.get("conviction") or "MEDIUM"
    net = action.get("net")
    net_str = f"{net:+.2f}%" if isinstance(net, (int, float)) else "n/a"
    headline = (
        f"<b>{sym}</b> — {action.get('action', '?')}  "
        f"&nbsp;<font color='{_TEXT_MUTED.hexval()}'>"
        f"({word.lower()} conviction)</font>"
    )
    story.append(Paragraph(headline, sty["callout_green"]))
    story.append(Paragraph(
        f"Expected net return next 5 days: <b>{net_str}</b>",
        sty["body"],
    ))

    rows = [[
        "Buy near", "Stop loss", "Target",
    ], [
        _safe_str(action.get("entry")),
        _safe_str(action.get("stop")),
        _safe_str(action.get("target")),
    ]]
    t = Table(rows, colWidths=[55 * mm, 55 * mm, 55 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BG_BAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND_BLUE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
    ]))
    story.append(t)

    if action.get("reason"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(action["reason"], sty["body_muted"]))


def _alerts_section(story: list, sty: dict, alerts: list) -> None:
    if not alerts:
        return
    story.append(Paragraph("Things to watch", sty["h2"]))
    _explain(story, sty,
        "Risk warnings the system has flagged for the next few sessions. "
        "Red dots are blackouts (don't open new positions on those "
        "names — they're about to report results, and earnings days "
        "typically produce 5–10% gaps that destroy short-term forecasts). "
        "Lighter dots are softer cautions worth knowing."
    )
    for a in alerts[:8]:
        st = sty["callout_red"] if a.get("level") == "warning" else sty["body"]
        bullet = "● " if a.get("level") == "warning" else "○ "
        story.append(Paragraph(bullet + (a.get("text") or ""), st))


def _forecast_section(story: list, sty: dict, brief: dict) -> None:
    preds = (brief.get("predictions") or {}).get("predictions") or []
    if not preds:
        return
    story.append(Paragraph("Forecast — next 5 trading days", sty["h2"]))
    _explain(story, sty,
        "Every stock in the bot's universe gets a fresh 5-trading-day "
        "forecast every morning, blending price action, fundamentals, "
        "intrinsic value, quality, earnings momentum, FIPI flows, "
        "global overnight cues, news sentiment, and an LLM strategist "
        "on top. <b>Action</b>: BUY/ADD = open or grow a position; "
        "HOLD = sit tight; AVOID/TRIM/SELL = stay away or reduce. "
        "<b>Direction</b>: where the bot thinks the 5-day price will "
        "go. <b>Conviction</b>: how confident (LOW / MEDIUM / HIGH). "
        "<b>Net 5d %</b>: expected return after estimated transaction "
        "costs — if it's negative or near zero, the trade isn't worth "
        "the cost. Green Action cells are tradable today; red are not."
    )

    header = ["Symbol", "Action", "Direction", "Conviction",
              "Entry", "Stop", "Target", "Net 5d %"]
    rows: list[list[str]] = [header]
    # Sort actionable first, then defensive
    def _rank(p: dict) -> int:
        a = (p.get("suggested_action") or "").upper()
        if a in ("BUY", "ADD"):
            return 0
        if a == "HOLD":
            return 1
        return 2
    for p in sorted(preds, key=lambda p: (_rank(p), p.get("symbol", ""))):
        net = p.get("expected_net_5d_pct")
        net_str = f"{net:+.2f}%" if isinstance(net, (int, float)) else "—"
        rows.append([
            str(p.get("symbol", "")),
            str(p.get("suggested_action", "—")),
            str(p.get("direction", "—")),
            str(p.get("conviction", "—")),
            _safe_str(p.get("entry_price_pkr")),
            _safe_str(p.get("suggested_stop_pkr")
                      or p.get("stop_loss_pkr")),
            _safe_str(p.get("suggested_target_pkr")
                      or p.get("target_price_pkr")),
            net_str,
        ])
    t = Table(rows, colWidths=[18*mm, 18*mm, 22*mm, 22*mm,
                                18*mm, 18*mm, 20*mm, 22*mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 0), (3, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _BG_BAND]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    # Color action cells
    for i, row in enumerate(rows[1:], start=1):
        action = row[1].upper()
        if action in ("BUY", "ADD"):
            style.append(("TEXTCOLOR", (1, i), (1, i), _BRAND_GREEN))
            style.append(("FONTNAME", (1, i), (1, i), "Helvetica-Bold"))
        elif action in ("SELL", "AVOID", "TRIM"):
            style.append(("TEXTCOLOR", (1, i), (1, i), _BRAND_RED))
        # Color net %
        try:
            net_val = float(row[7].rstrip("%"))
            cell_color = (_BRAND_GREEN if net_val > 0
                          else _BRAND_RED if net_val < 0 else _TEXT_MUTED)
            style.append(("TEXTCOLOR", (7, i), (7, i), cell_color))
        except (ValueError, AttributeError):
            pass
    t.setStyle(TableStyle(style))
    story.append(t)


def _portfolio_section(story: list, sty: dict, brief: dict) -> None:
    pf = brief.get("portfolio") or {}
    js = brief.get("journal_stats") or {}
    if pf.get("position_count", 0) == 0 and not pf.get("positions"):
        story.append(Paragraph("Your portfolio", sty["h2"]))
        story.append(Paragraph(
            "<i>No positions yet. Add holdings under My Holdings to see "
            "P&L tracking here.</i>",
            sty["body_muted"],
        ))
        return

    story.append(Paragraph("Your portfolio", sty["h2"]))
    _explain(story, sty,
        "Live snapshot of the holdings you've added. <b>Live value</b> "
        "= what your shares are worth right now (last close × quantity). "
        "<b>Cost</b> = what you paid in total. <b>Unrealized P&amp;L</b> "
        "= the paper gain/loss on positions you still hold; it becomes "
        "realised only when you close them. <b>Closed trades</b> below "
        "is your trading-journal track record (win rate + realised "
        "PKR P&amp;L) — useful for spotting whether you actually book "
        "winners or hang on too long."
    )
    pnl = pf.get("total_unrealized_pnl_pkr") or 0
    ret = pf.get("total_unrealized_pnl_pct")
    cost = pf.get("total_cost_pkr") or 0
    mv = pf.get("total_market_value_pkr") or 0
    color = _BRAND_GREEN if pnl >= 0 else _BRAND_RED
    story.append(Paragraph(
        f"Live value: <b>{mv:,.0f} PKR</b>  &nbsp;·&nbsp;  "
        f"cost: {cost:,.0f} PKR  &nbsp;·&nbsp;  "
        f"unrealized P&amp;L: "
        f"<font color='{color.hexval()}'><b>"
        f"{pnl:+,.0f} PKR ({ret:+.2f}%)</b></font>"
        if ret is not None else
        f"Live value: <b>{mv:,.0f} PKR</b>  &nbsp;·&nbsp;  "
        f"unrealized P&amp;L: <font color='{color.hexval()}'>"
        f"<b>{pnl:+,.0f} PKR</b></font>",
        sty["body"],
    ))
    if js.get("count"):
        story.append(Paragraph(
            f"Closed trades to date: {js.get('count')} ·  "
            f"win rate {js.get('win_rate_pct', 0):.0f}% ·  "
            f"realized P&amp;L {js.get('total_pnl_pkr', 0):+,.0f} PKR",
            sty["body_muted"],
        ))

    positions = pf.get("positions") or []
    if not positions:
        return
    rows = [["Symbol", "Qty", "Avg cost", "Last", "Mkt value", "P&L"]]
    for p in positions:
        pnl_p = p.get("unrealized_pnl_pkr") or 0
        avg = (p.get("entry_price_pkr") or p.get("avg_cost_pkr") or 0)
        last = (p.get("current_price_pkr") or p.get("last_price_pkr") or 0)
        rows.append([
            str(p.get("symbol", "")),
            f"{p.get('quantity', 0):,.0f}",
            f"{avg:,.2f}",
            f"{last:,.2f}",
            f"{p.get('market_value_pkr', 0):,.0f}",
            f"{pnl_p:+,.0f}",
        ])
    t = Table(rows, colWidths=[22*mm, 22*mm, 28*mm, 28*mm, 30*mm, 30*mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _BG_BAND]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, row in enumerate(rows[1:], start=1):
        try:
            pnl_v = float(row[5].replace(",", ""))
            cell_color = _BRAND_GREEN if pnl_v >= 0 else _BRAND_RED
            style.append(("TEXTCOLOR", (5, i), (5, i), cell_color))
        except ValueError:
            pass
    t.setStyle(TableStyle(style))
    story.append(t)


def _watchlist_section(story: list, sty: dict) -> None:
    try:
        from ui.watchlist import load_watchlist
        wl = load_watchlist() or {}
    except Exception:
        return
    items = wl.get("items") or []
    if not items:
        return
    story.append(Paragraph("Watchlist", sty["h2"]))
    _explain(story, sty,
        "Stocks you're tracking but don't yet own, plus any price "
        "alerts you've set. The <b>Action</b> column reuses today's "
        "5-day forecast so you can see at a glance whether each "
        "watched name is currently a BUY, HOLD, or AVOID. Use this "
        "to decide which alerts to actually act on."
    )
    rows = [["Symbol", "Target", "Note"]]
    for w in items:
        rows.append([
            str(w.get("symbol", "")),
            _safe_str(w.get("target_price")),
            (w.get("note") or "")[:60],
        ])
    t = Table(rows, colWidths=[25*mm, 30*mm, 105*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _BG_BAND]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)


def _quality_section(story: list, sty: dict, brief: dict) -> None:
    qb = brief.get("quality_book") or {}
    leaders = (qb.get("leaders") or qb.get("ranking")
               or qb.get("rows") or [])
    if not leaders:
        return
    leaders = sorted(
        [q for q in leaders if q.get("quality_score") is not None],
        key=lambda q: q["quality_score"], reverse=True,
    )
    if not leaders:
        return
    story.append(Paragraph("Quality leaders (highest-quality businesses)",
                            sty["h2"]))
    _explain(story, sty,
        "A quality score (0–100) blending profitability (ROE), "
        "leverage (debt-to-equity), earnings stability, and dividend "
        "consistency. <b>Why this matters</b>: high-quality businesses "
        "fall less in panics and recover faster. Bands: A = best, "
        "B = good, C = average, D = stretched. The forecasts for "
        "names in the A/B band are weighted more conservatively when "
        "size-position recommendations are computed."
    )
    rows = [["Symbol", "Sector", "Score / 100", "Band", "ROE %", "D/E"]]
    for q in leaders[:8]:
        comps = q.get("components") or {}
        rows.append([
            str(q.get("symbol", "")),
            str(q.get("sector", ""))[:18],
            _safe_str(q.get("quality_score") or q.get("score")),
            str(q.get("band", "—")),
            _safe_str((comps.get("profitability") or {}).get("value")),
            _safe_str((comps.get("leverage") or {}).get("value")),
        ])
    t = Table(rows, colWidths=[20*mm, 35*mm, 25*mm, 22*mm, 22*mm, 22*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _BG_BAND]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)


def _calendar_section(story: list, sty: dict, brief: dict) -> None:
    cal = brief.get("earnings_calendar") or {}
    upcoming = cal.get("upcoming") or []
    blackouts = cal.get("blackout_now") or []
    if not (upcoming or blackouts):
        return
    story.append(Paragraph("Earnings calendar (next 21 days)", sty["h2"]))
    _explain(story, sty,
        "Upcoming results announcements. The <b>day a company reports</b> "
        "and the 1–2 days around it routinely produce 5–10% gaps that "
        "no short-term model can predict — so any name within ~3 trading "
        "days of its release date is automatically <b>blackout</b> "
        "(don't open new positions). Confidence reflects whether the "
        "date came from the company itself, a broker, or a heuristic "
        "based on prior years."
    )
    if blackouts:
        story.append(Paragraph(
            "<b>Blackout — do NOT open new positions on:</b> "
            + ", ".join([b.get("symbol", "") for b in blackouts]),
            sty["callout_red"],
        ))
    if upcoming:
        rows = [["Symbol", "Predicted date", "Days until",
                 "Confidence", "Source"]]
        for ev in upcoming[:10]:
            rows.append([
                str(ev.get("symbol", "")),
                str(ev.get("next_event_date_utc", "—"))[:10],
                _safe_str(ev.get("days_until")),
                str(ev.get("confidence", "—")),
                str(ev.get("source", "—")),
            ])
        t = Table(rows, colWidths=[22*mm, 35*mm, 22*mm, 25*mm, 30*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, _BG_BAND]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)


def _macro_radar_section(story: list, sty: dict, brief: dict) -> None:
    """Render the macro radar: industry-KPI snapshot, active drivers,
    and per-sector tailwind / headwind verdicts.

    The analyst's repeated request was that every BUY / SELL the bot
    surfaces must be defended with a specific reason. This section shows
    the macro layer of that defence: what's moving today, which sectors
    benefit, which sectors get hurt, and the live numeric KPIs (T-bill
    3M, KIBOR 3M, FX reserves, KSE-100, CPI YoY) that drive the rule
    book in ``brain/macro_impact.py``.
    """
    mi = brief.get("macro_impact") or {}
    if not mi or mi.get("error"):
        return
    drivers = mi.get("drivers") or []
    by_sector = mi.get("by_sector") or {}
    kpis = mi.get("kpis") or {}

    story.append(Paragraph("Macro Radar — today's sector winners & losers",
                            sty["h2"]))
    _explain(story, sty,
        "How today's macro environment reads across PSX sectors. The bot "
        "tracks twelve macro variables (policy rate, Brent, USD/PKR, "
        "gold, copper, cotton, coal proxy, T-bill 3M, KIBOR 3M, FX "
        "reserves, KSE-100, CPI) and translates each move into a "
        "<b>signed score</b> for every sector via a hand-crafted rule "
        "book. Every score line carries a one-sentence reason so the "
        "analyst can audit the call. <b>Tailwind</b> = positive macro "
        "backdrop for that sector; <b>Headwind</b> = negative."
    )

    # ---- Industry KPI table -------------------------------------------
    if kpis:
        kpi_rows = [["Indicator", "Current", "Change", "Read"]]
        if kpis.get("tbill_3m_pct") is not None:
            chg = kpis.get("tbill_3m_change_5d")
            kpi_rows.append([
                "T-bill 3M cut-off", f"{kpis['tbill_3m_pct']:.2f}%",
                f"{chg*100:+.0f} bps (5d)" if chg is not None else "—",
                "Money-market yield (banking proxy)",
            ])
        if kpis.get("kibor_3m_pct") is not None:
            chg = kpis.get("kibor_3m_change_5d")
            kpi_rows.append([
                "KIBOR 3M", f"{kpis['kibor_3m_pct']:.2f}%",
                f"{chg*100:+.0f} bps (5d)" if chg is not None else "—",
                "Floating-rate loan benchmark",
            ])
        if kpis.get("reserves_sbp_usd_mn") is not None:
            chg = kpis.get("reserves_change_30d")
            kpi_rows.append([
                "SBP FX reserves",
                f"USD {kpis['reserves_sbp_usd_mn']/1000:.1f} bn",
                f"{chg/1000:+.1f} bn (30d)" if chg is not None else "—",
                "BoP stress signal",
            ])
        if kpis.get("kse100_close") is not None:
            r5 = kpis.get("kse100_ret_5d")
            kpi_rows.append([
                "KSE-100", f"{kpis['kse100_close']:,.0f}",
                f"{r5*100:+.1f}% (5d)" if r5 is not None else "—",
                "Broad-market regime",
            ])
        if kpis.get("cpi_yoy_pct") is not None:
            period = kpis.get("cpi_period") or ""
            kpi_rows.append([
                f"CPI YoY ({period})" if period else "CPI YoY",
                f"{kpis['cpi_yoy_pct']:.1f}%",
                "—",
                "Inflation regime / real-rate signal",
            ])
        if len(kpi_rows) > 1:
            kt = Table(kpi_rows, colWidths=[40*mm, 30*mm, 35*mm, 65*mm])
            kt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 1), (2, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, _BG_BAND]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(kt)
            story.append(Spacer(1, 4))

    # ---- Active drivers -----------------------------------------------
    if drivers:
        d_rows = [["Driver", "Magnitude", "Move", "Context"]]
        for d in drivers[:8]:
            d_rows.append([
                str(d.get("name", "")),
                str(d.get("magnitude", "")),
                str(d.get("move", "")),
                (str(d.get("context", "") or ""))[:120],
            ])
        dt = Table(d_rows, colWidths=[35*mm, 22*mm, 38*mm, 75*mm])
        dt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BG_BAND),
            ("TEXTCOLOR", (0, 0), (-1, 0), _BRAND_BLUE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(Paragraph(
            f"<b>Active macro drivers ({len(drivers)})</b>", sty["body"]))
        story.append(dt)
        story.append(Spacer(1, 4))

    # ---- Sector verdicts ----------------------------------------------
    if by_sector:
        s_rows = [["Sector", "Score", "Verdict", "Top reason"]]
        for sec, v in sorted(by_sector.items(),
                              key=lambda kv: -(kv[1].get("score") or 0)):
            score = int(v.get("score") or 0)
            verdict = v.get("verdict") or "NEUTRAL"
            top = (v.get("tailwinds") or [None])[0] if score > 0 else \
                   (v.get("headwinds") or [None])[0] if score < 0 else None
            top_str = (top or "—")[:130]
            s_rows.append([sec, f"{score:+d}", verdict, top_str])
        st_table = Table(s_rows, colWidths=[34*mm, 18*mm, 30*mm, 88*mm])
        s_style = [
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, _BG_BAND]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]
        for i, r in enumerate(s_rows[1:], start=1):
            try:
                sc = int(r[1])
                if sc >= 1:
                    s_style.append(("TEXTCOLOR", (1, i), (1, i), _BRAND_GREEN))
                    s_style.append(("FONTNAME", (1, i), (1, i),
                                    "Helvetica-Bold"))
                elif sc <= -1:
                    s_style.append(("TEXTCOLOR", (1, i), (1, i), _BRAND_RED))
                    s_style.append(("FONTNAME", (1, i), (1, i),
                                    "Helvetica-Bold"))
            except (ValueError, TypeError):
                pass
        st_table.setStyle(TableStyle(s_style))
        story.append(Paragraph("<b>Sector verdicts</b>", sty["body"]))
        story.append(st_table)
    story.append(Spacer(1, 6))


def _bots_verdict_section(story: list, sty: dict) -> None:
    """Render the unified verdict synthesizer's output as a one-page
    PDF section.

    The analyst's complaint was that different tabs gave different calls
    on the same name (Value SELL vs Momentum BUY). This section runs
    ``brain.verdict_synthesizer.synthesize_universe`` and prints the
    single, conflict-resolved verdict per ticker — plus, for any name
    where lenses disagree, the explicit resolution rule that was
    applied. The deterministic synthesizer is independent of the LLM
    strategist, so this section will populate even when the LLM call
    fails.
    """
    try:
        from brain.verdict_synthesizer import synthesize_universe
        out = synthesize_universe()
    except Exception:
        return
    rows = out.get("rows") or []
    if not rows:
        return

    story.append(PageBreak())
    story.append(Paragraph("The Bot's Verdict — one call per stock",
                            sty["h2"]))
    _explain(story, sty,
        "A single, conflict-resolved verdict per universe stock that "
        "blends <b>seven lenses</b> (Value, Quality, Momentum, Macro, "
        "News, Flow, Management). Each lens contributes a signed score "
        "in [-3 .. +3]; the weighted sum drives the action. When two "
        "lenses disagree sharply (e.g. Value SELL vs Momentum BUY), "
        "an explicit hand-crafted rule resolves the conflict and the "
        "rule is documented in plain English. <b>This is the answer "
        "to use when individual tabs seem to tell different stories — "
        "the synthesiser already did the reconciliation.</b>")

    # ----- Universe-wide ranking table ---------------------------------
    head = ["Symbol", "Sector", "Action", "Conviction", "Score",
            "Conflicts", "Notes"]
    body = [head]
    any_concentration = False
    for r in rows:
        if r.get("concentration_warning"):
            any_concentration = True
        body.append([
            r["symbol"], r.get("sector") or "—",
            r["action"], r["conviction"],
            f"{r['score']:+d}",
            str(len(r.get("conflicts") or [])),
            ("conc. cap" if r.get("concentration_warning") else ""),
        ])
    t = Table(body,
                colWidths=[16*mm, 32*mm, 18*mm, 22*mm, 14*mm, 16*mm, 20*mm])
    style = [
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",   (0, 0), (-1, -1), 8),
        ("GRID",       (0, 0), (-1, -1), 0.3,
            colors.HexColor("#cfd6e4")),
        ("ALIGN",      (2, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(rows, start=1):
        if r["action"] in ("BUY", "ADD"):
            style.append(("TEXTCOLOR", (2, i), (2, i), _BRAND_GREEN))
            style.append(("FONTNAME",  (2, i), (2, i),
                            "Helvetica-Bold"))
        elif r["action"] in ("AVOID", "TRIM", "SELL"):
            style.append(("TEXTCOLOR", (2, i), (2, i), _BRAND_RED))
            style.append(("FONTNAME",  (2, i), (2, i),
                            "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    story.append(t)
    if any_concentration:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "<b>Concentration cap applied.</b> At least one bullish "
            "verdict was downgraded to HOLD because too many names in "
            "the same sector were bullish — the bot prefers diversified "
            "picks over a single-sector bet. See the per-stock "
            "resolution log below.",
            sty["body_muted"]))
    story.append(Spacer(1, 8))

    # ----- Detail blocks for top-3 buys, top-3 avoids, all conflicts ---
    buys   = [r for r in rows if r["action"] in ("BUY", "ADD")][:3]
    avoids = [r for r in rows if r["action"] in ("AVOID", "TRIM")][:3]
    conflicting = [r for r in rows if r.get("conflicts")]

    def _lens_table(v: dict):
        h = ["Lens", "Score", "Reason"]
        b = [h]
        for c in v["contributions"]:
            sc = int(c["score"])
            b.append([c["name"], f"{sc:+d}",
                       c["reason"][:90]])
        tt = Table(b, colWidths=[26*mm, 14*mm, 110*mm])
        st_style = [
            ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0),
                colors.HexColor("#e6ecf6")),
            ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ("GRID",       (0, 0), (-1, -1), 0.25,
                colors.HexColor("#cfd6e4")),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]
        for i, c in enumerate(v["contributions"], start=1):
            sc = int(c["score"])
            if sc >= 1:
                st_style.append(("TEXTCOLOR", (1, i), (1, i),
                                   _BRAND_GREEN))
                st_style.append(("FONTNAME", (1, i), (1, i),
                                   "Helvetica-Bold"))
            elif sc <= -1:
                st_style.append(("TEXTCOLOR", (1, i), (1, i),
                                   _BRAND_RED))
                st_style.append(("FONTNAME", (1, i), (1, i),
                                   "Helvetica-Bold"))
        tt.setStyle(TableStyle(st_style))
        return tt

    def _render_card(v: dict, header: str):
        story.append(Paragraph(
            f"<b>{header}: {v['symbol']}</b> — "
            f"{v['action']} · {v['direction']} · "
            f"{v['conviction']} conviction · score {v['score']:+d}",
            sty["body"]))
        story.append(_lens_table(v))
        if v.get("concentration_warning"):
            story.append(Spacer(1, 3))
            story.append(Paragraph(
                f"<b>Concentration cap:</b> {v['concentration_warning']}",
                sty["body_muted"]))
        if v.get("resolution_log"):
            story.append(Spacer(1, 3))
            for line in v["resolution_log"]:
                story.append(Paragraph(
                    f"<i>Resolution: {line}</i>", sty["body_muted"]))
        story.append(Spacer(1, 6))

    if buys:
        story.append(Paragraph("<b>Top BUY-side verdicts</b>",
                                sty["body"]))
        for v in buys:
            _render_card(v, "BUY")

    if avoids:
        story.append(Paragraph("<b>AVOID / TRIM verdicts</b>",
                                sty["body"]))
        for v in avoids:
            _render_card(v, "AVOID")

    if conflicting:
        story.append(Paragraph(
            f"<b>Names where lenses disagreed "
            f"({len(conflicting)} of {len(rows)})</b>", sty["body"]))
        _explain(story, sty,
            "These are the names where the analyst would say 'the "
            "tabs don't agree'. Each one has been resolved by an "
            "explicit rule below.")
        for v in conflicting[:6]:
            _render_card(v, v["action"])
    story.append(Spacer(1, 6))


def _scored_news_for_symbol(sym: str, limit: int = 3) -> list[dict]:
    """Return the most recent scored news rows that mention `sym` in
    ``affected_symbols``. Sorted newest first."""
    try:
        import pandas as pd
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" / "news" \
            / "scored_news.parquet"
        if not p.exists():
            return []
        df = pd.read_parquet(p)
        if "affected_symbols" not in df.columns:
            return []
        sym_u = sym.upper()
        # affected_symbols is stored as comma-separated strings (sometimes
        # already a list). Normalise both.
        def _hit(v):
            if v is None:
                return False
            if isinstance(v, (list, tuple)):
                return sym_u in {str(x).upper() for x in v}
            if isinstance(v, str):
                return sym_u in {x.strip().upper()
                                  for x in v.split(",") if x.strip()}
            return False
        df = df[df["affected_symbols"].apply(_hit)]
        if df.empty:
            return []
        df = df.sort_values("scored_at", ascending=False).head(limit)
        return df.to_dict("records")
    except Exception:
        return []


def _material_info_for_symbol(sym: str, days: int = 14) -> list[dict]:
    """Return Material Information disclosures for `sym` filed in the
    last `days` calendar days."""
    try:
        import pandas as pd
        from datetime import datetime, timedelta
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" \
            / "material_information.parquet"
        if not p.exists():
            return []
        df = pd.read_parquet(p)
        if df.empty or "symbol" not in df.columns:
            return []
        df = df[df["symbol"].astype(str).str.upper() == sym.upper()]
        if "date" in df.columns and not df.empty:
            cutoff = datetime.now().date() - timedelta(days=days)
            df = df[pd.to_datetime(df["date"]).dt.date >= cutoff]
        if df.empty:
            return []
        return df.sort_values("date", ascending=False).head(5).to_dict("records")
    except Exception:
        return []


def _fundamentals_snapshot(sym: str) -> dict:
    """Return P/E, P/B, dividend yield, payout ratio for `sym` plus the
    matching sector medians for a 'vs peers' read."""
    try:
        from connectors.yfinance_fundamentals import load_latest
        from brain.sector_ratios import load_sector_medians
        f = load_latest(sym) or {}
        sec_meds = (load_sector_medians() or {}).get("by_sector") or {}
        sec = f.get("sector") or ""
        meds = sec_meds.get(sec, {}) or {}
        return {
            "sector":       sec,
            "pe":           f.get("pe_ratio"),
            "pe_med":       meds.get("pe_med"),
            "pb":           f.get("pb_ratio"),
            "pb_med":       meds.get("pb_med"),
            "dy":           f.get("dividend_yield_pct"),
            "dy_med":       meds.get("dy_med"),
            "payout":       f.get("payout_ratio_pct"),
            "payout_med":   meds.get("payout_med"),
            "debt_to_eq":   (round(float(f["total_debt_pkr"])
                                    / float(f["total_equity_pkr"]), 2)
                              if (f.get("total_debt_pkr") is not None
                                   and f.get("total_equity_pkr"))
                              else None),
        }
    except Exception:
        return {}


def _short_ideas_section(story: list, sty: dict) -> None:
    """Render the bot's short-side picks as a one-page section.

    Pulls :func:`brain.short_candidates.rank_shorts` and prints the
    candidates above the watch threshold with the eligibility
    disclaimer at the top. Mirrors the long-side verdict section
    layout so the analyst can compare bull vs bear ideas at a
    glance.
    """
    try:
        from brain.short_candidates import rank_shorts
        out = rank_shorts(min_conviction="LOW", max_results=15)
    except Exception:
        return
    rows = out.get("candidates") or []

    story.append(PageBreak())
    story.append(Paragraph("Short Ideas — what the bot expects to fall",
                            sty["h2"]))
    _explain(story, sty,
        "PSX names ranked by a composite <b>short_score (0-100)</b> "
        "combining the verdict synthesizer's bearish lean, BEARISH "
        "5-day predictions, negative news sentiment, technical "
        "breakdown patterns, sector macro headwinds, and intraday "
        "lower-circuit hits. <b>Read the eligibility note before "
        "acting</b> — Pakistan retail shorting works only via "
        "Single Stock Futures or NCCPL Securities Lending & "
        "Borrowing and the eligible list changes monthly.")

    # Disclaimer + regime banner
    disclaimer = out.get("disclaimer", "")
    if disclaimer:
        story.append(Paragraph(f"<b>Disclaimer.</b> {disclaimer}",
                                sty["body_muted"]))
        story.append(Spacer(1, 4))
    regime = out.get("regime") or {}
    rname = regime.get("regime", "?")
    rnote = regime.get("note", "")
    if regime.get("shorts_aligned"):
        story.append(Paragraph(
            f"<b>Regime: {rname}.</b> {rnote}", sty["body_muted"]))
    else:
        story.append(Paragraph(
            f"<b>Regime: {rname} — hostile to shorts.</b> {rnote}",
            sty["callout_red"]
            if "callout_red" in sty else sty["body_muted"]))
    story.append(Spacer(1, 6))

    if not rows:
        story.append(Paragraph(
            "<b>No short candidates today.</b> Either the bot's "
            "signals are not pointing bearish on any universe name "
            "or every name is below the watch-list cutoff. Check "
            "this section again tomorrow morning.",
            sty["body"]))
        return

    head = ["Symbol", "Sector", "Score", "Conviction",
            "5d pred", "Top driver", "Eligible?"]
    body = [head]
    for r in rows:
        pred = r.get("predicted_return_5d_pct")
        body.append([
            r["symbol"], r.get("sector") or "—",
            f"{r['short_score']:.0f}",
            r.get("conviction") or "LOW",
            f"{pred:+.1f}%" if pred is not None else "—",
            ((r.get("drivers") or ["—"])[0])[:60],
            ("Likely" if r.get("eligibility", {})
                .get("likely_eligible") else "Verify"),
        ])
    t = Table(body,
                colWidths=[16*mm, 32*mm, 14*mm, 22*mm, 16*mm,
                            66*mm, 16*mm])
    style = [
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTSIZE",   (0, 0), (-1, -1), 8),
        ("GRID",       (0, 0), (-1, -1), 0.3,
            colors.HexColor("#cfd6e4")),
        ("ALIGN",      (2, 0), (4, -1), "CENTER"),
        ("ALIGN",      (6, 0), (6, -1), "CENTER"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, r in enumerate(rows, start=1):
        conv = (r.get("conviction") or "LOW").upper()
        if conv == "HIGH":
            style.append(("TEXTCOLOR", (3, i), (3, i), _BRAND_RED))
            style.append(("FONTNAME",  (3, i), (3, i),
                            "Helvetica-Bold"))
        elif conv == "MEDIUM":
            style.append(("TEXTCOLOR", (3, i), (3, i),
                            colors.HexColor("#9a3412")))
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 6))

    # Top-2 drill-down cards
    for r in rows[:2]:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>{r['symbol']}</b> — {r.get('sector') or '—'} · "
            f"score {r['short_score']:.0f} · "
            f"{r.get('conviction')} conviction",
            sty["h3"] if "h3" in sty else sty["body"]))
        if r.get("suggested_entry_pkr"):
            story.append(Paragraph(
                f"Suggested geometry — entry "
                f"<b>{r['suggested_entry_pkr']:.2f}</b>, "
                f"stop <b>{r['suggested_stop_pkr']:.2f}</b>, "
                f"target <b>{r['suggested_target_pkr']:.2f}</b> "
                f"(R/R {r.get('risk_reward', 0):.2f}).",
                sty["body"]))
        for d in (r.get("drivers") or [])[:4]:
            story.append(Paragraph(f"&bull; {d}", sty["body_muted"]))
        if r.get("concentration_warning"):
            story.append(Paragraph(
                f"<b>Concentration cap:</b> "
                f"{r['concentration_warning']}",
                sty["body_muted"]))
        elig = r.get("eligibility") or {}
        for n in (elig.get("notes") or [])[:2]:
            story.append(Paragraph(f"<i>{n}</i>", sty["body_muted"]))


def _per_stock_detail_section(story: list, sty: dict, brief: dict) -> None:
    """Render one detail card per universe stock — the analyst-facing
    deep dive that explains *why* the forecast came out the way it did.

    For each stock we surface: rationale, key drivers, key risks,
    macro impact (sector + amplifier), latest scored news, recent
    material information, and fundamentals vs sector medians.
    """
    preds = (brief.get("predictions") or {}).get("predictions") or []
    if not preds:
        return
    mi = brief.get("macro_impact") or {}
    by_symbol = mi.get("by_symbol") or {}
    by_sector = mi.get("by_sector") or {}

    story.append(PageBreak())
    story.append(Paragraph("Per-stock detail", sty["h2"]))
    _explain(story, sty,
        "One card per stock with the full reasoning behind today's "
        "forecast. The table on the previous page tells you <i>what</i> "
        "the bot recommends; this section tells you <i>why</i>. Each "
        "card stitches together the LLM rationale, the deterministic "
        "macro engine's sector and stock-level reading, the latest "
        "scored news headlines that mention the ticker, any Material "
        "Information disclosures filed on PSX in the last 14 days, and "
        "the fundamental ratios benchmarked against the sector median. "
        "If a recommendation looks surprising, the reason is on this "
        "page."
    )

    def _action_color(action: str) -> colors.Color:
        a = (action or "").upper()
        if a in ("BUY", "ADD"):
            return _BRAND_GREEN
        if a in ("SELL", "AVOID", "TRIM"):
            return _BRAND_RED
        return _TEXT_MUTED

    # Sort BUY/ADD first then HOLD then AVOID/SELL
    def _rank(p: dict) -> int:
        a = (p.get("suggested_action") or "").upper()
        if a in ("BUY", "ADD"): return 0
        if a == "HOLD":         return 1
        return 2

    for p in sorted(preds, key=lambda x: (_rank(x),
                                           x.get("symbol", ""))):
        sym = (p.get("symbol") or "").upper()
        if not sym:
            continue
        action = (p.get("suggested_action") or "—").upper()
        direction = p.get("direction") or "—"
        conviction = p.get("conviction") or "—"
        net5 = p.get("expected_net_5d_pct")
        mid5 = p.get("expected_return_5d_mid_pct")
        net_str = (f"{net5:+.2f}%" if isinstance(net5, (int, float))
                   else "—")
        mid_str = (f"{mid5:+.1f}%" if isinstance(mid5, (int, float))
                   else "—")
        sym_block = by_symbol.get(sym) or {}
        sector = sym_block.get("sector") or ""
        sec_block = by_sector.get(sector) or {}
        ac = _action_color(action)

        # ---- Card header --------------------------------------------
        head_html = (
            f"<b>{sym}</b>"
            f"  <font color='{_TEXT_MUTED.hexval()}'>· {sector}</font>  "
            f"&nbsp;·&nbsp; <font color='{ac.hexval()}'>"
            f"<b>{action}</b></font>  "
            f"({direction.lower()}, {conviction.lower()} conviction)  "
            f"&nbsp;·&nbsp; net 5d <b>{net_str}</b> "
            f"(mid {mid_str})"
        )
        story.append(Paragraph(head_html, sty["body"]))

        entry = _safe_str(p.get("entry_price_pkr"))
        stop  = _safe_str(p.get("suggested_stop_pkr")
                          or p.get("stop_loss_pkr"))
        target = _safe_str(p.get("suggested_target_pkr")
                           or p.get("target_price_pkr"))
        story.append(Paragraph(
            f"<font color='{_TEXT_MUTED.hexval()}'>"
            f"Entry near {entry} PKR  ·  Stop {stop}  ·  "
            f"Target {target}</font>",
            sty["body_muted"]))

        # ---- Rationale ----------------------------------------------
        rat = (p.get("rationale") or "").strip()
        if rat:
            # Don't hard-truncate — analysts want the full LLM
            # reasoning. ReportLab will reflow naturally.
            story.append(Paragraph(f"<b>Rationale.</b> {rat}",
                                     sty["body"]))

        # ---- Key drivers / risks ------------------------------------
        kd = p.get("key_drivers") or []
        kr = p.get("key_risks") or []
        if kd:
            story.append(Paragraph("<b>Key drivers</b>", sty["body"]))
            for d in kd[:4]:
                story.append(Paragraph(f"• {d}", sty["body_muted"]))
        if kr:
            story.append(Paragraph("<b>Key risks</b>", sty["body"]))
            for r in kr[:4]:
                story.append(Paragraph(f"• {r}", sty["body_muted"]))

        # ---- Critic self-review notes -------------------------------
        # When the deterministic critic catches a logic gap (BULLISH
        # call with bearish drivers, inverted stop/target geometry, or
        # sharp disagreement with the seven-lens synthesizer), the
        # downgrade or rewrite is documented here so the analyst can
        # audit exactly what was caught and how it was handled.
        cn = p.get("critic_notes") or []
        if cn:
            story.append(Paragraph(
                "<b>Critic self-review</b> "
                "<font color='#888'>"
                "(post-checks that adjusted the call):</font>",
                sty["body"]))
            for note in cn[:4]:
                story.append(Paragraph(f"• {note}",
                                          sty["body_muted"]))

        # ---- MPC alert badge ----------------------------------------
        if p.get("mpc_cap_applied"):
            ms = p.get("mpc_alert") or {}
            story.append(Paragraph(
                f"<b>MPC alert applied.</b> "
                f"<font color='#c08030'>Conviction was capped one "
                f"notch — SBP MPC meets on {ms.get('next_mpc')} "
                f"({ms.get('days_until')} day(s) away) and this "
                f"stock's sector is rate-sensitive.</font>",
                sty["body_muted"]))

        # ---- Macro reading ------------------------------------------
        if sym_block or sec_block:
            sec_score = sec_block.get("score") or 0
            sec_verdict = sec_block.get("verdict") or "NEUTRAL"
            stock_verdict = sym_block.get("verdict") or "NEUTRAL"
            stock_score = sym_block.get("stock_score") or 0
            sec_color = (_BRAND_GREEN if sec_score > 0
                          else _BRAND_RED if sec_score < 0 else _TEXT_MUTED)
            stk_color = (_BRAND_GREEN if stock_score > 0
                          else _BRAND_RED if stock_score < 0 else _TEXT_MUTED)
            story.append(Paragraph(
                f"<b>Macro reading.</b> Sector ({sector}): "
                f"<font color='{sec_color.hexval()}'>{sec_verdict} "
                f"({sec_score:+d})</font>. Stock: "
                f"<font color='{stk_color.hexval()}'>{stock_verdict} "
                f"({stock_score:+d})</font>.",
                sty["body"]))
            for line in (sec_block.get("tailwinds") or [])[:2]:
                story.append(Paragraph(f"+ {line}", sty["body_muted"]))
            for line in (sec_block.get("headwinds") or [])[:2]:
                story.append(Paragraph(f"− {line}", sty["body_muted"]))
            if sym_block.get("amplifier_note"):
                story.append(Paragraph(
                    f"<i>Stock amplifier:</i> "
                    f"{sym_block['amplifier_note']}",
                    sty["body_muted"]))

        # ---- Recent news --------------------------------------------
        news = _scored_news_for_symbol(sym, limit=3)
        if news:
            story.append(Paragraph("<b>Recent news</b>", sty["body"]))
            for n in news:
                s = n.get("sentiment")
                s_str = (f"{s:+.2f}" if isinstance(s, (int, float))
                         else "—")
                s_color = (_BRAND_GREEN if isinstance(s, (int, float))
                                            and s > 0.1
                            else _BRAND_RED if isinstance(s, (int, float))
                                                and s < -0.1
                            else _TEXT_MUTED)
                cat = n.get("category") or ""
                src = n.get("source") or ""
                title = (n.get("title") or "")[:140]
                one = (n.get("one_liner") or "")[:200]
                story.append(Paragraph(
                    f"<font color='{s_color.hexval()}'>● {s_str}</font>  "
                    f"<font color='{_TEXT_MUTED.hexval()}'>[{cat} · "
                    f"{src}]</font>  {title}",
                    sty["body_muted"]))
                if one:
                    story.append(Paragraph(
                        f"<font color='{_TEXT_MUTED.hexval()}'>"
                        f"&nbsp;&nbsp;{one}</font>",
                        sty["body_muted"]))

        # ---- Material Information -----------------------------------
        mat = _material_info_for_symbol(sym, days=14)
        if mat:
            story.append(Paragraph("<b>Recent Material Information</b>",
                                     sty["body"]))
            for m in mat[:3]:
                date = str(m.get("date") or "")[:10]
                title = (m.get("title") or m.get("subject") or "")[:200]
                story.append(Paragraph(
                    f"• {date} — {title}", sty["body_muted"]))

        # ---- Fundamentals vs sector ---------------------------------
        f = _fundamentals_snapshot(sym)
        if f and (f.get("pe") is not None or f.get("pb") is not None):
            def _vs(v, m, fmt: str = "{:.2f}") -> str:
                if v is None:
                    return "—"
                base = fmt.format(float(v))
                if m is None:
                    return base
                return f"{base} <font color='{_TEXT_MUTED.hexval()}'>" \
                       f"(sector {fmt.format(float(m))})</font>"
            line = (
                f"<b>Ratios vs sector ({f.get('sector')}):</b>  "
                f"P/E {_vs(f.get('pe'), f.get('pe_med'))}  ·  "
                f"P/B {_vs(f.get('pb'), f.get('pb_med'))}  ·  "
                f"Div Yld {_vs(f.get('dy'), f.get('dy_med'), '{:.1f}%')}  ·  "
                f"Payout "
                f"{_vs(f.get('payout'), f.get('payout_med'), '{:.0f}%')}"
            )
            if f.get("debt_to_eq") is not None:
                line += f"  ·  D/E {f['debt_to_eq']:.2f}"
            story.append(Paragraph(line, sty["body_muted"]))

        story.append(Spacer(1, 8))


def _news_digest_section(story: list, sty: dict) -> None:
    """Top-level news digest — the highest-impact scored articles in
    the last 24 hours, ordered by absolute sentiment magnitude.

    Complementary to the per-stock cards: this surface answers the
    analyst's first question of the morning ("anything important
    happen overnight?") without having to drill into each ticker.
    """
    try:
        import pandas as pd
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "data" / "news" \
            / "scored_news.parquet"
        if not p.exists():
            return
        df = pd.read_parquet(p)
        if df.empty:
            return
        # Last 24 hours by scored_at; fall back to most recent 30 if
        # nothing arrived overnight.
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            df["_ts"] = pd.to_datetime(df["scored_at"], utc=True,
                                          errors="coerce")
            recent = df[df["_ts"] >= cutoff]
        except Exception:
            recent = df.tail(30)
        if recent.empty:
            recent = df.tail(30)

        # Sort by absolute sentiment then confidence (HIGH first).
        conf_rank = {"HIGH": 3, "MED": 2, "LOW": 1}
        recent = recent.assign(
            _abs=recent["sentiment"].abs().fillna(0.0),
            _conf=recent["confidence"].map(conf_rank).fillna(0),
        )
        recent = recent.sort_values(by=["_conf", "_abs"],
                                      ascending=[False, False]).head(8)
    except Exception:
        return

    if recent.empty:
        return
    story.append(Paragraph(
        "Top news in the last 24 hours", sty["h2"]))
    _explain(story, sty,
        "Highest-impact scored articles from Pakistani business "
        "newswires (Mettis Global, Dawn, Tribune, Profit, Business "
        "Recorder), ranked by an LLM-derived <b>sentiment score</b> "
        "from −1.0 (very bearish) to +1.0 (very bullish) and confidence "
        "(HIGH / MED / LOW). The <b>category</b> tag tells you whether "
        "it's a single-stock story, a macro / policy print, a "
        "commodity move, or a global cue. Use this as the morning "
        "scan; the per-stock cards below dive into ticker-specific "
        "items."
    )

    rows = [["Sentiment", "Cat", "Symbols", "Headline", "One-liner"]]
    for _, r in recent.iterrows():
        s = r.get("sentiment")
        s_str = f"{s:+.2f}" if isinstance(s, (int, float)) else "—"
        cat = (r.get("category") or "")[:6]
        syms = (r.get("affected_symbols") or "")
        if isinstance(syms, (list, tuple)):
            syms = ",".join([str(x) for x in syms])
        syms = (syms or "")[:18]
        title = (r.get("title") or "")[:80]
        one = (r.get("one_liner") or "")[:130]
        rows.append([s_str, cat, syms, title, one])
    t = Table(rows, colWidths=[18*mm, 14*mm, 25*mm, 60*mm, 60*mm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, _BG_BAND]),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, r in enumerate(rows[1:], start=1):
        try:
            sv = float(r[0].rstrip("%"))
            color = (_BRAND_GREEN if sv > 0.1
                     else _BRAND_RED if sv < -0.1 else _TEXT_MUTED)
            style.append(("TEXTCOLOR", (0, i), (0, i), color))
            style.append(("FONTNAME", (0, i), (0, i), "Helvetica-Bold"))
        except (ValueError, AttributeError):
            pass
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 6))


def _material_info_section(story: list, sty: dict, brief: dict) -> None:
    """Render Material Information disclosures from PSX for the
    universe in the last 14 days.

    Material information notices are price-sensitive disclosures
    (board meetings, capacity changes, related-party transactions,
    legal cases) that companies are *required* to publish. They often
    move stocks 5-10% in a single session, so they belong in any
    analyst-facing brief.
    """
    mi = brief.get("material_information") or {}
    rows = mi.get("rows") or []
    if not rows:
        return
    story.append(Paragraph(
        "Material Information (last 14 days)", sty["h2"]))
    _explain(story, sty,
        "Price-sensitive disclosures filed by your universe stocks on "
        "PSX. By regulation these are released to the market within 24 "
        "hours of the underlying event and frequently produce 5–10% "
        "single-day moves. <b>Always</b> read this section before "
        "opening a new position — many of the stocks below will be "
        "showing volatility unrelated to the macro / technical setup."
    )
    out = [["Date", "Symbol", "Subject"]]
    for r in rows[:15]:
        out.append([
            str(r.get("date") or "")[:10],
            str(r.get("symbol") or ""),
            (str(r.get("title") or r.get("subject") or ""))[:140],
        ])
    if len(out) > 1:
        t = Table(out, colWidths=[24*mm, 20*mm, 130*mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, _BG_BAND]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(t)


def _management_outlook_section(story: list, sty: dict, brief: dict) -> None:
    """Show the 5 most recent Director's Reports with extracted outlook."""
    mo = brief.get("management_outlook") or {}
    rows = mo.get("rows") or []
    if not rows:
        return
    rows = sorted(rows, key=lambda r: r.get("filing_date") or "",
                   reverse=True)[:5]
    story.append(Paragraph("What management is saying", sty["h2"]))
    _explain(story, sty,
        "The most recent Director's Reports filed by your universe "
        "stocks on PSX, with management's <b>forward-looking commentary</b> "
        "summarised in plain English. These reports come out quarterly + "
        "annually and contain capex plans, expansion announcements, and "
        "self-reported risks &mdash; leading information that doesn't "
        "show up in prices or news for weeks. <b>Tone</b> ranges from "
        "&minus;1 (very bearish guidance) to +1 (very bullish); "
        "<b>strength</b> reflects how concrete the guidance is."
    )
    for r in rows:
        sym = r.get("symbol", "")
        period = r.get("fy_period") or r.get("doc_type") or ""
        date = r.get("filing_date") or ""
        tone = float(r.get("outlook_tone") or 0.0)
        tone_word = ("bullish" if tone > 0.15
                     else "bearish" if tone < -0.15 else "neutral")
        head = (f"<b>{sym}</b> — {period} ({date}) · "
                f"tone {tone:+.2f} ({tone_word}), "
                f"strength {r.get('guidance_strength', '—')}")
        story.append(Paragraph(head, sty["body"]))
        story.append(Paragraph(
            r.get("outlook_summary", "") or "—", sty["body_muted"]))
        plans = r.get("growth_plans") or []
        if plans:
            story.append(Paragraph("<b>Growth plans:</b>", sty["body_muted"]))
            for plan in plans[:4]:
                story.append(Paragraph(f"• {plan}", sty["body_muted"]))
        risks = r.get("risks_mentioned") or []
        if risks:
            story.append(Paragraph("<b>Risks management flagged:</b>",
                                     sty["body_muted"]))
            for risk in risks[:3]:
                story.append(Paragraph(f"• {risk}", sty["body_muted"]))
        story.append(Spacer(1, 4))


def _movers_section(story: list, sty: dict, brief: dict) -> None:
    m = brief.get("universe_movers") or {}
    gainers = m.get("gainers") or []
    losers = m.get("losers") or []
    if not (gainers or losers):
        return
    story.append(Paragraph("Today's biggest moves", sty["h2"]))
    _explain(story, sty,
        "Universe stocks ranked by yesterday's price change. <b>Up most</b> "
        "= biggest 1-day winners; <b>Down most</b> = biggest losers. "
        "Pair this with the Forecast section above: a stock that "
        "ripped 4% yesterday but the model still says BUY is showing "
        "follow-through; a 4% drop with a BUY forecast is a potential "
        "discount. Big moves with no news are often noise; big moves "
        "lined up with the news section below are usually the start "
        "of a trend."
    )

    def _mover_row(items: list) -> list[list[str]]:
        rows: list[list[str]] = [["Symbol", "Last", "1d %", "5d %"]]
        for x in items:
            r1 = x.get("ret_1d_pct")
            r5 = x.get("ret_5d_pct")
            rows.append([
                str(x.get("symbol", "")),
                _safe_str(x.get("close_pkr")),
                f"{r1:+.2f}%" if isinstance(r1, (int, float)) else "—",
                f"{r5:+.2f}%" if isinstance(r5, (int, float)) else "—",
            ])
        return rows

    cells: list[list[Any]] = []
    if gainers:
        cells.append(["Up most", "Down most" if losers else ""])
        gt = Table(_mover_row(gainers), colWidths=[20*mm, 20*mm, 20*mm, 20*mm])
        gt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _BRAND_GREEN),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        if losers:
            lt = Table(_mover_row(losers),
                       colWidths=[20*mm, 20*mm, 20*mm, 20*mm])
            lt.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), _BRAND_RED),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, _GRID),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ]))
            row = Table([[gt, lt]], colWidths=[85*mm, 85*mm])
            row.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]))
            story.append(row)
        else:
            story.append(gt)
    elif losers:
        lt = Table(_mover_row(losers),
                   colWidths=[20*mm, 20*mm, 20*mm, 20*mm])
        story.append(lt)


def _footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(_TEXT_MUTED)
    page = canvas.getPageNumber()
    text = (
        f"PSX Advisor · generated "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')} · "
        f"page {page} · "
        "Educational use only — not investment advice."
    )
    canvas.drawString(15 * mm, 10 * mm, text)
    canvas.restoreState()


# ---------- helpers ----------------------------------------------------
def _safe_str(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


# ---------- public entry point -----------------------------------------
def build_daily_report(
    brief: dict | None = None,
    mood: dict | None = None,
    narrative: str | None = None,
    action: dict | None = None,
    alerts: list | None = None,
) -> bytes:
    """Generate the daily PDF brief and return it as raw bytes.

    All arguments are optional — if any is missing, this function will
    pull the data itself via dashboard_data and explainers. That keeps
    the CLI / cron use case (e.g. emailing a nightly PDF) symmetrical
    with the UI use case (download from the Today tab).
    """
    if brief is None:
        from ui import dashboard_data as _dd
        brief = _dd.morning_brief()
    if mood is None or narrative is None or action is None or alerts is None:
        from ui import explainers as _ex
        mood = mood or _ex.market_mood(brief)
        narrative = narrative or _ex.daily_narrative(brief)
        action = action or _ex.top_action_today(brief)
        alerts = alerts if alerts is not None else _ex.alert_lines(brief)

    sty = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=18 * mm,
        title="PSX Daily Brief",
        author="PSX Advisor",
    )

    story: list = []
    _hero_section(story, sty, mood, narrative)
    _action_section(story, sty, action)
    _alerts_section(story, sty, alerts)
    _macro_radar_section(story, sty, brief)
    _bots_verdict_section(story, sty)
    _short_ideas_section(story, sty)
    _forecast_section(story, sty, brief)
    _news_digest_section(story, sty)
    _material_info_section(story, sty, brief)
    _per_stock_detail_section(story, sty, brief)
    _management_outlook_section(story, sty, brief)
    _portfolio_section(story, sty, brief)
    _watchlist_section(story, sty)
    _movers_section(story, sty, brief)
    _quality_section(story, sty, brief)
    _calendar_section(story, sty, brief)

    story.append(Spacer(1, 8))
    fresh_lines: list[str] = []
    try:
        from ui.dashboard_data import data_freshness
        fresh = data_freshness()
        for k, v in fresh.items():
            if not v.get("exists"):
                fresh_lines.append(f"{k}: <i>missing</i>")
                continue
            latest = v.get("latest_data_date")
            tdb = v.get("trading_days_behind")
            if latest is not None and tdb is not None:
                if tdb == 0:
                    gap = "today"
                elif tdb == 1:
                    gap = "1 trading day ago"
                else:
                    gap = f"{tdb} trading days ago"
                fresh_lines.append(
                    f"<b>{k}</b>: data through {latest} ({gap})"
                    f" — file written {v.get('updated_at')}"
                    f" ({v.get('age_hours')}h ago)"
                )
            else:
                fresh_lines.append(
                    f"<b>{k}</b>: file written {v.get('updated_at')}"
                    f" ({v.get('age_hours')}h ago)"
                )
    except Exception:
        pass
    if fresh_lines:
        story.append(Paragraph("Data freshness", sty["h2"]))
        _explain(story, sty,
            "When each input file was last refreshed and what trading "
            "day it actually covers. PSX is closed Sat/Sun, so it is "
            "<b>normal</b> for prices, news, and FIPI to be 1 trading "
            "day behind on a Monday morning before the market opens. "
            "If anything is more than 2 trading days stale, run "
            "<i>Pull latest from GitHub</i> in the sidebar."
        )
        for ln in fresh_lines:
            story.append(Paragraph(ln, sty["body_muted"]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def default_filename() -> str:
    return f"psx-daily-brief-{datetime.now().strftime('%Y-%m-%d')}.pdf"


if __name__ == "__main__":
    out = Path("psx-daily-brief.pdf")
    out.write_bytes(build_daily_report())
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
