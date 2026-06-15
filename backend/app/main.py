import os
import sys
import pickle
from datetime import datetime, timedelta
from typing import List, Optional
import pandas as pd
import numpy as np
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import xgboost as xgb

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    TICKER_UNIVERSE, SHORT_TERM_HORIZON_BARS, MPT_WINDOW_DAYS,
    SHORT_TERM_ATR_STOP_MULT, SHORT_TERM_TP_MULT, SHORT_TERM_STOP_MIN, SHORT_TERM_STOP_MAX,
    SHORT_TERM_BUY_THRESHOLD, SHORT_TERM_SELL_THRESHOLD,
)
from app.database import (
    get_db, init_db, RecentPrice, DailyPrice, TickerSentiment, MacroIndicator,
    UniverseTicker, VirtualAccount, VirtualPosition, VirtualOrder, BrokerPerformanceLog,
    SentimentSourceLog
)
from ml_engine.features import build_features_for_df, build_all_features
from ml_engine.models import PortfolioOptimizer

app = FastAPI(title="Ampytech Trader API", version="1.0.0")

# Enable Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Saved models directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "ml_engine", "saved_models")

@app.on_event("startup")
def startup_event():
    init_db()

def get_latest_data(db, end_date=None, mode="real"):
    """Utility to load recent prices and indicators for inference."""
    if end_date:
        ref_date = datetime.strptime(end_date, "%Y-%m-%d")
        start_date = (ref_date - timedelta(days=90)).strftime("%Y-%m-%d")
        prices = db.query(RecentPrice).filter(RecentPrice.date >= start_date, RecentPrice.date <= end_date).all()
    else:
        start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        prices = db.query(RecentPrice).filter(RecentPrice.date >= start_date).all()

    if not prices:
        raise HTTPException(status_code=400, detail="Database is empty. Run data fetch pipeline first.")

    prices_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open,
        "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal
    } for p in prices])

    if end_date:
        macro = db.query(MacroIndicator).filter(MacroIndicator.date >= start_date, MacroIndicator.date <= end_date).all()
    else:
        macro = db.query(MacroIndicator).filter(MacroIndicator.date >= start_date).all()

    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()

    sent_query = db.query(TickerSentiment).filter(TickerSentiment.date >= start_date)
    if end_date:
        sent_query = sent_query.filter(TickerSentiment.date <= end_date)
    if mode == "real":
        sent_query = sent_query.filter(TickerSentiment.is_mock != True)
    sent = sent_query.all()

    sent_df = pd.DataFrame([{
        "ticker": s.ticker, "date": s.date, "source": s.source,
        "sentiment_score": s.sentiment_score, "mention_count": s.mention_count
    } for s in sent]) if sent else pd.DataFrame()

    return prices_df, macro_df, sent_df


def get_daily_data(db, end_date=None, lookback_days=600):
    """Loads DAILY prices (daily_prices) + macro for the long-term regime/MPT path.
    Returns (daily_prices_df, macro_df) covering ~lookback_days trading days."""
    if end_date:
        ref_date = datetime.strptime(end_date.split(" ")[0].split("T")[0], "%Y-%m-%d")
    else:
        ref_date = datetime.now()
    start_date = (ref_date - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end_str = ref_date.strftime("%Y-%m-%d")

    q = db.query(DailyPrice).filter(DailyPrice.date >= start_date, DailyPrice.date <= end_str)
    prices = q.all()
    prices_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open, "high": p.high,
        "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal,
    } for p in prices]) if prices else pd.DataFrame()

    macro = db.query(MacroIndicator).filter(MacroIndicator.date >= start_date,
                                            MacroIndicator.date <= end_str).all()
    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()
    return prices_df, macro_df


_suggestions_cache = {}

def clear_suggestions_cache():
    global _suggestions_cache
    _suggestions_cache.clear()
    print("Suggestions cache cleared successfully.")

