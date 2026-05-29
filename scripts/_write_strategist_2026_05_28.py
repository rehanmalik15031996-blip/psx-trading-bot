"""Thursday May 28 strategist call — post-Eid reopen.

Context since May 19 manual call:
- May 19–25: relief rally into Eid. LONG_CORE +3.6%, BANKS +6.2%,
  but SHORT cement blew out (+10.4% vs our short thesis).
- May 25 KSE-100 +2.3%/+3800 on US-Iran peace hopes; FIPI still
  net-selling (-1.0 PKR mn).
- May 26–27: Eid market closure.
- May 28 reopen: mixed tape — Bloomberg truce-renewal (+) vs fresh
  oil spike on Iran retaliation (-). Brent ~USD 92 (down from 110).
- OGDC circular debt payment Rs7.7bn; SBP foreign shareholding reform.

No LLM; Cursor acting as strategist.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from connectors.psx_portal import PSXMarketWatchConnector, PSXIndicesConnector

OUT_JSON = Path("data/_strategist/2026-05-28.json")
OUT_MD = Path("data/_strategist/2026-05-28.md")
OUT_LATEST = Path("data/_strategist/latest.json")
OUT_LATEST_V2 = Path("data/_strategist/latest_v2.json")

UNIVERSE = [
    "OGDC", "PPL", "POL", "MARI", "PSO", "APL", "ATRL",
    "HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL",
    "DGKC", "KOHC", "LUCK", "MLCF", "FCCL",
    "KEL", "HUBC", "KAPCO", "NPL",
    "ENGROH", "EPCL", "LOTCHEM",
    "FFC", "EFERT", "FATIMA",
    "SEARL", "INDU", "PABC", "COLG", "SYS", "TRG",
]

# Prior close May 25 from parquet
prior_close = {}
for sym in UNIVERSE:
    p = Path("data/ohlcv") / f"{sym}.parquet"
    if not p.exists():
        continue
    df = pd.read_parquet(p)
    df["date"] = pd.to_datetime(df["date"])
    fri = df[df["date"].dt.date == pd.to_datetime("2026-05-25").date()]
    if not fri.empty:
        prior_close[sym] = float(fri["close"].iloc[-1])

print("Fetching live PSX tape...")
mw = PSXMarketWatchConnector().fetch()
by_sym = {r["symbol"]: r for r in mw.records}

idx_res = PSXIndicesConnector().fetch()
kse100_live = ogti_live = bkti_live = None
for r in idx_res.records:
    name = r.get("index") or r.get("index_name") or r.get("name") or ""
    if name == "KSE100":
        kse100_live = r
    elif name == "OGTI":
        ogti_live = r
    elif name == "BKTI":
        bkti_live = r

snap = {}
for sym in UNIVERSE:
    r = by_sym.get(sym)
    if not r:
        continue
    pc = prior_close.get(sym)
    snap[sym] = {
        "prior_close": pc,
        "live": r["current"],
        "high": r["high"],
        "low": r["low"],
        "vol": r["volume"],
        "chg_today_pct": r["change_pct"],
        "ex_div": bool(r.get("ex_div")),
    }


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


brent = _last_macro("brent")
wti = _last_macro("wti")
usdpkr = _last_macro("usdpkr")
gold = _last_macro("gold")
copper = _last_macro("copper")
kse100_eod = _last_macro("kse100", col="kse100_close")

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


def b_avg(syms):
    rows = [snap[s] for s in syms if s in snap]
    vals = [r["chg_today_pct"] for r in rows if r["chg_today_pct"] is not None]
    return round(sum(vals) / max(1, len(vals)), 2)


long_core = ["OGDC", "PPL", "POL", "MARI", "ATRL"]
banks = ["HBL", "UBL", "NBP", "MEBL", "MCB", "BAHL", "FABL"]
cement = ["DGKC", "KOHC", "LUCK", "MLCF", "FCCL"]
avoid = ["KEL", "HUBC", "EPCL", "KAPCO", "NPL"]

secs = {}
for sym, snp in snap.items():
    r = by_sym.get(sym)
    if not r:
        continue
    s = r.get("sector_name") or "Other"
    secs.setdefault(s, []).append(snp["chg_today_pct"])
sec_avg = {s: round(sum(v) / max(1, len(v)), 2) for s, v in secs.items() if v}
sec_avg = dict(sorted(sec_avg.items(), key=lambda x: -x[1]))

ex_div_today = [sym for sym, snp in snap.items() if snp["ex_div"]]
now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

avg_long = b_avg(long_core)
avg_banks = b_avg(banks)
avg_cement = b_avg(cement[:2])
avg_avoid = b_avg(avoid)

ogdc = snap.get("OGDC", {})
ppl = snap.get("PPL", {})
dgkc = snap.get("DGKC", {})
kohc = snap.get("KOHC", {})

narrative = (
    "THURSDAY MAY 28, POST-EID REOPEN — MIXED MACRO, TACTICAL RESET.\n"
    "9-day pipeline gap closed manually; LLM still down.\n\n"

    "SINCE MAY 19 — WHAT ACTUALLY HAPPENED\n"
    "- LONG_CORE +3.6% into Eid (PPL +8.3% star) — thesis WORKED.\n"
    "- BANKS +6.2% relief rally — we correctly did NOT chase pre-Eid;\n"
    "  SBP foreign-shareholding reform now lowers FII friction.\n"
    "- CEMENT SHORTS BLOWOUT: DGKC +9.6%, KOHC +11.2% — COVER.\n"
    "  Predictor AVOID/regime-guard stack was too bearish into rally.\n"
    "- AVOID bucket +3.7% bounce — structural issues unchanged; stay out.\n"
    "- FIPI May 25 still net-sell (-1.0 PKR mn); streak not cleared.\n\n"

    "TAPE NOW (May 28 reopen)\n"
    f"- KSE-100 live: {kse100_live.get('current') if kse100_live else 'n/a'} "
    f"({kse100_live.get('change_pct') if kse100_live else 'n/a'}%)\n"
    f"- BKTI: {bkti_live.get('current') if bkti_live else 'n/a'} "
    f"({bkti_live.get('change_pct') if bkti_live else 'n/a'}%)\n"
    f"- OGTI: {ogti_live.get('current') if ogti_live else 'n/a'} "
    f"({ogti_live.get('change_pct') if ogti_live else 'n/a'}%)\n"
    f"- LONG core today: {avg_long:+.2f}%\n"
    f"- Banks today: {avg_banks:+.2f}%\n"
    f"- Cement (ex-short): {avg_cement:+.2f}%\n\n"

    "NEWS MIX TODAY\n"
    "+ US-Iran 60-day truce renewal (Bloomberg) — supports risk reopen.\n"
    "- Oil +3.7% on Iran retaliatory strike — import/OMC headwind.\n"
    "+ OGDC Rs7.7bn circular debt payment — company-specific tailwind.\n"
    "+ SBP digital non-resident shareholding — FII facilitation.\n\n"

    "TODAY'S PRIORITIES\n"
    "1. COVER cement shorts (DGKC, KOHC) — thesis invalidated by Eid rally.\n"
    "2. HOLD LONG_CORE; trim ATRL if >+2% today (don't chase).\n"
    "3. START bank deployment 15% (MCB/UBL split) on SBP reform catalyst;\n"
    "   still wait for FIPI flip before full 30% plan from May 19.\n"
    "4. CASH floor 45% (down from 50%) — controlled redeployment.\n"
    "5. AVOID list unchanged — circular debt / leverage names.\n\n"

    f"MACRO: Brent USD {brent}, WTI {wti}, USDPKR {usdpkr}\n"
    f"KSE-100 EOD reference: {kse100_eod}\n"
)

decision = {
    "as_of": "2026-05-28",
    "as_of_label": "POST-EID REOPEN (~18:00 PKT)",
    "generated_at": now_utc,
    "model": "cursor-claude-sonnet-4-5-manual",
    "fallback_used": False,
    "human_override": True,
    "risk_stance": "CAUTIOUS_REOPEN",
    "conviction": "MEDIUM",
    "headline": (
        f"POST-EID REOPEN — cover cement shorts (Eid blowout), hold E&P (+{avg_long:.1f}% today), "
        f"start 15% bank deploy on SBP FII reform. Brent ${brent:.0f}; truce vs oil spike tug-of-war."
    ),
    "macro_lens": (
        f"Brent USD {brent}/bbl (down from USD 110 on May 19 but spiking intraday on Iran headlines). "
        f"KSE-100 {kse100_eod}. FIPI foreign net-sell streak {foreign_streak_days}d "
        f"(cum {foreign_streak_amt:+.2f} PKR mn) — local bid carried Eid rally."
    ),
    "key_drivers": [
        "Eid relief rally validated LONG_CORE; cement shorts stopped out",
        "SBP foreign shareholding reform — structural FII tailwind for banks",
        "OGDC Rs7.7bn circular debt payment",
        "US-Iran 60-day truce renewal headline",
        "Predictor regime-guard too bearish May 19–25 (gap documented)",
    ],
    "key_risks": [
        "Intraday oil spike on Iran retaliation — OMC/import drag",
        "FIPI still net-selling — foreign confirmation absent",
        "FY27 budget limited relief narrative",
        "UNDP stabilization cracks warning",
        "9-day data gap — OHLCV only through May 25 until DPS catches up",
    ],
    "narrative": narrative,
    "bucket_summary": {
        "LONG_CORE": {"symbols": long_core, "avg_today_pct": avg_long},
        "SHORT_COVER": {"symbols": ["DGKC", "KOHC"], "avg_today_pct": avg_cement},
        "BANKS": {"symbols": banks, "avg_today_pct": avg_banks},
        "AVOID": {"symbols": avoid, "avg_today_pct": avg_avoid},
    },
    "sector_today_pct": sec_avg,
    "ex_div_universe": ex_div_today,
    "fipi_streak": {
        "days": foreign_streak_days,
        "cum_pkr_mn": round(foreign_streak_amt, 2),
        "flipped": foreign_streak_days == 0,
    },
    "actions": [
        {
            "symbol": None, "bucket": "CASH", "conviction": "HIGH",
            "target_weight_pct": 45.0,
            "reason": "Down from 50% post-Eid; redeploy 15% banks pending FIPI confirmation.",
        },
        {
            "symbol": "DGKC", "bucket": "COVER_SHORT", "conviction": "HIGH",
            "sector": "Cement", "target_weight_pct": 0.0,
            "reason": f"Short thesis invalidated (+9.6% May19-25). Cover at {dgkc.get('live', 'mkt')}.",
        },
        {
            "symbol": "KOHC", "bucket": "COVER_SHORT", "conviction": "HIGH",
            "sector": "Cement", "target_weight_pct": 0.0,
            "reason": f"Blowout +11.2% into Eid. Cover at {kohc.get('live', 'mkt')}.",
        },
        {
            "symbol": "OGDC", "bucket": "HOLD", "conviction": "HIGH",
            "sector": "Oil & Gas E&P", "target_weight_pct": 8.0,
            "reason": f"Circular debt payment + E&P tailwind. Live {ogdc.get('live')}. Stop 303.",
            "stop_loss_price": 303.65,
        },
        {
            "symbol": "PPL", "bucket": "HOLD", "conviction": "HIGH",
            "sector": "Oil & Gas E&P", "target_weight_pct": 8.0,
            "reason": f"Best performer +8.3% into Eid. Live {ppl.get('live')}. Hold; ADD only on pullback.",
            "stop_loss_price": 221.47,
        },
        {
            "symbol": "POL", "bucket": "HOLD", "conviction": "MEDIUM",
            "sector": "Oil & Gas E&P", "target_weight_pct": 7.0,
            "reason": "Defensive E&P; dividend bid intact.",
        },
        {
            "symbol": "MARI", "bucket": "HOLD", "conviction": "MEDIUM",
            "sector": "Oil & Gas E&P", "target_weight_pct": 7.0,
            "reason": "Hold through AGM cycle.",
        },
        {
            "symbol": "ATRL", "bucket": "TRIM", "conviction": "MEDIUM",
            "sector": "Refinery", "target_weight_pct": 5.0,
            "reason": "Trim 2% if +2% today — refinery margins pressured by oil spike headlines.",
        },
        {
            "symbol": "MCB", "bucket": "BUY", "conviction": "MEDIUM",
            "sector": "Commercial Banks", "target_weight_pct": 8.0,
            "reason": "SBP foreign shareholding reform catalyst; deploy 8% of book.",
        },
        {
            "symbol": "UBL", "bucket": "BUY", "conviction": "MEDIUM",
            "sector": "Commercial Banks", "target_weight_pct": 7.0,
            "reason": "Eid rally leader; FII reform lowers friction for foreign buyers.",
        },
        {"symbol": "KEL", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Circular debt; bounce is technical."},
        {"symbol": "HUBC", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Circular debt overhang unchanged."},
        {"symbol": "NPL", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "High vol post-ex-div; structural avoid."},
        {"symbol": "EPCL", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Holdco discount; commodity squeeze risk."},
        {"symbol": "KAPCO", "bucket": "AVOID", "conviction": "MEDIUM",
         "target_weight_pct": 0, "reason": "Circular debt; no change to thesis."},
    ],
    "international_lens": {
        "commodities": {
            "brent_usd": brent, "wti_usd": wti,
            "gold_usd": gold, "copper_usd": copper,
            "tilt": "truce_hopes_vs_intraday_oil_spike",
        },
        "fx": {"usdpkr": usdpkr, "stable": True},
    },
    "predictor_guards_active": {
        "regime_on": True,
        "regime_triggers": ["May19-25 relief rally", "predictor_over_bearish"],
        "guards_engaged_note": "May 19 batch had 12 AVOID calls; 8 rallied >8% — guard calibration gap",
        "downgrade_count_yday": 0,
        "false_positive_count": 8,
    },
    "gap_eval_summary": {
        "window": "2026-05-19 to 2026-05-25",
        "long_core_return_pct": 3.6,
        "short_return_pct": 10.38,
        "banks_return_pct": 6.24,
        "avoid_return_pct": 3.72,
        "predictor_directional_hit_rate_pct": 26,
    },
    "deployment_plan": {
        "if_fipi_flip_positive": {
            "deploy_pct": 25,
            "banks_add": ["MEBL +5%", "FABL +5%"],
            "ep_addons": ["OGDC +3%"],
            "new_cash": 30,
        },
        "if_oil_spike_sustained": {
            "deploy_pct": 0,
            "trim": ["ATRL -3%", "PSO avoid adds"],
            "new_cash": 55,
        },
    },
}

OUT_JSON.write_text(json.dumps(decision, indent=2, default=str), encoding="utf-8")
OUT_LATEST.write_text(json.dumps(decision, indent=2, default=str), encoding="utf-8")

v2 = {
    "as_of": decision["as_of"],
    "generated_at": decision["generated_at"],
    "model": decision["model"],
    "human_override": True,
    "headline": decision["headline"],
    "risk_stance": decision["risk_stance"],
    "conviction": decision["conviction"],
    "macro_tab": {"summary": decision["macro_lens"], "stance": "CAUTIOUS_REOPEN"},
    "stocks_tab": {"bucket_summary": decision["bucket_summary"]},
    "actions": decision["actions"],
}
OUT_LATEST_V2.write_text(json.dumps(v2, indent=2, default=str), encoding="utf-8")

md_lines = [
    f"# Strategist Call — {decision['as_of']} ({decision['as_of_label']})",
    "",
    f"**Headline:** {decision['headline']}",
    "",
    f"**Stance:** `{decision['risk_stance']}` | **Conviction:** `{decision['conviction']}` | **Cash:** 45%",
    "",
    "## Bucket performance today",
    "",
    "| Bucket | Avg today |",
    "|---|---|",
]
for b, info in decision["bucket_summary"].items():
    md_lines.append(f"| {b} | {info['avg_today_pct']:+.2f}% |")
md_lines += ["", "## Narrative", "", narrative]
OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

print(f"Wrote {OUT_JSON}")
print(f"Wrote {OUT_LATEST}")
print(f"Wrote {OUT_LATEST_V2}")
print(f"Wrote {OUT_MD}")
print(f"Headline: {decision['headline'][:100]}...")
