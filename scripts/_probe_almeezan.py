"""Probe Al Meezan FMR URL pattern across recent months/upload-paths."""
import sys, requests
sys.stdout.reconfigure(encoding='utf-8')

months = [
    ('2026-04', 'April-2026'), ('2026-03', 'March-2026'),
    ('2026-02', 'February-2026'), ('2026-01', 'January-2026'),
    ('2025-12', 'December-2025'), ('2025-11', 'November-2025'),
    ('2025-10', 'October-2025'), ('2025-09', 'September-2025'),
]

upload_paths = [
    '2026/05', '2026/04', '2026/03', '2026/02', '2026/01',
    '2025/12', '2025/11', '2025/10', '2025/09',
    '2025/02', '2025/01',
]

print('=== Al Meezan FMR URL probe ===')
for ym, mname in months:
    found = False
    for upload in upload_paths:
        url = f'https://www.almeezangroup.com/assets/uploads/{upload}/FMR-{mname}.pdf'
        try:
            r = requests.head(url, timeout=6, allow_redirects=True)
            if r.status_code == 200:
                cl = r.headers.get('content-length', '?')
                print(f'  {mname:18s} -> upload={upload}  {cl} bytes')
                found = True
                break
        except Exception:
            pass
    if not found:
        print(f'  {mname:18s} -> NOT FOUND')
