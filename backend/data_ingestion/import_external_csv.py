import os
import sys
import csv
import io
import sqlite3
import collections
from datetime import datetime

# Adjust path to import database connection
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading_system.db")

# Ground-truth holdings from the 2026-05-31 monthly statement. Used to (a) supply cost basis for
# positions that arrived via internal transfer / corporate action with no price in the CSV export,
# and (b) reconcile reconstructed share counts (backfill any residual gap).
INDIVIDUAL_PDF_HOLDINGS = {
    "AAPL": {"shares": 36.0, "avg_cost": 312.06},
    "AMD": {"shares": 11.0, "avg_cost": 516.10},
    "AMZN": {"shares": 110.0, "avg_cost": 270.64},
    "AVAV": {"shares": 1.0, "avg_cost": 207.24},
    "BABA": {"shares": 110.0, "avg_cost": 124.22},
    "BRK.B": {"shares": 48.0, "avg_cost": 474.48},
    "GEV": {"shares": 12.0, "avg_cost": 968.32},
    "GOOGL": {"shares": 30.0, "avg_cost": 380.34},
    "HMC": {"shares": 240.0, "avg_cost": 26.99},  # 57 + 183
    "HOOD": {"shares": 200.0, "avg_cost": 94.30},
    "INTC": {"shares": 10.0, "avg_cost": 114.68},
    "KMX": {"shares": 200.0, "avg_cost": 44.62},
    "LUV": {"shares": 100.0, "avg_cost": 42.95},
    "META": {"shares": 107.0, "avg_cost": 632.51},
    "MSFT": {"shares": 24.0, "avg_cost": 450.24},
    "NFLX": {"shares": 380.0, "avg_cost": 86.02},
    "NVDA": {"shares": 125.0, "avg_cost": 211.14},
    "PLTR": {"shares": 45.0, "avg_cost": 156.54},
    "QCOM": {"shares": 22.0, "avg_cost": 251.02},
    "RYCEY": {"shares": 2000.0, "avg_cost": 17.95},
    "TSM": {"shares": 25.0, "avg_cost": 418.45},
    "VTI": {"shares": 1.0, "avg_cost": 372.54},
    "WMT": {"shares": 291.0, "avg_cost": 115.75},
    "KDK": {"shares": 2250.0, "avg_cost": 7.04},
    "REMX": {"shares": 12.0, "avg_cost": 99.63}
}

JOINT_PDF_HOLDINGS = {
    "AAPL": {"shares": 35.0, "avg_cost": 312.06},
    "AMD": {"shares": 20.0, "avg_cost": 516.10},
    "AMZN": {"shares": 10.0, "avg_cost": 270.64},
    "BYND": {"shares": 2000.0, "avg_cost": 0.7885},
    "DKNG": {"shares": 10.0, "avg_cost": 24.49},
    "FSLY": {"shares": 150.0, "avg_cost": 17.765},
    "HOOD": {"shares": 35.0, "avg_cost": 94.30},
    "JKS": {"shares": 52.0, "avg_cost": 23.31},
    "NFLX": {"shares": 60.0, "avg_cost": 86.02},
    "NIO": {"shares": 40.0, "avg_cost": 5.60},
    "ROKU": {"shares": 14.0, "avg_cost": 130.18},
    "RUN": {"shares": 40.0, "avg_cost": 16.72},
    "RYCEY": {"shares": 800.0, "avg_cost": 17.95},
    "SHOP": {"shares": 5.0, "avg_cost": 118.71},
    "SPY": {"shares": 20.0, "avg_cost": 756.48},
    "TSLA": {"shares": 132.5, "avg_cost": 435.79}
}

STATEMENT_DATE = datetime(2026, 5, 31)
# Transaction codes that bring shares IN with no purchase price in the export (internal transfer,
# ACATS-in, merger/spinoff received). These are the lots that were wrongly booked at $0 basis.
TRANSFER_IN_CODES = {"ITRF", "ACATI", "MRGS", "SPR", "SOFF"}

