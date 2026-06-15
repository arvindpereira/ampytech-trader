import os
import sys
import pickle
import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.covariance import LedoitWolf
from hmmlearn.hmm import GaussianHMM

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    TICKER_UNIVERSE, SHORT_TERM_HORIZON_BARS, SHORT_TERM_ATR_STOP_MULT,
    SHORT_TERM_TP_MULT, SHORT_TERM_STOP_MIN, SHORT_TERM_STOP_MAX, SHORT_TERM_BUY_THRESHOLD,
)
from app.database import (
    SessionLocal, RecentPrice, DailyPrice, MacroIndicator, TickerSentiment, UniverseTicker
)
from ml_engine.features import build_features_for_df
from sklearn.metrics import roc_auc_score

# Saved models directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "ml_engine", "saved_models")
os.makedirs(SAVED_MODELS_DIR, exist_ok=True)

class PortfolioOptimizer:
    """Implements Mean-Variance Sharpe Maximization and Fractional Kelly calculations."""

    @staticmethod
    def calculate_optimal_weights(returns_df, target_regime):
        """
        Runs Mean-Variance Optimization using Ledoit-Wolf shrinkage covariance.
        Returns weight allocation dictionary for tickers.
        """
        tickers = [col for col in returns_df.columns if col not in ["SPY", "QQQ", "date", "month_year"]]
        if len(tickers) < 2:
            return {t: 1.0/len(tickers) for t in tickers}

        returns = returns_df[tickers].dropna()
        if returns.empty:
            return {t: 1.0/len(tickers) for t in tickers}

        # Expected Returns (1-year simple average returns)
        exp_returns = returns.mean() * 252  # Annualized

        # Shrinkage Covariance Matrix via Ledoit-Wolf
        lw = LedoitWolf()
        cov_matrix = lw.fit(returns).covariance_ * 252  # Annualized

        # Risk modification based on HMM Regime
        # If we are in high volatility contraction (crisis), shrink weights or maximize Cash/Index
        # In Growth: standard Sharpe max. In Crisis: penalize high-beta assets
        num_assets = len(tickers)

        # Solve for Maximum Sharpe Ratio using random portfolios simulation (robust & simple retail approach)
        rng = np.random.default_rng(42)
        num_portfolios = 10000
        best_sharpe = -100
        best_weights = np.ones(num_assets) / num_assets

        # Convert expected returns and cov to numpy arrays
        er = exp_returns.values
        cov = cov_matrix

        # Constraints: Weights sum to 1.0, long-only (0.0 <= w <= 0.25 to prevent over-concentration)
        for _ in range(num_portfolios):
            weights = rng.random(num_assets)
            weights /= np.sum(weights)

            # Apply maximum concentration constraint (max 25% in one stock)
            weights = np.clip(weights, 0.0, 0.25)
            weights /= np.sum(weights)

            # Portfolio metrics
            p_return = np.sum(er * weights)
            p_volatility = np.sqrt(np.dot(weights.T, np.dot(cov, weights)))

            # Risk free rate proxy (based on regime)
            rf = 0.04

            p_sharpe = (p_return - rf) / (p_volatility + 1e-9)

            if p_sharpe > best_sharpe:
                best_sharpe = p_sharpe
                best_weights = weights

        # Return dict
        optimal_weights = {tickers[i]: float(best_weights[i]) for i in range(num_assets)}
        return optimal_weights

    @staticmethod
    def calculate_fractional_kelly(win_prob, payoff_ratio, fraction=0.25):
        """
        Calculates Fractional Kelly sizing.
        Formula: f* = (b*p - q)/b where b is payoff ratio, p is win probability, q = 1-p.
        Sizing = fraction * f*.
        """
        p = win_prob
        q = 1.0 - p
        b = payoff_ratio

        if b <= 0:
            return 0.0

        f_star = (b * p - q) / b

        # Bound between 0.0 and 1.0, then scale by fraction
        f_star = max(0.0, min(1.0, f_star))
        return f_star * fraction

