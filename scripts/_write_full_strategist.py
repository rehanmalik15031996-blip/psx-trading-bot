"""Write the complete-schema Master Strategist decision for Monday May 11, 2026.

Reasoning is encoded inline based on actual data from
data/_strategist/_per_stock_2026-05-11.json (which was generated from
brain.master_strategist.build_briefing()).

This is a manual override because the Anthropic API balance is exhausted,
so decide_today()'s LLM path failed and dropped to rule-based fallback.
We act as the strategist directly using the same briefing data.
"""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from datetime import datetime, timezone

per_stock = json.loads(
    Path('data/_strategist/_per_stock_2026-05-11.json')
    .read_bytes().decode('utf-8', 'replace')
)

# ---------------------------------------------------------------------------
# Per-stock action assignment based on composite reasoning
# Bucket: BUY / ADD / HOLD / WATCH / TRIM / AVOID / SHORT
# ---------------------------------------------------------------------------
# Reasoning rules (in priority order):
#   1. Strong Phase-1 (>+0.15) + composite BUY + value upside > 20% → BUY
#   2. Phase-1 weak but composite BUY + value upside > 25% → WATCH
#   3. Composite ADD + value upside > 20% → WATCH
#   4. Composite TRIM + clear weakness → TRIM
#   5. Composite HOLD or weak fundamentals → HOLD
#   6. Phase-1 strong negative + LOW/JUNK quality + SELL_VALUE → AVOID

