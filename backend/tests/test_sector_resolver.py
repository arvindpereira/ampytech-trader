"""Tests for GICS sector handbook seed (research_sectors.json)."""
import unittest

from ml_engine.sector_resolver import (
    etf_map,
    find_by_sector,
    list_sector_entries,
    match_sectors_in_query,
    seed_tickers,
    sector_brief,
)
from ml_engine.sector_analyzer import detect_sectors_in_query


class SectorResolverTests(unittest.TestCase):
    def test_catalog_has_eleven_sectors(self):
        self.assertEqual(len(list_sector_entries()), 11)

    def test_etf_map_covers_all_sectors(self):
        m = etf_map()
        self.assertEqual(m.get("Technology"), "XLK")
        self.assertEqual(m.get("Consumer Cyclical"), "XLY")
        self.assertEqual(len(m), 11)

    def test_match_consumer_staples(self):
        found = match_sectors_in_query("Which consumer staples look defensive?")
        self.assertIn("Consumer Defensive", found)

    def test_match_information_technology_phrase(self):
        found = detect_sectors_in_query("information technology sector outlook")
        self.assertIn("Technology", found)

    def test_seed_tickers_technology(self):
        seeds = seed_tickers("Technology")
        self.assertIn("NVDA", seeds)
        self.assertIn("MSFT", seeds)

    def test_sector_brief_includes_representatives(self):
        brief = sector_brief("Financial Services")
        self.assertIsNotNone(brief)
        self.assertEqual(brief["etf_spdr"], "XLF")
        self.assertTrue(any("JPM" in line for line in brief["representative_names"]))

    def test_gics_name_lookup(self):
        entry = find_by_sector("Health Care")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["sector"], "Healthcare")


if __name__ == "__main__":
    unittest.main()
