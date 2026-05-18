"""Write today's (Mon May 18) strategist call as cursor-claude-manual.

Builds on the v2 pipeline output (which has all gap fixes applied) and
adds human-strategist context: post-mortem learnings, IMF post-print
positioning, FIPI flow read, and explicit per-trade plans.

Saves to:
  - data/_strategist/2026-05-18.json    (v1 legacy schema, for UI)
  - data/_strategist/latest.json        (UI consumer)
The v2 cache (latest_v2.json) is already in place from the pipeline.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Helpers: pull live data points
# ---------------------------------------------------------------------------
def _latest(parquet_path: str, col: str) -> float | None:
    p = Path(parquet_path)
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if col not in df.columns:
        return None
    return float(df[col].dropna().iloc[-1])


def _ret_pct(parquet_path: str, col: str,
              start_offset_rows: int) -> float | None:
    p = Path(parquet_path)
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if col not in df.columns or len(df) < start_offset_rows + 1:
        return None
    s = df[col].dropna().tail(start_offset_rows + 1)
    if len(s) < 2:
        return None
    return float(s.iloc[-1] / s.iloc[0] - 1)


# ---------------------------------------------------------------------------
# Macro snapshot
# ---------------------------------------------------------------------------
brent  = _latest("data/macro/brent.parquet",  "value")
wti    = _latest("data/macro/wti.parquet",    "value")
gold   = _latest("data/macro/gold.parquet",   "value")
btc    = _latest("data/macro/btc.parquet",    "value")
copper = _latest("data/macro/copper.parquet", "value")
cotton = _latest("data/macro/cotton.parquet", "value")
pkr    = _latest("data/macro/usdpkr.parquet", "value")
kse    = _latest("data/macro/kse100.parquet", "kse100_close")

brent_5d  = _ret_pct("data/macro/brent.parquet", "value", 5)
brent_21d = _ret_pct("data/macro/brent.parquet", "value", 21)
gold_21d  = _ret_pct("data/macro/gold.parquet", "value", 21)
copper_21d= _ret_pct("data/macro/copper.parquet", "value", 21)
kse_5d    = _ret_pct("data/macro/kse100.parquet", "kse100_close", 5)
kse_21d   = _ret_pct("data/macro/kse100.parquet", "kse100_close", 21)

# FIPI 5d net
fipi_5d_net = None
try:
    fipi = pd.read_parquet("data/flows/fipi_daily.parquet")
    fipi_5d_net = float(fipi.tail(5)["foreign_net_pkr_mn"].sum())
except Exception:
    pass

# v2 cache (with all gap fixes applied)
v2 = json.loads(Path("data/_strategist/latest_v2.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Strategist narrative (human-in-the-loop on top of v2)
# ---------------------------------------------------------------------------
headline = (
    "POST-IMF DERISK COMPLETE — KSE-100 -2.88% Mon-Fri, banks worst "
    "(-4.07%). Now LIGHT-OVERWEIGHT E&P (OGDC HIGH conv), START small "
    "Cement shorts (DGKC/KOHC auto-flagged), HOLD 50% cash through "
    "IMF outcome confirmation."
)

macro_lens = (
    f"Brent USD {brent:.2f}/bbl ({brent_21d*100:+.1f}% 21d) — sustained "
    "geopolitical premium with WTI lagging => structural Brent floor "
    "for E&P revenue. PKR USD {pkr:.2f} flat. KSE-100 {kse:.0f} closed "
    f"({kse_5d*100:+.2f}% 5d, {kse_21d*100:+.2f}% 21d) after 5 down sessions. "
    "FIPI: foreign net-selling 3 days running with Banks the most-sold "
    f"sector every day (-USD 0.84M, -0.61M, -0.95M). Locals absorbing "
    f"the flow. 5d cumulative foreign net = PKR {fipi_5d_net:+.2f} mn. "
    "This is textbook pre-IMF derisk. The new pre_event_derisk macro "
    "driver (added today) correctly flips Banking from +4 to +1 tilt "
    "and lifts E&P from +6 to +7."
).format(pkr=pkr, kse=kse, kse_5d=kse_5d, kse_21d=kse_21d, brent=brent, brent_21d=brent_21d, fipi_5d_net=fipi_5d_net)

behavioural_lens = (
    "Foreign flow tells the story: -USD 2.4M net foreign Mon-Fri, all "
    "of it concentrated in Banks. Local insurance / pension money was "
    "the absorber — which is why HBL/UBL/BAHL printed -6 to -7% even "
    "though no fundamental news hit them. Pattern is consistent with "
    "the 2024-07 IMF mission delay drawdown (banks -8% over 6 sessions, "
    "E&P held flat). Expected reaction to Friday's IMF print (assumed "
    "successful SLA): 1-2 day relief rally first in Banks (mean reversion), "
    "then sustained leg in E&P on Brent floor + dividend bid. Risk case: "
    "if the mission ended hawkish / extended, second leg down -2 to -4% "
    "in Banks; E&P would HOLD or even lift on safe-haven rotation."
)

key_drivers = [
    f"PRE-EVENT-DERISK macro driver active (severity=MILD, foreign net-sell streak 3d)",
    f"Brent {brent:.0f}/bbl, +20% in 21d — structural E&P tailwind",
    f"Foreign net-selling banks 3 days running ($0.84M, $0.61M, $0.95M)",
    f"KSE-100 -2.88% Mon-Fri — broad derisk validated",
    "E&P sector tilt +7 (STRONG TAILWIND) — defensive winner",
    "Banking sector tilt +1 (was +4 — driver fix landed today)",
    "Cement tilt -4 (STRONG HEADWIND) — DGKC/KOHC auto-promoted to shorts",
]

key_risks = [
    "Brent breaks $100 -> E&P thesis weakens; trim positions",
    "Foreign flow reverses sharply (1-day +USD 5M+) -> sub-relief rally in Banks; rotate",
    "IMF outcome delayed / hawkish -> banks another -3-4% leg; tighten Cement shorts",
    "TRG earnings May 18-22 window — single-name event risk",
    "Cement names already -4% week-on-week -> short squeeze possible if SLA reached",
    "USD/PKR weakness >280 -> reset macro engine: PKR_weak driver flips Banks back to positive",
]

narrative_body = """Monday open after a -2.88% derisk week. The fundamental story
is clean and the gap-fix work from this morning re-aligned the macro
engine with the truth on the tape.

