"""Deterministic tests for the May-19->28 post-mortem fixes:

  G1  relief-rally override   (brain/predictor_guards.py)
  G1  batch AVOID cap         (brain/predictor_guards.cap_universe_avoid)
  G2  short rally kill switch (brain/short_candidates.py)

These monkeypatch the data-reading helpers so the logic is tested in
isolation from whatever happens to be in the parquet caches.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain.predictor_guards as pg


def _reset():
    """Neutralize all data readers; individual tests re-stub as needed."""
    pg._universe_session_return = lambda sessions, as_of=None: None
    pg._universe_5d_return = lambda as_of=None: None
    pg._foreign_sell_streak = lambda as_of=None: 0
    pg._imf_days_until = lambda as_of=None: None
    pg._relief_news_signal = lambda as_of=None: None


# ---------------------------------------------------------------------------
print("G1.1: de-risk regime ON when 2 triggers fire, no relief signal")
_reset()
pg._universe_5d_return = lambda as_of=None: -0.05          # -5% 5d
pg._foreign_sell_streak = lambda as_of=None: 4             # 4d sell streak
on, trig = pg.detect_regime(as_of=date(2026, 5, 18))
print(f"  regime_on={on}  triggers={trig}")
assert on is True, "two triggers should turn regime ON"
assert not any("relief" in t for t in trig)

# ---------------------------------------------------------------------------
print("\nG1.2: relief NEWS signal (day-1) stands the regime down")
_reset()
pg._universe_5d_return = lambda as_of=None: -0.05          # still negative
pg._foreign_sell_streak = lambda as_of=None: 4             # foreign still selling
pg._universe_session_return = lambda sessions, as_of=None: -0.02  # index not up yet
pg._relief_news_signal = lambda as_of=None: (0.59, 5)      # strong HIGH bull news
on, trig = pg.detect_regime(as_of=date(2026, 5, 19))
print(f"  regime_on={on}  triggers={trig}")
assert on is False, "relief news on day-1 must stand the regime down"
assert any("relief_rally_override" in t for t in trig)

# ---------------------------------------------------------------------------
print("\nG1.3: relief INDEX signal (day-2+) stands the regime down")
_reset()
pg._universe_5d_return = lambda as_of=None: -0.03
pg._foreign_sell_streak = lambda as_of=None: 3
pg._universe_session_return = lambda sessions, as_of=None: 0.045  # +4.5% 3d
pg._relief_news_signal = lambda as_of=None: None           # no news leg
on, trig = pg.detect_regime(as_of=date(2026, 5, 21))
print(f"  regime_on={on}  triggers={trig}")
assert on is False, "+4.5% 3d index bounce must stand the regime down"
assert any("index_3d" in t for t in trig)

# ---------------------------------------------------------------------------
print("\nG1.4: weak news (+0.2 < 0.3) does NOT trigger override")
_reset()
pg._universe_5d_return = lambda as_of=None: -0.05
pg._foreign_sell_streak = lambda as_of=None: 4
pg._universe_session_return = lambda sessions, as_of=None: -0.01
pg._relief_news_signal = lambda as_of=None: (0.20, 3)      # below threshold
on, trig = pg.detect_regime(as_of=date(2026, 5, 19))
print(f"  regime_on={on}  triggers={trig}")
assert on is True, "sub-threshold news must NOT override the regime"

# ---------------------------------------------------------------------------
print("\nG1.5: relief override means guards A/C stand down on a bank ADD")
_reset()
# Regime would be ON, but a relief rally is in progress.
pg._universe_5d_return = lambda as_of=None: -0.05
pg._foreign_sell_streak = lambda as_of=None: 4
pg._universe_session_return = lambda sessions, as_of=None: 0.05
pg._relief_news_signal = lambda as_of=None: (0.59, 5)
pred = {
    "suggested_action": "ADD", "conviction": "MEDIUM",
    "expected_return_5d_low_pct": 0.5,
    "expected_return_5d_mid_pct": 2.5,
    "expected_return_5d_high_pct": 4.5,
    "key_risks": [], "critic_notes": [],
}
out = pg.apply_guards(pred, symbol="UBL", sector="Banking",
                       entry_price=100.0,
                       macro_impact_snapshot={"by_sector": {"score": -1}},
                       today=date(2026, 5, 19),
                       predictions_log={"predictions": []})
print(f"  bucket={out['suggested_action']}  guards={out['guards_applied']}  "
      f"regime_on={out['regime_on']}")
assert out["suggested_action"] == "ADD", "ADD must survive during relief rally"
assert "regime_sector_cap" not in out["guards_applied"]
assert "regime_forecast_clamp" not in out["guards_applied"]

# ---------------------------------------------------------------------------
print("\nG1.6: WITHOUT relief, the same bank ADD gets guard-downgraded")
_reset()
pg._universe_5d_return = lambda as_of=None: -0.05
pg._foreign_sell_streak = lambda as_of=None: 4
out = pg.apply_guards(dict(pred), symbol="UBL", sector="Banking",
                       entry_price=100.0,
                       macro_impact_snapshot={"by_sector": {"score": -1}},
                       today=date(2026, 5, 18),
                       predictions_log={"predictions": []})
print(f"  bucket={out['suggested_action']}  guards={out['guards_applied']}  "
      f"regime_on={out['regime_on']}")
assert out["regime_on"] is True
assert out["suggested_action"] != "ADD", "without relief, guards should bite"

# ---------------------------------------------------------------------------
print("\nG1.7: batch AVOID cap relaxes weakest guard-driven AVOIDs")
records = []
# 8 guard-driven AVOIDs of varying bearishness + 2 non-AVOID = 10 total.
for i in range(8):
    records.append({
        "symbol": f"S{i}", "suggested_action": "AVOID",
        "expected_return_5d_mid_pct": -6.0 + i,   # S7 least bearish (+1)
        "guards_applied": ["regime_forecast_clamp"],
        "key_risks": [],
    })
records.append({"symbol": "X", "suggested_action": "HOLD",
                "expected_return_5d_mid_pct": 0.0, "guards_applied": []})
records.append({"symbol": "Y", "suggested_action": "ADD",
                "expected_return_5d_mid_pct": 2.0, "guards_applied": []})
out = pg.cap_universe_avoid(records, max_fraction=0.5)
n_avoid = sum(1 for r in out if r["suggested_action"] == "AVOID")
relaxed = [r["symbol"] for r in out if "avoid_cap_relaxed"
           in (r.get("guards_applied") or [])]
print(f"  AVOID now {n_avoid}/10 (cap=5); relaxed={relaxed}")
assert n_avoid == 5, "must cap AVOID at 50% of 10 = 5"
# The three least-bearish (S7=+1, S6=0, S5=-1) should be relaxed first.
assert set(relaxed) == {"S7", "S6", "S5"}, f"wrong names relaxed: {relaxed}"

# ---------------------------------------------------------------------------
print("\nG1.8: AVOID cap never touches a fundamental (non-guard) AVOID")
records = [{"symbol": "F", "suggested_action": "AVOID",
            "expected_return_5d_mid_pct": -8.0, "guards_applied": [],
            "key_risks": []}]
for i in range(7):
    records.append({"symbol": f"G{i}", "suggested_action": "AVOID",
                    "expected_return_5d_mid_pct": -2.0,
                    "guards_applied": ["regime_sector_cap"], "key_risks": []})
out = pg.cap_universe_avoid(records, max_fraction=0.5)
fund = next(r for r in out if r["symbol"] == "F")
assert fund["suggested_action"] == "AVOID", "fundamental AVOID must be preserved"
print("  fundamental AVOID 'F' preserved ✓")

# ---------------------------------------------------------------------------
print("\nG2: short rally kill switch logic")
import brain.short_candidates as sc

# Stub the trailing return so we don't depend on parquet contents.
_orig = sc._trailing_return_5d
sc._trailing_return_5d = lambda sym, as_of=None: (
    11.0 if sym in ("DGKC", "KOHC") else 1.0)

# Mirror the rank_shorts conviction-cap block in isolation.
def _apply_kill(sym, conv):
    mom5 = sc._trailing_return_5d(sym)
    if mom5 is not None and mom5 >= sc.SHORT_RALLY_COVER_5D_PCT:
        return ("LOW", True)
    return (conv, False)

for sym, start_conv in [("DGKC", "HIGH"), ("KOHC", "MEDIUM"), ("HBL", "HIGH")]:
    conv, killed = _apply_kill(sym, start_conv)
    print(f"  {sym}: {start_conv} -> {conv} (kill={killed})")
assert _apply_kill("DGKC", "HIGH") == ("LOW", True)
assert _apply_kill("KOHC", "MEDIUM") == ("LOW", True)
assert _apply_kill("HBL", "HIGH") == ("HIGH", False)
sc._trailing_return_5d = _orig

print("\nAll relief / rally / cap tests PASSED.")
