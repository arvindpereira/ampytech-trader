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
    SHORT_TERM_SIGNAL_RATE, SERVED_MODEL,
)
import json
from datetime import datetime
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
    def calculate_optimal_weights(returns_df, target_regime, expected_return_tilt=None):
        """
        Runs Mean-Variance Optimization using Ledoit-Wolf shrinkage covariance.
        Returns weight allocation dictionary for tickers.

        `expected_return_tilt` (optional dict {ticker: annualized-return adjustment}) is a
        Black-Litterman-style "view": it is *added* to the historical-mean expected returns before
        optimization, so e.g. an insider-buying score nudges the optimizer to overweight those names.
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
        # If we are in high volatility contraction (crisis), shrink weights or enforce strict diversification (max 10% weight)
        num_assets = len(tickers)
        max_w = 0.10 if target_regime == "crisis" else 0.25

        # Convert expected returns and cov to numpy arrays
        er = exp_returns.values
        if expected_return_tilt:
            er = er + np.array([float(expected_return_tilt.get(t, 0.0)) for t in tickers])
        cov = cov_matrix
        rf = 0.04

        # Solve for Maximum Sharpe Ratio using scipy optimize SLSQP
        import scipy.optimize as sco

        def negative_sharpe(w, er, cov, rf):
            p_return = np.sum(er * w)
            p_volatility = np.sqrt(np.dot(w.T, np.dot(cov, w)))
            return -(p_return - rf) / (p_volatility + 1e-9)

        # Equal weight initial guess
        init_guess = np.ones(num_assets) / num_assets
        # Constraints: Weights sum to 1.0, long-only, bounded by max_w
        bounds = tuple((0.0, max_w) for _ in range(num_assets))
        constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0})

        res = sco.minimize(negative_sharpe, init_guess, args=(er, cov, rf),
                           method='SLSQP', bounds=bounds, constraints=constraints)

        if res.success:
            best_weights = res.x
        else:
            # Fallback to equal weights if optimizer fails
            best_weights = init_guess

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
    feature_cols = sorted([col for col in df.columns if col.startswith("feat_") and col != "feat_atr_14"])
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

    # Calibrate the live BUY threshold for the served model (writes threshold.json).
    try:
        calibrate_threshold()
    except Exception as e:
        print(f"Threshold calibration skipped: {e}")
    print("Model training execution complete.\n")


THRESHOLD_PATH = os.path.join(SAVED_MODELS_DIR, "threshold.json")


def calibrate_threshold(round_trip_fee=0.001, signal_rate=None):
    """Derives the live BUY threshold for the SERVED model and writes saved_models/threshold.json.

    A fixed absolute probability does not transfer across models (XGBoost vs PyTorch have different
    prob scales) and grid-searching it on the test metric overfits (PR#2 review C2/C6). Instead we:
      1. train the served model on a time-ordered 80% and predict the most-recent 20% (honest OOS),
      2. set the threshold to the quantile that yields a TARGET signal rate (a fixed selectivity prior,
         not chosen to maximize returns), then
      3. *report* the resulting OOS win-rate and net return so we know whether that selectivity is
         actually profitable.
    Inference/backtest read this threshold (per served model) instead of the static config default.
    """
    signal_rate = signal_rate if signal_rate is not None else SHORT_TERM_SIGNAL_RATE
    if SERVED_MODEL != "xgboost":
        print(f"calibrate_threshold: SERVED_MODEL={SERVED_MODEL} not auto-calibrated; "
              f"keeping config threshold {SHORT_TERM_BUY_THRESHOLD}. (Only 'xgboost' is auto-calibrated.)")
        return

    df = load_data_from_db().dropna(subset=["target_win", "trade_ret"]).copy()
    feature_cols = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    df = df.sort_values("dt").reset_index(drop=True)
    split = int(len(df) * 0.8)
    tr, te = df.iloc[:split], df.iloc[split:]
    if len(te) < 200:
        print("calibrate_threshold: not enough holdout rows; skipping.")
        return

    w = np.exp(-((tr["dt"].max() - tr["dt"]).dt.days) / (5.0 * 365.25))
    m = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                          subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
    m.fit(tr[feature_cols], tr["target_win"], sample_weight=w)
    p = m.predict_proba(te[feature_cols])[:, 1]
    y = te["target_win"].values
    ret = te["trade_ret"].values

    threshold = float(np.quantile(p, 1.0 - signal_rate))
    msk = p >= threshold
    n = int(msk.sum())
    win_rate = float(y[msk].mean()) if n else float("nan")
    mean_net = float((ret[msk] - round_trip_fee).mean()) if n else float("nan")

    payload = {
        "model_type": "xgboost",
        "signal_rate": signal_rate,
        "threshold": round(threshold, 4),
        "holdout_rows": int(len(te)),
        "oos_signals": n,
        "oos_win_rate": round(win_rate, 4) if n else None,
        "oos_mean_net_ret": round(mean_net, 5) if n else None,
        "base_win_rate": round(float(y.mean()), 4),
        "calibrated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(THRESHOLD_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n--- Threshold calibration (served={SERVED_MODEL}, target top {signal_rate*100:.2f}%) ---")
    print(f"  Threshold {threshold:.4f} -> {n} OOS signals | win {win_rate:.3f} (base {y.mean():.3f}) "
          f"| mean net/trade {mean_net:+.4f}")
    print(f"  {'PROFITABLE at this selectivity' if (n and mean_net > 0) else 'NOT yet profitable — treat as no edge'}")
    print(f"  Saved to {THRESHOLD_PATH}\n")
    return payload


def load_buy_threshold():
    """Returns the calibrated BUY threshold for the served model, or the config fallback."""
    try:
        if os.path.exists(THRESHOLD_PATH):
            with open(THRESHOLD_PATH) as f:
                payload = json.load(f)
            if payload.get("model_type") == SERVED_MODEL and "threshold" in payload:
                return float(payload["threshold"])
    except Exception as e:
        print(f"Could not read calibrated threshold ({e}); using config default.")
    return SHORT_TERM_BUY_THRESHOLD


def find_optimal_threshold(tr_fold, feature_cols, target_col="target_win", fallback_default=0.23):
    """
    Fits an inner XGBoost model on the first 80% of the training fold and finds the BUY threshold
    on the last 20% that maximizes the F1-score on the validation subset.
    Requires at least 2 validation signals to prevent selecting an overfit, noisy threshold.
    """
    try:
        df_sorted = tr_fold.sort_values("dt").reset_index(drop=True)
        split_idx = int(len(df_sorted) * 0.8)
        inner_tr = df_sorted.iloc[:split_idx]
        inner_val = df_sorted.iloc[split_idx:]
        
        if len(inner_tr) < 200 or len(inner_val) < 50:
            return float(fallback_default)
            
        # Exponential weights for decay
        w = np.exp(-((inner_tr["dt"].max() - inner_tr["dt"]).dt.days) / (5.0 * 365.25))
        
        # Fit inner model
        m_inner = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                                    subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
        m_inner.fit(inner_tr[feature_cols], inner_tr[target_col], sample_weight=w)
        
        p_val = m_inner.predict_proba(inner_val[feature_cols])[:, 1]
        y_val = inner_val[target_col].values
        
        best_threshold = fallback_default
        best_f1 = -1.0
        
        # Grid search from 0.05 to 0.95
        for threshold in np.linspace(0.05, 0.95, 181):
            preds = (p_val >= threshold).astype(int)
            tp = np.sum((preds == 1) & (y_val == 1))
            fp = np.sum((preds == 1) & (y_val == 0))
            fn = np.sum((preds == 0) & (y_val == 1))
            
            precision = tp / (tp + fp + 1e-9)
            recall = tp / (tp + fn + 1e-9)
            f1 = 2 * precision * recall / (precision + recall + 1e-9)
            
            if tp + fp >= 2:
                if f1 > best_f1:
                    best_f1 = f1
                    best_threshold = threshold
                    
        return float(best_threshold)
    except Exception as e:
        print(f"Error in find_optimal_threshold: {e}")
        return float(fallback_default)


def precalculate_exits(oos_df, prices_df, horizon=14):
    """
    Pre-calculates the exit date and exit price for every row in oos_df
    based on the triple barrier method. Optimized with dictionary index lookups.
    """
    price_groups = {}
    date_to_idx_groups = {}
    for ticker, grp in prices_df.groupby("ticker"):
        sorted_grp = grp.sort_values("date").reset_index(drop=True)
        price_groups[ticker] = sorted_grp
        date_to_idx_groups[ticker] = {d: idx for idx, d in enumerate(sorted_grp["date"].values)}
        
    exit_dates = []
    exit_prices = []
    
    tickers = oos_df["ticker"].values
    dates = oos_df["date"].values
    closes = oos_df["close"].values
    atrs = oos_df["atr_14"].values if "atr_14" in oos_df.columns else np.full(len(oos_df), np.nan)
    
    for i in range(len(oos_df)):
        ticker = tickers[i]
        dt = dates[i]
        
        if ticker not in price_groups:
            exit_dates.append(None)
            exit_prices.append(None)
            continue
            
        grp = price_groups[ticker]
        date_to_idx = date_to_idx_groups[ticker]
        
        entry_idx = date_to_idx.get(dt)
        if entry_idx is None:
            exit_dates.append(None)
            exit_prices.append(None)
            continue
            
        entry_close = float(closes[i])
        atr = float(atrs[i])
        if np.isnan(atr) or atr <= 0.0:
            atr = entry_close * 0.01
            
        sl_pct = min(SHORT_TERM_STOP_MAX, max(SHORT_TERM_STOP_MIN, (SHORT_TERM_ATR_STOP_MULT * atr) / entry_close))
        tp_pct = sl_pct * SHORT_TERM_TP_MULT
        stop_price = entry_close * (1.0 - sl_pct)
        target_price = entry_close * (1.0 + tp_pct)
        
        exit_date = None
        exit_price = None
        
        grp_high = grp["high"].values
        grp_low = grp["low"].values
        grp_close = grp["close"].values
        grp_date = grp["date"].values
        n_grp = len(grp)
        
        for k in range(1, horizon + 1):
            curr_idx = entry_idx + k
            if curr_idx >= n_grp:
                exit_date = grp_date[-1]
                exit_price = float(grp_close[-1])
                break
                
            high_k = float(grp_high[curr_idx])
            low_k = float(grp_low[curr_idx])
            close_k = float(grp_close[curr_idx])
            date_k = grp_date[curr_idx]
            
            tp_hit = (high_k >= target_price)
            sl_hit = (low_k <= stop_price)
            
            if tp_hit and sl_hit:
                exit_date = date_k
                exit_price = stop_price
                break
            elif sl_hit:
                exit_date = date_k
                exit_price = stop_price
                break
            elif tp_hit:
                exit_date = date_k
                exit_price = target_price
                break
            elif k == horizon:
                exit_date = date_k
                exit_price = close_k
                break
                
        exit_dates.append(exit_date)
        exit_prices.append(exit_price)
        
    oos_df_copy = oos_df.copy()
    oos_df_copy["exit_date"] = exit_dates
    oos_df_copy["exit_price"] = exit_prices
    return oos_df_copy


def simulate_portfolio_chronological(oos_df, prices_df, initial_capital=100000.0, max_allocation=0.10, fee_pct=0.0005, horizon=14):
    """
    Chronological portfolio simulator enforcing capital limits, max allocation,
    no overlaps, exits, and fees.
    """
    if oos_df.empty:
        return [], {}
        
    # 1. Sort all unique dates in the test set
    unique_dates = sorted(oos_df["date"].unique())
    
    # 2. Pre-calculate exit dates and prices
    oos_df = precalculate_exits(oos_df, prices_df, horizon=horizon)
    
    # Group signals by date
    signals_by_date = {}
    for _, row in oos_df.iterrows():
        dt = row["date"]
        ticker = row["ticker"]
        prob = float(row["prob"])
        thr = float(row["selected_threshold"])
        exit_dt = row["exit_date"]
        exit_p = row["exit_price"]
        entry_c = float(row["close"])
        
        if prob >= thr:
            if dt not in signals_by_date:
                signals_by_date[dt] = []
            signals_by_date[dt].append({
                "ticker": ticker,
                "prob": prob,
                "exit_date": exit_dt,
                "exit_price": exit_p,
                "entry_price": entry_c
            })
            
    # Simulation state
    cash = initial_capital
    active_trades = [] # list of dicts: {"ticker": t, "shares": s, "entry_price": p, "exit_date": d, "exit_price": ep}
    equity_curve = []
    
    # Group prices by date/ticker for marking positions to market
    price_by_date_ticker = {}
    for _, row in prices_df.iterrows():
        dt = row["date"]
        ticker = row["ticker"]
        price_by_date_ticker[(dt, ticker)] = float(row["close"])
        
    for dt in unique_dates:
        # A. Process exits on or before this bar
        trades_to_keep = []
        for trade in active_trades:
            if trade["exit_date"] <= dt:
                exit_price = trade["exit_price"]
                shares = trade["shares"]
                exit_value = shares * exit_price
                exit_fee = exit_value * fee_pct
                cash += (exit_value - exit_fee)
            else:
                trades_to_keep.append(trade)
        active_trades = trades_to_keep
        
        # B. Get current portfolio value
        current_equity = cash
        for trade in active_trades:
            curr_p = price_by_date_ticker.get((dt, trade["ticker"]), trade["entry_price"])
            current_equity += trade["shares"] * curr_p
            
        # C. Process entries on this bar
        signals = signals_by_date.get(dt, [])
        # Prioritize higher confidence signals
        signals = sorted(signals, key=lambda x: x["prob"], reverse=True)
        
        position_size = max_allocation * current_equity
        
        for sig in signals:
            ticker = sig["ticker"]
            if any(t["ticker"] == ticker for t in active_trades):
                continue
            if cash < position_size:
                continue
                
            entry_price = sig["entry_price"]
            entry_fee = position_size * fee_pct
            shares = (position_size - entry_fee) / entry_price
            
            cash -= position_size
            active_trades.append({
                "ticker": ticker,
                "shares": shares,
                "entry_price": entry_price,
                "exit_date": sig["exit_date"],
                "exit_price": sig["exit_price"]
            })
            
        # D. Calculate end-of-bar equity
        equity = cash
        for trade in active_trades:
            curr_p = price_by_date_ticker.get((dt, trade["ticker"]), trade["entry_price"])
            equity += trade["shares"] * curr_p
            
        equity_curve.append({
            "date": dt,
            "portfolio_value": equity,
            "cash": cash
        })
        
    # E. Compute metrics
    if not equity_curve:
        return [], {}
        
    eq_series = pd.Series([e["portfolio_value"] for e in equity_curve])
    eq_dates = pd.to_datetime([e["date"] for e in equity_curve])
    
    total_ret = (eq_series.iloc[-1] / initial_capital) - 1.0
    
    df_eq = pd.DataFrame({"date": eq_dates, "equity": eq_series})
    df_eq["day"] = df_eq["date"].dt.strftime("%Y-%m-%d")
    df_daily = df_eq.groupby("day").last().reset_index()
    
    daily_rets = df_daily["equity"].pct_change().dropna()
    if len(daily_rets) > 2:
        sharpe = (daily_rets.mean() / (daily_rets.std() + 1e-9)) * np.sqrt(252)
    else:
        sharpe = 0.0
        
    dd = (df_eq["equity"] - df_eq["equity"].cummax()) / df_eq["equity"].cummax()
    max_dd = dd.min()
    
    metrics = {
        "total_return": total_ret,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
        "final_value": eq_series.iloc[-1]
    }
    return equity_curve, metrics


def walk_forward_evaluate(n_splits=5, warmup_frac=0.4, round_trip_fee=0.001):
    """Honest out-of-sample evaluation comparing performance with vs. without alternative data features."""
    print("Loading data for walk-forward evaluation...")
    df = load_data_from_db().dropna(subset=["target_win", "trade_ret"]).copy()
    
    alt_feature_names = ["feat_insider_net_flow", "feat_insider_buy_count", "feat_insider_net_buyers",
                         "feat_insider_officer_buy", "feat_insider_cluster",
                         "feat_congress_buying_ratio", "feat_congress_buying_90d"]
    feature_cols_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    feature_cols_no_alt = sorted([c for c in feature_cols_all if c not in alt_feature_names])
    
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    df = df.sort_values("dt").reset_index(drop=True)

    tmin, tmax = df["dt"].min(), df["dt"].max()
    warmup_end = tmin + (tmax - tmin) * warmup_frac
    edges = pd.date_range(warmup_end, tmax, periods=n_splits + 1)

    print(f"\n=== WALK-FORWARD ({n_splits} expanding folds; warmup until {warmup_end.date()}) ===")
    print(f"{'fold':>4} {'train':>8} {'test':>7} {'period':>21} | {'ALL thr':>7} {'ALL AUC':>7} {'dyn win':>7} {'dyn net':>7} | {'NOALT thr':>9} {'NOALT AUC':>9} {'dyn win':>7} {'dyn net':>7}")
    
    frames_all = []
    frames_no_alt = []
    
    for i in range(n_splits):
        lo, hi = edges[i], edges[i + 1]
        tr = df[df["dt"] < lo]
        te = df[(df["dt"] >= lo) & (df["dt"] < hi)]
        if len(tr) < 1000 or len(te) < 200:
            continue
        w = np.exp(-((tr["dt"].max() - tr["dt"]).dt.days) / (5.0 * 365.25))
        
        # Train with ALL features
        m_all = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                                  subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
        m_all.fit(tr[feature_cols_all], tr["target_win"], sample_weight=w)
        p_all = m_all.predict_proba(te[feature_cols_all])[:, 1]
        
        # Optimize threshold on training fold
        thr_opt_all = find_optimal_threshold(tr, feature_cols_all, target_col="target_win", fallback_default=SHORT_TERM_BUY_THRESHOLD)
        
        fold_all = te[["dt", "date", "ticker", "target_win", "trade_ret", "open", "high", "low", "close", "atr_14"]].copy()
        fold_all["prob"] = p_all
        fold_all["selected_threshold"] = thr_opt_all
        frames_all.append(fold_all)
        
        # Train without alternative features
        m_no_alt = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05,
                                     subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
        m_no_alt.fit(tr[feature_cols_no_alt], tr["target_win"], sample_weight=w)
        p_no_alt = m_no_alt.predict_proba(te[feature_cols_no_alt])[:, 1]
        
        # Optimize threshold on training fold
        thr_opt_no_alt = find_optimal_threshold(tr, feature_cols_no_alt, target_col="target_win", fallback_default=SHORT_TERM_BUY_THRESHOLD)
        
        fold_no_alt = te[["dt", "date", "ticker", "target_win", "trade_ret", "open", "high", "low", "close", "atr_14"]].copy()
        fold_no_alt["prob"] = p_no_alt
        fold_no_alt["selected_threshold"] = thr_opt_no_alt
        frames_no_alt.append(fold_no_alt)

        try:
            auc_all = roc_auc_score(te["target_win"], p_all)
        except ValueError:
            auc_all = float("nan")
            
        try:
            auc_no_alt = roc_auc_score(te["target_win"], p_no_alt)
        except ValueError:
            auc_no_alt = float("nan")
            
        msk_all = p_all >= thr_opt_all
        w5_all = float(fold_all["target_win"][msk_all].mean()) if msk_all.sum() else float("nan")
        r5_all = float((fold_all["trade_ret"][msk_all] - round_trip_fee).mean()) if msk_all.sum() else float("nan")
        
        msk_no_alt = p_no_alt >= thr_opt_no_alt
        w5_no_alt = float(fold_no_alt["target_win"][msk_no_alt].mean()) if msk_no_alt.sum() else float("nan")
        r5_no_alt = float((fold_no_alt["trade_ret"][msk_no_alt] - round_trip_fee).mean()) if msk_no_alt.sum() else float("nan")
        
        print(f"{i:>4} {len(tr):>8} {len(te):>7} {str(lo.date())+'..'+str(hi.date()):>21} | "
              f"{thr_opt_all:>7.2f} {auc_all:>7.3f} {w5_all:>7.3f} {r5_all:>7.4f} | "
              f"{thr_opt_no_alt:>9.2f} {auc_no_alt:>9.3f} {w5_no_alt:>7.3f} {r5_no_alt:>7.4f}")

    if not frames_all:
        print("Not enough data for walk-forward folds.")
        return
        
    oos_all = pd.concat(frames_all).sort_values("dt")
    y_all, ret_all, p_all_vals = oos_all["target_win"].values, oos_all["trade_ret"].values, oos_all["prob"].values
    
    oos_no_alt = pd.concat(frames_no_alt).sort_values("dt")
    y_no_alt, ret_no_alt, p_no_alt_vals = oos_no_alt["target_win"].values, oos_no_alt["trade_ret"].values, oos_no_alt["prob"].values
    
    try:
        pooled_auc_all = roc_auc_score(y_all, p_all_vals)
    except ValueError:
        pooled_auc_all = float("nan")
        
    try:
        pooled_auc_no_alt = roc_auc_score(y_no_alt, p_no_alt_vals)
    except ValueError:
        pooled_auc_no_alt = float("nan")

    print(f"\n--- Pooled OUT-OF-SAMPLE Comparison ({len(oos_all)} bars, {oos_all['dt'].min().date()} -> {oos_all['dt'].max().date()}) ---")
    print(f"Model WITH Alternative Features:   Pooled AUC: {pooled_auc_all:.3f} | base win-rate: {y_all.mean():.3f} | base mean net ret/bar: {ret_all.mean()-round_trip_fee:+.4f}")
    print(f"Model WITHOUT Alternative Features: Pooled AUC: {pooled_auc_no_alt:.3f} | base win-rate: {y_no_alt.mean():.3f} | base mean net ret/bar: {ret_no_alt.mean()-round_trip_fee:+.4f}")
    
    # 1. Compare Dynamic Thresholding vs Static Thresholding
    # Dynamic (Nested) threshold performance
    msk_dyn_all = oos_all["prob"] >= oos_all["selected_threshold"]
    n_dyn_all = int(msk_dyn_all.sum())
    wr_dyn_all = float(oos_all["target_win"][msk_dyn_all].mean()) if n_dyn_all else float("nan")
    net_dyn_all = oos_all["trade_ret"][msk_dyn_all] - round_trip_fee
    
    msk_dyn_no_alt = oos_no_alt["prob"] >= oos_no_alt["selected_threshold"]
    n_dyn_no_alt = int(msk_dyn_no_alt.sum())
    wr_dyn_no_alt = float(oos_no_alt["target_win"][msk_dyn_no_alt].mean()) if n_dyn_no_alt else float("nan")
    net_dyn_no_alt = oos_no_alt["trade_ret"][msk_dyn_no_alt] - round_trip_fee
    
    print(f"\n--- Dynamic Nested Threshold OOS Results (F1 optimized) ---")
    print(f"  WITH ALT:    {n_dyn_all} signals | win {wr_dyn_all:.3f} | mean net {net_dyn_all.mean():+.4f} | total {net_dyn_all.sum():+.3f}")
    print(f"  WITHOUT ALT: {n_dyn_no_alt} signals | win {wr_dyn_no_alt:.3f} | mean net {net_dyn_no_alt.mean():+.4f} | total {net_dyn_no_alt.sum():+.3f}")

    # Print quantile table for compatibility and diagnostic transparency
    print(f"\n{'quantile':>10} | {'WITH ALT':<33} | {'WITHOUT ALT':<33}")
    print(f"{'':>10} | {'n':>6} {'win_rate':>8} {'mean_net':>8} {'sum_net':>8} | {'n':>6} {'win_rate':>8} {'mean_net':>8} {'sum_net':>8}")
    for q in [0.001, 0.005, 0.01, 0.02, 0.05, 0.10]:
        thr_all = np.quantile(p_all_vals, 1 - q)
        msk_all = p_all_vals >= thr_all
        n_all = int(msk_all.sum())
        wr_all = float(y_all[msk_all].mean()) if n_all else float("nan")
        net_all = ret_all[msk_all] - round_trip_fee
        
        thr_no_alt = np.quantile(p_no_alt_vals, 1 - q)
        msk_no_alt = p_no_alt_vals >= thr_no_alt
        n_no_alt = int(msk_no_alt.sum())
        wr_no_alt = float(y_no_alt[msk_no_alt].mean()) if n_no_alt else float("nan")
        net_no_alt = ret_no_alt[msk_no_alt] - round_trip_fee
        
        print(f"top {q*100:>5.1f}% | {n_all:>6} {wr_all:>8.3f} {net_all.mean():>8.4f} {net_all.sum():>8.2f} | {n_no_alt:>6} {wr_no_alt:>8.3f} {net_no_alt.mean():>8.4f} {net_no_alt.sum():>8.2f}")

    # 2. Run chronological portfolio-level simulations
    print("\nRunning chronological portfolio-level simulations (max 10% per trade, max 10 open positions)...")
    db = SessionLocal()
    prices_db = db.query(RecentPrice).all()
    prices_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open, "high": p.high, "low": p.low, "close": p.close
    } for p in prices_db])
    db.close()
    
    # Compute atr_14 for each ticker
    from ml_engine.features import compute_atr
    prices_df = prices_df.sort_values(["ticker", "date"]).reset_index(drop=True)
    atr_series = []
    for ticker, grp in prices_df.groupby("ticker"):
        grp = grp.sort_values("date").copy()
        grp["atr_14"] = compute_atr(grp, window=14)
        atr_series.append(grp)
    prices_df = pd.concat(atr_series, ignore_index=True)
    
    # 0.05% order execution fee (fee_pct=0.0005)
    curve_all, metrics_all = simulate_portfolio_chronological(oos_all, prices_df, initial_capital=100000.0, max_allocation=0.10, fee_pct=0.0005, horizon=SHORT_TERM_HORIZON_BARS)
    curve_no_alt, metrics_no_alt = simulate_portfolio_chronological(oos_no_alt, prices_df, initial_capital=100000.0, max_allocation=0.10, fee_pct=0.0005, horizon=SHORT_TERM_HORIZON_BARS)
    
    print(f"\n=== Chronological Portfolio-Level Simulation Results ===")
    print(f"{'Metric':<20} | {'WITH ALT':<15} | {'WITHOUT ALT':<15}")
    print(f"-" * 60)
    print(f"{'Total Return':<20} | {metrics_all.get('total_return', 0.0)*100:>13.2f}% | {metrics_no_alt.get('total_return', 0.0)*100:>13.2f}%")
    print(f"{'Sharpe Ratio':<20} | {metrics_all.get('sharpe_ratio', 0.0):>14.2f} | {metrics_no_alt.get('sharpe_ratio', 0.0):>14.2f}")
    print(f"{'Max Drawdown':<20} | {metrics_all.get('max_drawdown', 0.0)*100:>13.2f}% | {metrics_no_alt.get('max_drawdown', 0.0)*100:>13.2f}%")
    print(f"{'Final Value':<20} | ${metrics_all.get('final_value', 100000.0):>13,.2f} | ${metrics_no_alt.get('final_value', 100000.0):>13,.2f}")
    print("========================================================\n")
    
    return oos_all


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Model training / evaluation")
    parser.add_argument("--train", action="store_true", help="Train production models (default)")
    parser.add_argument("--walkforward", action="store_true", help="Run walk-forward out-of-sample evaluation")
    parser.add_argument("--calibrate", action="store_true", help="Re-calibrate the served-model BUY threshold")
    parser.add_argument("--splits", type=int, default=5, help="Number of walk-forward folds")
    args = parser.parse_args()
    if args.walkforward:
        walk_forward_evaluate(n_splits=args.splits)
    elif args.calibrate:
        calibrate_threshold()
    else:
        train_models()
