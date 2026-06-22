"""Tests for sector screening (Phase 2b)."""
import unittest
from unittest.mock import MagicMock

from ml_engine.intent_router import route, is_stub_intent
from ml_engine.sector_analyzer import detect_sectors_in_query, resolve_sector_screen


class SectorAnalyzerTests(unittest.TestCase):
    def test_sector_screen_not_stub(self):
        self.assertFalse(is_stub_intent("sector_screen"))

    def test_detect_technology_sector(self):
        sectors = detect_sectors_in_query("Which technology sectors look undervalued?")
        self.assertIn("Technology", sectors)

    def test_no_false_tickers_in_outlook_query(self):
        routed = route("What's the outlook for NVIDIA over the next year?")
        self.assertIn("NVDA", routed.tickers)
        self.assertNotIn("WHAT", routed.tickers)
        self.assertNotIn("THE", routed.tickers)

    def test_sector_screen_intent(self):
        routed = route("Which sectors are undervalued right now?")
        self.assertEqual(routed.intent, "sector_screen")

    def test_resolve_sector_screen_returns_tickers(self):
        db = MagicMock()
        # list_sectors / constituents need real DB — smoke test resolve doesn't crash on empty mock
        routed = route("technology sector outlook")
        tickers, meta = resolve_sector_screen(routed, db)
        self.assertIsInstance(tickers, list)
        self.assertIsInstance(meta, dict)


if __name__ == "__main__":
    unittest.main()
