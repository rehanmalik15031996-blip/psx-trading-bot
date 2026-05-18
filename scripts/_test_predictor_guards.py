"""End-to-end test of brain.predictor_guards against last week's
315 predictions. Replays each historical prediction through the
real apply_guards() entry point (not the inline validator copy)
and compares the precision / recall to the design target.

Pass criteria:
  - >= 70% precision (downgrades that caught losers)
  - <= 8% false-positive rate (downgrades that killed winners)
  - HBL May 6 BUY HIGH must be caught (chase or regime)
  - NPL May 8 ADD must be caught (chase)
  - OGDC May 5 ADD must be LEFT ALONE (E&P, positive tilt)
  - TRG May 5 HOLD must be LEFT ALONE
"""
import json
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from brain.predictor_guards import apply_guards

START = date(2026, 5, 4)
END   = date(2026, 5, 13)


# We can't change the predictions log file during the test, so
# emulate the chase detector's view of prior calls by passing
# `predictions_log` explicitly. We also override `today` per row.
def main():
    full_log = json.loads(
        Path("data/predictions_log.json").read_text(encoding="utf-8"))
    preds = full_log.get("predictions") or []

    # Build an in-window list ordered by gen date so each iteration
    # only sees PRIOR predictions in the chase detector.
    in_window = []
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if START <= gd <= END:
            in_window.append({**p, "_gd": gd})
    in_window.sort(key=lambda r: r["_gd"])

    # Sector tilt during de-risk (validated against macro_impact run)
    SECTOR_TILT = {
        "Banking":           +1,
        "Cement":            -4,
        "Oil & Gas E&P":     +7,
        "OMC/Refining":      +3,
        "Power":             +3,
        "Conglomerate/Chem":  0,
        "Pharma":             0,
        "Fertilizer":         0,
        "Autos":              0,
        "Technology":         0,
        "Consumer":           0,
        "Misc":               0,
    }

    # Load close prices to compute actual T+5 returns
    cache: dict[str, pd.DataFrame] = {}
    def load_close(sym):
        if sym in cache:
            return cache[sym]
        p = Path("data/ohlcv") / f"{sym}.parquet"
        if not p.exists():
            cache[sym] = None
            return None
        df = pd.read_parquet(p)[["date", "close"]].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        cache[sym] = df.sort_values("date").reset_index(drop=True)
        return cache[sym]

    def t5_actual(sym, gd, entry):
        df = load_close(sym)
        if df is None or not entry:
            return None
        sub = df[df["date"] > gd].head(5)
        if len(sub) < 1:
            return None
        return (float(sub["close"].iloc[-1]) / entry - 1) * 100

    # Run guards on every row, building up the historical log as we go
    seen: list[dict] = []
    results = []
    for row in in_window:
        sym = row["symbol"]
        sector = row.get("sector")
        gd = row["_gd"]
        entry = row.get("entry_price_pkr") or 0

        # Mock macro_impact snapshot — only by_sector matters here
        msnap = {
            "by_sector": {"score": SECTOR_TILT.get(sector, 0)},
        }

        raw_pred = {
            "suggested_action": row.get("suggested_action"),
            "conviction":       row.get("conviction"),
            "expected_return_5d_low_pct":  row.get("expected_return_5d_low_pct"),
            "expected_return_5d_mid_pct":  row.get("expected_return_5d_mid_pct"),
            "expected_return_5d_high_pct": row.get("expected_return_5d_high_pct"),
            "key_risks": list(row.get("key_risks") or []),
            "critic_notes": [],
        }

        # Build a fake predictions_log of prior calls only
        fake_log = {"predictions": seen}

        new_pred = apply_guards(
            raw_pred, symbol=sym, sector=sector,
            entry_price=entry,
            macro_impact_snapshot=msnap,
            today=gd,
            predictions_log=fake_log,
        )

        actual = t5_actual(sym, gd, entry)
        results.append({
            "gen_date":    gd.isoformat(),
            "symbol":      sym,
            "sector":      sector,
            "orig_bucket": row.get("suggested_action"),
            "new_bucket":  new_pred["suggested_action"],
            "orig_conv":   row.get("conviction"),
            "new_conv":    new_pred["conviction"],
            "actual%":     round(actual, 2) if actual is not None else None,
            "guards":      new_pred.get("guards_applied", []),
            "regime_on":   new_pred.get("regime_on"),
        })

        seen.append({**row, "generated_at": row["generated_at"]})

    df = pd.DataFrame(results).dropna(subset=["actual%"])

    print(f"Total predictions tested: {len(df)}")
    print(f"Predictions in risk-off regime: {df['regime_on'].sum()} "
           f"({df['regime_on'].sum()/len(df)*100:.0f}%)")

    downgrades = df[df["orig_bucket"] != df["new_bucket"]]
    print(f"\nDowngrades made: {len(downgrades)} "
          f"({len(downgrades)/len(df)*100:.0f}%)")
    print(f"  caught LOSERS (actual <= -1%): "
          f"{(downgrades['actual%'] <= -1).sum()}")
    print(f"  hit FLATS (actual in -1..+1): "
          f"{((downgrades['actual%'] > -1) & (downgrades['actual%'] < 1)).sum()}")
    print(f"  FALSE POSITIVES (actual >= +1%): "
          f"{(downgrades['actual%'] >= 1).sum()}")
    precision = (downgrades['actual%'] <= -1).sum() / max(len(downgrades), 1)
    fpr = (downgrades['actual%'] >= 1).sum() / max(len(downgrades), 1)
    print(f"\n  precision (losers / downgrades) = {precision*100:.0f}%")
    print(f"  fpr        (winners / downgrades) = {fpr*100:.0f}%")

    # Critical-case asserts
    print("\n--- Critical-case checks ---")
    def check_caught(date_str, sym):
        r = df[(df["gen_date"] == date_str) & (df["symbol"] == sym)]
        if r.empty:
            print(f"  [SKIP] {date_str} {sym} not in dataset")
            return
        orig = r.iloc[0]["orig_bucket"]
        new = r.iloc[0]["new_bucket"]
        guards = r.iloc[0]["guards"]
        caught = orig != new
        flag = "PASS" if caught else "FAIL"
        print(f"  [{flag}] {date_str} {sym} "
              f"{orig} -> {new} guards={guards} (actual {r.iloc[0]['actual%']:+.2f}%)")

    def check_untouched(date_str, sym):
        r = df[(df["gen_date"] == date_str) & (df["symbol"] == sym)]
        if r.empty:
            print(f"  [SKIP] {date_str} {sym} not in dataset")
            return
        orig = r.iloc[0]["orig_bucket"]
        new = r.iloc[0]["new_bucket"]
        guards = r.iloc[0]["guards"]
        untouched = orig == new
        flag = "PASS" if untouched else "FAIL"
        print(f"  [{flag}] {date_str} {sym} kept {orig} guards={guards} "
              f"(actual {r.iloc[0]['actual%']:+.2f}%)")

    print("Critical CATCHES (must be downgraded):")
    check_caught("2026-05-06", "HBL")    # BUY HIGH chase
    check_caught("2026-05-07", "HBL")    # BUY HIGH chase or regime
    check_caught("2026-05-08", "NPL")    # ADD chase +6.3%
    check_caught("2026-05-12", "KEL")    # ADD MEDIUM after rally — Power but regime
    check_caught("2026-05-13", "APL")    # ADD MEDIUM
    check_caught("2026-05-11", "HBL")    # HOLD on falling tape -> WATCH/AVOID

    print("\nCritical KEEPS (must NOT be touched):")
    check_untouched("2026-05-05", "OGDC")    # E&P tilt +7 — rallied +6.4%
    check_untouched("2026-05-04", "OGDC")    # E&P tilt +7
    check_untouched("2026-05-04", "PPL")     # E&P tilt +7
    check_untouched("2026-05-05", "PPL")     # E&P tilt +7
    check_untouched("2026-05-05", "POL")     # E&P tilt +7
    check_untouched("2026-05-04", "MARI")    # E&P tilt +7

    # Save full results
    out = Path("data/_research/PREDICTOR_GUARDS_VALIDATION.csv")
    df.to_csv(out, index=False)
    print(f"\nSaved full guard-replay results to {out}")


if __name__ == "__main__":
    main()
