"""Trigger all Monday pre-market workflows on GitHub Actions immediately."""
import sys, os, urllib.request, urllib.error, json, subprocess
sys.path.insert(0, '.')
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv; load_dotenv()

tok = os.environ.get('GITHUB_TOKEN', '')
if not tok:
    print('ERROR: no GITHUB_TOKEN in .env')
    sys.exit(1)

remote = subprocess.run(
    ['git', 'remote', 'get-url', 'origin'],
    capture_output=True, text=True
).stdout.strip()
cleaned = remote.replace('https://', '').replace('.git', '')
if '@' in cleaned:
    cleaned = cleaned.split('@', 1)[1]
parts = cleaned.split('/')
owner, repo = parts[-2], parts[-1]
print(f'Triggering workflows on {owner}/{repo}\n')

WORKFLOWS = [
    ('Overnight globals',        'overnight.yml'),
    ('News sentiment scoring',   'news_scoring.yml'),
    ('Macro KPIs',               'macro_kpis.yml'),
    ('Macro series (FX/commod)', 'macro_series.yml'),
    ('Generate predictions',     'predictions.yml'),
    ('Master Strategist',        'master_strategist.yml'),
    ('Intraday session',         'intraday_session.yml'),
]

ok = 0
fail = 0
for name, fname in WORKFLOWS:
    url = (f'https://api.github.com/repos/{owner}/{repo}'
           f'/actions/workflows/{fname}/dispatches')
    payload = json.dumps({'ref': 'main'}).encode()
    req = urllib.request.Request(
        url, data=payload, method='POST',
        headers={
            'Authorization': f'Bearer {tok}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if 200 <= resp.status < 300:
                print(f'  [OK]  {name}')
                ok += 1
            else:
                print(f'  [??]  {name}  HTTP {resp.status}')
                fail += 1
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:120]
        print(f'  [ERR] {name}  HTTP {e.code}: {detail}')
        fail += 1
    except Exception as e:
        print(f'  [ERR] {name}  {e}')
        fail += 1

print(f'\n{ok} dispatched, {fail} failed.')
if ok > 0:
    print(
        '\nWorkflows are now running on GitHub (~10-20 min to complete).'
        '\nOnce done, run: git pull origin main'
        '\n... to sync the fresh predictions and strategist decision.'
    )
