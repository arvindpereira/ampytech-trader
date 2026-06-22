"""Tests for web search fetcher cache layer."""
import json
import unittest
from unittest.mock import MagicMock, patch

from data_ingestion.web_search_fetcher import _query_hash, build_research_query, search_web


class WebSearchFetcherTests(unittest.TestCase):
    def test_query_hash_stable(self):
        self.assertEqual(_query_hash("NVDA outlook"), _query_hash("NVDA outlook"))

    def test_build_research_query_includes_tickers(self):
        q = build_research_query("outlook", ["NVDA", "AMD"])
        self.assertIn("NVDA", q)
        self.assertIn("outlook", q)

    @patch("data_ingestion.web_search_fetcher.SEARCH_API_KEY", "")
    def test_search_web_empty_without_key(self):
        self.assertEqual(search_web("test query"), [])

    @patch("data_ingestion.web_search_fetcher.SEARCH_API_KEY", "test-key")
    def test_cache_hit(self):
        db = MagicMock()
        with patch("data_ingestion.web_search_fetcher._cache_get") as mock_get:
            mock_get.return_value = [{"title": "cached"}]
            out = search_web("q", db=db)
        self.assertEqual(out[0]["title"], "cached")


if __name__ == "__main__":
    unittest.main()
