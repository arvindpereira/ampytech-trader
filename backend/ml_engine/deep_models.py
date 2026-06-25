"""Deep Swing Model — GRU + Self-Attention trained on the same daily-bar + LLM-news pipeline
as the XGBoost swing model, enabling a fair walk-forward OOS comparison between the two.

Architecture: LightTemporalAttentionNet (GRU → scaled-dot-product self-attention → mean-pool → sigmoid)
Data:         Daily bars via load_swing_data() — same features + LLM news as XGBoost
Target:       5-day triple-barrier target_win (identical to swing XGBoost)
Training:     N-fold walk-forward with fold-local scaler and per-fold threshold calibration

The old hourly short-term version of this model (train_temporal_attention_model) is kept at the
bottom for backward compatibility but is deprecated.
"""
import os
import sys
import pickle
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from datetime import datetime
from torch.utils.data import Dataset, DataLoader

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "ml_engine", "saved_models")
os.makedirs(SAVED_MODELS_DIR, exist_ok=True)

DEEP_SWING_MODEL_PATH = os.path.join(SAVED_MODELS_DIR, "deep_swing_model.pth")
DEEP_SWING_META_PATH = os.path.join(SAVED_MODELS_DIR, "deep_swing_metadata.pkl")

# Legacy paths (old hourly model)
_LEGACY_MODEL_PATH = os.path.join(SAVED_MODELS_DIR, "temporal_attention_model.pth")
_LEGACY_META_PATH = os.path.join(SAVED_MODELS_DIR, "temporal_attention_metadata.pkl")

# ─── Architecture ─────────────────────────────────────────────────────────────

class LightTemporalAttentionNet(nn.Module):
    """GRU + scaled dot-product self-attention for sequential daily-bar prediction.
    Input:  [batch, seq_len, input_dim]
    Output: [batch, 1]  — sigmoid probability of target_win=1
    """
    def __init__(self, input_dim, hidden_dim=64, num_layers=1):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers, batch_first=True, dropout=0.0)
        self.query = nn.Linear(hidden_dim, hidden_dim)
        self.key   = nn.Linear(hidden_dim, hidden_dim)
        self.value = nn.Linear(hidden_dim, hidden_dim)
        self.dropout = nn.Dropout(0.2)
        self.fc = nn.Linear(hidden_dim, 1)
        self.sigmoid = nn.Sigmoid()

    def forward(self, x):
        out, _ = self.gru(x)                                    # [B, T, H]
        q = self.query(out)
        k = self.key(out)
        v = self.value(out)
        scores = torch.matmul(q, k.transpose(-2, -1)) / (out.shape[-1] ** 0.5)
        weights = torch.softmax(scores, dim=-1)
        context = torch.matmul(weights, v)                      # [B, T, H]
        pooled = self.dropout(context.mean(dim=1))              # [B, H]
        return self.sigmoid(self.fc(pooled))                    # [B, 1]


class SequenceTimeSeriesDataset(Dataset):
    def __init__(self, features, targets, weights):
        self.features = torch.tensor(features, dtype=torch.float32)
        self.targets  = torch.tensor(targets,  dtype=torch.float32).unsqueeze(1)
        self.weights  = torch.tensor(weights,  dtype=torch.float32).unsqueeze(1)

    def __len__(self):
        return len(self.features)

    def __getitem__(self, idx):
        return self.features[idx], self.targets[idx], self.weights[idx]


# ─── Sequence preparation (fold-aware) ────────────────────────────────────────