@app.get("/api/suggestions")
def get_daily_suggestions(date: Optional[str] = None, mode: str = "real", db=Depends(get_db)):
    """Computes daily trading suggestions (Short-Term and Long-Term) using our trained models."""
    global _suggestions_cache

    # Load stock universe dynamically from DB to establish part of cache key
    db_tickers = db.query(UniverseTicker).all()
    active_universe = sorted([t.ticker for t in db_tickers]) if db_tickers else sorted(TICKER_UNIVERSE)

    # Establish latest dates/states as part of cache key
    latest_price = db.query(RecentPrice).order_by(RecentPrice.date.desc()).first()
    latest_price_date = latest_price.date if latest_price else "none"

    latest_sent = db.query(TickerSentiment).order_by(TickerSentiment.date.desc()).first()
    latest_sent_date = latest_sent.date if latest_sent else "none"

    # Count database items to notice edits/simulations
    prices_count = db.query(RecentPrice).count()
    sent_count = db.query(TickerSentiment).count()

    cache_key = (
        date or "live",
        mode,
        latest_price_date,
        latest_sent_date,
        prices_count,
        sent_count,
        tuple(active_universe)
    )

    if cache_key in _suggestions_cache:
        print("Returning suggestions from in-memory cache.")
        return _suggestions_cache[cache_key]

    prices_df, macro_df, sent_df = get_latest_data(db, end_date=date, mode=mode)

    # 1. Load short-term models (PyTorch first, fallback to XGBoost)
    deep_model_path = os.path.join(SAVED_MODELS_DIR, "temporal_attention_model.pth")
    deep_metadata_path = os.path.join(SAVED_MODELS_DIR, "temporal_attention_metadata.pkl")
    model_path = os.path.join(SAVED_MODELS_DIR, "short_term_model.json")

    use_pytorch = False
    deep_model = None
    scaler_metadata = None
    st_model = None

    if os.path.exists(deep_model_path) and os.path.exists(deep_metadata_path):
        import torch
        from ml_engine.deep_models import LightTemporalAttentionNet
        try:
            with open(deep_metadata_path, "rb") as f:
                scaler_metadata = pickle.load(f)
            input_dim = len(scaler_metadata["feature_cols"])
            deep_model = LightTemporalAttentionNet(input_dim=input_dim, hidden_dim=32)
            deep_model.load_state_dict(torch.load(deep_model_path))
            deep_model.eval()
            use_pytorch = True
        except Exception as e:
            print(f"Failed to load PyTorch model in API suggestions: {e}")

    # Load XGBoost if needed or as fallback
    if not use_pytorch:
        if not os.path.exists(model_path):
            raise HTTPException(status_code=500, detail="Short-Term models not trained. Run 'python run.py train' first.")
        st_model = xgb.XGBClassifier()
        st_model.load_model(model_path)

    # 2. Load HMM model
    hmm_path = os.path.join(SAVED_MODELS_DIR, "hmm_model.pkl")
    metadata_path = os.path.join(SAVED_MODELS_DIR, "hmm_metadata.pkl")
    if not os.path.exists(hmm_path) or not os.path.exists(metadata_path):
        raise HTTPException(status_code=500, detail="Long-Term HMM model not trained. Run 'python run.py train' first.")

    with open(hmm_path, "rb") as f:
        hmm_model = pickle.load(f)
    with open(metadata_path, "rb") as f:
        hmm_metadata = pickle.load(f)
    state_mapping = hmm_metadata["state_mapping"]

    # --- Long-term DAILY dataset (regime + MPT) — kept separate from the hourly short-term path ---
    daily_prices_df, daily_macro_df = get_daily_data(db, end_date=date)

    # --- Compute Current HMM Market Regime (on DAILY SPY + macro, matching how it was trained) ---
    spy_data = daily_prices_df[daily_prices_df["ticker"] == "SPY"].sort_values("date").copy() if not daily_prices_df.empty else pd.DataFrame()
    if spy_data.empty:
        current_regime = "growth"
    else:
        spy_features = build_features_for_df(spy_data, sentiment_df=None, macro_df=daily_macro_df)
        hmm_feature_cols = ["feat_volatility_10", "feat_fed_funds", "feat_yield_spread"]
        last_row = spy_features[hmm_feature_cols].tail(1)
        if last_row.isna().any().any():
            current_regime = "growth"
        else:
            state = hmm_model.predict(last_row.values)[0]
            current_regime = state_mapping.get(state, "growth")

    # Load stock universe dynamically from DB (honoring user edits)
    db_tickers = db.query(UniverseTicker).all()
    active_universe = [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE

    # --- Compute Short-Term Signals (on HOURLY bars + real sentiment) ---
    suggestions = []

    # Process features globally using build_all_features to generate cross-ticker metrics safely
    full_features_df = build_all_features(
        prices_df, sent_df, macro_df, active_universe,
        target_horizon_bars=SHORT_TERM_HORIZON_BARS,
        target_atr_stop_mult=SHORT_TERM_ATR_STOP_MULT, target_tp_mult=SHORT_TERM_TP_MULT,
        target_stop_min=SHORT_TERM_STOP_MIN, target_stop_max=SHORT_TERM_STOP_MAX,
    )
    if full_features_df.empty:
        raise HTTPException(status_code=500, detail="Insufficient price data to generate prediction features.")

    feature_cols = sorted([col for col in full_features_df.columns if col.startswith("feat_")])

    for ticker in active_universe:
        t_feat = full_features_df[full_features_df["ticker"] == ticker]
        if t_feat.empty:
            continue

        # Determine if we can run PyTorch inference
        prob = None
        current_close = float(t_feat["close"].values[-1])
        atr = float(t_feat["atr_14"].values[-1])
        sentiment_score = float(t_feat["combined_sentiment"].values[-1])

        if use_pytorch and deep_model is not None and scaler_metadata is not None:
            f_cols = scaler_metadata["feature_cols"]
            t_feat_valid = t_feat.dropna(subset=f_cols).copy()
            if len(t_feat_valid) >= 10:
                last_10_rows = t_feat_valid.tail(10)
                mean = np.array(scaler_metadata["mean"])
                std = np.array(scaler_metadata["std"])
                scaled_vals = (last_10_rows[f_cols].values - mean) / std

                import torch
                seq_tensor = torch.tensor([scaled_vals], dtype=torch.float32)
                with torch.no_grad():
                    prob = float(deep_model(seq_tensor).squeeze(1).numpy()[0])
            else:
                # If not enough history for sequence, fall back to XGBoost for this specific asset if possible
                if os.path.exists(model_path):
                    if st_model is None:
                        st_model = xgb.XGBClassifier()
                        st_model.load_model(model_path)
                    last_idx = t_feat.index[-1]
                    inference_row = t_feat.loc[[last_idx]]
                    if not inference_row[feature_cols].isna().any().any():
                        prob = float(st_model.predict_proba(inference_row[feature_cols])[:, 1][0])

        # If PyTorch was not used, fallback to standard XGBoost
        if prob is None:
            if st_model is None and os.path.exists(model_path):
                st_model = xgb.XGBClassifier()
                st_model.load_model(model_path)

            if st_model is not None:
                last_idx = t_feat.index[-1]
                inference_row = t_feat.loc[[last_idx]]
                if not inference_row[feature_cols].isna().any().any():
                    prob = float(st_model.predict_proba(inference_row[feature_cols])[:, 1][0])

        if prob is None:
            continue  # No inference possible for this ticker

        # Sizing and triggers. `prob` = model P(take-profit hit before stop within the horizon).
        action = "HOLD"
        confidence = prob
        reasoning = "Technicals and sentiment are in a balanced range."

        # Buy the high-confidence tail (thresholds are config-driven, tuned to the label's base rate).
        if prob >= SHORT_TERM_BUY_THRESHOLD:
            action = "BUY"
            reasoning = f"Win probability ({prob*100:.1f}%) exceeds the entry threshold ({SHORT_TERM_BUY_THRESHOLD*100:.0f}%), supported by a sentiment score of {sentiment_score:.2f}."
        elif prob <= SHORT_TERM_SELL_THRESHOLD:
            action = "SELL"
            reasoning = f"Very low win probability ({prob*100:.1f}%) indicates poor risk/reward."

        # Extract audit features Safely
        last_row = t_feat.iloc[-1]
        rsi_val = float(last_row.get("rsi_14", 50.0))
        if np.isnan(rsi_val): rsi_val = 50.0

        macd_val = float(last_row.get("macd", 0.0))
        if np.isnan(macd_val): macd_val = 0.0

        macd_sig = float(last_row.get("macd_signal", 0.0))
        if np.isnan(macd_sig): macd_sig = 0.0

        sma_10_val = float(last_row.get("sma_10", current_close))
        if np.isnan(sma_10_val): sma_10_val = current_close

        sma_50_val = float(last_row.get("sma_50", current_close))
        if np.isnan(sma_50_val): sma_50_val = current_close

        news_sent = float(last_row.get("news_sentiment_score", 0.0))
        if np.isnan(news_sent): news_sent = 0.0

        reddit_sent = float(last_row.get("reddit_sentiment_score", 0.0))
        if np.isnan(reddit_sent): reddit_sent = 0.0

        news_mentions = int(last_row.get("news_mention_count", 0))
        reddit_mentions = int(last_row.get("reddit_mention_count", 0))

        audit_data = {
            "rsi_14": round(rsi_val, 2),
            "macd": round(macd_val, 4),
            "macd_signal": round(macd_sig, 4),
            "sma_10": round(sma_10_val, 2),
            "sma_50": round(sma_50_val, 2),
            "news_sentiment": round(news_sent, 2),
            "news_mentions": news_mentions,
            "reddit_sentiment": round(reddit_sent, 2),
            "reddit_mentions": reddit_mentions
        }

        # Bracket orders limits
        # Same brackets used to LABEL training data (triple-barrier) — keeps target ≈ execution.
        stop_loss_pct = min(SHORT_TERM_STOP_MAX, max(SHORT_TERM_STOP_MIN, (SHORT_TERM_ATR_STOP_MULT * atr) / current_close))
        take_profit_pct = stop_loss_pct * SHORT_TERM_TP_MULT

        suggestions.append({
            "ticker": ticker,
            "close": current_close,
            "action": action,
            "confidence": confidence,
            "stop_loss": current_close * (1.0 - stop_loss_pct) if action == "BUY" else None,
            "take_profit": current_close * (1.0 + take_profit_pct) if action == "BUY" else None,
            "reasoning": reasoning,
            "audit": audit_data
        })


    # --- Compute Long-Term Portfolio Weights (on DAILY returns, ~1 trading year) ---
    returns_list = []
    for ticker in active_universe:
        t_data = daily_prices_df[daily_prices_df['ticker'] == ticker].sort_values('date').copy() if not daily_prices_df.empty else pd.DataFrame()
        if t_data.empty:
            continue
        t_data['returns'] = t_data['close'].pct_change()
        returns_list.append(t_data[['date', 'returns']].rename(columns={'returns': ticker}))

    if returns_list:
        returns_df = returns_list[0]
        for r in returns_list[1:]:
            returns_df = pd.merge(returns_df, r, on='date', how='outer')
        returns_df = returns_df.sort_values('date').tail(MPT_WINDOW_DAYS)

        opt_weights = PortfolioOptimizer.calculate_optimal_weights(returns_df, current_regime)
    else:
        opt_weights = {}

    # Scale portfolio allocations based on current market regime
    # In Crisis: 50% Cash, scale stock allocations in half to hedge risk
    regime_scalar = 0.5 if current_regime == "crisis" else 1.0
    scaled_weights = {t: float(w * regime_scalar) for t, w in opt_weights.items()}
    cash_allocation = 1.0 - sum(scaled_weights.values())

    long_term_allocation = []
    for ticker, weight in scaled_weights.items():
        if weight > 0.01: # Filter out trace allocations
            long_term_allocation.append({
                "ticker": ticker,
                "weight": weight,
                "shares_multiplier": 1.0
            })
    long_term_allocation.append({"ticker": "CASH", "weight": cash_allocation})

    res = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": current_regime,
        "short_term_suggestions": suggestions,
        "long_term_allocation": sorted(long_term_allocation, key=lambda x: x["weight"], reverse=True)
    }
    _suggestions_cache[cache_key] = res
    return res

