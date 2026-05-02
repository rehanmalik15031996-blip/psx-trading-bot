"""Scrape MUFAP (Mutual Funds Association of Pakistan) monthly industry
Asset-Under-Management data for the last N months and persist to parquet.

This is the **upstream** source AHL summarises in its monthly Mutual Funds
Equity Holdings PDFs. Scraping MUFAP directly removes our dependency on
AHL's PDF cadence and lets us backfill 24+ months in one pass.

URL pattern
-----------
``https://www.mufap.com.pk/Industry/IndustryStatMonthly?datefrom=YYYY-MM&tab=1``

Each request returns one HTML page with one row per (AMC, Fund) combination
and a single column of AUMs (PKR mn) for the requested month. Funds that
were inceptioned after the requested month return ``"Not Published"``.

Outputs
-------
``data/flows/mufap_industry_aum.parquet``      -- per-fund per-month rows
``data/flows/mufap_industry_summary.parquet``  -- per-month industry rollup
``data/flows/equity_aums_monthly.parquet``     -- (compat) industry equity-AUMs %

Note: MUFAP categorises funds in a slightly different taxonomy than AHL.
For the **equity-AUMs %** signal that the strategist reads, we mirror AHL's
methodology: numerator = Open-End / Closed-End equity-flavoured funds
(not including pension VPS-Equity or capital-protected). Denominator =
total industry AUM **excluding** voluntary pension schemes (AHL reports
this excluding VPS).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_FUND_PARQUET = ROOT / "data" / "flows" / "mufap_industry_aum.parquet"
OUT_SUMMARY_PARQUET = ROOT / "data" / "flows" / "mufap_industry_summary.parquet"
OUT_EQUITY_AUMS_PARQUET = ROOT / "data" / "flows" / "equity_aums_monthly.parquet"

URL_TPL = ("https://www.mufap.com.pk/Industry/IndustryStatMonthly"
           "?datefrom={ym}&tab=1")

# Categories that count toward the AHL-style "equity AUMs". The strict
# interpretation: only categories whose primary mandate is direct PSX
# equity exposure. Hybrid (Asset Allocation, Balanced) flows in/out so
# they're included; Capital-Protected and Fund-of-Funds excluded.
_EQUITY_CATEGORIES = {
    "Equity",
    "Shariah Compliant Equity",
    "Dedicated Equity",
    "Shariah Compliant Dedicated Equity",
    "Asset Allocation",
    "Shariah Compliant Asset Allocation",
    "Balanced",
    "Shariah Compliant Balanced",
    "Index Tracker",
    "Shariah Compliant Index Tracker",
}

# Categories that count toward "debt + money-market + fixed-rate" — the
# rest of the open-end/closed-end industry.
_DEBT_CATEGORIES = {
    "Income", "Aggressive Fixed Income",
    "Shariah Compliant Income", "Money Market",
    "Shariah Compliant Money Market", "Fixed Rate / Return",
    "Shariah Compliant Fixed Rate / Return", "Sovereign",
    "Capital Protected", "Government Securities",
    "Shariah Compliant Government Securities",
}

# Categories to EXCLUDE from the AHL-style equity-AUMs % numerator AND
# denominator (they sit in pension wrappers, not the open-end industry).
_EXCLUDE_FROM_DENOMINATOR = re.compile(r"^VPS-|^Voluntary Pension")

_TR_RE = re.compile(r"<tr data-filter=.*?>(.*?)</tr>", re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _ym_iter(start_ym: str, end_ym: str):
    """Yield 'YYYY-MM' strings (inclusive) between two dates."""
    s = datetime.strptime(start_ym, "%Y-%m").date().replace(day=1)
    e = datetime.strptime(end_ym, "%Y-%m").date().replace(day=1)
    cur = s
    while cur <= e:
        yield cur.strftime("%Y-%m")
        # Increment one month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)


def _fetch_month(ym: str, retries: int = 3) -> str:
    """GET MUFAP industry stats HTML for a single month."""
    url = URL_TPL.format(ym=ym)
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=60,
                             headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"MUFAP fetch failed for {ym}: {last_err}")


def _parse_row(html: str) -> list[str]:
    cells = _TD_RE.findall(html)
    out: list[str] = []
    for c in cells:
        s = _HTML_TAG_RE.sub("", c).strip()
        s = re.sub(r"\s+", " ", s)
        out.append(s)
    return out


def _to_float(s: str) -> float | None:
    s = s.replace(",", "").strip()
    if not s or s.lower() in ("not published", "n/a", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_month(html: str, ym: str) -> list[dict]:
    """Extract per-fund rows for a single requested month."""
    rows: list[dict] = []
    for tr_html in _TR_RE.findall(html):
        cells = _parse_row(tr_html)
        if len(cells) < 6:
            continue
        sector, amc, fund, category, inception, aum_str = cells[:6]
        aum = _to_float(aum_str)
        if aum is None:
            continue
        rows.append({
            "as_of_month": f"{ym}-01",
            "sector": sector,
            "amc": amc,
            "fund": fund,
            "category": category,
            "inception": inception,
            "aum_pkr_mn": aum,
            "source": "MUFAP",
            "ingested_at": _utcnow_iso(),
        })
    return rows


def _summarise(rows: list[dict]) -> list[dict]:
    """Compute one summary row per month with industry-level rollups."""
    if not rows:
        return []
    df = pd.DataFrame(rows)
    out: list[dict] = []
    for ym, grp in df.groupby("as_of_month"):
        # Open-end + Closed-end industry only (exclude VPS / Pension)
        non_pension_mask = ~grp["category"].str.match(_EXCLUDE_FROM_DENOMINATOR)
        non_pension = grp[non_pension_mask]

        equity_mask = non_pension["category"].isin(_EQUITY_CATEGORIES)
        debt_mask = non_pension["category"].isin(_DEBT_CATEGORIES)

        total_industry = float(non_pension["aum_pkr_mn"].sum())
        equity_aum = float(non_pension.loc[equity_mask, "aum_pkr_mn"].sum())
        debt_aum = float(non_pension.loc[debt_mask, "aum_pkr_mn"].sum())

        # Pure-equity (excludes hybrids) for the AHL "equity-AUMs %" metric
        pure_equity_mask = non_pension["category"].isin({
            "Equity", "Shariah Compliant Equity",
            "Dedicated Equity", "Shariah Compliant Dedicated Equity",
            "Index Tracker", "Shariah Compliant Index Tracker",
        })
        pure_equity_aum = float(
            non_pension.loc[pure_equity_mask, "aum_pkr_mn"].sum()
        )

        out.append({
            "as_of_month": ym,
            "total_industry_aum_pkr_mn": total_industry,
            "equity_aum_pkr_mn": equity_aum,
            "pure_equity_aum_pkr_mn": pure_equity_aum,
            "debt_aum_pkr_mn": debt_aum,
            "equity_aum_pct": (equity_aum / total_industry * 100.0)
                              if total_industry else None,
            "pure_equity_aum_pct": (pure_equity_aum / total_industry * 100.0)
                                    if total_industry else None,
            "debt_aum_pct": (debt_aum / total_industry * 100.0)
                            if total_industry else None,
            "n_funds": int(len(non_pension)),
            "n_equity_funds": int(equity_mask.sum()),
            "n_pure_equity_funds": int(pure_equity_mask.sum()),
            "source": "MUFAP",
            "ingested_at": _utcnow_iso(),
        })
    return sorted(out, key=lambda r: r["as_of_month"])


def _write_parquet_idempotent(rows: list[dict], path: Path,
                                unique_keys: list[str]) -> int:
    """Upsert rows into the parquet, replacing matches on unique_keys."""
    if not rows:
        return 0
    new_df = pd.DataFrame(rows)
    if path.exists():
        try:
            old_df = pd.read_parquet(path)
            mask = ~old_df.set_index(unique_keys).index.isin(
                new_df.set_index(unique_keys).index
            )
            merged = pd.concat([old_df[mask], new_df], ignore_index=True)
        except Exception as e:
            print(f"  WARN: could not read existing {path.name}: {e}; "
                  f"overwriting")
            merged = new_df
    else:
        merged = new_df
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = merged.sort_values(unique_keys)
    merged.to_parquet(path, index=False)
    return len(merged)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", default=None,
                     help="Start month YYYY-MM (default = today minus 24m)")
    ap.add_argument("--end", default=None,
                     help="End month YYYY-MM (default = current month)")
    ap.add_argument("--validate", action="store_true",
                     help="Print parquet stats and exit.")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    if args.end:
        end_ym = args.end
    else:
        end_ym = today.strftime("%Y-%m")
    if args.start:
        start_ym = args.start
    else:
        # 24 months back
        m = today.month - 24
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        start_ym = f"{y:04d}-{m:02d}"

    print(f"MUFAP ingest: {start_ym} -> {end_ym}")

    if args.validate:
        return _validate()

    all_rows: list[dict] = []
    for ym in _ym_iter(start_ym, end_ym):
        print(f"  fetching {ym} ...", end=" ", flush=True)
        try:
            html = _fetch_month(ym)
        except Exception as e:
            print(f"FAILED ({type(e).__name__}: {e})")
            continue
        rows = _parse_month(html, ym)
        all_rows.extend(rows)
        print(f"{len(rows):>3d} fund-rows")

    if not all_rows:
        print("\nNo data fetched. Exiting.")
        return 1

    n_funds = _write_parquet_idempotent(
        all_rows, OUT_FUND_PARQUET,
        unique_keys=["as_of_month", "amc", "fund", "category"],
    )
    print(f"\nfund-aum parquet:  {n_funds:,} rows -> {OUT_FUND_PARQUET.name}")

    summary_rows = _summarise(all_rows)
    n_sum = _write_parquet_idempotent(
        summary_rows, OUT_SUMMARY_PARQUET,
        unique_keys=["as_of_month"],
    )
    print(f"summary parquet:   {n_sum:,} rows -> {OUT_SUMMARY_PARQUET.name}")

    # Compatibility view: equity-AUMs % monthly
    eq_rows = [{
        "as_of_month": r["as_of_month"],
        "equity_aum_pct": r["equity_aum_pct"],
        "pure_equity_aum_pct": r["pure_equity_aum_pct"],
        "equity_aum_pkr_mn": r["equity_aum_pkr_mn"],
        "total_industry_aum_pkr_mn": r["total_industry_aum_pkr_mn"],
        "source": "MUFAP",
    } for r in summary_rows]
    n_eq = _write_parquet_idempotent(
        eq_rows, OUT_EQUITY_AUMS_PARQUET,
        unique_keys=["as_of_month"],
    )
    print(f"equity-aums view:  {n_eq:,} rows -> {OUT_EQUITY_AUMS_PARQUET.name}")

    return _validate()


def _validate() -> int:
    if OUT_SUMMARY_PARQUET.exists():
        df = pd.read_parquet(OUT_SUMMARY_PARQUET)
        print()
        print("== validation ==")
        print(f"  months: {len(df):>3d}  (range "
              f"{df['as_of_month'].min()} .. {df['as_of_month'].max()})")
        print()
        print("  Last 6 months (equity AUMs %):")
        sample = df.tail(6)[["as_of_month", "total_industry_aum_pkr_mn",
                              "equity_aum_pct", "pure_equity_aum_pct",
                              "n_funds"]]
        print(sample.to_string(index=False))
    else:
        print("No summary parquet on disk yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
