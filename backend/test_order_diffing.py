import unittest
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import tempfile
os.environ.setdefault("DATA_STORAGE_DIR", tempfile.mkdtemp(prefix="ampy_test_db_"))

from app.database import init_db, SessionLocal, VirtualPosition, VirtualOrder, VirtualAccount, RecentPrice, CrashRiskSnapshot
from app.main import apply_crash_rebalancing, ApplyRebalancingRequest
from test_db_guard import assert_isolated_db

class TestOrderDiffing(unittest.TestCase):
    def setUp(self):
        assert_isolated_db()
        init_db()
        self.db = SessionLocal()
        # Clean up database tables for testing
        self.db.query(VirtualPosition).delete()
        self.db.query(VirtualOrder).delete()
        self.db.query(VirtualAccount).delete()
        self.db.query(RecentPrice).delete()
        self.db.query(CrashRiskSnapshot).delete()
        self.db.commit()

        # Seed default virtual account (ID 1)
        self.account = VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
        self.db.add(self.account)

        # Seed crash risk snapshot (Extremely risky regime, forcing large defensive tilt)
        self.db.add(CrashRiskSnapshot(
            as_of_date="2026-06-19",
            composite_index=90.0,
            risk_band="Extreme",
            current_posture="Protect",
            hmm_regime_subscore=80.0
        ))

        # Seed prices
        self.db.add(RecentPrice(ticker="SPY", date="2026-06-19", open=500.0, high=500.0, low=500.0, close=500.0, volume=1000))
        self.db.add(RecentPrice(ticker="GLD", date="2026-06-19", open=200.0, high=200.0, low=200.0, close=200.0, volume=1000))
        self.db.add(RecentPrice(ticker="TLT", date="2026-06-19", open=100.0, high=100.0, low=100.0, close=100.0, volume=1000))
        self.db.commit()

    def tearDown(self):
        self.db.query(VirtualPosition).delete()
        self.db.query(VirtualOrder).delete()
        self.db.query(VirtualAccount).delete()
        self.db.query(RecentPrice).delete()
        self.db.query(CrashRiskSnapshot).delete()
        self.db.commit()
        self.db.close()

    def test_apply_rebalancing_unconfirmed(self):
        req = ApplyRebalancingRequest(confirm_execution=False, target_posture="Froth", preset="balanced")
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            apply_crash_rebalancing(req, self.db)

    def test_apply_rebalancing_execution(self):
        # 1. Seed some current positions: 100 shares of SPY (value = $50,000)
        # Cash is $50,000. Total portfolio value = $100,000.
        self.account.cash = 50000.0
        self.account.buying_power = 50000.0
        self.account.equity = 100000.0

        pos_spy = VirtualPosition(
            ticker="SPY",
            mode="virtual",
            quantity=100.0,
            entry_price=450.0,
            policy="rebalance"
        )
        self.db.add(pos_spy)
        self.db.commit()

        # 2. Run apply rebalancing with balanced preset
        # Defensive playbook for balanced preset will de-risk some fraction and shift to cash/bonds/gold.
        req = ApplyRebalancingRequest(confirm_execution=True, target_posture="Froth", preset="balanced")

        res = apply_crash_rebalancing(req, self.db)

        self.assertEqual(res["status"], "executed")
        self.assertEqual(res["posture_applied"], "Froth")

        # Verify order records got inserted in the database
        orders = self.db.query(VirtualOrder).filter(VirtualOrder.mode == "virtual").all()
        self.assertTrue(len(orders) > 0)

        # Verify positions changed
        spy_pos = self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "SPY", VirtualPosition.mode == "virtual").first()
        # Since SPY is de-risked, we should have sold some shares of SPY.
        if spy_pos:
            self.assertTrue(spy_pos.quantity < 100.0)

if __name__ == "__main__":
    unittest.main()