POST-MORTEM CONCLUSIONS (May 11-15 week):
- Banks were the worst sector (-4.07%, HBL -7.17%, UBL -6.41%, BAHL -6.40%)
  even though the macro engine read Banking +4 BULLISH all week.
- E&P held -0.98% (alpha +1.9pp vs KSE-100) — defensive thesis correct.
- Cement -4.17% — flagged correctly as -4 tilt; auto-short fix
  surfaces DGKC and KOHC today.
- 4 days of LLM strategist had no opinion at all (BadRequestError
  from invalid Anthropic key) — the rule-based fallback wrote
  empty stubs. This is the meta-gap; user has been notified.

GAPS FIXED TODAY:
1. macro_impact.py: added `pre_event_derisk` driver. Detects
   IMF events <=5 days, foreign net-sell streak >=2d, universe 5d
   <=-2%. Penalises Banking -3 and Conglomerate -2, lifts E&P +2.
   Cement was already -4 so no change needed (already accurate).
2. pipeline.py: overlay bucket downgrades now mirror into
   long_ideas, not just size. ATRL and NPL now show 'action
   downgraded by overlay' in the UI alongside their stops.
3. master_strategist_v2.py: when a sector has tilt <= -3, the
   worst-2 names in it are auto-promoted to short candidates
   even when their individual composite score is only mildly
   negative. DGKC + KOHC come out today.