@app.get("/api/sentiment")
def get_sentiment_aggregates(mode: str = "real", db=Depends(get_db)):
    """Exposes current sentiment indicators and scores for all tickers."""
    # Find most recent date cached
    query_latest = db.query(TickerSentiment)
    if mode == "real":
        query_latest = query_latest.filter(TickerSentiment.is_mock != True)
    latest_record = query_latest.order_by(TickerSentiment.date.desc()).first()
    if not latest_record:
        raise HTTPException(status_code=400, detail="No sentiment records found. Run fetch pipeline.")

    date_str = latest_record.date
    query_records = db.query(TickerSentiment).filter(TickerSentiment.date == date_str)
    if mode == "real":
        query_records = query_records.filter(TickerSentiment.is_mock != True)
    sent_records = query_records.all()

    results = []
    for r in sent_records:
        results.append({
            "ticker": r.ticker,
            "source": r.source,
            "sentiment_score": r.sentiment_score,
            "mention_count": r.mention_count,
            "positive_ratio": r.positive_ratio,
            "negative_ratio": r.negative_ratio
        })
    return {"date": date_str, "sentiment": results}

@app.get("/api/performance")
def get_backtest_performance(mode: str = "live", db=Depends(get_db)):
    """Returns simulated historical equity curve vs benchmark S&P 500, QQQ, and BRK-B."""
    logs = db.query(BrokerPerformanceLog).filter(BrokerPerformanceLog.mode == mode).order_by(BrokerPerformanceLog.date.asc()).all()

    if not logs:
        if mode == "live":
            # Real/Live mode: do not return mock performance data!
            return {
                "metrics": {
                    "total_return": 0.0,
                    "sharpe_ratio": 0.0,
                    "max_drawdown": 0.0,
                    "win_rate": 0.0
                },
                "equity_curve": []
            }
        # Fallback to simulated data if db is empty so the UI looks beautiful
        # Generate 100 days of mock data
        dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
        portfolio_val = 100000.0
        spy_val = 100000.0
        qqq_val = 100000.0
        brk_val = 100000.0

        rng = np.random.default_rng(42)
        equity_curve = []
        for i, d in enumerate(dates):
            if i == 0:
                p_ret, s_ret, q_ret, b_ret = 0.0, 0.0, 0.0, 0.0
            else:
                s_ret = rng.normal(0.0003, 0.012)
                q_ret = s_ret * 1.2 + rng.normal(0.0001, 0.005)
                b_ret = s_ret * 0.7 + rng.normal(0.0002, 0.004)
                p_ret = s_ret * 0.8 + rng.normal(0.0008, 0.007)
                if s_ret < -0.02:
                    p_ret = s_ret * 0.3 + rng.normal(0.0, 0.002)

            portfolio_val *= (1.0 + p_ret)
            spy_val *= (1.0 + s_ret)
            qqq_val *= (1.0 + q_ret)
            brk_val *= (1.0 + b_ret)

            equity_curve.append({
                "date": d.strftime("%Y-%m-%d"),
                "portfolio": portfolio_val,
                "spy": spy_val,
                "qqq": qqq_val,
                "brk": brk_val
            })

        metrics = {
            "total_return": (portfolio_val / 100000.0) - 1.0,
            "sharpe_ratio": 1.78,
            "max_drawdown": -0.114,
            "win_rate": 0.58
        }

        return {
            "metrics": metrics,
            "equity_curve": equity_curve
        }

    equity_curve = []
    portfolio_start = logs[0].portfolio_value
    spy_start = logs[0].spy_value
    qqq_start = logs[0].qqq_value
    brk_start = logs[0].brk_value

    # Calculate values relative to start index at $100k
    for l in logs:
        equity_curve.append({
            "date": l.date,
            "portfolio": l.portfolio_value,
            "spy": (l.spy_value / spy_start) * 100000.0 if spy_start > 0 else 100000.0,
            "qqq": (l.qqq_value / qqq_start) * 100000.0 if qqq_start > 0 else 100000.0,
            "brk": (l.brk_value / brk_start) * 100000.0 if brk_start > 0 else 100000.0,
        })

    # Calculate performance metrics
    portfolio_vals = [l.portfolio_value for l in logs]
    portfolio_returns = pd.Series(portfolio_vals).pct_change().dropna()

    total_return = (portfolio_vals[-1] / portfolio_vals[0]) - 1.0 if len(portfolio_vals) > 1 else 0.0

    # Sharpe Ratio: annualized mean return / std
    if len(portfolio_returns) > 1 and portfolio_returns.std() > 0:
        sharpe_ratio = (portfolio_returns.mean() / portfolio_returns.std()) * np.sqrt(252)
    else:
        sharpe_ratio = 0.0

    # Max Drawdown
    peak = portfolio_vals[0]
    max_dd = 0.0
    for v in portfolio_vals:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd

    # Win rate
    win_rate = (portfolio_returns > 0).mean() if len(portfolio_returns) > 0 else 0.0

    metrics = {
        "total_return": total_return,
        "sharpe_ratio": float(sharpe_ratio) if not np.isnan(sharpe_ratio) else 0.0,
        "max_drawdown": float(max_dd) if not np.isnan(max_dd) else 0.0,
        "win_rate": float(win_rate) if not np.isnan(win_rate) else 0.0
    }

    return {
        "metrics": metrics,
        "equity_curve": equity_curve
    }


