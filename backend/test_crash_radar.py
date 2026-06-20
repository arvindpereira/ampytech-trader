import unittest
import sys
import os
import pandas as pd
import numpy as np

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import tempfile
_temp_dir = tempfile.TemporaryDirectory()
os.environ["DATA_STORAGE_DIR"] = _temp_dir.name

from ml_engine.crash_radar import normalize_value, get_rolling_percentile, compute_composite_index
from app.database import init_db, SessionLocal, MacroIndicator, CrashRiskSnapshot

class TestCrashRadar(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        # Clean up macro indicators before tests
        self.db.query(MacroIndicator).delete()
        self.db.query(CrashRiskSnapshot).delete()
        self.db.commit()

    def tearDown(self):
        self.db.query(MacroIndicator).delete()
        self.db.query(CrashRiskSnapshot).delete()
        self.db.commit()
        self.db.close()

    def test_normalize_value(self):
        # Test basic linear mapping
        self.assertEqual(normalize_value(50, 0, 100), 50.0)
        self.assertEqual(normalize_value(75, 50, 100), 50.0)
        
        # Test clipping
        self.assertEqual(normalize_value(120, 0, 100), 100.0)
        self.assertEqual(normalize_value(-20, 0, 100), 0.0)

        # Test invert
        self.assertEqual(normalize_value(25, 0, 100, invert=True), 75.0)

        # Test None value
        self.assertEqual(normalize_value(None, 0, 100), 50.0)

    def test_get_rolling_percentile(self):
        # Empty history
        self.assertIsNone(get_rolling_percentile(10, pd.Series(dtype=float)))

        # History too short
        history = pd.Series(np.linspace(1, 10, 20))
        self.assertIsNone(get_rolling_percentile(5, history, min_len=30))

        # Long enough history
        history = pd.Series(np.linspace(1, 100, 150))
        # Value at 50 should be 50th percentile (or near it due to winsorization)
        pct = get_rolling_percentile(50.0, history, min_len=100)
        self.assertIsNotNone(pct)
        self.assertTrue(45.0 <= pct <= 55.0)

        # Extreme winsorization clip check
        pct_high = get_rolling_percentile(500.0, history, min_len=100)
        self.assertTrue(98.0 <= pct_high <= 100.0)

    def test_compute_composite_index(self):
        # Seed dummy indicator data for a few dates
        dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
        indicators = ["cape", "buffett_indicator", "term_spread_10y3m", "fed_funds", "yield_spread",
                      "excess_bond_premium", "ebp_recession_prob", "hy_spread", "ig_spread",
                      "nfci", "nfci_leverage", "sloos_tightening", "building_permits",
                      "initial_claims_4w", "sahm_indicator", "margin_debt_quarterly"]
        
        # Insert 110 records per indicator to meet minimum rolling percentile requirement
        base_date = pd.to_datetime("2026-01-01")
        for ind in indicators:
            for i in range(110):
                d_str = (base_date + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
                val = 15.0 if ind == "cape" else 1.5
                self.db.add(MacroIndicator(
                    date=d_str,
                    indicator_name=ind,
                    value=val
                ))
        self.db.commit()

        # Compute index for latest date
        latest_date = (base_date + pd.Timedelta(days=109)).strftime("%Y-%m-%d")
        snapshot, breakdown = compute_composite_index(as_of_date=latest_date)

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.as_of_date.strftime("%Y-%m-%d") if hasattr(snapshot.as_of_date, 'strftime') else snapshot.as_of_date, latest_date)
        self.assertTrue(0.0 <= snapshot.composite_index <= 100.0)
        self.assertIn(snapshot.risk_band, ["Calm", "Elevated", "High", "Extreme"])
        self.assertIn(snapshot.current_posture, ["Normal", "Froth", "De-Risk", "Protect", "Deploy", "Recover"])

if __name__ == "__main__":
    unittest.main()
