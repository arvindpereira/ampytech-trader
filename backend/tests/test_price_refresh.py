"""Tests for the smart (bar-period-aware) intraday price refresh."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_ingestion.price_fetcher as pf


class BarPeriodStartTests(unittest.TestCase):
    def test_hourly_floors_to_the_hour(self):
        with mock.patch.object(pf, "DATA_TIMESPAN", "hour"), mock.patch.object(pf, "DATA_MULTIPLIER", 1):
            self.assertEqual(pf._current_bar_period_start(datetime(2026, 6, 25, 15, 1, 39)),
                             datetime(2026, 6, 25, 15, 0, 0))
            self.assertEqual(pf._current_bar_period_start(datetime(2026, 6, 25, 15, 59, 59)),
                             datetime(2026, 6, 25, 15, 0, 0))

    def test_multi_hour_bucket(self):
        with mock.patch.object(pf, "DATA_TIMESPAN", "hour"), mock.patch.object(pf, "DATA_MULTIPLIER", 4):
            self.assertEqual(pf._current_bar_period_start(datetime(2026, 6, 25, 14, 30)),
                             datetime(2026, 6, 25, 12, 0))  # 14 // 4 * 4 = 12

    def test_minute_floors_to_bucket(self):
        with mock.patch.object(pf, "DATA_TIMESPAN", "minute"), mock.patch.object(pf, "DATA_MULTIPLIER", 5):
            self.assertEqual(pf._current_bar_period_start(datetime(2026, 6, 25, 15, 7, 30)),
                             datetime(2026, 6, 25, 15, 5, 0))

    def test_refetch_only_on_new_period(self):
        """The decision the freshness check makes: a bar from the CURRENT period is skipped; one from a
        PREVIOUS period is re-fetched."""
        with mock.patch.object(pf, "DATA_TIMESPAN", "hour"), mock.patch.object(pf, "DATA_MULTIPLIER", 1):
            now = datetime(2026, 6, 25, 15, 20)
            cutoff = pf._current_bar_period_start(now)        # 15:00
            self.assertFalse(datetime(2026, 6, 25, 15, 0) < cutoff)   # current-hour bar -> skip
            self.assertTrue(datetime(2026, 6, 25, 14, 0) < cutoff)    # previous-hour bar -> fetch


if __name__ == "__main__":
    unittest.main()
