"""Tests for the extension guard (MA distance) and queue supersede-on-run."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db, DailyPrice, PendingTrade
import execution.executor as ex


def _seed_daily(db, ticker, closes, start="2026-01-01"):
    """Seed `closes` as consecutive daily bars (closes[-1] is the most recent)."""
    db.query(DailyPrice).filter(DailyPrice.ticker == ticker).delete()
    d0 = datetime.strptime(start, "%Y-%m-%d")
    for i, c in enumerate(closes):
        ds = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        db.add(DailyPrice(ticker=ticker, date=ds, open=c, high=c, low=c, close=c, volume=1000))
    db.commit()


class MaExtensionTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()

    def tearDown(self):
        self.db.query(DailyPrice).filter(DailyPrice.ticker.in_(["EXT1", "EXT2", "EXT3"])).delete()
        self.db.commit()
        self.db.close()

    def test_extension_positive_when_above_ma(self):
        # 49 bars at 100 + a last bar at 130 → MA≈100.6, extension ≈ +29%.
        _seed_daily(self.db, "EXT1", [100.0] * 49 + [130.0])
        ext = ex.ma_extension(self.db, "EXT1", ma_days=50)
        self.assertIsNotNone(ext)
        self.assertGreater(ext, 0.25)

    def test_none_when_insufficient_history(self):
        _seed_daily(self.db, "EXT2", [100.0] * 10)   # < 50 bars
        self.assertIsNone(ex.ma_extension(self.db, "EXT2", ma_days=50))

    def test_extension_block_threshold(self):
        _seed_daily(self.db, "EXT3", [100.0] * 49 + [130.0])   # ~+29% above MA
        with mock.patch.object(ex, "EXTENSION_GUARD_ENABLED", True), \
             mock.patch.object(ex, "EXTENSION_GUARD_MAX_ABOVE", 0.20):
            self.assertIsNotNone(ex._extension_block(self.db, "EXT3"))   # +29% > 20% → blocked
        with mock.patch.object(ex, "EXTENSION_GUARD_MAX_ABOVE", 0.40):
            self.assertIsNone(ex._extension_block(self.db, "EXT3"))      # +29% < 40% → allowed
        with mock.patch.object(ex, "EXTENSION_GUARD_ENABLED", False):
            self.assertIsNone(ex._extension_block(self.db, "EXT3"))      # guard off → never blocks


class SupersedeTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        self.db.query(PendingTrade).delete()
        self.db.commit()

    def tearDown(self):
        self.db.query(PendingTrade).delete()
        self.db.commit()
        self.db.close()

    def _add(self, account_key, ticker, status="pending_approval"):
        now = datetime.now()
        self.db.add(PendingTrade(account_key=account_key, ticker=ticker, side="buy", qty=1,
                                 intended_type="market", status=status, created_at=now.isoformat(),
                                 expires_at=now.strftime("%Y-%m-%d")))
        self.db.commit()

    def test_supersede_only_target_account_pending(self):
        self._add("live", "AAA")
        self._add("live", "BBB")
        self._add("live", "CCC", status="submitted")   # decided → untouched
        self._add("paper", "DDD")                        # other account → untouched
        n = ex._supersede_pending(self.db, "live")
        self.assertEqual(n, 2)
        statuses = {(r.account_key, r.ticker): r.status for r in self.db.query(PendingTrade).all()}
        self.assertEqual(statuses[("live", "AAA")], "superseded")
        self.assertEqual(statuses[("live", "BBB")], "superseded")
        self.assertEqual(statuses[("live", "CCC")], "submitted")
        self.assertEqual(statuses[("paper", "DDD")], "pending_approval")
        # A subsequent live re-run finds nothing left to supersede (no duplicate buildup).
        self.assertEqual(ex._supersede_pending(self.db, "live"), 0)


class _FakeOrder:
    def __init__(self, symbol):
        self.symbol = symbol


class _FakeApi:
    def __init__(self, symbols):
        self._symbols = symbols
    def list_orders(self, **kw):
        return [_FakeOrder(s) for s in self._symbols]


class OpenOrderTests(unittest.TestCase):
    def test_open_order_symbols(self):
        self.assertEqual(ex.open_order_symbols(_FakeApi(["AAPL", "MU"])), {"AAPL", "MU"})

    def test_open_order_symbols_handles_broker_error(self):
        class Boom:
            def list_orders(self, **kw): raise RuntimeError("down")
        self.assertEqual(ex.open_order_symbols(Boom()), set())

    def test_plan_marks_resting_order_open_order(self):
        from execution.plan import _replay_longterm
        allocations = [{"ticker": "AAPL", "weight": 0.1, "current_price": 100.0}]
        # No position (current == 0) → would normally be "would_open"; resting flips it to "open_order".
        cands = _replay_longterm(allocations, {"AAPL"}, positions={}, equity=100000.0,
                                 budget_fraction=1.0, db=None, resting={"AAPL"})
        self.assertEqual(cands[0]["verdict"], "open_order")
        # Without a resting order it would open.
        cands2 = _replay_longterm(allocations, {"AAPL"}, positions={}, equity=100000.0,
                                  budget_fraction=1.0, db=None, resting=set())
        self.assertEqual(cands2[0]["verdict"], "would_open")


if __name__ == "__main__":
    unittest.main()
