"""Tests for Phase 2c BM25 news retrieval."""
import unittest
from unittest.mock import MagicMock

from ml_engine.news_retriever import _BM25, _tokenize, search_news_bm25


class NewsRetrieverTests(unittest.TestCase):
    def test_tokenize(self):
        self.assertEqual(_tokenize("NVDA Memory Cycle 2026"), ["nvda", "memory", "cycle", "2026"])

    def test_bm25_ranks_relevant_higher(self):
        corpus = [
            "nvidia beats earnings on ai demand",
            "weather forecast sunny weekend",
            "micron memory pricing improves dram cycle",
        ]
        scores = _BM25(corpus).score("memory cycle micron")
        self.assertGreater(scores[2], scores[1])
        self.assertGreater(scores[2], scores[0])

    def test_search_empty_without_rows(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        out = search_news_bm25("memory cycle", ["MU"], db=db)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
