"""PSX dividend calendar connector.

Sources:
  1. azeetrade.com/dividend-calendar.php  — live PSX dividend announcements
     with ex-date, rate, yield (best structured source found)
  2. psxterminal.com/dividends           — backup; similar structured table
  3. data/fundamentals/<SYM>.parquet      — fallback using last dividend
     from the cached fundamentals parquet (always available offline)

Output:
  A list of dicts: {symbol, announcement_date, ex_date, record_date,
                    cash_dividend_pkr, yield_pct, source}

Saved to: data/dividends/upcoming.parquet
Also returns the list as FetchResult.records for the health monitor.
"""

from __future__ import annotations

import io
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from connectors.base import BaseConnector, ConnectionResult, FetchResult

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIVIDEND_DIR = PROJECT_ROOT / "data" / "dividends"

AZEE_URL = "https://www.azeetrade.com/dividend-calendar.php"
PSX_TERM_URL = "https://psxterminal.com/dividends"


def _pct_to_float(raw) -> float | None:
    """'160%' -> 160.0, '0' -> 0.0, NaN/blank -> None."""
    if raw is None:
        return None
    try:
        if pd.isna(raw):
            return None
    except (TypeError, ValueError):
        pass
    s = str(raw).replace("%", "").replace("PKR", "").strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_azee(html: str, as_of: date) -> list[dict]:
    """Parse azeetrade dividend table HTML into structured records.

    Fixed 2026-09-01: two independent breaks found live, not from a stale
    memory of how the site used to look --
      1. pandas 3.0 requires literal HTML wrapped in io.StringIO(); passing
         the raw string directly now raises FileNotFoundError (it gets
         treated as a path). Both try/except branches were silently eating
         this and returning [].
      2. The site's actual columns are 'KATS Code' / 'Name' / 'X Date' /
         'Dividend' / 'Bonus' / 'Right Issue' -- not 'symbol'/'scrip' or
         'ex date', so even with (1) fixed the old matching never found the
         table. KATS Code IS the same ticker convention used everywhere
         else in this repo (verified against live data: UBL, LOTCHEM,
         EFERT, FFC all present).

    Dividend/Bonus/Right Issue are printed as a percentage OF FACE VALUE
    (standard PSX convention, not a percentage of market price) -- e.g.
    '160%' on a Rs 10 face-value share is a Rs 16/share cash dividend.
    cash_dividend_pkr below assumes the standard Rs 10 face value; this is
    a documented approximation (a handful of PSX-listed banks/insurers use
    other face values), not silently exact for every symbol.
    """
    try:
        tables = pd.read_html(io.StringIO(html))
    except Exception:
        return []

    FACE_VALUE_PKR = 10.0
    records = []
    for tbl in tables:
        cols = [str(c).lower().strip() for c in tbl.columns]
        sym_col = next((c for c in cols if "kats" in c or "symbol" in c or "scrip" in c), None)
        ex_col = next((c for c in cols if "x date" in c or ("ex" in c and "date" in c)
                       or c == "date"), None)
        div_col = next((c for c in cols if "dividend" in c), None)
        bonus_col = next((c for c in cols if "bonus" in c), None)
        rights_col = next((c for c in cols if "right" in c), None)

        if sym_col is None or ex_col is None:
            continue
        tbl.columns = cols

        for _, row in tbl.iterrows():
            try:
                symbol = str(row[sym_col]).strip().upper()
                if not symbol or symbol in ("NAN", "SYMBOL", "SCRIP", "KATS CODE"):
                    continue
                ex_raw = str(row[ex_col]).strip()
                ex_date = pd.to_datetime(ex_raw, errors="coerce", dayfirst=False)
                if pd.isna(ex_date):
                    continue
                ex_date_d = ex_date.date()
                # Skip past ex-dates (more than 5 days ago)
                if (ex_date_d - as_of).days < -5:
                    continue

                div_pct = _pct_to_float(row.get(div_col)) if div_col else None
                bonus_pct = _pct_to_float(row.get(bonus_col)) if bonus_col else None
                rights_pct = _pct_to_float(row.get(rights_col)) if rights_col else None
                cash = (round(div_pct / 100.0 * FACE_VALUE_PKR, 2)
                        if div_pct else None)

                records.append({
                    "symbol": symbol,
                    "announcement_date": str(as_of),
                    "ex_date": str(ex_date_d),
                    "record_date": None,
                    "dividend_pct": div_pct,
                    "cash_dividend_pkr": cash,
                    "bonus_pct": bonus_pct,
                    "rights_pct": rights_pct,
                    "yield_pct": None,
                    "source": "azeetrade.com",
                })
            except Exception:
                continue
    return records


def _fundamentals_fallback(as_of: date) -> list[dict]:
    """Extract upcoming dividend estimates from cached fundamentals parquets."""
    fund_dir = PROJECT_ROOT / "data" / "fundamentals"
    records = []
    if not fund_dir.exists():
        return records
    for pq in fund_dir.glob("*.parquet"):
        symbol = pq.stem
        if symbol.startswith("_"):
            continue
        try:
            df = pd.read_parquet(pq)
            if df.empty:
                continue
            # Look for dividend_yield or last_dividend columns
            div_yield = None
            if "dividend_yield" in df.columns:
                latest = df.sort_values("as_of_utc").iloc[-1]
                raw_yield = latest["dividend_yield"]
                if pd.notna(raw_yield):
                    div_yield = float(raw_yield) * 100 if float(raw_yield) < 1 else float(raw_yield)

            if div_yield and div_yield >= 5.0:
                # Estimate ex-date: PSX dividend season peaks Apr-Jul and Oct-Dec
                # Use a rough 90-day forward estimate if no ex-date available
                est_ex = as_of + timedelta(days=60)
                records.append({
                    "symbol": symbol,
                    "announcement_date": str(as_of),
                    "ex_date": str(est_ex),
                    "record_date": None,
                    "cash_dividend_pkr": None,
                    "yield_pct": round(div_yield, 2),
                    "source": "fundamentals_fallback",
                })
        except Exception:
            continue
    return records


