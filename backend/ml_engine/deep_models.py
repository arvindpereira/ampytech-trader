import os
import sys
import argparse
import pickle
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ml_engine.models import load_data_from_db

# Saved models directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "ml_engine", "saved_models")
os.makedirs(SAVED_MODELS_DIR, exist_ok=True)

class LightTemporalAttentionNet(nn.Module):
    """
    A lightweight GRU + Self-Attention model for time-series forecasting.
    Input size: features per step (OHLCV + pre-calculated indicators + sentiment).
    Sequence length: T steps (default: 10).
    """
    def __init__(self, input_dim, hidden_dim=32, num_layers=1):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True)

        # Self-Attention projections
        self.query = nn.Linear(hidden_dim, hidden_dim)
        self.key = nn.Linear(hidden_dim, hidden_dim)
        self.value = nn.Linear(hidden_dim, hidden_dim)

        self.fc = nn.Linear(hidden_dim, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        # x shape: [batch_size, seq_len, input_dim]
        out, _ = self.gru(x)  # out shape: [batch_size, seq_len, hidden_dim]

        # Simple Scaled Dot-Product Self-Attention
        q = self.query(out)   # [batch_size, seq_len, hidden_dim]
        k = self.key(out)     # [batch_size, seq_len, hidden_dim]
        v = self.value(out)   # [batch_size, seq_len, hidden_dim]

        attn_scores = torch.matmul(q, k.transpose(-2, -1)) / (out.shape[-1] ** 0.5)
        attn_weights = torch.softmax(attn_scores, dim=-1)
        attn_context = torch.matmul(attn_weights, v)

        # Average pooled vector over the sequence length
        context_vector = attn_context.mean(dim=1)

        logits = self.fc(context_vector)
        return self.sigmoid(logits)


class SequenceTimeSeriesDataset(Dataset):
    """PyTorch Dataset building sequence sliding windows from a feature DataFrame."""
    def __init__(self, features, targets, weights):
        self.features = torch.tensor(features, dtype=torch.float32)
        self.targets = torch.tensor(targets, dtype=torch.float32).unsqueeze(1)
        self.weights = torch.tensor(weights, dtype=torch.float32).unsqueeze(1)

    def __len__(self):
        return len(self.features)

    def __getitem__(self, idx):
        return self.features[idx], self.targets[idx], self.weights[idx]


def prepare_sequences(df, feature_cols, seq_len=10, fit_scaler=False, scaler_metadata=None):
    """
    Prepares sequence windows of length seq_len for each ticker in the dataframe.
    Standardizes features using global mean and standard deviation.
    """
    df = df.copy()

    # Find rows that are fully valid
    valid_cols = feature_cols + ["target_win", "ticker", "date"]
    df = df.dropna(subset=feature_cols)

    if df.empty:
        return np.empty((0, seq_len, len(feature_cols))), np.empty((0,)), np.empty((0,)), scaler_metadata

    # Fit or apply feature scaling
    if fit_scaler:
        mean = df[feature_cols].mean().values
        std = df[feature_cols].std().values
        std[std == 0.0] = 1.0  # Prevent divide by zero
        scaler_metadata = {"mean": mean.tolist(), "std": std.tolist(), "feature_cols": feature_cols}
    else:
        if scaler_metadata is None:
            raise ValueError("scaler_metadata must be provided if fit_scaler is False")
        mean = np.array(scaler_metadata["mean"])
        std = np.array(scaler_metadata["std"])

    # Scale the features
    scaled_feats = (df[feature_cols].values - mean) / std

    # Assign scaled values back to DataFrame
    df_scaled = df.copy()
    df_scaled[feature_cols] = scaled_feats

    sequences = []
    targets = []
    dates = []

    # Slide window within each ticker to avoid cross-ticker bleed
    for ticker in df_scaled["ticker"].unique():
        ticker_df = df_scaled[df_scaled["ticker"] == ticker].sort_values("date")
        if len(ticker_df) < seq_len:
            continue

        feat_vals = ticker_df[feature_cols].values
        target_vals = ticker_df["target_win"].values
        date_vals = ticker_df["date"].values

        for i in range(seq_len - 1, len(ticker_df)):
            seq = feat_vals[i - seq_len + 1 : i + 1]
            target = target_vals[i]
            sequences.append(seq)
            targets.append(target)
            dates.append(date_vals[i])

    if not sequences:
        return np.empty((0, seq_len, len(feature_cols))), np.empty((0,)), np.empty((0,)), scaler_metadata

    # Calculate temporal decay weights (5-year half-life)
    dt_vals = pd.to_datetime(dates, format='mixed')
    max_dt = dt_vals.max()
    days_diff = (max_dt - dt_vals).days
    half_life_days = 5.0 * 365.25
    weights = np.exp(-days_diff / half_life_days)

    return np.array(sequences), np.array(targets), np.array(weights), scaler_metadata


def train_temporal_attention_model(seq_len=10, epochs=30, batch_size=128):
    """Loads DB data, constructs sequences, trains LightTemporalAttentionNet, and saves it."""
    print("Loading data from database...")
    df = load_data_from_db()

    feature_cols = sorted([col for col in df.columns if col.startswith("feat_") and col != "feat_atr_14"])
    target_col = "target_win"

    print("Preparing training sequences...")
    # Drop rows without targets (last few rows of dataset)
    train_df = df.dropna(subset=[target_col]).copy()

    X_seq, y_seq, weights, scaler_metadata = prepare_sequences(
        train_df, feature_cols, seq_len=seq_len, fit_scaler=True
    )

    if len(X_seq) == 0:
        print("Error: No training sequences could be prepared. Check database contents.")
        return

    print(f"Prepared sequence shape: {X_seq.shape}")

    # Set up DataLoader
    dataset = SequenceTimeSeriesDataset(X_seq, y_seq, weights)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    # Initialize model
    input_dim = len(feature_cols)
    model = LightTemporalAttentionNet(input_dim=input_dim, hidden_dim=32)

    # Set up loss and optimizer (reduction='none' to apply sample weights)
    criterion = nn.BCELoss(reduction='none')
    optimizer = optim.Adam(model.parameters(), lr=0.005)

    print("Training Temporal Attention Network with temporal weights...")
    model.train()
    for epoch in range(1, epochs + 1):
        epoch_loss = 0.0
        correct = 0
        total = 0
        for batch_x, batch_y, batch_w in dataloader:
            optimizer.zero_grad()
            outputs = model(batch_x)
            loss_elements = criterion(outputs, batch_y)
            loss = (loss_elements * batch_w).mean()
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item() * batch_x.size(0)
            predictions = (outputs >= 0.5).float()
            correct += (predictions == batch_y).sum().item()
            total += batch_y.size(0)

        avg_loss = epoch_loss / total
        accuracy = (correct / total) * 100.0
        print(f"Epoch {epoch}/{epochs} - Weighted Loss: {avg_loss:.4f} - Accuracy: {accuracy:.2f}%")

    # Save model weight state
    model_path = os.path.join(SAVED_MODELS_DIR, "temporal_attention_model.pth")
    torch.save(model.state_dict(), model_path)
    print(f"Saved PyTorch weights to: {model_path}")

    # Save scaler metadata
    metadata_path = os.path.join(SAVED_MODELS_DIR, "temporal_attention_metadata.pkl")
    with open(metadata_path, "wb") as f:
        pickle.dump(scaler_metadata, f)
    print(f"Saved scaling metadata to: {metadata_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PyTorch Short-Term Sequence Forecasting Trainer")
    parser.add_argument("--train", action="store_true", help="Train the model")
    parser.add_argument("--epochs", type=int, default=30, help="Number of training epochs")
    args = parser.parse_args()

    if args.train:
        train_temporal_attention_model(epochs=args.epochs)
    else:
        print("Usage: python deep_models.py --train [--epochs N]")
