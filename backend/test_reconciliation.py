import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, VirtualPosition, VirtualOrder, VirtualAccount
from execution.executor import (
    check_alpaca_authentication,
    sync_broker_orders,
    sync_broker_positions
)

class TestReconciliation(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        # Clean up database tables for testing
        self.db.query(VirtualPosition).delete()
        self.db.query(VirtualOrder).delete()
        self.db.query(VirtualAccount).delete()
        self.db.commit()

        # Seed default virtual accounts (ID 1 for replay, ID 2 for real)
        self.account = VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
        self.real_account = VirtualAccount(id=2, cash=100000.0, buying_power=100000.0, equity=100000.0)
        self.db.add(self.account)
        self.db.add(self.real_account)
        self.db.commit()

    def tearDown(self):
        self.db.query(VirtualPosition).delete()
        self.db.query(VirtualOrder).delete()
        self.db.query(VirtualAccount).delete()
        self.db.commit()
        self.db.close()

    def test_check_alpaca_authentication_success(self):
        mock_api = MagicMock()
        mock_api.get_account.return_value = MagicMock()

        result = check_alpaca_authentication(mock_api)
        self.assertTrue(result)
        mock_api.get_account.assert_called_once()

    def test_check_alpaca_authentication_failure(self):
        mock_api = MagicMock()
        mock_api.get_account.side_effect = Exception("Auth key invalid")

        with self.assertRaises(Exception):
            check_alpaca_authentication(mock_api)

    def test_sync_broker_orders_filled(self):
        # 1. Add a pending order to local DB
        pending_order = VirtualOrder(
            id="order-xyz-123",
            ticker="AAPL",
            qty=10.0,
            side="buy",
            type="market",
            status="submitted",
            created_at=datetime.now().isoformat()
        )
        self.db.add(pending_order)
        self.db.commit()

        # 2. Mock Alpaca API get_order to return status "filled"
        mock_api = MagicMock()
        mock_broker_order = MagicMock()
        mock_broker_order.status = "filled"
        mock_broker_order.filled_avg_price = "150.00"
        mock_api.get_order.return_value = mock_broker_order

        # 3. Execute sync
        sync_broker_orders(self.db, mock_api)

        # 4. Assert status is updated in DB
        updated_order = self.db.query(VirtualOrder).filter(VirtualOrder.id == "order-xyz-123").first()
        self.assertEqual(updated_order.status, "filled")
        self.assertEqual(updated_order.filled_price, 150.0)

        # 5. Assert position is created in DB
        pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "AAPL").first()
        self.assertIsNotNone(pos)
        self.assertEqual(pos.quantity, 10.0)
        self.assertEqual(pos.entry_price, 150.0)

    def test_sync_broker_orders_rejected(self):
        # 1. Add pending order to local DB
        pending_order = VirtualOrder(
            id="order-abc-456",
            ticker="TSLA",
            qty=5.0,
            side="buy",
            type="market",
            status="submitted",
            created_at=datetime.now().isoformat()
        )
        self.db.add(pending_order)
        self.db.commit()

        # 2. Mock Alpaca get_order to return status "rejected"
        mock_api = MagicMock()
        mock_broker_order = MagicMock()
        mock_broker_order.status = "rejected"
        mock_api.get_order.return_value = mock_broker_order

        # 3. Execute sync
        sync_broker_orders(self.db, mock_api)

        # 4. Assert status is updated to rejected
        updated_order = self.db.query(VirtualOrder).filter(VirtualOrder.id == "order-abc-456").first()
        self.assertEqual(updated_order.status, "rejected")

        # 5. Assert NO position is created in DB
        pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "TSLA").first()
        self.assertIsNone(pos)

    def test_sync_broker_positions_missing_locally(self):
        # Broker has position in NVDA, local DB has nothing
        mock_api = MagicMock()

        mock_pos = MagicMock()
        mock_pos.symbol = "NVDA"
        mock_pos.qty = "100.0"
        mock_pos.avg_entry_price = "120.50"
        mock_api.list_positions.return_value = [mock_pos]

        mock_account = MagicMock()
        mock_account.cash = "95000.00"
        mock_api.get_account.return_value = mock_account

        # Execute positions sync
        sync_broker_positions(self.db, mock_api)

        # Assert local position was created
        pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "NVDA").first()
        self.assertIsNotNone(pos)
        self.assertEqual(pos.quantity, 100.0)
        self.assertEqual(pos.entry_price, 120.50)

        # Assert synthetic BUY order was logged to populate FIFO lots
        sync_order = self.db.query(VirtualOrder).filter(
            VirtualOrder.ticker == "NVDA",
            VirtualOrder.side == "buy",
            VirtualOrder.status == "filled"
        ).first()
        self.assertIsNotNone(sync_order)
        self.assertEqual(sync_order.qty, 100.0)
        self.assertTrue(sync_order.id.startswith("sync-buy-NVDA-"))

        # Assert cash was reconciled
        account = self.db.query(VirtualAccount).filter(VirtualAccount.id == 2).first()
        self.assertEqual(account.cash, 95000.0)

    def test_sync_broker_positions_qty_mismatch(self):
        # Local DB has 20 shares of AMD, but broker has 35 shares
        local_pos = VirtualPosition(ticker="AMD", quantity=20.0, entry_price=100.0, policy="rebalance")
        self.db.add(local_pos)
        self.db.commit()

        mock_api = MagicMock()
        mock_pos = MagicMock()
        mock_pos.symbol = "AMD"
        mock_pos.qty = "35.0"
        mock_pos.avg_entry_price = "105.00"
        mock_api.list_positions.return_value = [mock_pos]

        mock_account = MagicMock()
        mock_account.cash = "100000.00"
        mock_api.get_account.return_value = mock_account

        # Execute positions sync
        sync_broker_positions(self.db, mock_api)

        # Assert local position qty was updated
        updated_pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "AMD").first()
        self.assertEqual(updated_pos.quantity, 35.0)
        self.assertEqual(updated_pos.entry_price, 105.0)

        # Assert synthetic buy of the difference (15 shares) was logged for FIFO tracking
        sync_order = self.db.query(VirtualOrder).filter(
            VirtualOrder.ticker == "AMD",
            VirtualOrder.side == "buy",
            VirtualOrder.qty == 15.0
        ).first()
        self.assertIsNotNone(sync_order)
        self.assertTrue(sync_order.id.startswith("sync-buy-AMD-"))

    def test_sync_broker_positions_closed_on_broker(self):
        # Local DB has 10 shares of MSFT, but broker has no positions
        local_pos = VirtualPosition(ticker="MSFT", quantity=10.0, entry_price=300.0, policy="rebalance")
        self.db.add(local_pos)
        self.db.commit()

        mock_api = MagicMock()
        mock_api.list_positions.return_value = [] # Closed on broker
        # A genuine close is confirmed by a real filled SELL on the broker.
        mock_sell = MagicMock(); mock_sell.filled_qty = "10.0"
        mock_api.list_orders.return_value = [mock_sell]

        mock_account = MagicMock()
        mock_account.cash = "103000.00"
        mock_api.get_account.return_value = mock_account

        # Execute positions sync
        sync_broker_positions(self.db, mock_api)

        # Assert local position was deleted
        pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "MSFT").first()
        self.assertIsNone(pos)

        # Assert synthetic sell of 10 shares was logged to clean up FIFO logs
        sync_order = self.db.query(VirtualOrder).filter(
            VirtualOrder.ticker == "MSFT",
            VirtualOrder.side == "sell",
            VirtualOrder.qty == 10.0
        ).first()
        self.assertIsNotNone(sync_order)
        self.assertTrue(sync_order.id.startswith("sync-sell-MSFT-"))

    def test_sync_broker_positions_stale_read_keeps_position(self):
        # Broker momentarily returns no positions but there's NO real sell — a stale read.
        # The guard must KEEP the local position and NOT log a phantom sync-sell.
        local_pos = VirtualPosition(ticker="MSFT", quantity=10.0, entry_price=300.0, policy="rebalance")
        self.db.add(local_pos)
        self.db.commit()

        mock_api = MagicMock()
        mock_api.list_positions.return_value = []      # stale/empty read
        mock_api.list_orders.return_value = []          # but no real sell exists

        mock_account = MagicMock()
        mock_account.cash = "100000.00"
        mock_api.get_account.return_value = mock_account

        sync_broker_positions(self.db, mock_api)

        # Position is preserved, and no phantom sell is logged.
        pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "MSFT").first()
        self.assertIsNotNone(pos)
        self.assertEqual(pos.quantity, 10.0)
        phantom = self.db.query(VirtualOrder).filter(
            VirtualOrder.ticker == "MSFT", VirtualOrder.side == "sell").first()
        self.assertIsNone(phantom)

if __name__ == "__main__":
    unittest.main()
