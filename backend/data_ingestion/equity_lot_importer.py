"""Import equity tax lots from brokerage/stock-plan PDF exports.

Supports Charles Schwab cost-basis lot-detail PDFs and Morgan Stanley at Work / E*TRADE
stock-plan statements (ESPP + RS sections). Uses fast deterministic parsers when the format
is recognized; falls back to gpt-4o-mini JSON extraction for unknown layouts.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Optional

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from app.core.llm_cost import record_usage
from app.database.models import EquityLot

_ROW_RE = re.compile(
    r"^(\d+)\s+\$([\d.]+)\s+\$([\d.]+)\s+\$[\d,.]+\s+\$[\d,.]+\s+[+-]?\$[\d,.]+\s+[+-]?[\d.]+%\s+"
    r"(Short Term|Long Term)\s*$",
    re.MULTILINE,
)
_DATE_RE = re.compile(r"(\d{2}/\d{2}/\d{4})")
_ESPP_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s+\$([\d.]+)\s+(\d+)\s+(\d+)")
_RS_RE = re.compile(r"(\d{2}/\d{2}/\d{4})(\d+)\s+(\d+)\s+(\d+)\s+\$")


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def detect_format(text: str, filename: str = "") -> str:
    low = (text + " " + filename).lower()
    if "lot details:" in low and "cost basis calculator" in low:
        return "schwab_lot_details"
    if "stock plan (" in low or "employee stock purchase plan" in low or "restricted stock" in low:
        return "etrade_stock_plan"
    if "charles schwab" in low:
        return "schwab_lot_details"
    return "unknown"


def _mdy_to_iso(s: str) -> str:
    dt = datetime.strptime(s.strip(), "%m/%d/%Y")
    return dt.strftime("%Y-%m-%d")


def _norm_shares(v: float) -> float:
    return round(float(v), 4)


def _norm_price(v: float) -> float:
    return round(float(v), 4)


def lot_fingerprint(lot: dict) -> tuple:
    return (
        (lot.get("ticker") or "").upper(),
        (lot.get("account_label") or "").strip().lower(),
        lot.get("lot_type") or "other",
        _norm_shares(lot["shares"]),
        _norm_price(lot["cost_basis_per_share"]),
        lot["acquisition_date"],
    )


def parse_schwab_lot_details(text: str, account_label: str = "Charles Schwab") -> dict:
    """Parse Schwab cost-basis lot export. Open dates are often in a footer column."""
    m = re.search(r"Lot Details:\s*([A-Z]{1,5})\b", text, re.I)
    ticker = (m.group(1) if m else "").upper()
    rows = list(_ROW_RE.finditer(text))
    if not rows:
        return {"ticker": ticker, "lots": [], "warnings": ["No Schwab lot rows found."]}

    dates: list[str] = []
    if "Open Date" in text:
        tail = text.split("Open Date", 1)[1]
        dates = _DATE_RE.findall(tail)
    if len(dates) < len(rows):
        return {
            "ticker": ticker,
            "lots": [],
            "warnings": [f"Found {len(rows)} lot rows but only {len(dates)} open dates — try LLM import."],
        }

    lots = []
    for i, row in enumerate(rows):
        shares = float(row.group(1))
        cost_basis = float(row.group(3))
        lots.append({
            "ticker": ticker,
            "account_label": account_label,
            "lot_type": "rsu",
            "shares": shares,
            "cost_basis_per_share": cost_basis,
            "acquisition_date": _mdy_to_iso(dates[i]),
        })
    return {"ticker": ticker, "lots": lots, "warnings": []}


def parse_etrade_stock_plan(text: str, account_label: str = "E*TRADE Stock Plan") -> dict:
    """Parse Morgan Stanley at Work / E*TRADE stock-plan PDF (ESPP purchases + RS grants)."""
    ticker = ""
    m = re.search(r"Stock Plan\s*\(([A-Z]{1,5})\)", text, re.I)
    if m:
        ticker = m.group(1).upper()
    if not ticker:
        m2 = re.search(r"\b([A-Z]{2,5})\s+\$\d+\.\d{2}", text)
        ticker = (m2.group(1) if m2 else "").upper()

    lots: list[dict] = []
    warnings: list[str] = []

    for m in _ESPP_RE.finditer(text):
        acq, price, _purchased, sellable = m.group(1), float(m.group(2)), int(m.group(3)), int(m.group(4))
        if sellable <= 0:
            continue
        lots.append({
            "ticker": ticker,
            "account_label": account_label,
            "lot_type": "espp",
            "shares": float(sellable),
            "cost_basis_per_share": price,
            "acquisition_date": _mdy_to_iso(acq),
        })

    rs_skipped = 0
    for m in _RS_RE.finditer(text):
        sellable = int(m.group(4))
        if sellable <= 0:
            continue
        rs_skipped += 1

    if rs_skipped:
        warnings.append(
            f"Skipped {rs_skipped} restricted-stock grant row(s) — this export has no per-lot cost basis. "
            "Download the Cost Basis report from your stock-plan portal or enter RSU lots manually."
        )

    return {"ticker": ticker, "lots": lots, "warnings": warnings}


_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "format_detected": {"type": "string"},
        "ticker": {"type": "string"},
        "account_label": {"type": "string"},
        "lots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "account_label": {"type": "string"},
                    "lot_type": {"type": "string", "enum": ["rsu", "espp", "other"]},
                    "shares": {"type": "number"},
                    "cost_basis_per_share": {"type": "number"},
                    "acquisition_date": {"type": "string"},
                },
                "required": ["ticker", "lot_type", "shares", "cost_basis_per_share", "acquisition_date"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["format_detected", "lots", "warnings"],
}


def parse_with_llm(text: str, filename: str = "") -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set — required for LLM PDF import of unknown formats.")

    import requests

    clipped = text[:28000]
    prompt = (
        "Extract employee equity tax lots from this PDF text export for a personal tax-aware portfolio tool.\n\n"
        "Known formats:\n"
        "1) Charles Schwab 'Lot Details' — each row: quantity, current price, cost/share, …, holding period. "
        "Open/acquisition dates may appear in a separate footer column in the same order as rows.\n"
        "2) E*TRADE / Morgan Stanley at Work stock plan — ESPP: purchase date, purchase price, purchased qty, "
        "sellable qty. RS/RSU grants often lack cost basis; omit those unless cost basis is explicitly shown.\n\n"
        "Rules:\n"
        "- Return ONLY currently held lots (skip zero sellable / fully sold ESPP rows).\n"
        "- lot_type: rsu for RSU/restricted stock vest lots, espp for ESPP purchases, other otherwise.\n"
        "- acquisition_date as YYYY-MM-DD.\n"
        "- cost_basis_per_share must be present and > 0 for every lot; omit lots without a reliable basis.\n"
        "- Infer ticker and account_label (e.g. Charles Schwab, E*TRADE Stock Plan) from headers.\n"
        "- Add human-readable warnings for anything skipped or ambiguous.\n\n"
        f"Filename: {filename or '(unknown)'}\n\n"
        f"PDF text:\n{clipped}\n\n"
        f"Return JSON matching this schema:\n{json.dumps(_LLM_SCHEMA)}"
    )
    body = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=180)
    r.raise_for_status()
    j = r.json()
    u = j.get("usage") or {}
    record_usage(
        "equity_lot_import",
        OPENAI_MODEL,
        u.get("prompt_tokens", 0),
        u.get("completion_tokens", 0),
        provider="openai",
    )
    parsed = json.loads(j["choices"][0]["message"]["content"])
    return {
        "format": parsed.get("format_detected", "llm"),
        "ticker": parsed.get("ticker", ""),
        "lots": parsed.get("lots") or [],
        "warnings": parsed.get("warnings") or [],
        "account_label": parsed.get("account_label", ""),
        "llm": True,
    }


def _validate_lot(raw: dict, default_ticker: str = "", default_account: str = "") -> Optional[dict]:
    try:
        ticker = (raw.get("ticker") or default_ticker or "").upper().strip()
        acq = str(raw.get("acquisition_date", "")).strip()
        if "/" in acq:
            acq = _mdy_to_iso(acq)
        shares = float(raw["shares"])
        basis = float(raw["cost_basis_per_share"])
        lot_type = (raw.get("lot_type") or "other").lower()
        if lot_type not in ("rsu", "espp", "other"):
            lot_type = "other"
        if not ticker or shares <= 0 or basis <= 0 or not re.match(r"\d{4}-\d{2}-\d{2}$", acq):
            return None
        return {
            "ticker": ticker,
            "account_label": (raw.get("account_label") or default_account or "").strip() or None,
            "lot_type": lot_type,
            "shares": shares,
            "cost_basis_per_share": basis,
            "acquisition_date": acq,
        }
    except (KeyError, TypeError, ValueError):
        return None


def parse_equity_lot_pdf(data: bytes, filename: str = "", force_llm: bool = False) -> dict:
    """Extract lots from PDF bytes. Uses deterministic parsers first, LLM when needed."""
    text = extract_pdf_text(data)
    if not text.strip():
        return {"format": "empty", "lots": [], "warnings": ["PDF contained no extractable text."]}

    fmt = detect_format(text, filename)
    result: dict[str, Any] = {"format": fmt, "lots": [], "warnings": [], "text_chars": len(text)}

    if not force_llm:
        if fmt == "schwab_lot_details":
            account = "Charles Schwab" if "schwab" in filename.lower() else "Charles Schwab"
            parsed = parse_schwab_lot_details(text, account_label=account)
            result.update(parsed)
            result["format"] = fmt
        elif fmt == "etrade_stock_plan":
            parsed = parse_etrade_stock_plan(text)
            result.update(parsed)
            result["format"] = fmt

    need_llm = force_llm or not result.get("lots") or (
        fmt == "unknown" and OPENAI_API_KEY
    )
    if need_llm and OPENAI_API_KEY:
        llm = parse_with_llm(text, filename)
        if llm.get("lots"):
            result["lots"] = llm["lots"]
            result["format"] = llm.get("format", "llm")
            result["llm"] = True
        result["warnings"] = list(dict.fromkeys((result.get("warnings") or []) + (llm.get("warnings") or [])))
        if llm.get("ticker") and not result.get("ticker"):
            result["ticker"] = llm["ticker"]
    elif need_llm and not OPENAI_API_KEY:
        result["warnings"].append("Set OPENAI_API_KEY for LLM import of unrecognized PDF layouts.")

    default_ticker = result.get("ticker") or ""
    default_account = ""
    if result.get("lots") and isinstance(result["lots"][0], dict):
        default_account = result["lots"][0].get("account_label") or ""
    validated = []
    for raw in result.get("lots") or []:
        lot = _validate_lot(raw, default_ticker=default_ticker, default_account=default_account)
        if lot:
            validated.append(lot)
    result["lots"] = validated
    return result


def dedupe_against_existing(parsed_lots: list[dict], existing: list[EquityLot]) -> tuple[list[dict], list[dict]]:
    """Split parsed lots into new vs already present (exact fingerprint match)."""
    existing_keys = {lot_fingerprint(_lot_dict(row)) for row in existing}
    new, skipped = [], []
    for lot in parsed_lots:
        key = lot_fingerprint(lot)
        if key in existing_keys:
            skipped.append(lot)
        else:
            new.append(lot)
            existing_keys.add(key)
    return new, skipped


def _lot_dict(row: EquityLot) -> dict:
    return {
        "ticker": row.ticker,
        "account_label": row.account_label,
        "lot_type": row.lot_type,
        "shares": row.shares,
        "cost_basis_per_share": row.cost_basis_per_share,
        "acquisition_date": row.acquisition_date,
    }


def ingest_parsed_lots(
    db,
    parsed_lots: list[dict],
    *,
    source_filename: str = "",
    file_hash: str = "",
    replace_ticker_account: bool = False,
) -> dict:
    """Insert deduped lots into equity_lots. Optionally replace all lots for ticker+account first."""
    from app.database import ExternalAccount
    all_lots = db.query(EquityLot).all()
    external_labels = {acct.account_label for acct in db.query(ExternalAccount).all()}
    existing = [l for l in all_lots if l.account_label not in external_labels]
    new_lots, skipped = dedupe_against_existing(parsed_lots, existing)

    removed = 0
    if replace_ticker_account and new_lots:
        pairs = {(l["ticker"], l.get("account_label") or "") for l in parsed_lots}
        for ticker, acct in pairs:
            q = db.query(EquityLot).filter(EquityLot.ticker == ticker)
            if acct:
                q = q.filter(EquityLot.account_label == acct)
            else:
                q = q.filter((EquityLot.account_label == None) | (EquityLot.account_label == ""))  # noqa: E711
            for row in q.all():
                db.delete(row)
                removed += 1

    now = datetime.now().isoformat(timespec="seconds")
    note = f"Imported from PDF: {source_filename}" if source_filename else "Imported from PDF"
    if file_hash:
        note += f" [{file_hash[:12]}]"

    inserted = []
    for lot in new_lots:
        row = EquityLot(
            ticker=lot["ticker"],
            account_label=lot.get("account_label"),
            lot_type=lot["lot_type"],
            shares=float(lot["shares"]),
            cost_basis_per_share=float(lot["cost_basis_per_share"]),
            acquisition_date=lot["acquisition_date"],
            notes=note,
            created_at=now,
        )
        db.add(row)
        inserted.append(lot)
    db.commit()

    tickers = sorted({l["ticker"] for l in parsed_lots})
    total_shares = sum(l["shares"] for l in inserted)
    return {
        "inserted": len(inserted),
        "skipped_duplicates": len(skipped),
        "removed_replaced": removed,
        "tickers": tickers,
        "total_shares_inserted": total_shares,
        "lots_preview": inserted[:5],
    }


def import_equity_lot_pdf(
    db,
    data: bytes,
    filename: str = "",
    *,
    force_llm: bool = False,
    replace_ticker_account: bool = False,
) -> dict:
    """End-to-end: parse PDF, dedupe, insert."""
    file_hash = hashlib.sha256(data).hexdigest()
    parsed = parse_equity_lot_pdf(data, filename=filename, force_llm=force_llm)
    ingest = ingest_parsed_lots(
        db,
        parsed.get("lots") or [],
        source_filename=filename,
        file_hash=file_hash,
        replace_ticker_account=replace_ticker_account,
    )
    return {
        "format": parsed.get("format"),
        "warnings": parsed.get("warnings") or [],
        "parsed_count": len(parsed.get("lots") or []),
        "llm_used": bool(parsed.get("llm")),
        "file_hash": file_hash,
        **ingest,
    }
