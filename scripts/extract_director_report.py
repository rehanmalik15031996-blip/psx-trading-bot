"""Director's Report / Director's Review extractor.

Pakistani PSX-listed companies file Director's Reports as part of every
quarterly and annual disclosure. The narrative section ("Future Outlook",
"Director's Review", "Chairman's Statement") contains forward-looking
commentary — capex plans, demand outlook, risk factors — that no price
or news signal captures for weeks.

Most PSX filings are *image-based scanned PDFs*, so plain-text extraction
returns 0 chars. We use vision-capable LLMs to OCR + reason in a single
pass:

  * ANNUAL reports     → Claude Haiku (paid, ~Rs. 5-10 per report,
                          richer extraction)
  * QUARTERLY / HALF-YEAR / MATERIAL → GitHub Models (free tier,
                          gpt-4o-mini with vision)

Public API
----------
    extract_report(symbol, doc_id, pdf_path, doc_type, period, date)
        -> dict   (matches the schema written to data/results/reports.parquet)

    extract_universe(symbols=None, force=False) -> dict
        Walks each symbol's announcements, downloads new PDFs, runs the
        extractor, and appends results to reports.parquet. Idempotent.

Run from CLI:
    python -m scripts.extract_director_report                   # full universe
    python -m scripts.extract_director_report --symbol HUBC     # one stock
    python -m scripts.extract_director_report --force           # re-extract all
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow `python scripts/extract_director_report.py` (no -m).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Inline .env loader (same pattern as other scripts).
def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
_load_dotenv()


# Will be lazily-imported once we know we need it.
fitz: Any = None


REPORTS_DIR = ROOT / "data" / "results"
RAW_DIR = REPORTS_DIR / "raw"
REPORTS_PARQUET = REPORTS_DIR / "reports.parquet"

# Pages to send to the LLM. Director's Report lives in the first ~10
# pages of a typical Pakistani filing; sending more burns tokens for no
# extra signal AND tends to trip GitHub Models' input-size limits.
MAX_PAGES = 10
RENDER_DPI = 100  # 100 DPI = ~0.85 MP per page; clean enough for OCR
                    # while keeping each base64 page <250 KB so 10 pages
                    # fit comfortably in a vision model's input.

# LLM defaults.
ANNUAL_MODEL = "claude-haiku-4-5"
QUARTERLY_MODEL = "openai/gpt-4o-mini"
QUARTERLY_FALLBACK = "claude-haiku-4-5"  # used if no GitHub token


PROMPT = """You are extracting forward-looking management commentary from
a Pakistan Stock Exchange (PSX) listed company's regulatory filing.

Symbol:        {symbol}
Period:        {period}
Document type: {doc_type}
Filing date:   {date}

This document is the company's filing with PSX. Find the section called
"Director's Report", "Director's Review", "Chairman's Review", or
"Future Outlook" (whichever exists). It's normally near the front, after
the cover page. Read it carefully.

Then return ONLY a single JSON object with EXACTLY these fields:

{{
  "outlook_summary": "<plain-English 1-2 sentence summary of management's outlook for the next 6-12 months>",
  "outlook_tone": <number from -1.0 (very bearish guidance) to +1.0 (very bullish guidance), 0 = neutral>,
  "growth_plans": ["<concrete future plan>", ...],          // 0-5 items, each ≤ 200 chars
  "risks_mentioned": ["<concrete risk>", ...],              // 0-5 items, each ≤ 200 chars
  "guidance_strength": "LOW" | "MEDIUM" | "HIGH",           // how specific the guidance is
  "capex_announced": true | false,                          // are new capex/expansion projects mentioned
  "expansion_announced": true | false,                      // new geography / new product / capacity
  "installed_capacity": "<verbatim quote of installed/nameplate/total capacity, e.g. '1,292 MW gross', '7,000 tpd cement'> or null",
  "actual_production": "<verbatim quote of current/actual production for the period, e.g. '823 MW average dispatch', '4,200 tpd average'> or null",
  "capacity_utilization_pct": <number 0-100 if both installed_capacity and actual_production are stated AS NUMBERS in the same unit; otherwise null>,
  "new_products": ["<concrete new product or service launch in the next 12 months>", ...],   // 0-3 items, each ≤ 150 chars
  "key_financials_called_out": {{
    "revenue_growth_yoy_pct": <number or null>,
    "profit_growth_yoy_pct": <number or null>,
    "margin_direction": "expanding" | "stable" | "contracting" | "unspecified"
  }},
  "raw_excerpt": "<verbatim 200-800 char excerpt from the Director's Report you used>"
}}

