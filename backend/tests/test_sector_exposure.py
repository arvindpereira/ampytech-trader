"""Tests for consolidated sector exposure analyzer."""
import unittest
from unittest.mock import MagicMock, patch

from ml_engine.sector_exposure_analyzer import (
    _revenue_driver,
    analyze_sector_exposure,
    load_sp500_weights,
)


class SectorExposureTests(unittest.TestCase):
    def test_sp500_weights_load(self):
        w = load_sp500_weights()
        self.assertIn("Technology", w.get("weights", {}))
        self.assertAlmostEqual(sum(w["weights"].values()), 1.0, places=2)

    def test_revenue_driver_semiconductor(self):
        hint = _revenue_driver("Technology", "Semiconductors", "NVDA")
        self.assertIn("Semiconductor", hint)

    @patch("ml_engine.sector_exposure_analyzer.collect_consolidated_positions")
    @patch("ml_engine.sector_exposure_analyzer._metadata_for")
    def test_alerts_on_overweight(self, mock_meta, mock_pos):
        mock_pos.return_value = [
            {"ticker": "NVDA", "market_value": 60000, "shares": 100, "sources": ["trading_account"], "accounts": []},
            {"ticker": "JPM", "market_value": 40000, "shares": 200, "sources": ["external"], "accounts": ["RH"]},
        ]

        def meta_side(db, ticker):
            if ticker == "NVDA":
                return "Technology", "Semiconductors"
            return "Financial Services", "Banks"

        mock_meta.side_effect = meta_side
        db = MagicMock()
        with patch("data_ingestion.ticker_metadata_fetcher.refresh_tickers"):
            with patch("data_ingestion.sector_catalog_refresh._backfill_metadata_sectors"):
                result = analyze_sector_exposure(db, refresh_metadata=False)

        self.assertEqual(result["total_equity_value"], 100000)
        tech = next(s for s in result["sectors"] if s["sector"] == "Technology")
        self.assertGreater(tech["portfolio_weight"], tech["benchmark_weight"])
        self.assertTrue(any(a["sector"] == "Technology" for a in result["alerts"]))


if __name__ == "__main__":
    unittest.main()
