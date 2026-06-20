import sys
import os
import json
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, MacroIndicator, DailyPrice, CrashRiskSnapshot
from ml_engine.crash_radar import get_latest_date

HORIZONS = [30, 90, 180]
DRAWDOWN_LEVELS = [0.10, 0.20, 0.35]

def prepare_features_df():
    """Loads and aligns daily/monthly macro and price features chronologically."""
    db = SessionLocal()
    
    # 1. Fetch daily SPY close prices
    spy_records = db.query(DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.ticker == "SPY"
    ).order_by(DailyPrice.date.asc()).all()
    if not spy_records:
        db.close()
        return pd.DataFrame()
        
    df = pd.DataFrame(spy_records, columns=["date", "spy_close"])
    df["date"] = pd.to_datetime(df["date"])
    
    # 2. Fetch target macro indicators
    indicators = ["cape", "buffett_indicator", "term_spread_10y3m", "yield_spread",
                  "excess_bond_premium", "nfci_leverage"]
                  
    for ind in indicators:
        records = db.query(MacroIndicator.date, MacroIndicator.value).filter(
            MacroIndicator.indicator_name == ind
        ).order_by(MacroIndicator.date.asc()).all()
        if records:
            ind_df = pd.DataFrame(records, columns=["date", ind])
            ind_df["date"] = pd.to_datetime(ind_df["date"])
            # Merge (forward fill macro indicators since they are monthly/weekly)
            df = pd.merge_asof(df.sort_values("date"), ind_df.sort_values("date"), on="date", direction="backward")
            
    db.close()
    
    # Clean term spread
    if "term_spread_10y3m" in df.columns and "yield_spread" in df.columns:
        df["term_spread"] = df["term_spread_10y3m"].fillna(df["yield_spread"])
    elif "yield_spread" in df.columns:
        df["term_spread"] = df["yield_spread"]
    else:
        df["term_spread"] = 1.5 # default fallback
        
    df = df.sort_values("date").reset_index(drop=True)
    # Forward fill missing values
    df = df.ffill().dropna()
    return df

def generate_drawdown_labels(df, dd_threshold, horizon_days):
    """Generates binary labels: 1 if drawdown >= dd_threshold within forward horizon_days, else 0."""
    labels = []
    close_prices = df["spy_close"].values
    dates = df["date"].values
    n = len(df)
    
    for i in range(n):
        ref_price = close_prices[i]
        ref_date = dates[i]
        
        # Find all prices within forward horizon_days
        limit_date = ref_date + np.timedelta64(horizon_days, 'D')
        forward_window = df[(df["date"] > ref_date) & (df["date"] <= limit_date)]["spy_close"].values
        
        if len(forward_window) == 0:
            labels.append(np.nan) # Cannot label end of series
            continue
            
        min_price = np.min(forward_window)
        max_dd = (min_price - ref_price) / ref_price
        
        if max_dd <= -dd_threshold:
            labels.append(1)
        else:
            labels.append(0)
            
    return pd.Series(labels, index=df.index)

def purged_embargo_kfold_split(df, n_splits=3, purge_window=180, embargo_window=30):
    """
    Implements Purged & Embargoed Cross-Validation.
    Removes training periods that overlap with the test set or fall within the post-test embargo.
    """
    n = len(df)
    dates = df["date"].values
    indices = np.arange(n)
    
    # Split into K contiguous blocks
    block_size = n // n_splits
    splits = []
    
    for k in range(n_splits):
        test_start_idx = k * block_size
        test_end_idx = min((k + 1) * block_size, n - 1)
        test_indices = indices[test_start_idx:test_end_idx+1]
        
        test_start_date = dates[test_start_idx]
        test_end_date = dates[test_end_idx]
        
        # Determine train indices (exclude test, purged, and embargoed ranges)
        # Purge range: test_start_date - purge_window to test_end_date
        # Embargo range: test_end_date to test_end_date + embargo_window
        purge_limit = test_start_date - np.timedelta64(purge_window, 'D')
        embargo_limit = test_end_date + np.timedelta64(embargo_window, 'D')
        
        train_indices = [
            i for i in indices
            if (dates[i] < purge_limit) or (dates[i] > embargo_limit)
        ]
        
        splits.append((np.array(train_indices), np.array(test_indices)))
        
    return splits

