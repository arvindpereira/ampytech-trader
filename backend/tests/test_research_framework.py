"""Tests for documented research scoring framework."""
import unittest

from ml_engine.research_framework import (
    aggregate_sector_metrics,
    compute_internal_target,
    composite_stock_score,
    stock_component_scores,
)


class _Snap:
    def __init__(self, ticker, upside, mom, news, qual, sector="Technology"):
        self.ticker = ticker
        self.upside_pct = upside
        self.momentum_3m = mom
        self.news_score_30d = news
        self.quality = qual
        self.sector = sector


class ResearchFrameworkTests(unittest.TestCase):
    def test_median_not_mean_for_sector(self):
        snaps = [
            _Snap("A", 0.5, 0.1, 0.2, 0.8),
            _Snap("B", -0.1, 0.05, 0.0, 0.6),
            _Snap("OUT", 0.9, 0.5, 0.4, 0.9),
        ]
        agg = aggregate_sector_metrics(snaps)
        self.assertAlmostEqual(agg["median_upside_pct"], 0.5)
        self.assertNotAlmostEqual(agg["median_upside_pct"], 0.433, places=2)

    def test_breadth_metrics(self):
        snaps = [_Snap("A", 0.2, 0.1, 0, 0.5), _Snap("B", -0.1, -0.2, 0, 0.4)]
        agg = aggregate_sector_metrics(snaps)
        self.assertEqual(agg["breadth_upside_positive"], 0.5)

    def test_internal_target_momentum_tilt(self):
        base = compute_internal_target(100.0, 8, None, 90.0)
        tilt = compute_internal_target(100.0, 8, 0.2, 90.0)
        self.assertGreater(tilt["target_price"], base["target_price"])

    def test_composite_score_ordering(self):
        strong = {
            "quality": {"value": 0.9, "coverage": "full"},
            "upside_pct": {"value": 0.3, "coverage": "full"},
            "news_score_30d": {"value": 0.5, "coverage": "full"},
            "momentum_3m": {"value": 0.2, "coverage": "full"},
            "tier": {"value": "core", "coverage": "full"},
        }
        weak = {
            "quality": {"value": 0.2, "coverage": "full"},
            "upside_pct": {"value": -0.2, "coverage": "full"},
            "news_score_30d": {"value": -0.3, "coverage": "full"},
            "momentum_3m": {"value": -0.2, "coverage": "full"},
            "tier": {"value": "value_trap", "coverage": "full"},
        }
        self.assertGreater(composite_stock_score(strong), composite_stock_score(weak))


if __name__ == "__main__":
    unittest.main()
