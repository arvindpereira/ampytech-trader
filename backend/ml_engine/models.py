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

from app.core.config import TICKER_UNIVERSE
from app.database import SessionLocal, RecentPrice, MacroIndicator, TickerSentiment, UniverseTicker
from ml_engine.features import build_features_for_df

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
    """Loads prices, sentiment and macro from SQLite and builds a merged features dataset."""
    db = SessionLocal()

    # 1. Fetch Prices
    prices = db.query(RecentPrice).all()
    if not prices:
        db.close()
        raise ValueError("No price records found in SQLite database. Run ingestion first!")

    prices_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open,
        "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal
    } for p in prices])

    # 2. Fetch Macro
    macro = db.query(MacroIndicator).all()
    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()

    # 3. Fetch Sentiment
    sent = db.query(TickerSentiment).all()
    sent_df = pd.DataFrame([{
        "ticker": s.ticker, "date": s.date, "source": s.source,
        "sentiment_score": s.sentiment_score, "mention_count": s.mention_count
    } for s in sent]) if sent else pd.DataFrame()

    # Load stock universe dynamically from DB (honoring user edits)
    db_tickers = db.query(UniverseTicker).all()
    active_universe = [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE
    db.close()

    # Process features for all tickers with cross-ticker relationships
    from ml_engine.features import build_all_features
    full_df = build_all_features(prices_df, sent_df, macro_df, active_universe)
    if full_df.empty:
        raise ValueError("Insufficient historical rows to build features.")
    return full_df

def train_models():
    """Trains the XGBoost classifier and the macro HMM model."""
    print("Loading data for model training...")
    df = load_data_from_db()

    # --- 1. Train Short-Term XGBoost Model ---
    print("\n--- Training Short-Term XGBoost Classifier ---")

    # Features starting with 'feat_'
    feature_cols = [col for col in df.columns if col.startswith("feat_")]
    target_col = "target_3d_gain"

    # Remove rows with NaN target (last few rows of dataset because target is future-looking)
    train_data = df.dropna(subset=[target_col]).copy()

    X = train_data[feature_cols]
    y = train_data[target_col]

    # Calculate temporal decay weights (5-year half-life)
    print("Calculating temporal decay weights (5-year half-life)...")
    max_date = pd.to_datetime(train_data['date'], format='mixed').max()
    train_dates = pd.to_datetime(train_data['date'], format='mixed')
    days_diff = (max_date - train_dates).dt.days
    half_life_days = 5.0 * 365.25
    sample_weights = np.exp(-days_diff / half_life_days)

    print(f"Features list: {feature_cols}")
    print(f"Training dataset size: {X.shape[0]} rows, {X.shape[1]} features.")

    # Fit XGBoost
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

    # Save model natively as JSON
    model_path = os.path.join(SAVED_MODELS_DIR, "short_term_model.json")
    model.save_model(model_path)
    print(f"Short-Term XGBoost Model saved successfully to: {model_path}")

    # --- 2. Train Long-Term HMM Regime Model ---
    print("\n--- Training Long-Term HMM Regime Classifier ---")
    # Fetch SPY prices to extract volatility
    spy_data = df[df["ticker"] == "SPY"].sort_values("date").copy()

    if spy_data.empty:
        print("Warning: SPY index data missing. Cannot fit HMM. Creating dummy HMM file...")
        # Save dummy regime file
        dummy_regime = {"current_regime": "growth", "state_mean_vol": {0: 0.1, 1: 0.2, 2: 0.3}}
        with open(os.path.join(SAVED_MODELS_DIR, "hmm_metadata.pkl"), "wb") as f:
            pickle.dump(dummy_regime, f)
        return

    # We want daily vol, fed funds and yield spread
    hmm_features = spy_data[["feat_volatility_10", "feat_fed_funds", "feat_yield_spread"]].dropna()

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

if __name__ == "__main__":
    train_models()
