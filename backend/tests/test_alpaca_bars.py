"""Tests for the Alpaca bars fetcher + provider dispatch (PRICE_HOURLY_SOURCE flag)."""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_ingestion.price_fetcher as pf


def _epoch_ms(iso_utc):
    return int(datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).timestamp() * 1000)


class AlpacaTimeframeTests(unittest.TestCase):
    def test_timeframe_mapping(self):
        with mock.patch.object(pf, "DATA_TIMESPAN", "hour"), mock.patch.object(pf, "DATA_MULTIPLIER", 1):
            self.assertEqual(pf._alpaca_timeframe(), "1Hour")
        with mock.patch.object(pf, "DATA_TIMESPAN", "minute"), mock.patch.object(pf, "DATA_MULTIPLIER", 5):
            self.assertEqual(pf._alpaca_timeframe(), "5Min")
        with mock.patch.object(pf, "DATA_TIMESPAN", "day"), mock.patch.object(pf, "DATA_MULTIPLIER", 1):
            self.assertEqual(pf._alpaca_timeframe(), "1Day")


class AlpacaParseTests(unittest.TestCase):
    def test_fetch_parses_polygon_shape_and_paginates(self):
        page1 = {"bars": [{"t": "2017-11-01T13:00:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 100, "n": 9, "vw": 1.4}],
                 "next_page_token": "tok2"}
        page2 = {"bars": [{"t": "2017-11-01T14:00:00Z", "o": 1.5, "h": 2.5, "l": 1.0, "c": 2.0, "v": 200}],
                 "next_page_token": None}
        calls = []

        def fake_get(url, headers, params, ticker):
            calls.append(params.get("page_token"))
            return page1 if params.get("page_token") is None else page2

        with mock.patch.object(pf, "ALPACA_API_KEY", "k"), mock.patch.object(pf, "ALPACA_SECRET_KEY", "s"), \
             mock.patch.object(pf, "_alpaca_get", side_effect=fake_get):
            bars = pf.fetch_alpaca_hourly("AAPL", datetime(2017, 11, 1), datetime(2017, 11, 2))

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0], {"t": _epoch_ms("2017-11-01T13:00:00Z"), "o": 1.0, "h": 2.0,
                                   "l": 0.5, "c": 1.5, "v": 100.0})
        self.assertEqual(bars[1]["t"], _epoch_ms("2017-11-01T14:00:00Z"))
        self.assertEqual(calls, [None, "tok2"])   # followed the cursor exactly once

    def test_missing_creds_returns_empty(self):
        with mock.patch.object(pf, "ALPACA_API_KEY", ""), mock.patch.object(pf, "ALPACA_SECRET_KEY", ""):
            self.assertEqual(pf.fetch_alpaca_hourly("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 2)), [])


class DispatcherTests(unittest.TestCase):
    def test_dispatch_follows_flag(self):
        s, e = datetime(2024, 1, 1), datetime(2024, 1, 2)
        with mock.patch.object(pf, "fetch_alpaca_hourly", return_value=["A"]) as fa, \
             mock.patch.object(pf, "fetch_massive_hourly", return_value=["M"]) as fm:
            with mock.patch.object(pf, "PRICE_HOURLY_SOURCE", "alpaca"):
                self.assertEqual(pf.fetch_hourly_bars("AAPL", s, e), ["A"])
            with mock.patch.object(pf, "PRICE_HOURLY_SOURCE", "massive"):
                self.assertEqual(pf.fetch_hourly_bars("AAPL", s, e), ["M"])
        self.assertEqual(fa.call_count, 1)
        self.assertEqual(fm.call_count, 1)


if __name__ == "__main__":
    unittest.main()
