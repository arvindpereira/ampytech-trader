"""Vanguard *cost-basis* CSV importer (separate from the Robinhood transaction-CSV path).

Vanguard's "Cost Basis" download (Holdings → Cost basis → Download) is a per-lot report — one row
per tax lot with the real acquired date, quantity, and cost per share. That is exactly the lot-level
detail the rest of the app wants, so this importer REPLACES an account's `equity_lots` with the file's
lots (dropping any prior aggregate/placeholder/manually-entered rows for that account).

Format (after two informational preamble lines) — header columns:
    Account, Symbol/CUSIP, Description, Acquired date, Cost basis method, Quantity,
    Cost per share, Total cost, Market value as of ..., Short term gain loss ...,
    Long term gain loss ..., Total gain loss ..., Covered/Non-covered, Percent gain loss

Safeguards (the "confirm everything exists before starting from a clean table" the user asked for):
  * The account is matched to an EXISTING external account (by the file's account number) or an explicit
    override — we never invent one.
  * Every ticker the account currently holds must appear in the file, else the destructive replace is
    refused (so we don't wipe a position the file doesn't cover).
  * Per-ticker share/cost totals are reconciled against the prior Vanguard snapshot (the statement
    anchor, falling back to the current lots) and large deviations are surfaced as warnings.
"""
import os
import sys
import csv
import io
import sqlite3
import collections
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trading_system.db")

# Columns that uniquely identify the Vanguard cost-basis layout (used for auto-detection).
SIGNATURE_COLUMNS = {"Symbol/CUSIP", "Acquired date", "Cost basis method", "Cost per share", "Quantity"}

# Vanguard reports average-cost mutual-fund pools as a single lot with acquired date "Various" — there is
# no per-lot date to recover. We store one lot with the exact (average) basis and this placeholder date so
# it classifies as long-term (these pools are multi-year holdings); the note flags the approximation and
# the lot remains editable in the UI if a real date is wanted.
VARIOUS_DATE = "2000-01-01"

# Reconciliation tolerances (relative) before a per-ticker delta is flagged for review.
SHARE_TOL = 0.01    # 1%
COST_TOL = 0.02     # 2%


def _decode(content):
    if isinstance(content, bytes):
        return content.decode("utf-8-sig", errors="replace")
    return content


def _header_index(lines):
    """Row index of the real header (skips the informational preamble), or None."""
    for i, line in enumerate(lines):
        if "Symbol/CUSIP" in line and "Acquired date" in line:
            return i
    return None


def is_vanguard_costbasis(content) -> bool:
    """True if `content` looks like a Vanguard cost-basis download."""
    lines = _decode(content).splitlines()
    hidx = _header_index(lines)
    if hidx is None:
        return False
    header = {h.strip() for h in next(csv.reader([lines[hidx]]))}
    return SIGNATURE_COLUMNS.issubset(header)


