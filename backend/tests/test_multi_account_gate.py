"""Tests for multiple Alpaca accounts + the per-account human-approval gate.

Covers the account registry/client factory, the gate flag defaults, the place_or_queue_order
choke-point, raw-submit shapes, end-of-day expiry, and the approve/reject endpoints (including the
double-submit guard, limit validation, and the unconfigured-live safety stop).
"""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db, PendingTrade, VirtualOrder, AppSetting
import execution.executor as ex
import execution.accounts as accounts

# Tests must NEVER touch a real Alpaca account, even if the dev .env supplies live/paper credentials.
# Force the registry creds-free: paper → in-process mock, live → unconfigured (None).
accounts.ACCOUNTS["paper"] = accounts.AccountDef(
    "paper", "Alpaca Paper", "", "", "https://paper-api.alpaca.markets", is_live=False, default_gate=False)
accounts.ACCOUNTS["live"] = accounts.AccountDef(
    "live", "Alpaca Live", "", "", "https://api.alpaca.markets", is_live=True, default_gate=True)


class FakeOrder:
    def __init__(self, oid="ord-1", status="filled", filled_avg_price=101.0, filled_qty=None):
        self.id = oid
        self.status = status
        self.filled_avg_price = filled_avg_price
        self.filled_qty = filled_qty


class FakeApi:
    """Minimal Alpaca stand-in that records submit_order calls."""
    def __init__(self):
        self.submitted = []
        self.next_order = FakeOrder()

    def submit_order(self, **kwargs):
        self.submitted.append(kwargs)
        return self.next_order

    def get_order(self, oid):
        return self.next_order

    def get_latest_trade(self, ticker):
        return mock.Mock(price=100.0)

    def list_positions(self):
        return []

    def get_account(self):
        return mock.Mock(equity="100000", buying_power="100000", cash="100000")


def _clean(db):
    db.query(PendingTrade).delete()
    db.query(AppSetting).filter(AppSetting.key.like("approval_gate:%")).delete()
    db.commit()


class AccountRegistryTests(unittest.TestCase):
    def test_get_alpaca_api_defaults_to_paper(self):
        # No creds in test env → paper falls back to the in-process virtual mock (a REST client),
        # live returns None (never fabricated), unknown returns None.
        self.assertIsNotNone(ex.get_alpaca_api())          # default == paper
        self.assertIsNotNone(ex.get_alpaca_api("paper"))
        self.assertIsNone(ex.get_alpaca_api("live"))
        self.assertIsNone(ex.get_alpaca_api("nope"))

    def test_enabled_accounts_excludes_unconfigured_live(self):
        self.assertEqual(accounts.enabled_account_keys(), ["paper"])


class GateFlagTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        _clean(self.db)

    def tearDown(self):
        _clean(self.db)
        self.db.close()

    def test_defaults(self):
        self.assertFalse(ex.approval_gate_on(self.db, "paper"))   # paper auto-executes
        self.assertTrue(ex.approval_gate_on(self.db, "live"))     # live gated by default
        self.assertTrue(ex.approval_gate_on(self.db, "ghost"))    # unknown → fail safe ON

    def test_appsetting_override(self):
        self.db.add(AppSetting(key="approval_gate:paper", value="true"))
        self.db.add(AppSetting(key="approval_gate:live", value="false"))
        self.db.commit()
        self.assertTrue(ex.approval_gate_on(self.db, "paper"))
        self.assertFalse(ex.approval_gate_on(self.db, "live"))


class ChokePointTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        _clean(self.db)
        self.api = FakeApi()
        self.params = {"ticker": "AAPL", "side": "buy", "qty": 3, "type": "market",
                       "take_profit": 120.0, "stop_loss": 90.0, "intended_price": 100.0,
                       "sleeve": "swing", "label": "swing", "reason": "test"}

    def tearDown(self):
        _clean(self.db)
        self.db.close()

    def test_gate_on_queues_and_does_not_submit(self):
        res = ex.place_or_queue_order("live", self.db, self.api, self.params)   # live gate default ON
        self.assertEqual(res["action"], "queued")
        self.assertEqual(self.api.submitted, [])
        rows = self.db.query(PendingTrade).filter(PendingTrade.account_key == "live").all()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r.ticker, r.side, r.qty, r.status), ("AAPL", "buy", 3, "pending_approval"))
        self.assertEqual((r.take_profit, r.stop_loss, r.intended_price), (120.0, 90.0, 100.0))
        self.assertEqual(r.expires_at, datetime.now().strftime("%Y-%m-%d"))

    def test_gate_off_submits_once(self):
        res = ex.place_or_queue_order("paper", self.db, self.api, self.params)  # paper gate default OFF
        self.assertEqual(res["action"], "submitted")
        self.assertEqual(len(self.api.submitted), 1)
        self.assertEqual(self.db.query(PendingTrade).count(), 0)

    def test_submit_order_shapes(self):
        # market bracket
        ex._submit_alpaca_order(self.api, self.params)
        kw = self.api.submitted[-1]
        self.assertEqual(kw["order_class"], "bracket")
        self.assertEqual(kw["take_profit"], dict(limit_price=120.0))
        self.assertEqual(kw["stop_loss"], dict(stop_price=90.0))
        # limit
        ex._submit_alpaca_order(self.api, {"ticker": "AAPL", "side": "buy", "qty": 1,
                                           "type": "limit", "limit_price": 99.5})
        kw = self.api.submitted[-1]
        self.assertEqual(kw["type"], "limit")
        self.assertEqual(kw["limit_price"], 99.5)
        self.assertNotIn("order_class", kw)
        # plain market (no bracket legs)
        ex._submit_alpaca_order(self.api, {"ticker": "AAPL", "side": "sell", "qty": 1, "type": "market"})
        kw = self.api.submitted[-1]
        self.assertEqual(kw["type"], "market")
        self.assertNotIn("order_class", kw)

    def test_record_submitted_order_uses_account_mode(self):
        order = FakeOrder(oid="rec-1", status="filled")
        self.api.next_order = order
        ex._record_submitted_order(self.db, self.api, "live", order, self.params)
        vo = self.db.query(VirtualOrder).filter(VirtualOrder.id == "rec-1").first()
        self.assertIsNotNone(vo)
        self.assertEqual(vo.mode, "live")


class ExpiryTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        _clean(self.db)

    def tearDown(self):
        _clean(self.db)
        self.db.close()

    def test_only_stale_pending_expire(self):
        y = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        t = datetime.now().strftime("%Y-%m-%d")
        now = datetime.now().isoformat()
        self.db.add_all([
            PendingTrade(account_key="live", ticker="A", side="buy", qty=1, status="pending_approval",
                         created_at=now, expires_at=y),                       # stale → expire
            PendingTrade(account_key="live", ticker="B", side="buy", qty=1, status="pending_approval",
                         created_at=now, expires_at=t),                       # today → keep
            PendingTrade(account_key="live", ticker="C", side="buy", qty=1, status="submitted",
                         created_at=now, expires_at=y),                       # decided → untouched
        ])
        self.db.commit()
        n = ex._expire_stale_pending(self.db)
        self.assertEqual(n, 1)
        by_ticker = {r.ticker: r.status for r in self.db.query(PendingTrade).all()}
        self.assertEqual(by_ticker, {"A": "expired", "B": "pending_approval", "C": "submitted"})


class ApproveRejectEndpointTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        _clean(self.db)
        from app.main import approve_pending_trade, reject_pending_trade, ApprovePendingRequest
        self.approve = approve_pending_trade
        self.reject = reject_pending_trade
        self.Req = ApprovePendingRequest
        self.api = FakeApi()

    def tearDown(self):
        _clean(self.db)
        self.db.close()

    def _new_pending(self, account_key="paper", status="pending_approval"):
        now = datetime.now()
        row = PendingTrade(account_key=account_key, ticker="AAPL", side="buy", qty=2, intended_type="market",
                           take_profit=120.0, stop_loss=90.0, intended_price=100.0, time_in_force="gtc",
                           status=status, created_at=now.isoformat(), expires_at=now.strftime("%Y-%m-%d"))
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _http(self, fn, *a, **k):
        from fastapi import HTTPException
        try:
            fn(*a, **k)
            return None
        except HTTPException as e:
            return e.status_code

    def test_approve_market_bracket_submits_and_marks_submitted(self):
        row = self._new_pending("paper")
        with mock.patch("execution.executor.get_alpaca_api", return_value=self.api):
            res = self.approve(row.id, self.Req(placement="market_bracket"), db=self.db)
        self.assertEqual(res["status"], "success")
        kw = self.api.submitted[-1]
        self.assertEqual(kw["order_class"], "bracket")
        self.assertEqual(kw["take_profit"], dict(limit_price=120.0))
        self.db.refresh(row)
        self.assertEqual(row.status, "submitted")
        self.assertEqual(row.broker_order_id, "ord-1")

    def test_approve_limit_requires_price(self):
        row = self._new_pending("paper")
        with mock.patch("execution.executor.get_alpaca_api", return_value=self.api):
            code = self._http(self.approve, row.id, self.Req(placement="limit", limit_price=None), db=self.db)
        self.assertEqual(code, 400)
        self.assertEqual(self.api.submitted, [])   # nothing placed

    def test_approve_limit_places_limit(self):
        row = self._new_pending("paper")
        with mock.patch("execution.executor.get_alpaca_api", return_value=self.api):
            self.approve(row.id, self.Req(placement="limit", limit_price=98.5), db=self.db)
        kw = self.api.submitted[-1]
        self.assertEqual(kw["type"], "limit")
        self.assertEqual(kw["limit_price"], 98.5)

    def test_double_approve_returns_409(self):
        row = self._new_pending("paper", status="submitted")
        with mock.patch("execution.executor.get_alpaca_api", return_value=self.api):
            code = self._http(self.approve, row.id, self.Req(placement="market_bracket"), db=self.db)
        self.assertEqual(code, 409)

    def test_approve_unconfigured_live_400_no_submit(self):
        row = self._new_pending("live")   # live has no creds in test env
        code = self._http(self.approve, row.id, self.Req(placement="market_bracket"), db=self.db)
        self.assertEqual(code, 400)
        self.assertEqual(self.api.submitted, [])

    def test_reject(self):
        row = self._new_pending("paper")
        res = self.reject(row.id, db=self.db)
        self.assertEqual(res["status"], "success")
        self.db.refresh(row)
        self.assertEqual(row.status, "rejected")
        # double-reject → 409
        self.assertEqual(self._http(self.reject, row.id, db=self.db), 409)


class MigrationTests(unittest.TestCase):
    def test_real_rows_rekeyed_to_paper(self):
        init_db()
        db = SessionLocal()
        try:
            # Clear the one-time marker so the re-key runs for this fixture, then seed a legacy row.
            db.query(AppSetting).filter(AppSetting.key == "migration:mode_real_to_paper").delete()
            db.add(VirtualOrder(id="legacy-real-1", mode="real", ticker="ZZZ", qty=1.0, side="buy",
                                type="market", status="filled", created_at=datetime.now().isoformat()))
            db.commit()
        finally:
            db.close()
        init_db()   # one-time re-key of mode='real' → 'paper'
        db = SessionLocal()
        try:
            vo = db.query(VirtualOrder).filter(VirtualOrder.id == "legacy-real-1").first()
            self.assertEqual(vo.mode, "paper")
            self.assertEqual(db.query(VirtualOrder).filter(VirtualOrder.mode == "real").count(), 0)
        finally:
            db.delete(vo)
            db.commit()
            db.close()


if __name__ == "__main__":
    unittest.main()
