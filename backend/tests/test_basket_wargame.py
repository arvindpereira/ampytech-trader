"""Tests for the crash war-game sleeve builders + engine generalization.

Covers: build_basket_path (hybrid real + beta-proxy, coverage report, weight renormalization),
build_defense_path (tlt/brkb/cash/blend), and the regression guard that the generalized
_simulate_curve reproduces the original SPY/TLT result when RISK=SPY and DEF=TLT.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.connection import SessionLocal, Base, engine
from app.database.models import DailyPrice
import ml_engine.wargame as wg
from ml_engine.basket import build_basket_path, build_defense_path


def _seed_daily(db, ticker, dates, prices):
    for d, p in zip(dates, prices):
        db.add(DailyPrice(ticker=ticker, date=d.strftime("%Y-%m-%d"), open=p, high=p, low=p,
                          close=p, volume=1000.0))
    db.commit()


class BasketWargameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.dates = list(pd.bdate_range("2023-01-02", periods=120))
        rng = np.random.RandomState(7)
        cls.spy = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, len(cls.dates)))
        db = SessionLocal()
        for t in ("SPY", "AAPL", "TLT", "BRK.B"):
            db.query(DailyPrice).filter(DailyPrice.ticker == t).delete()
        db.commit()
        # SPY + AAPL + defensive assets get real history; "GHOST" intentionally has none → proxied.
        _seed_daily(db, "SPY", cls.dates, cls.spy)
        _seed_daily(db, "AAPL", cls.dates, 50 * np.cumprod(1 + rng.normal(0.0005, 0.013, len(cls.dates))))
        _seed_daily(db, "TLT", cls.dates, 95 * np.cumprod(1 + rng.normal(-0.0001, 0.004, len(cls.dates))))
        _seed_daily(db, "BRK.B", cls.dates, 300 * np.cumprod(1 + rng.normal(0.0004, 0.008, len(cls.dates))))
        db.close()

    def setUp(self):
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_basket_hybrid_real_and_proxy(self):
        path, cov = build_basket_path(self.db, {"AAPL": 0.6, "GHOST": 0.4}, self.dates, self.spy, era=None)
        self.assertEqual(len(path), len(self.dates))
        self.assertEqual(cov["names"]["AAPL"]["source"], "real")
        self.assertEqual(cov["names"]["GHOST"]["source"], "proxy")
        self.assertAlmostEqual(cov["real_weight"], 0.6, places=4)
        self.assertAlmostEqual(cov["proxy_weight"], 0.4, places=4)
        self.assertAlmostEqual(cov["real_weight"] + cov["proxy_weight"], 1.0, places=6)
        self.assertAlmostEqual(float(path[0]), 100.0, places=6)   # normalized to 100

    def test_basket_weights_renormalize(self):
        # Weights that don't sum to 1 (cash excluded) must renormalize over the included names.
        _, cov = build_basket_path(self.db, {"AAPL": 0.3, "GHOST": 0.1}, self.dates, self.spy, era=None)
        self.assertAlmostEqual(cov["real_weight"], 0.75, places=4)   # 0.3 / 0.4
        self.assertAlmostEqual(cov["proxy_weight"], 0.25, places=4)

    def test_basket_all_real(self):
        _, cov = build_basket_path(self.db, {"AAPL": 1.0}, self.dates, self.spy, era=None)
        self.assertEqual(cov["proxy_weight"], 0.0)
        self.assertEqual(cov["real_weight"], 1.0)

    def test_defense_variants(self):
        for spec in ("tlt", "brkb", "cash", {"tlt": 0.5, "brkb": 0.5}):
            path = build_defense_path(self.db, spec, self.dates, self.spy, era=None)
            self.assertEqual(len(path), len(self.dates))
            self.assertAlmostEqual(float(path[0]), 100.0, places=6)
            self.assertTrue(np.all(np.isfinite(path)))

    def test_defense_missing_asset_falls_back_to_cash(self):
        # An asset with no rows must not blow up — it degrades to the cash-like proxy.
        self.db.query(DailyPrice).filter(DailyPrice.ticker == "TLT").delete()
        self.db.commit()
        path = build_defense_path(self.db, "tlt", self.dates, self.spy, era=None)
        self.assertTrue(np.all(np.isfinite(path)) and path[0] == 100.0)
        # restore for other tests
        _seed_daily(self.db, "TLT", self.dates, 95 * np.ones(len(self.dates)))

    def test_simulate_curve_regression(self):
        n = 80
        rng = np.random.RandomState(3)
        df = pd.DataFrame({
            "date": pd.bdate_range("2024-01-01", periods=n),
            "SPY": 100 * np.cumprod(1 + rng.normal(0, 0.011, n)),
            "TLT": 100 * np.cumprod(1 + rng.normal(0, 0.004, n)),
        })
        d = np.linspace(0.0, 0.6, n)
        old, t_old = wg._simulate_curve(df, d)
        df2 = df.copy()
        df2["RISK"] = df["SPY"].values
        df2["DEF"] = df["TLT"].values
        new, t_new = wg._simulate_curve(df2, d)
        self.assertTrue(np.allclose(old, new))
        self.assertAlmostEqual(t_old, t_new, places=9)


if __name__ == "__main__":
    unittest.main()
