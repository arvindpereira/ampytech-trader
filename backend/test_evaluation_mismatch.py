import unittest
import pandas as pd
import numpy as np
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml_engine.models import simulate_portfolio_chronological

class TestEvaluationMismatch(unittest.TestCase):
    def setUp(self):
        # Setup synthetic OOS data: one buy signal for ticker AAA on 2026-06-01
        self.oos_df = pd.DataFrame([
            {"date": "2026-06-01", "ticker": "AAA", "close": 100.0, "atr_14": 2.0, "prob": 0.5, "selected_threshold": 0.4, "target_win": 1.0, "trade_ret": 0.05},
            {"date": "2026-06-02", "ticker": "AAA", "close": 104.0, "atr_14": 2.0, "prob": 0.0, "selected_threshold": 0.4, "target_win": 0.0, "trade_ret": 0.0},
            {"date": "2026-06-03", "ticker": "AAA", "close": 105.0, "atr_14": 2.0, "prob": 0.0, "selected_threshold": 0.4, "target_win": 0.0, "trade_ret": 0.0},
            {"date": "2026-06-04", "ticker": "AAA", "close": 107.0, "atr_14": 2.0, "prob": 0.0, "selected_threshold": 0.4, "target_win": 0.0, "trade_ret": 0.0},
            {"date": "2026-06-05", "ticker": "AAA", "close": 111.0, "atr_14": 2.0, "prob": 0.0, "selected_threshold": 0.4, "target_win": 0.0, "trade_ret": 0.0},
        ])

        # Setup synthetic prices: AAA open/high/low/close from 2026-06-01 to 2026-06-05
        # atr_14 = 2.0.
        # stop_pct = (2.0 * atr) / close = 4.0 / 100 = 4% (0.04) under default mult = 2.0.
        # TP = stop_pct * tp_mult. Under mult = 2.5, TP = 10% (target price = 110.0).
        # Under custom mult = 1.0, TP = 4% (target price = 104.0).
        self.prices_df = pd.DataFrame([
            {"ticker": "AAA", "date": "2026-06-01", "open": 100.0, "high": 102.0, "low": 99.0, "close": 100.0, "atr_14": 2.0},
            {"ticker": "AAA", "date": "2026-06-02", "open": 100.0, "high": 105.0, "low": 98.0, "close": 104.0, "atr_14": 2.0},
            {"ticker": "AAA", "date": "2026-06-03", "open": 104.0, "high": 106.0, "low": 102.0, "close": 105.0, "atr_14": 2.0},
            {"ticker": "AAA", "date": "2026-06-04", "open": 105.0, "high": 108.0, "low": 104.0, "close": 107.0, "atr_14": 2.0},
            {"ticker": "AAA", "date": "2026-06-05", "open": 107.0, "high": 112.0, "low": 106.0, "close": 111.0, "atr_14": 2.0},
        ])

    def test_default_stop_tp_params(self):
        # Under default parameters (SHORT_TERM_ATR_STOP_MULT=2.0, SHORT_TERM_TP_MULT=2.5, stop_min=0.015, stop_max=0.05):
        # stop_pct = 4.0 / 100.0 = 4%.
        # tp_pct = 10%. Target = 110.0.
        # Target is hit on 2026-06-05 (high is 112.0).
        curve, metrics = simulate_portfolio_chronological(
            self.oos_df, self.prices_df, initial_capital=100000.0, max_allocation=0.10, fee_pct=0.0, horizon=5
        )
        self.assertIsNotNone(curve)

        # Check active trades: since we exited on 2026-06-05, cash should be back to 100,000 + gains
        # We allocated 10% of 100k = 10,000. Shares = 10,000 / 100 = 100 shares.
        # Target price is 110.0. Realized value = 100 * 110 = 11,000.
        # Cash should be 90,000 + 11,000 = 101,000 on 2026-06-05.
        final_value = curve[-1]["portfolio_value"]
        self.assertEqual(final_value, 101000.0)
        self.assertEqual(curve[-1]["date"], "2026-06-05")

    def test_custom_stop_tp_params(self):
        # Under custom parameters (tp_mult=1.0):
        # stop_pct = 4%. tp_pct = 4%. Target = 104.0.
        # Target is hit on 2026-06-02 (high is 105.0 >= 104.0).
        # We allocate 10k = 100 shares. Realized value = 100 * 104 = 10,400.
        # Cash should be 90,000 + 10,400 = 100,400 on 2026-06-02.
        # For dates 2026-06-02 to 2026-06-05, we hold no active trades, so equity remains 100,400.
        curve, metrics = simulate_portfolio_chronological(
            self.oos_df, self.prices_df, initial_capital=100000.0, max_allocation=0.10, fee_pct=0.0, horizon=5,
            stop_max=0.05, stop_min=0.015, atr_mult=2.0, tp_mult=1.0
        )
        self.assertIsNotNone(curve)

        # Verify that the trade exited on 2026-06-02 and portfolio value remained flat at 100,400 afterwards
        final_value = curve[-1]["portfolio_value"]
        self.assertEqual(final_value, 100400.0)

        # Verify day 2 portfolio value was 100,400 (which confirms exit on 2026-06-02)
        day_2_val = [c["portfolio_value"] for c in curve if c["date"] == "2026-06-02"][0]
        self.assertEqual(day_2_val, 100400.0)

if __name__ == "__main__":
    unittest.main()
