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
ANCHOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anchors")

# Account labels (the BYND/KDK markers below are only used to auto-detect which account a CSV is for —
# they are NOT a basis source).
ACCOUNT_LABELS = {
    "joint": "Robinhood Joint (116424851826)",
    "individual": "Robinhood Individual (706393097)",
}

# Seed anchor files for accounts whose cost basis can't be reconstructed from their own CSV (positions
# transferred in with no trade price). These hold the formerly-hardcoded values as plain data; a real
# monthly statement PDF import overwrites them. Accounts NOT listed here reconstruct purely from the CSV.
SEED_ANCHORS = {
    ACCOUNT_LABELS["joint"]: os.path.join(ANCHOR_DIR, "robinhood_joint_2026-05-31.csv"),
    ACCOUNT_LABELS["individual"]: os.path.join(ANCHOR_DIR, "robinhood_individual_2026-05-31.csv"),
}

# Transaction codes that bring shares IN with no purchase price in the export (internal transfer,
# ACATS-in, merger/spinoff received).
TRANSFER_IN_CODES = {"ITRF", "ACATI", "MRGS", "SPR", "SOFF"}


# ---------------------------------------------------------------------------
# Statement anchor (cost-basis + share-count snapshot) — replaces the old hardcoded dicts.
# Populated automatically from a monthly statement PDF, or seeded from a data CSV.
# ---------------------------------------------------------------------------
def _ensure_anchor_table(cursor):
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS external_statement_holdings ("
        "account_label TEXT NOT NULL, ticker TEXT NOT NULL, shares REAL NOT NULL, avg_cost REAL, "
        "statement_date TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'pdf', created_at TEXT NOT NULL, "
        "PRIMARY KEY (account_label, ticker))"
    )


def get_statement_anchor(account_label, db_path=None):
    """Return the account's anchor as {TICKER: {shares, avg_cost, statement_date}} (empty if none)."""
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    _ensure_anchor_table(cur)
    cur.execute("SELECT ticker, shares, avg_cost, statement_date FROM external_statement_holdings "
                "WHERE account_label = ?", (account_label,))
    rows = cur.fetchall()
    conn.close()
    return {r[0].upper(): {"shares": r[1], "avg_cost": r[2], "statement_date": r[3]} for r in rows}


