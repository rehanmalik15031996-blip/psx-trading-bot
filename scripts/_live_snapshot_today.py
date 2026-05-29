"""Live intraday snapshot (today) for the 35-stock universe + KSE-100.
Pulls the PSX market-watch tape and the index feed; prints each name's
intraday % change and groups by sector so we can read the tape live.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors.psx_portal import PSXMarketWatchConnector, PSXIndicesConnector
from config.universe import symbols as universe_symbols

UNI = set(universe_symbols())

# --- index ---
try:
    idx = PSXIndicesConnector().fetch()
    recs = idx.records or []
    kse = next((r for r in recs if "100" in str(r.get("index") or r.get("name") or "")), None)
    if kse:
        print(f"KSE-100: {kse}")
    else:
        print(f"Index records sample: {recs[:2]}")
except Exception as e:
    print(f"index fetch failed: {type(e).__name__}: {e}")

print("\n--- Universe intraday tape ---")
try:
    mw = PSXMarketWatchConnector().fetch()
    rows = mw.records or []
except Exception as e:
    print(f"market watch failed: {type(e).__name__}: {e}")
    rows = []

by_sym = {}
for r in rows:
    s = r.get("symbol")
    if s in UNI:
        by_sym[s] = r

if by_sym:
    # show change %
    out = []
    for s in sorted(UNI):
        r = by_sym.get(s)
        if not r:
            out.append((s, None, None, None))
            continue
        last = r.get("price") or r.get("last") or r.get("current")
        chg = r.get("change_pct") or r.get("pct_change") or r.get("changePercent")
        prev = r.get("prev_close") or r.get("previous_close")
        out.append((s, last, chg, prev))
    print(f"{'sym':<7}{'last':>10}{'chg%':>9}{'prev':>10}")
    for s, last, chg, prev in out:
        ls = f"{last:.2f}" if isinstance(last, (int, float)) else "n/a"
        cs = f"{chg:+.2f}" if isinstance(chg, (int, float)) else "n/a"
        ps = f"{prev:.2f}" if isinstance(prev, (int, float)) else "n/a"
        print(f"{s:<7}{ls:>10}{cs:>9}{ps:>10}")
    # one sample raw record to see available fields
    sample = next(iter(by_sym.values()))
    print(f"\nsample raw record keys: {list(sample.keys())}")
    print(f"sample raw record: {sample}")
else:
    print("no universe symbols found in tape")
    if rows:
        print(f"sample raw record: {rows[0]}")
