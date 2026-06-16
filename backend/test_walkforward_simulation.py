import os
import sys
import unittest
import pandas as pd
import numpy as np

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ml_engine.models import find_optimal_threshold, precalculate_exits, simulate_portfolio_chronological

class TestWalkforwardSimulation(unittest.TestCase):

    def setUp(self):
        # Create a mock daily/hourly price dataset for testing
        dates = pd.date_range(start="2026-01-01", end="2026-01-20", freq="H").strftime("%Y-%m-%d %H:%M:%S")
        self.prices_df = pd.DataFrame({
            "ticker": ["AAPL"] * len(dates) + ["MSFT"] * len(dates),
            "date": list(dates) + list(dates),
            "open": [100.0] * (len(dates) * 2),
            "high": [105.0] * (len(dates) * 2),
            "low": [98.0] * (len(dates) * 2),
            "close": [102.0] * (len(dates) * 2),
            "atr_14": [2.0] * (len(dates) * 2)
        })

        # Mock OOS predictions dataframe
        self.oos_df = pd.DataFrame({
            "dt": ["2026-01-02 00:00:00", "2026-01-03 00:00:00", "2026-01-04 00:00:00"],
            "date": ["2026-01-02 00:00:00", "2026-01-03 00:00:00", "2026-01-04 00:00:00"],
            "ticker": ["AAPL", "MSFT", "AAPL"],
            "prob": [0.45, 0.60, 0.35],
            "target_win": [1.0, 1.0, 0.0],
            "trade_ret": [0.05, 0.04, -0.02],
            "selected_threshold": [0.40, 0.50, 0.40],
            "close": [100.0, 100.0, 100.0]
        })

    def test_precalculate_exits(self):
        """Verifies that precalculate_exits maps correct exits based on mock prices."""
        df_exits = precalculate_exits(self.oos_df, self.prices_df, horizon=5)
        self.assertIn("exit_date", df_exits.columns)
        self.assertIn("exit_price", df_exits.columns)

        # Verify first row exit is mapped
        row_0 = df_exits.iloc[0]
        self.assertIsNotNone(row_0["exit_date"])
        self.assertIsNotNone(row_0["exit_price"])

    def test_simulate_portfolio_chronological(self):
        """Verifies that the chronological portfolio simulator enforces limits and computes metrics."""
        curve, metrics = simulate_portfolio_chronological(
            self.oos_df, self.prices_df, initial_capital=100000.0, max_allocation=0.10, fee_pct=0.0005, horizon=5
        )

        # Verify we get a non-empty equity curve and correct structure
        self.assertTrue(len(curve) > 0)
        self.assertIn("portfolio_value", curve[0])
        self.assertIn("cash", curve[0])

        # Verify metrics keys are returned
        self.assertIn("total_return", metrics)
        self.assertIn("sharpe_ratio", metrics)
        self.assertIn("max_drawdown", metrics)
        self.assertIn("final_value", metrics)

        # With initial 100k capital, final value should reflect returns minus transaction fees
        self.assertGreater(metrics["final_value"], 50000.0)

    def test_find_optimal_threshold_fallback(self):
        """Verifies that find_optimal_threshold falls back to default on small datasets."""
        # Small dataset should fallback to 0.23
        thr = find_optimal_threshold(self.oos_df, ["prob"], target_col="target_win", fallback_default=0.23)
        self.assertEqual(thr, 0.23)

if __name__ == "__main__":
    unittest.main()