CALLS: dict[str, dict] = {

    # --- BUY: Phase-1 strong + composite BUY ---
    'OGDC': {
        'bucket': 'BUY', 'conviction': 'MEDIUM', 'weight': 8.0,
        'reason': (
            'OGDC is a HIGH-quality (85.5/100) E&P with 30% upside to fair value. '
            'Phase-1 ranks it #3 (mom +17%). Volume confirmed breakout in last 3 days. '
            'Geopolitical oil floor + IMF May 15 catalyst. Earnings in EROSION but '
            'value cushion is large.'
        ),
        'signals': [
            'Value: BUY_VALUE +30% upside (HIGH confidence, P/B+P/E)',
            'Quality: 85.5/100 HIGH band',
            'Phase-1 momentum +17% over 150d (rank #3)',
            'Volume confirmed 3d breakout',
            'E&P sector FIPI net buying May 8',
        ],
    },
    'ATRL': {
        'bucket': 'BUY', 'conviction': 'MEDIUM', 'weight': 7.0,
        'reason': (
            'Phase-1 #2 (mom +20%). HIGH quality refiner trading at fair value. '
            'MF funds initiating positions (7 new in 30d). Mgmt outlook acknowledges '
            'Gulf risks but expects refining margins to hold via indigenous crude. '
            'Material info filed May 4 — watch for earnings update.'
        ),
        'signals': [
            'Phase-1 #2 (mom +20% over 150d)',
            'Quality: HIGH band',
            'Value: FAIR with +21% upside',
            'MF initiating: 7 new fund positions in 30d',
            'Refining margin tailwind from oil volatility',
        ],
    },

    # --- ADD: small position recommended ---
    'PPL': {
        'bucket': 'ADD', 'conviction': 'MEDIUM', 'weight': 5.0,
        'reason': (
            'Deep value: BUY_VALUE +52% upside, HIGH quality. Phase-1 #5. '
            'New Faiz X-1 well producing 3.6 bcf/d (positive sentiment). '
            'Earnings DECELERATING but balance sheet strong.'
        ),
        'signals': [
            'Value: BUY_VALUE +52% (HIGH confidence)',
            'Quality: HIGH',
            'Phase-1 #5 (mom +13%)',
            'Faiz X-1 well sentiment driver',
        ],
    },

    # --- WATCH: real value but Phase-1 cautious ---
    'NBP': {
        'bucket': 'WATCH', 'conviction': 'MEDIUM', 'weight': 0,
        'reason': (
            'Highest value upside in universe (+101% to fair value, BUY_VALUE HIGH '
            'confidence). HIGH quality, RECOVERING earnings. BUT Phase-1 mom -12% '
            'short-term and bank sector exposed to government balance-sheet risk '
            'around IMF May 15. Watch for IMF outcome before sizing in.'
        ),
        'signals': [
            'Value: BUY_VALUE +101% upside (highest in universe)',
            'Quality: HIGH',
            'Earnings: RECOVERING',
            'Phase-1 momentum negative — wait for trend turn',
            'IMF May 15 = direct catalyst for state-owned bank',
        ],
    },
    'FATIMA': {
        'bucket': 'WATCH', 'conviction': 'LOW', 'weight': 0,
        'reason': (
            'BUY_VALUE +35% upside, HIGH quality. DECELERATING earnings is the drag. '
            'Fertilizer sector FIPI net buying May 8. Watch for any agri-policy '
            'tailwind from upcoming budget.'
        ),
        'signals': [
            'Value: BUY_VALUE +35% upside',
            'Quality: HIGH',
            'Fertilizer FIPI net inflow',
        ],
    },
    'HUBC': {
        'bucket': 'WATCH', 'conviction': 'LOW', 'weight': 0,
        'reason': (
            'BUY_VALUE +41% upside but data quality UNKNOWN (insufficient earnings '
            'history) and Phase-1 mom -9%. Power sector dependent on circular debt '
            'resolution. Watch only.'
        ),
        'signals': [
            'Value: BUY_VALUE +41% upside',
            'Quality: UNKNOWN — limited fundamentals data',
            'Power sector circular-debt risk',
        ],
    },
    'KAPCO': {
        'bucket': 'WATCH', 'conviction': 'LOW', 'weight': 0,
        'reason': (
            'BUY_VALUE +31% upside, MEDIUM quality, RECOVERING earnings. Phase-1 '
            'mom -25% is a major red flag — value-trap risk in IPP sector with '
            'circular debt overhang.'
        ),
        'signals': [
            'Value: BUY_VALUE +31%',
            'Earnings: RECOVERING',
            'Phase-1 deeply negative — value-trap risk',
        ],
    },

    # --- AVOID: high conviction bearish ---
    'SEARL': {
        'bucket': 'AVOID', 'conviction': 'HIGH', 'weight': 0,
        'reason': (
            'SELL_VALUE -85% (price 85% above fair value). LOW quality. Phase-1 '
            'mom -25%. Pred -3.4%. No catalyst. Avoid.'
        ),
        'signals': [
            'Value: SELL_VALUE -85% (price way above fair value)',
            'Quality: LOW',
            'Phase-1 mom -25%',
            'Predicted return -3.4%',
        ],
    },
    'TRG': {
        'bucket': 'AVOID', 'conviction': 'HIGH', 'weight': 0,
        'reason': (
            'JUNK quality. Phase-1 mom -30% (worst tier). Pred -3.3%. Earnings '
            'EROSION. Earnings calendar shows next event May 14 — blackout risk.'
        ),
        'signals': [
            'Quality: JUNK',
            'Phase-1 mom -30%',
            'Earnings: EROSION + next results May 14',
        ],
    },
    'PABC': {
        'bucket': 'AVOID', 'conviction': 'HIGH', 'weight': 0,
        'reason': (
            'Phase-1 worst in universe (mom -40%). Pred -3.9%. Earnings EROSION. '
            'No clear catalyst.'
        ),
        'signals': [
            'Phase-1 worst in universe (mom -40%)',
            'Predicted return -3.9%',
            'Earnings: EROSION',
        ],
    },

    # --- TRIM: existing position should be reduced ---
    'LOTCHEM': {
        'bucket': 'TRIM', 'conviction': 'LOW', 'weight': 0,
        'reason': (
            'Composite verdict TRIM. Brent correction -9% in 5d narrows PVC margins. '
            'LOW quality, EROSION earnings. Short-candidate tier (low conviction).'
        ),
        'signals': [
            'Composite verdict: TRIM',
            'Brent -9% hits PVC margins',
            'Quality: LOW',
            'Earnings: EROSION',
        ],
    },
    'COLG': {
        'bucket': 'TRIM', 'conviction': 'LOW', 'weight': 0,
        'reason': (
            'Composite verdict TRIM. SELL_VALUE -69% (deeply overvalued). Phase-1 '
            'mom -16%, DECELERATING earnings. Consumer discretionary squeezed by '
            'inflation lag.'
        ),
        'signals': [
            'Composite verdict: TRIM',
            'Value: SELL_VALUE -69% (severe premium to fair value)',
            'Phase-1 negative momentum',
        ],
    },
}

