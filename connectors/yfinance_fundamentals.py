"""yfinance connector — per-stock fundamentals for the PSX universe.

Pulls EPS (TTM), Book Value Per Share, dividend history, and 5-year income
statement for each ticker via Yahoo Finance with the `.KA` suffix
(e.g. ``OGDC.KA``).

Yahoo's *price* feed for PSX names is often stale, so we DO NOT use it for
current price — that comes from PSX DPS via ``connectors/psx_historical.py``.
We only trust filings-based fields: bookValue, financials, dividends,
sharesOutstanding, marketCap.

Cache layout::

    data/fundamentals/{SYMBOL}.parquet     # one row per refresh, time-stamped
    data/fundamentals/_meta.json           # last refresh time, success/fail map

Refreshed weekly by ``.github/workflows/fundamentals.yml`` and on demand by
``scripts/refresh_fundamentals.py``.
"""

from __future__ import annotations

import json
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from connectors.base import BaseConnector, ConnectionResult, FetchResult

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "fundamentals"
META_PATH = CACHE_DIR / "_meta.json"


class YFinanceFundamentalsConnector(BaseConnector):
    name = "yfinance (PSX fundamentals)"
    category = "fundamentals"
    layer = "Layer 8 — Fundamentals"
    url = "https://finance.yahoo.com"

    PROBE_SYMBOL = "OGDC"

    def test(self) -> ConnectionResult:
        try:
            import yfinance as yf

            def _pull():
                t = yf.Ticker(self.PROBE_SYMBOL + ".KA")
                info = t.info
                return {
                    "bookValue": info.get("bookValue"),
                    "marketCap": info.get("marketCap"),
                    "currency": info.get("currency"),
                }

            sample, elapsed = self._timed(_pull)
            ok = bool(sample.get("bookValue") or sample.get("marketCap"))
            return ConnectionResult(
                name=self.name,
                ok=ok,
                latency_ms=elapsed,
                sample=sample,
                notes=(f"probe {self.PROBE_SYMBOL}.KA "
                       f"book={sample.get('bookValue')} "
                       f"mcap={sample.get('marketCap')}"),
                error=None if ok else "no fundamental fields returned",
            )
        except Exception as e:
            return ConnectionResult(
                name=self.name, ok=False, latency_ms=0.0,
                error=f"{type(e).__name__}: {e}",
            )

    # ---------------------------------------------------------------- fetch
    def fetch_one(self, symbol: str) -> dict[str, Any]:
        """Fetch a single stock's fundamentals as a flat dict.

        Returns at minimum::

            {"symbol": "OGDC", "as_of_utc": "...", "ok": True,
             "book_value_per_share": 321.71, "eps_ttm": 32.12,
             "shares_outstanding": 4.3e9, "market_cap_pkr": 5.6e11,
             "dividend_ttm": 17.5, "dividend_5y_avg": 14.2,
             "n_dividends_lifetime": 75,
             "revenue_5y": [...], "net_income_5y": [...],
             "eps_5y": [...], "currency": "PKR"}

        On failure returns ``{"symbol": ..., "ok": False, "error": "..."}``.
        """
        import yfinance as yf
        import pandas as pd

        rec: dict[str, Any] = {
            "symbol": symbol,
            "as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ok": False,
        }
        try:
            t = yf.Ticker(symbol + ".KA")
            info = t.info or {}

            book = info.get("bookValue")
            shares = info.get("sharesOutstanding")
            mcap = info.get("marketCap")
            eps_ttm = info.get("trailingEps")

            # Pull annual financials (rows = line items, cols = fiscal years)
            fins = t.financials  # may be None / empty
            ni_5y: list[float] = []
            rev_5y: list[float] = []
            eps_5y: list[float] = []
            if fins is not None and not fins.empty:
                row_ni = next(
                    (r for r in fins.index if "Net Income" in str(r)
                     and "Common" not in str(r) and "Continuous" not in str(r)),
                    None,
                )
                row_rev = next(
                    (r for r in fins.index if "Total Revenue" in str(r)
                     or str(r) == "Revenue"),
                    None,
                )
                if row_ni is not None:
                    ni_5y = [float(v) for v in fins.loc[row_ni].dropna().tolist()]
                if row_rev is not None:
                    rev_5y = [float(v) for v in fins.loc[row_rev].dropna().tolist()]
                # Compute EPS for each year if we have shares
                if shares and ni_5y:
                    eps_5y = [round(v / float(shares), 2) for v in ni_5y]

            # Fallback: derive TTM EPS from latest annual NI / shares
            if eps_ttm is None and ni_5y and shares:
                eps_ttm = round(float(ni_5y[0]) / float(shares), 2)

            # Dividends — TTM and 5y average per share
            divs = t.dividends
            div_ttm = None
            div_5y_avg = None
            n_divs = 0
            if divs is not None and not divs.empty:
                divs = divs.copy()
                # yfinance sometimes returns naive; coerce to UTC
                if getattr(divs.index, "tz", None) is None:
                    divs.index = pd.to_datetime(divs.index, utc=True)
                else:
                    divs.index = divs.index.tz_convert("UTC")
                n_divs = int(len(divs))
                cutoff_ttm = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)
                ttm = divs[divs.index >= cutoff_ttm]
                div_ttm = round(float(ttm.sum()), 4) if len(ttm) else 0.0
                cutoff_5y = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=5 * 365)
                last5 = divs[divs.index >= cutoff_5y]
                if len(last5):
                    # annualize: total of last 5y / 5
                    div_5y_avg = round(float(last5.sum()) / 5.0, 4)

            rec.update({
                "ok": True,
                "currency": info.get("currency", "PKR"),
                "book_value_per_share": (round(float(book), 4)
                                         if book is not None else None),
                "eps_ttm": (round(float(eps_ttm), 4)
                            if eps_ttm is not None else None),
                "shares_outstanding": (float(shares)
                                       if shares is not None else None),
                "market_cap_pkr": (float(mcap) if mcap is not None else None),
                "dividend_ttm": div_ttm,
                "dividend_5y_avg": div_5y_avg,
                "n_dividends_lifetime": n_divs,
                "revenue_5y": rev_5y,
                "net_income_5y": ni_5y,
                "eps_5y": eps_5y,
                "yf_stale_price": (float(info.get("regularMarketPrice"))
                                   if info.get("regularMarketPrice") is not None
                                   else None),
            })
        except Exception as e:
            rec["error"] = f"{type(e).__name__}: {e}"
        return rec

    # ----------------------------------------------------------------- I/O
    def fetch(self, symbols: list[str] | None = None) -> FetchResult:
        """Fetch fundamentals for every symbol in ``symbols`` (default: universe).

        Each symbol is written to ``data/fundamentals/{SYMBOL}.parquet`` (one
        row per refresh, append-only history). The latest record per symbol is
        what every downstream consumer (``brain/valuation.py``,
        ``ui/tools.get_value_signal``) reads via :func:`load_latest`.
        """
        from config.universe import symbols as universe_symbols
        import pandas as pd

        syms = symbols or universe_symbols()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        start = time.perf_counter()
        records: list[dict] = []
        meta = {"refreshed_utc": datetime.now(timezone.utc)
                                          .isoformat(timespec="seconds"),
                "results": {}}
        for sym in syms:
            rec = self.fetch_one(sym)
            records.append(rec)
            meta["results"][sym] = ("ok" if rec.get("ok") else
                                    rec.get("error", "fail"))
            # Save as a 1-row parquet (overwrite latest snapshot per symbol).
            # We keep a flat schema so downstream readers don't need to deal
            # with growing history per file.
            try:
                df = pd.DataFrame([{
                    "symbol": rec["symbol"],
                    "as_of_utc": rec["as_of_utc"],
                    "ok": rec["ok"],
                    "currency": rec.get("currency"),
                    "book_value_per_share": rec.get("book_value_per_share"),
                    "eps_ttm": rec.get("eps_ttm"),
                    "shares_outstanding": rec.get("shares_outstanding"),
                    "market_cap_pkr": rec.get("market_cap_pkr"),
                    "dividend_ttm": rec.get("dividend_ttm"),
                    "dividend_5y_avg": rec.get("dividend_5y_avg"),
                    "n_dividends_lifetime": rec.get("n_dividends_lifetime"),
                    "revenue_5y_json": json.dumps(rec.get("revenue_5y") or []),
                    "net_income_5y_json": json.dumps(rec.get("net_income_5y") or []),
                    "eps_5y_json": json.dumps(rec.get("eps_5y") or []),
                    "error": rec.get("error", ""),
                }])
                df.to_parquet(CACHE_DIR / f"{sym}.parquet", index=False)
            except Exception as e:
                meta["results"][sym] = f"save_fail: {e}"

        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        elapsed = (time.perf_counter() - start) * 1000.0
        ok_count = sum(1 for v in meta["results"].values() if v == "ok")
        return FetchResult(
            name=self.name,
            ok=ok_count > 0,
            latency_ms=elapsed,
            format="parquet",
            schema=["symbol", "book_value_per_share", "eps_ttm",
                    "dividend_ttm", "shares_outstanding", "market_cap_pkr"],
            records=records,
            extras={"meta": meta},
            summary=f"{ok_count}/{len(syms)} symbols fetched ok",
        )