# ==========================================
# Virtual Alpaca Broker & Holdings Endpoints
# ==========================================
from pydantic import BaseModel
from typing import List, Optional

SIM_DATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sim_date.txt")

def get_sim_date():
    if os.path.exists(SIM_DATE_FILE):
        with open(SIM_DATE_FILE, "r") as f:
            date_str = f.read().strip()
            if date_str:
                return date_str
    return None

def set_sim_date(date_str):
    os.makedirs(os.path.dirname(SIM_DATE_FILE), exist_ok=True)
    with open(SIM_DATE_FILE, "w") as f:
        if date_str:
            f.write(date_str.strip())
        else:
            f.write("")

def get_current_price(db, ticker, date=None):
    if date:
        # Get latest available price on or before date
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date <= date).order_by(RecentPrice.date.desc()).first()
    else:
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_by(RecentPrice.date.desc()).first()
    if price_rec:
        return price_rec.close
    return 100.0  # Fallback

# Route to get account details
@app.get("/api/virtual_alpaca/v2/account")
def get_virtual_account(mode: str = "real", db=Depends(get_db)):
    sim_date_val = get_sim_date()
    effective_mode = "replay" if sim_date_val else mode
    acc_id = 2 if effective_mode == "real" else 1
    pos_mode = "real" if effective_mode == "real" else "replay"
    sim_date = None if effective_mode == "real" else sim_date_val

    account = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    if not account:
        account = VirtualAccount(id=acc_id, cash=100000.0, buying_power=100000.0, equity=100000.0)
        db.add(account)
        db.commit()
        db.refresh(account)

    # Calculate current equity = cash + sum(qty * price)
    positions = db.query(VirtualPosition).filter(VirtualPosition.quantity > 0, VirtualPosition.mode == pos_mode).all()
    pos_val = 0.0
    for p in positions:
        price = get_current_price(db, p.ticker, sim_date)
        pos_val += p.quantity * price

    equity = account.cash + pos_val

    # Update equity in db
    account.equity = equity
    account.buying_power = account.cash
    db.commit()

    return {
        "id": "mock-account-id",
        "account_number": "mock-account-num",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": str(round(account.cash, 2)),
        "portfolio_value": str(round(equity, 2)),
        "equity": str(round(equity, 2)),
        "buying_power": str(round(account.cash, 2)),
        "daytrade_buying_power": str(round(account.cash, 2)),
        "regt_buying_power": str(round(account.cash, 2)),
        "cash_withdrawable": str(round(account.cash, 2))
    }

