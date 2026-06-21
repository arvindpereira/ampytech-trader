import os
import sys
import csv
import sqlite3
import collections
from datetime import datetime

# Adjust path to import database connection
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading_system.db")

# Hardcoded PDF Statement ground-truth holdings as of May 31, 2026
INDIVIDUAL_PDF_HOLDINGS = {
    "AAPL": {"shares": 36.0, "avg_cost": 312.06},
    "AMD": {"shares": 11.0, "avg_cost": 516.10},
    "AMZN": {"shares": 110.0, "avg_cost": 270.64},
    "AVAV": {"shares": 1.0, "avg_cost": 207.24},
    "BABA": {"shares": 110.0, "avg_cost": 124.22},
    "BRK.B": {"shares": 48.0, "avg_cost": 474.48},
    "GEV": {"shares": 12.0, "avg_cost": 968.32},
    "GOOGL": {"shares": 30.0, "avg_cost": 380.34},
    "HMC": {"shares": 240.0, "avg_cost": 26.99}, # 57 + 183
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

def import_csv(csv_path):
    print(f"\nProcessing CSV: {csv_path}")
    
    # 1. Read all rows and determine target account
    rows = []
    has_bynd = False
    has_kdk = False
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for r in reader:
            if not r or len(r) < 9 or not r[0].strip():
                continue
            rows.append(r)
            ticker = r[3].strip().upper()
            if ticker == "BYND":
                has_bynd = True
            elif ticker == "KDK":
                has_kdk = True

    if has_bynd:
        account_label = "Robinhood Joint (116424851826)"
        pdf_holdings = JOINT_PDF_HOLDINGS
        print(f"Detected Account: Joint Account ({account_label})")
    elif has_kdk:
        account_label = "Robinhood Individual (706393097)"
        pdf_holdings = INDIVIDUAL_PDF_HOLDINGS
        print(f"Detected Account: Individual Account ({account_label})")
    else:
        print("Could not identify account type (neither BYND nor KDK found). Skipping.")
        return

    # Sort rows chronologically
    def get_date(row):
        return datetime.strptime(row[0], "%m/%d/%Y")
    rows.sort(key=get_date)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Clear existing tables for this account
    cursor.execute("DELETE FROM external_transactions WHERE account_label = ?", (account_label,))
    cursor.execute("DELETE FROM external_orders WHERE account_label = ?", (account_label,))
    cursor.execute("DELETE FROM equity_lots WHERE account_label = ?", (account_label,))
    
    # Create or update external account entry
    now_str = datetime.now().isoformat(timespec="seconds")
    cursor.execute(
        "INSERT OR IGNORE INTO external_accounts (account_label, cash, risk_profile, created_at, updated_at) VALUES (?, 0.0, 'balanced', ?, ?)",
        (account_label, now_str, now_str)
    )

    cash = 0.0
    # positions lot structure: ticker -> list of dicts: {"date": date, "qty": qty, "price": price}
    positions = collections.defaultdict(list)

    # 1. Play transactions up to statement date (2026-05-31)
    pre_statement_rows = []
    post_statement_rows = []
    
    for row in rows:
        dt = get_date(row)
        if dt <= datetime(2026, 5, 31):
            pre_statement_rows.append(row)
        else:
            post_statement_rows.append(row)

    print(f"Pre-statement rows: {len(pre_statement_rows)}, Post-statement rows: {len(post_statement_rows)}")

    def apply_transactions(tx_rows):
        nonlocal cash
        for row in tx_rows:
            date_str = row[0]
            dt_iso = datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
            ticker = row[3].strip().upper() if row[3] and row[3].strip() else None
            trans_code = row[5]
            quantity = row[6]
            price = row[7]
            amount_str = row[8] if row[8].strip() else "$0.00"
            
            # Safe quantity parsing
            qty_val = 0.0
            is_surrender = False
            if quantity and quantity.strip():
                qty_clean = quantity.strip()
                if qty_clean.endswith('S'):
                    is_surrender = True
                    qty_clean = qty_clean.rstrip('S')
                qty_val = float(qty_clean)
            
            # Safe price parsing
            price_val = 0.0
            if price and price.strip():
                price_val = float(price.replace("$", "").replace(",", ""))
            
            # Safe amount parsing
            clean_amt = amount_str.replace("$", "").replace("(", "-").replace(")", "").replace(",", "")
            amount = float(clean_amt) if clean_amt.strip() else 0.0
            cash += amount

            # Ingest to database
            cursor.execute(
                "INSERT INTO external_transactions (account_label, ticker, side, qty, price, execution_date, raw_details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    account_label,
                    ticker or "",
                    "BUY" if amount < 0 else "SELL" if amount > 0 else "OTHER",
                    qty_val,
                    price_val,
                    dt_iso,
                    row[4],
                    now_str
                )
            )
            
            # Map transaction side
            actual_code = trans_code
            if is_surrender:
                actual_code = "SELL"
            elif trans_code in ["ACATI", "ITRF"] and ticker:
                if qty_val > 0:
                    actual_code = "BUY"
                elif qty_val < 0:
                    actual_code = "SELL"
            elif trans_code == "ACATO":
                actual_code = "SELL"
            elif trans_code in ["SPR", "MRGS"] and ticker:
                actual_code = "BUY"

            if actual_code in ["Buy", "BUY"]:
                positions[ticker].append({
                    "date": dt_iso,
                    "qty": qty_val,
                    "price": price_val
                })
            elif actual_code in ["Sell", "SELL"]:
                qty_to_sell = qty_val
                
                # Consume lots FIFO
                lots = positions[ticker]
                while qty_to_sell > 0 and lots:
                    first_lot = lots[0]
                    if first_lot["qty"] <= qty_to_sell:
                        qty_to_sell -= first_lot["qty"]
                        lots.pop(0)
                    else:
                        first_lot["qty"] -= qty_to_sell
                        qty_to_sell = 0.0
            elif trans_code == "SPL":
                split_qty = qty_val
                lots = positions[ticker]
                existing_qty = sum(l["qty"] for l in lots)
                if existing_qty > 0:
                    split_ratio = (existing_qty + split_qty) / existing_qty
                    for lot in lots:
                        lot["qty"] *= split_ratio
                        lot["price"] /= split_ratio

    # Apply pre-statement history
    apply_transactions(pre_statement_rows)

    # 2. Reconcile with PDF Ground-Truth on 2026-05-31 and inject synthetic lots for missing assets
    print("Reconciling with Statement ground-truth holdings...")
    for ticker, gt in pdf_holdings.items():
        reconstructed_shares = sum(l["qty"] for l in positions[ticker])
        diff = gt["shares"] - reconstructed_shares
        if diff > 1e-4:
            print(f"  {ticker}: Statement has {gt['shares']} shares, reconstructed {reconstructed_shares:.4f}. Backfilling diff: {diff:.4f} shares @ avg cost ${gt['avg_cost']}")
            positions[ticker].append({
                "date": "2026-05-31",
                "qty": diff,
                "price": gt["avg_cost"],
                "notes": "Backfilled synthetic lot from monthly statement"
            })
        elif diff < -1e-4:
            print(f"  Warning: Reconstructed {ticker} has {reconstructed_shares:.4f} shares, statement only has {gt['shares']}.")

    # Apply post-statement history (June 2026 transactions)
    apply_transactions(post_statement_rows)

    # 3. Write final lots and cash to database
    conn_cash = round(cash, 2)
    print(f"Final Reconstructed Cash: ${conn_cash:,.2f}")
    cursor.execute(
        "UPDATE external_accounts SET cash = ?, updated_at = ? WHERE account_label = ?",
        (conn_cash, now_str, account_label)
    )

    lots_written = 0
    for ticker, lots in positions.items():
        for lot in lots:
            if lot["qty"] > 0:
                notes = lot.get("notes") or "Parsed and processed from Robinhood CSV history"
                cursor.execute(
                    "INSERT INTO equity_lots (ticker, account_label, lot_type, shares, cost_basis_per_share, acquisition_date, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, account_label, "other", lot["qty"], lot["price"], lot["date"], notes, now_str)
                )
                lots_written += 1

    print(f"Wrote {lots_written} active tax lots to the database.")

    # 4. Insert orders into external_orders corresponding to transactions
    orders_inserted = 0
    for row in rows:
        trans_code = row[5]
        if trans_code not in ["Buy", "Sell", "BUY", "SELL"]:
            continue
        ticker = row[3].strip().upper()
        date_str = row[0]
        dt_iso = datetime.strptime(date_str, "%m/%d/%Y").strftime("%Y-%m-%d")
        qty = float(row[6]) if row[6] else 0.0
        price = float(row[7].replace("$", "").replace(",", "")) if row[7] else 0.0
        
        cursor.execute(
            "INSERT INTO external_orders (account_label, ticker, side, qty, limit_price, time_in_force, status, filled_price, filled_qty, execution_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                account_label,
                ticker,
                trans_code.upper(),
                qty,
                price,
                "DAY",
                "reconciled",
                price,
                qty,
                dt_iso,
                dt_iso + "T10:00:00",
                dt_iso + "T16:00:00"
            )
        )
        orders_inserted += 1

    print(f"Created {orders_inserted} reconciled orders history.")

    conn.commit()
    conn.close()
    print("Account seeding completed successfully.")

if __name__ == "__main__":
    import_csv("/Users/arvind/Downloads/cccb46f5-92bf-5f33-b88d-07f158e2871d.csv")
    import_csv("/Users/arvind/Downloads/b5ff07b9-feba-59cd-95fa-9eb10768ab2b.csv")
