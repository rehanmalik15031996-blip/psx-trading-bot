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
SECTOR_MEDIANS_PATH = CACHE_DIR / "_sector_medians.json"


def _latest_psx_close(symbol: str) -> float | None:
    """Most recent PSX close from the cached OHLCV parquet.

    yfinance ``regularMarketPrice`` is often days stale for PSX names, so
    every derived ratio (P/E, P/B, dividend yield, payout ratio) is
    anchored on the price our own pipeline already trusts.
    """
    try:
        from data.store import load_ohlcv
        df = load_ohlcv(symbol)
        if df is None or df.empty:
            return None
        c = float(df.iloc[-1]["close"])
        return c if c > 0 else None
    except Exception:
        return None


def _safe_div(num, den):
    """Float division that returns None instead of raising on bad inputs."""
    try:
        if num is None or den is None:
            return None
        n = float(num)
        d = float(den)
        if d == 0:
            return None
        return n / d
    except (TypeError, ValueError):
        return None


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

        Includes income-statement series, balance-sheet aggregates (total
        equity, total debt, total assets), latest 6 dividend dates, and
        the next earnings timestamp when yfinance exposes one.

        Returns at minimum::

            {"symbol": "OGDC", "as_of_utc": "...", "ok": True,
             "book_value_per_share": 321.71, "eps_ttm": 32.12,
             "shares_outstanding": 4.3e9, "market_cap_pkr": 5.6e11,
             "total_equity_pkr": 1.25e12, "total_debt_pkr": 0.0,
             "total_assets_pkr": 1.5e12,
             "dividend_ttm": 17.5, "dividend_5y_avg": 14.2,
             "n_dividends_lifetime": 75,
             "last_dividend_dates": ["2026-03-15", ...],
             "next_earnings_date_utc": "2026-04-29T16:00:00Z" | None,
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
            last_div_dates: list[str] = []
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
                # Last 6 dividend dates (newest first) for cadence-based
                # earnings prediction
                last_div_dates = [d.date().isoformat()
                                   for d in divs.index.sort_values(
                                       ascending=False)[:6]]

            # Balance-sheet aggregates (total equity, total debt, total assets)
            bs = t.balance_sheet
            tot_eq = tot_dt = tot_as = None
            if bs is not None and not bs.empty:
                # Equity — try several common labels in order
                for lbl in ("Stockholders Equity",
                            "Total Equity Gross Minority Interest",
                            "Common Stock Equity"):
                    if lbl in bs.index:
                        try:
                            tot_eq = float(bs.loc[lbl].dropna().iloc[0])
                            break
                        except Exception:
                            pass
                # Debt — prefer Total Debt; otherwise sum LT + ST debt
                for lbl in ("Total Debt",
                            "Long Term Debt And Capital Lease Obligation",
                            "Long Term Debt"):
                    if lbl in bs.index:
                        try:
                            tot_dt = float(bs.loc[lbl].dropna().iloc[0])
                            break
                        except Exception:
                            pass
                # Total assets
                for lbl in ("Total Assets", ):
                    if lbl in bs.index:
                        try:
                            tot_as = float(bs.loc[lbl].dropna().iloc[0])
                            break
                        except Exception:
                            pass

            # Next earnings date when yfinance has it
            next_earn = None
            ts = info.get("earningsTimestamp")
            if ts:
                try:
                    next_earn = (pd.Timestamp(int(ts), unit="s", tz="UTC")
                                  .isoformat())
                except Exception:
                    pass
            else:
                # Sometimes lives under t.calendar
                try:
                    cal = t.calendar
                    if isinstance(cal, dict):
                        ed = cal.get("Earnings Date")
                        if ed:
                            d0 = ed[0] if isinstance(ed, list) else ed
                            next_earn = pd.Timestamp(d0).tz_localize(
                                "UTC", nonexistent="shift_forward",
                                ambiguous="NaT").isoformat()
                except Exception:
                    pass

            # ---------------- derived valuation ratios ----------------
            # Anchored on the most recent PSX close (our own cache), NOT
            # yfinance ``regularMarketPrice`` which is days stale for
            # Pakistani equities. This means the ratios match what the
            # analyst sees on the screen the same morning.
            psx_close = _latest_psx_close(symbol)
            book_f = float(book) if book is not None else None
            eps_f = float(eps_ttm) if eps_ttm is not None else None
            div_f = float(div_ttm) if div_ttm is not None else None

            pe_ratio = _safe_div(psx_close, eps_f)
            pb_ratio = _safe_div(psx_close, book_f)
            div_yield_pct = (
                _safe_div(div_f, psx_close) * 100.0
                if (_safe_div(div_f, psx_close) is not None) else None
            )
            payout_pct = (
                _safe_div(div_f, eps_f) * 100.0
                if (_safe_div(div_f, eps_f) is not None) else None
            )
            # Payout ratio can spike past 100% in down-earnings years
            # (companies often hold the dividend constant). We clip to a
            # sane band so downstream interpretation isn't dominated by
            # outliers, but we keep the raw number too for transparency.
            payout_pct_clipped = (
                max(0.0, min(200.0, payout_pct))
                if payout_pct is not None else None
            )

            rec.update({
                "ok": True,
                "currency": info.get("currency", "PKR"),
                "book_value_per_share": (round(book_f, 4)
                                         if book_f is not None else None),
                "eps_ttm": (round(eps_f, 4)
                            if eps_f is not None else None),
                "shares_outstanding": (float(shares)
                                       if shares is not None else None),
                "market_cap_pkr": (float(mcap) if mcap is not None else None),
                "total_equity_pkr": tot_eq,
                "total_debt_pkr": tot_dt,
                "total_assets_pkr": tot_as,
                "dividend_ttm": div_ttm,
                "dividend_5y_avg": div_5y_avg,
                "n_dividends_lifetime": n_divs,
                "last_dividend_dates": last_div_dates,
                "next_earnings_date_utc": next_earn,
                "revenue_5y": rev_5y,
                "net_income_5y": ni_5y,
                "eps_5y": eps_5y,
                "yf_stale_price": (float(info.get("regularMarketPrice"))
                                   if info.get("regularMarketPrice") is not None
                                   else None),
                # New derived ratios — analyst-facing
                "psx_close_pkr": (round(psx_close, 4)
                                  if psx_close is not None else None),
                "pe_ratio": (round(pe_ratio, 2)
                             if pe_ratio is not None else None),
                "pb_ratio": (round(pb_ratio, 2)
                             if pb_ratio is not None else None),
                "dividend_yield_pct": (round(div_yield_pct, 2)
                                       if div_yield_pct is not None else None),
                "payout_ratio_pct": (round(payout_pct_clipped, 1)
                                     if payout_pct_clipped is not None
                                     else None),
                "payout_ratio_pct_raw": (round(payout_pct, 1)
                                         if payout_pct is not None else None),
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
                    "total_equity_pkr": rec.get("total_equity_pkr"),
                    "total_debt_pkr": rec.get("total_debt_pkr"),
                    "total_assets_pkr": rec.get("total_assets_pkr"),
                    "dividend_ttm": rec.get("dividend_ttm"),
                    "dividend_5y_avg": rec.get("dividend_5y_avg"),
                    "n_dividends_lifetime": rec.get("n_dividends_lifetime"),
                    "last_dividend_dates_json": json.dumps(
                        rec.get("last_dividend_dates") or []),
                    "next_earnings_date_utc": rec.get("next_earnings_date_utc"),
                    "revenue_5y_json": json.dumps(rec.get("revenue_5y") or []),
                    "net_income_5y_json": json.dumps(rec.get("net_income_5y") or []),
                    "eps_5y_json": json.dumps(rec.get("eps_5y") or []),
                    # Derived ratios (anchored on PSX close)
                    "psx_close_pkr": rec.get("psx_close_pkr"),
                    "pe_ratio": rec.get("pe_ratio"),
                    "pb_ratio": rec.get("pb_ratio"),
                    "dividend_yield_pct": rec.get("dividend_yield_pct"),
                    "payout_ratio_pct": rec.get("payout_ratio_pct"),
                    "payout_ratio_pct_raw": rec.get("payout_ratio_pct_raw"),
                    "error": rec.get("error", ""),
                }])
                df.to_parquet(CACHE_DIR / f"{sym}.parquet", index=False)
            except Exception as e:
                meta["results"][sym] = f"save_fail: {e}"

        META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Recompute sector medians (P/E, P/B, dividend yield, payout
        # ratio) once the universe-wide refresh is finished. We keep
        # this in a separate module so callers can also recompute it
        # cheaply at any time without re-pulling yfinance.
        try:
            from brain.sector_ratios import refresh_sector_medians
            refresh_sector_medians()
        except Exception as e:
            meta["sector_medians_error"] = f"{type(e).__name__}: {e}"
            META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        elapsed = (time.perf_counter() - start) * 1000.0
        ok_count = sum(1 for v in meta["results"].values() if v == "ok")
        return FetchResult(
            name=self.name,
            ok=ok_count > 0,
            latency_ms=elapsed,
            format="parquet",
            schema=["symbol", "book_value_per_share", "eps_ttm",
                    "dividend_ttm", "shares_outstanding", "market_cap_pkr",
                    "pe_ratio", "pb_ratio", "dividend_yield_pct",
                    "payout_ratio_pct"],
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
        for k in ("revenue_5y", "net_income_5y", "eps_5y",
                  "last_dividend_dates"):
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