ACCOUNTS = {
    "joint": ("Robinhood Joint (116424851826)", JOINT_PDF_HOLDINGS),
    "individual": ("Robinhood Individual (706393097)", INDIVIDUAL_PDF_HOLDINGS),
}


def _latest_price(cursor, ticker):
    """Most recent close we have locally, used only as a last-resort basis fallback."""
    try:
        cursor.execute("SELECT close FROM recent_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,))
        r = cursor.fetchone()
        return float(r[0]) if r and r[0] else None
    except Exception:
        return None


def _select_account(rows, override_account):
    override = (override_account or "").strip().lower()
    if "joint" in override:
        return ACCOUNTS["joint"]
    if "indiv" in override:
        return ACCOUNTS["individual"]
    tickers = {r[3].strip().upper() for r in rows if r[3] and r[3].strip()}
    if "BYND" in tickers:
        return ACCOUNTS["joint"]
    if "KDK" in tickers:
        return ACCOUNTS["individual"]
    return (None, None)


def import_robinhood_csv(content, override_account=None, db_path=None):
    """Reconstruct a Robinhood account's holdings from a transaction-history CSV export and write
    them to the external-portfolio tables. `content` is the CSV text (str or bytes). Returns a
    summary dict. Positions that arrive via internal transfer / corporate action (no price in the
    export) get their cost basis from the 2026-05-31 statement so they are never booked at $0."""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    db_path = db_path or DB_PATH
    notes = []

    reader = csv.reader(io.StringIO(content))
    next(reader, None)  # header
    rows = []
    for r in reader:
        if not r or len(r) < 9 or not r[0].strip():
            continue
        rows.append(r)

    account_label, pdf_holdings = _select_account(rows, override_account)
    if not account_label:
        return {"status": "error",
                "detail": "Could not identify the account (no BYND/KDK marker found). "
                          "Re-upload and choose Individual or Joint."}

    def get_date(row):
        return datetime.strptime(row[0], "%m/%d/%Y")
    rows.sort(key=get_date)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Replace any prior data for this account (idempotent re-import).
    cursor.execute("DELETE FROM external_transactions WHERE account_label = ?", (account_label,))
    cursor.execute("DELETE FROM external_orders WHERE account_label = ?", (account_label,))
    cursor.execute("DELETE FROM equity_lots WHERE account_label = ?", (account_label,))

    now_str = datetime.now().isoformat(timespec="seconds")
    cursor.execute(
        "INSERT OR IGNORE INTO external_accounts (account_label, cash, risk_profile, created_at, updated_at) "
        "VALUES (?, 0.0, 'balanced', ?, ?)",
        (account_label, now_str, now_str)
    )

    cash = 0.0
    positions = collections.defaultdict(list)  # ticker -> [{date, qty, price, notes?}]

    def resolve_basis(ticker, price_val):
        """Never return $0 for a real holding: prefer the trade price, then the statement avg cost,
        then the latest local price."""
        if price_val and price_val > 0:
            return price_val, None
        gt = pdf_holdings.get(ticker)
        if gt and gt.get("avg_cost"):
            return float(gt["avg_cost"]), "basis from 2026-05-31 statement (transferred-in, no trade price)"
        lp = _latest_price(cursor, ticker)
        if lp:
            return lp, "basis estimated from latest market price (no statement entry)"
        return 0.0, "basis unknown — verify manually"

    def apply_transactions(tx_rows):
        nonlocal cash
        for row in tx_rows:
            dt_iso = datetime.strptime(row[0], "%m/%d/%Y").strftime("%Y-%m-%d")
            ticker = row[3].strip().upper() if row[3] and row[3].strip() else None
            trans_code = row[5]
            quantity = row[6]
            price = row[7]
            amount_str = row[8] if row[8].strip() else "$0.00"

            qty_val = 0.0
            is_surrender = False
            if quantity and quantity.strip():
                qty_clean = quantity.strip()
                if qty_clean.endswith('S'):
                    is_surrender = True
                    qty_clean = qty_clean.rstrip('S')
                try:
                    qty_val = float(qty_clean)
                except ValueError:
                    qty_val = 0.0

            price_val = 0.0
            if price and price.strip():
                try:
                    price_val = float(price.replace("$", "").replace(",", ""))
                except ValueError:
                    price_val = 0.0

            clean_amt = amount_str.replace("$", "").replace("(", "-").replace(")", "").replace(",", "")
            try:
                amount = float(clean_amt) if clean_amt.strip() else 0.0
            except ValueError:
                amount = 0.0
            cash += amount

            cursor.execute(
                "INSERT INTO external_transactions (account_label, ticker, side, qty, price, execution_date, raw_details, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (account_label, ticker or "", "BUY" if amount < 0 else "SELL" if amount > 0 else "OTHER",
                 qty_val, price_val, dt_iso, row[4], now_str)
            )

            actual_code = trans_code
            if is_surrender:
                actual_code = "SELL"
            elif trans_code in TRANSFER_IN_CODES and ticker:
                actual_code = "BUY" if qty_val > 0 else "SELL" if qty_val < 0 else trans_code
            elif trans_code == "ACATO":
                actual_code = "SELL"

            if actual_code in ["Buy", "BUY"] and ticker:
                basis, note = resolve_basis(ticker, price_val)
                lot = {"date": dt_iso, "qty": qty_val, "price": basis}
                if note:
                    lot["notes"] = note
                positions[ticker].append(lot)
            elif actual_code in ["Sell", "SELL"] and ticker:
                qty_to_sell = abs(qty_val)
                lots = positions[ticker]
                while qty_to_sell > 1e-9 and lots:
                    first_lot = lots[0]
                    if first_lot["qty"] <= qty_to_sell + 1e-9:
                        qty_to_sell -= first_lot["qty"]
                        lots.pop(0)
                    else:
                        first_lot["qty"] -= qty_to_sell
                        qty_to_sell = 0.0
            elif trans_code == "SPL" and ticker:
                lots = positions[ticker]
                existing_qty = sum(l["qty"] for l in lots)
                if existing_qty > 0:
                    split_ratio = (existing_qty + qty_val) / existing_qty
                    for lot in lots:
                        lot["qty"] *= split_ratio
                        lot["price"] /= split_ratio

    pre_statement = [r for r in rows if get_date(r) <= STATEMENT_DATE]
    post_statement = [r for r in rows if get_date(r) > STATEMENT_DATE]
    notes.append(f"Pre-statement rows: {len(pre_statement)}, post-statement rows: {len(post_statement)}")

    apply_transactions(pre_statement)

    # Snap every position to the 2026-05-31 statement (the authoritative month-end snapshot), then
    # roll forward post-statement transactions. This makes holdings accurate even when the CSV's
    # FIFO reconstruction drifts (e.g. a leading sell with no prior lots leaves phantom shares) and
    # preserves correct post-statement buys.
    for ticker in set(positions.keys()) | set(pdf_holdings.keys()):
        reconstructed = sum(l["qty"] for l in positions[ticker])
        gt = pdf_holdings.get(ticker)
        if gt:
            diff = round(gt["shares"] - reconstructed, 6)
            if diff > 1e-4:
                positions[ticker].append({"date": "2026-05-31", "qty": diff, "price": gt["avg_cost"],
                                          "notes": "Backfilled from 2026-05-31 statement"})
                notes.append(f"{ticker}: backfilled {diff:.4f} sh @ ${gt['avg_cost']} to match statement {gt['shares']}")
            elif diff < -1e-4:
                to_trim = -diff
                lots = positions[ticker]
                while to_trim > 1e-9 and lots:
                    if lots[0]["qty"] <= to_trim + 1e-9:
                        to_trim -= lots[0]["qty"]
                        lots.pop(0)
                    else:
                        lots[0]["qty"] -= to_trim
                        to_trim = 0.0
                notes.append(f"{ticker}: trimmed {(-diff):.4f} sh to match statement {gt['shares']}")
        else:
            if reconstructed > 1e-4:
                notes.append(f"{ticker}: cleared {reconstructed:.4f} sh — not held per 2026-05-31 statement")
            positions[ticker] = []

    apply_transactions(post_statement)

    conn_cash = round(cash, 2)
    cursor.execute("UPDATE external_accounts SET cash = ?, updated_at = ? WHERE account_label = ?",
                   (conn_cash, now_str, account_label))

    lots_written = 0
    zero_basis = 0
    for ticker, lots in positions.items():
        for lot in lots:
            if lot["qty"] > 1e-9:
                note = lot.get("notes") or "Parsed from Robinhood CSV transaction history"
                if lot["price"] <= 0:
                    zero_basis += 1
                cursor.execute(
                    "INSERT INTO equity_lots (ticker, account_label, lot_type, shares, cost_basis_per_share, acquisition_date, notes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, account_label, "other", lot["qty"], lot["price"], lot["date"], note, now_str)
                )
                lots_written += 1

    # Reconciled order history (filled Buy/Sell only).
    orders_inserted = 0
    for row in rows:
        if row[5] not in ["Buy", "Sell", "BUY", "SELL"]:
            continue
        ticker = row[3].strip().upper()
        dt_iso = datetime.strptime(row[0], "%m/%d/%Y").strftime("%Y-%m-%d")
        try:
            qty = float(row[6]) if row[6] else 0.0
            price = float(row[7].replace("$", "").replace(",", "")) if row[7] else 0.0
        except ValueError:
            continue
        cursor.execute(
            "INSERT INTO external_orders (account_label, ticker, side, qty, limit_price, time_in_force, status, filled_price, filled_qty, execution_date, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (account_label, ticker, row[5].upper(), qty, price, "DAY", "reconciled", price, qty,
             dt_iso, dt_iso + "T10:00:00", dt_iso + "T16:00:00")
        )
        orders_inserted += 1

    conn.commit()
    conn.close()

    summary = {
        "status": "success",
        "account_label": account_label,
        "lots_written": lots_written,
        "orders_inserted": orders_inserted,
        "cash": conn_cash,
        "zero_basis_lots": zero_basis,
        "reconciliation": notes,
    }
    print(f"[import_robinhood_csv] {account_label}: {lots_written} lots, {orders_inserted} orders, "
          f"cash ${conn_cash:,.2f}, zero-basis lots {zero_basis}")
    return summary


def import_csv(csv_path, override_account=None):
    """File-path wrapper for ad-hoc/CLI use."""
    with open(csv_path, "r", encoding="utf-8") as f:
        return import_robinhood_csv(f.read(), override_account=override_account)


def cleanup_stray_robinhood(db_path=None):
    """Remove any plain 'Robinhood' account (a stale prior-import artifact) across all external
    tables. The correctly-labeled Joint/Individual accounts are untouched."""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    removed = {}
    for tbl in ("external_transactions", "external_orders", "equity_lots", "external_accounts"):
        cur.execute(f"DELETE FROM {tbl} WHERE account_label = 'Robinhood'")
        removed[tbl] = cur.rowcount
    conn.commit()
    conn.close()
    return removed


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        for p in args:
            print(import_csv(p))
    else:
        print(import_csv("/Users/arvind/Documents/RobinHood/cccb46f5-92bf-5f33-b88d-07f158e2871d.csv"))
        print(import_csv("/Users/arvind/Documents/RobinHood/b5ff07b9-feba-59cd-95fa-9eb10768ab2b.csv"))