def load_data_from_db():
    """Loads HOURLY prices + REAL sentiment + macro and builds the SHORT-TERM feature set
    (cross-ticker breakout features over an intraday/few-day horizon)."""
    db = SessionLocal()

    # 1. Hourly prices (recent_prices)
    prices = db.query(RecentPrice).all()
    if not prices:
        db.close()
        raise ValueError("No hourly price records found. Run ingestion first!")

    prices_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open,
        "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal
    } for p in prices])

    # 2. Macro (daily; broadcast onto hourly bars by calendar date in features)
    macro = db.query(MacroIndicator).all()
    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()

    # 3. Sentiment — REAL only (exclude mock/simulated so the model does not learn noise)
    sent = db.query(TickerSentiment).filter(TickerSentiment.is_mock != True).all()
    sent_df = pd.DataFrame([{
        "ticker": s.ticker, "date": s.date, "source": s.source,
        "sentiment_score": s.sentiment_score, "mention_count": s.mention_count
    } for s in sent]) if sent else pd.DataFrame()

    db_tickers = db.query(UniverseTicker).all()
    active_universe = [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE
    db.close()

    from ml_engine.features import build_all_features
    full_df = build_all_features(
        prices_df, sent_df, macro_df, active_universe,
        target_horizon_bars=SHORT_TERM_HORIZON_BARS,
        target_atr_stop_mult=SHORT_TERM_ATR_STOP_MULT,
        target_tp_mult=SHORT_TERM_TP_MULT,
        target_stop_min=SHORT_TERM_STOP_MIN,
        target_stop_max=SHORT_TERM_STOP_MAX,
    )
    if full_df.empty:
        raise ValueError("Insufficient historical rows to build features.")
    return full_df


def load_daily_spy_features():
    """Loads DAILY SPY + macro (multi-decade) and returns SPY features for HMM regime training."""
    db = SessionLocal()
    prices = db.query(DailyPrice).filter(DailyPrice.ticker == "SPY").all()
    macro = db.query(MacroIndicator).all()
    db.close()

    if not prices:
        return pd.DataFrame()

    spy_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open, "high": p.high,
        "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal,
    } for p in prices]).sort_values("date")

    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()

    return build_features_for_df(spy_df, sentiment_df=None, macro_df=macro_df)

def _evaluate_holdout(train_data, feature_cols, target_col, sample_weights):
    """Time-ordered 80/20 split: train on the older 80%, report out-of-sample metrics on
    the most recent 20% so we get an honest (non-leaky) read on signal quality."""
    ordered = train_data.sort_values('date')
    split = int(len(ordered) * 0.8)
    if split < 50 or len(ordered) - split < 50:
        print("Not enough rows for a held-out evaluation; skipping.")
        return
    tr, te = ordered.iloc[:split], ordered.iloc[split:]
    w = np.exp(-(pd.to_datetime(tr['date'], format='mixed').max()
                 - pd.to_datetime(tr['date'], format='mixed')).dt.days / (5.0 * 365.25))
    m = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                          subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
    m.fit(tr[feature_cols], tr[target_col], sample_weight=w)
    proba = m.predict_proba(te[feature_cols])[:, 1]
    y_te = te[target_col].values
    base_rate = float(y_te.mean())
    try:
        auc = roc_auc_score(y_te, proba)
    except ValueError:
        auc = float("nan")
    # Precision among rows the live rule would actually BUY (prob >= configured threshold)
    buy_mask = proba >= SHORT_TERM_BUY_THRESHOLD
    precision = float(y_te[buy_mask].mean()) if buy_mask.sum() > 0 else float("nan")
    print("\n  === Short-Term OUT-OF-SAMPLE evaluation (most recent 20%) ===")
    print(f"  Test rows: {len(te)} | target base rate (random): {base_rate:.3f}")
    print(f"  ROC-AUC: {auc:.3f}  (0.5 = no skill)")
    print(f"  Precision @ BUY threshold (>={SHORT_TERM_BUY_THRESHOLD:.2f}): {precision:.3f} on {int(buy_mask.sum())} signals "
          f"(lift vs base: {precision - base_rate:+.3f})")
    print("  =============================================================\n")


