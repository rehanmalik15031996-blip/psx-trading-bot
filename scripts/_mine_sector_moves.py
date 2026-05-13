"""Mine 5 years of OHLCV history per sector and surface major moves.

For each sector in `config/universe.py`, builds a daily equal-weighted
sector index from constituent close prices, then computes 5d AND 21d
rolling returns. Finds the top N biggest UP and DOWN moves per sector
AND across the whole universe.

Each major move is annotated with:
  - The sector's 5d/21d return
  - The universe's 5d/21d return on the same date (relative move)
  - Macro driver tags fired by `replay_briefing` for that date
  - Active events on that date
  - Brent / USD/PKR / Gold / SBP rate snapshot
  - Whether ANY playbook case fired
  - Top-3 cases that fired (if any)

Output:
  data/_research/sector_moves_catalog.json    (full per-sector list)
  data/_research/major_moves_master.csv       (flat table for review)
  data/_research/sector_moves_report.md       (human-readable report)
"""
from __future__ import annotations
import json
import sys
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from config.universe import UNIVERSE
from brain import playbook as pb
from brain import strategist_overlays as ov

OHLCV_DIR = ROOT / "data" / "ohlcv"
MACRO_DIR = ROOT / "data" / "macro"
OUT_DIR = ROOT / "data" / "_research"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _norm_sector(s: str | None) -> str:
    if not s:
        return ""
    head = s.split("/")[0].strip()
    return ov.SECTOR_ALIASES.get(head.lower(), head)


# ---- Build per-sector equal-weight indices --------------------------------
def build_sector_panels() -> dict[str, pd.DataFrame]:
    """Return {sector_norm: DataFrame(date, sector_close, n_constituents)}."""
    sector_to_syms: dict[str, list[str]] = defaultdict(list)
    for u in UNIVERSE:
        sec = _norm_sector(u.sector)
        sector_to_syms[sec].append(u.symbol)

    panels: dict[str, pd.DataFrame] = {}
    for sec, syms in sector_to_syms.items():
        frames = []
        for sym in syms:
            fp = OHLCV_DIR / f"{sym}.parquet"
            if not fp.exists():
                continue
            df = pd.read_parquet(fp)[["date", "close"]].copy()
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.dropna().sort_values("date")
            df = df.rename(columns={"close": sym})
            frames.append(df.set_index("date"))
        if not frames:
            continue
        merged = pd.concat(frames, axis=1, join="outer").sort_index()
        # Equal-weight return-based index: rebase each constituent to 100 at
        # its first available date, then take the cross-sectional mean.
        normed = merged.apply(lambda c: c / c.dropna().iloc[0] * 100
                                          if c.dropna().size else c)
        panels[sec] = pd.DataFrame({
            "sector_index": normed.mean(axis=1, skipna=True),
            "n_constituents": normed.notna().sum(axis=1),
        })
    return panels


# ---- Universe equal-weighted index -----------------------------------------
def build_universe_panel() -> pd.DataFrame:
    frames = []
    for u in UNIVERSE:
        fp = OHLCV_DIR / f"{u.symbol}.parquet"
        if not fp.exists():
            continue
        df = pd.read_parquet(fp)[["date", "close"]].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.dropna().sort_values("date").rename(columns={"close": u.symbol})
        frames.append(df.set_index("date"))
    merged = pd.concat(frames, axis=1, join="outer").sort_index()
    normed = merged.apply(lambda c: c / c.dropna().iloc[0] * 100
                                      if c.dropna().size else c)
    return pd.DataFrame({"univ_index": normed.mean(axis=1, skipna=True)})


# ---- Helpers --------------------------------------------------------------
def _ret_n(series: pd.Series, n: int) -> pd.Series:
    return series.pct_change(n)