Rules:
- If this document has NO narrative outlook (e.g. it's a Notice of
  Dividend or pure financial tables), set outlook_summary to "No
  narrative outlook in this filing.", outlook_tone to 0, all booleans
  to false, all lists to [], all the new capacity/product fields to
  null/[], and raw_excerpt to "".
- Use Pakistani context: "FX pressure" = rupee devaluation; "circular
  debt" common in power; "policy rate" / "CPI" = SBP context; "imported
  coal" matters for cement.
- Concrete plans only — "commission new 2 MW line by Q3 FY27" beats
  "growth opportunities exist".
- CAPACITY EXTRACTION (analyst-requested): only populate
  installed_capacity, actual_production, and capacity_utilization_pct
  when management EXPLICITLY states the numbers in the report. Do NOT
  infer or estimate from financial tables or your own knowledge — if
  the prose does not give the number, return null. Hallucinated
  capacity numbers are worse than missing ones because they would
  fool the analyst.
- capacity_utilization_pct must be a single number that you can
  compute from the two quoted figures using compatible units (e.g.
  823 MW / 1,292 MW = 64). If units differ or a number is missing,
  return null.
- new_products are forward-looking ONLY (next 12 months): a brand new
  cement variety being launched, a new bank product line, a
  pharmaceutical molecule being filed for registration, etc.
- raw_excerpt must be a true verbatim excerpt (no paraphrasing).
- Output ONLY the JSON object, no markdown fences, no preamble."""


# ---------- LLM CLIENTS ------------------------------------------------
def _claude_extract_pdf(pdf_path: Path, prompt: str) -> dict:
    """Send the PDF directly to Claude (it handles OCR internally)."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = anthropic.Anthropic(api_key=api_key)

    pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode("ascii")

    resp = client.messages.create(
        model=ANNUAL_MODEL,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": [
                {"type": "document",
                  "source": {
                      "type": "base64",
                      "media_type": "application/pdf",
                      "data": pdf_b64,
                  }},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content
                    if getattr(b, "type", "") == "text").strip()
    return _parse_json(text, model=ANNUAL_MODEL)


def _render_pdf_pages_to_png_b64(pdf_path: Path,
                                  max_pages: int = MAX_PAGES) -> list[str]:
    """Render the first N pages of a PDF as base64-PNG strings."""
    global fitz
    if fitz is None:
        import fitz as _fitz  # type: ignore
        fitz = _fitz

    doc = fitz.open(str(pdf_path))
    out: list[str] = []
    n = min(len(doc), max_pages)
    for i in range(n):
        page = doc.load_page(i)
        # zoom 1.0 = 72 DPI; 1.8 ≈ 130 DPI
        mat = fitz.Matrix(RENDER_DPI / 72.0, RENDER_DPI / 72.0)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("ascii")
        out.append(b64)
    doc.close()
    return out


def _github_extract_pdf(pdf_path: Path, prompt: str,
                          model: str = QUARTERLY_MODEL) -> dict:
    """Render PDF pages to PNG and send to a GitHub Models vision model."""
    from openai import OpenAI

    api_key = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not api_key:
        raise RuntimeError("GITHUB_TOKEN not set")
    client = OpenAI(
        base_url="https://models.github.ai/inference",
        api_key=api_key,
        default_headers={"X-GitHub-Api-Version": "2022-11-28"},
    )

    pages = _render_pdf_pages_to_png_b64(pdf_path)
    if not pages:
        return _empty_extract(model)

    content: list[dict] = [{"type": "text", "text": prompt}]
    for b64 in pages:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        max_tokens=1500,
        temperature=0.1,
    )
    text = (resp.choices[0].message.content or "").strip()
    return _parse_json(text, model=model)