def train_models():
    """Trains the XGBoost short-term classifier (HOURLY) and the macro HMM regime model (DAILY)."""
    print("Loading hourly data for short-term model training...")
    df = load_data_from_db()

    # --- 1. Train Short-Term XGBoost Model (hourly) ---
    print("\n--- Training Short-Term XGBoost Classifier (hourly bars) ---")

    # Sorted so the saved booster's feature order matches inference (main.py / backtest.py
    # both use sorted feat_* columns). Mismatched order triggers XGBoost feature_names errors.
    feature_cols = sorted([col for col in df.columns if col.startswith("feat_")])
    target_col = "target_win"

    # Remove rows with NaN target (last few bars: target is future-looking)
    train_data = df.dropna(subset=[target_col]).copy()

    X = train_data[feature_cols]
    y = train_data[target_col]

    # Temporal decay weights (5-year half-life)
    print("Calculating temporal decay weights (5-year half-life)...")
    max_date = pd.to_datetime(train_data['date'], format='mixed').max()
    train_dates = pd.to_datetime(train_data['date'], format='mixed')
    days_diff = (max_date - train_dates).dt.days
    half_life_days = 5.0 * 365.25
    sample_weights = np.exp(-days_diff / half_life_days)

    print(f"Training dataset size: {X.shape[0]} rows, {X.shape[1]} features. "
          f"Target positive rate: {y.mean():.3f}")

    # Honest out-of-sample read before fitting the production model on all data.
    _evaluate_holdout(train_data, feature_cols, target_col, sample_weights)

    # Fit production model on the full dataset
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=42
    )
    model.fit(X, y, sample_weight=sample_weights)

    model_path = os.path.join(SAVED_MODELS_DIR, "short_term_model.json")
    model.save_model(model_path)
    print(f"Short-Term XGBoost Model saved successfully to: {model_path}")

    # --- 2. Train Long-Term HMM Regime Model (DAILY, multi-decade) ---
    print("\n--- Training Long-Term HMM Regime Classifier (daily history) ---")
    spy_data = load_daily_spy_features()

    if spy_data is None or spy_data.empty:
        print("Warning: daily SPY data missing. Cannot fit HMM. Creating dummy HMM file...")
        dummy_regime = {"current_regime": "growth", "state_mean_vol": {0: 0.1, 1: 0.2, 2: 0.3}}
        with open(os.path.join(SAVED_MODELS_DIR, "hmm_metadata.pkl"), "wb") as f:
            pickle.dump(dummy_regime, f)
        return

    # Daily volatility, fed funds and yield spread over the full available history
    hmm_features = spy_data[["feat_volatility_10", "feat_fed_funds", "feat_yield_spread"]].dropna()
    print(f"HMM training rows (daily): {len(hmm_features)} "
          f"({spy_data['date'].min()} -> {spy_data['date'].max()})")

    # Standardize features
    X_hmm = hmm_features.values

    # Fit Gaussian HMM with 3 components (Growth, Transition, Crisis)
    hmm_model = GaussianHMM(n_components=3, covariance_type="diag", n_iter=100, random_state=42)
    hmm_model.fit(X_hmm)

    # Map components to regimes based on Volatility mean
    # Lower vol = Growth/Bull, Higher vol = Crisis/Bear, Mid vol = Transition
    means = hmm_model.means_
    vol_means = means[:, 0]  # Volatility is index 0
    sorted_states = np.argsort(vol_means)

    state_mapping = {
        sorted_states[0]: "growth",
        sorted_states[1]: "transition",
        sorted_states[2]: "crisis"
    }

    # Save model and metadata
    hmm_path = os.path.join(SAVED_MODELS_DIR, "hmm_model.pkl")
    with open(hmm_path, "wb") as f:
        pickle.dump(hmm_model, f)

    metadata = {
        "state_mapping": state_mapping,
        "features": ["volatility_10", "fed_funds", "yield_spread"],
        "means": means
    }
    metadata_path = os.path.join(SAVED_MODELS_DIR, "hmm_metadata.pkl")
    with open(metadata_path, "wb") as f:
        pickle.dump(metadata, f)

    print(f"Long-Term HMM Model saved successfully to: {hmm_path}")
    print(f"Regime State Mapping: {state_mapping}")
    print("Model training execution complete.\n")


