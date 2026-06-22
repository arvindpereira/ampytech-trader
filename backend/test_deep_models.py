import os
import unittest
import numpy as np
import torch
from ml_engine.deep_models import (
    LightTemporalAttentionNet, prepare_sequences,
    _calibrate_threshold, _train_model,
)


class TestDeepModels(unittest.TestCase):
    def test_model_forward_pass(self):
        """Verifies that LightTemporalAttentionNet correctly processes batch inputs and generates probabilities."""
        batch_size = 4
        seq_len = 10
        input_dim = 27
        hidden_dim = 32

        model = LightTemporalAttentionNet(input_dim=input_dim, hidden_dim=hidden_dim)
        x = torch.randn(batch_size, seq_len, input_dim)
        out = model(x)

        # Output shape should be [batch_size, 1]
        self.assertEqual(out.shape, (batch_size, 1))
        # Outputs should be probabilities between 0.0 and 1.0 (sigmoid output)
        self.assertTrue(torch.all(out >= 0.0))
        self.assertTrue(torch.all(out <= 1.0))

    def test_sequence_preparation(self):
        """Verifies that sequence builder correctly scales features and creates correct overlapping sliding windows."""
        # Create a mock dataframe for 2 tickers with 15 rows each
        num_rows = 15
        feature_cols = [f"feat_col{i}" for i in range(5)]

        ticker_1_data = {col: np.random.randn(num_rows) for col in feature_cols}
        ticker_1_data["ticker"] = "AAPL"
        ticker_1_data["date"] = pd_date_range = [f"2026-06-01T{i:02d}:00:00" for i in range(num_rows)]
        ticker_1_data["target_win"] = np.random.choice([0, 1], size=num_rows)

        ticker_2_data = {col: np.random.randn(num_rows) for col in feature_cols}
        ticker_2_data["ticker"] = "MSFT"
        ticker_2_data["date"] = [f"2026-06-01T{i:02d}:00:00" for i in range(num_rows)]
        ticker_2_data["target_win"] = np.random.choice([0, 1], size=num_rows)

        import pandas as pd
        df1 = pd.DataFrame(ticker_1_data)
        df2 = pd.DataFrame(ticker_2_data)
        df = pd.concat([df1, df2], ignore_index=True)

        seq_len = 10
        # Prepare sequences with scaling fit
        X, y, _, metadata = prepare_sequences(
            df, feature_cols, seq_len=seq_len, fit_scaler=True
        )

        # Expected samples per ticker = 15 - 10 + 1 = 6
        # Total samples = 6 * 2 = 12
        self.assertEqual(X.shape, (12, seq_len, len(feature_cols)))
        self.assertEqual(len(y), 12)
        self.assertIsNotNone(metadata)
        self.assertIn("mean", metadata)
        self.assertIn("std", metadata)
        self.assertEqual(metadata["feature_cols"], feature_cols)

        # Test utilizing saved metadata to prepare sequences (fit_scaler=False)
        X_eval, y_eval, _, metadata_eval = prepare_sequences(
            df, feature_cols, seq_len=seq_len, fit_scaler=False, scaler_metadata=metadata
        )
        np.testing.assert_array_almost_equal(X, X_eval)
        np.testing.assert_array_almost_equal(y, y_eval)


    def test_threshold_calibration(self):
        """_calibrate_threshold returns a value in [0.25, 0.74] for a small synthetic dataset."""
        import pandas as pd
        feature_cols = [f"feat_x{i}" for i in range(4)]
        np.random.seed(42)
        num_rows = 20
        data = {col: np.random.randn(num_rows) for col in feature_cols}
        data["ticker"] = "AAPL"
        data["date"] = [f"2026-0{(i//10)+1}-{(i%10)+1:02d}T09:00:00" for i in range(num_rows)]
        data["target_win"] = (np.random.rand(num_rows) > 0.5).astype(int)
        df = pd.DataFrame(data)
        seq_len = 5
        X, y, w, _ = prepare_sequences(df, feature_cols, seq_len=seq_len, fit_scaler=True)
        model, device = _train_model(X, y, w, input_dim=len(feature_cols),
                                     epochs=2, hidden_dim=8)
        thr = _calibrate_threshold(model, X, y, device)
        self.assertGreaterEqual(thr, 0.25)
        self.assertLess(thr, 0.75)

    def test_train_model_helper(self):
        """_train_model trains a tiny net without error and returns a model + device string."""
        import pandas as pd
        feature_cols = [f"feat_x{i}" for i in range(6)]
        np.random.seed(0)
        num_rows = 30
        data = {col: np.random.randn(num_rows) for col in feature_cols}
        data["ticker"] = "AAPL"
        data["date"] = [f"2026-01-{i+1:02d}T09:00:00" for i in range(num_rows)]
        data["target_win"] = (np.random.rand(num_rows) > 0.5).astype(int)
        df = pd.DataFrame(data)
        X, y, w, _ = prepare_sequences(df, feature_cols, seq_len=5, fit_scaler=True)
        model, device = _train_model(X, y, w, input_dim=len(feature_cols),
                                     epochs=3, hidden_dim=8)
        self.assertIsInstance(model, LightTemporalAttentionNet)
        self.assertIn(device, ("cpu", "cuda"))
        # Model should still produce valid probabilities after training
        x_t = torch.randn(2, 5, len(feature_cols))
        with torch.no_grad():
            out = model(x_t)
        self.assertEqual(out.shape, (2, 1))
        self.assertTrue(torch.all(out >= 0.0) and torch.all(out <= 1.0))


if __name__ == "__main__":
    unittest.main()