def prepare_sequences(df, feature_cols, seq_len=20, fit_scaler=False, scaler_metadata=None):
    """Build sliding-window sequences per ticker.

    fit_scaler=True: fit mean/std on df and return scaler_metadata.
    fit_scaler=False: apply pre-fitted scaler_metadata from the training fold.
    Cross-ticker bleed is avoided: windows are built within each ticker independently.

    Returns: (X: [N, seq_len, F], y: [N,], weights: [N,], scaler_metadata)
    """
    df = df.copy().dropna(subset=feature_cols)
    if df.empty:
        return (np.empty((0, seq_len, len(feature_cols))),
                np.empty((0,)), np.empty((0,)), scaler_metadata)

    if fit_scaler:
        mean = df[feature_cols].mean().to_numpy(dtype=float)
        std  = df[feature_cols].std().to_numpy(dtype=float)
        # np.where returns a fresh writable array — pandas 3.0 / numpy 2 make .values/.to_numpy()
        # read-only, so an in-place `std[mask] = 1.0` would raise "assignment destination is read-only".
        std = np.where(std == 0.0, 1.0, std)
        scaler_metadata = {"mean": mean.tolist(), "std": std.tolist(), "feature_cols": feature_cols}
    else:
        if scaler_metadata is None:
            raise ValueError("scaler_metadata required when fit_scaler=False")
        mean = np.array(scaler_metadata["mean"])
        std  = np.array(scaler_metadata["std"])

    scaled = (df[feature_cols].values - mean) / std
    df = df.copy()
    df[feature_cols] = scaled

    sequences, targets, dates = [], [], []
    for ticker in df["ticker"].unique():
        tdf = df[df["ticker"] == ticker].sort_values("date")
        if len(tdf) < seq_len:
            continue
        fv = tdf[feature_cols].values
        tv = tdf["target_win"].values
        dv = tdf["date"].values
        for i in range(seq_len - 1, len(tdf)):
            sequences.append(fv[i - seq_len + 1: i + 1])
            targets.append(tv[i])
            dates.append(dv[i])

    if not sequences:
        return (np.empty((0, seq_len, len(feature_cols))),
                np.empty((0,)), np.empty((0,)), scaler_metadata)

    dt_vals = pd.to_datetime(dates, format="mixed")
    max_dt  = dt_vals.max()
    days_diff = (max_dt - dt_vals).days
    weights = np.exp(-days_diff / (5.0 * 365.25))

    return np.array(sequences), np.array(targets), np.array(weights), scaler_metadata


# ─── Threshold calibration ────────────────────────────────────────────────────

def _calibrate_threshold(model, X_val, y_val, device="cpu"):
    """F1-maximizing threshold over the model's sigmoid outputs on a validation set."""
    model.eval()
    with torch.no_grad():
        probs = model(torch.tensor(X_val, dtype=torch.float32).to(device)).cpu().numpy().flatten()
    best_thr, best_f1 = 0.5, 0.0
    for thr in np.arange(0.25, 0.75, 0.02):
        preds = (probs >= thr).astype(int)
        tp = ((preds == 1) & (y_val == 1)).sum()
        fp = ((preds == 1) & (y_val == 0)).sum()
        fn = ((preds == 0) & (y_val == 1)).sum()
        prec = tp / (tp + fp + 1e-9)
        rec  = tp / (tp + fn + 1e-9)
        f1   = 2 * prec * rec / (prec + rec + 1e-9)
        if f1 > best_f1:
            best_f1, best_thr = f1, float(thr)
    return best_thr


# ─── Core training step ───────────────────────────────────────────────────────

