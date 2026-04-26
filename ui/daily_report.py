"""Daily PDF brief generator.

Builds a single-file PDF you can download from the Today tab and forward
to anyone (WhatsApp, email, print) — same content the UI shows, formatted
for offline reading.

Sections (in order):
  1. Header: date, market mood, one-line narrative
  2. What to do today: top action card
  3. Things to watch: alerts
  4. Forecast table: every universe stock with action / direction / 5d net
  5. Portfolio snapshot: positions + P&L
  6. Watchlist
  7. Quality leaders
  8. Earnings calendar (next 21 days)
  9. Top movers (universe)
 10. Footer: data freshness + disclaimers

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
    if narrative:
        story.append(Paragraph(narrative, sty["body"]))
    story.append(Spacer(1, 4))


def _action_section(story: list, sty: dict, action: dict) -> None:
    if not action:
        return
    sym = action.get("symbol")
    story.append(Paragraph("Top action today", sty["h2"]))
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
    for a in alerts[:8]:
        st = sty["callout_red"] if a.get("level") == "warning" else sty["body"]
        bullet = "● " if a.get("level") == "warning" else "○ "
        story.append(Paragraph(bullet + (a.get("text") or ""), st))


def _forecast_section(story: list, sty: dict, brief: dict) -> None:
    preds = (brief.get("predictions") or {}).get("predictions") or []
    if not preds:
        return
    story.append(Paragraph("Forecast — next 5 trading days", sty["h2"]))

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


def _movers_section(story: list, sty: dict, brief: dict) -> None:
    m = brief.get("universe_movers") or {}
    gainers = m.get("gainers") or []
    losers = m.get("losers") or []
    if not (gainers or losers):
        return
    story.append(Paragraph("Today's biggest moves", sty["h2"]))

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
    _forecast_section(story, sty, brief)
    _portfolio_section(story, sty, brief)
    _watchlist_section(story, sty)
    _movers_section(story, sty, brief)
    _quality_section(story, sty, brief)
    _calendar_section(story, sty, brief)

    story.append(Spacer(1, 8))
    fresh_lines = []
    try:
        from ui.dashboard_data import data_freshness
        fresh = data_freshness()
        for k, v in fresh.items():
            if v.get("exists"):
                fresh_lines.append(
                    f"{k}: {v.get('updated_at')} ({v.get('age_hours')}h ago)"
                )
            else:
                fresh_lines.append(f"{k}: missing")
    except Exception:
        pass
    if fresh_lines:
        story.append(Paragraph("Data freshness", sty["h2"]))
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
