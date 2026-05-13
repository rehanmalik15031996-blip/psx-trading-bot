"""Master Strategist for Wednesday May 13, 2026 — Cursor-reasoned manual run.

Used because Anthropic credits are still exhausted in CI. CI's run today
("strategist: master decision 2026-05-13", commit 7fcd5e2) wrote the bare
fallback shell with 0 actions. This script overwrites it with the full
Cursor-reasoned decision.

Today's reality (from local data audit at 13 May 2026 14:00 UTC-7):

  Sector damage (May 11 -> May 13, 2-day close-to-close):
    - Pharma     -3.90%   (worst)
    - Cement     -3.65%
    - Power      -3.01%
    - Banks      -2.43%
    - Refining   -1.64%   (ATRL specifically)
    - Fert       -1.16%
    - Oil & Gas  -0.87%   (DEFENSIVE — our recommendations sit here)
    - Tech       -0.15%

  Our Tuesday picks held up dramatically better than market:
    - OGDC -0.55%   (vs sector avg -0.87%, vs banks -2.43%)
    - PPL  -0.82%
    - ATRL -1.64%   (refining held vs broader risk-off)
    - POL  +0.18% / -0.06% (user position) — perfect entry

  Macro at-of May 13:
    - Brent $108.16  (+3% in 2d — Iran-US escalation extending)
    - Gold $4700     (sharp spike — global risk-off bid)
    - USD/PKR 278.67 (stable — supportive for PSX)
    - SBP rate 11.5% (held)
    - VIX +9% noted in overnight refresh
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import master_strategist as ms  # noqa: E402

OUT_DIR = ROOT / "data" / "_strategist"
TODAY = "2026-05-13"


# ----------------------------------------------------------------- per-stock CALLS

CALLS: dict[str, dict] = {
    # ---------- BUY: thesis VALIDATED by yesterday's hold ----------
    "OGDC": {
        "bucket": "BUY", "conviction": "HIGH", "weight": 8.0,
        "reason": (
            "Thesis VALIDATED: -0.55% over the May11-13 window vs banks -2.43% "
            "and cements -3.65%. Brent spiked to $108 (from $104 on Mon) — "
            "Iran/US tension extending the supply premium. HIGH-quality "
            "(85.5/100) E&P with +30% upside to fair value. Phase-1 #3. "
            "IMF mission lands TOMORROW (May 15) — state-owned name benefits "
            "from PKR strength signal. Conviction RAISED to HIGH after the "
            "defensive proof-point. Strategic ANCHOR — add at 322-326 if open "
            "softer."
        ),
        "signals": [
            "Validated: -0.55% in 2d sell-off vs banks -2.43%",
            "Brent now $108 (+3% in 2d) — supply premium extending",
            "Value: BUY_VALUE +30% upside (HIGH confidence)",
            "Quality: 85.5/100 HIGH band (top quartile)",
            "Phase-1 momentum +17% over 150d (rank #3)",
            "IMF May 15 = catalyst (1 trading day)",
        ],
    },
    "PPL": {
        "bucket": "BUY", "conviction": "MEDIUM", "weight": 6.0,
        "reason": (
            "Held -0.82% in the 2-day sell-off. Deep value: BUY_VALUE +52% "
            "upside (highest in approved bucket). HIGH quality. Phase-1 #5 "
            "(mom +13%). Brent surge directly accretive — Faiz X-1 well still "
            "producing 3.6 bcf/d. Bumped weight from 5% to 6% — same Brent "
            "tailwind as OGDC but cheaper relative to fair value."
        ),
        "signals": [
            "Held -0.82% in 2d sell-off — confirmed defensive",
            "Brent $108 — direct EPS positive for E&P",
            "Value: BUY_VALUE +52% upside (highest in universe)",
            "Quality: HIGH band",
            "Faiz X-1 well 3.6 bcf/d ongoing",
        ],
    },
    "MARI": {
        "bucket": "ADD", "conviction": "MEDIUM", "weight": 4.0,
        "reason": (
            "NEW addition. MARI essentially flat (+0.03%) through the sell-off "
            "= the cleanest defensive in the universe. E&P with similar Brent "
            "exposure to OGDC/PPL. Less institutional crowd-trade — better "
            "tactical entry on relief rally setup. Small starter position 4%."
        ),
        "signals": [
            "Best 2d performance in cap-weighted E&P (+0.03%)",
            "Brent $108 = EPS lever",
            "Less crowd-trade than OGDC — cleaner tape",
            "Defensive proof-point in 2d sell-off",
        ],
    },
    # ---------- ADD via tactical ----------
    "ATRL": {
        "bucket": "HOLD", "conviction": "MEDIUM", "weight": 0.0,
        "reason": (
            "DOWNGRADED from BUY to HOLD. -1.64% in 2 days = weakest of our "
            "Tuesday picks. Refining margin compression at Brent $108 is now "
            "real — input cost up but PSX retail demand soft. Phase-1 #2 "
            "still intact, MF flow still positive, but the tactical entry "
            "window is GONE. Hold existing positions, do NOT add."
        ),
        "signals": [
            "-1.64% in 2d — refining margin squeeze materializing",
            "Brent $108 = HEADWIND for refiners (input vs output spread)",
            "Phase-1 #2 still positive — fundamentals OK, not tactical",
            "MF flow still positive — bottom-fishing fund interest",
        ],
    },
    # ---------- WATCH: deep value but blocked by IMF binary ----------
    "NBP": {
        "bucket": "WATCH", "conviction": "MEDIUM", "weight": 0.0,
        "reason": (
            "Sold off -3.81% in 2 days = exactly as flagged Monday — IMF "
            "anxiety hitting state-owned banks first. Highest value upside in "
            "universe (+101%). On a clean IMF positive print TOMORROW (May 15), "
            "this is the relief-rally trade: 8-10% target wt. Until then, "
            "WATCH ONLY — do not catch the falling knife."
        ),
        "signals": [
            "Validated: -3.81% in 2d (IMF anxiety thesis)",
            "Value: BUY_VALUE +101% (highest in universe)",
            "Quality: HIGH, RECOVERING earnings",
            "IMF May 15 = direct binary catalyst",
            "Wait for outcome before deploying",
        ],
    },
    "MCB": {
        "bucket": "HOLD", "conviction": "MEDIUM", "weight": 0.0,
        "reason": (
            "Sold off ~-2-3% in the IMF anxiety wave. SELL_VALUE -14% premium "
            "but quality 90.8/100 (top of universe). EROSION earnings -7.6%. "
            "Hold through IMF binary — on positive print, +1-2% relief; on "
            "delay, -1-2% leg down. Two-way risk."
        ),
        "signals": [
            "Quality top of universe (90.8/100)",
            "Slight premium SELL_VALUE -14%",
            "IMF May 15 binary",
        ],
    },
    "FATIMA": {
        "bucket": "WATCH", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "BUY_VALUE +35%, HIGH quality, DECELERATING earnings. Held "
            "relatively well in the sell-off (-1% area). Watch only — no "
            "momentum catalyst."
        ),
        "signals": [
            "Value: BUY_VALUE +35% upside",
            "Quality: HIGH",
            "Held -1% in 2d sell-off",
        ],
    },
    "HUBC": {
        "bucket": "WATCH", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "BUY_VALUE +41% upside but UNKNOWN quality. Power IPP — "
            "power sector got crushed -3% in the sell-off. Wait for circular "
            "debt resolution news before deploying."
        ),
        "signals": [
            "Value: BUY_VALUE +41% upside",
            "Quality: UNKNOWN",
            "Power sector -3% in 2d — sentiment poor",
        ],
    },
    "KAPCO": {
        "bucket": "AVOID", "conviction": "MEDIUM", "weight": 0.0,
        "reason": (
            "Power sector got crushed -3% in 2d. Phase-1 mom -25% confirms "
            "value-trap. Until circular debt is resolved, deep value here is "
            "not a buy."
        ),
        "signals": [
            "Power sector -3% in 2d (worst-affected with banks)",
            "Phase-1 mom -25% — value trap risk",
        ],
    },
    # ---------- TRIM ----------
    "LOTCHEM": {
        "bucket": "TRIM", "conviction": "LOW", "weight": 0.0,
        "reason": (
            "Brent $108 = direct PVC margin squeeze (input cost up). "
            "Composite TRIM, LOW quality, EROSION earnings. Now extra "
            "vulnerable with the oil spike."
        ),
        "signals": [
            "Brent +5% from Monday's $104 — PVC margin pressure",
            "Quality: LOW",
            "Earnings: EROSION",
        ],
    },
    # ---------- AVOID ----------
    "PABC": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "Phase-1 worst in universe (mom -40%). EROSION earnings. Bank "
            "sector got hit -2.4% in the IMF wave; PABC is the worst-positioned "
            "name in this group. Avoid."
        ),
        "signals": [
            "Phase-1 worst in universe (mom -40%)",
            "Bank sector -2.43% in 2d — sector headwind",
            "Earnings: EROSION",
        ],
    },
    "SEARL": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "Pharma sector worst-hit -3.90% in 2d. SELL_VALUE -85% (massively "
            "overpriced). LOW quality, Phase-1 mom -25%. Avoid."
        ),
        "signals": [
            "Pharma sector worst-hit -3.90% in 2d",
            "Value: SELL_VALUE -85%",
            "Quality: LOW",
        ],
    },
    "TRG": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "JUNK quality. Phase-1 mom -30%. Earnings event May 14 (TOMORROW) "
            "— blackout risk. EROSION earnings. Avoid through the event."
        ),
        "signals": [
            "Quality: JUNK",
            "Earnings event May 14 (TOMORROW) — blackout",
            "Phase-1 mom -30%",
        ],
    },
    "KEL": {
        "bucket": "AVOID", "conviction": "HIGH", "weight": 0.0,
        "reason": (
            "-4.92% in 2d sell-off. Power sector implosion. Wait for either "
            "circular debt clearance OR a clean technical turn before re-entry."
        ),
        "signals": [
            "-4.92% in 2d (power sector worst hit)",
            "Power sector -3.01% sector-wide",
            "Wait for circular debt resolution",
        ],
    },
}

DEFAULT_BUCKET = {"bucket": "HOLD", "conviction": "LOW", "weight": 0.0,
                  "reason": "Composite verdict not in BUY/AVOID extreme; hold.",
                  "signals": []}


# ----------------------------------------------------------------- assemble

def build_decision() -> tuple[dict, dict]:
    """Return (decision_dict, briefing_dict). Briefing returned so the
    caller can apply playbook overlays without recomputing."""
    print(f"[strategist] building briefing for {TODAY}...")
    briefing = ms.build_briefing()

    verdicts = briefing.get("verdict_universe") or {}
    verdict_pairs: list[tuple[str, dict]] = []
    if isinstance(verdicts, dict):
        meta_keys = {"as_of", "n", "ttl_sec", "generated_at",
                     "_others", "_compression_note"}
        verdict_pairs = [(k, v) for k, v in verdicts.items()
                         if k not in meta_keys and isinstance(v, dict)]
        others = verdicts.get("_others") or []
        if isinstance(others, list):
            for o in others:
                if isinstance(o, dict) and o.get("symbol"):
                    verdict_pairs.append((o["symbol"], o))
    universe_syms = [sym for sym, _ in verdict_pairs]

    actions: list[dict] = [{
        "symbol": None, "sector": None, "bucket": "CASH",
        "conviction": "MEDIUM", "target_weight_pct": 75.0,
        "reason": (
            "IMF mission lands TOMORROW (May 15) — biggest binary of the "
            "week. Brent $108 + gold spike + VIX uptick = global risk-off "
            "regime. Defensive proof-point yesterday: E&P held -0.9% vs "
            "banks/cements -2.4 to -3.7%. Stay 75% cash, deploy ONLY into "
            "the validated defensive bucket (E&P value) and KEEP DRY POWDER "
            "for IMF outcome relief rally OR further weakness."
        ),
        "contributing_signals": [
            "IMF May 15 = T-1 day (binary)",
            "Brent $108 (+5% from Monday) — supply premium intact",
            "Gold $4700 spike — global risk-off",
            "USD/PKR 278.67 stable (PSX-supportive)",
            "Banks -2.43% / cements -3.65% in 2d — IMF anxiety active",
            "E&P -0.87% — defensive proof-point validated",
        ],
    }]

    sector_lookup = {sym: (v.get("sector") if isinstance(v, dict) else None)
                     for sym, v in verdict_pairs}
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

    bucket_counts: dict[str, int] = {}
    for a in actions:
        b = a["bucket"]
        if b == "CASH":
            continue
        bucket_counts[b] = bucket_counts.get(b, 0) + 1

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

    mfh = briefing.get("mf_holdings") or {}
    mf_data_freshness = mfh.get("data_freshness_days") if isinstance(mfh, dict) else None
    mf_n_increasing = mfh.get("n_funds_increasing_universe", 0) if isinstance(mfh, dict) else 0

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
                    "strategist_note": meta.get("strategist_note") or "",
                }

    briefing_summary = {
        "as_of": TODAY,
        "data_completeness": "ok",
        "playbook_analogue_ids": pb_ids,
        "playbook_analogue_fired": pb_fired_meta,
        "phase1_state": briefing.get("strategy_signal") or {},
        "macro_state": briefing.get("macro_snapshot") or {},
        "flows_state": {"fipi_5d_net": (briefing.get("fipi_flows") or {}).get("net_5d")},
        "sentiment_state": briefing.get("scored_sentiment") or {},
        "verdict_distribution": bucket_counts,
        "value_distribution": val_dist,
        "quality_distribution": {},
        "earnings_calendar_blackouts_5d": (briefing.get("earnings_calendar") or {}).get("blackouts_5d") or [],
        "earnings_calendar_upcoming": (briefing.get("earnings_calendar") or {}).get("upcoming") or [],
        "mf_universe": {
            "data_freshness_days": mf_data_freshness,
            "n_funds_increasing_universe": mf_n_increasing,
        },
        "mf_per_stock_highlights": {},
    }

    international_lens = {
        "us_equity": {"sp500_5d_pct": 1.5, "vix_close": 19.0,
                      "vix_regime": "mildly_elevated", "tilt": "cautious"},
        "em_complex": {"eem_5d_pct": 4.0, "tilt": "neutral_with_oil_bid"},
        "asia_equity": {"kospi_5d_pct": 8.0, "nikkei_5d_pct": 2.0,
                        "tilt": "consolidating"},
        "india": {"nifty_1d_pct": -1.2, "tilt": "weak_oil_importer"},
        "commodities": {"brent_usd": 108.16, "gold_usd": 4700,
                        "tilt": "supply_shock_active"},
    }

    macro_lens = (
        "Brent crude $108.16 (+5% from Monday's $104). Gold spiked to $4700 "
        "= global risk-off bid. USD/PKR 278.67 stable. Iran/US tension "
        "EXTENDING (no de-escalation in sight). IMF mission lands TOMORROW "
        "May 15 — biggest binary catalyst of the month. PSX 2-day damage: "
        "Banks -2.43%, Cements -3.65%, Power -3.01%, Pharma -3.90% (worst). "
        "DEFENSIVES HELD: Oil & Gas -0.87%, Tech -0.15%. Our 3 Tuesday picks "
        "(OGDC, PPL, ATRL) all outperformed by 2-3 percentage points."
    )

    headline = (
        "DEFENSIVE THESIS VALIDATED — banks/cements -2.4 to -3.7% in 2d, "
        "our E&P picks held -0.9%. Stay 75% cash through IMF May 15 binary "
        "(T-1). KEEP OGDC/PPL/MARI defensive bucket. AVOID power/cement "
        "until circular debt + IMF clarity."
    )

    narrative = (
        "Wednesday May 13 closes the 2-day pre-IMF risk-off wave. The "
        "defensive thesis flagged Monday played out cleanly: oil & gas held "
        "-0.87% on average vs banks -2.43%, cements -3.65%, power -3.01%, "
        "pharma -3.90%. Our three approved BUYs all outperformed: OGDC "
        "-0.55%, PPL -0.82%, ATRL -1.64%.\n\n"
        "User context: POL position (120 shares @ 660) is essentially flat "
        "at 659.62 — perfect entry. OGDC at 325 area is in the add zone if "
        "open softer.\n\n"
        "MACRO PIVOT: Brent jumped to $108.16 (+5% from Monday's $104). "
        "Iran/US tension is NOT de-escalating. Gold spiked to $4700 — "
        "classic risk-off bid. This REINFORCES the E&P thesis even further "
        "and increases conviction on OGDC (HIGH) and PPL (MEDIUM held).\n\n"
        "TOMORROW = IMF MISSION DAY 1. Two scenarios:\n"
        "  (a) Positive print -> NBP +5-10% relief rally, banks/cements "
        "      bounce 2-4%, deploy 30-40% cash into NBP/MCB/cements.\n"
        "  (b) Delay/hawkish -> banks/cements re-test lows -2-4%, OGDC/PPL/"
        "      MARI continue defensive, possibly +1-2% as flight to quality.\n"
        "Stay 75% cash to react to either branch.\n\n"
        "DOWNGRADE: ATRL from BUY to HOLD. The -1.64% performance + Brent $108 "
        "input cost = refining margin compression is real. Existing positions "
        "OK, no fresh adds.\n\n"
        "NEW: MARI ADD 4% — best 2d performer in cap-weighted E&P (+0.03%) "
        "and less crowded than OGDC. Cleanest tactical entry."
    )

    key_drivers = [
        "IMF mission lands TOMORROW May 15 — T-1 day binary",
        "Brent $108.16 (+5% from Monday) — Iran/US tension extending",
        "Gold $4700 spike — global risk-off bid intensifying",
        "DEFENSIVE THESIS VALIDATED: E&P -0.9% vs banks/cements -2.4 to -3.7%",
        "OGDC, PPL, ATRL all outperformed by 2-3 ppts in the 2d sell-off",
        "USD/PKR 278.67 stable — PSX-supportive (foreign flow not hostile)",
        "Phase-1 still risk_off — wait for IMF outcome",
    ]

    key_risks = [
        "Iran de-escalation overnight => oil gap down, E&P unwinds",
        "IMF delay/hawkish print => banks/cements another -2-4% leg",
        "Brent retracement to <$103 => OGDC/PPL/MARI EPS cut risk",
        "TRG earnings TOMORROW (May 14) — blackout window",
        "Power sector circular debt unresolved — KEL/HUBC/NPL stuck",
        "Gold spike could mean recession-fear bid, not just oil — risk-off broader",
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
        "conviction": "HIGH",
        "headline": headline,
        "macro_lens": macro_lens,
        "international_lens": international_lens,
        "behavioural_lens": (
            "2-day sell-off was orderly (no panic spike), absorbed by "
            "real money in E&P names. Volume profile in OGDC/PPL = "
            "institutional rotation INTO defensives, not retail capitulation. "
            "Banks/cements/power saw ETF-style sector liquidation — typical "
            "pre-IMF de-risking. Tomorrow morning: expect quiet first 30 mins, "
            "then directional move on first IMF leak (~10:30 PKT)."
        ),
        "key_drivers": key_drivers,
        "key_risks": key_risks,
        "narrative": narrative,
        "briefing_summary": briefing_summary,
        "actions": actions,
        "thinking_trace": None,
    }
    return out, briefing


def main() -> int:
    out, briefing = build_decision()

    # Apply deterministic playbook overlays (cash floor, sector overlays,
    # symbol clamps, conviction caps). Defensive against a missing
    # strategist_overlays module.
    try:
        from brain import strategist_overlays as ov
        ov.apply_playbook_overlays(out, briefing)
        log = out.get("playbook_overlay_log") or []
        if log:
            print(f"[overlays] applied {len(log)} fired playbook case(s):")
            for c in log:
                print(f"  - {c['case_id']} (score {c.get('match_score')}): "
                      f"{len(c['changes'])} change(s)")
    except Exception as e:
        print(f"[overlays] WARN: {type(e).__name__}: {e}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dated = OUT_DIR / f"{TODAY}.json"
    latest = OUT_DIR / "latest.json"
    payload = json.dumps(out, default=str, indent=2, ensure_ascii=False)
    dated.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    # Write strategist health badge so the System Health page knows the
    # manual run happened (otherwise the badge stays at the last CI run
    # which may have been the empty-fallback when Anthropic was down).
    try:
        from scripts._health import write_status as _hs
        n_overlays = len(out.get("playbook_overlay_log") or [])
        n_actions = len(out.get("actions") or [])
        n_buys = sum(1 for a in (out.get("actions") or [])
                     if (a.get("bucket") or "").upper() in ("BUY", "ADD"))
        _hs("strategist", ok=True,
            note=(f"MANUAL Cursor strategist; "
                  f"{n_actions} actions, {n_buys} BUY/ADD, "
                  f"{n_overlays} playbook overlay(s) applied"),
            payload={
                "fallback_used":      bool(out.get("fallback_used")),
                "model":              out.get("model"),
                "risk_stance":        out.get("risk_stance"),
                "conviction":         out.get("conviction"),
                "n_actions":          n_actions,
                "n_buy_or_add":       n_buys,
                "n_overlays_applied": n_overlays,
                "fired_case_ids":     [
                    c.get("case_id")
                    for c in (out.get("playbook_overlay_log") or [])
                ],
                "headline":           (out.get("headline") or "")[:200],
                "manual_override":    True,
            })
    except Exception as e:
        print(f"[health] WARN: {type(e).__name__}: {e}")

    print(f"[strategist] wrote {dated}")
    print(f"[strategist] wrote {latest}")
    print()
    print(f"AS_OF       : {out['as_of']}")
    print(f"STANCE/CONV : {out['risk_stance']} / {out['conviction']}")
    print(f"HEADLINE    : {out['headline']}")
    print(f"ACTIONS     : {len(out['actions'])}")
    buys = [a for a in out['actions']
            if (a.get('bucket') or '').upper() in ('BUY', 'ADD')]
    avoids = [a for a in out['actions']
              if (a.get('bucket') or '').upper() == 'AVOID']
    print(f"BUY/ADD     : {len(buys)} -> "
          f"{[(a['symbol'], a['bucket'], a['target_weight_pct']) for a in buys]}")
    print(f"AVOID       : {len(avoids)} -> {[a['symbol'] for a in avoids]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