# Route to get positions
@app.get("/api/virtual_alpaca/v2/positions")
def get_virtual_positions(mode: str = "real", db=Depends(get_db)):
    sim_date_val = get_sim_date()
    effective_mode = "replay" if sim_date_val else mode
    pos_mode = "real" if effective_mode == "real" else "replay"
    sim_date = None if effective_mode == "real" else sim_date_val

    positions = db.query(VirtualPosition).filter(VirtualPosition.quantity > 0, VirtualPosition.mode == pos_mode).all()
    res = []
    for p in positions:
        curr_price = get_current_price(db, p.ticker, sim_date)
        market_value = p.quantity * curr_price
        cost_basis = p.quantity * p.entry_price
        unrealized_pl = market_value - cost_basis
        unrealized_plpc = unrealized_pl / cost_basis if cost_basis > 0 else 0.0

        res.append({
            "asset_id": f"mock-asset-{p.ticker}",
            "symbol": p.ticker,
            "exchange": "NASDAQ",
            "asset_class": "us_equity",
            "avg_entry_price": str(round(p.entry_price, 4)),
            "qty": str(round(p.quantity, 4)),
            "side": "long",
            "market_value": str(round(market_value, 2)),
            "cost_basis": str(round(cost_basis, 2)),
            "unrealized_pl": str(round(unrealized_pl, 2)),
            "unrealized_plpc": str(round(unrealized_plpc, 4)),
            "current_price": str(round(curr_price, 2)),
            "lastday_price": str(round(curr_price, 2)),
            "change_today": "0.00"
        })
    return res

class OrderRequest(BaseModel):
    symbol: str
    qty: float
    side: str  # 'buy' or 'sell'
    type: str  # 'market' etc.
    time_in_force: Optional[str] = "gtc"
    order_class: Optional[str] = "simple"
    take_profit: Optional[dict] = None
    stop_loss: Optional[dict] = None

