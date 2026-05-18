"""Smoke test: verify XD-symbol canonicalization in PSXMarketWatchConnector."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from connectors.psx_portal import PSXMarketWatchConnector, _canonical_symbol

print("Unit tests for _canonical_symbol:")
cases = [
    ("OGDC", ("OGDC", False)),
    ("OGDCXD", ("OGDC", True)),
    ("PPLXD", ("PPL", True)),
    ("HBL", ("HBL", False)),
    ("MEBLXD", ("MEBL", True)),
    ("LOTCHEM", ("LOTCHEM", False)),
    ("",  ("", False)),
    (None, (None, False)),
]
for raw, expected in cases:
    canon, flags = _canonical_symbol(raw)
    ok = (canon == expected[0]) and (flags["ex_div"] == expected[1])
    status = "OK " if ok else "FAIL"
    print(f"  [{status}] {raw!r:<12} -> canon={canon!r}, "
          f"ex_div={flags['ex_div']}  expected={expected}")

print()
print("=== Re-fetch live PSX MarketWatch with patched connector ===")
res = PSXMarketWatchConnector().fetch()
recs = res.records
print(f"got {len(recs)} symbols")

by_sym = {r["symbol"]: r for r in recs}

# Confirm formerly missing names now resolve under canonical ticker
expect_present = ["OGDC", "PPL", "MEBL", "MCB", "HUBC", "NPL", "FFC", "INDU"]
print("\nNames that were 'missing' last run:")
for s in expect_present:
    r = by_sym.get(s)
    if r:
        print(f"  {s:<7} -> symbol={r['symbol']!r}  "
              f"raw_symbol={r['raw_symbol']!r}  "
              f"ex_div={r['ex_div']}  "
              f"cur={r['current']}  chg={r['change_pct']}%")
    else:
        print(f"  {s:<7} STILL MISSING")

# Confirm no raw-XD ticker leaks into the canonical 'symbol' field
xd_leaks = [r["symbol"] for r in recs if r["symbol"] and r["symbol"].endswith("XD")]
print(f"\nXD leakage into symbol field: {len(xd_leaks)} (must be 0)")
assert len(xd_leaks) == 0, f"XD-suffix leaked: {xd_leaks[:5]}"

ex_div_count = sum(1 for r in recs if r["ex_div"])
print(f"Total ex_div=True records on the tape: {ex_div_count}")
print("\nFirst 5 ex-div symbols on tape today:")
for r in recs:
    if r["ex_div"]:
        print(f"  {r['symbol']:<7} raw={r['raw_symbol']:<10} "
              f"cur={r['current']}  chg={r['change_pct']}%")
        if recs.index(r) >= 0:  # cap output
            pass