def _level_at(parquet_path: Path, as_of: date) -> float | None:
    if not parquet_path.exists():
        return None
    df = pd.read_parquet(parquet_path)
    if "date" not in df.columns:
        return None
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date")
    sub = df[df["date"] <= as_of]
    if sub.empty:
        return None
    val_cols = [c for c in df.columns if c not in ("date",)]
    if not val_cols:
        return None
    val = sub.iloc[-1][val_cols[0]]
    try:
        return float(val)
    except Exception:
        return None


# ---- Replay-briefing patch (reuse from research_backtest) -----------------
import scripts._research_backtest as rb
from scripts.replay_briefing import replay_briefing


def _annotate_date(d: date) -> dict:
    rb._REPLAY_AS_OF = d
    pb._load_active_events = rb._replay_active_events  # noqa: SLF001
    try:
        b = replay_briefing(d)
    except Exception as e:
        return {
            "active_events": [],
            "drivers": [],
            "brent": None, "usdpkr": None, "gold": None, "policy_rate": None,
            "fired_cases": [], "n_fires": 0,
            "_error": f"{type(e).__name__}: {e}",
        }
    drivers = (b.get("macro_impact") or {}).get("drivers") or []
    drivers_out = []
    for dr in drivers:
        if isinstance(dr, dict):
            drivers_out.append(f"{dr.get('tag')}:{dr.get('magnitude')}")
        else:
            try:
                drivers_out.append(f"{dr[0]}:{dr[1]}")
            except Exception:
                drivers_out.append(str(dr))
    macro = (b.get("macro_snapshot") or {}).get("indicators") or {}
    analogues = pb.retrieve_analogues(b, top_k=5) or []
    return {
        "active_events": sorted(rb._replay_active_events()),
        "drivers": drivers_out,
        "brent":      (macro.get("brent")  or {}).get("last"),
        "usdpkr":     (macro.get("usdpkr") or {}).get("last"),
        "gold":       (macro.get("gold")   or {}).get("last"),
        "policy_rate": (b.get("policy_rate") or {}).get("policy_rate_pct"),
        "regime": (b.get("regime") or {}).get("regime"),
        "univ_5d_lookback":  (b.get("regime") or {}).get("universe_ret_5d"),
        "breadth":  (b.get("regime") or {}).get("breadth_pct_up"),
        "fired_cases": [
            {"id": a["id"], "score": a["match_score"],
             "fired_triggers": a["fired_triggers"]}
            for a in analogues
        ],
        "n_fires": len(analogues),
    }