class DividendCalendarConnector(BaseConnector):
    name = "Dividend Calendar (azeetrade / psxterminal)"
    category = "corporate-actions"
    layer = "Layer 4 — Sentiment & Flow"
    url = AZEE_URL

    TIMEOUT = 25

    def test(self) -> ConnectionResult:
        start_ts = time.perf_counter()
        try:
            import requests
            r = requests.get(
                AZEE_URL,
                headers=self.DEFAULT_HEADERS,
                timeout=self.TIMEOUT,
            )
            elapsed = (time.perf_counter() - start_ts) * 1000.0
            r.raise_for_status()
            records = _parse_azee(r.text, date.today())
            return ConnectionResult(
                name=self.name,
                ok=True,
                latency_ms=elapsed,
                sample={"upcoming_dividends": len(records)},
                notes=f"{len(records)} entries parsed from azeetrade",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name,
                ok=False,
                latency_ms=(time.perf_counter() - start_ts) * 1000.0,
                error=f"{type(e).__name__}: {e}",
            )

    def fetch(self, as_of: date | None = None) -> FetchResult:
        """Fetch upcoming PSX dividend events and save to parquet."""
        start_ts = time.perf_counter()
        if as_of is None:
            as_of = date.today()

        records: list[dict] = []
        errors: list[str] = []

        # Source 1: azeetrade
        try:
            import requests
            r = requests.get(AZEE_URL, headers=self.DEFAULT_HEADERS, timeout=self.TIMEOUT)
            r.raise_for_status()
            azee_records = _parse_azee(r.text, as_of)
            records.extend(azee_records)
        except Exception as e:
            errors.append(f"azeetrade: {type(e).__name__}: {e}")

        # Source 2: psxterminal (backup if azeetrade returned nothing)
        if not records:
            try:
                import requests
                r2 = requests.get(PSX_TERM_URL, headers=self.DEFAULT_HEADERS, timeout=self.TIMEOUT)
                r2.raise_for_status()
                psx_records = _parse_azee(r2.text, as_of)  # same HTML-table parsing
                records.extend(psx_records)
                for rec in psx_records:
                    rec["source"] = "psxterminal.com"
            except Exception as e:
                errors.append(f"psxterminal: {type(e).__name__}: {e}")

        # Source 3: fundamentals fallback
        if not records:
            fb_records = _fundamentals_fallback(as_of)
            records.extend(fb_records)
            if fb_records:
                errors.append("Using fundamentals fallback — web sources unavailable")

        # Deduplicate by symbol+ex_date
        seen = set()
        deduped = []
        for rec in records:
            key = (rec.get("symbol"), rec.get("ex_date"))
            if key not in seen:
                seen.add(key)
                deduped.append(rec)

        # Save to parquet
        elapsed = (time.perf_counter() - start_ts) * 1000.0
        if deduped:
            try:
                DIVIDEND_DIR.mkdir(parents=True, exist_ok=True)
                df = pd.DataFrame(deduped)
                df.to_parquet(DIVIDEND_DIR / "upcoming.parquet", index=False)
            except Exception as e:
                errors.append(f"parquet save: {e}")

        return FetchResult(
            name=self.name,
            ok=bool(deduped),
            latency_ms=elapsed,
            format="dataframe",
            schema=["symbol", "ex_date", "dividend_pct", "cash_dividend_pkr",
                    "bonus_pct", "rights_pct", "yield_pct", "source"],
            records=deduped,
            extras={"errors": errors, "as_of": str(as_of)},
            summary=(f"{len(deduped)} upcoming dividends; "
                     f"high-yield (≥7%): "
                     f"{sum(1 for r in deduped if r.get('yield_pct') and r['yield_pct'] >= 7)}"),
        )


def load_upcoming_dividends(as_of: date | None = None, min_yield_pct: float = 0.0) -> list[dict]:
    """Load saved dividend calendar from parquet. Returns [] if not available.

    Used by brain/calendar_alpha.py for dividend-capture signal and by
    brain/master_strategist.py to surface ex-date context in briefings.
    """
    pq = DIVIDEND_DIR / "upcoming.parquet"
    if not pq.exists():
        return []
    try:
        df = pd.read_parquet(pq)
        if as_of:
            df["ex_date"] = pd.to_datetime(df["ex_date"])
            df = df[df["ex_date"].dt.date >= as_of]
        if min_yield_pct > 0:
            df = df[df["yield_pct"].fillna(0) >= min_yield_pct]
        return df.to_dict(orient="records")
    except Exception:
        return []


if __name__ == "__main__":
    conn = DividendCalendarConnector()
    print("Connection test:", conn.test())
    print("\nFetching dividends...")
    result = conn.fetch()
    print(f"  ok={result.ok}, {result.summary}")
    for rec in result.records[:5]:
        print(f"  {rec}")
    if result.extras.get("errors"):
        print(f"  errors: {result.extras['errors']}")