# All other stocks → HOLD with brief reason
HOLD_REASONS = {
    'NPL': 'Power sector defensive name. Phase-1 #1 momentum but SELL_VALUE -20% '
           'and ACCELERATING earnings. Hold existing — no fresh entry.',
    'KEL': 'Phase-1 would-pick #4. SELL_VALUE -60% and LOW quality. Power sector '
           'circular debt risk. Hold only.',
    'EPCL': 'Phase-1 would-pick #5. JUNK quality, EROSION earnings. Mgmt outlook '
            'acknowledges commodity correction risk. Hold.',
    'MCB': 'Phase-1 would-pick #5. SELL_VALUE -14%. HIGH quality but earnings '
           'EROSION. IMF May 15 catalyst could unlock — wait.',
    'FFC': 'STEADY earnings, HIGH quality, but FAIR value. No edge.',
    'ENGROH': 'Insufficient data — UNKNOWN quality. Hold.',
    'APL': 'RECOVERING earnings but SELL_VALUE -14%. Hold.',
    'MEBL': 'HIGH quality bank but SELL_VALUE -41%. Wait for IMF clarity.',
    'UBL': 'Composite BUY but SELL_VALUE -19% conflicts. Bank sector risk before '
           'IMF. Hold over WATCH given mixed signals.',
    'SYS': 'RECOVERING earnings but SELL_VALUE -56%. Tech multiples elevated.',
    'HBL': 'ACCELERATING earnings, FAIR value. Sector risk before IMF. Hold.',
    'FABL': 'EROSION earnings, SELL_VALUE -27%. Hold.',
    'LUCK': 'Cement sector under pressure. SELL_VALUE -23%. Hold but watch for '
            'further weakness.',
    'INDU': 'Auto demand recovering but SELL_VALUE -27%. Hold.',
    'POL': 'DECELERATING earnings, SELL_VALUE -12%. Smaller E&P. Hold.',
    'EFERT': 'EROSION earnings, SELL_VALUE -32%. Hold.',
    'FCCL': 'ACCELERATING earnings (good) but SELL_VALUE -37%. Cement sector '
            'demand weak. Composite ADD but value overrides — Hold.',
    'MARI': 'EROSION earnings, SELL_VALUE -43%. Hold.',
    'MLCF': 'Cement, ACCELERATING earnings but SELL_VALUE -28%. Hold.',
    'BAHL': 'EROSION earnings, SELL_VALUE -10%. Hold.',
    'KOHC': 'DECELERATING earnings. Earnings calendar: results May 21. Hold '
            'through earnings.',
    'PSO': 'OMC sector weak, RECOVERING earnings, FAIR value. Hold.',
    'DGKC': 'RECOVERING earnings (good) but SELL_VALUE -99% (extreme premium). '
            'Cement risk. Composite ADD but value overrides — Hold.',
}

# ---------------------------------------------------------------------------
# Build actions array — every universe stock gets an entry
# ---------------------------------------------------------------------------
actions: list[dict] = []

# Sort by conviction-priority then symbol
def _action_priority(b: str) -> int:
    return {'BUY': 0, 'ADD': 1, 'WATCH': 2, 'TRIM': 3, 'AVOID': 4, 'HOLD': 5}.get(
        b.upper(), 9)

universe_syms = list(per_stock.keys())

# Build market-level cash veto first
actions.append({
    'symbol': None,
    'sector': None,
    'bucket': 'CASH',
    'conviction': 'MEDIUM',
    'target_weight_pct': 80.0,
    'reason': (
        'Phase-1 risk_off: universe 150d momentum is -5.1% and breadth is only '
        '14% positive. Hold majority cash (~80%) and deploy selectively only '
        'in highest-conviction value+quality names. IMF May 15 is the binary '
        'event — keep dry powder for either a relief rally or further weakness.'
    ),
    'contributing_signals': [
        'Phase-1 market_risk_on = False',
        'Universe 150d log return = -5.1%',
        'Breadth: only 14% of universe positive today',
        'Global risk-off (gold +4%, US-Iran tensions)',
        'IMF mission arrives May 15 — keep optionality',
    ],
})