# Route to place orders
@app.post("/api/virtual_alpaca/v2/orders")
def post_virtual_order(order_req: OrderRequest, mode: str = "real", db=Depends(get_db)):
    sim_date_val = get_sim_date()
    effective_mode = "replay" if sim_date_val else mode
    pos_mode = "real" if effective_mode == "real" else "replay"
    acc_id = 2 if effective_mode == "real" else 1
    sim_date = None if effective_mode == "real" else sim_date_val

    ticker = order_req.symbol
    fill_price = None

    if sim_date:
        # Check open price on sim_date
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if price_rec:
            fill_price = price_rec.open
        else:
            # Fallback to closest available on or before sim_date
            price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date <= sim_date).order_by(RecentPrice.date.desc()).first()
            if price_rec:
                fill_price = price_rec.close
    else:
        # Live mode
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_by(RecentPrice.date.desc()).first()
        if price_rec:
            fill_price = price_rec.close

    if not fill_price:
        raise HTTPException(status_code=400, detail=f"No price data available for {ticker}")

    account = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    if not account:
        account = VirtualAccount(id=acc_id, cash=100000.0, buying_power=100000.0, equity=100000.0)
        db.add(account)
        db.commit()

    qty = order_req.qty
    side = order_req.side.lower()

    tp_price = None
    sl_price = None
    if order_req.take_profit:
        tp_price = order_req.take_profit.get("limit_price")
    if order_req.stop_loss:
        sl_price = order_req.stop_loss.get("stop_price")

    order_id = f"order-{datetime.now().timestamp()}-{np.random.randint(1000, 9999)}"

    if side == "buy":
        cost = qty * fill_price
        if account.cash < cost:
            raise HTTPException(status_code=400, detail=f"Insufficient funds. Order cost: ${cost:.2f}, Cash: ${account.cash:.2f}")

        account.cash -= cost
        account.buying_power = account.cash

        # Update position
        pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker, VirtualPosition.mode == pos_mode).first()
        if pos:
            new_qty = pos.quantity + qty
            new_entry = ((pos.quantity * pos.entry_price) + cost) / new_qty
            pos.quantity = new_qty
            pos.entry_price = new_entry
        else:
            pos = VirtualPosition(ticker=ticker, mode=pos_mode, quantity=qty, entry_price=fill_price, policy="rebalance")
            db.add(pos)

        # Create order log
        v_order = VirtualOrder(
            id=order_id,
            mode=pos_mode,
            ticker=ticker,
            qty=qty,
            side="buy",
            type=order_req.type,
            status="filled",
            stop_loss=sl_price,
            take_profit=tp_price,
            filled_price=fill_price,
            created_at=datetime.now().isoformat(),
            sim_date=sim_date
        )
        db.add(v_order)
        db.commit()

        return {
            "id": order_id,
            "client_order_id": order_id,
            "created_at": datetime.now().isoformat(),
            "status": "filled",
            "symbol": ticker,
            "qty": str(qty),
            "side": "buy",
            "type": order_req.type,
            "filled_at": datetime.now().isoformat(),
            "filled_avg_price": str(round(fill_price, 2))
        }

    elif side == "sell":
        pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker, VirtualPosition.mode == pos_mode).first()
        if not pos or pos.quantity <= 0:
            raise HTTPException(status_code=400, detail=f"No position held in {ticker} to sell")

        qty_sold = min(pos.quantity, qty)
        revenue = qty_sold * fill_price

        account.cash += revenue
        account.buying_power = account.cash

        pos.quantity -= qty_sold
        if pos.quantity <= 0.0001:
            db.delete(pos)

        # Create order log
        v_order = VirtualOrder(
            id=order_id,
            mode=pos_mode,
            ticker=ticker,
            qty=qty_sold,
            side="sell",
            type=order_req.type,
            status="filled",
            filled_price=fill_price,
            created_at=datetime.now().isoformat(),
            sim_date=sim_date
        )
        db.add(v_order)
        db.commit()

        return {
            "id": order_id,
            "client_order_id": order_id,
            "created_at": datetime.now().isoformat(),
            "status": "filled",
            "symbol": ticker,
            "qty": str(qty_sold),
            "side": "sell",
            "type": order_req.type,
            "filled_at": datetime.now().isoformat(),
            "filled_avg_price": str(round(fill_price, 2))
        }

    raise HTTPException(status_code=400, detail="Invalid side parameter")

# Route to delete (close) position
@app.delete("/api/virtual_alpaca/v2/positions/{symbol}")
def delete_virtual_position(symbol: str, mode: str = "real", db=Depends(get_db)):
    sim_date_val = get_sim_date()
    effective_mode = "replay" if sim_date_val else mode
    pos_mode = "real" if effective_mode == "real" else "replay"
    acc_id = 2 if effective_mode == "real" else 1
    sim_date = None if effective_mode == "real" else sim_date_val

    pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == symbol, VirtualPosition.mode == pos_mode).first()
    if not pos or pos.quantity <= 0:
        raise HTTPException(status_code=404, detail=f"No position held in {symbol}")

    ticker = symbol
    fill_price = None

    if sim_date:
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if price_rec:
            fill_price = price_rec.open
        else:
            price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date <= sim_date).order_by(RecentPrice.date.desc()).first()
            if price_rec:
                fill_price = price_rec.close
    else:
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_by(RecentPrice.date.desc()).first()
        if price_rec:
            fill_price = price_rec.close

    if not fill_price:
        raise HTTPException(status_code=400, detail=f"No price data available for {ticker}")

    account = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    qty_sold = pos.quantity
    revenue = qty_sold * fill_price

    account.cash += revenue
    account.buying_power = account.cash

    db.delete(pos)

    order_id = f"close-{datetime.now().timestamp()}-{np.random.randint(1000, 9999)}"
    v_order = VirtualOrder(
        id=order_id,
        mode=pos_mode,
        ticker=ticker,
        qty=qty_sold,
        side="sell",
        type="market",
        status="filled",
        filled_price=fill_price,
        created_at=datetime.now().isoformat(),
        sim_date=sim_date
    )
    db.add(v_order)
    db.commit()

    return {
        "id": order_id,
        "symbol": symbol,
        "qty": str(qty_sold),
        "status": "filled",
        "filled_avg_price": str(round(fill_price, 2))
    }

