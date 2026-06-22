"""Tests for factor calibrator grid search."""
import unittest

import pandas as pd

from ml_engine.factor_calibrator import walk_forward_calibrate
from ml_engine.research_framework import STOCK_FACTOR_WEIGHTS, composite_stock_score


class FactorCalibratorTests(unittest.TestCase):
    def test_walk_forward_returns_weights(self):
        rows = []
        for d in ("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"):
            for i, ticker in enumerate(["A", "B", "C", "D"]):
                rows.append({
                    "as_of_date": d,
                    "ticker": ticker,
                    "quality": 0.2 + i * 0.2,
                    "upside": 0.1 + i * 0.15,
                    "news": 0.3,
                    "momentum": 0.1 + i * 0.1,
                    "forward_return": 0.05 * i,
                })
        df = pd.DataFrame(rows)
        weights, meta = walk_forward_calibrate(df, folds=2)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=1)
        self.assertIn("folds", meta)

    def test_get_stock_factor_weights_default(self):
        from ml_engine.research_framework import get_stock_factor_weights

        w = get_stock_factor_weights()
        self.assertEqual(w, STOCK_FACTOR_WEIGHTS)

    def test_composite_with_custom_weights(self):
        facts = {
            "quality": {"value": 0.8, "coverage": "full"},
            "upside_pct": {"value": 0.2, "coverage": "full"},
            "news_score_30d": {"value": 0.3, "coverage": "full"},
            "momentum_3m": {"value": 0.1, "coverage": "full"},
            "tier": {"value": "core", "coverage": "full"},
        }
        w = {"quality": 1.0, "upside": 0.0, "news": 0.0, "momentum": 0.0}
        score = composite_stock_score(facts, weights=w)
        self.assertGreater(score, 0.5)


if __name__ == "__main__":
    unittest.main()
