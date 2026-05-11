"""Quick playbook validation."""
import sys, json
sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('data/playbook/cases.json', encoding='utf-8'))
print(f'Cases: {len(d["cases"])}')
for c in d['cases']:
    print(f'  - {c["id"]}  ({c["confidence"]})')
