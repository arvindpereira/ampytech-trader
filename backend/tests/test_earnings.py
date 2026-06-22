"""Tests for earnings revision logic and intent routing."""
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

from ml_engine.intent_router import route


class EarningsIntentTests(unittest.TestCase):
    def test_earnings_report_intent(self):
        r = route("What did NVDA management say on the last earnings call?")
        self.assertEqual(r.intent, "earnings_report")
        self.assertIn("NVDA", r.tickers)

    def test_spillover_vs_earnings(self):
        r = route("How might Micron earnings impact my semiconductor holdings?")
        self.assertEqual(r.intent, "event_spillover")


class EpsRevisionTests(unittest.TestCase):
    def test_eps_revision_30d(self):
        from data_ingestion.earnings_content_fetcher import eps_revision_30d

        today = date.today().isoformat()
        old = (date.today() - timedelta(days=40)).isoformat()
        ticker = "TEST"

        class Snap:
            def __init__(self, period, as_of, eps):
                self.period = period
                self.as_of_date = as_of
                self.eps_avg = eps
                self.freq = "quarterly"
                self.ticker = ticker

        snaps = [Snap("2026-03-31", old, 1.0), Snap("2026-03-31", today, 1.1)]
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = snaps
        db.query.return_value = q

        rev = eps_revision_30d(db, ticker)
        self.assertAlmostEqual(rev, 0.1, places=4)


if __name__ == "__main__":
    unittest.main()