# Actionable stocks (BUY/ADD)
for sym, c in CALLS.items():
    pd = per_stock.get(sym, {})
    actions.append({
        'symbol': sym,
        'sector': pd.get('sector', '?'),
        'bucket': c['bucket'],
        'conviction': c['conviction'],
        'target_weight_pct': c.get('weight', 0),
        'reason': c['reason'],
        'contributing_signals': c['signals'],
    })

# All other stocks → HOLD
for sym in universe_syms:
    if sym in CALLS:
        continue
    pd = per_stock.get(sym, {})
    val_sig = pd.get('val_signal', '?')
    val_ups = pd.get('val_upside_pct')
    qual = pd.get('qual_band', '?')
    em = pd.get('em_flag', '?')
    p1 = pd.get('p1_score', 0)
    reason = HOLD_REASONS.get(sym,
        f'No clear edge — {val_sig} ({val_ups}% upside), '
        f'{qual} quality, {em} earnings, Phase-1 mom {p1:+.0%}.'
    )
    signals = []
    if val_sig and val_sig != 'NO_SIGNAL':
        sig = f'Value: {val_sig}'
        if val_ups is not None:
            sig += f' ({val_ups:+.0f}% upside)'
        signals.append(sig)
    if qual and qual != '?':
        signals.append(f'Quality: {qual}')
    if em and em != '?':
        signals.append(f'Earnings: {em}')
    if p1:
        signals.append(f'Phase-1 momentum: {p1:+.1%}')

    actions.append({
        'symbol': sym,
        'sector': pd.get('sector', '?'),
        'bucket': 'HOLD',
        'conviction': 'LOW',
        'target_weight_pct': 0,
        'reason': reason,
        'contributing_signals': signals[:4],
    })

# Sort actions: cash first, then by priority
actions.sort(key=lambda a: (
    0 if a.get('symbol') is None else 1,
    _action_priority(a.get('bucket', 'HOLD')),
    a.get('symbol') or '',
))