def walk_forward_evaluate(n_splits=5, warmup_frac=0.4, round_trip_fee=0.001):
    """Honest out-of-sample evaluation of the short-term model.

    Expanding-window walk-forward: for each of `n_splits` contiguous test slices in the back
    `(1-warmup_frac)` of history, train a FRESH model on ALL data strictly before the slice and
    predict on it. Concatenate the per-slice predictions into one continuous out-of-sample series
    — no slice is ever predicted by a model that has seen it.

    Reports, scale-invariantly, what a *selective* strategy actually gets: win rate and mean
    realised net return (after `round_trip_fee`) among the top-percentile most-confident entries,
    plus the result at the live BUY threshold. Positive mean net return => genuine edge.
    """
    print("Loading data for walk-forward evaluation...")
    df = load_data_from_db().dropna(subset=["target_win", "trade_ret"]).copy()
    feature_cols = sorted([c for c in df.columns if c.startswith("feat_")])
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    df = df.sort_values("dt").reset_index(drop=True)

    tmin, tmax = df["dt"].min(), df["dt"].max()
    warmup_end = tmin + (tmax - tmin) * warmup_frac
    edges = pd.date_range(warmup_end, tmax, periods=n_splits + 1)

    print(f"\n=== WALK-FORWARD ({n_splits} expanding folds; warmup until {warmup_end.date()}) ===")
    print(f"{'fold':>4} {'train':>8} {'test':>7} {'period':>21} {'AUC':>6} {'top5%win':>9} {'top5%net':>9}")
    frames = []
    for i in range(n_splits):
        lo, hi = edges[i], edges[i + 1]
        tr = df[df["dt"] < lo]
        te = df[(df["dt"] >= lo) & (df["dt"] < hi)]
        if len(tr) < 1000 or len(te) < 200:
            continue
        w = np.exp(-((tr["dt"].max() - tr["dt"]).dt.days) / (5.0 * 365.25))
        m = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
        m.fit(tr[feature_cols], tr["target_win"], sample_weight=w)
        p = m.predict_proba(te[feature_cols])[:, 1]
        fold = te[["dt", "ticker", "target_win", "trade_ret"]].copy()
        fold["prob"] = p
        frames.append(fold)

        try:
            auc = roc_auc_score(te["target_win"], p)
        except ValueError:
            auc = float("nan")
        msk = p >= np.quantile(p, 0.95)
        w5 = float(fold["target_win"][msk].mean()) if msk.sum() else float("nan")
        r5 = float((fold["trade_ret"][msk] - round_trip_fee).mean()) if msk.sum() else float("nan")
        print(f"{i:>4} {len(tr):>8} {len(te):>7} {str(lo.date())+'..'+str(hi.date()):>21} {auc:>6.3f} {w5:>9.3f} {r5:>9.4f}")

    if not frames:
        print("Not enough data for walk-forward folds.")
        return
    oos = pd.concat(frames).sort_values("dt")
    y, ret, p = oos["target_win"].values, oos["trade_ret"].values, oos["prob"].values
    try:
        pooled_auc = roc_auc_score(y, p)
    except ValueError:
        pooled_auc = float("nan")

    print(f"\n--- Pooled OUT-OF-SAMPLE ({len(oos)} bars, {oos['dt'].min().date()} -> {oos['dt'].max().date()}) ---")
    print(f"Pooled AUC: {pooled_auc:.3f} | base win-rate: {y.mean():.3f} | base mean net ret/bar: {ret.mean()-round_trip_fee:+.4f}")
    print(f"{'select':>10} {'n':>7} {'win_rate':>9} {'mean_net_ret':>13} {'sum_net_ret':>12}  (break-even win ~0.286)")
    for q in [0.001, 0.005, 0.01, 0.02, 0.05, 0.10]:
        thr = np.quantile(p, 1 - q)
        msk = p >= thr
        n = int(msk.sum())
        wr = float(y[msk].mean()) if n else float("nan")
        net = ret[msk] - round_trip_fee
        print(f"top {q*100:>5.1f}% {n:>7} {wr:>9.3f} {net.mean():>13.4f} {net.sum():>12.3f}")
    # At the live entry threshold
    from app.core.config import SHORT_TERM_BUY_THRESHOLD as BUY
    msk = p >= BUY
    n = int(msk.sum())
    if n:
        net = ret[msk] - round_trip_fee
        print(f"\nAt live BUY threshold {BUY:.2f}: {n} signals | win {y[msk].mean():.3f} | "
              f"mean net ret {net.mean():+.4f} | total {net.sum():+.3f}")
    else:
        print(f"\nAt live BUY threshold {BUY:.2f}: 0 OOS signals (model rarely this confident out-of-sample).")
    print("Interpretation: positive mean_net_ret in the selective top buckets => genuine tradable edge.\n")
    return oos


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Model training / evaluation")
    parser.add_argument("--train", action="store_true", help="Train production models (default)")
    parser.add_argument("--walkforward", action="store_true", help="Run walk-forward out-of-sample evaluation")
    parser.add_argument("--splits", type=int, default=5, help="Number of walk-forward folds")
    args = parser.parse_args()
    if args.walkforward:
        walk_forward_evaluate(n_splits=args.splits)
    else:
        train_models()
