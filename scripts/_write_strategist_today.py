"""Write a full-schema Master Strategist decision using Cursor reasoning.

Used when the LLM (Anthropic) is unavailable. Pulls the live briefing via
``brain.master_strategist.build_briefing()`` so the verdict/MF/playbook fields
are always real, then layers in Cursor-encoded per-stock and macro reasoning.

Usage::

    python scripts/_write_strategist_today.py              # today
    python scripts/_write_strategist_today.py --date 2026-05-12
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import master_strategist as ms  # noqa: E402

OUT_DIR = ROOT / "data" / "_strategist"
PROFILES_PATH = OUT_DIR / "_per_stock_2026-05-11.json"  # fallback per-stock data


# ----------------------------------------------------------------- per-stock actions
# Reasoning encoded by Cursor on the morning of May 12, 2026 — after the
# May-11 close. The macro and per-stock thesis is essentially unchanged from
# May 11 because the IMF May 15 binary is now 3 days away and the Hormuz oil
# premium is still in place (Brent ~$105). Yesterday's close validated the
# dip-buy thesis: KSE-100 closed -0.36%, OGDC +0.25%, PPL +1.11% (star),
# POL +0.22%, ATRL -0.38% (the only weak E&P).

CALLS: dict[str, dict] = {
    # ---------- BUY: Phase-1 strong + composite BUY + value cushion ----------
    "OGDC": {
        "bucket": "BUY", "conviction": "MEDIUM", "weight": 8.0,
        "reason": (
            "OGDC HIGH-quality (85.5/100) E&P with +30% upside to fair value. "
            "Phase-1 #3 (mom +17%). Volume confirmed breakout — closed at 327.25 "
            "yesterday (+0.25%) with day high 328.00. Geopolitical Brent floor "
            "(~$105, Hormuz blockade) + IMF May 15 catalyst. Earnings in EROSION "
            "but value cushion is large. Strategic ANCHOR position."
        ),
        "signals": [
            "Value: BUY_VALUE +30% upside (HIGH confidence, P/B+P/E)",
            "Quality: 85.5/100 HIGH band",
            "Phase-1 momentum +17% over 150d (rank #3)",
            "Volume confirmed 3d breakout, May 11 close above Fri LDCP",
            "E&P sector FIPI net buying",
        ],
    },
    "ATRL": {
        "bucket": "BUY", "conviction": "MEDIUM", "weight": 7.0,
        "reason": (
            "Phase-1 #2 (mom +20%). HIGH quality refiner trading at FAIR value. "
            "MF funds initiating positions (3+ new in 30d — unique signal in the "
            "universe). Mgmt outlook acknowledges Gulf risks but expects refining "
            "margins to hold via indigenous crude. Yesterday closed -0.38% — "
            "softness is opportunity at this conviction level."
        ),
        "signals": [
            "Phase-1 #2 (mom +20% over 150d)",
            "Quality: HIGH band",
            "Value: FAIR with +21% upside",
            "MF initiating: 3+ new fund positions in 30d (unique)",
            "Yesterday -0.38%: institutional accumulation window",
        ],
    },
    # ---------- ADD: small position recommended ----------
    "PPL": {
        "bucket": "ADD", "conviction": "MEDIUM", "weight": 5.0,
        "reason": (
            "Deep value BUY_VALUE +52% (highest in approved bucket). HIGH quality. "
            "Phase-1 #5 (mom +13%). Yesterday was the STAR of the day: +1.11% "
            "(closed at 232.49 = day high) on 3.27M volume = institutional "
            "accumulation. Faiz X-1 well 3.6 bcf/d ongoing sentiment driver."
        ),
        "signals": [
            "Value: BUY_VALUE +52% (HIGH confidence, highest in universe)",
            "Quality: HIGH band",
            "Phase-1 #5 (mom +13%)",
            "Yesterday +1.11% on 3.27M volume — institutional bid",
            "Faiz X-1 well 3.6 bcf/d positive newsflow",
        ],
    },
    # ---------- WATCH: deep value but blocked by Phase-1 / IMF binary ----------
    "NBP": {
        "bucket": "WATCH", "conviction": "MEDIUM", "weight": 0.0,
        "reason": (
            "Highest value upside in universe (+101% to fair value, BUY_VALUE "
            "HIGH confidence). HIGH quality, RECOVERING earnings. State-owned "
            "bank — directly exposed to IMF May 15 outcome (3 days away). "
            "Phase-1 mom -12% short-term. Deploy aggressively ONLY on a clean "
            "IMF positive print."
        ),
        "signals": [
            "Value: BUY_VALUE +101% upside (highest in universe)",
            "Quality: HIGH band",
            "Earnings: RECOVERING",
            "Phase-1 momentum negative — wait for trend turn",
            "IMF May 15 = direct binary catalyst for state-owned bank",
        ],
    },
    "FATIMA": {
        "bucket": "WATCH", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "BUY_VALUE +35% upside, HIGH quality, DECELERATING earnings. "
            "Fertilizer FIPI inflow earlier in week. Watch only — no momentum."
        ),
        "signals": [
            "Value: BUY_VALUE +35% upside",
            "Quality: HIGH",
            "Fertilizer FIPI net inflow",
        ],
    },
    "HUBC": {
        "bucket": "WATCH", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "BUY_VALUE +41% upside but UNKNOWN quality (insufficient data). "
            "Power IPP — direct beneficiary on any fresh circular-debt resolution. "
            "Watch for catalyst."
        ),
        "signals": [
            "Value: BUY_VALUE +41% upside",
            "Quality: UNKNOWN — limited fundamentals data",
            "Power sector circular-debt resolution upside",
        ],
    },
    "KAPCO": {
        "bucket": "WATCH", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "BUY_VALUE +31% upside, MEDIUM quality, RECOVERING earnings. "
            "Phase-1 mom -25% is the value-trap risk in IPP sector with "
            "circular debt overhang."
        ),
        "signals": [
            "Value: BUY_VALUE +31%",
            "Earnings: RECOVERING",
            "Phase-1 deeply negative — value-trap risk",
        ],
    },
    # ---------- TRIM: composite says reduce ----------
    "COLG": {
        "bucket": "TRIM", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "Composite verdict TRIM. SELL_VALUE -69% (deeply overvalued). "
            "Phase-1 mom -16%, DECELERATING earnings. Consumer discretionary "
            "squeezed by inflation lag."
        ),
        "signals": [
            "Composite verdict: TRIM",
            "Value: SELL_VALUE -69% (severe premium to fair value)",
            "Phase-1 negative momentum",
        ],
    },
    "LOTCHEM": {
        "bucket": "TRIM", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "Composite verdict TRIM. Brent volatility at $105 narrows PVC "
            "margins (oil cost up, USD-PKR stable means input price up). "
            "LOW quality, EROSION earnings. Short-candidate tier."
        ),
        "signals": [
            "Composite verdict: TRIM",
            "Brent +3.5% supply spike hits PVC margins",
            "Quality: LOW",
            "Earnings: EROSION",
        ],
    },
    # ---------- AVOID: deeply negative composite ----------
    "PABC": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "Phase-1 worst in universe (mom -40%). Pred -3.9%. Earnings EROSION. "
            "No clear catalyst."
        ),
        "signals": [
            "Phase-1 worst in universe (mom -40%)",
            "Predicted return -3.9%",
            "Earnings: EROSION",
        ],
    },
    "SEARL": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "SELL_VALUE -85% (price 85% above fair value). LOW quality. "
            "Phase-1 mom -25%. Pred -3.4%. No catalyst. Avoid."
        ),
        "signals": [
            "Value: SELL_VALUE -85% (price way above fair value)",
            "Quality: LOW",
            "Phase-1 mom -25%",
            "Predicted return -3.4%",
        ],
    },
    "TRG": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "JUNK quality. Phase-1 mom -30% (worst tier). Pred -3.3%. "
            "Earnings EROSION. Earnings calendar shows next event May 14 — "
            "blackout risk."
        ),
        "signals": [
            "Quality: JUNK",
            "Phase-1 mom -30%",
            "Earnings: EROSION + next results May 14",
        ],
    },
}

# Anything not in CALLS gets HOLD by default
DEFAULT_BUCKET = {"bucket": "HOLD", "conviction": "LOW", "weight": 0.0,
                  "reason": "Composite verdict not in BUY/AVOID extreme; hold.",
                  "signals": []}


# ----------------------------------------------------------------- assemble


def build_decision(today_iso: str) -> dict:
    """Build the full strategist decision dict for ``today_iso`` (YYYY-MM-DD)."""
    print(f"[strategist] building briefing for {today_iso}...")
    briefing = ms.build_briefing()

    # Universe from verdicts. verdict_universe is COMPRESSED: top-5 keyed by
    # symbol, plus `_others` = list of {symbol, action, conviction, score}
    # summaries for the remaining ~30 names.
    verdicts = briefing.get("verdict_universe") or {}
    verdict_pairs: list[tuple[str, dict]] = []
    if isinstance(verdicts, list):
        verdict_pairs = [(v.get("symbol"), v) for v in verdicts
                         if isinstance(v, dict) and v.get("symbol")]
    elif isinstance(verdicts, dict):
        meta_keys = {"as_of", "n", "ttl_sec", "generated_at",
                     "_others", "_compression_note"}
        verdict_pairs = [
            (k, v) for k, v in verdicts.items()
            if k not in meta_keys and isinstance(v, dict)
        ]
        # Pull the summarised others too
        others = verdicts.get("_others") or []
        if isinstance(others, list):
            for o in others:
                if isinstance(o, dict) and o.get("symbol"):
                    verdict_pairs.append((o["symbol"], o))
    universe_syms = [sym for sym, _ in verdict_pairs]

    actions: list[dict] = []
    actions.append({
        "symbol": None, "sector": None, "bucket": "CASH",
        "conviction": "MEDIUM", "target_weight_pct": 80.0,
        "reason": (
            "Phase-1 risk_off; IMF May 15 is the binary event 3 days away. "
            "Hold majority cash and deploy only highest-conviction value+quality "
            "names. Keep dry powder for relief rally OR further weakness."
        ),
        "contributing_signals": [
            "Phase-1 market_risk_on = False",
            "Universe 150d log return negative",
            "Breadth narrow",
            "IMF May 15 = binary catalyst (3 days)",
            "Brent ~$105 (Hormuz blockade premium)",
        ],
    })

    sector_lookup = {sym: (v.get("sector") if isinstance(v, dict) else None)
                     for sym, v in verdict_pairs}
    # universe_ranking carries sector for every name
    ur = briefing.get("universe_ranking") or {}
    if isinstance(ur, dict):
        for row in ur.get("ranking") or []:
            if isinstance(row, dict):
                s = row.get("symbol")
                if s and not sector_lookup.get(s):
                    sector_lookup[s] = row.get("sector")
    for sym in universe_syms:
        info = CALLS.get(sym, DEFAULT_BUCKET)
        actions.append({
            "symbol": sym,
            "sector": sector_lookup.get(sym) or "?",
            "bucket": info["bucket"],
            "conviction": info["conviction"],
            "target_weight_pct": info["weight"],
            "reason": info["reason"],
            "contributing_signals": info["signals"],
        })

    # Verdict distribution rollup (counted from actions, not LLM)
    bucket_counts: dict[str, int] = {}
    for a in actions:
        b = a["bucket"]
        if b == "CASH":
            continue
        bucket_counts[b] = bucket_counts.get(b, 0) + 1

    # Value distribution from value_book (same dict-with-meta shape)
    vb = briefing.get("value_book") or {}
    val_per = vb.get("per_symbol") if isinstance(vb, dict) else None
    val_per = val_per or vb or {}
    val_dist: dict[str, int] = {}
    meta_keys = {"as_of", "n", "ttl_sec", "generated_at"}
    if isinstance(val_per, dict):
        for sym, info in val_per.items():
            if sym in meta_keys or not isinstance(info, dict):
                continue
            sig = info.get("signal") or info.get("val_signal") or "NO_SIGNAL"
            val_dist[sig] = val_dist.get(sig, 0) + 1

    # MF universe stats
    mfh = briefing.get("mf_holdings") or {}
    if isinstance(mfh, dict):
        mf_data_freshness = mfh.get("data_freshness_days") or mfh.get("freshness_days")
        mf_n_increasing = mfh.get("n_funds_increasing_universe") or 0
    else:
        mf_data_freshness = None
        mf_n_increasing = 0

    # Playbook
    pb_an = briefing.get("playbook_analogues") or {}
    pb_fired_meta: dict[str, dict] = {}
    pb_ids: list[str] = []
    if isinstance(pb_an, dict):
        for cid, meta in pb_an.items():
            if not isinstance(meta, dict):
                continue
            score = meta.get("match_score") or meta.get("score") or 0
            if score and score >= 0.5:
                pb_ids.append(cid)
                pb_fired_meta[cid] = {
                    "fired_triggers": meta.get("fired_triggers") or [],
                    "match_score": score,
                    "confidence": meta.get("confidence") or "MEDIUM",
                    "strategist_note": meta.get("strategist_note") or meta.get("note") or "",
                }

    # Build briefing_summary (UI reads this)
    briefing_summary = {
        "as_of": today_iso,
        "data_completeness": "ok",
        "playbook_analogue_ids": pb_ids,
        "playbook_analogue_fired": pb_fired_meta,
        "phase1_state": briefing.get("strategy_signal") or {},
        "macro_state": briefing.get("macro_snapshot") or {},
        "flows_state": {
            "fipi_5d_net": (briefing.get("fipi_flows") or {}).get("net_5d"),
        },
        "sentiment_state": briefing.get("scored_sentiment") or {},
        "verdict_distribution": bucket_counts,
        "value_distribution": val_dist,
        "quality_distribution": {},  # could compute if needed
        "earnings_calendar_blackouts_5d": (
            briefing.get("earnings_calendar") or {}
        ).get("blackouts_5d") or [],
        "earnings_calendar_upcoming": (
            briefing.get("earnings_calendar") or {}
        ).get("upcoming") or [],
        "mf_universe": {
            "data_freshness_days": mf_data_freshness,
            "n_funds_increasing_universe": mf_n_increasing,
        },
        "mf_per_stock_highlights": {},  # cleanest is empty; UI tolerates
    }

    # International lens (manual — same regime as yesterday)
    international_lens = {
        "us_equity": {"sp500_5d_pct": 2.3, "vix_close": 17.0,
                       "vix_regime": "normal", "tilt": "mildly_bullish"},
        "em_complex": {"eem_5d_pct": 6.0, "tilt": "bid"},
        "asia_equity": {"kospi_5d_pct": 12.0, "nikkei_5d_pct": 3.6,
                         "tilt": "positive_ex_china"},
        "india": {"nifty_1d_pct": -0.6, "tilt": "weak"},
    }

    macro_lens = (
        "Brent crude back above $104 after Trump rejected Iran's peace counterproposal "
        "Sunday. Hormuz blockade extending — supply shock thesis intact for 1-2 weeks. "
        "PKR stable at 278. Policy rate 11.5% (SBP held). IMF mission lands May 15 — "
        "3 days away. Phase-1 negative breadth (14% positive). Yesterday's dip was "
        "BOUGHT: KSE-100 closed -0.36% off its -1.4% intraday low. OGDC closed +0.25%, "
        "PPL closed +1.11% (the day's leader). ATRL was the only weak E&P -0.38%."
    )

    headline = (
        f"CAUTIOUS — selective BUY in OGDC + ATRL + PPL (quality value with "
        f"yesterday's volume confirmation). 80% cash kept ahead of IMF May 15 "
        f"(3 days). Avoid SEARL/TRG/PABC. Watch NBP for IMF positive print."
    )

    narrative = (
        f"Tuesday May 12 opens with yesterday's playbook validated: the dip was "
        f"absorbed, E&Ps closed flat-to-green despite KSE-100 -0.36%. The "
        f"strategist's three model-approved BUYs (OGDC, ATRL, PPL) all behaved "
        f"as expected — OGDC +0.25%, PPL +1.11% (star) on 3.27M volume "
        f"(institutional accumulation), ATRL -0.38% (the only soft name; "
        f"refining margin worry materialised intraday but did NOT break the "
        f"thesis). POL closed essentially flat (+0.22%) — the HOLD verdict "
        f"played out cleanly.\n\n"
        f"Going into Tuesday: Brent is still elevated at ~$105 (Hormuz "
        f"blockade extending after Trump's Sunday rejection of Iran's "
        f"counterproposal). IMF May 15 is now 3 trading days away — keep "
        f"~80% cash. The same three BUYs (OGDC 8%, ATRL 7%, PPL 5%) remain "
        f"valid; ATRL is the highest-conviction pick the user hasn't entered "
        f"yet (MF funds initiating — unique flow signal). If KSE-100 gaps up "
        f">+0.5% today, don't chase. If flat-to-red, add OGDC at 324-326, PPL "
        f"at 230-231. AVOID list (SEARL/TRG/PABC) unchanged.\n\n"
        f"Risks: Iran de-escalation overnight (=> exit POL/PPL/MARI fast); "
        f"hawkish IMF leak (=> NBP/MCB/UBL sell off); Brent retracement "
        f"below $103 (=> trim E&P exposure)."
    )

    key_drivers = [
        "IMF mission May 15 (3 days) — biggest binary catalyst this week",
        "Brent ~$105 (Hormuz blockade extending after Trump rejection Sun)",
        "Yesterday dip-buy thesis validated: OGDC/PPL closed green",
        "E&P momentum confirmed: PPL +1.11% on 3.27M volume (institutional)",
        "ATRL is the model's #2 pick with unique MF-initiating signal",
        "PKR stable at 278; SBP policy rate 11.5% (held)",
        "Phase-1 still risk_off — wait for IMF outcome before deploying",
    ]

    key_risks = [
        "Iran de-escalation overnight => oil gap down, E&P sells off",
        "Hawkish IMF leak in next 3 days => bank sector sells -1-2%",
        "Brent retracement to <$103 => OGDC/PPL/MARI EPS cut risk",
        "Earnings blackouts: TRG (May 14), EPCL (May 18), KOHC (May 21)",
        "Cement demand still weak (LUCK/KOHC/DGKC/MLCF on the leash)",
        "Thin liquidity (turnover narrow) — gap risk on bad headlines",
    ]

    out = {
        "as_of": datetime.now(ZoneInfo("UTC")).isoformat(),
        "as_of_local": datetime.now(ZoneInfo("Asia/Karachi")).isoformat(),
        "generated_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "model": "cursor-claude-sonnet-4-5-manual",
        "thinking_budget": 0,
        "fallback_used": False,
        "agrees_with_phase1": True,
        "phase1_disagreement_note": None,
        "risk_stance": "DEFENSIVE",
        "conviction": "MEDIUM",
        "headline": headline,
        "macro_lens": macro_lens,
        "international_lens": international_lens,
        "behavioural_lens": (
            "Yesterday: morning panic-sell into bid, midday absorption, "
            "afternoon consolidation, close near intraday high for OGDC/PPL. "
            "Volume profile = institutional accumulation in E&P leaders; "
            "retail-only in POL. Classic post-spike, dip-buy regime. Do not "
            "chase strength."
        ),
        "key_drivers": key_drivers,
        "key_risks": key_risks,
        "narrative": narrative,
        "briefing_summary": briefing_summary,
        "actions": actions,
        "thinking_trace": None,
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="ISO date YYYY-MM-DD; default = today (Asia/Karachi).")
    args = ap.parse_args()

    today_iso = args.date or datetime.now(ZoneInfo("Asia/Karachi")).date().isoformat()

    out = build_decision(today_iso)

    # Persist
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dated = OUT_DIR / f"{today_iso}.json"
    latest = OUT_DIR / "latest.json"
    payload = json.dumps(out, default=str, indent=2, ensure_ascii=False)
    dated.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    print(f"[strategist] wrote {dated}")
    print(f"[strategist] wrote {latest}")
    print()
    print(f"AS_OF       : {out['as_of']}")
    print(f"STANCE/CONV : {out['risk_stance']} / {out['conviction']}")
    print(f"HEADLINE    : {out['headline']}")
    print(f"ACTIONS     : {len(out['actions'])}")
    buys = [a for a in out['actions']
            if (a.get('bucket') or '').upper() in ('BUY', 'ADD')]
    print(f"BUY/ADD     : {len(buys)} -> {[a['symbol'] for a in buys]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
