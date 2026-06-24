"""Tests for the Vanguard cost-basis CSV importer (parsing, detection, merge safeguards)."""
import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_ingestion.import_vanguard_costbasis import (
    is_vanguard_costbasis,
    parse_vanguard_costbasis,
    import_vanguard_costbasis,
    resolve_account_label,
    VARIOUS_DATE,
)

# Two preamble lines + header + a FIFO lot, an avg-cost "Various" lot, and a junk (zero-share) row.
VANGUARD_CSV = (
    "Any changes to your lot relief method or transactions will not be reflected for a few business days.\n"
    "This report is generated for informational purposes only.\n"
    '"Account","Symbol/CUSIP","Description","Acquired date","Cost basis method","Quantity",'
    '"Cost per share","Total cost","Market value","Short term gain loss","Long term gain loss",'
    '"Total gain loss","Covered/Non-covered","Percent gain loss"\n'
    '"72887062","AAPL","APPLE INC","06/15/2017","FIFO","8.0000","36.05","288.42","2354.40"," - ","2065.98","2065.98","Covered","716.31%"\n'
    '"72887062","VTSAX","Vanguard Total Stock Market","Various","AvgCost","383.8250","110.95","42585.02","67971.57","294.19","25092.35","25386.55","Covered","59.61%"\n'
    '"72887062","ZZZ","ZERO SHARES","01/01/2020","FIFO","0.0000","10.00","0.00","0.00"," - "," - "," - ","Covered","0%"\n'
)

ROBINHOOD_CSV = (
    '"Activity Date","Process Date","Settle Date","Instrument","Description","Trans Code","Quantity","Price","Amount"\n'
    '"05/01/2026","05/01/2026","05/03/2026","AAPL","Apple","Buy","10","$150.00","-$1500.00"\n'
)


def _seed_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE external_accounts (account_label TEXT PRIMARY KEY, cash REAL, risk_profile TEXT,"
        " created_at TEXT, updated_at TEXT);"
        "CREATE TABLE equity_lots (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, account_label TEXT,"
        " lot_type TEXT, shares REAL, cost_basis_per_share REAL, acquisition_date TEXT, notes TEXT, created_at TEXT);"
        "CREATE TABLE external_statement_holdings (account_label TEXT, ticker TEXT, shares REAL, avg_cost REAL,"
        " statement_date TEXT, source TEXT, created_at TEXT, PRIMARY KEY (account_label, ticker));"
        "CREATE TABLE universe_tickers (ticker TEXT PRIMARY KEY, strategy TEXT);"
    )
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("INSERT INTO external_accounts VALUES ('Vanguard Joint (7062)',0,'balanced',?,?)", (now, now))
    # Prior aggregate/manual lots that a successful import should replace.
    conn.execute("INSERT INTO equity_lots (ticker,account_label,lot_type,shares,cost_basis_per_share,acquisition_date,notes,created_at)"
                 " VALUES ('AAPL','Vanguard Joint (7062)','other',8,36.05,'2026-06-21','Manually entered',?)", (now,))
    conn.execute("INSERT INTO equity_lots (ticker,account_label,lot_type,shares,cost_basis_per_share,acquisition_date,notes,created_at)"
                 " VALUES ('VTSAX','Vanguard Joint (7062)','other',383.825,110.95,'2026-06-21','Prior',?)", (now,))
    conn.commit()
    conn.close()


class VanguardCostBasisTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix="vg_test_")
        self.db = os.path.join(self._dir, "t.db")
        _seed_db(self.db)

    def test_detects_vanguard_not_robinhood(self):
        self.assertTrue(is_vanguard_costbasis(VANGUARD_CSV))
        self.assertFalse(is_vanguard_costbasis(ROBINHOOD_CSV))

    def test_parse_lots_dates_and_various(self):
        parsed = parse_vanguard_costbasis(VANGUARD_CSV)
        self.assertEqual(parsed["account_number"], "72887062")
        lots = {l["ticker"]: l for l in parsed["lots"]}
        self.assertEqual(set(lots), {"AAPL", "VTSAX"})            # zero-share row dropped
        self.assertEqual(lots["AAPL"]["acquisition_date"], "2017-06-15")
        self.assertEqual(lots["AAPL"]["shares"], 8.0)
        self.assertEqual(lots["AAPL"]["cost_basis_per_share"], 36.05)
        self.assertTrue(lots["VTSAX"]["various"])
        self.assertEqual(lots["VTSAX"]["acquisition_date"], VARIOUS_DATE)

    def test_account_resolution_by_trailing_digits(self):
        label, _ = resolve_account_label("72887062", self.db)
        self.assertEqual(label, "Vanguard Joint (7062)")

    def test_import_replaces_lots(self):
        res = import_vanguard_costbasis(VANGUARD_CSV, db_path=self.db)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["prior_lot_count"], 2)
        self.assertEqual(res["lots_written"], 2)
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT ticker, acquisition_date FROM equity_lots WHERE account_label='Vanguard Joint (7062)'"
                            " ORDER BY ticker").fetchall()
        conn.close()
        self.assertEqual(rows, [("AAPL", "2017-06-15"), ("VTSAX", VARIOUS_DATE)])   # real date now, manual row gone

    def test_dry_run_does_not_mutate(self):
        res = import_vanguard_costbasis(VANGUARD_CSV, db_path=self.db, dry_run=True)
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["dry_run"])
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT acquisition_date FROM equity_lots WHERE ticker='AAPL'").fetchall()
        conn.close()
        self.assertEqual(rows, [("2026-06-21",)])   # unchanged

    def test_unknown_account_is_refused_without_mutating(self):
        res = import_vanguard_costbasis(VANGUARD_CSV, override_account="No Such Account", db_path=self.db)
        self.assertEqual(res["status"], "error")
        conn = sqlite3.connect(self.db)
        n = conn.execute("SELECT count(*) FROM equity_lots").fetchone()[0]
        conn.close()
        self.assertEqual(n, 2)   # untouched

    def test_refuses_replace_when_held_ticker_missing(self):
        # A held ticker the file does NOT cover must block the destructive replace.
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO equity_lots (ticker,account_label,lot_type,shares,cost_basis_per_share,acquisition_date,notes,created_at)"
                     " VALUES ('TSLA','Vanguard Joint (7062)','other',5,200,'2024-01-01','held',datetime('now'))")
        conn.commit()
        conn.close()
        res = import_vanguard_costbasis(VANGUARD_CSV, db_path=self.db)
        self.assertEqual(res["status"], "error")
        self.assertIn("TSLA", res.get("missing_tickers", []))


if __name__ == "__main__":
    unittest.main()