def _parse_json(text: str, model: str) -> dict:
    """Parse a JSON object from an LLM response. Strips ```json fences
    and tries to recover from trailing commentary."""
    t = text.strip()
    if t.startswith("```"):
        # Strip ```json or ``` opening
        first_nl = t.find("\n")
        if first_nl > 0:
            t = t[first_nl + 1:]
        if t.endswith("```"):
            t = t[: -3].strip()

    # Find the first { ... } block (greedy, allowing nested).
    try:
        return _augment(json.loads(t), model)
    except json.JSONDecodeError:
        pass

    # Fall back: find first '{' and last '}'.
    s = t.find("{")
    e = t.rfind("}")
    if s >= 0 and e > s:
        try:
            return _augment(json.loads(t[s: e + 1]), model)
        except json.JSONDecodeError:
            pass

    # Final fallback: empty extraction.
    out = _empty_extract(model)
    out["error"] = f"could not parse LLM JSON: {text[:200]!r}"
    return out


def _empty_extract(model: str) -> dict:
    return {
        "outlook_summary": "Extraction failed.",
        "outlook_tone": 0.0,
        "growth_plans": [],
        "risks_mentioned": [],
        "guidance_strength": "LOW",
        "capex_announced": False,
        "expansion_announced": False,
        "installed_capacity": None,
        "actual_production": None,
        "capacity_utilization_pct": None,
        "new_products": [],
        "key_financials_called_out": {
            "revenue_growth_yoy_pct": None,
            "profit_growth_yoy_pct": None,
            "margin_direction": "unspecified",
        },
        "raw_excerpt": "",
        "extracted_by_model": model,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def _augment(obj: dict, model: str) -> dict:
    """Add audit fields to whatever the LLM returned."""
    obj.setdefault("outlook_summary", "")
    obj.setdefault("outlook_tone", 0.0)
    obj.setdefault("growth_plans", [])
    obj.setdefault("risks_mentioned", [])
    obj.setdefault("guidance_strength", "LOW")
    obj.setdefault("capex_announced", False)
    obj.setdefault("expansion_announced", False)
    obj.setdefault("installed_capacity", None)
    obj.setdefault("actual_production", None)
    obj.setdefault("capacity_utilization_pct", None)
    obj.setdefault("new_products", [])
    obj.setdefault("key_financials_called_out", {
        "revenue_growth_yoy_pct": None,
        "profit_growth_yoy_pct": None,
        "margin_direction": "unspecified",
    })
    obj.setdefault("raw_excerpt", "")
    obj["extracted_by_model"] = model
    obj["extracted_at"] = datetime.now(timezone.utc).isoformat()
    # Sanitize numeric fields.
    try:
        obj["outlook_tone"] = float(obj.get("outlook_tone") or 0.0)
        obj["outlook_tone"] = max(-1.0, min(1.0, obj["outlook_tone"]))
    except (TypeError, ValueError):
        obj["outlook_tone"] = 0.0
    # Sanitize capacity utilization — strict 0-100 numeric or None.
    cu = obj.get("capacity_utilization_pct")
    if cu is None or (isinstance(cu, str) and cu.strip().lower() in
                       ("", "null", "none", "n/a")):
        obj["capacity_utilization_pct"] = None
    else:
        try:
            cu_f = float(cu)
            if 0.0 <= cu_f <= 100.0:
                obj["capacity_utilization_pct"] = round(cu_f, 1)
            else:
                obj["capacity_utilization_pct"] = None
        except (TypeError, ValueError):
            obj["capacity_utilization_pct"] = None
    # Coerce string fields to plain str | None
    for k in ("installed_capacity", "actual_production"):
        v = obj.get(k)
        if v is None:
            continue
        if not isinstance(v, str):
            obj[k] = str(v)
        s = obj[k].strip()
        if s.lower() in ("", "null", "none", "n/a", "not stated",
                          "not disclosed"):
            obj[k] = None
    # new_products must be a list[str], capped
    nps = obj.get("new_products") or []
    if not isinstance(nps, list):
        nps = []
    obj["new_products"] = [str(x)[:150] for x in nps][:3]
    return obj


# ---------- ROUTER ----------------------------------------------------
def extract_report(symbol: str, doc_id: str, pdf_path: Path,
                    doc_type: str, period: str, date: str) -> dict:
    """Extract structured outlook from one filing PDF.

    Routes ANNUAL → Claude (paid, richer), everything else → GitHub
    Models free tier with vision. Falls back across providers if one is
    missing creds.
    """
    prompt = PROMPT.format(
        symbol=symbol, period=period or "—",
        doc_type=doc_type, date=date,
    )

    has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_github = bool(os.environ.get("GITHUB_TOKEN")
                       or os.environ.get("GH_TOKEN"))

    target = (ANNUAL_MODEL if doc_type == "ANNUAL"
              else QUARTERLY_MODEL)

    # Routing with graceful fallback. We always try the cheaper option
    # first (per doc type) and fall through to Claude if it fails — that
    # way a transient GitHub Models 5xx or oversized input doesn't leave
    # us with an empty record.
    extract: dict = {}
    last_err: str | None = None

    def _try(label: str, fn) -> bool:
        nonlocal extract, last_err
        try:
            extract = fn()
            # Only treat a successful extraction (non-empty summary +
            # no error field) as a final answer.
            if (extract.get("outlook_summary")
                and extract.get("outlook_summary") != "Extraction failed."
                and not extract.get("error")):
                return True
            last_err = extract.get("error") or "empty extraction"
            return False
        except Exception as e:
            last_err = f"{label}: {type(e).__name__}: {e}"
            return False

    succeeded = False
    if doc_type == "ANNUAL" and has_anthropic:
        succeeded = _try("claude",
                          lambda: _claude_extract_pdf(pdf_path, prompt))
    if not succeeded and has_github:
        succeeded = _try("github",
                          lambda: _github_extract_pdf(
                              pdf_path, prompt, model=target))
    if not succeeded and has_anthropic:
        succeeded = _try("claude-fallback",
                          lambda: _claude_extract_pdf(pdf_path, prompt))

    if not succeeded:
        extract = _empty_extract(target)
        extract["error"] = (
            last_err or "no LLM provider available "
            "(set ANTHROPIC_API_KEY or GITHUB_TOKEN)"
        )

    # Compose the persisted record.
    return {
        "symbol": symbol,
        "doc_id": str(doc_id),
        "filing_date": date,
        "doc_type": doc_type,
        "fy_period": period,
        "pdf_path": str(pdf_path.relative_to(ROOT)).replace("\\", "/"),
        **extract,
    }


# ---------- BULK PIPELINE ---------------------------------------------
def extract_universe(symbols: list[str] | None = None,
                      force: bool = False,
                      types_to_keep: tuple[str, ...] = (
                          "ANNUAL", "HALF_YEAR", "QUARTERLY"
                      ),
                      max_per_symbol: int = 4,
                      ) -> dict[str, Any]:
    """Walk announcements for each symbol, fetch new PDFs, extract, persist.

    Only the most recent `max_per_symbol` filings of `types_to_keep` are
    extracted per stock. Set `force=True` to re-extract even if the doc
    is already in the parquet.
    """
    from connectors.psx_results import PSXResultsConnector
    from config.universe import symbols as universe_symbols
    import pandas as pd

    syms = symbols or universe_symbols()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    existing_ids: set[str] = set()
    if REPORTS_PARQUET.exists() and not force:
        try:
            df_old = pd.read_parquet(REPORTS_PARQUET)
            existing_ids = set(df_old["doc_id"].astype(str).tolist())
        except Exception:
            df_old = None
    else:
        df_old = None

    conn = PSXResultsConnector()
    fresh_records: list[dict] = []
    errors: list[str] = []
    counts: dict[str, int] = {}

    for sym in syms:
        try:
            anns = conn.fetch_announcements(sym)
        except Exception as e:
            errors.append(f"{sym}: announcements: {type(e).__name__}: {e}")
            counts[sym] = 0
            continue

        # Keep only target types, newest first, capped.
        eligible = [a for a in anns if a["type"] in types_to_keep][:max_per_symbol]
        new_for_sym = 0
        for a in eligible:
            if str(a["doc_id"]) in existing_ids:
                continue
            pdf_dest = (RAW_DIR / sym
                        / f"{a['date']}_{a['doc_id']}.pdf")
            try:
                conn.download_pdf(a["doc_id"], pdf_dest)
            except Exception as e:
                errors.append(
                    f"{sym}/{a['doc_id']}: download: "
                    f"{type(e).__name__}: {e}"
                )
                continue

            t0 = time.time()
            try:
                rec = extract_report(
                    symbol=sym,
                    doc_id=a["doc_id"],
                    pdf_path=pdf_dest,
                    doc_type=a["type"],
                    period=a["fy_period"],
                    date=a["date"],
                )
                rec["title"] = a["title"]
                rec["pdf_url"] = a["pdf_url"]
                rec["extraction_seconds"] = round(time.time() - t0, 1)
                fresh_records.append(rec)
                new_for_sym += 1
                print(f"  + {sym} {a['date']} {a['type']:9s} "
                      f"{a['fy_period']:9s} → "
                      f"tone={rec.get('outlook_tone'):+.2f} "
                      f"({rec.get('extraction_seconds', 0)}s)")
            except Exception as e:
                errors.append(
                    f"{sym}/{a['doc_id']}: extract: "
                    f"{type(e).__name__}: {e}")
        counts[sym] = new_for_sym

    # Persist.
    if fresh_records:
        df_new = pd.DataFrame(fresh_records)
        if df_old is not None and not df_old.empty:
            df_all = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df_all = df_new
        # Dedupe by (symbol, doc_id) keeping the newest extraction.
        df_all = (df_all.sort_values("extracted_at")
                         .drop_duplicates(subset=["symbol", "doc_id"],
                                            keep="last"))
        df_all.to_parquet(REPORTS_PARQUET, index=False)

    return {
        "ok": True,
        "new_records": len(fresh_records),
        "per_symbol": counts,
        "errors": errors,
        "total_in_store": (
            len(df_old) + len(fresh_records)
            if df_old is not None else len(fresh_records)
        ),
    }


# ---------- CLI -------------------------------------------------------
def _cli() -> int:
    p = argparse.ArgumentParser(
        description="Extract forward-looking management commentary from "
                    "PSX filings.")
    p.add_argument("--symbol", action="append", default=None,
                    help="Limit to this symbol (repeatable).")
    p.add_argument("--force", action="store_true",
                    help="Re-extract even if doc already in parquet.")
    p.add_argument("--max-per-symbol", type=int, default=4,
                    help="Max number of recent filings per symbol "
                         "(default: 4).")
    args = p.parse_args()

    print(f"Extracting director's reports — symbols={args.symbol or 'ALL'} "
          f"force={args.force} max_per_symbol={args.max_per_symbol}")
    res = extract_universe(symbols=args.symbol, force=args.force,
                            max_per_symbol=args.max_per_symbol)
    print()
    print(f"new records: {res['new_records']}")
    print(f"total in store: {res['total_in_store']}")
    print(f"errors ({len(res['errors'])}):")
    for e in res["errors"][:20]:
        print(f"  - {e}")

    try:
        from scripts._health import write_status
        write_status(
            workflow="financial_results",
            ok=bool(res.get("ok")),
            note=(f"new={res.get('new_records', 0)} "
                  f"total={res.get('total_in_store', 0)} "
                  f"errors={len(res.get('errors') or [])}"),
            payload={
                "new":   int(res.get("new_records") or 0),
                "total": int(res.get("total_in_store") or 0),
                "errors": list((res.get("errors") or [])[:20]),
            },
        )
    except Exception as e:
        print(f"  WARN: _health.write_status failed: "
              f"{type(e).__name__}: {e}")

    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
