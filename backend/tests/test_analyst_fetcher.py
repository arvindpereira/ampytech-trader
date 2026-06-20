"""Tests for equity advisor price backfill on cached analyst forecasts."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, init_db, AnalystForecast, DailyPrice
from data_ingestion.analyst_fetcher import latest_or_refresh, _patch_forecast_price


class AnalystFetcherPricePatchTest(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        self.ticker = "ZZTEST"
        self.today = date.today().isoformat()
        self.db.query(AnalystForecast).filter(AnalystForecast.ticker == self.ticker).delete()
        self.db.query(DailyPrice).filter(DailyPrice.ticker == self.ticker).delete()
        self.db.add(DailyPrice(
            ticker=self.ticker, date=self.today, open=10.0, high=10.5, low=9.5, close=10.25, volume=1000.0,
        ))
        self.db.add(AnalystForecast(
            ticker=self.ticker, as_of_date=self.today, current_price=None,
            target_mean=12.0, source="massive:benzinga",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.query(AnalystForecast).filter(AnalystForecast.ticker == self.ticker).delete()
        self.db.query(DailyPrice).filter(DailyPrice.ticker == self.ticker).delete()
        self.db.commit()
        self.db.close()

    def test_patch_forecast_price_fills_missing_current_price(self):
        row = self.db.query(AnalystForecast).filter(AnalystForecast.ticker == self.ticker).first()
        patched = _patch_forecast_price(self.db, row)
        self.assertIsNotNone(patched)
        self.assertAlmostEqual(patched.current_price, 10.25)
        self.assertAlmostEqual(patched.upside_pct, (12.0 - 10.25) / 10.25)

    def test_latest_or_refresh_repairs_stale_null_price_row(self):
        row = latest_or_refresh(self.ticker, self.db, stale_days=1)
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row.current_price, 10.25)


if __name__ == "__main__":
    unittest.main()
