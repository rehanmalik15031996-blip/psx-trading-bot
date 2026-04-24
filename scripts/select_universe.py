"""Universe selector: picks the 9 best "flex" tickers from the candidate pool.

Process:
  1. Load REQUIRED_TICKERS (locked by user) and CANDIDATE_POOL.
  2. Backfill EOD history for every candidate from PSX DPS.
  3. Build technical features (price/volume only — fast, no macro join needed).
  4. Train a quick LightGBM classifier per candidate on an 80/20 time split.
  5. Rank candidates by out-of-sample AUC (their PREDICTIVE edge).
  6. Apply sector-diversity caps and pick top 9.
  7. Write the final 15-stock universe to config/universe.py.

This runs once when the user changes required tickers or the market regime
changes substantially. It should NOT run every day — the full
`train_models.py` retrains within this same universe on a weekly schedule.

Usage:
    python scripts/select_universe.py
    python scripts/select_universe.py --dry-run    # show ranking but don't write universe.py
    python scripts/select_universe.py --pool-only  # evaluate ONLY pool, not required
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

warnings.filterwarnings("ignore")

import lightgbm as lgb
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from sklearn.metrics import accuracy_score, roc_auc_score

from brain.features import _calendar, _cross_sectional, _price_features, _targets
from config.candidates import CANDIDATE_POOL, REQUIRED_TICKERS, sector_of_candidate
from connectors.psx_historical import PSXHistoricalConnector
from data.store import load_ohlcv, save_ohlcv


# --------------------------------------------------------------------------
# Sector diversification rules
# --------------------------------------------------------------------------
# Canonicalize sectors into broader groups for caps
SECTOR_GROUPS: dict[str, str] = {
    "Commercial Banks":                  "Banking",
    "Oil & Gas Exploration Companies":   "Oil & Gas E&P",
    "Oil & Gas Marketing Companies":     "OMC/Refining",
    "Refinery":                          "OMC/Refining",
    "Cement":                            "Cement",
    "Fertilizer":                        "Fertilizer",
    "Power Generation & Distribution":   "Power",
    "Cable & Electrical Goods":          "Conglomerate/Chem",
    "Chemical":                          "Conglomerate/Chem",
    "Technology & Communication":        "Technology",
    "Automobile Assembler":              "Autos",
    "Pharmaceuticals":                   "Pharma",
    "Food & Personal Care":              "Consumer",
    "Miscellaneous":                     "Misc",
}

# How many stocks allowed per grouped sector in the FINAL 15
SECTOR_CAPS: dict[str, int] = {
    "Banking":             4,   # up to 4 banks (1 is FABL required, so 3 more max)
    "Oil & Gas E&P":       3,   # OGDC + PPL = 2 required; cap allows 1 more (e.g. MARI)
    "OMC/Refining":        2,
    "Cement":              3,   # MLCF required; 2 more max
    "Fertilizer":          2,
    "Power":               2,   # HUBC required; 1 more
    "Conglomerate/Chem":   2,
    "Technology":          2,
    "Autos":               1,
    "Pharma":              1,
    "Consumer":            1,
    "Misc":                2,   # PABC required
}


def _grouped_sector(canonical: str) -> str:
    return SECTOR_GROUPS.get(canonical, "Other")


# --------------------------------------------------------------------------
# Quick per-symbol AUC estimator (lightweight features, LGBM only)
# --------------------------------------------------------------------------
FEAT_DROP = {
    "date", "symbol", "sector", "open", "close", "volume",
    "fwd_ret_5d", "fwd_ret_5d_up",
    "vol_sma_20", "turnover_sma_20", "turnover_pkr", "obv",
} | {f"sma_{w}" for w in (5, 10, 20, 50, 100, 200)}


def quick_auc_for_symbol(symbol: str) -> dict | None:
    """Build technical features, train LGBM on 80%, score on 20% tail.

    Returns: {auc, acc, n_train, n_test, rows_used}
    """
    raw = load_ohlcv(symbol)
    if raw.empty or len(raw) < 400:
        return None

    feat = _price_features(raw)
    # no macro/cross-sectional here — we're ranking one symbol at a time
    feat = _calendar(feat)
    feat = _targets(feat, horizon=5)

    cols = [c for c in feat.columns
            if c not in FEAT_DROP and pd.api.types.is_numeric_dtype(feat[c])]
    d = feat.dropna(subset=cols + ["fwd_ret_5d_up"]).sort_values("date").reset_index(drop=True)
    if len(d) < 300:
        return None

    cut = int(len(d) * 0.80)
    train, test = d.iloc[:cut], d.iloc[cut:]
    X_tr, y_tr = train[cols], train["fwd_ret_5d_up"].astype(int)
    X_te, y_te = test[cols],  test["fwd_ret_5d_up"].astype(int)
    if y_tr.nunique() < 2 or y_te.nunique() < 2:
        return None

    params = {
        "objective": "binary", "metric": "binary_logloss",
        "learning_rate": 0.03, "num_leaves": 24, "min_data_in_leaf": 15,
        "feature_fraction": 0.7, "bagging_fraction": 0.8, "bagging_freq": 5,
        "lambda_l1": 0.1, "lambda_l2": 0.1, "verbose": -1,
    }
    m = lgb.train(params, lgb.Dataset(X_tr, label=y_tr), num_boost_round=250)
    p = m.predict(X_te)
    try:
        auc = roc_auc_score(y_te, p)
    except ValueError:
        return None
    acc = accuracy_score(y_te, (p > 0.5).astype(int))
    return {
        "auc": float(auc),
        "acc": float(acc),
        "n_train": len(train),
        "n_test":  len(test),
        "rows_used": len(d),
    }


# --------------------------------------------------------------------------
# Main selection
# --------------------------------------------------------------------------
def backfill_if_missing(symbols: list[str], console: Console) -> None:
    conn = PSXHistoricalConnector()
    for sym in symbols:
        p = PROJECT_ROOT / "data" / "ohlcv" / f"{sym}.parquet"
        if p.exists():
            continue
        try:
            rows = conn.fetch_symbol(sym)
            if rows:
                save_ohlcv(sym, rows)
                console.print(f"[dim]backfilled {sym}: {len(rows)} rows[/dim]")
        except Exception as e:
            console.print(f"[yellow]{sym} backfill failed: {e}[/yellow]")


def rank_candidates(pool_syms: list[str], console: Console) -> list[dict]:
    results = []
    for sym in pool_syms:
        r = quick_auc_for_symbol(sym)
        if r is None:
            console.print(f"[dim]  {sym}: skipped (not enough data)[/dim]")
            continue
        results.append({
            "symbol": sym,
            "sector_canonical": sector_of_candidate(sym) or "Other",
            "sector_grouped": _grouped_sector(sector_of_candidate(sym) or "Other"),
            **r,
        })
    return results


def pick_flex_nine(
    ranked: list[dict],
    required_sector_counts: dict[str, int],
    n_slots: int = 9,
) -> list[dict]:
    """Greedy pick: highest AUC first, respecting sector caps (already counting required)."""
    picked: list[dict] = []
    sector_count = dict(required_sector_counts)
    for r in sorted(ranked, key=lambda x: -x["auc"]):
        if len(picked) >= n_slots:
            break
        sec = r["sector_grouped"]
        cap = SECTOR_CAPS.get(sec, 2)
        if sector_count.get(sec, 0) >= cap:
            r["skipped_reason"] = f"sector cap for {sec} reached ({cap})"
            continue
        picked.append(r)
        sector_count[sec] = sector_count.get(sec, 0) + 1
    return picked


# --------------------------------------------------------------------------
# Writing the final universe.py
# --------------------------------------------------------------------------
UNIVERSE_TEMPLATE = '''"""Trading universe: the 15 PSX names the bot trades.