def set_statement_anchor(account_label, holdings, statement_date, source="pdf", db_path=None):
    """Replace the account's anchor with `holdings` (iterable of dicts with ticker/shares and an
    optional avg_cost or cost_basis_per_share). Returns the number of rows written."""
    conn = sqlite3.connect(db_path or DB_PATH)
    cur = conn.cursor()
    _ensure_anchor_table(cur)
    now = datetime.now().isoformat(timespec="seconds")
    cur.execute("DELETE FROM external_statement_holdings WHERE account_label = ?", (account_label,))
    n = 0
    for h in holdings:
        ticker = str(h.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            shares = float(h.get("shares") or 0)
        except (TypeError, ValueError):
            continue
        if shares <= 0:
            continue
        raw_cost = h.get("avg_cost", h.get("cost_basis_per_share"))
        try:
            avg_cost = float(raw_cost) if raw_cost not in (None, "") else None
        except (TypeError, ValueError):
            avg_cost = None
        cur.execute("INSERT OR REPLACE INTO external_statement_holdings "
                    "(account_label, ticker, shares, avg_cost, statement_date, source, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (account_label, ticker, shares, avg_cost, statement_date, source, now))
        n += 1
    conn.commit()
    conn.close()
    return n


def load_anchor_csv(path, account_label, statement_date=None, source="csv-seed", db_path=None):
    """Load a simple `ticker,shares,avg_cost` CSV into the anchor for an account."""
    if statement_date is None:
        base = os.path.basename(path)
        statement_date = base.rsplit("_", 1)[-1].replace(".csv", "") if "_" in base else datetime.now().strftime("%Y-%m-%d")
    holdings = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            holdings.append({"ticker": row.get("ticker"), "shares": row.get("shares"),
                             "avg_cost": row.get("avg_cost")})
    return set_statement_anchor(account_label, holdings, statement_date, source=source, db_path=db_path)


def _resolve_anchor(account_label, db_path):
    """Return the account's anchor, auto-seeding from a bundled data CSV the first time if one exists."""
    anchor = get_statement_anchor(account_label, db_path)
    if not anchor and account_label in SEED_ANCHORS and os.path.exists(SEED_ANCHORS[account_label]):
        load_anchor_csv(SEED_ANCHORS[account_label], account_label, db_path=db_path)
        anchor = get_statement_anchor(account_label, db_path)
    return anchor


def _latest_price(cursor, ticker):
    """Most recent close we have locally, used only as a last-resort basis fallback."""
    try:
        cursor.execute("SELECT close FROM recent_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,))
        r = cursor.fetchone()
        if r and r[0]:
            return float(r[0])
        cursor.execute("SELECT close FROM daily_prices WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,))
        r = cursor.fetchone()
        return float(r[0]) if r and r[0] else None
    except Exception:
        return None


def _select_account_label(rows, override_account):
    override = (override_account or "").strip().lower()
    if "joint" in override:
        return ACCOUNT_LABELS["joint"]
    if "indiv" in override:
        return ACCOUNT_LABELS["individual"]
    tickers = {r[3].strip().upper() for r in rows if r[3] and r[3].strip()}
    if "BYND" in tickers:
        return ACCOUNT_LABELS["joint"]
    if "KDK" in tickers:
        return ACCOUNT_LABELS["individual"]
    return None


def import_robinhood_csv(content, override_account=None, db_path=None):
    """Reconstruct a Robinhood account's holdings from a transaction-history CSV export.

    Cost basis comes from real trade prices in the CSV. If the account has a statement *anchor*
    (from a monthly PDF import or a seed CSV) it is used to (a) supply basis for positions that
    transferred in with no price and (b) snap share counts to the month-end snapshot before rolling
    forward later trades. Accounts with no anchor (e.g. the Individual account, which has full history
    from account opening) reconstruct purely from the CSV, with deficit-aware FIFO so a sell that
    precedes its buy doesn't leave phantom shares."""
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

    account_label = _select_account_label(rows, override_account)
    if not account_label:
        return {"status": "error",
                "detail": "Could not identify the account (no BYND/KDK marker found). "
                          "Re-upload and choose Individual or Joint."}

    anchor = _resolve_anchor(account_label, db_path)
    anchor_date = None
    if anchor:
        try:
            anchor_date = datetime.strptime(next(iter(anchor.values()))["statement_date"], "%Y-%m-%d")
        except Exception:
            anchor_date = None
    notes.append(f"Anchor: {'yes (' + str(len(anchor)) + ' holdings)' if anchor else 'none — pure CSV reconstruction'}")

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
    positions = collections.defaultdict(list)   # ticker -> [{date, qty, price, notes?}]
    deficits = collections.defaultdict(float)    # ticker -> shares sold before they were held (FIFO deficit)

    def resolve_basis(ticker, price_val):
        if price_val and price_val > 0:
            return price_val, None
        a = anchor.get(ticker)
        if a and a.get("avg_cost"):
            return float(a["avg_cost"]), "basis from statement anchor (transferred-in / no trade price)"
        lp = _latest_price(cursor, ticker)
        if lp:
            return lp, "basis estimated from latest market price — verify"
        return 0.0, "basis unknown — enter manually"

    def add_buy(ticker, qty, dt_iso, price_val):
        d = deficits[ticker]
        if d > 0:
            repay = min(d, qty)
            deficits[ticker] -= repay
            qty -= repay
        if qty <= 1e-9:
            return
        basis, note = resolve_basis(ticker, price_val)
        lot = {"date": dt_iso, "qty": qty, "price": basis}
        if note:
            lot["notes"] = note
        positions[ticker].append(lot)

    def do_sell(ticker, qty):
        qty_to_sell = abs(qty)
        lots = positions[ticker]
        while qty_to_sell > 1e-9 and lots:
            if lots[0]["qty"] <= qty_to_sell + 1e-9:
                qty_to_sell -= lots[0]["qty"]
                lots.pop(0)
            else:
                lots[0]["qty"] -= qty_to_sell
                qty_to_sell = 0.0
        if qty_to_sell > 1e-9:      # sold more than we held (sell precedes its buy) — carry a deficit
            deficits[ticker] += qty_to_sell

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
                add_buy(ticker, qty_val, dt_iso, price_val)
            elif actual_code in ["Sell", "SELL"] and ticker:
                do_sell(ticker, qty_val)
            elif trans_code == "SPL" and ticker:
                lots = positions[ticker]
                existing_qty = sum(l["qty"] for l in lots)
                if existing_qty > 0:
                    split_ratio = (existing_qty + qty_val) / existing_qty
                    for lot in lots:
                        lot["qty"] *= split_ratio
                        lot["price"] /= split_ratio

    if anchor and anchor_date:
        # Replay up to the statement, snap to the anchor (authoritative snapshot), roll forward.
        pre = [r for r in rows if get_date(r) <= anchor_date]
        post = [r for r in rows if get_date(r) > anchor_date]
        notes.append(f"Pre-anchor rows: {len(pre)}, post-anchor rows: {len(post)}")
        apply_transactions(pre)
        for ticker in set(positions.keys()) | set(anchor.keys()):
            reconstructed = sum(l["qty"] for l in positions[ticker])
            a = anchor.get(ticker)
            if a:
                diff = round(a["shares"] - reconstructed, 6)
                if diff > 1e-4:
                    basis = a["avg_cost"] if a.get("avg_cost") else resolve_basis(ticker, 0)[0]
                    positions[ticker].append({"date": a["statement_date"], "qty": diff, "price": basis,
                                              "notes": "Backfilled from statement anchor"})
                    notes.append(f"{ticker}: backfilled {diff:.4f} sh to match anchor {a['shares']}")
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
                    notes.append(f"{ticker}: trimmed {(-diff):.4f} sh to match anchor {a['shares']}")
            else:
                if reconstructed > 1e-4:
                    notes.append(f"{ticker}: cleared {reconstructed:.4f} sh — not in the statement anchor")
                positions[ticker] = []
        apply_transactions(post)
    else:
        # No anchor: reconstruct holdings purely from the full CSV history.
        apply_transactions(rows)
        leftover = {t: round(d, 2) for t, d in deficits.items() if d > 1e-4}
        if leftover:
            notes.append(f"Unresolved sell deficits (sold more than reconstructed): {leftover}")

    conn_cash = round(cash, 2)
    cursor.execute("UPDATE external_accounts SET cash = ?, updated_at = ? WHERE account_label = ?",
                   (conn_cash, now_str, account_label))

    lots_written = 0
    zero_basis = 0
    held_tickers = set()
    for ticker, lots in positions.items():
        for lot in lots:
            if lot["qty"] > 1e-9:
                note = lot.get("notes") or "Parsed from Robinhood CSV transaction history"
                held_tickers.add(ticker)
                if lot["price"] <= 0:
                    zero_basis += 1
                cursor.execute(
                    "INSERT INTO equity_lots (ticker, account_label, lot_type, shares, cost_basis_per_share, acquisition_date, notes, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, account_label, "other", lot["qty"], lot["price"], lot["date"], note, now_str)
                )
                lots_written += 1

    # Keep imported holdings in the shared price/model universe even when this importer is run
    # directly from the CLI. Preserve any explicit strategy assignment already on the row.
    for ticker in held_tickers:
        cursor.execute(
            "INSERT OR IGNORE INTO universe_tickers (ticker, strategy) VALUES (?, ?)",
            (ticker, "hold"),
        )

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
        "anchored": bool(anchor),
        "tickers": sorted(held_tickers),
        "reconciliation": notes,
    }
    print(f"[import_robinhood_csv] {account_label}: {lots_written} lots, {orders_inserted} orders, "
          f"cash ${conn_cash:,.2f}, zero-basis lots {zero_basis}, anchored={bool(anchor)}")
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
