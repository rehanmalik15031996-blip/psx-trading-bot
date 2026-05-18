"""Backtest the 3 proposed predictor guards against last week's 315 calls.

GUARD A — Regime cap:
  When `pre_event_derisk` is active OR universe_5d <= -1% OR an IMF event
  is within 10 calendar days, downgrade:
    BUY  -> ADD
    ADD  -> HOLD
    HIGH -> MEDIUM, MEDIUM -> LOW

GUARD B — Chase-the-tape detector:
  If we issued a LONG (BUY/ADD) on the same symbol within the last 3
  trading days at a price >= 3% LOWER than today's entry, downgrade
  conviction one notch (HIGH->MEDIUM, MEDIUM->LOW) and bucket
  one notch (BUY->ADD, ADD->HOLD). This catches "chasing the rally
  on the same name."

GUARD C — Regime forecast clamp:
  When risk-off regime active, subtract 2.5pp from mid forecast and
  re-derive bucket from the clamped forecast:
    mid >= +2.5%  -> BUY
    mid >= +0.5%  -> ADD
    mid in [-0.5, +0.5]% -> HOLD
    mid <= -0.5%  -> WATCH/AVOID

Run all three guards in series (A -> B -> C, last guard wins on bucket).
Then re-score the prediction vs the same actual T+5 return and compare
the resulting hit-rates + avg miss vs the original.
"""
import json
from datetime import date, timedelta
from pathlib import Path
import pandas as pd

START = date(2026, 5, 4)
END   = date(2026, 5, 13)
OHLCV_DIR = Path("data/ohlcv")
IMF_EVENT_DATE = date(2026, 5, 15)  # May 15 mission start

# ---------------------------------------------------------------------------
# Load KSE-100 history + FIPI for regime detection
# ---------------------------------------------------------------------------
kse = pd.read_parquet("data/macro/kse100.parquet")
kse["date"] = pd.to_datetime(kse["date"]).dt.date
kse = kse.sort_values("date").reset_index(drop=True)

fipi = pd.read_parquet("data/flows/fipi_daily.parquet")
fipi["date"] = pd.to_datetime(fipi["date"]).dt.date
fipi = fipi.sort_values("date").reset_index(drop=True)


def universe_5d_on(d: date) -> float | None:
    sub = kse[kse["date"] <= d].tail(6)
    if len(sub) < 6:
        return None
    return float(sub["kse100_close"].iloc[-1] / sub["kse100_close"].iloc[0] - 1)


def foreign_sell_streak_on(d: date) -> int:
    sub = fipi[fipi["date"] <= d].tail(5)
    streak = 0
    for v in reversed(sub["foreign_net_pkr_mn"].tolist()):
        if v is not None and v < 0:
            streak += 1
        else:
            break
    return streak


def days_to_imf(d: date) -> int:
    return (IMF_EVENT_DATE - d).days


def is_derisk_regime(d: date) -> tuple[bool, list[str]]:
    """V2: require AT LEAST 2 of 3 conditions to call regime risk-off.
    This is stricter than v1 and avoids false-positives on the early
    pre-event week when only the IMF clock is ticking but the tape
    is still flat or rallying."""
    triggers = []
    u5 = universe_5d_on(d)
    if u5 is not None and u5 <= -0.01:
        triggers.append(f"u5d={u5*100:.1f}%")
    streak = foreign_sell_streak_on(d)
    if streak >= 2:
        triggers.append(f"fipi_streak={streak}d")
    dti = days_to_imf(d)
    if 0 <= dti <= 5:
        triggers.append(f"imf_in_{dti}d")
    return (len(triggers) >= 2, triggers)


