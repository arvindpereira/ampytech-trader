import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_STORAGE_DIR", tempfile.mkdtemp(prefix="ampy_wg_test_"))

from app.services.account_wargame import _simulate


class TestWargameSimulation(unittest.TestCase):
    def test_curve_starts_at_one_and_holds_with_returns(self):
        idx = pd.date_range("2024-01-01", periods=4, freq="D")
        piv = pd.DataFrame({"A": [100, 110, 121, 133.1]}, index=idx)   # +10%/day
        curve, cov = _simulate({"A": 1.0}, 0.0, piv, rebalance_days=21)
        self.assertAlmostEqual(curve[0], 1.0)
        self.assertAlmostEqual(curve[-1], 1.331, places=3)
        self.assertAlmostEqual(cov, 1.0)

    def test_partial_entry_holds_in_cash_until_listing(self):
        """A name that only starts trading mid-window waits in cash, then joins at the next
        rebalance and contributes its return from listing."""
        idx = pd.date_range("2024-01-01", periods=6, freq="D")
        piv = pd.DataFrame({"A": [100, 100, 100, 100, 100, 100],          # flat
                            "B": [np.nan, np.nan, np.nan, 50, 55, 60]},    # lists day 3, +20%
                           index=idx)
        curve, cov = _simulate({"A": 0.5, "B": 0.5}, 0.0, piv, rebalance_days=3)
        self.assertAlmostEqual(curve[0], 1.0)        # B's weight parked in cash at start
        self.assertAlmostEqual(curve[3], 1.0)        # rebalance brings B in at its listing price
        self.assertAlmostEqual(curve[5], 1.10, places=4)   # half the book rode B +20%
        self.assertAlmostEqual(cov, 1.0)             # both eventually investable

    def test_rebalancing_trims_the_winner(self):
        """Monthly rebalancing should reduce a runaway winner's end weight vs pure buy-and-hold."""
        idx = pd.date_range("2024-01-01", periods=44, freq="D")
        a = [100 * (1.05 ** i) for i in range(44)]   # compounding winner
        b = [100.0] * 44                              # flat
        piv = pd.DataFrame({"A": a, "B": b}, index=idx)
        rebal, _ = _simulate({"A": 0.5, "B": 0.5}, 0.0, piv, rebalance_days=21)
        hold, _ = _simulate({"A": 0.5, "B": 0.5}, 0.0, piv, rebalance_days=10_000)  # never rebalances
        self.assertLess(rebal[-1], hold[-1])         # rebalancing sells the winner down → lower end value

    def test_uncovered_weight_stays_cash(self):
        idx = pd.date_range("2024-01-01", periods=3, freq="D")
        piv = pd.DataFrame({"A": [100, 100, 100]}, index=idx)   # only A has data
        curve, cov = _simulate({"A": 0.5, "MISSING": 0.5}, 0.0, piv)
        self.assertAlmostEqual(cov, 0.5)             # MISSING never trades → excluded from coverage
        self.assertAlmostEqual(curve[-1], 1.0)       # 0.5 A (flat) + 0.5 cash


if __name__ == "__main__":
    unittest.main()
