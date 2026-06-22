"""Tests for Phase 3 cross-theme and crowding analyzers."""
import unittest
from unittest.mock import MagicMock, patch

from ml_engine.crowding_analyzer import analyze_crowding
from ml_engine.intent_router import route
from ml_engine.theme_cross_analyzer import analyze_cross_theme, detect_themes_in_query


class Phase3AnalyzerTests(unittest.TestCase):
    def test_detect_multiple_themes(self):
        themes = detect_themes_in_query("AI infrastructure and quantum computing demand")
        self.assertIn("ai_infrastructure", themes)
        self.assertIn("quantum_computing", themes)

    def test_cross_theme_intent(self):
        routed = route("Which interdependent themes are under-invested in AI and quantum?")
        self.assertEqual(routed.intent, "cross_theme")

    def test_crowding_intent(self):
        routed = route("Is my portfolio crowded and at bubble risk?")
        self.assertEqual(routed.intent, "crowding_risk")

    @patch("ml_engine.theme_cross_analyzer.resolve")
    def test_cross_theme_overlap(self, mock_resolve):
        mock_resolve.side_effect = lambda tid, _: {
            "ai_infrastructure": ["NVDA", "AMD"],
            "quantum_computing": ["IONQ", "NVDA"],
        }.get(tid, [])
        routed = route("AI infrastructure and quantum computing interdependent demand")
        tickers, meta = analyze_cross_theme(routed, MagicMock())
        self.assertIn("NVDA", meta["overlap_tickers"])
        self.assertIn("NVDA", tickers)

    @patch("ml_engine.crowding_analyzer.portfolio_tickers")
    @patch("ml_engine.crowding_analyzer.get_many")
    def test_crowding_score_range(self, mock_get_many, mock_portfolio):
        mock_portfolio.return_value = ["NVDA", "AMD"]
        mock_get_many.return_value = {
            "NVDA": {
                "tier": {"value": "speculative", "coverage": "full"},
                "momentum_3m": {"value": 0.3, "coverage": "full"},
                "news_score_30d": {"value": 0.4, "coverage": "full"},
            },
            "AMD": {
                "tier": {"value": "core", "coverage": "full"},
                "momentum_3m": {"value": 0.1, "coverage": "full"},
                "news_score_30d": {"value": 0.1, "coverage": "full"},
            },
        }
        routed = route("crowded bubble risk in my holdings")
        tickers, meta = analyze_crowding(routed, MagicMock())
        self.assertEqual(len(tickers), 2)
        self.assertGreaterEqual(meta["crowding_score"], 0)
        self.assertLessEqual(meta["crowding_score"], 1)


if __name__ == "__main__":
    unittest.main()