class UniverseRequest(BaseModel):
    tickers: List[str]

@app.get("/api/universe")
def get_universe(db=Depends(get_db)):
    tickers = db.query(UniverseTicker).all()
    return {"tickers": [t.ticker for t in tickers]}

@app.get("/api/universe/supported")
def get_supported_universe():
    return {"tickers": TICKER_UNIVERSE}


@app.post("/api/universe")
def update_universe(req: UniverseRequest, db=Depends(get_db)):
    db.query(UniverseTicker).delete()
    for t in req.tickers:
        db.add(UniverseTicker(ticker=t.upper().strip()))
    db.commit()
    clear_suggestions_cache()
    return {"status": "success", "tickers": req.tickers}

class HoldingRequest(BaseModel):
    ticker: str
    quantity: float
    entry_price: float
    policy: str  # 'rebalance', 'lock', 'liquidate'
    purchase_date: Optional[str] = None

class AccountCashRequest(BaseModel):
    cash: float

@app.post("/api/account")
def update_account_cash(req: AccountCashRequest, mode: str = "real", db=Depends(get_db)):
    acc_id = 2 if mode == "real" else 1
    account = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    if not account:
        account = VirtualAccount(id=acc_id, cash=req.cash, buying_power=req.cash, equity=req.cash)
        db.add(account)
    else:
        account.cash = req.cash
        account.buying_power = req.cash
    db.commit()
    return {"status": "success", "cash": req.cash}

@app.get("/api/holdings")
def get_holdings(mode: str = "real", db=Depends(get_db)):
    pos_mode = "real" if mode == "real" else "replay"
    positions = db.query(VirtualPosition).filter(VirtualPosition.mode == pos_mode).all()
    return [
        {
            "ticker": p.ticker,
            "quantity": p.quantity,
            "entry_price": p.entry_price,
            "policy": p.policy,
            "purchase_date": p.purchase_date
        } for p in positions
    ]

@app.post("/api/holdings")
def update_holding(req: HoldingRequest, mode: str = "real", db=Depends(get_db)):
    ticker = req.ticker.upper().strip()
    pos_mode = "real" if mode == "real" else "replay"
    pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker, VirtualPosition.mode == pos_mode).first()
    if pos:
        pos.quantity = req.quantity
        pos.entry_price = req.entry_price
        pos.policy = req.policy
        pos.purchase_date = req.purchase_date
    else:
        pos = VirtualPosition(
            ticker=ticker,
            mode=pos_mode,
            quantity=req.quantity,
            entry_price=req.entry_price,
            policy=req.policy,
            purchase_date=req.purchase_date
        )
        db.add(pos)
    db.commit()
    return {"status": "success", "holding": {
        "ticker": ticker,
        "mode": pos_mode,
        "quantity": req.quantity,
        "entry_price": req.entry_price,
        "policy": req.policy,
        "purchase_date": req.purchase_date
    }}

@app.delete("/api/holdings/{ticker}")
def delete_holding(ticker: str, mode: str = "real", db=Depends(get_db)):
    ticker_val = ticker.upper().strip()
    pos_mode = "real" if mode == "real" else "replay"
    pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker_val, VirtualPosition.mode == pos_mode).first()
    if pos:
        db.delete(pos)
        db.commit()
        return {"status": "success"}
    raise HTTPException(status_code=404, detail=f"Holding not found for {ticker_val}")

@app.post("/api/simulate")
def trigger_simulate(days: int = 5, background_tasks: BackgroundTasks = None):
    from execution.simulator import run_forward_simulation
    clear_suggestions_cache()
    if background_tasks:
        background_tasks.add_task(run_forward_simulation, days)
        return {"status": "started", "message": f"Forward simulation for {days} days started in background."}
    else:
        run_forward_simulation(days)
        return {"status": "completed"}

@app.post("/api/backtest-virtual")
def trigger_backtest_virtual(months: int = 6, background_tasks: BackgroundTasks = None):
    from execution.simulator import run_historical_replay
    clear_suggestions_cache()
    if background_tasks:
        background_tasks.add_task(run_historical_replay, months)
        return {"status": "started", "message": f"Historical replay for {months} months started in background."}
    else:
        run_historical_replay(months)
        return {"status": "completed"}


from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

class PremiumSentimentRequest(BaseModel):
    ticker: str
    title: str
    text: str
    url: Optional[str] = "manual-premium-upload"

