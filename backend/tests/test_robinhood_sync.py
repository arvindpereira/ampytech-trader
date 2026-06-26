"""Tests for the read-only Robinhood reconcile (no network — the lot math only).

The key invariant: a live snapshot refreshes *current share counts* while preserving the cost-basis
CSV's per-lot detail. We only backfill/trim the delta. ``set_statement_anchor`` is patched out so
these tests never touch the real statement-anchor table.
"""
import os
import sys
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import SessionLocal, Base, engine
from app.database.models import EquityLot, ExternalAccount
import data_ingestion.robinhood_sync as rs

LABEL = "Robinhood Joint (TEST)"


class ReconcileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    def setUp(self):
        self.db = SessionLocal()
        # Clean slate for this label.
        self.db.query(EquityLot).filter(EquityLot.account_label == LABEL).delete()
        self.db.query(ExternalAccount).filter(ExternalAccount.account_label == LABEL).delete()
        now = datetime.now().isoformat(timespec="seconds")
        self.db.add(ExternalAccount(account_label=LABEL, cash=0.0, risk_profile="balanced",
                                    created_at=now, updated_at=now))
        self.db.commit()
        self._anchor = mock.patch.object(rs, "set_statement_anchor", return_value=0)
        self._anchor.start()

    def tearDown(self):
        self._anchor.stop()
        self.db.query(EquityLot).filter(EquityLot.account_label == LABEL).delete()
        self.db.query(ExternalAccount).filter(ExternalAccount.account_label == LABEL).delete()
        self.db.commit()
        self.db.close()

    def _add_lot(self, ticker, shares, basis, date, notes="csv"):
        self.db.add(EquityLot(ticker=ticker, account_label=LABEL, lot_type="other", shares=shares,
                              cost_basis_per_share=basis, acquisition_date=date, notes=notes,
                              created_at=datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def _lots(self, ticker):
        return self.db.query(EquityLot).filter(
            EquityLot.account_label == LABEL, EquityLot.ticker == ticker
        ).order_by(EquityLot.acquisition_date).all()

    def test_match_preserves_existing_lots(self):
        self._add_lot("AAPL", 10, 150.0, "2024-01-01")
        self._add_lot("AAPL", 5, 180.0, "2024-06-01")
        rs.reconcile_positions_to_lots(
            self.db, LABEL, [{"ticker": "AAPL", "shares": 15, "avg_cost": 999.0}], 100.0, "2026-06-25")
        lots = self._lots("AAPL")
        self.assertEqual(len(lots), 2)  # untouched — basis preserved, no delta lot
        self.assertEqual(sorted(l.cost_basis_per_share for l in lots), [150.0, 180.0])

    def test_increase_backfills_one_lot_at_live_cost(self):
        self._add_lot("MSFT", 10, 200.0, "2024-01-01")
        rs.reconcile_positions_to_lots(
            self.db, LABEL, [{"ticker": "MSFT", "shares": 13, "avg_cost": 410.0}], 0.0, "2026-06-25")
        lots = self._lots("MSFT")
        self.assertEqual(len(lots), 2)
        backfill = [l for l in lots if l.notes == "Robinhood API backfill"]
        self.assertEqual(len(backfill), 1)
        self.assertAlmostEqual(backfill[0].shares, 3.0)
        self.assertAlmostEqual(backfill[0].cost_basis_per_share, 410.0)

    def test_decrease_trims_fifo(self):
        self._add_lot("NVDA", 10, 100.0, "2024-01-01")  # oldest
        self._add_lot("NVDA", 10, 120.0, "2024-06-01")
        rs.reconcile_positions_to_lots(
            self.db, LABEL, [{"ticker": "NVDA", "shares": 13, "avg_cost": 130.0}], 0.0, "2026-06-25")
        lots = self._lots("NVDA")
        total = sum(l.shares for l in lots)
        self.assertAlmostEqual(total, 13.0)
        # FIFO: the oldest lot is consumed first (7 of its 10 shares trimmed -> 3 left), newer intact.
        self.assertAlmostEqual(lots[0].shares, 3.0)
        self.assertEqual(lots[0].cost_basis_per_share, 100.0)
        self.assertAlmostEqual(lots[1].shares, 10.0)

    def test_absent_position_zeroes_out(self):
        self._add_lot("PINS", 100, 30.0, "2024-01-01")
        rs.reconcile_positions_to_lots(
            self.db, LABEL, [{"ticker": "MSFT", "shares": 5, "avg_cost": 400.0}], 0.0, "2026-06-25")
        self.assertEqual(len(self._lots("PINS")), 0)  # no longer held at RH -> trimmed to zero

    def test_updates_cash(self):
        rs.reconcile_positions_to_lots(self.db, LABEL, [], 4242.42, "2026-06-25")
        acct = self.db.query(ExternalAccount).filter(ExternalAccount.account_label == LABEL).first()
        self.assertAlmostEqual(acct.cash, 4242.42)


if __name__ == "__main__":
    unittest.main()
