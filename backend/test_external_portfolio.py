import unittest
import unittest.mock
import sys
import os
import tempfile
from datetime import datetime

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_STORAGE_DIR", tempfile.mkdtemp(prefix="ampy_test_db_"))

from app.database import (
    init_db, SessionLocal, EquityLot, ExternalAccount, ExternalOrder, ExternalTransaction,
    RecentPrice, UniverseTicker,
)
from test_db_guard import assert_isolated_db
from data_ingestion.external_importer import (
    parse_robinhood_positions, parse_robinhood_transactions,
    parse_vanguard_positions, parse_vanguard_transactions,
    detect_broker_and_type, import_external_pdf
)
from app.main import (
    UniverseRequest, TickerRequest, confirm_external_order, get_external_positions,
    get_external_suggestions, reconcile_external_portfolio, remove_universe_ticker,
    update_universe,
)
from data_ingestion.equity_universe_sync import sync_equity_lot_universe


class TestExternalPortfolio(unittest.TestCase):
    def setUp(self):
        assert_isolated_db()
        init_db()
        self.db = SessionLocal()
        self._universe_rows = [(row.ticker, row.strategy) for row in self.db.query(UniverseTicker).all()]

        # Clean up database tables
        self.db.query(EquityLot).delete()
        self.db.query(ExternalAccount).delete()
        self.db.query(ExternalOrder).delete()
        self.db.query(ExternalTransaction).delete()
        self.db.query(RecentPrice).delete()
        self.db.query(UniverseTicker).filter(UniverseTicker.ticker == "ZZZZ").delete()
        self.db.commit()

    def tearDown(self):
        self.db.query(EquityLot).delete()
        self.db.query(ExternalAccount).delete()
        self.db.query(ExternalOrder).delete()
        self.db.query(ExternalTransaction).delete()
        self.db.query(RecentPrice).delete()
        self.db.query(UniverseTicker).delete()
        for ticker, strategy in self._universe_rows:
            self.db.add(UniverseTicker(ticker=ticker, strategy=strategy))
        self.db.commit()
        self.db.close()

    def test_detect_broker_and_type(self):
        rh_pos_text = "Robinhood Financial Account Statement Securities Held"
        broker, doc_type = detect_broker_and_type(rh_pos_text)
        self.assertEqual(broker, "Robinhood")
        self.assertEqual(doc_type, "positions")

        rh_joint_text = "Robinhood Joint Tenancy With Rights of Survivorship Account"
        broker, doc_type = detect_broker_and_type(rh_joint_text)
        self.assertEqual(broker, "Robinhood Joint")

        vg_tx_text = "Vanguard Brokerage Account Transactions Bought Sold"
        broker, doc_type = detect_broker_and_type(vg_tx_text)
        self.assertEqual(broker, "Vanguard")
        self.assertEqual(doc_type, "transactions")

        # Test dynamic account number extraction
        rh_num_text = "Robinhood Financial Individual Account #:706393097"
        broker, _ = detect_broker_and_type(rh_num_text)
        self.assertEqual(broker, "Robinhood Individual (706393097)")

        rh_joint_num_text = "Robinhood Joint Tenancy Account #:116424851826"
        broker, _ = detect_broker_and_type(rh_joint_num_text)
        self.assertEqual(broker, "Robinhood Joint (116424851826)")

        vg_num_text = "Vanguard Brokerage Account Number: 1234-5678"
        broker, _ = detect_broker_and_type(vg_num_text)
        self.assertEqual(broker, "Vanguard Individual (1234-5678)")

    def test_parse_robinhood_positions(self):
        sample_text = "AAPL Apple Inc. 12.345600 Shares $182.30 Average Cost $150.50\nMSFT Microsoft Corp. 5.000000 Shares $420.00 Average Cost $400.00"
        lots = parse_robinhood_positions(sample_text)
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots[0]["ticker"], "AAPL")
        self.assertEqual(lots[0]["shares"], 12.3456)
        self.assertEqual(lots[0]["cost_basis_per_share"], 150.50)
        self.assertEqual(lots[1]["ticker"], "MSFT")
        self.assertEqual(lots[1]["shares"], 5.0)
        self.assertEqual(lots[1]["cost_basis_per_share"], 400.0)

    def test_parse_robinhood_positions_new(self):
        sample_text_new = "AAPL Margin 36 $312.06000 $11,234.16\nBRK.B Margin 48 $474.48000 $22,775.04"
        lots = parse_robinhood_positions(sample_text_new)
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots[0]["ticker"], "AAPL")
        self.assertEqual(lots[0]["shares"], 36.0)
        self.assertEqual(lots[0]["cost_basis_per_share"], 312.06)
        self.assertEqual(lots[1]["ticker"], "BRK.B")
        self.assertEqual(lots[1]["shares"], 48.0)
        self.assertEqual(lots[1]["cost_basis_per_share"], 474.48)

    def test_parse_robinhood_transactions(self):
        sample_text = "06/12/2026 Buy AAPL 5.000000 Shares at $175.00 Executed\n06/15/2026 Sell MSFT 2.500000 Shares @ $410.00 Executed"
        txs = parse_robinhood_transactions(sample_text)
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]["date"], "2026-06-12")
        self.assertEqual(txs[0]["ticker"], "AAPL")
        self.assertEqual(txs[0]["side"], "BUY")
        self.assertEqual(txs[0]["qty"], 5.0)
        self.assertEqual(txs[0]["price"], 175.0)

        self.assertEqual(txs[1]["date"], "2026-06-15")
        self.assertEqual(txs[1]["ticker"], "MSFT")
        self.assertEqual(txs[1]["side"], "SELL")
        self.assertEqual(txs[1]["qty"], 2.5)
        self.assertEqual(txs[1]["price"], 410.0)

    def test_parse_robinhood_transactions_new(self):
        sample_text_new = "KDK Margin Buy 05/07/2026 100 $6.13000 $613.00\nAMD Margin Sell 05/12/2026 20 $458.60000 $9,171.81"
        txs = parse_robinhood_transactions(sample_text_new)
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]["date"], "2026-05-07")
        self.assertEqual(txs[0]["ticker"], "KDK")
        self.assertEqual(txs[0]["side"], "BUY")
        self.assertEqual(txs[0]["qty"], 100.0)
        self.assertEqual(txs[0]["price"], 6.13)
        self.assertEqual(txs[1]["date"], "2026-05-12")
        self.assertEqual(txs[1]["ticker"], "AMD")
        self.assertEqual(txs[1]["side"], "SELL")
        self.assertEqual(txs[1]["qty"], 20.0)
        self.assertEqual(txs[1]["price"], 458.60)

    def test_parse_vanguard_positions(self):
        sample_text = "AAPL Acquisition Date: 05/12/2025 Shares: 50.000 Cost basis per share: $145.00\nMSFT Acquisition Date: 08/24/2025 Shares: 10.000 Cost basis per share: $390.00"
        lots = parse_vanguard_positions(sample_text)
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots[0]["ticker"], "AAPL")
        self.assertEqual(lots[0]["shares"], 50.0)
        self.assertEqual(lots[0]["cost_basis_per_share"], 145.0)
        self.assertEqual(lots[0]["acquisition_date"], "2025-05-12")

    def test_parse_vanguard_transactions(self):
        sample_text = "06/10/2026 Buy AAPL 10.0000 $180.00\n06/14/2026 Sell MSFT 5.0000 $405.50"
        txs = parse_vanguard_transactions(sample_text)
        self.assertEqual(len(txs), 2)
        self.assertEqual(txs[0]["date"], "2026-06-10")
        self.assertEqual(txs[0]["ticker"], "AAPL")
        self.assertEqual(txs[0]["side"], "BUY")
        self.assertEqual(txs[0]["qty"], 10.0)
        self.assertEqual(txs[0]["price"], 180.0)

    def test_external_holding_is_added_to_universe_as_hold(self):
        self.db.add(EquityLot(
            ticker="ZZZZ", account_label="External", lot_type="other", shares=3.0,
            cost_basis_per_share=10.0, acquisition_date="2026-06-01",
            notes="test", created_at="2026-06-21",
        ))
        self.db.commit()

        first = sync_equity_lot_universe(self.db)
        second = sync_equity_lot_universe(self.db)
        row = self.db.query(UniverseTicker).filter(UniverseTicker.ticker == "ZZZZ").first()
        self.assertEqual(first["added"], ["ZZZZ"])
        self.assertEqual(second["added"], [])
        self.assertEqual(row.strategy, "hold")

        result = update_universe(UniverseRequest(tickers=["AAPL", "ZZZZ"]), db=self.db)
        self.assertIn("AAPL", result["tickers"])
        self.assertIn("ZZZZ", result["tickers"])
        self.assertEqual(
            self.db.query(UniverseTicker).filter(UniverseTicker.ticker == "ZZZZ").first().strategy,
            "hold",
        )
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as raised:
            remove_universe_ticker(TickerRequest(ticker="ZZZZ"), db=self.db)
        self.assertEqual(raised.exception.status_code, 409)

    @unittest.mock.patch("data_ingestion.external_importer.parse_robinhood_transactions", return_value=[])
    @unittest.mock.patch("data_ingestion.external_importer.parse_robinhood_cash", return_value=100.0)
    @unittest.mock.patch("data_ingestion.external_importer.parse_robinhood_positions")
    @unittest.mock.patch("data_ingestion.external_importer.detect_broker_and_type",
                         return_value=("Robinhood", "positions"))
    @unittest.mock.patch("data_ingestion.external_importer.extract_pdf_text",
                         return_value="Robinhood statement")
    def test_pdf_import_immediately_updates_universe(self, _extract, _detect, positions,
                                                     _cash, _transactions):
        positions.return_value = [{
            "ticker": "ZZZZ", "account_label": "Robinhood", "lot_type": "other",
            "shares": 2.0, "cost_basis_per_share": 25.0,
            "acquisition_date": "2026-06-01", "notes": "imported",
        }]
        result = import_external_pdf(self.db, b"pdf", filename="statement.pdf")
        row = self.db.query(UniverseTicker).filter(UniverseTicker.ticker == "ZZZZ").first()
        self.assertEqual(row.strategy, "hold")
        self.assertIn("ZZZZ", result["universe_tickers_added"])

    def test_confirm_external_order(self):
        # 1. Setup account and prices
        acct = ExternalAccount(account_label="Robinhood", cash=10000.0, risk_profile="balanced", created_at="2026-06-20", updated_at="2026-06-20")
        self.db.add(acct)
        self.db.commit()

        # 2. Confirm BUY trade
        class ConfReq:
            ticker = "AAPL"
            side = "BUY"
            qty = 10.0
            filled_price = 150.0
            execution_date = "2026-06-20"
            time_in_force = "GTC_90"

        res = confirm_external_order(ConfReq(), "Robinhood", db=self.db)
        self.assertEqual(res["cash"], 8500.0)

        # Verify lot was created
        lots = self.db.query(EquityLot).filter(EquityLot.account_label == "Robinhood", EquityLot.ticker == "AAPL").all()
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].shares, 10.0)
        self.assertEqual(lots[0].cost_basis_per_share, 150.0)

        # 3. Confirm SELL trade (FIFO)
        class ConfReqSell:
            ticker = "AAPL"
            side = "SELL"
            qty = 4.0
            filled_price = 160.0
            execution_date = "2026-06-21"
            time_in_force = "DAY"

        res2 = confirm_external_order(ConfReqSell(), "Robinhood", db=self.db)
        self.assertEqual(res2["cash"], 8500.0 + (4.0 * 160.0))

        # Check lot quantity
        lots2 = self.db.query(EquityLot).filter(EquityLot.account_label == "Robinhood", EquityLot.ticker == "AAPL").all()
        self.assertEqual(len(lots2), 1)
        self.assertEqual(lots2[0].shares, 6.0)

    def test_reconcile_external_portfolio(self):
        # Setup account
        acct = ExternalAccount(account_label="Vanguard", cash=5000.0, risk_profile="balanced", created_at="2026-06-20", updated_at="2026-06-20")
        self.db.add(acct)
        self.db.commit()

        # Add manual proposed order
        proposal = ExternalOrder(
            account_label="Vanguard", ticker="AAPL", side="BUY", qty=10.0, limit_price=150.0,
            time_in_force="GTC_90", status="proposed", created_at="2026-06-20", updated_at="2026-06-20"
        )
        self.db.add(proposal)

        # Mock parsed transaction from statement
        stmt_tx = ExternalTransaction(
            account_label="Vanguard", ticker="AAPL", side="BUY", qty=10.0, price=149.50,
            execution_date="2026-06-22", created_at="2026-06-22"
        )
        self.db.add(stmt_tx)
        self.db.commit()

        # Run reconciliation
        res = reconcile_external_portfolio("Vanguard", db=self.db)
        self.assertEqual(res["reconciled_orders"], 1)
        self.assertEqual(res["new_trades_imported"], 0)

        # Check that order status was updated
        prop = self.db.query(ExternalOrder).first()
        self.assertEqual(prop.status, "reconciled")
        self.assertEqual(prop.filled_price, 149.50)

        # Check that lot was created and cash deducted
        self.db.refresh(acct)
        self.assertEqual(acct.cash, 5000.0 - (10.0 * 149.50))
        lot = self.db.query(EquityLot).filter(EquityLot.account_label == "Vanguard", EquityLot.ticker == "AAPL").first()
        self.assertEqual(lot.shares, 10.0)
        self.assertEqual(lot.cost_basis_per_share, 149.50)

    @unittest.mock.patch('robin_stocks.robinhood.login')
    @unittest.mock.patch('robin_stocks.robinhood.account.load_phoenix_account')
    @unittest.mock.patch('robin_stocks.robinhood.build_holdings')
    @unittest.mock.patch('robin_stocks.robinhood.orders.get_all_stock_orders')
    @unittest.mock.patch('robin_stocks.robinhood.get_symbol_by_url')
    @unittest.mock.patch('robin_stocks.robinhood.logout')
    def test_sync_robinhood_api(self, mock_logout, mock_get_symbol, mock_get_orders, mock_build_holdings, mock_load_phoenix, mock_login):
        from app.main import sync_robinhood_api, RobinhoodSyncRequest

        # 1. Mock cash sweep return
        mock_load_phoenix.return_value = {
            "cash_available_from_sweep": {"amount": "10028.02"},
            "cash": {"amount": "257932.92"}
        }

        # 2. Mock build holdings return
        mock_build_holdings.return_value = {
            "AAPL": {"quantity": "36.0", "average_buy_price": "150.0"},
            "MSFT": {"quantity": "24.0", "average_buy_price": "400.0"}
        }

        # 3. Mock get orders return
        mock_get_orders.return_value = [
            {
                "id": "order-1",
                "state": "filled",
                "cumulative_quantity": "5.0",
                "average_price": "160.0",
                "side": "buy",
                "last_transaction_at": "2026-06-12T10:00:00Z",
                "instrument": "https://api.robinhood.com/instruments/aapl-url/",
                "time_in_force": "day"
            }
        ]
        mock_get_symbol.return_value = "AAPL"

        # 4. Construct request
        req = RobinhoodSyncRequest(
            username="test@example.com",
            password="secure_password",
            mfa_secret="ABCD EFGH IJKL MNOP",
            account_label="Robinhood"
        )

        # 5. Call API handler
        res = sync_robinhood_api(req, db=self.db)

        self.assertEqual(res["status"], "success")
        self.assertEqual(res["account_label"], "Robinhood")
        self.assertEqual(res["cash"], 257932.92)
        self.assertEqual(res["positions_synced"], 2)
        self.assertEqual(res["transactions_synced"], 1)

        # Verify db matches
        acct = self.db.query(ExternalAccount).filter(ExternalAccount.account_label == "Robinhood").first()
        self.assertEqual(acct.cash, 257932.92)

        lots = self.db.query(EquityLot).filter(EquityLot.account_label == "Robinhood").all()
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots[0].ticker, "AAPL")
        self.assertEqual(lots[0].shares, 36.0)
        self.assertEqual(lots[0].cost_basis_per_share, 150.0)

        txs = self.db.query(ExternalTransaction).filter(ExternalTransaction.account_label == "Robinhood").all()
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].ticker, "AAPL")
        self.assertEqual(txs[0].qty, 5.0)
        self.assertEqual(txs[0].price, 160.0)
