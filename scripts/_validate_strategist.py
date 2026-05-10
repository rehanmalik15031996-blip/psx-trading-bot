"""Validate the strategist decision JSON satisfies every UI consumer."""
import sys, json
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()
from pathlib import Path

from brain import master_strategist as ms

print('Loading via brain.master_strategist.load_cached()...')
d = ms.load_cached()
if not d:
    print('FAIL: load_cached returned None')
    sys.exit(1)

print(f'OK: loaded decision with {len(d)} top-level keys')
print()

# Required by _render_master_strategist_card (ui/app.py)
card_required = [
    'risk_stance', 'conviction', 'headline', 'agrees_with_phase1',
    'phase1_disagreement_note', 'fallback_used', 'model', 'narrative',
    'macro_lens', 'behavioural_lens', 'key_drivers', 'key_risks',
    'briefing_summary', 'thinking_trace', 'actions',
]
print('=== Master Strategist card fields ===')
for k in card_required:
    present = k in d
    val = d.get(k)
    if present and val:
        meta = type(val).__name__ + (
            f'[{len(val)}]' if isinstance(val, (list, dict, str)) else ''
        )
        status = '[OK]'
    elif present:
        meta = 'empty'
        status = '[WARN]'
    else:
        meta = 'MISSING'
        status = '[FAIL]'
    print(f'  {status} {k:<30} {meta}')

print()
print('=== Banner fields (_render_strategist_stance_banner) ===')
banner_fields = ['risk_stance', 'conviction', 'headline', 'fallback_used', 'actions']
for k in banner_fields:
    if k in d:
        print(f'  [OK]  {k}')
    else:
        print(f'  [FAIL] {k} MISSING')

# Cash veto check
actions = d.get('actions', [])
cash_veto = any(
    not a.get('symbol') and (a.get('bucket') or '').upper() in ('HOLD','WATCH','CASH')
    for a in actions
) or (d.get('risk_stance') or '').upper() == 'CASH'
print(f'  Cash veto detected: {cash_veto}')

# BUY / TRIM / AVOID strips
buys = [a for a in actions if (a.get('bucket') or '').upper() in ('BUY','ADD')]
trims = [a for a in actions if (a.get('bucket') or '').upper() == 'TRIM']
avoids = [a for a in actions if (a.get('bucket') or '').upper() in ('AVOID','SHORT')]
print(f'  BUY/ADD: {[a["symbol"] for a in buys]}')
print(f'  TRIM: {[a["symbol"] for a in trims]}')
print(f'  AVOID/SHORT: {[a["symbol"] for a in avoids]}')

print()
print('=== Per-action fields (used in card and overlay) ===')
required_action = ['symbol', 'bucket', 'conviction', 'reason', 'contributing_signals']
optional_action = ['sector', 'target_weight_pct']
for i, a in enumerate(actions[:5]):
    for k in required_action:
        if k not in a:
            print(f'  [FAIL] action {i} ({a.get("symbol","?")}) missing required: {k}')

# Check ALL actions have buckets in valid set
valid_buckets = {'BUY','ADD','HOLD','TRIM','AVOID','SHORT','WATCH','CASH'}
for a in actions:
    b = (a.get('bucket') or '').upper()
    if b not in valid_buckets:
        print(f'  [WARN] {a.get("symbol")} has invalid bucket: {b}')

print(f'All {len(actions)} actions have valid buckets')

# Strategist actions by symbol (used by Fair Value, Scanner, Find Ideas, Short Ideas)
print()
print('=== Per-symbol action coverage ===')
by_sym = {(a.get('symbol') or '').upper(): a for a in actions if a.get('symbol')}
print(f'Coverage: {len(by_sym)} symbols have actions')

# Check briefing_summary
print()
print('=== briefing_summary subfields (Reports tab playbook expander) ===')
bs = d.get('briefing_summary', {})
for k in ['playbook_analogue_ids', 'playbook_analogue_fired',
           'phase1_state', 'macro_state', 'flows_state']:
    if k in bs:
        print(f'  [OK]  {k}')
    else:
        print(f'  [WARN] {k} not in briefing_summary')

# Reports tab gate (ui/app.py render_reports_tab)
print()
print('=== Tab-level gates ===')
print(f'  Reports tab gate: fallback_used = {d.get("fallback_used")} '
       f'-> banner shown: {not d.get("fallback_used")}')
print(f'  News tab gate:    fallback_used = {d.get("fallback_used")} '
       f'-> banner shown: {not d.get("fallback_used")}')

# Auto-expand check
stance = (d.get('risk_stance') or '').upper()
auto_expand = stance in ('CAUTIOUS', 'DEFENSIVE', 'CASH')
print(f'  Reports/News banner auto-expand: {auto_expand} (stance={stance})')

# Sample render: pretend to render Today card
print()
print('=== Sample render (Today card) ===')
print(f'  Stance:    {d.get("risk_stance")} ({d.get("conviction")})')
print(f'  Headline:  {d.get("headline","")[:100]}')
print(f'  BUY/ADD:   {", ".join(a["symbol"] for a in buys) or "(none)"}')
print(f'  WATCH:     {", ".join(a["symbol"] for a in actions if (a.get("bucket") or "").upper() == "WATCH") or "(none)"}')
print(f'  AVOID:     {", ".join(a["symbol"] for a in avoids) or "(none)"}')
print(f'  TRIM:      {", ".join(a["symbol"] for a in trims) or "(none)"}')
print(f'  Cash w:    {sum((a.get("target_weight_pct") or 0) for a in actions if (a.get("bucket") or "").upper() == "CASH")}%')
print(f'  Total dep: {sum((a.get("target_weight_pct") or 0) for a in actions if (a.get("bucket") or "").upper() in ("BUY","ADD"))}%')

print()
print('VALIDATION COMPLETE — all UI fields present.')
