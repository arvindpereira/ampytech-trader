"""Tests for dashboard account-awareness: the book resolver, live read-only guards, live-liquidate
gating, and per-account position reads."""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db, VirtualPosition, VirtualAccount, PendingTrade, AppSetting
import execution.accounts as accounts

# Never touch a real Alpaca account from tests, even if the dev .env has creds.
accounts.ACCOUNTS["paper"] = accounts.AccountDef(
    "paper", "Alpaca Paper", "", "", "https://paper-api.alpaca.markets", is_live=False, default_gate=False)
accounts.ACCOUNTS["live"] = accounts.AccountDef(
    "live", "Alpaca Live", "", "", "https://api.alpaca.markets", is_live=True, default_gate=True)

from app import main
from app.main import (_resolve_book, _is_broker_account, update_holding, delete_holding,
                      update_account_cash, get_virtual_positions, liquidate_position,
                      HoldingRequest, AccountCashRequest, LiquidateRequest, set_sim_date)


def _http_code(fn, *a, **k):
    from fastapi import HTTPException
    try:
        fn(*a, **k)
        return None
    except HTTPException as e:
        return e.status_code


class FakeBrokerPos:
    def __init__(self, qty, price):
        self.qty = qty
        self.current_price = price


class FakeApi:
    def __init__(self, held=10.0, price=100.0):
        self.submitted = []
        self._held, self._price = held, price
    def get_position(self, ticker):
        return FakeBrokerPos(self._held, self._price)
    def submit_order(self, **kw):
        self.submitted.append(kw)
        return mock.Mock(id="ord-x", status="accepted")
    def get_order(self, oid):
        return mock.Mock(status="accepted", filled_avg_price=None, filled_qty=0)


class ResolverTests(unittest.TestCase):
    def test_book_mapping(self):
        self.assertEqual(_resolve_book("paper"), (2, "paper", None))
        self.assertEqual(_resolve_book("live"), (3, "live", None))
        self.assertEqual(_resolve_book("real"), (2, "paper", None))    # legacy alias
        self.assertEqual(_resolve_book(None), (2, "paper", None))
        self.assertEqual(_resolve_book("replay"), (1, "replay", None))
        self.assertEqual(_resolve_book("whatever"), (1, "replay", None))

    def test_broker_account_flag(self):
        self.assertTrue(_is_broker_account("paper"))
        self.assertTrue(_is_broker_account("live"))
        self.assertFalse(_is_broker_account("replay"))

    def test_sim_date_overlay_forces_replay(self):
        set_sim_date("2020-01-01")
        try:
            self.assertEqual(_resolve_book("paper"), (1, "replay", "2020-01-01"))
            self.assertEqual(_resolve_book("live"), (1, "replay", "2020-01-01"))
            self.assertFalse(_is_broker_account("paper"))
        finally:
            set_sim_date(None)


class LiveReadOnlyTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_live_holdings_and_cash_are_403(self):
        self.assertEqual(_http_code(update_holding, HoldingRequest(
            ticker="AAPL", quantity=1, entry_price=100, policy="rebalance"), mode="live", db=self.db), 403)
        self.assertEqual(_http_code(delete_holding, "AAPL", mode="live", db=self.db), 403)
        self.assertEqual(_http_code(update_account_cash, AccountCashRequest(cash=5.0),
                                    mode="live", db=self.db), 403)

    def test_paper_holdings_allowed(self):
        res = update_holding(HoldingRequest(ticker="ZZZ", quantity=2, entry_price=50, policy="rebalance"),
                             mode="paper", db=self.db)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["holding"]["mode"], "paper")
        delete_holding("ZZZ", mode="paper", db=self.db)   # cleanup; should not raise


class PerAccountPositionTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "BOOKX").delete()
        self.db.add(VirtualPosition(ticker="BOOKX", mode="paper", quantity=5, entry_price=10, policy="rebalance"))
        self.db.commit()

    def tearDown(self):
        self.db.query(VirtualPosition).filter(VirtualPosition.ticker == "BOOKX").delete()
        self.db.commit()
        self.db.close()

    def test_positions_are_scoped_to_account(self):
        paper = get_virtual_positions(mode="paper", db=self.db)
        self.assertIn("BOOKX", [p["symbol"] for p in paper])
        live = get_virtual_positions(mode="live", db=self.db)
        self.assertNotIn("BOOKX", [p["symbol"] for p in live])   # live book is separate


class LiveLiquidateGateTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        self.db.query(PendingTrade).delete()
        self.db.query(AppSetting).filter(AppSetting.key.like("approval_gate:%")).delete()
        self.db.commit()

    def tearDown(self):
        self.db.query(PendingTrade).delete()
        self.db.commit()
        self.db.close()

    def test_live_liquidate_queues_for_approval(self):
        api = FakeApi(held=8.0, price=150.0)
        with mock.patch("execution.executor.get_alpaca_api", return_value=api):
            res = liquidate_position(LiquidateRequest(ticker="AAPL", shares=3), mode="live", db=self.db)
        self.assertEqual(res["status"], "queued_for_approval")
        self.assertEqual(res["shares"], 3.0)
        self.assertEqual(api.submitted, [])   # nothing placed on the broker
        rows = self.db.query(PendingTrade).filter(PendingTrade.account_key == "live").all()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].side, rows[0].qty, rows[0].status),
                         ("sell", 3.0, "pending_approval"))


if __name__ == "__main__":
    unittest.main()