# Sector tilt mapping reflects the new macro_impact engine output
# after the pre_event_derisk driver lands. These are the per-sector
# tilts during the pre-IMF de-risk week (calibrated from live snapshot).
SECTOR_TILT_DURING_DERISK = {
    "Banking":           +1,     # was +4 pre-fix, now +1 post-fix
    "Cement":            -4,
    "Oil & Gas E&P":     +7,
    "OMC/Refining":      +3,
    "Power":             +3,
    "Conglomerate/Chem":  0,    # was +2, now 0
    "Pharma":             0,
    "Fertilizer":         0,
    "Autos":              0,
    "Technology":         0,
    "Consumer":           0,
    "Misc":               0,
}


# ---------------------------------------------------------------------------
# Load OHLCV
# ---------------------------------------------------------------------------
_close_cache: dict[str, pd.DataFrame] = {}


def load_close(sym):
    if sym in _close_cache:
        return _close_cache[sym]
    p = OHLCV_DIR / f"{sym}.parquet"
    if not p.exists():
        _close_cache[sym] = None
        return None
    df = pd.read_parquet(p)[["date", "close"]].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    _close_cache[sym] = df
    return df


def close_n(df, d, n):
    sub = df[df["date"] > d].head(n)
    return float(sub["close"].iloc[-1]) if len(sub) >= 1 else None


# ---------------------------------------------------------------------------
# Guard implementations
# ---------------------------------------------------------------------------
def downgrade_bucket(b):
    return {"BUY": "ADD", "ADD": "HOLD", "HOLD": "WATCH",
            "WATCH": "AVOID"}.get(b, b)


