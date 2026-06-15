import os
import sys
import unittest
from unittest.mock import MagicMock
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal, CongressDisclosure, InsiderDisclosure
from app.core.config import INSIDER_LOOKBACK_DAYS, CONGRESS_LOOKBACK_DAYS, HEDGE_MODE
from ml_engine.features import build_features_for_df
from backtesting.backtest import get_hedge_info

class TestAlternativeData(unittest.TestCase):
    
    def setUp(self):
        self.db = SessionLocal()
        
    def tearDown(self):
        self.db.close()
        
    def test_database_insertion(self):
        """Verifies that we can insert and retrieve Congress and Insider disclosures correctly."""
        # Clean up any existing test records if necessary (or just test adding new ones)
        test_ticker = "TEST_TICKER_XYZ"
        
        # Add Congress Disclosure
        c_disc = CongressDisclosure(
            ticker=test_ticker,
            date="2026-06-01",
            politician_name="Senator Bob",
            chamber="Senate",
            transaction_type="purchase",
            amount_range="$15,001 - $50,000",
            estimated_value=32500.0
        )
        self.db.add(c_disc)
        
        # Add Insider Disclosure
        i_disc = InsiderDisclosure(
            ticker=test_ticker,
            date="2026-06-02",
            insider_name="Alice CEO",
            relationship="CEO",
            transaction_type="purchase",
            shares=1000.0,
            share_price=100.0,
            total_value=100000.0
        )
        self.db.add(i_disc)
        
        self.db.commit()
        
        # Retrieve and verify
        retrieved_c = self.db.query(CongressDisclosure).filter_by(ticker=test_ticker).first()
        self.assertIsNotNone(retrieved_c)
        self.assertEqual(retrieved_c.politician_name, "Senator Bob")
        self.assertEqual(retrieved_c.estimated_value, 32500.0)
        
        retrieved_i = self.db.query(InsiderDisclosure).filter_by(ticker=test_ticker).first()
        self.assertIsNotNone(retrieved_i)
        self.assertEqual(retrieved_i.insider_name, "Alice CEO")
        self.assertEqual(retrieved_i.total_value, 100000.0)
        
        # Clean up
        self.db.delete(retrieved_c)
        self.db.delete(retrieved_i)
        self.db.commit()

    def test_feature_calculation(self):
        """Verifies that indicators are calculated correctly and have no look-ahead bias."""
        # Create a mock stock prices dataframe (daily frequency to keep it simple)
        dates = pd.date_range(start="2026-01-01", end="2026-01-15", freq='D').strftime("%Y-%m-%d")
        prices_data = {
            "ticker": ["TICK"] * len(dates),
            "date": dates,
            "open": [100.0] * len(dates),
            "high": [105.0] * len(dates),
            "low": [98.0] * len(dates),
            "close": [102.0] * len(dates),
            "volume": [100000] * len(dates),
            "sma_10": [100.0] * len(dates),
            "sma_50": [100.0] * len(dates),
            "rsi_14": [50.0] * len(dates),
            "macd": [0.0] * len(dates),
            "macd_signal": [0.0] * len(dates),
        }
        df = pd.DataFrame(prices_data)
        
        # Create mock disclosures
        # 1. Congress buy of $50k on 2026-01-05
        congress_df = pd.DataFrame([{
            "ticker": "TICK",
            "date": "2026-01-05",
            "transaction_type": "purchase",
            "estimated_value": 50000.0
        }])
        
        # 2. Insider buy of $100k on 2026-01-07
        insider_df = pd.DataFrame([{
            "ticker": "TICK",
            "date": "2026-01-07",
            "transaction_type": "purchase",
            "total_value": 100000.0
        }])
        
        # Compute features
        res_df = build_features_for_df(
            df,
            sentiment_df=None,
            macro_df=None,
            target_horizon_bars=5,
            congress_df=congress_df,
            insider_df=insider_df
        )
        
        # Check that the columns exist
        self.assertIn("feat_insider_buying_30d", res_df.columns)
        self.assertIn("feat_congress_buying_90d", res_df.columns)
        
        # Verify look-ahead protection:
        # A Congress buy filed on 2026-01-05:
        # - On 2026-01-05, the disclosure is filed. Since we shift the daily series by 1 day, it is first visible in the daily series on 2026-01-06.
        # - Shifting the feature by 1 bar means that on 2026-01-06, the feature `feat_congress_buying_90d` must still be 0.
        # - On 2026-01-07, the feature `feat_congress_buying_90d` should become positive (50000.0 / close_price).
        # Let's inspect the results:
        
        res_df = res_df.sort_values("date").reset_index(drop=True)
        
        # Find row for 2026-01-05
        row_05 = res_df[res_df["date"] == "2026-01-05"]
        if not row_05.empty:
            self.assertEqual(row_05.iloc[0]["feat_congress_buying_90d"], 0.0)
            
        # Find row for 2026-01-06 (should still be 0.0 because of the 1-bar shift of the daily series)
        row_06 = res_df[res_df["date"] == "2026-01-06"]
        if not row_06.empty:
            self.assertEqual(row_06.iloc[0]["feat_congress_buying_90d"], 0.0)
            
        # Find row for 2026-01-07 (should be positive now)
        row_07 = res_df[res_df["date"] == "2026-01-07"]
        if not row_07.empty:
            self.assertGreater(row_07.iloc[0]["feat_congress_buying_90d"], 0.0)
            # The value should be 50000.0 / close_price on 2026-01-06 (which was 102.0)
            # Wait, let's verify: row_07's feature is row_06's value = 50000.0 / 102.0
            expected_val = 50000.0 / 102.0
            self.assertAlmostEqual(row_07.iloc[0]["feat_congress_buying_90d"], expected_val, places=4)
            
        # Find row for 2026-01-08
        row_08 = res_df[res_df["date"] == "2026-01-08"]
        if not row_08.empty:
            # Should not include insider buy yet (it was filed on 07, visible on 08, shifted by 1 bar -> visible on 09)
            self.assertEqual(row_08.iloc[0]["feat_insider_buying_30d"], 0.0)
            
        # Find row for 2026-01-09
        row_09 = res_df[res_df["date"] == "2026-01-09"]
        if not row_09.empty:
            # Should now include insider buy
            self.assertGreater(row_09.iloc[0]["feat_insider_buying_30d"], 0.0)
            expected_ins_val = 100000.0 / 102.0
            self.assertAlmostEqual(row_09.iloc[0]["feat_insider_buying_30d"], expected_ins_val, places=4)

    def test_hedging_logic(self):
        """Verifies that get_hedge_info behaves correctly based on correlation and volatilities."""
        # Create a mock ExecContext
        ctx = MagicMock()
        ctx.symbol = "AAPL"
        
        # Set up indicators map for ctx
        indicators = {
            'feat_corr_spy_20': [0.5],
            'feat_corr_qqq_20': [0.8],
            'feat_relative_vol_spy': [1.2],
            'feat_relative_vol_qqq': [0.9]
        }
        ctx.indicators = set(indicators.keys())
        ctx.indicator.side_effect = lambda name: indicators[name]
        
        # Test beta_neutral mode (should pick QQQ because 0.8 > 0.5)
        symbol, beta = get_hedge_info(ctx, 'beta_neutral')
        self.assertEqual(symbol, 'QQQ')
        # beta = corr_qqq * rel_vol_qqq = 0.8 * 0.9 = 0.72
        self.assertAlmostEqual(beta, 0.72)
        
        # Test beta_neutral when SPY correlation is higher
        indicators['feat_corr_spy_20'] = [0.9]
        indicators['feat_corr_qqq_20'] = [0.4]
        symbol, beta = get_hedge_info(ctx, 'beta_neutral')
        self.assertEqual(symbol, 'SPY')
        # beta = corr_spy * rel_vol_spy = 0.9 * 1.2 = 1.08
        self.assertAlmostEqual(beta, 1.08)
        
        # Test pair_trade mode for MSFT -> peer is AAPL
        ctx.symbol = "MSFT"
        symbol, beta = get_hedge_info(ctx, 'pair_trade')
        self.assertEqual(symbol, 'AAPL')
        self.assertEqual(beta, 1.0)

if __name__ == "__main__":
    unittest.main()