def _num(val):
    """Parse a numeric cell ('1,234.50', ' - ', '') → float, or None when not a number."""
    s = (val or "").strip().replace("$", "").replace(",", "").replace("%", "")
    if s in ("", "-", "–"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_vanguard_costbasis(content):
    """Parse the file into lots. Returns {account_number, lots, warnings}.

    Each lot: ticker, acquisition_date (YYYY-MM-DD), shares, cost_basis_per_share, total_cost,
    method, raw_acquired_date, various (bool)."""
    lines = _decode(content).splitlines()
    hidx = _header_index(lines)
    if hidx is None:
        raise ValueError("Not a Vanguard cost-basis file (no recognizable header row).")

    reader = csv.DictReader(io.StringIO("\n".join(lines[hidx:])))
    lots, warnings = [], []
    accounts = set()
    skipped = 0

    for row in reader:
        ticker = (row.get("Symbol/CUSIP") or "").strip().upper()
        shares = _num(row.get("Quantity"))
        basis = _num(row.get("Cost per share"))
        if not ticker or shares is None or shares <= 0:
            skipped += 1
            continue
        accounts.add((row.get("Account") or "").strip())

        raw_date = (row.get("Acquired date") or "").strip()
        various = False
        try:
            acq = datetime.strptime(raw_date, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            various = True
            acq = VARIOUS_DATE

        total_cost = _num(row.get("Total cost"))
        if basis is None:
            basis = (total_cost / shares) if (total_cost and shares) else 0.0

        lots.append({
            "ticker": ticker,
            "acquisition_date": acq,
            "shares": shares,
            "cost_basis_per_share": basis,
            "total_cost": total_cost if total_cost is not None else round(shares * basis, 2),
            "method": (row.get("Cost basis method") or "").strip(),
            "raw_acquired_date": raw_date,
            "various": various,
        })

    if len(accounts) > 1:
        warnings.append(f"File contains multiple account numbers {sorted(accounts)} — only one account is "
                        "imported at a time; pass an explicit account to disambiguate.")
    if skipped:
        warnings.append(f"Skipped {skipped} row(s) with no ticker or non-positive quantity.")

    account_number = next(iter(accounts)) if accounts else None
    return {"account_number": account_number, "lots": lots, "warnings": warnings}


def resolve_account_label(account_number, db_path, override_account=None):
    """Map the file's account number to an EXISTING external account label (never invents one).

    Resolution order: explicit override (must exist) → label containing the full account number →
    label containing the account's trailing 4 digits. Returns (label_or_None, note)."""
    conn = sqlite3.connect(db_path)
    labels = [r[0] for r in conn.execute("SELECT account_label FROM external_accounts").fetchall()]
    conn.close()

    if override_account:
        if override_account in labels:
            return override_account, f"Using explicit account '{override_account}'."
        return None, (f"Requested account '{override_account}' does not exist. Existing accounts: "
                      f"{labels or '(none)'}.")

    num = (account_number or "").strip()
    if num:
        exact = [l for l in labels if num in l]
        if len(exact) == 1:
            return exact[0], f"Matched account number {num} → '{exact[0]}'."
        tail = num[-4:]
        tail_matches = [l for l in labels if tail in l]
        if len(tail_matches) == 1:
            return tail_matches[0], f"Matched account number …{tail} → '{tail_matches[0]}'."
        if len(tail_matches) > 1:
            return None, (f"Account number …{tail} is ambiguous across {tail_matches}; "
                          "re-run with an explicit account.")
    return None, (f"Could not match the file's account number ({num or 'unknown'}) to any existing "
                  f"account. Existing accounts: {labels or '(none)'}. Pass an explicit account to proceed.")


def _existing_aggregate(conn, account_label):
    """Per-ticker reference totals for the account: the statement anchor if present, else current lots."""
    anchor = {}
    for t, sh, ac in conn.execute(
        "SELECT ticker, shares, avg_cost FROM external_statement_holdings WHERE account_label = ?",
        (account_label,),
    ).fetchall():
        anchor[t.upper()] = {"shares": sh, "cost": (sh * ac) if ac else None, "source": "anchor"}

    lots_agg = collections.defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "source": "lots"})
    for t, sh, cb in conn.execute(
        "SELECT ticker, shares, cost_basis_per_share FROM equity_lots WHERE account_label = ?",
        (account_label,),
    ).fetchall():
        a = lots_agg[t.upper()]
        a["shares"] += sh
        a["cost"] += sh * cb

    ref = {}
    for t in set(anchor) | set(lots_agg):
        ref[t] = anchor.get(t) or lots_agg.get(t)
    return ref, dict(lots_agg)


def _reconcile(new_by_ticker, reference):
    """Compare imported per-ticker totals against the reference; return (report_rows, warnings)."""
    rows, warnings = [], []
    for t in sorted(set(new_by_ticker) | set(reference)):
        new = new_by_ticker.get(t, {"shares": 0.0, "cost": 0.0, "n": 0})
        ref = reference.get(t)
        row = {"ticker": t, "new_shares": round(new["shares"], 4), "new_cost": round(new["cost"], 2),
               "new_lots": new.get("n", 0)}
        if ref:
            row["ref_shares"] = round(ref["shares"], 4)
            row["ref_cost"] = round(ref["cost"], 2) if ref.get("cost") is not None else None
            row["ref_source"] = ref["source"]
            if ref["shares"]:
                dsh = (new["shares"] - ref["shares"]) / ref["shares"]
                row["d_shares_pct"] = round(dsh * 100, 2)
                if abs(dsh) > SHARE_TOL:
                    warnings.append(f"{t}: shares {new['shares']:.4f} vs prior {ref['shares']:.4f} "
                                    f"({dsh * 100:+.1f}%).")
            if ref.get("cost"):
                dco = (new["cost"] - ref["cost"]) / ref["cost"]
                row["d_cost_pct"] = round(dco * 100, 2)
                if abs(dco) > COST_TOL:
                    warnings.append(f"{t}: cost ${new['cost']:,.2f} vs prior ${ref['cost']:,.2f} "
                                    f"({dco * 100:+.1f}%).")
        else:
            row["ref_source"] = "new"
            warnings.append(f"{t}: not in the prior snapshot — new position from this file.")
        rows.append(row)
    return rows, warnings