4. ABL.parquet: re-fetched 22 days of missing data.

TODAY'S POSITIONING:

CORE LONGS (E&P-led, defensive on derisk flow):
- OGDC: BUY HIGH at ~320 area. Stop 5.6% (303). Target +14% (365). Size 6.7%.
        Thesis: Brent floor + BUY_VALUE + sector tilt +7. Conviction
        elevated because pre_event_derisk now correctly lifts E&P.
- ATRL: HOLD (was ADD; overlay downgraded). At 880-900. Existing positions
        OK. Don't add — Brent at 108 squeezes refining margins.
- PPL:  HOLD MEDIUM at 220-230. Already accumulated; stop 7.3% (-16 PKR).
        Best 2-week E&P performer ex-OGDC.
- MARI: HOLD at 645-650. Dividend bid pre-AGM; ride into June.

CASH FLOOR:
- 50% (down from 70-85% last week — pre-event window narrowing).
- If IMF outcome turns positive (Fri-Mon next week), deploy 30% into
  Banks (NBP/MCB) for relief-rally trade.
- If outcome delayed/hawkish, drop to 70% cash and add to Cement shorts.

SHORTS (NEW — from auto-promote):
- DGKC: SHORT at 75-77. -31.7% momentum 150d, sector tilt -4. Cover at +10%.
- KOHC: SHORT at 80-82. -23.6% momentum 150d, sector tilt -4. Cover at +10%.
- Size each at 3% of NAV (=6% gross short). Conviction MEDIUM.
- Risk: short squeeze on IMF SLA — set tight buy-stops at the prior
  day's high.

AVOID UNTIL IMF CLARITY:
- KEL, HUBC, NPL (power; circular debt overhang, leveraged exposure)
- EPCL (chem; commodity squeeze + ENGRO holdco discount widening)
- KAPCO (power; circular debt risk, ranked TRIM by playbook overlay)

WATCH (not actionable yet):
- INDU: 0% sector return last week — autos may rotate up if Brent decays
- COLG: +0.27% last week — only positive name; defensive consumer bid
- TRG: +3.88% last week — tech leadership but earnings in 5-7d, blackout