def _train_model(X_train, y_train, w_train, input_dim, epochs, batch_size=128,
                 hidden_dim=64, lr=0.001, verbose=False):
    """Train a LightTemporalAttentionNet and return (model, device)."""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LightTemporalAttentionNet(input_dim=input_dim, hidden_dim=hidden_dim).to(device)
    criterion = nn.BCELoss(reduction="none")
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=max(1, epochs // 3), gamma=0.5)

    dataset    = SequenceTimeSeriesDataset(X_train, y_train, w_train)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    model.train()
    for epoch in range(1, epochs + 1):
        total_loss, correct, total = 0.0, 0, 0
        for bx, by, bw in dataloader:
            bx, by, bw = bx.to(device), by.to(device), bw.to(device)
            optimizer.zero_grad()
            out = model(bx)
            loss = (criterion(out, by) * bw).mean()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item() * bx.size(0)
            correct += ((out >= 0.5).float() == by).sum().item()
            total   += by.size(0)
        scheduler.step()
        if verbose and (epoch % max(1, epochs // 5) == 0 or epoch == epochs):
            print(f"  epoch {epoch}/{epochs}  loss={total_loss/max(total,1):.4f}  "
                  f"acc={100*correct/max(total,1):.1f}% (train-set, not OOS)")
    model.eval()
    return model, device


# ─── Walk-forward OOS frame ───────────────────────────────────────────────────

def deep_swing_oos_frame(horizon=5, seq_len=20, n_splits=4, warmup_frac=0.4,
                          oos_start=None, progress_cb=None, fold_epochs=40):
    """Walk-forward, look-ahead-free OOS prediction frame for the deep swing model.

    Returns (oos_df, prices_df, equities) — same contract as swing_oos_frame().
    oos_df columns: date, ticker, close, atr_14, target_win, trade_ret, prob, selected_threshold
    """
    from ml_engine.swing_alpha import load_swing_data, NON_EQUITY

    print("Deep swing OOS: loading daily swing data + LLM features…")
    full, equities, prices_df, first_llm_date = load_swing_data(horizon)

    df = full[full["ticker"].isin(equities)].dropna(subset=["target_win", "trade_ret"]).copy()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    feat_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    df = df.dropna(subset=feat_all)
    if first_llm_date:
        df = df[df["date"] >= first_llm_date]
    df = df.sort_values("dt").reset_index(drop=True)

    if len(df) < 1000:
        print(f"Only {len(df)} samples — run `make news-llm` to score more news first.")
        return None, prices_df, equities

    first_edge = (pd.to_datetime(oos_start) if oos_start
                  else df["dt"].min() + (df["dt"].max() - df["dt"].min()) * warmup_frac)
    edges = pd.date_range(first_edge, df["dt"].max(), periods=n_splits + 1)

    frames = []
    print(f"{'fold':>4} {'train':>7} {'test':>6} {'period':>23} | thr    auc   p̄±σ           buys/total")
    for i in range(n_splits):
        lo, hi = edges[i], edges[i + 1]
        tr = df[df["dt"] < lo]
        te = df[(df["dt"] >= lo) & (df["dt"] < hi)]
        if len(tr) < 500 or len(te) < 100:
            print(f"{i:>4}  skipped (train={len(tr)}, test={len(te)})")
            continue

        # Fit scaler on training split only
        X_tr, y_tr, w_tr, fold_scaler = prepare_sequences(
            tr, feat_all, seq_len=seq_len, fit_scaler=True)
        if len(X_tr) == 0:
            continue

        # 80/20 inner split for threshold calibration
        n_val  = max(1, int(0.2 * len(X_tr)))
        X_val, y_val = X_tr[-n_val:], y_tr[-n_val:]
        X_fit, y_fit, w_fit = X_tr[:-n_val], y_tr[:-n_val], w_tr[:-n_val]

        model, device = _train_model(X_fit, y_fit, w_fit, input_dim=len(feat_all),
                                     epochs=fold_epochs, hidden_dim=64)
        fold_thr = _calibrate_threshold(model, X_val, y_val, device)

        # Apply scaler from training to test split (no look-ahead).
        # IMPORTANT: prepare_sequences iterates df.dropna(feature_cols)["ticker"].unique()
        # in insertion order.  The reconstruction frame below must use the same order
        # so probs[k] maps to the correct row.
        te_clean = te.dropna(subset=feat_all).copy()
        X_te, y_te, _, _ = prepare_sequences(
            te_clean, feat_all, seq_len=seq_len, fit_scaler=False, scaler_metadata=fold_scaler)
        if len(X_te) == 0:
            continue

        model.eval()
        with torch.no_grad():
            probs = model(torch.tensor(X_te, dtype=torch.float32).to(device)).cpu().numpy().flatten()

        # AUC on test fold
        try:
            from sklearn.metrics import roc_auc_score
            auc = roc_auc_score(y_te, probs) if len(np.unique(y_te)) > 1 else float("nan")
        except ImportError:
            auc = float("nan")

        # Reconstruct the OOS frame in the SAME ticker-major order as prepare_sequences
        # so probs[k] aligns with the correct date/ticker row.
        out_rows = []
        for ticker in te_clean["ticker"].unique():        # same order as prepare_sequences
            tdf = te_clean[te_clean["ticker"] == ticker].sort_values("date")
            if len(tdf) < seq_len:
                continue
            slice_df = tdf.iloc[seq_len - 1:][
                ["date", "ticker", "close", "atr_14", "target_win", "trade_ret"]].copy()
            out_rows.append(slice_df)
        if not out_rows:
            continue

        f = pd.concat(out_rows).reset_index(drop=True)   # ticker-major, matches probs
        if len(f) != len(probs):
            print(f"  WARNING fold {i}: frame rows {len(f)} != probs {len(probs)} — skipping")
            continue
        f["prob"] = probs
        f["selected_threshold"] = fold_thr
        frames.append(f.sort_values("date"))   # sort by date only after alignment

        above_thr = int((probs >= fold_thr).sum())
        print(f"{i:>4} {len(tr):>7} {len(te):>6} {str(lo.date())+'..'+str(hi.date()):>23} | "
              f"thr={fold_thr:.3f} auc={auc:.3f} "
              f"p̄={probs.mean():.3f}±{probs.std():.3f} buys={above_thr}/{len(probs)}")
        if progress_cb:
            progress_cb((i + 1) / n_splits)

    if not frames:
        print("No valid OOS folds — insufficient data.")
        return None, prices_df, equities

    return pd.concat(frames).sort_values("date"), prices_df, equities


# ─── Walk-forward backtest curve ──────────────────────────────────────────────

def backtest_deep_swing_curve(horizon=5, n_splits=4, warmup_frac=0.4,
                               oos_start=None, progress_cb=None, fold_epochs=40):
    """Walk-forward backtest → (equity_curve list, metrics dict, prices_df).
    Same return shape as backtest_swing_curve() so run_evaluation() can treat both identically."""
    from ml_engine.swing_alpha import SWING_STOP_MAX, SWING_STOP_MIN, SWING_ATR_STOP_MULT, SWING_TP_MULT
    from ml_engine.models import simulate_portfolio_chronological, compute_regime_series

    oos, prices_df, _ = deep_swing_oos_frame(
        horizon=horizon, n_splits=n_splits, warmup_frac=warmup_frac,
        oos_start=oos_start, progress_cb=progress_cb, fold_epochs=fold_epochs)
    if oos is None or oos.empty:
        return [], {}

    regime_by_date = compute_regime_series(oos_start)
    curve, metrics = simulate_portfolio_chronological(
        oos, prices_df, horizon=horizon,
        stop_max=SWING_STOP_MAX, stop_min=SWING_STOP_MIN,
        atr_mult=SWING_ATR_STOP_MULT, tp_mult=SWING_TP_MULT,
        regime_by_date=regime_by_date)
    return curve, metrics


# ─── Production train + save ──────────────────────────────────────────────────

def train_deep_swing_and_save(horizon=5, seq_len=20, epochs=30, allowed_tickers=None,
                               hidden_dim=64, batch_size=128):
    """Train the deep swing model on the full LLM-active window and persist artifacts.
    For OOS evaluation call deep_swing_oos_frame(); this produces the serving model."""
    from ml_engine.swing_alpha import load_swing_data

    print(f"Training deep swing model (horizon={horizon}d, seq_len={seq_len}, epochs={epochs})…")
    full, equities, _, first_llm_date = load_swing_data(horizon)
    if allowed_tickers is not None:
        equities = [t for t in equities if t in set(allowed_tickers)]

    df = full[full["ticker"].isin(equities)].dropna(subset=["target_win", "trade_ret"]).copy()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    feat_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    df = df.dropna(subset=feat_all)
    if first_llm_date:
        df = df[df["date"] >= first_llm_date]
    df = df.sort_values("dt").reset_index(drop=True)
    if len(df) < 1000:
        print(f"Only {len(df)} samples — need more scored news (make news-llm).")
        return False

    X, y, w, scaler_metadata = prepare_sequences(df, feat_all, seq_len=seq_len, fit_scaler=True)
    if len(X) == 0:
        print("No sequences built — check data.")
        return False

    print(f"  sequences: {X.shape}  |  window: {df['date'].min()} .. {df['date'].max()}")

    # Hold out last 15% for threshold calibration
    n_val   = max(1, int(0.15 * len(X)))
    X_val, y_val = X[-n_val:], y[-n_val:]
    X_fit, y_fit, w_fit = X[:-n_val], y[:-n_val], w[:-n_val]

    model, device = _train_model(X_fit, y_fit, w_fit, input_dim=len(feat_all),
                                 epochs=epochs, hidden_dim=hidden_dim,
                                 batch_size=batch_size, verbose=True)
    threshold = _calibrate_threshold(model, X_val, y_val, device)
    print(f"  calibrated threshold: {threshold:.3f}")

    torch.save(model.state_dict(), DEEP_SWING_MODEL_PATH)
    meta = {
        "feature_cols": feat_all,
        "seq_len": seq_len,
        "hidden_dim": hidden_dim,
        "threshold": threshold,
        "horizon": int(horizon),
        "scaler_metadata": scaler_metadata,
        "n_samples": int(len(df)),
        "window": [str(df["date"].min()), str(df["date"].max())],
        "trained_at": datetime.now().isoformat(),
    }
    with open(DEEP_SWING_META_PATH, "wb") as f:
        pickle.dump(meta, f)
    print(f"  saved → {DEEP_SWING_MODEL_PATH}")
    print(f"  saved → {DEEP_SWING_META_PATH}")
    return True


# ─── Live inference ───────────────────────────────────────────────────────────

def load_deep_swing_model():
    """Load the persisted deep swing model and metadata. Returns (model, meta) or (None, None)."""
    if not (os.path.exists(DEEP_SWING_MODEL_PATH) and os.path.exists(DEEP_SWING_META_PATH)):
        return None, None
    with open(DEEP_SWING_META_PATH, "rb") as f:
        meta = pickle.load(f)
    model = LightTemporalAttentionNet(
        input_dim=len(meta["feature_cols"]),
        hidden_dim=meta.get("hidden_dim", 64))
    model.load_state_dict(torch.load(DEEP_SWING_MODEL_PATH, map_location="cpu"))
    model.eval()
    return model, meta


def build_deep_swing_signals(daily_prices_df, daily_macro_df, active_universe, top_n=None):
    """Live inference: per-ticker deep swing signal for the latest date.

    Returns the same list[dict] schema as build_swing_signals():
    [{ticker, close, action, confidence, stop_loss, take_profit, horizon_days, llm_news, ...}]
    """
    from ml_engine.swing_alpha import (
        add_llm_features, load_llm_news_daily, NON_EQUITY,
        SWING_TOP_N, SWING_STOP_MAX, SWING_STOP_MIN, SWING_ATR_STOP_MULT, SWING_TP_MULT,
    )
    from ml_engine.features import build_all_features

    model, meta = load_deep_swing_model()
    if model is None or daily_prices_df is None or daily_prices_df.empty:
        return []

    top_n = top_n or SWING_TOP_N
    seq_len = meta["seq_len"]
    feat_cols = meta["feature_cols"]
    thr = meta["threshold"]
    scaler_meta = meta["scaler_metadata"]

    equities = [t for t in active_universe
                if t not in NON_EQUITY and not str(t).startswith(("X:", "C:"))]
    feat_universe = sorted(set(equities + ["SPY", "QQQ"]))
    prices = daily_prices_df[daily_prices_df["ticker"].isin(feat_universe)].copy()
    if prices.empty:
        return []

    full = build_all_features(prices, None, daily_macro_df, feat_universe,
                              target_horizon_bars=meta["horizon"])
    if full.empty:
        return []
    full, _ = add_llm_features(full, load_llm_news_daily())
    full = full[full["ticker"].isin(equities)].copy()

    # Ensure all training feature columns exist
    for c in feat_cols:
        if c not in full.columns:
            full[c] = 0.0

    out = []
    for ticker, g in full.groupby("ticker"):
        g = g.sort_values("date").dropna(subset=feat_cols)
        if len(g) < seq_len:
            continue

        # Build one sequence: the last seq_len rows
        seq_df = g.iloc[-seq_len:].copy()
        X_raw = seq_df[feat_cols].values
        mean = np.array(scaler_meta["mean"])
        std  = np.array(scaler_meta["std"])
        X_scaled = (X_raw - mean) / std
        x_tensor = torch.tensor(X_scaled[np.newaxis], dtype=torch.float32)  # [1, T, F]

        with torch.no_grad():
            prob = float(model(x_tensor).item())

        last_row = g.iloc[-1]
        close = float(last_row["close"])
        atr   = float(last_row["atr_14"]) if "atr_14" in last_row else 0.0
        if close <= 0:
            continue

        sl_pct = min(SWING_STOP_MAX, max(SWING_STOP_MIN, (SWING_ATR_STOP_MULT * atr) / close))
        tp_pct = sl_pct * SWING_TP_MULT
        llm_news = float(last_row.get("feat_llm_news", 0.0))
        llm_int  = float(last_row.get("feat_llm_news_intensity", 0.0))
        out.append({
            "ticker": ticker,
            "close": round(close, 2),
            "confidence": round(prob, 4),
            "_sl_pct": sl_pct, "_tp_pct": tp_pct,
            "horizon_days": meta["horizon"],
            "llm_news": round(llm_news, 3),
            "llm_news_intensity": round(llm_int, 2),
        })

    out.sort(key=lambda x: x["confidence"], reverse=True)
    buys = 0
    for s in out:
        is_buy = s["confidence"] >= thr and buys < top_n
        if is_buy:
            buys += 1
        news_bit = (f" LLM news {s['llm_news']:+.2f}." if abs(s["llm_news"]) > 0.02 else "")
        s["action"]      = "BUY" if is_buy else "HOLD"
        s["stop_loss"]   = round(s["close"] * (1 - s["_sl_pct"]), 2) if is_buy else None
        s["take_profit"] = round(s["close"] * (1 + s["_tp_pct"]), 2) if is_buy else None
        s["reasoning"]   = (f"Deep GRU win-prob {s['confidence']*100:.0f}% vs threshold "
                            f"{thr*100:.0f}%.{news_bit}")
        del s["_sl_pct"], s["_tp_pct"]
    return out


# ─── Deprecated: original hourly short-term model ─────────────────────────────

def train_temporal_attention_model(seq_len=10, epochs=20, batch_size=128):
    """DEPRECATED — trains on hourly bars with short-term labels (negative portfolio edge).
    Use train_deep_swing_and_save() instead."""
    import warnings
    warnings.warn(
        "train_temporal_attention_model is deprecated. Use train_deep_swing_and_save() which "
        "trains on daily swing data with LLM features and proper walk-forward evaluation.",
        DeprecationWarning, stacklevel=2)
    from ml_engine.models import load_data_from_db
    print("Loading hourly data (legacy short-term model)…")
    df = load_data_from_db()
    feat_cols = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    train_df = df.dropna(subset=["target_win"]).copy()
    X, y, w, scaler_meta = prepare_sequences(train_df, feat_cols, seq_len=seq_len, fit_scaler=True)
    if len(X) == 0:
        print("No sequences — check database.")
        return
    model, _ = _train_model(X, y, w, input_dim=len(feat_cols), epochs=epochs,
                             hidden_dim=32, verbose=True)
    torch.save(model.state_dict(), _LEGACY_MODEL_PATH)
    with open(_LEGACY_META_PATH, "wb") as f:
        pickle.dump(scaler_meta, f)
    print(f"Saved legacy model to {_LEGACY_MODEL_PATH}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Deep Swing Model trainer")
    parser.add_argument("--train",  action="store_true", help="Train the deep swing model")
    parser.add_argument("--epochs", type=int,   default=30, help="Training epochs")
    parser.add_argument("--seq",    type=int,   default=20, help="Sequence length (trading days)")
    args = parser.parse_args()
    if args.train:
        train_deep_swing_and_save(epochs=args.epochs, seq_len=args.seq)
    else:
        print("Usage: python deep_models.py --train [--epochs N] [--seq N]")
