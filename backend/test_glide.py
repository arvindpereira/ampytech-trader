import unittest
import sys
import os

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ml_engine.glide import get_standardized_score, compute_defensive_coefficient, blend_portfolios

class TestGlide(unittest.TestCase):
    def test_standardized_score(self):
        # 50 composite index should map to 0 z-score
        self.assertEqual(get_standardized_score(50.0), 0.0)
        # 65 composite index should map to +1 z-score
        self.assertEqual(get_standardized_score(65.0), 1.0)
        # 35 composite index should map to -1 z-score
        self.assertEqual(get_standardized_score(35.0), -1.0)

    def test_compute_defensive_coefficient(self):
        # Parameters (balanced preset)
        theta = 0.85
        k = 2.0
        L = 0.0
        U = 0.90
        gamma = 0.25

        # z far below theta should result in L (0.0)
        d_low = compute_defensive_coefficient(-4.0, 0.0, theta, k, L, U, gamma)
        self.assertAlmostEqual(d_low, L, places=3)

        # z far above theta should result in U (0.90)
        d_high = compute_defensive_coefficient(5.0, 0.0, theta, k, L, U, gamma)
        self.assertAlmostEqual(d_high, U, places=3)

        # Trend gate shift: positive trend_score (+1.0) shifts effective theta to 0.85 + 0.25 = 1.10
        # If z = 0.95, it's above normal theta (0.85) but below shifted theta (1.10).
        # Therefore, the defensive coefficient should be lower when trend_score is positive (uptrend).
        d_no_trend = compute_defensive_coefficient(0.95, 0.0, theta, k, L, U, gamma)
        d_with_trend = compute_defensive_coefficient(0.95, 1.0, theta, k, L, U, gamma)
        self.assertTrue(d_with_trend < d_no_trend)

    def test_blend_portfolios_simplex_preservation(self):
        agg = {"SPY": 0.6, "QQQ": 0.4}
        def_ptf = {"GLD": 0.5, "TLT": 0.5}

        # Blend with d = 0.0 (all aggressive)
        blend_0 = blend_portfolios(agg, def_ptf, 0.0)
        self.assertAlmostEqual(sum(blend_0.values()), 1.0)
        self.assertEqual(blend_0.get("SPY", 0.0), 0.6)
        self.assertEqual(blend_0.get("GLD", 0.0), 0.0)

        # Blend with d = 1.0 (all defensive)
        blend_1 = blend_portfolios(agg, def_ptf, 1.0)
        self.assertAlmostEqual(sum(blend_1.values()), 1.0)
        self.assertEqual(blend_1.get("TLT", 0.0), 0.5)
        self.assertEqual(blend_1.get("QQQ", 0.0), 0.0)

        # Blend with d = 0.5 (half/half)
        blend_mid = blend_portfolios(agg, def_ptf, 0.5)
        self.assertAlmostEqual(sum(blend_mid.values()), 1.0)
        self.assertEqual(blend_mid.get("SPY", 0.0), 0.3)
        self.assertEqual(blend_mid.get("GLD", 0.0), 0.25)

if __name__ == "__main__":
    unittest.main()