AUTO-GENERATED by scripts/select_universe.py on {date}.
  - {n_required} tickers user-required (REQUIRED_TICKERS in config/candidates.py)
  - {n_flex} tickers selected by training-AUC ranking from the candidate pool

Edit config/candidates.py (REQUIRED_TICKERS or CANDIDATE_POOL) and re-run
the selector to regenerate this file. Any manual edits will be overwritten.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    name: str
    sector: str
    notes: str = ""


UNIVERSE: list[UniverseEntry] = [
{entries}
]


def symbols() -> list[str]:
    return [u.symbol for u in UNIVERSE]


def sector_of(symbol: str) -> str | None:
    for u in UNIVERSE:
        if u.symbol == symbol:
            return u.sector
    return None


def by_sector() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {{}}
    for u in UNIVERSE:
        out.setdefault(u.sector, []).append(u.symbol)
    return out
'''


# Human-readable names for all candidates + required tickers
NAMES: dict[str, str] = {
    "HUBC": "Hub Power",
    "PABC": "Pakistan Aluminium Beverage Cans",
    "MLCF": "Maple Leaf Cement",
    "OGDC": "Oil & Gas Development Co.",
    "FABL": "Faysal Bank",
    "PPL":  "Pakistan Petroleum",
    "MCB":  "MCB Bank", "HBL": "Habib Bank", "UBL": "United Bank",
    "MEBL": "Meezan Bank", "BAHL": "Bank Al Habib", "ABL": "Allied Bank",
    "NBP":  "National Bank",
    "MARI": "Mari Petroleum", "POL": "Pakistan Oilfields",
    "PSO":  "Pakistan State Oil", "APL": "Attock Petroleum", "ATRL": "Attock Refinery",
    "LUCK": "Lucky Cement", "FCCL": "Fauji Cement",
    "DGKC": "D.G. Khan Cement", "KOHC": "Kohat Cement",
    "FFC":  "Fauji Fertilizer", "EFERT": "Engro Fertilizers",
    "KAPCO":"Kot Addu Power", "KEL": "K-Electric",
    "ENGROH": "Engro Holdings", "LOTCHEM": "Lotte Chemical Pakistan",
    "EPCL": "Engro Polymer",
    "SYS":  "Systems Ltd", "TRG": "TRG Pakistan",
    "INDU": "Indus Motor", "SEARL": "The Searle Company",
    "COLG": "Colgate Palmolive Pakistan",
}


def write_universe(picks: list[dict], required_entries: list[dict], dry_run: bool) -> Path:
    from datetime import date
    out = PROJECT_ROOT / "config" / "universe.py"

    all_entries = required_entries + picks
    lines = []
    for e in all_entries:
        sym = e["symbol"]
        sec_group = e["sector_grouped"]
        note = e.get("note", "")
        if "auc" in e:
            note = (note + f" [picked by selector, AUC={e['auc']:.2f}]").strip()
        elif "required" in e:
            note = (note + " [user-required]").strip()
        name = NAMES.get(sym, sym)
        # NB: do NOT pad sym — padding leaks into the data
        lines.append(
            f'    UniverseEntry("{sym}", "{name}", "{sec_group}",\n'
            f'                   "{note}"),'
        )

    content = UNIVERSE_TEMPLATE.format(
        date=date.today().isoformat(),
        n_required=len(required_entries),
        n_flex=len(picks),
        entries="\n".join(lines),
    )

    if dry_run:
        return out  # just return the path without writing

    out.write_text(content, encoding="utf-8")
    return out


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pool-only", action="store_true",
                        help="Rank candidate pool only; skip required backfill")
    args = parser.parse_args()

    console = Console()
    console.rule("[bold cyan]PSX universe selector")

    pool_syms = [s for s, _ in CANDIDATE_POOL]
    all_syms = list(REQUIRED_TICKERS) + pool_syms

    # 1. Backfill any missing histories
    console.print(f"\n[bold]Step 1:[/bold] ensure OHLCV for {len(all_syms)} symbols "
                  f"({len(REQUIRED_TICKERS)} required + {len(pool_syms)} pool)")
    backfill_if_missing(all_syms, console)

    # 2. Measure AUC for each candidate + each required (for reporting)
    console.print(f"\n[bold]Step 2:[/bold] training quick LGBM per symbol "
                  f"(~5s each, ~3 min total)")
    pool_results = rank_candidates(pool_syms, console)
    req_results = rank_candidates(list(REQUIRED_TICKERS), console)

    # 3. Sector counts contributed by required
    req_sector_counts: dict[str, int] = {}
    for r in req_results:
        req_sector_counts[r["sector_grouped"]] = (
            req_sector_counts.get(r["sector_grouped"], 0) + 1
        )

    # 4. Pick 9 from pool with sector caps
    picks = pick_flex_nine(pool_results, req_sector_counts, n_slots=9)

    # 5. Report
    table = Table(title="Candidate pool — ranked by out-of-sample AUC")
    for col in ("Rank", "Symbol", "Sector (grouped)", "AUC", "Acc", "n_rows", "Picked?"):
        table.add_column(col)
    picked_syms = {p["symbol"] for p in picks}
    for i, r in enumerate(sorted(pool_results, key=lambda x: -x["auc"]), 1):
        picked = "[bold green]YES[/bold green]" if r["symbol"] in picked_syms else (
            "[dim]cap[/dim]" if r.get("skipped_reason") else "[dim]no[/dim]"
        )
        auc_style = "bold green" if r["auc"] >= 0.58 else (
            "green" if r["auc"] >= 0.54 else ("yellow" if r["auc"] >= 0.50 else "red"))
        table.add_row(
            str(i), r["symbol"], r["sector_grouped"],
            f"[{auc_style}]{r['auc']:.3f}[/{auc_style}]",
            f"{r['acc']:.3f}", str(r["rows_used"]), picked,
        )
    console.print(table)

    # Required tickers AUC (informational — they're in regardless)
    rt = Table(title="Required tickers (locked in)")
    for col in ("Symbol", "Sector (grouped)", "AUC", "Acc"):
        rt.add_column(col)
    for r in req_results:
        rt.add_row(
            r["symbol"], r["sector_grouped"],
            f"{r['auc']:.3f}", f"{r['acc']:.3f}",
        )
    console.print(rt)

    # 6. Build required_entries list for universe.py writer
    required_entries = [
        {
            "symbol": r["symbol"],
            "sector_grouped": r["sector_grouped"],
            "required": True,
            "note": "",
        }
        for r in req_results
    ]

    final_path = write_universe(picks, required_entries, dry_run=args.dry_run)

    console.print("\n[bold]Final 15-stock universe:[/bold]")
    for e in required_entries:
        console.print(f"  [cyan]{e['symbol']:7s}[/cyan] [dim]({e['sector_grouped']})[/dim]  [yellow]required[/yellow]")
    for p in picks:
        console.print(f"  [cyan]{p['symbol']:7s}[/cyan] [dim]({p['sector_grouped']})[/dim]  AUC={p['auc']:.3f}")

    if args.dry_run:
        console.print(f"\n[yellow]DRY RUN[/yellow] — not writing {final_path}")
    else:
        console.print(f"\n[green]Wrote[/green] {final_path}")
        console.print("\nNext steps:")
        console.print("  1. python scripts/backfill.py         # make sure all 15 are backfilled")
        console.print("  2. python scripts/train_models.py     # retrain full ensemble")
        console.print("  3. python brain/backtest.py           # backtest the new universe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