def downgrade_conviction(c):
    return {"HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}.get(c, c)


def guard_a_regime_cap(pred, regime_on):
    """V2: If risk-off regime AND this stock's sector_tilt <= 0,
    downgrade BUY/ADD and HIGH/MEDIUM one notch.

    Critically: leaves stocks in supportive sectors (E&P, Power, OMC)
    untouched. The whole point of pre_event_derisk is to redistribute
    risk INTO defensive sectors, not away from all equity.
    """
    if not regime_on:
        return pred
    sector = pred.get("sector", "")
    tilt = SECTOR_TILT_DURING_DERISK.get(sector, 0)
    if tilt > 0:
        # Supportive sector — don't penalise
        return pred
    new = dict(pred)
    new["bucket"] = downgrade_bucket(pred["bucket"])
    new["conviction"] = downgrade_conviction(pred["conviction"])
    new["guard_a_applied"] = f"tilt={tilt:+d}"
    return new


def guard_b_chase_detector(pred, recent_calls):
    """If we issued a LONG on this symbol within the last 3 trading days
    at >=3% lower price, downgrade one notch."""
    if pred["bucket"] not in ("BUY", "ADD"):
        return pred
    gd = pred["gen_date"]
    sym = pred["symbol"]
    entry = pred["entry"]
    # Look back 3 trading days within the same window
    lookback_start = gd - timedelta(days=5)
    prior = [c for c in recent_calls
              if c["symbol"] == sym
              and c["gen_date"] < gd
              and c["gen_date"] >= lookback_start
              and c["bucket"] in ("BUY", "ADD")]
    if not prior:
        return pred
    prior.sort(key=lambda c: c["gen_date"])
    prior_entry = prior[-1]["entry"]
    chase_pct = (entry / prior_entry - 1) * 100
    if chase_pct >= 3.0:
        new = dict(pred)
        new["bucket"] = downgrade_bucket(pred["bucket"])
        new["conviction"] = downgrade_conviction(pred["conviction"])
        new["guard_b_applied"] = (
            f"chase {chase_pct:+.1f}% vs prior {prior[-1]['gen_date']} "
            f"@ {prior_entry:.2f}")
        return new
    return pred


def guard_c_regime_clamp(pred, regime_on):
    """V2: Subtract 1.5pp from mid forecast and re-derive bucket — only
    for stocks in sectors with non-positive tilt during risk-off."""
    if not regime_on:
        return pred
    sector = pred.get("sector", "")
    tilt = SECTOR_TILT_DURING_DERISK.get(sector, 0)
    if tilt > 0:
        return pred
    mid = pred.get("mid_pred")
    if mid is None:
        return pred
    new = dict(pred)
    clamped = mid - 1.5
    new["mid_pred_clamped"] = clamped
    if clamped >= 2.5:
        derived = "BUY"
    elif clamped >= 0.5:
        derived = "ADD"
    elif clamped >= -1.5:
        derived = "HOLD"
    elif clamped >= -3.5:
        derived = "WATCH"
    else:
        derived = "AVOID"
    # Only downgrade
    order = {"BUY": 5, "ADD": 4, "HOLD": 3, "WATCH": 2, "AVOID": 1, "EXIT": 0}
    if order.get(derived, 0) < order.get(new["bucket"], 0):
        new["bucket"] = derived
        new["guard_c_applied"] = f"mid {mid:+.1f}% -> {clamped:+.1f}%"
    return new


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def main():
    d = json.loads(open("data/predictions_log.json", encoding="utf-8").read())
    preds = d.get("predictions") or []

    rows = []
    for p in preds:
        try:
            gd = pd.to_datetime(p["generated_at"]).date()
        except Exception:
            continue
        if not (START <= gd <= END):
            continue
        rows.append({
            "gen_date":    gd,
            "symbol":      p["symbol"],
            "sector":      p.get("sector"),
            "bucket":      p.get("suggested_action"),
            "conviction":  p.get("conviction"),
            "direction":   p.get("direction"),
            "entry":       p.get("entry_price_pkr") or 0,
            "mid_pred":    p.get("expected_return_5d_mid_pct"),
        })

    df = pd.DataFrame(rows).sort_values(["gen_date", "symbol"]).reset_index(drop=True)

    # Compute actual T+5 returns
    actuals = []
    for _, r in df.iterrows():
        cdf = load_close(r["symbol"])
        if cdf is None or r["entry"] == 0:
            actuals.append(None)
            continue
        t5 = close_n(cdf, r["gen_date"], 5)
        if t5 is None:
            actuals.append(None)
        else:
            actuals.append((t5 / r["entry"] - 1) * 100)
    df["actual"] = actuals
    df = df.dropna(subset=["actual"]).reset_index(drop=True)

    # Apply guards in series
    after = []
    history: list[dict] = []
    for _, r in df.iterrows():
        gd = r["gen_date"]
        regime_on, triggers = is_derisk_regime(gd)
        pred = {
            "gen_date":   gd,
            "symbol":     r["symbol"],
            "sector":     r["sector"],
            "bucket":     r["bucket"],
            "conviction": r["conviction"],
            "entry":      r["entry"],
            "mid_pred":   r["mid_pred"],
        }
        p1 = guard_a_regime_cap(pred, regime_on)
        p2 = guard_b_chase_detector(p1, history)
        p3 = guard_c_regime_clamp(p2, regime_on)
        after.append({
            "gen_date":    gd,
            "symbol":      r["symbol"],
            "sector":      r["sector"],
            "regime_on":   regime_on,
            "triggers":    ",".join(triggers),
            "orig_bucket": r["bucket"],
            "orig_conv":   r["conviction"],
            "new_bucket":  p3["bucket"],
            "new_conv":    p3["conviction"],
            "mid_orig":    r["mid_pred"],
            "mid_clamp":   p3.get("mid_pred_clamped"),
            "actual":      r["actual"],
            "guard_a":     p3.get("guard_a_applied", False),
            "guard_b":     p3.get("guard_b_applied", ""),
            "guard_c":     p3.get("guard_c_applied", ""),
        })
        history.append(pred)

    df_after = pd.DataFrame(after)

    # ---------- Compare hit rates and dollar impact ----------
    print(f"Total predictions in window: {len(df_after)}")
    regime_active = df_after["regime_on"].sum()
    print(f"Predictions issued during de-risk regime: {regime_active} "
           f"({regime_active/len(df_after)*100:.0f}%)")
    print()

    # Bucket distribution before/after
    print("BUCKET distribution before -> after:")
    before_dist = df_after["orig_bucket"].value_counts().to_dict()
    after_dist = df_after["new_bucket"].value_counts().to_dict()
    buckets = sorted(set(before_dist) | set(after_dist),
                       key=lambda b: -before_dist.get(b, 0))
    for b in buckets:
        a_pct = ""
        b1 = before_dist.get(b, 0)
        a1 = after_dist.get(b, 0)
        print(f"  {b:<8} {b1:>3} -> {a1:>3}  ({a1-b1:+d})")
    print()

    # Conviction distribution
    print("CONVICTION distribution before -> after:")
    before_c = df_after["orig_conv"].value_counts().to_dict()
    after_c = df_after["new_conv"].value_counts().to_dict()
    for c in ["HIGH", "MEDIUM", "LOW"]:
        print(f"  {c:<8} {before_c.get(c, 0):>3} -> {after_c.get(c, 0):>3}  "
              f"({after_c.get(c, 0)-before_c.get(c, 0):+d})")
    print()

    # Avg actual return by ORIGINAL action vs by NEW action
    print("Avg actual T+5 return BY ORIGINAL bucket:")
    for b in ["BUY", "ADD", "HOLD", "AVOID"]:
        sub = df_after[df_after["orig_bucket"] == b]
        if not sub.empty:
            print(f"  ORIG={b:<5}  n={len(sub):>3}  avg_actual={sub['actual'].mean():+.2f}%")
    print()
    print("Avg actual T+5 return BY NEW bucket:")
    for b in ["BUY", "ADD", "HOLD", "WATCH", "AVOID"]:
        sub = df_after[df_after["new_bucket"] == b]
        if not sub.empty:
            print(f"  NEW ={b:<5}  n={len(sub):>3}  avg_actual={sub['actual'].mean():+.2f}%")
    print()

    # Did the guards mostly hit losers or winners?
    print("Guard activation effectiveness (where bucket was downgraded):")
    downgraded = df_after[df_after["orig_bucket"] != df_after["new_bucket"]]
    print(f"  {len(downgraded)} downgrades made")
    print(f"  avg_actual of downgraded: {downgraded['actual'].mean():+.2f}%")
    print(f"  (avg_actual of unchanged: {df_after[df_after['orig_bucket'] == df_after['new_bucket']]['actual'].mean():+.2f}%)")
    print()
    print("Of the downgrades, how many would have lost money? (=good catch)")
    print(f"  losers downgraded: {(downgraded['actual'] < -1).sum()} ({(downgraded['actual'] < -1).sum() / len(downgraded) * 100:.0f}%)")
    print(f"  winners downgraded (false positive): {(downgraded['actual'] > 1).sum()} ({(downgraded['actual'] > 1).sum() / len(downgraded) * 100:.0f}%)")
    print()

    # Show all downgrades for inspection
    print("--- All bucket downgrades ---")
    show = downgraded[["gen_date", "symbol", "sector",
                        "orig_bucket", "new_bucket", "orig_conv", "new_conv",
                        "actual", "mid_orig", "mid_clamp",
                        "guard_a", "guard_b", "guard_c"]].copy()
    show["gen_date"] = show["gen_date"].astype(str)
    show["actual"] = show["actual"].round(2)
    show["mid_orig"] = show["mid_orig"].round(2)
    print(show.to_string(index=False, max_colwidth=40))

    # Show the BUY/ADD calls specifically — did we catch the HBL/NBP/NPL chases?
    print()
    print("--- Original BUY / ADD calls — did guards catch them? ---")
    orig_long = df_after[df_after["orig_bucket"].isin(["BUY", "ADD"])].copy()
    orig_long["gen_date"] = orig_long["gen_date"].astype(str)
    orig_long["actual"] = orig_long["actual"].round(2)
    print(orig_long[["gen_date", "symbol", "sector",
                      "orig_bucket", "new_bucket", "orig_conv", "new_conv",
                      "actual",
                      "guard_a", "guard_b", "guard_c"]].to_string(
        index=False, max_colwidth=35))


if __name__ == "__main__":
    main()
