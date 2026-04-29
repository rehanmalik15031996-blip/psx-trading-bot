"""Refresh the daily macroeconomic-KPI time series.

The State Bank of Pakistan publishes a *current* snapshot of the policy
rate, KIBOR, T-bill and PIB yield curves, and FX reserves. That snapshot
is great for "what is it today?" questions but useless for the macro
impact engine, which needs to know whether T-bills moved, whether
reserves are tightening, and whether yield-curve shape changed.

This script appends today's snapshot to three on-disk parquets:

    data/macro/sbp_rates.parquet     (one row per business day)
    data/macro/kse100.parquet        (KSE-100 daily close from PSX DPS)
    data/macro/cpi_pakistan.parquet  (CPI YoY % - latest official print)

Each is keyed by date and is idempotent: rerunning the same day
overwrites the row rather than appending duplicates. The macro impact
engine consumes these files to detect:

  - T-bill 3M trend and gap vs policy rate (rate-cut expectation)
  - KIBOR-3M trend (banking funding cost)
  - FX reserves stress / recovery (BoP flag)
  - KSE-100 5d / 21d momentum (broad-market regime)
  - CPI YoY level and direction (real-rate signal)

Run::

    python scripts/refresh_macro_kpis.py
    python scripts/refresh_macro_kpis.py --skip-cpi   # skip slow PBS pull
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

OUT_DIR = ROOT / "data" / "macro"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  SBP rates time series
# ---------------------------------------------------------------------------
def refresh_sbp_rates() -> dict:
    """Append today's SBP snapshot to ``data/macro/sbp_rates.parquet``.

    Stored columns:
      ``date, policy_rate_pct, ceiling_pct, floor_pct, kibor_3m_pct,
       kibor_6m_pct, kibor_12m_pct, tbill_1m_pct, tbill_3m_pct,
       tbill_6m_pct, tbill_12m_pct, pib_3y_pct, pib_5y_pct, pib_10y_pct,
       reserves_sbp_usd_mn, reserves_banks_usd_mn, reserves_total_usd_mn``
    """
    import pandas as pd
    from connectors.sbp import SBPPolicyRateConnector

    conn = SBPPolicyRateConnector()
    fr = conn.fetch()
    if not fr.ok or not fr.records:
        return {"ok": False, "error": fr.error or "no records"}

    rec = fr.records[0]
    today = (rec.get("as_on")
              or datetime.now(timezone.utc).date().isoformat())

    kibor = rec.get("kibor") or {}
    tb = rec.get("tbill_yields_pct") or {}
    pib = rec.get("pib_yields_pct") or {}
    rsv = rec.get("reserves_usd_mn") or {}

    def _kibor_mid(key: str) -> float | None:
        v = kibor.get(key) or {}
        bid = v.get("bid"); offer = v.get("offer")
        if bid is None or offer is None:
            return None
        return float((bid + offer) / 2.0)

    row = {
        "date": today,
        "policy_rate_pct":      rec.get("policy_rate_pct"),
        "ceiling_pct":          rec.get("ceiling_rate_pct"),
        "floor_pct":            rec.get("floor_rate_pct"),
        "kibor_3m_pct":         _kibor_mid("3-M"),
        "kibor_6m_pct":         _kibor_mid("6-M"),
        "kibor_12m_pct":        _kibor_mid("12-M"),
        "tbill_1m_pct":         tb.get("1-M"),
        "tbill_3m_pct":         tb.get("3-M"),
        "tbill_6m_pct":         tb.get("6-M"),
        "tbill_12m_pct":        tb.get("12-M"),
        "pib_3y_pct":           pib.get("3-Y"),
        "pib_5y_pct":           pib.get("5-Y"),
        "pib_10y_pct":          pib.get("10-Y"),
        "reserves_sbp_usd_mn":   rsv.get("sbp_usd_mn"),
        "reserves_banks_usd_mn": rsv.get("banks_usd_mn"),
        "reserves_total_usd_mn": rsv.get("total_usd_mn"),
    }

    p = OUT_DIR / "sbp_rates.parquet"
    if p.exists():
        try:
            df_old = pd.read_parquet(p)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()

    df_new = pd.DataFrame([row])
    if df_old.empty:
        df_all = df_new
    else:
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    # Idempotent on date.
    df_all = (df_all
              .drop_duplicates(subset=["date"], keep="last")
              .sort_values("date")
              .reset_index(drop=True))
    df_all.to_parquet(p, index=False)
    return {"ok": True, "date": today, "rows": int(len(df_all)),
             "tbill_3m_pct": row["tbill_3m_pct"],
             "reserves_total_usd_mn": row["reserves_total_usd_mn"]}


# ---------------------------------------------------------------------------
#  KSE-100 daily close
# ---------------------------------------------------------------------------
def refresh_kse100() -> dict:
    """Append today's KSE-100 close to ``data/macro/kse100.parquet``.

    The PSXIndicesConnector scrapes the live indices table on PSX DPS;
    we extract the KSE-100 row and persist {date, current, change_pct}.
    """
    import pandas as pd
    from connectors.psx_portal import PSXIndicesConnector

    conn = PSXIndicesConnector()
    fr = conn.fetch()
    if not fr.ok or not fr.records:
        return {"ok": False, "error": fr.error or "no indices"}

    kse = next((r for r in fr.records
                if (r.get("index") or "").upper().replace(" ", "")
                   in ("KSE100", "KSE-100", "KSE100INDEX")),
                None)
    if not kse:
        # Fallback — sometimes the index name is "KSE 100 Index"
        kse = next((r for r in fr.records
                    if "KSE" in (r.get("index") or "").upper()
                    and "100" in (r.get("index") or "")), None)
    if not kse or not kse.get("current"):
        return {"ok": False,
                "error": f"KSE-100 row not found in {len(fr.records)} indices"}

    today = datetime.now(timezone.utc).date().isoformat()
    row = {
        "date": today,
        "kse100_close": float(kse["current"]),
        "kse100_change_pct": float(kse.get("change_pct") or 0.0),
        "kse100_high": float(kse.get("high") or 0.0) or None,
        "kse100_low":  float(kse.get("low")  or 0.0) or None,
    }

    p = OUT_DIR / "kse100.parquet"
    if p.exists():
        try:
            df_old = pd.read_parquet(p)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()

    df_new = pd.DataFrame([row])
    if df_old.empty:
        df_all = df_new
    else:
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    df_all = (df_all
              .drop_duplicates(subset=["date"], keep="last")
              .sort_values("date")
              .reset_index(drop=True))
    df_all.to_parquet(p, index=False)
    return {"ok": True, "date": today, "rows": int(len(df_all)),
             "kse100_close": row["kse100_close"]}


# ---------------------------------------------------------------------------
#  CPI YoY (Pakistan)
# ---------------------------------------------------------------------------
def refresh_cpi() -> dict:
    """Pull the latest Pakistan CPI YoY % and persist it.

    Sources, tried in priority order:

    1. Trading Economics ``/pakistan/inflation-cpi`` — its
       ``meta name='description'`` tag is a single English sentence such as
       *"Inflation Rate in Pakistan increased to 7.30 percent in March
       from 7 percent in February of 2026"* which is robust to extract.
    2. PBS ``/cpi`` landing page — fallback if TE is unreachable.

    CPI prints are monthly; daily reruns are no-ops once the print is
    captured. We additionally store the *period text* (e.g. ``"March"``)
    so the engine can detect when a new release lands.
    """
    import re
    import pandas as pd
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 PSX-Bot"}

    cpi_yoy: float | None = None
    period_text = ""
    source = ""

    try:
        r = requests.get(
            "https://tradingeconomics.com/pakistan/inflation-cpi",
            headers=headers, timeout=20)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            meta = soup.find("meta", attrs={"name": "description"})
            desc = meta.get("content", "") if meta else ""
            m = re.search(
                r"Inflation\s+Rate\s+in\s+Pakistan[^.]*?"
                r"(\d{1,2}(?:\.\d{1,2})?)\s*percent\s+in\s+([A-Za-z]+)",
                desc)
            if m:
                cpi_yoy = float(m.group(1))
                period_text = m.group(2)
                source = "tradingeconomics.com"
    except Exception:
        pass

    if cpi_yoy is None:
        try:
            r = requests.get("https://www.pbs.gov.pk/cpi", headers=headers,
                              timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                text = soup.get_text(" ", strip=True)
                m = re.search(
                    r"(?:CPI|Consumer\s+Price\s+Index|inflation)"
                    r"[^.]{0,200}?(\d{1,2}(?:\.\d{1,2})?)\s*(?:percent|%)",
                    text, flags=re.I)
                if m:
                    cpi_yoy = float(m.group(1))
                    source = "pbs.gov.pk"
        except Exception:
            pass

    if cpi_yoy is None:
        return {"ok": False, "error": "could not extract CPI YoY"}

    today = datetime.now(timezone.utc).date().isoformat()
    row = {
        "date": today,
        "cpi_yoy_pct": float(cpi_yoy),
        "period": period_text,
        "source": source,
    }
    p = OUT_DIR / "cpi_pakistan.parquet"
    if p.exists():
        try:
            df_old = pd.read_parquet(p)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()
    df_new = pd.DataFrame([row])
    if df_old.empty:
        df_all = df_new
    else:
        df_all = pd.concat([df_old, df_new], ignore_index=True)
    df_all = (df_all
              .drop_duplicates(subset=["date"], keep="last")
              .sort_values("date")
              .reset_index(drop=True))
    df_all.to_parquet(p, index=False)
    return {"ok": True, "date": today, "rows": int(len(df_all)),
             "cpi_yoy_pct": cpi_yoy}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-cpi", action="store_true",
                        help="Skip the CPI scrape (PBS site can be slow).")
    parser.add_argument("--skip-kse100", action="store_true",
                        help="Skip the KSE-100 pull.")
    parser.add_argument("--skip-sbp", action="store_true",
                        help="Skip the SBP rates pull.")
    args = parser.parse_args()

    out: dict = {}
    if not args.skip_sbp:
        out["sbp_rates"] = refresh_sbp_rates()
        print(f"[sbp_rates]  {json.dumps(out['sbp_rates'])}")
    if not args.skip_kse100:
        out["kse100"] = refresh_kse100()
        print(f"[kse100]     {json.dumps(out['kse100'])}")
    if not args.skip_cpi:
        out["cpi"] = refresh_cpi()
        print(f"[cpi]        {json.dumps(out['cpi'])}")

    try:
        from scripts._health import write_status
        ok_count = sum(1 for k, v in out.items()
                        if isinstance(v, dict) and v.get("ok"))
        total = len(out) or 1
        write_status(
            workflow="macro_kpis",
            ok=(ok_count == total),
            note=f"{ok_count}/{total} sub-sources refreshed",
            payload=out,
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