def import_vanguard_costbasis(content, override_account=None, db_path=None, dry_run=False):
    """Parse + reconcile + (unless dry_run) replace the account's equity lots with the file's lots.

    Returns a summary dict (status, account_label, reconciliation, warnings, counts). On any blocking
    problem returns {"status": "error", "detail": ...} WITHOUT mutating the database."""
    db_path = db_path or DB_PATH
    parsed = parse_vanguard_costbasis(content)
    lots = parsed["lots"]
    warnings = list(parsed["warnings"])
    if not lots:
        return {"status": "error", "detail": "No usable lots found in the file."}

    account_label, note = resolve_account_label(parsed["account_number"], db_path, override_account)
    if not account_label:
        return {"status": "error", "detail": note, "account_number": parsed["account_number"]}

    # Aggregate the imported lots per ticker (for reconciliation + the coverage check).
    new_by_ticker = collections.defaultdict(lambda: {"shares": 0.0, "cost": 0.0, "n": 0})
    for lot in lots:
        a = new_by_ticker[lot["ticker"]]
        a["shares"] += lot["shares"]
        a["cost"] += lot["shares"] * lot["cost_basis_per_share"]
        a["n"] += 1

    conn = sqlite3.connect(db_path)
    try:
        reference, lots_agg = _existing_aggregate(conn, account_label)

        # "Confirm everything exists before starting from a clean table": every ticker the account
        # currently HOLDS (has lots for) must be covered by the file, or we refuse to wipe.
        held = {t for t, a in lots_agg.items() if a["shares"] > 1e-6}
        missing = sorted(held - set(new_by_ticker))
        if missing:
            return {"status": "error",
                    "detail": ("Refusing to replace lots: the file is missing ticker(s) the account "
                               f"currently holds: {missing}. Import would delete those positions. "
                               "Re-download a complete cost-basis file or pass them explicitly."),
                    "account_label": account_label, "missing_tickers": missing}

        recon_rows, recon_warnings = _reconcile(new_by_ticker, reference)
        warnings.extend(recon_warnings)
        various_tickers = sorted({l["ticker"] for l in lots if l["various"]})
        if various_tickers:
            warnings.append(f"Average-cost lots with 'Various' acquired date stored with placeholder "
                            f"date {VARIOUS_DATE} (long-term): {various_tickers}.")

        prior_lot_count = sum(1 for _ in conn.execute(
            "SELECT 1 FROM equity_lots WHERE account_label = ?", (account_label,)))

        summary = {
            "status": "success",
            "dry_run": dry_run,
            "account_label": account_label,
            "account_match": note,
            "prior_lot_count": prior_lot_count,
            "lots_to_write": len(lots),
            "tickers": sorted(new_by_ticker),
            "various_tickers": various_tickers,
            "reconciliation": recon_rows,
            "warnings": warnings,
        }
        if dry_run:
            conn.rollback()
            return summary

        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("DELETE FROM equity_lots WHERE account_label = ?", (account_label,))
        written = 0
        for lot in lots:
            note_txt = "Imported from Vanguard cost-basis export."
            if lot["various"]:
                note_txt = (f"Imported from Vanguard cost-basis export. Average-cost pool "
                            f"(acquired 'Various'); date is a long-term placeholder, basis is exact.")
            conn.execute(
                "INSERT INTO equity_lots (ticker, account_label, lot_type, shares, cost_basis_per_share, "
                "acquisition_date, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (lot["ticker"], account_label, "other", lot["shares"], lot["cost_basis_per_share"],
                 lot["acquisition_date"], note_txt, now),
            )
            written += 1
        # Keep imported tickers in the shared price/model universe.
        for t in new_by_ticker:
            conn.execute("INSERT OR IGNORE INTO universe_tickers (ticker, strategy) VALUES (?, 'hold')", (t,))
        conn.commit()
        summary["lots_written"] = written
        return summary
    finally:
        conn.close()


def import_file(path, override_account=None, dry_run=False, db_path=None):
    """File-path wrapper for CLI/ad-hoc use."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return import_vanguard_costbasis(f.read(), override_account=override_account,
                                         dry_run=dry_run, db_path=db_path)


if __name__ == "__main__":
    import json
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv or "--dry" in sys.argv
    override = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--account=")), None)
    path = args[0] if args else "/Users/arvind/Downloads/costbasisdownload_7062.csv"
    print(json.dumps(import_file(path, override_account=override, dry_run=dry), indent=2))