OPS NOTE: Anthropic API key in GitHub Actions is INVALID (401
AuthenticationError). News-scoring, intraday-session, and the LLM
strategist call have all been failing for 10+ days. User should
rotate the key in repo secrets to restore the news pipeline. Until
then, the rule-based v2 path is the production strategist and Cursor
takes the human-in-the-loop role on overrides like this file.
"""

# ---------------------------------------------------------------------------
# Build the v1-compatible decision file
# ---------------------------------------------------------------------------
actions = []

# Convert v2 long_ideas to v1 actions (with overlay-synced buckets)
for idea in v2["long_ideas"]["ideas"]:
    pp = idea.get("position_plan") or {}
    actions.append({
        "symbol":            idea["symbol"],
        "bucket":            idea["action"],
        "conviction":        idea["conviction"],
        "sector":            idea["sector"],
        "target_weight_pct": pp.get("position_size_pct"),
        "reason":            idea.get("why", ""),
        "contributing_signals": idea.get("key_drivers", []),
        "stop_loss_pct":     pp.get("stop_loss_pct"),
        "target_pct":        pp.get("target_pct"),
        "stop_loss_price":   pp.get("stop_loss_price"),
        "target_price":      pp.get("target_price"),
    })

# Add shorts
for s in v2["short_ideas"]["ideas"]:
    actions.append({
        "symbol":     s["symbol"],
        "bucket":     "SHORT",
        "conviction": s["conviction"],
        "sector":     s["sector"],
        "target_weight_pct": 3.0,
        "reason":     s.get("why", ""),
        "contributing_signals": s.get("key_drivers", []),
    })

# Add explicit AVOID list
for sym, reason in [
    ("KEL",   "Power circular-debt overhang; tilt +3 sector but heavy leverage"),
    ("HUBC",  "Power circular-debt overhang"),
    ("NPL",   "WATCH (overlay-downgraded); high-vol despite +3 sector tilt"),
    ("EPCL",  "Chem squeeze + ENGRO holdco discount; max_bucket=AVOID via playbook"),
    ("KAPCO", "Power; max_bucket=AVOID via imf_review_mission_week"),
]:
    actions.append({
        "symbol":     sym,
        "bucket":     "AVOID",
        "conviction": "MEDIUM",
        "target_weight_pct": 0,
        "reason":     reason,
        "contributing_signals": [],
    })

# Cash floor row
actions.insert(0, {
    "symbol":     None,
    "bucket":     "CASH",
    "conviction": "HIGH",
    "target_weight_pct": 50.0,
    "reason":     ("Pre-event derisk window narrowing but not closed; "
                    "hold 50% cash to react to IMF outcome (relief or delay)."),
    "contributing_signals": ["pre_event_derisk MILD",
                              "fipi_5d_net=-2.4M PKR mn"],
})

now_utc = datetime.now(timezone.utc)
decision = {
    "as_of":              "2026-05-18",
    "as_of_local":        now_utc.isoformat(),
    "generated_at":       now_utc.isoformat(),
    "model":              "cursor-claude-sonnet-4-5-manual",
    "thinking_budget":    0,
    "fallback_used":      False,
    "agrees_with_phase1": False,
    "phase1_disagreement_note": (
        "Phase 1 is still in cash via the 150d log-return filter. "
        "Strategist overrides with E&P tactical longs because the "
        "pre_event_derisk driver lifts E&P +2 (validated -0.98% vs "
        "market -2.88% last week) and OGDC's BUY_VALUE + +19% 150d "
        "momentum justifies a 6-7% position even with the broad "
        "filter off."),
    "risk_stance":        "CAUTIOUS",
    "conviction":         "MEDIUM",
    "headline":           headline,
    "macro_lens":         macro_lens,
    "behavioural_lens":   behavioural_lens,
    "key_drivers":        key_drivers,
    "key_risks":          key_risks,
    "narrative":          narrative_body,
    "actions":            actions,
    "international_lens": {
        "commodities": {
            "brent_usd": brent,
            "wti_usd":   wti,
            "gold_usd":  gold,
            "copper_usd": copper,
            "tilt": "geopolitical_oil_premium_persists",
        },
    },
    "briefing_summary": v2.get("briefing_summary", {}),
    "v2_cache_ref":      "data/_strategist/latest_v2.json",
    "gap_fixes_applied": [
        "macro_impact.pre_event_derisk driver",
        "pipeline._sync_overlay_changes bucket downgrade",
        "stock_scorer auto-promote sector_tilt<=-3 to shorts",
        "ABL.parquet backfill (22 days)",
    ],
}

# Persist
out_dated   = Path("data/_strategist/2026-05-18.json")
out_latest  = Path("data/_strategist/latest.json")
out_dated.write_text(json.dumps(decision, indent=2, default=str),
                       encoding="utf-8")
out_latest.write_text(json.dumps(decision, indent=2, default=str),
                       encoding="utf-8")

print(f"Wrote {out_dated}")
print(f"Wrote {out_latest}")
print()
print(f"Headline: {headline[:120]}")
print(f"Stance:   {decision['risk_stance']}  ({decision['conviction']})")
print(f"Actions:  {len(actions)}")
print()
print("Top 5 actions:")
for a in actions[:5]:
    sym = a.get("symbol") or "-"
    bucket = a.get("bucket", "?")
    tw = a.get("target_weight_pct")
    tw_s = f"{tw:.1f}%" if isinstance(tw, (int, float)) else "-"
    print(f"  {sym:<7} {bucket:<6} {a.get('conviction','?'):<7} size={tw_s}")
