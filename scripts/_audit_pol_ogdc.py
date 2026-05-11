"""Audit OGDC + POL across the full data stack — used to validate live recs."""
import json, pathlib
import pandas as pd

PROJECT = pathlib.Path(__file__).resolve().parent.parent

print("=" * 70)
print("FAIR VALUE / VALUE / QUALITY / EARNINGS MOMENTUM / VERDICT BOOKS")
print("=" * 70)
for f in ["data/fair_value_book.json","data/value_book.json","data/quality_book.json","data/earnings_momentum.json","data/verdict_book.json","data/verdict_synthesizer.json"]:
    fp = PROJECT / f
    if not fp.exists():
        print(f"MISSING {f}")
        continue
    d = json.loads(fp.read_text(encoding="utf-8"))
    print(f"\n=== {f} ===")
    if isinstance(d, dict):
        # try multiple shapes
        target = d.get("per_symbol") or d.get("symbols") or d
        if isinstance(target, dict):
            for sym in ("OGDC","POL","PPL","ATRL","MARI"):
                v = target.get(sym)
                if v:
                    s = json.dumps(v, default=str)
                    print(f"  {sym}: {s[:400]}")

print("\n\n" + "=" * 70)
print("RECENT PREDICTIONS (full log) — OGDC, POL, PPL, ATRL")
print("=" * 70)
log = json.loads((PROJECT / "data/predictions_log.json").read_text(encoding="utf-8"))
preds = log.get("predictions", [])
print(f"Total preds in log: {len(preds)}")
for sym in ("OGDC","POL","PPL","ATRL"):
    rows = [r for r in preds if isinstance(r, dict) and r.get("symbol") == sym]
    rows = sorted(rows, key=lambda r: r.get("as_of_date") or r.get("date") or "")
    if not rows:
        print(f"  {sym}: NO predictions in log")
        continue
    last3 = rows[-3:]
    print(f"\n--- {sym} latest 3 predictions ---")
    for r in last3:
        print(f"  {r.get('as_of_date') or r.get('date')} | action={r.get('action')} dir={r.get('direction')} h={r.get('horizon_days')}d exp%={r.get('expected_return_pct')} conf={r.get('confidence')}")
        if r.get("outcome"): print(f"    outcome: {r.get('outcome')}")

print("\n\n" + "=" * 70)
print("MF FLOWS — combined AHL + AMC-FMR for OGDC and POL")
print("=" * 70)
for path in ["data/flows/mutual_fund_holdings.parquet","data/flows/amc_fmr_holdings.parquet"]:
    fp = PROJECT / path
    if not fp.exists():
        print(f"MISSING {path}")
        continue
    df = pd.read_parquet(fp)
    print(f"\n--- {path} (total rows: {len(df)}, freshest: {df['as_of_month'].max() if 'as_of_month' in df.columns else 'n/a'}) ---")
    for sym in ("OGDC","POL"):
        sub = df[df["symbol"] == sym] if "symbol" in df.columns else df.iloc[0:0]
        if sub.empty:
            print(f"  {sym}: no holdings recorded")
            continue
        # group by fund, latest report
        if "as_of_month" in sub.columns:
            sub = sub.sort_values("as_of_month")
        sub_latest = sub.groupby("fund_name").tail(1) if "fund_name" in sub.columns else sub
        sub_latest = sub_latest.sort_values("pct_of_fund", ascending=False).head(8)
        print(f"  {sym}: {len(sub)} total records, top funds by pct_of_fund (latest each):")
        for _, row in sub_latest.iterrows():
            print(f"    {row.get('as_of_month','?')} | {row.get('amc','?')[:35]} | {row.get('fund_name','?')[:45]} | {row.get('pct_of_fund','?')}% | rank #{row.get('rank_in_fund','?')}")

print("\n\n" + "=" * 70)
print("EOD PRICES — last 5 bars for OGDC + POL")
print("=" * 70)
for sym in ("OGDC","POL"):
    candidates = list((PROJECT / "data/eod").glob(f"*{sym}*.parquet")) + list((PROJECT / "data/eod").glob(f"*{sym}*.csv"))
    if not candidates:
        # try a combined file
        combined = PROJECT / "data/eod/eod.parquet"
        if combined.exists():
            df = pd.read_parquet(combined)
            sub = df[df["symbol"] == sym] if "symbol" in df.columns else df[df.index.get_level_values(0) == sym] if hasattr(df.index, "get_level_values") else df
            if not sub.empty:
                print(f"\n--- {sym} EOD (from combined) ---")
                print(sub.tail(5).to_string())
            continue
        else:
            print(f"\n{sym}: no EOD file found")
            continue
    fp = candidates[0]
    if fp.suffix == ".parquet":
        df = pd.read_parquet(fp)
    else:
        df = pd.read_csv(fp)
    print(f"\n--- {sym} EOD ({fp.name}) ---")
    print(df.tail(5).to_string())

print("\n\n" + "=" * 70)
print("NEWS SCORES — recent OGDC + POL")
print("=" * 70)
news_dir = PROJECT / "data/news"
if news_dir.exists():
    files = sorted(news_dir.glob("*.json"))[-5:]
    print(f"Recent news files: {[f.name for f in files]}")
    for f in files[-2:]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            # try to find OGDC/POL entries
            if isinstance(d, list):
                for item in d:
                    if isinstance(item, dict) and any(s in str(item).upper() for s in ("OGDC","POL ","PAKISTAN OILFIELDS","OIL & GAS DEVELOPMENT")):
                        print(f"\n  [{f.name}]")
                        print(f"  {json.dumps(item, default=str)[:400]}")
                        break
            elif isinstance(d, dict):
                for sym in ("OGDC","POL"):
                    v = d.get(sym) or d.get("per_symbol",{}).get(sym)
                    if v:
                        print(f"\n  [{f.name}] {sym}: {json.dumps(v, default=str)[:400]}")
        except Exception as e:
            print(f"  err {f}: {e}")
else:
    print("No data/news dir")
