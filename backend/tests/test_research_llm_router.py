"""Tests for research LLM tier routing."""
import unittest
from unittest.mock import patch

from ml_engine.intent_router import RoutedQuery, route
from ml_engine.research_llm_router import (
    decide,
    estimate_cost_for_tier,
    is_lookup_query,
    upgrade_offer,
)


class ResearchLlmRouterTests(unittest.TestCase):
    def test_lookup_query_skips_llm(self):
        routed = RoutedQuery(
            intent="ticker_outlook",
            tickers=["NVDA"],
            theme=None,
            raw_query="What is NVDA price target?",
            deep_research=False,
        )
        self.assertTrue(is_lookup_query(routed))
        d = decide(routed, {"NVDA": 0.9})
        self.assertEqual(d.tier, "lookup")

    @patch("ml_engine.research_llm_router._openai_available", return_value=True)
    def test_theme_rank_defaults_to_standard(self, _oa):
        routed = route("Rank quantum computing companies")
        d = decide(routed, {"IONQ": 0.8, "RGTI": 0.7})
        self.assertEqual(d.tier, "standard")
        self.assertIn("gpt-4o-mini", d.model or "")

    @patch("ml_engine.research_llm_router._openai_available", return_value=True)
    def test_use_premium_forces_premium_tier(self, _oa):
        routed = route("What's the outlook for NVDA?")
        d = decide(routed, {"NVDA": 0.9}, use_premium=True)
        self.assertEqual(d.tier, "premium")

    @patch("ml_engine.research_llm_router._openai_available", return_value=True)
    def test_high_complexity_auto_premium(self, _oa):
        routed = RoutedQuery(
            intent="event_spillover",
            tickers=["MU", "NVDA", "AMD", "AVGO"],
            theme=None,
            raw_query="Micron earnings impact on holdings",
            deep_research=True,
        )
        d = decide(routed, {"MU": 0.9, "NVDA": 0.3})
        self.assertEqual(d.tier, "premium")

    @patch("ml_engine.research_llm_router._openai_available", return_value=True)
    def test_upgrade_offer_after_standard(self, _oa):
        routed = route("What's the outlook for NVDA?")
        d = decide(routed, {"NVDA": 0.9})
        offer = upgrade_offer(d, routed.intent, 1)
        self.assertTrue(offer["available"])
        self.assertIsNotNone(offer.get("est_cost_usd"))

    def test_premium_estimate_has_cost(self):
        with patch("ml_engine.research_llm_router._openai_available", return_value=True):
            est = estimate_cost_for_tier("premium", "ticker_outlook", 1)
        self.assertIsNotNone(est["est_cost_usd"])
        self.assertGreater(est["est_cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
