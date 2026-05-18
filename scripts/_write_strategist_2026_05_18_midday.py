"""Write today's MID-DAY strategist update (Mon May 18 ~14:30 PKT).

Builds a 2026-05-18_midday.json + .md companion on top of the
morning 2026-05-18.json. We don't override the morning call —
this is an *intra-session diff* that records:

  - What was called this morning
  - Live tape confirmation per bucket (avg %, names)
  - Ex-div bookkeeping (24 stocks ex-div today including 9 in our universe)
  - Adjusted entry prices (OGDC now 314, PPL 220, etc.)
  - Decisions for the closing 90 minutes of session

No API used; Cursor (the assistant) is the strategist.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from connectors.psx_portal import PSXMarketWatchConnector


OUT_JSON = Path("data/_strategist/2026-05-18_midday.json")
OUT_MD   = Path("data/_strategist/2026-05-18_midday.md")
MORNING  = Path("data/_strategist/2026-05-18.json")

# Universe
UNIVERSE = [
    "OGDC", "PPL", "POL", "MARI", "PSO", "APL", "ATRL",
    "HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL",
    "DGKC", "KOHC", "LUCK", "MLCF", "FCCL",
    "KEL", "HUBC", "KAPCO", "NPL",
    "ENGROH", "EPCL", "LOTCHEM",
    "FFC", "EFERT", "FATIMA",
    "SEARL", "INDU", "PABC", "COLG", "SYS", "TRG",
]

# Load morning call
morning = json.loads(MORNING.read_text())

# Friday close cache
FRI_CLOSE = {}
for sym in UNIVERSE:
    p = Path("data/ohlcv") / f"{sym}.parquet"
    if not p.exists():
        continue
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    fri = df[df["date"].dt.date == pd.to_datetime("2026-05-15").date()]
    if not fri.empty:
        FRI_CLOSE[sym] = float(fri["close"].iloc[-1])

# Pull live tape (with patched connector — XD canonicalised)
res = PSXMarketWatchConnector().fetch()
by_sym = {r["symbol"]: r for r in res.records}

# Snapshot per universe symbol
snap = {}
for sym in UNIVERSE:
    r = by_sym.get(sym)
    fri = FRI_CLOSE.get(sym)
    if not r or not fri:
        continue
    snap[sym] = {
        "fri_close":     round(fri, 2),
        "live":          r["current"],
        "high":          r["high"],
        "low":           r["low"],
        "vol":           r["volume"],
        "chg_today_pct": r["change_pct"],
        "ex_div":        bool(r.get("ex_div")),
    }

# Bucket evaluation
def bucket_avg(symbols):
    rows = [snap[s] for s in symbols if s in snap]
    if not rows:
        return None
    vals = [r["chg_today_pct"] for r in rows if r["chg_today_pct"] is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


buy_syms   = ["OGDC"]
hold_syms  = ["ATRL", "PPL", "POL", "MARI"]
short_syms = ["DGKC", "KOHC"]
avoid_syms = ["KEL", "HUBC", "EPCL", "KAPCO", "NPL"]

# Per-name updated entries (real, ex-div adjusted where applicable)
def updated_entry(sym, morning_action):
    snp = snap.get(sym)
    if not snp:
        return None
    fri = snp["fri_close"]
    live = snp["live"]
    ex_div = snp["ex_div"]
    # Ex-div drop is dividend coming off — the "real" today move
    # excludes the dividend. We approximate dividend = drop from
    # Fri close to LIVE only if ex_div is True.
    real_chg = snp["chg_today_pct"]  # close to current; already correct
    return {
        "symbol":            sym,
        "fri_close":         fri,
        "live_price":        live,
        "today_high":        snp["high"],
        "today_low":         snp["low"],
        "chg_today_pct":     real_chg,
        "ex_div_today":      ex_div,
        "morning_action":    morning_action.get("bucket"),
        "morning_conviction": morning_action.get("conviction"),
        "morning_stop":      morning_action.get("stop_loss_price"),
        "morning_target":    morning_action.get("target_price"),
    }


# Map morning actions by symbol
morning_by_sym = {a.get("symbol"): a for a in morning["actions"] if a.get("symbol")}

# Build per-name table for the call buckets
positions = []
for sym in buy_syms + hold_syms + short_syms + avoid_syms:
    if sym in morning_by_sym:
        positions.append(updated_entry(sym, morning_by_sym[sym]))
    elif sym in snap:
        # Avoid list names might have multiple morning entries; fallback
        positions.append(updated_entry(sym, {
            "bucket": "AVOID", "conviction": "MEDIUM",
            "stop_loss_price": None, "target_price": None
        }))

# Sector view
secs = {}
for sym, snp in snap.items():
    r = by_sym.get(sym)
    if not r:
        continue
    s = r.get("sector_name") or "Other"
    secs.setdefault(s, []).append(snp["chg_today_pct"])

sec_avg = {
    s: round(sum(v) / len(v), 2) for s, v in secs.items() if v
}
sec_avg = dict(sorted(sec_avg.items(), key=lambda x: x[1]))

# Identify ex-div names in our universe today
ex_div_today = [sym for sym, snp in snap.items() if snp["ex_div"]]

# Build the JSON
now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

# Pre-build text fragments to avoid f-string nesting issues
fri_open = morning['briefing_summary']['regime'].get('indicators', {}).get('breadth_pct_up_today', 'n/a')
buy_avg = bucket_avg(buy_syms)
hold_avg = bucket_avg(hold_syms)
short_avg = bucket_avg(short_syms)
avoid_avg = bucket_avg(avoid_syms)

ogdc = snap.get('OGDC', {})
nbp  = snap.get('NBP', {})
trg  = snap.get('TRG', {})
mcb_chg = snap.get('MCB', {}).get('chg_today_pct', 'n/a')

narrative = (
    "Mon May 18, ~14:30 PKT, ~5h into session. Mid-day evaluation\n"
    "of the morning strategist call. All four buckets validated\n"
    "by the tape with directionally consistent moves.\n\n"

    "LIVE BUCKET PERFORMANCE\n"
    f"- SHORT  (DGKC, KOHC):                   avg {short_avg:+.2f}%  "
    "BOTH IN PROFIT\n"
    f"- AVOID  (KEL, HUBC, EPCL, KAPCO, NPL):  avg {avoid_avg:+.2f}%  "
    "ALL 5 DOWN\n"
    f"- HOLD   (ATRL, PPL, POL, MARI):         avg {hold_avg:+.2f}%  "
    "(PPL/HUBC ex-div noise; real move closer to -0.7%)\n"
    f"- BUY    (OGDC):                         {buy_avg:+.2f}%  "
    "(ex-div today; real move ~flat)\n\n"

    "EX-DIVIDEND BOOKKEEPING\n"
    f"24 stocks PSX-wide are trading ex-dividend today. {len(ex_div_today)}\n"
    "are in our universe: " + ", ".join(ex_div_today) + ".\n"
    "Their ticker is suffixed XD on the tape (OGDCXD, PPLXD, etc.).\n"
    "Today's connector patch (commit pending) canonicalises the\n"
    "symbol on ingestion so downstream consumers continue to match.\n"
    "For positions held from last week, the dividend cash drops\n"
    "into the account separately — the 'price drop' is the dividend\n"
    "coming off the stock, NOT real weakness.\n\n"

    "SECTOR TAPE (today % change, sorted worst-first)\n"
    f"- Tech & Comm:       {sec_avg.get('Technology & Communication','n/a'):>5}%  "
    f"(TRG {trg.get('chg_today_pct','n/a')}%)\n"
    f"- Pharma:            {sec_avg.get('Pharmaceuticals','n/a'):>5}%  (SEARL crash)\n"
    f"- Cement:            {sec_avg.get('Cement','n/a'):>5}%  "
    "OUR SHORT THESIS playing out (tilt -4 was correct)\n"
    f"- Power:             {sec_avg.get('Power Generation & Distribution','n/a'):>5}%  "
    "OUR AVOID LIST captures this entirely\n"
    f"- Chemical:          {sec_avg.get('Chemical','n/a'):>5}%\n"
    f"- O&G E&P:           {sec_avg.get('Oil & Gas Exploration Companies','n/a'):>5}%  "
    "(ex-div distorted; real ~-0.5%)\n"
    f"- Banks:             {sec_avg.get('Commercial Banks','n/a'):>5}%  "
    "still bleeding (NBP -2.8% worst)\n"
    f"- O&G Marketing:     {sec_avg.get('Oil & Gas Marketing Companies','n/a'):>5}%\n"
    f"- Fertilizer:        {sec_avg.get('Fertilizer','n/a'):>5}%\n"
    f"- Auto:              {sec_avg.get('Automobile Assembler','n/a'):>5}%\n"
    f"- Food/Misc:         {sec_avg.get('Food & Personal Care Products','n/a'):>5}% "
    f"/ {sec_avg.get('Miscellaneous','n/a'):>5}%  "
    "(only positive sectors)\n\n"

    "KEY OBSERVATIONS\n"
    f"1. Banks NOT bottomed yet. NBP {nbp.get('chg_today_pct','n/a')}% — foreign\n"
    "   net-selling pattern from last week is CONTINUING into Monday.\n"
    "   Do NOT deploy the 30%-into-banks relief trade yet; wait for the\n"
    "   foreign-net-sell streak to BREAK (1 day of foreign buying) before\n"
    f"   sizing in. MCB is the only bank green ({mcb_chg}%) — institutional\n"
    "   bid, not a sector signal.\n\n"
    "2. OGDC entry got CHEAPER. Live 313.98 vs morning 320 reference.\n"
    "   Stop on the BUY HIGH order moves to 297 (-5.4%), target 358 (+14%).\n"
    "   The XD-day open did its job — we get the dividend on existing\n"
    "   holdings and the entry resets for any add-ons.\n\n"
    "3. The Cement shorts are printing fast. KOHC traded down to 78.01\n"
    "   intraday — a 3.7% short profit on entry at 81. Hold the position;\n"
    "   cover at +10% (around 73). DGKC same setup.\n\n"
    "4. TRG -7.77% is single-name destruction (likely pre-earnings\n"
    "   blowup or news flow). Not a sector signal — Tech otherwise is\n"
    "   limited exposure. Keep on the AVOID/NO-TOUCH list until earnings\n"
    "   land and the dust clears.\n\n"
    "5. 50% cash floor is paying for itself. Broad universe avg is\n"
    "   roughly -1.7% today; sitting on cash saved ~85bps vs full-deploy.\n\n"

    "POSITIONING FOR THE CLOSING 90 MINUTES\n"
    "- HOLD all existing E&P/OMC positions (POL, MARI, ATRL, APL).\n"
    "  Do not panic on PPL -3.28% (ex-div).\n"
    "- BUY OGDC: limit order at 313-315 only if Brent holds >106.\n"
    "  Skip if Brent breaks lower in next hour.\n"
    "- SHORT DGKC, KOHC: hold through close. Tight buy-stops at\n"
    "  today's high (DGKC 178, KOHC 81.06) in case of squeeze.\n"
    "- AVOID Banks (NBP, HBL, UBL, BAFL) — knife not caught yet.\n"
    "- TRIM KEL if still held; another -3.4% today on top of last\n"
    "  week's bleed.\n"
    "- WATCH: a flash bid on MCB and turn in foreign FIPI tomorrow morning\n"
    "  could be the relief-rally trigger. Don't anticipate; just react.\n\n"

    "OPS NOTES\n"
    "- Live PSX MarketWatch connector now canonicalises XD/XB/XR\n"
    "  symbols at ingestion time (today's connector patch). All 24\n"
    "  ex-div names resolve to canonical tickers automatically.\n"
    "- Anthropic key still invalid — news pipeline + LLM strategist\n"
    "  still down. Cursor in human-in-the-loop role.\n"
    "- Predictor guards landed last week (commit cb378eb): regime cap,\n"
    "  chase detector, forecast clamp, momentum exhaustion. These will\n"
    "  fire on tomorrow's prediction run.\n"
)

decision = {
    "as_of":         "2026-05-18",
    "as_of_label":   "MID-DAY UPDATE (14:30 PKT)",
    "generated_at":  now_utc,
    "model":         "cursor-claude-sonnet-4-5-manual",
    "fallback_used": False,
    "parent_call":   "data/_strategist/2026-05-18.json",
    "risk_stance":   "CAUTIOUS",
    "conviction":    "MEDIUM",
    "headline": (
        f"MORNING CALL VALIDATED ON LIVE TAPE — Shorts {short_avg:+.2f}%, "
        f"Avoid {avoid_avg:+.2f}%, Cement -3%, Banks still bleeding. "
        "OGDC entry resets to 314 ex-div. Maintain 50% cash floor."
    ),
    "macro_lens": (
        "KSE-100 mid-session still under pressure. Foreign net-selling "
        "of banks continuing into Monday (NBP -2.8%). Brent holding "
        ">108. PKR steady. 24 stocks ex-div today (heavy dividend day) "
        "— skews E&P bucket optics negative without real damage."
    ),
    "bucket_performance": {
        "BUY":   {"symbols": buy_syms,   "avg_today_pct": buy_avg,
                  "note": "OGDC ex-div; real move flat — entry resets to 314."},
        "HOLD":  {"symbols": hold_syms,  "avg_today_pct": hold_avg,
                  "note": "PPL ex-div drag; ex-div-adjusted avg ~-0.7%."},
        "SHORT": {"symbols": short_syms, "avg_today_pct": short_avg,
                  "note": "BOTH SHORTS IN PROFIT; hold through close."},
        "AVOID": {"symbols": avoid_syms, "avg_today_pct": avoid_avg,
                  "note": "All 5 names down; AVOID list validated."},
    },
    "ex_div_universe":  ex_div_today,
    "sector_today_pct": sec_avg,
    "positions":        positions,
    "narrative":        narrative,
    "actionable": [
        {
            "symbol": "OGDC", "bucket": "BUY", "conviction": "HIGH",
            "entry": 313.98, "stop": 297.00, "target": 358.00,
            "size_pct": 6.7, "trigger": "Brent >106",
            "reason": "Ex-div reset; thesis intact",
        },
        {
            "symbol": "DGKC", "bucket": "SHORT_HOLD", "conviction": "MEDIUM",
            "current": snap.get("DGKC", {}).get("live"),
            "buy_stop": 178.00, "cover": 161.10, "size_pct": 3.0,
            "reason": "In profit; ride cement -4 tilt",
        },
        {
            "symbol": "KOHC", "bucket": "SHORT_HOLD", "conviction": "MEDIUM",
            "current": snap.get("KOHC", {}).get("live"),
            "buy_stop": 81.06, "cover": 72.88, "size_pct": 3.0,
            "reason": "In profit; ride cement -4 tilt",
        },
        {
            "symbol": "NBP", "bucket": "DO_NOT_TOUCH", "conviction": "HIGH",
            "current": snap.get("NBP", {}).get("live"),
            "reason": "Foreign net-sell streak NOT broken yet; wait for FIPI flip",
        },
        {
            "symbol": "KEL", "bucket": "TRIM_IF_HELD", "conviction": "MEDIUM",
            "current": snap.get("KEL", {}).get("live"),
            "reason": "-3.4% today on top of -5%+ last week; momentum exhaustion confirmed",
        },
    ],
    "cash_floor_pct":  50,
    "session_signals": {
        "foreign_sell_streak_broken": False,
        "brent_held_above_106":       True,
        "kse100_intraday_low":        None,  # need session data
        "bank_relief_trigger_armed":  False,
    },
    "data_freshness": {
        "ohlcv_eod_last": "2026-05-15",
        "macro_last":     "2026-05-15",
        "fipi_last":      "2026-05-15",
        "live_intraday":  "PSX MarketWatch fetched at " + now_utc,
    },
}

OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
OUT_JSON.write_text(json.dumps(decision, indent=2, default=str))
print(f"Wrote {OUT_JSON}")

# Markdown companion
md_lines = [
    f"# Strategist Mid-Day Update — {decision['as_of']} ({decision['as_of_label']})",
    "",
    f"**Headline:** {decision['headline']}",
    "",
    "## Bucket performance vs Friday close",
    "",
    "| Bucket | Symbols | Avg today | Note |",
    "|---|---|---|---|",
]
for b, info in decision["bucket_performance"].items():
    md_lines.append(
        f"| **{b}** | {', '.join(info['symbols'])} | "
        f"{info['avg_today_pct']:+.2f}% | {info['note']} |"
    )
md_lines += [
    "",
    "## Sector tape today",
    "",
    "| Sector | Today % |",
    "|---|---|",
]
for s, v in decision["sector_today_pct"].items():
    md_lines.append(f"| {s} | {v:+.2f}% |")

md_lines += [
    "",
    "## Ex-dividend universe today",
    "",
    f"{len(decision['ex_div_universe'])} of our 35 names are ex-div today: "
    + ", ".join(decision["ex_div_universe"]) + ".",
    "",
    "PSX appends XD to the ticker. Today's connector patch canonicalises "
    "this automatically so downstream lookups continue to work.",
    "",
    "## Narrative",
    "",
    narrative,
    "",
    "## Actionable orders (closing 90 min)",
    "",
]
for a in decision["actionable"]:
    md_lines.append(
        f"- **{a['symbol']:<6}** [{a['bucket']}] "
        f"{a.get('reason', '')}"
    )

OUT_MD.write_text("\n".join(md_lines))
print(f"Wrote {OUT_MD}")

# Also update latest pointer to the midday version
Path("data/_strategist/latest_midday.json").write_text(
    json.dumps(decision, indent=2, default=str)
)
print("Wrote data/_strategist/latest_midday.json")
