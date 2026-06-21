"""Import external broker statements/confirmations from Vanguard and Robinhood.

Supports extracting positions (with tax lots) and transaction histories.
Uses deterministic regex parsers first; falls back to LLM JSON extraction (OpenAI or local Ollama).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Optional

import requests
from app.core.config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL,
    OLLAMA_URL, LLM_MODEL
)
from app.core.llm_cost import record_usage
from app.database.models import EquityLot, ExternalAccount, ExternalTransaction

# Robinhood Positions Regex: Ticker, Qty, Price, Average Cost
# e.g., "AAPL Apple Inc. 10.000000 Shares $182.30 Average Cost $165.20"
_RH_POS_RE = re.compile(
    r"\b([A-Z]{1,5})\b.*?\b([\d,]+\.\d+)\s+Shares.*?\bAverage Cost\s+\$?([\d,]+\.\d+)",
    re.IGNORECASE
)

# Robinhood Positions without Average Cost:
# e.g. "AAPL Margin 36 $312.06000 $11,234.16" or "BRK.B Margin 48 $474.48000 $22,775.04"
_RH_POS_NEW_RE = re.compile(
    r"\b([A-Z]{1,5}(?:\.[A-Z]{1,5})?)\s+(Margin|Cash|Short)\s+([\d,]+(?:\.\d+)?)\s+\$?([\d,]+\.\d+)\s+\$?([\d,]+\.\d+)",
    re.IGNORECASE
)

# Robinhood Transactions Regex: Date, Side (Buy/Sell), Ticker, Qty, Price
# e.g., "06/12/2026 Buy AAPL 5.000000 Shares at $175.00 Executed"
_RH_TX_RE = re.compile(
    r"\b(\d{2}/\d{2}/\d{4})\b.*?\b(Buy|Sell|Bought|Sold|Market Buy|Market Sell)\b.*?\b([A-Z]{1,5})\b.*?\b([\d,]+\.\d+)\s+Shares\s+(?:at|@)\s+\$?([\d,]+\.\d+)",
    re.IGNORECASE
)

# Robinhood Transactions without Shares keyword:
# e.g. "KDK Margin Buy 05/07/2026 100 $6.13000 $613.00"
_RH_TX_NEW_RE = re.compile(
    r"\b([A-Z]{1,5}(?:\.[A-Z]{1,5})?)\s+(Margin|Cash|Short)\s+(Buy|Sell)\s+(\d{2}/\d{2}/\d{4})\s+([\d,]+(?:\.\d+)?)\s+\$?([\d,]+\.\d+)",
    re.IGNORECASE
)

# Vanguard Positions Regex: e.g. "Apple Inc. (AAPL) 100.0000 $175.50 $17,550.00"
# or "AAPL Acquisition Date: 05/12/2025 Shares: 50.000 Cost basis per share: $145.00"
_VG_POS_LOT_RE = re.compile(
    r"\b([A-Z]{1,5})\b.*?\bAcquisition Date:\s*(\d{2}/\d{2}/\d{4})\b.*?\bShares:\s*([\d,]+\.\d+)\b.*?\b(?:Cost basis per share|Cost basis):\s*\$?([\d,]+\.\d+)",
    re.IGNORECASE
)
_VG_POS_LINE_RE = re.compile(
    r"\b([A-Z]{1,5})\b.*?\b([\d,]+\.\d+)\s+\$?[\d,]+\.\d+\s+\$?[\d,]+\.\d+\s+\$?([\d,]+\.\d+)",
    re.IGNORECASE
)

# Vanguard Transactions Regex: Date, Side, Ticker, Qty, Price
# e.g., "06/10/2026 Buy AAPL 10.0000 $180.00"
_VG_TX_RE = re.compile(
    r"\b(\d{2}/\d{2}/\d{4})\b\s+\b(Buy|Sell|Bought|Sold)\b\s+\b([A-Z]{1,5})\b\s+([\d,]+\.\d+)\s+\$?([\d,]+\.\d+)",
    re.IGNORECASE
)

# Vanguard Statement Summary Regex:
_VG_POS_BLOCK_RE = re.compile(
    r"\b([A-Z]{3,7})\b\s*"
    r"([-+]?\$?[\d,]+\.\d{2})\s*"
    r"(\$?[\d,]+\.\d{2})\s*"
    r"([\d,]+\.\d{4})\s*"
    r"([$ \d,.-]*?\.\d{2,4}[$ \d,.-]*?\.\d{2}[$ \d,.-]*?\.\d{2})",
    re.IGNORECASE
)

# Vanguard Statement Transactions Regex: Date, Symbol, Name, Type, Account, Qty, Price, Comm, Amount
_VG_TX_STATEMENT_RE = re.compile(
    r"(\d{2}/\d{2})\s*"
    r"(\d{2}/\d{2})\s*"
    r"([A-Z]{3,5}|-)\s*"
    r"(.*?)\s*"
    r"(Dividend|Reinvestment|Stock Split|ADR Custody Fee|Sweep out|Sweep in)\s*"
    r"(Cash|-)?\s*"
    r"([\d,]+\.\d{4}|-)?\s*"
    r"([\d,]+\.\d{2,4}|-)?\s*"
    r"(-)?\s*"
    r"(-?\$?[\d,]+\.\d{2})",
    re.IGNORECASE
)

KNOWN_VANGUARD_TICKERS = {
    "VASGX", "VFIAX", "VSMGX", "VTSAX",
    "VUG", "VXUS", "VOO", "VTI", "PALL", "QQQ", "IAU", "IWF", "GLD",
    "BABA", "AAPL", "XYZ", "ISRG", "META", "MSFT", "NFLX", "NVDA", "PYPL", "HOOD", "ROKU", "SHOP"
}


def extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def detect_broker_and_type(text: str, filename: str = "") -> tuple[str, str]:
    low = (text + " " + filename).lower()
    broker = "Unknown"

    is_vanguard = "vanguard" in low
    is_robinhood = "robinhood" in low

    if is_vanguard and is_robinhood:
        if low.count("vanguard") > low.count("robinhood"):
            is_robinhood = False
        else:
            is_vanguard = False

    # 2. Extract Account Number if present
    account_number = None
    if is_robinhood:
        rh_acct_match = re.search(r"account\s*#:\s*(\d+)", low)
        if rh_acct_match:
            account_number = rh_acct_match.group(1)
    elif is_vanguard:
        vg_acct_match = re.search(r"account\s*number:\s*([\d-]+)", low)
        if not vg_acct_match:
            vg_acct_match = re.search(r"account\s*#\s*([\d-]+)", low)
        if not vg_acct_match:
            vg_acct_match = re.search(r"brokerage\s*account\s*#\s*([\d-]+)", low)
        if not vg_acct_match:
            vg_acct_match = re.search(r"account\s*[-–—]\s*(?:xxxx)?(\d+)", low)
        if vg_acct_match:
            account_number = vg_acct_match.group(1)

    # 3. Detect Account Type
    is_joint = any(k in low for k in ["joint tenancy", "joint account", "survivorship", "joint"])

    if is_robinhood:
        if is_joint:
            acct_type = "Joint"
        else:
            acct_type = "Individual"
        if account_number:
            broker = f"Robinhood {acct_type} ({account_number})"
        else:
            broker = "Robinhood Joint" if is_joint else "Robinhood"
    elif is_vanguard:
        if is_joint:
            acct_type = "Joint"
        elif "traditional ira" in low or "trad ira" in low:
            acct_type = "IRA"
        elif "roth ira" in low:
            acct_type = "Roth IRA"
        else:
            acct_type = "Individual"
        if account_number:
            broker = f"Vanguard {acct_type} ({account_number})"
        else:
            broker = "Vanguard Joint" if is_joint else "Vanguard"

    # A monthly statement is classified as 'positions' if it lists held assets,
    # even if it also contains transactions.
    doc_type = "transactions"
    if any(k in low for k in ["portfolio summary", "securities held", "positions held", "holdings", "position summary"]):
        doc_type = "positions"
    elif not any(k in low for k in ["transaction", "activity", "trade confirmation", "bought", "sold"]):
        doc_type = "positions"

    return broker, doc_type


def _mdy_to_iso(s: str) -> str:
    dt = datetime.strptime(s.strip(), "%m/%d/%Y")
    return dt.strftime("%Y-%m-%d")


def _extract_statement_date(text: str) -> str:
    """Best-effort statement period-end date for the anchor (so later CSV trades roll forward from
    it). Falls back to today if no date is found."""
    # "... - June 30, 2026" / "Statement Period ... December 31, 2025"
    m = re.search(r"(?:through|to|[-–])\s*([A-Z][a-z]+ \d{1,2},\s*\d{4})", text)
    if not m:
        m = re.search(r"([A-Z][a-z]+ \d{1,2},\s*\d{4})", text)
    if m:
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(m.group(1).strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", text)
    if m:
        try:
            return _mdy_to_iso(m.group(1))
        except ValueError:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def parse_robinhood_cash(text: str) -> Optional[float]:
    brokerage_match = re.search(
        r"Brokerage Cash Balance\s*\*?\s*\$?([\d,]+\.\d+)\s+\$?([\d,]+\.\d+)",
        text, re.IGNORECASE
    )
    sweep_match = re.search(
        r"Deposit Sweep Balance\s*\*?\s*\$?([\d,]+\.\d+)\s+\$?([\d,]+\.\d+)",
        text, re.IGNORECASE
    )

    total_cash = 0.0
    found = False
    if brokerage_match:
        total_cash += float(brokerage_match.group(2).replace(",", ""))
        found = True
    if sweep_match:
        total_cash += float(sweep_match.group(2).replace(",", ""))
        found = True

    return total_cash if found else None


def parse_robinhood_positions(text: str) -> list[dict]:
    lots = []
    # 1. Try to find matching position rows with average cost
    for m in _RH_POS_RE.finditer(text):
        ticker = m.group(1).upper()
        shares = float(m.group(2).replace(",", ""))
        avg_cost = float(m.group(3).replace(",", ""))
        lots.append({
            "ticker": ticker,
            "account_label": "Robinhood",
            "lot_type": "other",
            "shares": shares,
            "cost_basis_per_share": avg_cost,
            "acquisition_date": datetime.now().strftime("%Y-%m-%d"), # statement date proxy
            "notes": "Parsed from Robinhood Statement Positions"
        })

    # 2. Try the new regex format if no lots or to catch additional tickers
    for m in _RH_POS_NEW_RE.finditer(text):
        ticker = m.group(1).upper()
        shares = float(m.group(3).replace(",", ""))
        price = float(m.group(4).replace(",", ""))

        # Deduplicate with the old regex if they match the same ticker and shares
        if any(l["ticker"] == ticker and abs(l["shares"] - shares) < 1e-4 for l in lots):
            continue

        lots.append({
            "ticker": ticker,
            "account_label": "Robinhood",
            "lot_type": "other",
            "shares": shares,
            "cost_basis_per_share": price,  # Default to current price as cost basis
            "acquisition_date": datetime.now().strftime("%Y-%m-%d"), # statement date proxy
            "notes": "Parsed from Robinhood Statement Positions (Price used as cost basis)"
        })
    return lots


def parse_robinhood_transactions(text: str) -> list[dict]:
    txs = []
    # 1. Try old format
    for m in _RH_TX_RE.finditer(text):
        date_str = _mdy_to_iso(m.group(1))
        side = "BUY" if "buy" in m.group(2).lower() else "SELL"
        ticker = m.group(3).upper()
        shares = float(m.group(4).replace(",", ""))
        price = float(m.group(5).replace(",", ""))
        txs.append({
            "date": date_str,
            "ticker": ticker,
            "side": side,
            "qty": shares,
            "price": price
        })

    # 2. Try new format
    for m in _RH_TX_NEW_RE.finditer(text):
        ticker = m.group(1).upper()
        side = m.group(3).upper()
        date_str = _mdy_to_iso(m.group(4))
        shares = float(m.group(5).replace(",", ""))
        price = float(m.group(6).replace(",", ""))

        # Deduplicate
        if any(t["ticker"] == ticker and t["date"] == date_str and t["side"] == side and abs(t["qty"] - shares) < 1e-4 for t in txs):
            continue

        txs.append({
            "date": date_str,
            "ticker": ticker,
            "side": side,
            "qty": shares,
            "price": price
        })
    return txs


def parse_vanguard_positions(text: str) -> list[dict]:
    lots = []
    # 1. First try detailed lot details (with dates)
    for m in _VG_POS_LOT_RE.finditer(text):
        ticker = m.group(1).upper()
        date_str = _mdy_to_iso(m.group(2))
        shares = float(m.group(3).replace(",", ""))
        cost = float(m.group(4).replace(",", ""))
        lots.append({
            "ticker": ticker,
            "account_label": "Vanguard",
            "lot_type": "other",
            "shares": shares,
            "cost_basis_per_share": cost,
            "acquisition_date": date_str,
            "notes": "Parsed from Vanguard detailed tax lots"
        })

    # 2. If no detailed lots found, fall back to line items (using statement date as acquisition date)
    if not lots:
        for m in _VG_POS_LINE_RE.finditer(text):
            ticker = m.group(1).upper()
            shares = float(m.group(2).replace(",", ""))
            cost = float(m.group(3).replace(",", ""))
            lots.append({
                "ticker": ticker,
                "account_label": "Vanguard",
                "lot_type": "other",
                "shares": shares,
                "cost_basis_per_share": cost,
                "acquisition_date": datetime.now().strftime("%Y-%m-%d"),
                "notes": "Parsed from Vanguard Positions line item"
            })
    return lots


def parse_vanguard_transactions(text: str) -> list[dict]:
    txs = []
    for m in _VG_TX_RE.finditer(text):
        date_str = _mdy_to_iso(m.group(1))
        side = "BUY" if "buy" in m.group(2).lower() else "SELL"
        ticker = m.group(3).upper()
        shares = float(m.group(4).replace(",", ""))
        price = float(m.group(5).replace(",", ""))
        txs.append({
            "date": date_str,
            "ticker": ticker,
            "side": side,
            "qty": shares,
            "price": price
        })
    return txs


def clean_ticker(ticker_cand: str) -> str:
    tc = ticker_cand.upper()
    if tc in KNOWN_VANGUARD_TICKERS:
        return tc
    for known in KNOWN_VANGUARD_TICKERS:
        if known in tc:
            return known
    return tc


def parse_price_and_balances(ticker: str, raw_str: str) -> tuple[str, str, str]:
    clean_str = raw_str.replace(" ", "").replace("$", "")
    is_mutual_fund = len(ticker) == 5 and ticker.startswith("V")
    price_decimals = 2 if is_mutual_fund else 4
    first_dot_idx = clean_str.find(".")
    if first_dot_idx == -1:
        raise ValueError("No decimal point found for Price")
    price_end_idx = first_dot_idx + 1 + price_decimals
    price = clean_str[:price_end_idx]
    remaining = clean_str[price_end_idx:]
    second_dot_idx = remaining.find(".")
    if second_dot_idx == -1:
        raise ValueError("No decimal point found for Prev Balance")
    prev_bal_end_idx = second_dot_idx + 1 + 2
    prev_bal = remaining[:prev_bal_end_idx]
    curr_bal = remaining[prev_bal_end_idx:]
    return price, prev_bal, curr_bal


def _vg_tx_date_to_iso(month_day_str: str, statement_date_str: str) -> str:
    stmt_dt = datetime.strptime(statement_date_str, "%Y-%m-%d")
    stmt_year = stmt_dt.year
    stmt_month = stmt_dt.month
    tx_month, tx_day = map(int, month_day_str.split("/"))
    if tx_month == 12 and stmt_month == 1:
        year = stmt_year - 1
    else:
        year = stmt_year
    return f"{year:04d}-{tx_month:02d}-{tx_day:02d}"


def parse_vanguard_statement_pdf(text: str) -> dict:
    lots = []
    transactions = []
    cash = None
    
    cleaned_text = re.sub(r"(\d{4})([A-Z]{3,7})", r"\1 \2", text)
    
    # 1. Parse positions
    pos_matches = _VG_POS_BLOCK_RE.findall(cleaned_text)
    for raw_ticker, gain_loss, cost_basis, qty, block in pos_matches:
        ticker = clean_ticker(raw_ticker)
        try:
            price, prev_bal, curr_bal = parse_price_and_balances(ticker, block)
            shares = float(qty.replace(",", ""))
            total_cost = float(cost_basis.replace("$", "").replace(",", ""))
            cost_basis_per_share = total_cost / shares if shares > 0 else 0.0
            lots.append({
                "ticker": ticker,
                "lot_type": "other",
                "shares": shares,
                "cost_basis_per_share": round(cost_basis_per_share, 4),
                "acquisition_date": datetime.now().strftime("%Y-%m-%d"),
                "notes": "Parsed from Vanguard Statement Positions"
            })
        except Exception as e:
            pass
            
    # 2. Parse Cash sweep
    cash_match = re.search(
        r"Total Sweep Balance\s*\$?([\d,]+\.\d{2})\s*\$?([\d,]+\.\d{2})",
        cleaned_text, re.IGNORECASE
    )
    if cash_match:
        cash = float(cash_match.group(2).replace(",", ""))
        
    # 3. Completed transactions
    tx_matches = _VG_TX_STATEMENT_RE.findall(cleaned_text)
    stmt_date = _extract_statement_date(cleaned_text)
    for settle, trade, symbol, name, tx_type, acct, qty, price, comm, amount in tx_matches:
        cleaned_symbol = clean_ticker(symbol)
        if cleaned_symbol == "-":
            continue
        if tx_type.lower() in ("buy", "bought", "sell", "sold", "reinvestment"):
            side = "BUY"
            if tx_type.lower() in ("sell", "sold"):
                side = "SELL"
            try:
                tx_qty = float(qty.replace(",", "")) if qty and qty != "-" else 0.0
                tx_price = float(price.replace(",", "")) if price and price != "-" else 0.0
                if tx_qty > 0:
                    txs_date = _vg_tx_date_to_iso(trade, stmt_date)
                    transactions.append({
                        "date": txs_date,
                        "ticker": cleaned_symbol,
                        "side": side,
                        "qty": tx_qty,
                        "price": tx_price
                    })
            except Exception as e:
                pass
                
    return {
        "lots": lots,
        "cash": cash,
        "transactions": transactions
    }


_POSITIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "format_detected": {"type": "string"},
        "account_label": {"type": "string"},
        "cash": {"type": "number"},
        "positions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "shares": {"type": "number"},
                    "cost_basis_per_share": {"type": "number"},
                    "acquisition_date": {"type": "string"}, # YYYY-MM-DD
                    "lot_type": {"type": "string", "enum": ["rsu", "espp", "other"]},
                },
                "required": ["ticker", "shares", "cost_basis_per_share"]
            }
        },
        "warnings": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["format_detected", "positions", "warnings"]
}

_TRANSACTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "format_detected": {"type": "string"},
        "account_label": {"type": "string"},
        "transactions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"}, # YYYY-MM-DD
                    "ticker": {"type": "string"},
                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                    "qty": {"type": "number"},
                    "price": {"type": "number"}
                },
                "required": ["date", "ticker", "side", "qty", "price"]
            }
        },
        "warnings": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["format_detected", "transactions", "warnings"]
}


def parse_with_llm(text: str, filename: str, doc_type: str) -> dict:
    """Invokes LLM (OpenAI or local Ollama) to extract positions or transactions from statement text."""
    schema = _POSITIONS_SCHEMA if doc_type == "positions" else _TRANSACTIONS_SCHEMA
    prompt = (
        f"Extract broker {doc_type} from this statement PDF text export.\n\n"
        "Rules:\n"
        "- Return JSON object matching the requested schema.\n"
        "- Extract ticker, shares, cost basis per share (for positions), and execution details (for transactions).\n"
        "- Infer account_label (e.g. Robinhood or Vanguard) from header references.\n"
        "- Dates must be in YYYY-MM-DD format.\n"
        "- Add warnings if any figures are estimated or ambiguous.\n\n"
        f"Filename: {filename}\n\n"
        f"PDF text:\n{text[:28000]}\n\n"
        f"Schema:\n{json.dumps(schema)}"
    )

    if OPENAI_API_KEY:
        # Use OpenAI API
        body = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {"type": "json_object"}
        }
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=180)
        r.raise_for_status()
        j = r.json()
        u = j.get("usage") or {}
        record_usage("external_import_llm", OPENAI_MODEL, u.get("prompt_tokens", 0), u.get("completion_tokens", 0), provider="openai")
        return json.loads(j["choices"][0]["message"]["content"])
    else:
        # Fallback to local Ollama
        body = {
            "model": LLM_MODEL,
            "prompt": prompt,
            "system": "You are a precise data extractor. Respond only with a raw JSON object matching the requested schema.",
            "stream": False,
            "options": {"temperature": 0}
        }
        r = requests.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=180)
        r.raise_for_status()
        res_text = r.json().get("response", "")
        # Clean up any potential markdown formatting from Ollama response
        match = re.search(r"(\{.*\})", res_text, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        raise RuntimeError("Failed to parse local Ollama response as JSON object.")


def import_external_pdf(
    db,
    data: bytes,
    filename: str = "",
    force_llm: bool = False,
    override_account: Optional[str] = None
) -> dict:
    """Parse PDF bytes, detect account and type, insert positions or transactions into database."""
    text = extract_pdf_text(data)
    if not text.strip():
        return {"status": "error", "message": "PDF contains no extractable text."}

    broker, doc_type = detect_broker_and_type(text, filename)
    if broker != "Unknown":
        account_label = broker
    else:
        account_label = override_account or "External Account"

    # Create account if it doesn't exist
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if not acct:
        now_str = datetime.now().isoformat(timespec="seconds")
        acct = ExternalAccount(
            account_label=account_label,
            cash=0.0,
            risk_profile="balanced",
            created_at=now_str,
            updated_at=now_str
        )
        db.add(acct)
        db.commit()

    parsed_data: dict[str, Any] = {
        "format": "regex",
        "lots": [],
        "transactions": [],
        "warnings": [],
        "cash": None
    }

    # Deterministic parsing attempts
    if not force_llm:
        if account_label.startswith("Robinhood"):
            if doc_type == "positions":
                parsed_data["lots"] = parse_robinhood_positions(text)
                for lot in parsed_data["lots"]:
                    lot["account_label"] = account_label
                cash_val = parse_robinhood_cash(text)
                if cash_val is not None:
                    parsed_data["cash"] = cash_val
                # Parse transactions from the combined statement
                parsed_data["transactions"] = parse_robinhood_transactions(text)
            else:
                parsed_data["transactions"] = parse_robinhood_transactions(text)
        elif account_label.startswith("Vanguard"):
            if "acquisition date:" in text.lower():
                if doc_type == "positions":
                    parsed_data["lots"] = parse_vanguard_positions(text)
                    for lot in parsed_data["lots"]:
                        lot["account_label"] = account_label
                    # Parse transactions from the combined statement
                    parsed_data["transactions"] = parse_vanguard_transactions(text)
                else:
                    parsed_data["transactions"] = parse_vanguard_transactions(text)
            else:
                v_res = parse_vanguard_statement_pdf(text)
                parsed_data["lots"] = v_res["lots"]
                for lot in parsed_data["lots"]:
                    lot["account_label"] = account_label
                if v_res["cash"] is not None:
                    parsed_data["cash"] = v_res["cash"]
                parsed_data["transactions"] = v_res["transactions"]

    # Fallback to LLM if deterministic regex failed to extract anything
    extracted_any = bool(parsed_data["lots"]) or bool(parsed_data["transactions"])
    if force_llm or not extracted_any:
        try:
            llm_res = parse_with_llm(text, filename, doc_type)
            parsed_data["format"] = llm_res.get("format_detected", "llm")
            parsed_data["warnings"] = llm_res.get("warnings") or []
            if doc_type == "positions":
                parsed_data["cash"] = llm_res.get("cash")
                raw_positions = llm_res.get("positions") or []
                lots = []
                for p in raw_positions:
                    lots.append({
                        "ticker": p["ticker"].upper(),
                        "account_label": account_label,
                        "lot_type": p.get("lot_type") or "other",
                        "shares": float(p["shares"]),
                        "cost_basis_per_share": float(p["cost_basis_per_share"]),
                        "acquisition_date": p.get("acquisition_date") or datetime.now().strftime("%Y-%m-%d"),
                        "notes": f"LLM parsed from {filename}"
                    })
                parsed_data["lots"] = lots
            else:
                raw_txs = llm_res.get("transactions") or []
                txs = []
                for t in raw_txs:
                    txs.append({
                        "date": t["date"],
                        "ticker": t["ticker"].upper(),
                        "side": t["side"].upper(),
                        "qty": float(t["qty"]),
                        "price": float(t["price"])
                    })
                parsed_data["transactions"] = txs
        except Exception as e:
            if not extracted_any:
                raise RuntimeError(f"Deterministic parser failed and LLM parser errored: {e}")
            parsed_data["warnings"].append(f"LLM fallback failed with error: {e}")

    # Process and commit parsed data
    now_str = datetime.now().isoformat(timespec="seconds")
    if doc_type == "positions":
        # Delete existing lots for this account before replacing
        db.query(EquityLot).filter(EquityLot.account_label == account_label).delete()

        inserted_count = 0
        for lot in parsed_data["lots"]:
            db_lot = EquityLot(
                ticker=lot["ticker"],
                account_label=account_label,
                lot_type=lot["lot_type"],
                shares=lot["shares"],
                cost_basis_per_share=lot["cost_basis_per_share"],
                acquisition_date=lot["acquisition_date"],
                notes=lot.get("notes") or f"Imported from {filename}",
                created_at=now_str
            )
            db.add(db_lot)
            inserted_count += 1

        if parsed_data["cash"] is not None:
            acct.cash = float(parsed_data["cash"])
            acct.updated_at = now_str

        # Commit transactions found in the combined statement
        txs_inserted = 0
        if parsed_data["transactions"]:
            existing_txs = db.query(ExternalTransaction).filter(ExternalTransaction.account_label == account_label).all()
            existing_fingerprints = {
                (t.execution_date, t.ticker, t.side, round(t.qty, 4), round(t.price, 4))
                for t in existing_txs
            }
            for tx in parsed_data["transactions"]:
                fingerprint = (tx["date"], tx["ticker"], tx["side"], round(tx["qty"], 4), round(tx["price"], 4))
                if fingerprint in existing_fingerprints:
                    continue
                db_tx = ExternalTransaction(
                    account_label=account_label,
                    ticker=tx["ticker"],
                    side=tx["side"],
                    qty=tx["qty"],
                    price=tx["price"],
                    execution_date=tx["date"],
                    raw_details=f"Imported from statement {filename}",
                    created_at=now_str
                )
                db.add(db_tx)
                txs_inserted += 1

        db.commit()
        from data_ingestion.equity_universe_sync import sync_equity_lot_universe
        universe_sync = sync_equity_lot_universe(db)

        # Update the statement anchor (cost basis + share-count snapshot) for this account so future
        # CSV transaction imports reconstruct holdings against the latest real statement instead of a
        # hardcoded/seed snapshot.
        anchor_written = 0
        if parsed_data["lots"]:
            try:
                from data_ingestion.import_external_csv import set_statement_anchor
                stmt_date = _extract_statement_date(text)
                anchor_written = set_statement_anchor(
                    account_label,
                    [{"ticker": l["ticker"], "shares": l["shares"], "avg_cost": l["cost_basis_per_share"]}
                     for l in parsed_data["lots"]],
                    statement_date=stmt_date, source="pdf",
                )
            except Exception as e:
                print(f"Statement-anchor update skipped (non-fatal): {e}")

        return {
            "status": "success",
            "account_label": account_label,
            "doc_type": "positions",
            "parsed_count": inserted_count,
            "cash_updated": parsed_data["cash"],
            "transactions_imported": txs_inserted,
            "anchor_holdings": anchor_written,
            "format": parsed_data["format"],
            "warnings": parsed_data["warnings"],
            "universe_tickers_added": universe_sync["added"],
            "tickers": list({l["ticker"] for l in parsed_data["lots"]} | {t["ticker"] for t in parsed_data["transactions"]})
        }
    else:
        # Ingest transactions, deduplicating against existing logs
        existing_txs = db.query(ExternalTransaction).filter(ExternalTransaction.account_label == account_label).all()
        existing_fingerprints = {
            (t.execution_date, t.ticker, t.side, round(t.qty, 4), round(t.price, 4))
            for t in existing_txs
        }

        inserted_count = 0
        skipped_count = 0
        for tx in parsed_data["transactions"]:
            fingerprint = (tx["date"], tx["ticker"], tx["side"], round(tx["qty"], 4), round(tx["price"], 4))
            if fingerprint in existing_fingerprints:
                skipped_count += 1
                continue

            db_tx = ExternalTransaction(
                account_label=account_label,
                ticker=tx["ticker"],
                side=tx["side"],
                qty=tx["qty"],
                price=tx["price"],
                execution_date=tx["date"],
                raw_details=f"Imported from {filename}",
                created_at=now_str
            )
            db.add(db_tx)
            inserted_count += 1

        db.commit()
        return {
            "status": "success",
            "account_label": account_label,
            "doc_type": "transactions",
            "inserted_count": inserted_count,
            "skipped_count": skipped_count,
            "format": parsed_data["format"],
            "warnings": parsed_data["warnings"],
            "tickers": list({t["ticker"] for t in parsed_data["transactions"]})
        }
