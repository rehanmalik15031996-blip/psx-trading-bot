"""Validate the relief-rally override against the REAL data caches for
the May 19->28 window the post-mortem covers. No monkeypatching — this
reads data/news/scored_news.parquet, data/macro/kse100.parquet and
data/flows/fipi_daily.parquet exactly as production will.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain.predictor_guards as pg

print("Relief-rally override — live-data replay (May 19 -> 28 2026)\n")
print(f"{'as_of':<12} {'idx3d':>7} {'fsell':>6} {'news':>16} "
      f"{'regime':>7} note")
print("-" * 78)

for d in [date(2026, 5, 18), date(2026, 5, 19), date(2026, 5, 20),
          date(2026, 5, 21), date(2026, 5, 22), date(2026, 5, 25),
          date(2026, 5, 28)]:
    idx3 = pg._universe_session_return(3, d)
    fsell = pg._foreign_sell_streak(d)
    news = pg._relief_news_signal(d)
    relief, reason = pg.detect_relief_rally(d)
    on, trig = pg.detect_regime(d)
    idx_s = f"{idx3*100:+.1f}%" if idx3 is not None else "  n/a"
    news_s = f"{news[0]:+.2f}(n={news[1]})" if news else "      none"
    override = next((t for t in trig if "relief" in t), "")
    print(f"{d.isoformat():<12} {idx_s:>7} {fsell:>6} {news_s:>16} "
      f"{'ON' if on else 'off':>7} {override}")

print("\nInterpretation:")
print("  - On any day flagged 'off' with a relief_rally_override note,")
print("    guards A (regime cap) and C (forecast clamp) stand down, so")
print("    banks/cement would NOT have been force-downgraded to AVOID.")
print("  - Guards B (chase) and D (momentum exhaustion) still apply.")