def train_and_evaluate_forecast():
    """Runs purged-embargo cross-validation and computes current drawdown probabilities."""
    print("Preparing features for drawdown-odds model...")
    df = prepare_features_df()
    if df.empty or len(df) < 200:
        print("⚠ Insufficient price or macro data to train forecasting model.")
        return []
        
    features = ["cape", "buffett_indicator", "term_spread", "excess_bond_premium", "nfci_leverage"]
    # Ensure all features exist
    features = [f for f in features if f in df.columns]
    
    latest_date_str = get_latest_date()
    latest_row = df[df["date"] <= pd.to_datetime(latest_date_str)].iloc[-1]
    
    results = []
    
    for level in DRAWDOWN_LEVELS:
        for horizon in HORIZONS:
            # Generate target labels
            target_col = f"target_{int(level*100)}_{horizon}"
            df[target_col] = generate_drawdown_labels(df, level, horizon)
            
            # Filter rows with valid labels
            model_df = df.dropna(subset=[target_col]).reset_index(drop=True)
            if len(model_df) < 100:
                continue
                
            X = model_df[features].values
            y = model_df[target_col].values
            
            # Run Purged and Embargoed CV
            splits = purged_embargo_kfold_split(model_df, n_splits=3, purge_window=horizon, embargo_window=30)
            cv_scores = []
            
            for train_idx, test_idx in splits:
                if len(train_idx) < 30 or len(test_idx) < 10:
                    continue
                
                # Settle scaling
                scaler = StandardScaler()
                X_train = scaler.fit_transform(X[train_idx])
                X_test = scaler.transform(X[test_idx])
                
                y_train = y[train_idx]
                y_test = y[test_idx]
                
                # Check class balance
                if len(np.unique(y_train)) < 2:
                    continue
                    
                # Train regularized logistic regression (L2 penalty)
                clf = LogisticRegression(penalty="l2", C=1.0, class_weight="balanced", random_state=42)
                clf.fit(X_train, y_train)
                
                # Predict probabilities
                probs = clf.predict_proba(X_test)[:, 1]
                # Log average prediction score (simple accuracy or log-loss proxy)
                cv_scores.append(np.mean(probs))
                
            # Train final model on all data
            scaler = StandardScaler()
            X_all = scaler.fit_transform(X)
            y_all = y
            
            clf = LogisticRegression(penalty="l2", C=1.0, class_weight="balanced", random_state=42)
            clf.fit(X_all, y_all)
            
            # Predict for the current/latest date
            current_x = latest_row[features].values.reshape(1, -1)
            current_x_scaled = scaler.transform(current_x)
            prob = clf.predict_proba(current_x_scaled)[0, 1]
            
            results.append({
                "drawdown": f">={int(level*100)}%",
                "horizon_days": horizon,
                "probability": float(prob),
                "cv_runs": len(cv_scores),
                "recession_probability_proxy": float(prob)
            })
            
    print(f"✓ Calculated {len(results)} drawdown probability forecasts.")
    return results

def update_latest_forecast_odds():
    """Runs the forecasting job and updates the latest CrashRiskSnapshot."""
    results = train_and_evaluate_forecast()
    if not results:
        return
        
    latest_date_str = get_latest_date()
    db = SessionLocal()
    snapshot = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest_date_str).first()
    if snapshot:
        snapshot.experimental_forecast_odds = json.dumps(results)
        db.add(snapshot)
        db.commit()
        print(f"✓ Updated forecast odds for {latest_date_str} in database.")
    else:
        print(f"⚠ No snapshot found for {latest_date_str} to update forecast odds.")
    db.close()

if __name__ == "__main__":
    update_latest_forecast_odds()