# ----------------------------------------------------------------- helpers
def load_latest(symbol: str) -> dict | None:
    """Read the most recent parquet snapshot for ``symbol``.

    Returns ``None`` if the file is missing or unreadable. Lists stored as JSON
    strings (``revenue_5y_json`` etc.) are decoded back into Python lists.
    """
    import pandas as pd

    p = CACHE_DIR / f"{symbol}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if df.empty:
            return None
        row = df.iloc[-1].to_dict()
        for k in ("revenue_5y", "net_income_5y", "eps_5y"):
            jk = f"{k}_json"
            if jk in row:
                try:
                    row[k] = json.loads(row.pop(jk))
                except Exception:
                    row[k] = []
        return row
    except Exception:
        return None


def load_universe() -> dict[str, dict]:
    """Load latest fundamentals for every universe symbol.

    Symbols with missing or unreadable cache are skipped.
    """
    from config.universe import symbols as universe_symbols
    out: dict[str, dict] = {}
    for sym in universe_symbols():
        rec = load_latest(sym)
        if rec is not None:
            out[sym] = rec
    return out


if __name__ == "__main__":  # pragma: no cover  (manual run)
    print("Probing connector...")
    c = YFinanceFundamentalsConnector()
    pr = c.test()
    print(f"  test ok={pr.ok}  latency={pr.latency_ms:.0f}ms  notes={pr.notes}")
    if not pr.ok:
        raise SystemExit(1)
    print("\nFetching full universe...")
    fr = c.fetch()
    print(f"  fetch: {fr.summary}  ({fr.latency_ms:.0f}ms)")
    for r in fr.records:
        if r.get("ok"):
            print(f"  {r['symbol']:<6} BVPS={r.get('book_value_per_share'):>9} "
                  f"EPS_ttm={r.get('eps_ttm')!s:>8} "
                  f"div_ttm={r.get('dividend_ttm')!s:>8} "
                  f"NI_5y={r.get('net_income_5y') and len(r['net_income_5y'])}y")
        else:
            print(f"  {r['symbol']:<6} FAIL: {r.get('error')}")