# ---- Main -----------------------------------------------------------------
def main() -> int:
    print("[mine] building sector panels...")
    panels = build_sector_panels()
    print(f"[mine] sectors: {len(panels)}")
    univ = build_universe_panel()
    univ["univ_5d"]  = _ret_n(univ["univ_index"], 5)
    univ["univ_21d"] = _ret_n(univ["univ_index"], 21)

    # Per-sector top-K up/down moves
    TOP_N = 12   # per direction per sector
    TOP_N_UNIV = 25
    catalog: dict[str, list[dict]] = {}
    all_dates_to_annotate: set[date] = set()

    for sec, df in panels.items():
        df = df.copy()
        df["sec_5d"]  = _ret_n(df["sector_index"], 5)
        df["sec_21d"] = _ret_n(df["sector_index"], 21)
        df = df.dropna(subset=["sec_5d"])
        # Only consider dates where we have >= 2 constituents (avoid single-stock noise)
        df = df[df["n_constituents"] >= 2]

        # De-cluster: pick local extremes that are >= 14 days apart so we
        # don't get 7 entries from the same slow-moving event week.
        for direction, ascending in [("DOWN", True), ("UP", False)]:
            sorted_df = df.sort_values("sec_5d", ascending=ascending)
            picked: list[date] = []
            picked_records: list[dict] = []
            for d, row in sorted_df.iterrows():
                if all(abs((d - p).days) >= 14 for p in picked):
                    picked.append(d)
                    picked_records.append({
                        "date": d.isoformat(),
                        "direction": direction,
                        "sec_5d_pct":  row["sec_5d"]  * 100,
                        "sec_21d_pct": (row["sec_21d"] * 100
                                          if pd.notna(row["sec_21d"]) else None),
                        "n_constituents": int(row["n_constituents"]),
                    })
                    all_dates_to_annotate.add(d)
                if len(picked_records) >= TOP_N:
                    break
            catalog.setdefault(sec, []).extend(picked_records)

    # Universe-level top moves
    univ_clean = univ.dropna(subset=["univ_5d"])
    univ_records: list[dict] = []
    for direction, ascending in [("DOWN", True), ("UP", False)]:
        sorted_u = univ_clean.sort_values("univ_5d", ascending=ascending)
        picked: list[date] = []
        for d, row in sorted_u.iterrows():
            if all(abs((d - p).days) >= 14 for p in picked):
                picked.append(d)
                univ_records.append({
                    "date": d.isoformat(),
                    "direction": direction,
                    "univ_5d_pct":  row["univ_5d"]  * 100,
                    "univ_21d_pct": (row["univ_21d"] * 100
                                      if pd.notna(row["univ_21d"]) else None),
                })
                all_dates_to_annotate.add(d)
            if len(univ_records) >= TOP_N_UNIV * 2:  # both up and down sets
                break

    print(f"[mine] {len(all_dates_to_annotate)} unique dates to annotate")

    # Annotate every unique date
    annotations: dict[str, dict] = {}
    sorted_dates = sorted(all_dates_to_annotate)
    for i, d in enumerate(sorted_dates, 1):
        ann = _annotate_date(d)
        # Add universe context from our index
        try:
            ux = univ.loc[d]
            ann["univ_5d_pct"]  = float(ux["univ_5d"])  * 100 if pd.notna(ux["univ_5d"])  else None
            ann["univ_21d_pct"] = float(ux["univ_21d"]) * 100 if pd.notna(ux["univ_21d"]) else None
        except Exception:
            pass
        annotations[d.isoformat()] = ann
        if i % 25 == 0 or i == len(sorted_dates):
            print(f"  [{i:>3}/{len(sorted_dates)}] {d}  fires={ann.get('n_fires', 0)}",
                  flush=True)

    # Stitch annotations into catalog
    for sec, recs in catalog.items():
        for r in recs:
            r["annotation"] = annotations.get(r["date"], {})
    for r in univ_records:
        r["annotation"] = annotations.get(r["date"], {})

    # ---- Write outputs ----
    out_payload = {
        "n_dates_annotated": len(annotations),
        "universe_top_moves": univ_records,
        "per_sector": catalog,
    }
    (OUT_DIR / "sector_moves_catalog.json").write_text(
        json.dumps(out_payload, indent=2, default=str), encoding="utf-8")

    # Flat CSV
    csv_path = OUT_DIR / "major_moves_master.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow([
            "date", "scope", "direction",
            "ret_5d_pct", "ret_21d_pct",
            "univ_5d_pct", "univ_21d_pct",
            "regime", "breadth", "drivers", "active_events",
            "n_fires", "fired_cases",
            "brent", "usdpkr", "gold", "policy_rate",
        ])
        for r in univ_records:
            ann = r.get("annotation") or {}
            wr.writerow([
                r["date"], "UNIVERSE", r["direction"],
                f'{r["univ_5d_pct"]:.2f}', f'{r["univ_21d_pct"] or 0:.2f}',
                f'{r["univ_5d_pct"]:.2f}', f'{r["univ_21d_pct"] or 0:.2f}',
                ann.get("regime"), ann.get("breadth"),
                "|".join(ann.get("drivers") or []),
                "|".join(ann.get("active_events") or []),
                ann.get("n_fires", 0),
                "|".join(c["id"] for c in ann.get("fired_cases") or []),
                ann.get("brent"), ann.get("usdpkr"),
                ann.get("gold"), ann.get("policy_rate"),
            ])
        for sec, recs in catalog.items():
            for r in recs:
                ann = r.get("annotation") or {}
                wr.writerow([
                    r["date"], sec, r["direction"],
                    f'{r["sec_5d_pct"]:.2f}',
                    f'{r["sec_21d_pct"] or 0:.2f}',
                    f'{(ann.get("univ_5d_pct")  or 0):.2f}',
                    f'{(ann.get("univ_21d_pct") or 0):.2f}',
                    ann.get("regime"), ann.get("breadth"),
                    "|".join(ann.get("drivers") or []),
                    "|".join(ann.get("active_events") or []),
                    ann.get("n_fires", 0),
                    "|".join(c["id"] for c in ann.get("fired_cases") or []),
                    ann.get("brent"), ann.get("usdpkr"),
                    ann.get("gold"), ann.get("policy_rate"),
                ])

    # Markdown report
    md = ["# Sector + Universe Major-Move Catalog", ""]
    md.append(f"Mined {len(annotations)} unique dates: top 12 up + 12 down moves "
              f"per sector, top 25 up + 25 down moves universe-wide. De-clustered "
              f"to >=14 days apart so each move gets one entry.")
    md.append("")
    md.append("Move = trailing **5-trading-day** % change of the equal-weighted "
              "sector index (or universe index). 21d shows context.")
    md.append("")

    md.append("## Universe-wide top moves")
    md.append("")
    md.append("| Date | Dir | 5d | 21d | Regime | Breadth | Drivers | Events | Fires | Cases |")
    md.append("|------|-----|-----|-----|--------|---------|---------|--------|-------|-------|")
    for r in sorted(univ_records,
                     key=lambda x: -abs(x["univ_5d_pct"]))[:40]:
        ann = r.get("annotation") or {}
        breadth = ann.get("breadth")
        breadth_s = f"{breadth:.0f}%" if breadth is not None else "-"
        md.append(
            f"| {r['date']} | {r['direction']} | {r['univ_5d_pct']:+.2f}% | "
            f"{(r['univ_21d_pct'] or 0):+.2f}% | "
            f"{ann.get('regime') or '-':<8} | {breadth_s} | "
            f"{(', '.join(ann.get('drivers') or []))[:60] or '—'} | "
            f"{(', '.join(ann.get('active_events') or []))[:40] or '—'} | "
            f"{ann.get('n_fires', 0)} | "
            f"{(', '.join(c['id'] for c in ann.get('fired_cases') or []))[:60] or '—'} |"
        )
    md.append("")

    for sec in sorted(catalog.keys()):
        recs = catalog[sec]
        md.append(f"## {sec}")
        md.append("")
        md.append("| Date | Dir | sec5d | sec21d | univ5d | drivers | events | fires | cases |")
        md.append("|------|-----|-------|--------|--------|---------|--------|-------|-------|")
        for r in sorted(recs, key=lambda x: -abs(x["sec_5d_pct"]))[:24]:
            ann = r.get("annotation") or {}
            md.append(
                f"| {r['date']} | {r['direction']} | "
                f"{r['sec_5d_pct']:+.2f}% | "
                f"{(r['sec_21d_pct'] or 0):+.2f}% | "
                f"{(ann.get('univ_5d_pct') or 0):+.2f}% | "
                f"{(', '.join(ann.get('drivers') or []))[:50] or '—'} | "
                f"{(', '.join(ann.get('active_events') or []))[:30] or '—'} | "
                f"{ann.get('n_fires', 0)} | "
                f"{(', '.join(c['id'] for c in ann.get('fired_cases') or []))[:60] or '—'} |"
            )
        md.append("")

    md_path = OUT_DIR / "sector_moves_report.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"[mine] wrote {csv_path}")
    print(f"[mine] wrote {md_path}")
    print(f"[mine] wrote {OUT_DIR / 'sector_moves_catalog.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