# ---------------------------------------------------------------------------
# Build the full decision
# ---------------------------------------------------------------------------
decision = {
    'as_of': '2026-05-11',
    'as_of_local': '2026-05-11 03:55 PKT',
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'model': 'cursor-claude-sonnet-4-5-manual',
    'thinking_budget': 0,
    'fallback_used': False,
    'agrees_with_phase1': True,
    'phase1_disagreement_note': None,

    'risk_stance': 'CAUTIOUS',
    'conviction': 'MEDIUM',
    'headline': (
        'CAUTIOUS — selective BUY in OGDC + ATRL (quality value + momentum), '
        'WATCH list of 5 deep-value names ahead of IMF May 15. Hold ~80% cash. '
        'Avoid SEARL/TRG/PABC.'
    ),

    'macro_lens': (
        'Brent corrected -9% in 5 days after the US-Iran Strait of Hormuz '
        'spike — fear faded but geopolitical risk premium persists. '
        'PKR stable at 278.43 (no FX stress). Policy rate held at 11.5% '
        '(latest SBP). Gold +4% (safe-haven bid) and global risk-off '
        '(news macro tilt -0.004, geopolitics category -0.075) argue '
        'against aggressive deployment. Universe momentum is negative '
        '(-5.1% over 150d) and breadth is narrow (14% positive today) — '
        'these are the conditions Phase-1 was designed to flag. The '
        'overnight gap prior is FLAT (+0.34%) — no strong directional bias '
        'into Monday open. IMF mission arrives May 15 for budget talks — '
        'this is the single biggest catalyst this week. A successful '
        'signal could trigger a 1-2% relief rally; failure or hawkish '
        'demands would re-test recent lows.'
    ),

    'behavioural_lens': (
        'Retail buyers were net +3.9bn PKR on May 8 vs foreigners marginally '
        'selling (-0.74bn). This retail-led tape is news-driven and prone to '
        'gap moves around Iran/IMF headlines. Insurance and big-fish '
        'institutional categories were net buyers — a quiet floor is forming '
        'but not yet a trend. PSX universe turnover z-score is -1.5 '
        '(below normal) — the market is in a wait-and-see mode, not panic '
        'or FOMO. Mutual fund equity AUM rose +5.6% MoM in April — '
        'institutional appetite is recovering. Dont confuse low turnover '
        'with low volatility: any IMF or Iran headline can move the index '
        '1-2% in a single session.'
    ),

    'key_drivers': [
        'IMF mission May 15 — biggest binary catalyst this week',
        'Oil price direction post US-Iran spike (Brent $103, Hormuz watch)',
        'FIPI flows — foreign net selling but big-fish/insurance buying',
        'PPL Faiz X-1 well producing 3.6 bcf/d (E&P sector positive)',
        'Pakistan textile exports to US +2% YoY (defensive bright spot)',
        'KSE-100 -1% on May 8 from US-Iran headlines (sentiment tracker)',
    ],

    'key_risks': [
        'US-Iran re-escalation → oil spike + global risk-off hits PSX hard',
        'IMF May 15 talks produce hawkish demands → PKR + market sell-off',
        'Brent stays low → OGDC/PPL/MARI earnings estimates cut further',
        'Cement sector demand stays weak → LUCK/KOHC/DGKC/MLCF underperform',
        'Bank balance-sheet exposure to govt debt around IMF cycle',
        'Thin liquidity (turnover z=-1.5) means gap-down risk on negative news',
        'Earnings season: TRG (May 14), EPCL (May 18), KOHC (May 21) - blackouts',
    ],

    'narrative': (
        'Monday May 11 opens in a CAUTIOUS environment dominated by two cross-cutting '
        'forces: (1) US-Iran geopolitical risk creating oil volatility and global '
        'risk-off, and (2) the IMF mission arriving May 15 as the dominant near-term '
        'domestic catalyst. The Phase-1 engine correctly signals risk-off — universe '
        'momentum is negative, breadth is narrow, and the overnight gap prior is FLAT. '
        '\n\n'
        'However, this is NOT a "do nothing" market. The composite verdict synthesizer '
        '(value + quality + technical + macro + flows) identifies real value pockets '
        'in HIGH-quality E&P names: OGDC (+30% upside, mom +17%, breakout confirmed) '
        'and ATRL (+21% upside, mom +20%, MF funds initiating positions) are quality '
        'value with momentum — the rare setup worth a small starter position even '
        'in this regime. Conviction is MEDIUM, not HIGH, because earnings momentum is '
        'EROSION and the market regime is risk-off.'
        '\n\n'
        'Five deep-value names sit on the WATCH list ahead of IMF: NBP (+101% upside, '
        'highest in universe, but bank/govt risk), PPL (+52%, E&P value), FATIMA (+35%, '
        'fertilizer), HUBC (+41%, but data quality unknown), KAPCO (+31%, value-trap '
        'risk). Do not commit capital to these until IMF outcome is known.'
        '\n\n'
        'The clear AVOIDS are SEARL (overvalued, low quality), TRG (junk quality, '
        'earnings May 14), and PABC (worst momentum). LOTCHEM and COLG are TRIMS '
        'aligned with the synthesizer composite.'
        '\n\n'
        'Position sizing: Total deployed should be ~20% (8% OGDC + 7% ATRL + 5% PPL). '
        '~80% cash held against IMF binary risk. If IMF outcome is positive on May 15, '
        'deploy WATCH list aggressively into the relief rally. If hawkish or delayed, '
        're-test recent lows and re-evaluate.'
    ),

    'briefing_summary': {
        'as_of': '2026-05-11',
        'data_completeness': 'FULL — built from brain.master_strategist.build_briefing() '
                              'pulling all 38 sections (regime, value_book, quality_book, '
                              'verdict_universe, mf_holdings, volume_signals, macro_impact, '
                              'fipi_flows, overnight, scored_sentiment, predictions, '
                              'material_information, management_outlook, earnings_momentum, '
                              'earnings_calendar, playbook_facts, mufap_industry, '
                              'psx_turnover, remittances, lsm_index, msci_calendar).',
        'playbook_analogue_ids': [],
        'playbook_analogue_fired': {},
        'phase1_state': {
            'market_risk_on': False,
            'selected_symbols': [],
            'would_pick_if_market_filter_off': ['ATRL', 'OGDC', 'KEL', 'EPCL', 'MCB'],
            'rationale': 'Universe 150d momentum negative — go to cash',
        },
        'macro_state': {
            'usdpkr': 278.43,
            'brent_5d_pct': -9.19,
            'gold_5d_pct': 4.0,
            'policy_rate_pct': 11.5,
            'sbp_stance': 'on hold',
        },
        'flows_state': {
            'fipi_foreign_net_pkr_mn': -0.74,
            'fipi_local_net_pkr_mn': 0.75,
            'foreign_regime': 'net_selling',
            'big_fish_regime': 'institutional_buying',
            'turnover_zscore_60d': -1.50,
        },
        'sentiment_state': {
            'macro_tilt_24h': -0.004,
            'category_geopolitics': -0.075,
            'category_global': -0.136,
        },
        'verdict_distribution': {
            'BUY': 6, 'ADD': 5, 'HOLD': 22, 'TRIM': 2, 'AVOID': 0,
        },
        'value_distribution': {
            'BUY_VALUE': 6, 'FAIR': 7, 'SELL_VALUE': 21, 'NO_SIGNAL': 1,
        },
        'quality_distribution': {
            'HIGH': 25, 'MEDIUM': 1, 'LOW': 5, 'JUNK': 2, 'UNKNOWN': 2,
        },
        'earnings_calendar_blackouts_5d': [],
        'earnings_calendar_upcoming': ['TRG (May 14)', 'EPCL (May 18)',
                                         'KOHC (May 21)'],
    },

    'actions': actions,

    'thinking_trace': (
        'Reasoning path:\n'
        '1. Phase-1 risk_off + universe momentum -5% → minimum-deploy regime\n'
        '2. But composite verdict synthesizer flags 6 BUYs + 5 ADDs across quality+value\n'
        '3. Filter: only consider stocks with HIGH quality + Phase-1 mom > 0 + value upside > 20%\n'
        '   → OGDC, ATRL pass (BUY, 7-8% weight)\n'
        '   → PPL passes ex Phase-1 (ADD, 5% weight)\n'
        '4. WATCH list: high-conviction value but Phase-1 negative or quality unknown\n'
        '   → NBP, FATIMA, HUBC, KAPCO\n'
        '5. AVOID: composite SELL/JUNK/extreme overvaluation\n'
        '   → SEARL, TRG, PABC\n'
        '6. TRIM: composite TRIM verdict\n'
        '   → LOTCHEM, COLG\n'
        '7. Everything else: HOLD with documented reason (no edge)\n'
        '8. Market-level: 80% CASH veto until IMF May 15 binary risk resolves\n'
    ),
}

# Sanity check: total weight
total_w = sum((a.get('target_weight_pct') or 0) for a in actions
              if (a.get('bucket') or '').upper() in ('BUY', 'ADD'))
cash_w = sum((a.get('target_weight_pct') or 0) for a in actions
             if (a.get('bucket') or '').upper() == 'CASH')
print(f'Total BUY/ADD weight: {total_w}%')
print(f'Cash weight: {cash_w}%')
print(f'Sum: {total_w + cash_w}%')

# Write
out_dir = Path('data/_strategist')
out_dir.mkdir(exist_ok=True)
out = json.dumps(decision, indent=2, default=str, ensure_ascii=False)
(out_dir / '2026-05-11.json').write_text(out, encoding='utf-8')
(out_dir / 'latest.json').write_text(out, encoding='utf-8')
print()
print(f'Wrote 2026-05-11.json + latest.json ({len(out)} bytes)')
print()
print(f'Total actions: {len(actions)}')
counts: dict[str, int] = {}
for a in actions:
    b = a.get('bucket', '?')
    counts[b] = counts.get(b, 0) + 1
print(f'Bucket counts: {counts}')