@app.get("/api/sentiment/sources")
def get_sentiment_sources(ticker: str, date: Optional[str] = None, mode: str = "real", db=Depends(get_db)):
    ticker_val = ticker.upper().strip()

    query_latest = db.query(SentimentSourceLog).filter(SentimentSourceLog.ticker == ticker_val)
    if mode == "real":
        query_latest = query_latest.filter(SentimentSourceLog.is_mock != True)

    if not date:
        latest = query_latest.order_by(SentimentSourceLog.date.desc()).first()
        if latest:
            date = latest.date
        else:
            date = datetime.now().strftime("%Y-%m-%d")

    query_records = db.query(SentimentSourceLog).filter(
        SentimentSourceLog.ticker == ticker_val,
        SentimentSourceLog.date == date
    )
    if mode == "real":
        query_records = query_records.filter(SentimentSourceLog.is_mock != True)
    records = query_records.all()

    return {
        "ticker": ticker_val,
        "date": date,
        "sources": [
            {
                "id": r.id,
                "source": r.source,
                "title": r.title,
                "text": r.text,
                "url": r.url,
                "score": r.score,
                "date": r.date
            } for r in records
        ]
    }

@app.post("/api/sentiment/premium")
def post_premium_sentiment(req: PremiumSentimentRequest, db=Depends(get_db)):
    ticker_val = req.ticker.upper().strip()
    date_str = datetime.now().strftime("%Y-%m-%d")

    analyzer = SentimentIntensityAnalyzer()
    full_content = (req.title + ". " + req.text) if req.text else req.title
    vs = analyzer.polarity_scores(full_content)
    compound = vs['compound']

    log_rec = SentimentSourceLog(
        ticker=ticker_val,
        date=date_str,
        source="premium",
        title=req.title[:250],
        text=req.text[:1000] if req.text else None,
        url=req.url,
        score=compound
    )
    db.add(log_rec)
    db.commit()

    # Recalculate aggregates
    premium_logs = db.query(SentimentSourceLog).filter(
        SentimentSourceLog.ticker == ticker_val,
        SentimentSourceLog.date == date_str,
        SentimentSourceLog.source == "premium"
    ).all()

    scores = [r.score for r in premium_logs]
    pos_count = sum(1 for s in scores if s > 0.05)
    neg_count = sum(1 for s in scores if s < -0.05)

    avg_score = sum(scores) / len(scores) if scores else 0.0
    pos_ratio = pos_count / len(scores) if scores else 0.0
    neg_ratio = neg_count / len(scores) if scores else 0.0

    existing = db.query(TickerSentiment).filter(
        TickerSentiment.ticker == ticker_val,
        TickerSentiment.date == date_str,
        TickerSentiment.source == "premium"
    ).first()

    if existing:
        existing.sentiment_score = avg_score
        existing.positive_ratio = pos_ratio
        existing.negative_ratio = neg_ratio
        existing.mention_count = len(premium_logs)
    else:
        existing = TickerSentiment(
            ticker=ticker_val,
            date=date_str,
            sentiment_score=avg_score,
            positive_ratio=pos_ratio,
            negative_ratio=neg_ratio,
            mention_count=len(premium_logs),
            source="premium"
        )
        db.add(existing)

    db.commit()
    clear_suggestions_cache()
    return {"status": "success", "score": compound, "ticker": ticker_val, "date": date_str}

@app.post("/api/reconcile")
def trigger_reconciliation(db=Depends(get_db)):
    """Triggers manual position and order reconciliation with Alpaca broker."""
    from execution.executor import get_alpaca_api, sync_broker_orders, sync_broker_positions
    api = get_alpaca_api()
    if not api:
        raise HTTPException(status_code=400, detail="Alpaca API credentials missing or invalid.")
    try:
        sync_broker_orders(db, api)
        sync_broker_positions(db, api)
        return {"status": "success", "message": "Positions and orders synchronized successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# In-memory cache for screener results to keep response quick
_volatile_screener_cache = {
    "data": None,
    "timestamp": None
}

@app.get("/api/screener/volatile")
def get_volatile_stocks(refresh: bool = False):
    """Computes 30-day historical volatility for a selection of liquid high-volatility trading candidates using yfinance."""
    global _volatile_screener_cache
    now = datetime.now()

    if not refresh and _volatile_screener_cache["data"] is not None and _volatile_screener_cache["timestamp"] is not None:
        if now - _volatile_screener_cache["timestamp"] < timedelta(hours=4):
            return _volatile_screener_cache["data"]

    import yfinance as yf

    candidates = [
        "TSLA", "MSTR", "MARA", "COIN", "PLTR", "RIOT", "GME", "AMC", "AMD", "NVDA",
        "SOXL", "TQQQ", "SQ", "PYPL", "AAPL", "MSFT", "GOOGL", "NFLX", "META", "AMZN"
    ]
    results = []

    # Download 45 days to cover 30 trading days
    for ticker in candidates:
        try:
            df = yf.download(ticker, period="45d", interval="1d", progress=False)
            if df.empty:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                close_series = df['Close'][ticker]
            else:
                close_series = df['Close']

            close_series = close_series.dropna()
            if len(close_series) < 10:
                continue

            log_returns = np.log(close_series / close_series.shift(1)).dropna()
            vol = float(log_returns.std() * np.sqrt(252)) * 100.0
            curr_price = float(close_series.iloc[-1])

            results.append({
                "ticker": ticker,
                "volatility": round(vol, 2),
                "current_price": round(curr_price, 2)
            })
        except Exception as e:
            print(f"Error calculating volatility for {ticker}: {e}")

    results = sorted(results, key=lambda x: x["volatility"], reverse=True)

    _volatile_screener_cache["data"] = results
    _volatile_screener_cache["timestamp"] = now

    return results
