"""Write the Monday May 11 Master Strategist decision (Cursor-reasoned)."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path
from datetime import datetime, timezone

decision = {
    "as_of": "2026-05-11",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "model": "cursor-claude-sonnet-4-5",
    "fallback_used": False,
    "risk_stance": "CAUTIOUS",
    "conviction": "MEDIUM",
    "headline": (
        "Selective WATCH mode — US/Iran risk premium + IMF catalyst "
        "create asymmetric opportunity in E&P; avoid broad market exposure. "
        "IMF mission May 15 is the week's key event."
    ),
    "macro_lens": (
        "Oil corrected sharply (-9% Brent in 5 days) after the US-Iran "
        "Strait of Hormuz spike — fear dominated fundamentals. Pakistan "
        "net benefits from lower oil (CAD relief, PKR stability) but E&P "
        "stocks will see short-term pressure. USD/PKR stable at 278 — "
        "no acute FX stress. Policy rate unchanged at 11.5%. IMF mission "
        "arrives May 15 for budget talks — a successful signal could "
        "trigger a 1-2% relief rally. Global risk-off (gold +4%, "
        "safe-haven bid) argues against aggressive positioning. "
        "Institutional buyers (insurance, big-fish) were net buyers on "
        "May 8 suggesting value emerging at current levels."
    ),
    "behavioural_lens": (
        "Retail buyers net +3.9bn PKR on May 8 while foreigners sold "
        "marginally (-0.74bn). This retail-driven market is susceptible "
        "to news-driven panic — US-Iran headlines caused KSE-100 to shed "
        "~1% on May 8. Expect volatility around any Iran/Hormuz update "
        "and the IMF arrival on May 15. Sentiment is bearish-leaning but "
        "not panicked — VIX at 17 globally suggests contained fear."
    ),
    "key_risks": [
        "US-Iran escalation re-escalates → oil spike + global risk-off hit PSX hard",
        "IMF May 15 talks produce negative signals → PKR weakness + market sell-off",
        "Oil stays low → OGDC/PPL/ATRL/MARI earnings estimates cut",
        "Cement sector demand remains weak → LUCK/KOHC/DGKC continue underperforming",
        "Retail sentiment turns → thin liquidity market can gap down quickly",
    ],
    "key_drivers": [
        "IMF mission May 15 — single biggest week catalyst",
        "Oil price direction — Brent $103 post-correction, watch for Hormuz updates",
        "FIPI flows — institutional buying suggests floor; watch Monday flows",
        "PPL new Gambat South well — modest positive for E&P sector confidence",
        "Pakistan export data showing textile resilience (+2% to US)",
    ],
    "actions": [
        {
            "symbol": "OGDC",
            "bucket": "WATCH",
            "conviction": "MEDIUM",
            "reason": (
                "Phase-1 would-pick name. Oil geopolitical risk premium "
                "not fully priced out. E&P sector FIPI net inflow +0.26mn. "
                "Wait for Monday price action before sizing in — if oil "
                "recovers on Iran news, this is the entry point."
            ),
            "contributing_signals": [
                "Phase-1 would-pick #2 (score +0.35)",
                "E&P sector FIPI net buying May 8",
                "PPL new well positive for sector sentiment",
                "Brent geopolitical floor ~$95-100",
            ],
        },
        {
            "symbol": "ATRL",
            "bucket": "WATCH",
            "conviction": "MEDIUM",
            "reason": (
                "Highest Phase-1 score (+0.40). Refining margins "
                "benefit from oil price spread. Defensive earnings "
                "base with domestic exposure. Monitor for breakout "
                "above recent consolidation."
            ),
            "contributing_signals": [
                "Phase-1 would-pick #1 (score +0.40, highest in universe)",
                "Refining margin play on oil volatility",
                "Bullish rule-based signal +1.8%",
            ],
        },
        {
            "symbol": "MCB",
            "bucket": "WATCH",
            "conviction": "LOW",
            "reason": (
                "Phase-1 would-pick. Banks at low valuations. IMF success "
                "May 15 = direct positive catalyst for banking sector. "
                "Wait for IMF outcome before committing capital."
            ),
            "contributing_signals": [
                "Phase-1 would-pick #5",
                "IMF May 15 = bank sector catalyst",
                "Cheap valuations vs regional peers",
            ],
        },
        {
            "symbol": "LOTCHEM",
            "bucket": "ADD",
            "conviction": "LOW",
            "reason": (
                "Only ADD signal from rule engine (+2.3% expected). "
                "Small position acceptable given LOW conviction — "
                "do not size aggressively. Lotte Chemical benefits "
                "from PTA spread and cotton downstream demand."
            ),
            "contributing_signals": [
                "Rule-based ADD signal, score +0.35",
                "Cotton +3.5% in 5 days — downstream positive",
                "Only name with ADD vs all-HOLD universe",
            ],
        },
        {
            "symbol": "LUCK",
            "bucket": "AVOID",
            "conviction": "MEDIUM",
            "reason": (
                "Cement sector under pressure. FIPI net selling in cement "
                "sector. Demand weak, input costs volatile. Rule engine "
                "bearish -1.3%."
            ),
            "contributing_signals": [
                "Rule-based bearish -1.3%",
                "Cement sector FIPI outflow",
                "Demand weakness persists",
            ],
        },
        {
            "symbol": "NBP",
            "bucket": "AVOID",
            "conviction": "HIGH",
            "reason": (
                "Highest bearish conviction in universe (-5.2% expected). "
                "State bank, exposed to government debt rollover risk. "
                "Avoid until IMF program clarity."
            ),
            "contributing_signals": [
                "Highest bearish rule score in universe",
                "Government balance sheet risk",
                "Foreign selling in banks sector",
            ],
        },
        {
            "symbol": None,
            "bucket": "HOLD",
            "conviction": "MEDIUM",
            "reason": (
                "Market filter active — Phase-1 universe 150-day momentum "
                "is negative. Do NOT deploy full capital. Keep 60-70% in "
                "cash/T-bills. Use any IMF-driven rally on May 15 as an "
                "opportunity to reassess positioning, not to chase."
            ),
            "contributing_signals": [
                "Phase-1 market risk_on = False",
                "Universe 150d momentum negative",
                "US-Iran uncertainty unresolved",
                "Global risk-off (gold +4%, VIX elevated)",
            ],
        },
    ],
    "narrative": (
        "Monday May 11 opens in a cautious environment dominated by two "
        "cross-cutting forces: (1) US-Iran geopolitical risk creating "
        "oil price volatility and global risk-off, and (2) the IMF "
        "mission arriving May 15 as a near-term domestic positive "
        "catalyst. The Phase-1 engine correctly signals CASH — universe "
        "momentum is negative and the broad market is not in a trending "
        "regime. However, selective WATCH positions in quality E&P names "
        "(OGDC, ATRL) are warranted given: institutional buying in the "
        "sector, PPL's new well positive for sentiment, and the "
        "geopolitical risk premium that keeps an oil floor in place. "
        "LOTCHEM is the only ADD signal — small position acceptable. "
        "Avoid cement (heavy FIPI outflows, demand weak) and NBP. "
        "The IMF outcome on May 15 is the week's binary risk event — "
        "positive signals could trigger a 1-2% relief rally across the "
        "board; hold cash reserves ready to deploy on that catalyst."
    ),
}

# Save to cache
cache_dir = Path('data/_strategist')
cache_dir.mkdir(exist_ok=True)
date_file = cache_dir / '2026-05-11.json'
latest_file = cache_dir / 'latest.json'

out = json.dumps(decision, indent=2, ensure_ascii=False)
date_file.write_text(out, encoding='utf-8')
latest_file.write_text(out, encoding='utf-8')
print(f'Strategist decision written to:')
print(f'  {date_file}')
print(f'  {latest_file}')
print()
print(f'Stance   : {decision["risk_stance"]} ({decision["conviction"]})')
print(f'Headline : {decision["headline"]}')
print()
print(f'Actions  :')
for a in decision['actions']:
    sym = a.get('symbol') or '(market)'
    print(f'  {a["bucket"]:6} {sym:<10} {a["conviction"]:<6}  {a["reason"][:70]}')
