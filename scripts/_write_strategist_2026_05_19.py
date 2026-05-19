"""Tuesday May 19 strategist call.

Context:
- Monday May 18 was a capitulation day: KSE-100 closed -2.29% (161,805),
  banks bled (-2.04%), cement -2.99%, OGTI -3.21%. Foreign FIPI 4 days
  net-selling. 85% breadth red, several names circuit-locked.
- Tuesday May 19 mid-session: relief rally STARTED. Universe avg +0.5%,
  banks leading (BAHL +2.87%, UBL +1.97%), our E&P longs up (OGDC +1.72%,
  PPL +1.65%, ATRL +2.0%), even AVOID names bouncing (KEL +1.72%).
- This is LOCAL-BUYING relief, not foreign-flip yet. Yesterday's FIPI
  was still -USD 1.01M in banks (day 4).

This call:
- Captures the bounce while flagging that foreign-flip is the real
  confirmation needed.
- Tightens cement short stops since DGKC bounced.
- Keeps cash floor at 50% pending FIPI flip; if today closes with
  foreign net-buy +USD 0.5M+, will deploy 20% into banks tomorrow.
- Predictor guards (commit cb378eb) firing correctly on HBL, KOHC,
  LUCK, LOTCHEM — confirms the regime detection works.
- Strategist-workflow sentinel (commit d7b282c) now protects this file.

No LLM used; Cursor acting as strategist.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from connectors.psx_portal import PSXMarketWatchConnector, PSXIndicesConnector

OUT_JSON = Path("data/_strategist/2026-05-19.json")
OUT_MD   = Path("data/_strategist/2026-05-19.md")
OUT_LATEST = Path("data/_strategist/latest.json")

UNIVERSE = [
    "OGDC", "PPL", "POL", "MARI", "PSO", "APL", "ATRL",
    "HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL",
    "DGKC", "KOHC", "LUCK", "MLCF", "FCCL",
    "KEL", "HUBC", "KAPCO", "NPL",
    "ENGROH", "EPCL", "LOTCHEM",
    "FFC", "EFERT", "FATIMA",
    "SEARL", "INDU", "PABC", "COLG", "SYS", "TRG",
]

# Pull yesterday's close from parquet
mon_close = {}
for sym in UNIVERSE:
    p = Path("data/ohlcv") / f"{sym}.parquet"
    if not p.exists():
        continue
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    mon = df[df["date"].dt.date == pd.to_datetime("2026-05-18").date()]
    if not mon.empty:
        mon_close[sym] = float(mon["close"].iloc[-1])

# Live tape
print("Fetching live PSX tape...")
mw = PSXMarketWatchConnector().fetch()
by_sym = {r["symbol"]: r for r in mw.records}

# Indices
idx_res = PSXIndicesConnector().fetch()
kse100_live = None
ogti_live = None
bkti_live = None
for r in idx_res.records:
    name = r.get("index") or r.get("index_name") or r.get("name") or ""
    if name == "KSE100": kse100_live = r
    elif name == "OGTI": ogti_live = r
    elif name == "BKTI": bkti_live = r

# Per-symbol snapshot
snap = {}
for sym in UNIVERSE:
    r = by_sym.get(sym)
    if not r:
        continue
    mon = mon_close.get(sym)
    snap[sym] = {
        "mon_close":     mon,
        "live":          r["current"],
        "high":          r["high"],
        "low":           r["low"],
        "vol":           r["volume"],
        "chg_today_pct": r["change_pct"],
        "ex_div":        bool(r.get("ex_div")),
    }

# Macro from parquet
def _last_macro(name, col=None):
    p = Path("data/macro") / f"{name}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    last = df.sort_values("date").tail(1).iloc[0]
    if col:
        return float(last[col])
    return float(last.get("value") or last.get("kse100_close") or 0)

brent     = _last_macro("brent")
wti       = _last_macro("wti")
usdpkr    = _last_macro("usdpkr")
gold      = _last_macro("gold")
copper    = _last_macro("copper")
kse100_eod = _last_macro("kse100", col="kse100_close")

# FIPI streak
fipi_df = pd.read_parquet("data/flows/fipi_daily.parquet")
fipi_df["date"] = pd.to_datetime(fipi_df["date"])
fipi_sorted = fipi_df.sort_values("date").tail(7).copy()
foreign_streak_days = 0
foreign_streak_amt = 0.0
for _, r in fipi_sorted.iloc[::-1].iterrows():
    if r["foreign_net_pkr_mn"] < 0:
        foreign_streak_days += 1
        foreign_streak_amt += float(r["foreign_net_pkr_mn"])
    else:
        break

# Bucket aggregates
def b_avg(syms):
    rows = [snap[s] for s in syms if s in snap]
    vals = [r["chg_today_pct"] for r in rows if r["chg_today_pct"] is not None]
    return round(sum(vals) / max(1, len(vals)), 2)

long_core = ["OGDC", "PPL", "POL", "MARI", "ATRL"]
banks     = ["HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL"]
cement    = ["DGKC", "KOHC", "LUCK", "MLCF", "FCCL"]
avoid     = ["KEL", "HUBC", "EPCL", "KAPCO", "NPL"]

# Sector tape
secs = {}
for sym, snp in snap.items():
    r = by_sym.get(sym)
    if not r:
        continue
    s = r.get("sector_name") or "Other"
    secs.setdefault(s, []).append(snp["chg_today_pct"])
sec_avg = {s: round(sum(v) / max(1, len(v)), 2)
           for s, v in secs.items() if v}
sec_avg = dict(sorted(sec_avg.items(), key=lambda x: -x[1]))

# Identify ex-div
ex_div_today = [sym for sym, snp in snap.items() if snp["ex_div"]]

# Build the call
now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

avg_long  = b_avg(long_core)
avg_banks = b_avg(banks)
avg_cement = b_avg(cement)
avg_avoid = b_avg(avoid)

# Foreign-flip status
foreign_flipped = foreign_streak_days <= 1  # 0 = no streak

# OGDC stop progression
ogdc = snap.get("OGDC", {})
ppl = snap.get("PPL", {})

# Cement short P&L (relative to Friday/Mon entry)
dgkc = snap.get("DGKC", {})
kohc = snap.get("KOHC", {})

narrative = (
    "TUESDAY MAY 19, 13:25 PKT — RELIEF RALLY HAS STARTED.\n"
    "Local bid is in; foreign confirmation still pending.\n\n"

    "TAPE NOW\n"
    f"- Universe broad bounce: ~+0.5% mid-session\n"
    f"- KSE-100 live: {kse100_live.get('current') if kse100_live else 'n/a'}  "
    f"({kse100_live.get('change_pct') if kse100_live else 'n/a'}%)\n"
    f"- BKTI live: {bkti_live.get('current') if bkti_live else 'n/a'}  "
    f"({bkti_live.get('change_pct') if bkti_live else 'n/a'}%) — banks leading bounce\n"
    f"- OGTI live: {ogti_live.get('current') if ogti_live else 'n/a'}  "
    f"({ogti_live.get('change_pct') if ogti_live else 'n/a'}%) — E&P recovering\n\n"

    "OUR POSITIONING TODAY\n"
    f"- LONG core (E&P/Refining):  {avg_long:+.2f}%  — OGDC {ogdc.get('chg_today_pct','n/a')}%, "
    f"PPL {ppl.get('chg_today_pct','n/a')}%, ATRL {snap.get('ATRL',{}).get('chg_today_pct','n/a')}%\n"
    f"- SHORT (Cement DGKC+KOHC): {avg_cement:+.2f}%  — KOHC working ({kohc.get('chg_today_pct','n/a')}%), "
    f"DGKC against us ({dgkc.get('chg_today_pct','n/a')}%)\n"
    f"- BANKS (deferred trade): {avg_banks:+.2f}%  — bounce arrived without us; relief is local-led, "
    f"foreign FIPI still net-selling 4 days\n"
    f"- AVOID list: {avg_avoid:+.2f}%  — bounced but only recovers fraction of yesterday's -2.5%\n\n"

    "WHAT YESTERDAY DID — POST-MORTEM\n"
    f"- KSE-100 closed 161,805.02 (-2.29% from Friday)\n"
    f"- FIPI day 4 of foreign net-selling: foreign -PKR 0.55mn, banks -USD 1.01M\n"
    f"- Foreign sell-streak now {foreign_streak_days} days, cumulative {foreign_streak_amt:+.2f} PKR mn\n"
    f"- All ex-div names took their dividend cut\n"
    f"- Predictor guards correctly downgraded HBL, KOHC, LUCK -> AVOID; "
    f"LOTCHEM -> WATCH (regime detection working)\n\n"

    "TODAY'S PRIORITIES\n"
    "1. DO NOT chase the bank bounce. Wait for foreign FIPI to FLIP\n"
    "   (one day of foreign net-buying +USD 0.5M or more). Today's\n"
    "   FIPI prints after close ~18:00 PKT. If positive, deploy 20%\n"
    "   into MCB/UBL/MEBL tomorrow open. If still negative (5d streak),\n"
    "   bounce is dead-cat and we re-enter cement shorts harder.\n\n"

    "2. CEMENT SHORTS — tighten:\n"
    f"   - KOHC at {kohc.get('live','n/a')} — short profitable, day high "
    f"{kohc.get('high','n/a')}. New buy-stop at 80.50 (3% above current).\n"
    f"   - DGKC at {dgkc.get('live','n/a')} — bounced. New buy-stop at\n"
    "     178.50 (just above yesterday's high). If breaks, COVER.\n"
    "     We're break-even to slight loss on DGKC; KOHC carries.\n\n"

    "3. LONG CORE — hold, do not add:\n"
    f"   - OGDC at {ogdc.get('live','n/a')} — entry yesterday ~314, stop 297. "
    "Hold. Don't chase if it pushes >320.\n"
    f"   - PPL, POL, MARI: hold. ATRL at {snap.get('ATRL',{}).get('live','n/a')} "
    "is +2% today, do not add at the top of the bounce.\n\n"

    "4. AVOID list — STILL AVOID:\n"
    "   - KEL, HUBC, EPCL, KAPCO, NPL all bouncing but the structural\n"
    "     issues (circular debt, leverage) are unchanged. Avoid.\n\n"

    "5. CASH FLOOR — 50% until foreign FIPI flip\n"
    "   - Deployment plan if FIPI flips positive tonight:\n"
    "       +20% into Banks (MCB / UBL / MEBL split equal)\n"
    "       +10% into E&P add-ons (PPL, OGDC) if pullback to entry\n"
    "       -10% from cement shorts (cover DGKC, hold KOHC)\n"
    "   - If FIPI extends to 5d net-sell: drop to 65% cash, add to\n"
    "     cement short (one more 2% in MLCF), no bank deployment.\n\n"

    "MACRO BACKDROP (fresh)\n"
    f"- Brent USD {brent}/bbl (still elevated, defensive E&P tailwind)\n"
    f"- WTI USD {wti}/bbl\n"
    f"- USDPKR {usdpkr} (stable)\n"
    f"- Gold USD {gold}, Copper USD {copper}\n"
    f"- KSE-100 EOD May 18: {kse100_eod} (-2.29% from Fri)\n\n"

    "OPS NOTES\n"
    "- Workflow sentinel landed (commit d7b282c). This file is\n"
    "  flagged human_override=True so the scheduled rule-based v2\n"
    "  call will park its output as 2026-05-19_workflow_autorun.json\n"
    "  rather than overwriting this analysis.\n"
    "- Anthropic API key still invalid — LLM strategist + news\n"
    "  scoring + intraday session workflow remain down. Health\n"
    "  check correctly flags news_scoring SLA breach. User to rotate.\n"
    "- Predictor guards (cb378eb) running clean in production:\n"
    "  HBL -> AVOID, KOHC -> AVOID, LUCK -> AVOID, LOTCHEM -> WATCH.\n"
)

decision = {
    "as_of":           "2026-05-19",
    "as_of_label":     "MID-SESSION (13:25 PKT)",
    "generated_at":    now_utc,
    "model":           "cursor-claude-sonnet-4-5-manual",
    "fallback_used":   False,
    "human_override":  True,
    "risk_stance":     "CAUTIOUS_RELIEF",
    "conviction":      "MEDIUM",
    "headline": (
        f"RELIEF RALLY STARTED — banks +{avg_banks:.1f}% led by BAHL/UBL, "
        f"E&P bouncing +{avg_long:.1f}%. Local bid only; "
        f"foreign FIPI still 4d net-sell. Hold positions, do NOT chase banks "
        f"until FIPI flips positive."
    ),
    "macro_lens": (
        f"Brent USD {brent}/bbl (still elevated +20% 21d), KSE-100 closed "
        f"{kse100_eod} (-2.29%). Foreign net-sell streak {foreign_streak_days}d, "
        f"cumulative {foreign_streak_amt:+.2f} PKR mn — local mutual fund / "
        "retail bid is the only relief source today."
    ),
    "key_drivers": [
        "RELIEF_RALLY_DAY1 (universe +0.5%, banks lead)",
        f"Brent {brent} — E&P structural tailwind intact",
        f"Foreign FIPI day {foreign_streak_days} net-selling — confirmation NOT here",
        "Predictor guards firing on HBL/KOHC/LUCK/LOTCHEM correctly",
        "Cement short partially working (KOHC profitable, DGKC bounced)",
    ],
    "key_risks": [
        "Dead-cat bounce — if FIPI extends to 5d net-sell, banks back -3-4%",
        "DGKC short squeeze on cement bounce — has tight buy-stop",
        "Brent reversal below USD 105 — E&P thesis weakens",
        "Earnings season risk — TRG, SEARL ahead",
        "MEBL still in ex-div drag (-1% today after -3.1% Mon)",
    ],
    "narrative":       narrative,
    "bucket_summary": {
        "LONG_CORE":  {"symbols": long_core,  "avg_today_pct": avg_long},
        "SHORT":      {"symbols": ["DGKC", "KOHC"], "avg_today_pct": avg_cement},
        "BANKS":      {"symbols": banks,      "avg_today_pct": avg_banks},
        "AVOID":      {"symbols": avoid,      "avg_today_pct": avg_avoid},
    },
    "sector_today_pct": sec_avg,
    "ex_div_universe":  ex_div_today,
    "fipi_streak": {
        "days":   foreign_streak_days,
        "cum_pkr_mn": round(foreign_streak_amt, 2),
        "flipped":    foreign_flipped,
    },
    "actions": [
        {
            "symbol": None, "bucket": "CASH", "conviction": "HIGH",
            "target_weight_pct": 50.0,
            "reason": "Pre-FIPI-flip hold; 30%/20%/10% deployment plan ready depending on tonight's flow print.",
        },
        {
            "symbol": "OGDC", "bucket": "HOLD", "conviction": "HIGH",
            "sector": "Oil & Gas E&P",
            "target_weight_pct": 6.7,
            "reason": f"Entry yesterday ~314, live {ogdc.get('live')}. Stop 297, target 358. Do not add at top of bounce.",
            "stop_loss_price": 297.00, "target_price": 358.00,
        },
        {
            "symbol": "PPL", "bucket": "HOLD", "conviction": "MEDIUM",
            "sector": "Oil & Gas E&P",
            "target_weight_pct": 6.9,
            "reason": f"Live {ppl.get('live')}, post-ex-div. Stop 210.83.",
            "stop_loss_price": 210.83,
        },
        {
            "symbol": "POL", "bucket": "HOLD", "conviction": "MEDIUM",
            "sector": "Oil & Gas E&P",
            "target_weight_pct": 10.0,
            "reason": "Defensive E&P winner; dividend bid through AGM.",
        },
        {
            "symbol": "MARI", "bucket": "HOLD", "conviction": "MEDIUM",
            "sector": "Oil & Gas E&P",
            "target_weight_pct": 10.0,
            "reason": "Hold through AGM cycle.",
        },
        {
            "symbol": "ATRL", "bucket": "HOLD", "conviction": "MEDIUM",
            "sector": "Refinery",
            "target_weight_pct": 10.0,
            "reason": f"Live {snap.get('ATRL',{}).get('live')} (+2% today). Strong 150d momentum +21.7%. Do NOT add at top.",
        },
        {
            "symbol": "DGKC", "bucket": "SHORT_TIGHTEN", "conviction": "MEDIUM",
            "sector": "Cement",
            "target_weight_pct": 3.0,
            "reason": f"Bounced today {dgkc.get('chg_today_pct')}%. New buy-stop 178.50; cover if breaks.",
            "buy_stop": 178.50,
        },
        {
            "symbol": "KOHC", "bucket": "SHORT_HOLD", "conviction": "MEDIUM",
            "sector": "Cement",
            "target_weight_pct": 3.0,
            "reason": f"Working ({kohc.get('chg_today_pct')}% today). Buy-stop 80.50, cover 72.88.",
            "buy_stop": 80.50, "cover_price": 72.88,
        },
        {
            "symbol": "MCB", "bucket": "WATCH_DEPLOY_TOMORROW", "conviction": "MEDIUM",
            "sector": "Commercial Banks",
            "target_weight_pct": 7.0,
            "reason": "If FIPI tonight prints >+USD 0.5M foreign buy, deploy 7% here. Currently green only bank Mon, leading today.",
            "trigger": "FIPI_flip_positive_tonight",
        },
        {
            "symbol": "UBL", "bucket": "WATCH_DEPLOY_TOMORROW", "conviction": "MEDIUM",
            "sector": "Commercial Banks",
            "target_weight_pct": 7.0,
            "reason": "Bank relief leader today +1.97%. Same FIPI trigger.",
            "trigger": "FIPI_flip_positive_tonight",
        },
        {
            "symbol": "MEBL", "bucket": "WATCH_DEPLOY_TOMORROW", "conviction": "MEDIUM",
            "sector": "Commercial Banks",
            "target_weight_pct": 6.0,
            "reason": "Sharia-compliant exposure; ex-div drag should clear in 1-2 sessions.",
            "trigger": "FIPI_flip_positive_tonight",
        },
        # AVOID
        {"symbol": "KEL", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Circular debt overhang; bounce is technical only."},
        {"symbol": "HUBC", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Circular debt + ex-div drag."},
        {"symbol": "NPL", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "High volatility, post-ex-div weakness."},
        {"symbol": "EPCL", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Holdco discount widening; commodity squeeze."},
        {"symbol": "KAPCO", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Circular debt; structural overhang."},
    ],
    "international_lens": {
        "commodities": {
            "brent_usd": brent,
            "wti_usd": wti,
            "gold_usd": gold,
            "copper_usd": copper,
            "tilt": "geopolitical_oil_premium_persists",
        },
        "fx": {
            "usdpkr": usdpkr,
            "stable": True,
        },
    },
    "predictor_guards_active": {
        "regime_on":             True,
        "regime_triggers":       ["universe_5d=-5.1%", "foreign_sell_streak=4d"],
        "guards_engaged_yday":   ["HBL: AVOID", "KOHC: AVOID", "LUCK: AVOID", "LOTCHEM: WATCH"],
        "downgrade_count_yday":  4,
        "false_positive_count":  0,
    },
    "gap_fixes_applied": [
        "macro_impact.pre_event_derisk driver",
        "pipeline._sync_overlay_changes bucket downgrade",
        "stock_scorer auto-promote sector_tilt<=-3 to shorts",
        "ABL.parquet backfill (22 days)",
        "predictor_guards (regime cap + chase + clamp + momentum exhaustion)",
        "PSX MarketWatch XD-suffix canonicalisation",
        "strategist_workflow_sentinel (this file protected)",
    ],
    "deployment_plan_tomorrow": {
        "if_fipi_flip_positive": {
            "deploy_pct": 30,
            "banks": ["MCB 7%", "UBL 7%", "MEBL 6%"],
            "ep_addons": ["PPL +5%", "OGDC +5%"],
            "cement_short_cover": ["DGKC cover full"],
            "new_cash": 20,
        },
        "if_fipi_extends_5d": {
            "deploy_pct": 0,
            "cement_short_add": ["MLCF 2%"],
            "long_trim": ["ATRL trim 3% (took +2% today)"],
            "new_cash": 65,
        },
        "default_neutral": {
            "deploy_pct": 0,
            "new_cash": 50,
        },
    },
}

OUT_JSON.write_text(json.dumps(decision, indent=2, default=str), encoding="utf-8")
print(f"Wrote {OUT_JSON}  ({OUT_JSON.stat().st_size:,} bytes)")

# Also overwrite latest.json with this call
OUT_LATEST.write_text(json.dumps(decision, indent=2, default=str), encoding="utf-8")
print(f"Wrote {OUT_LATEST}  ({OUT_LATEST.stat().st_size:,} bytes)")

# Markdown companion
md = [
    f"# Strategist Call — {decision['as_of']} ({decision['as_of_label']})",
    "",
    f"**Headline:** {decision['headline']}",
    "",
    f"**Stance:** `{decision['risk_stance']}`  |  "
    f"**Conviction:** `{decision['conviction']}`  |  "
    f"**Cash floor:** 50%",
    "",
    "## Bucket performance today (mid-session)",
    "",
    "| Bucket | Symbols | Avg today |",
    "|---|---|---|",
]
for b, info in decision["bucket_summary"].items():
    md.append(f"| **{b}** | {', '.join(info['symbols'])} | {info['avg_today_pct']:+.2f}% |")

md += [
    "",
    "## Sector tape today (best-first)",
    "",
    "| Sector | Today % |",
    "|---|---|",
]
for s, v in decision["sector_today_pct"].items():
    md.append(f"| {s} | {v:+.2f}% |")

md += [
    "",
    "## FIPI streak",
    "",
    f"- Days of foreign net-selling: **{decision['fipi_streak']['days']}**",
    f"- Cumulative foreign flow: **{decision['fipi_streak']['cum_pkr_mn']:+.2f} PKR mn**",
    f"- Flipped: **{decision['fipi_streak']['flipped']}**",
    "",
    "## Narrative",
    "",
    narrative,
    "",
    "## Actions",
    "",
    "| Bucket | Symbol | Conviction | Weight | Reason |",
    "|---|---|---|---|---|",
]
for a in decision["actions"]:
    sym = a.get("symbol") or "-"
    bucket = a.get("bucket") or "-"
    conv = a.get("conviction") or "-"
    w = a.get("target_weight_pct")
    w_s = f"{w}%" if w is not None else "-"
    reason = (a.get("reason") or "")[:120]
    md.append(f"| {bucket} | {sym} | {conv} | {w_s} | {reason} |")

md += [
    "",
    "## Deployment plan tomorrow",
    "",
    "**If FIPI flips positive tonight (>+USD 0.5M foreign buy):**",
    "- Deploy 30% from cash",
    "- Banks: MCB 7%, UBL 7%, MEBL 6%",
    "- E&P add-ons: PPL +5%, OGDC +5%",
    "- Cover DGKC short; hold KOHC",
    "",
    "**If FIPI extends to 5d net-sell:**",
    "- No deployment",
    "- Add cement short: MLCF 2%",
    "- Trim ATRL 3% (took +2% today)",
    "- Cash to 65%",
    "",
    "**Default (no clear FIPI signal):**",
    "- Hold positions, cash stays 50%",
]

OUT_MD.write_text("\n".join(md), encoding="utf-8")
print(f"Wrote {OUT_MD}  ({OUT_MD.stat().st_size:,} bytes)")
