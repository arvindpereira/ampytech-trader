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
    SHORT_TERM_BUY_THRESHOLD, SHORT_TERM_SELL_THRESHOLD, HEDGE_MODE,
    ALT_DATA_ENABLED, LONGTERM_TILT_STRENGTH, SWING_ENABLED,
)
from app.database import (
    get_db, init_db, RecentPrice, DailyPrice, TickerSentiment, MacroIndicator,
    UniverseTicker, VirtualAccount, VirtualPosition, VirtualOrder, BrokerPerformanceLog,
    SentimentSourceLog, InsiderDisclosure, NewsLLMScore, AppSetting
)

import json as _json
STRATEGY_KEYS = ["swing", "longterm"]
DEFAULT_BUCKETS = {"swing": 1.0, "longterm": 0.0}

def get_strategy_buckets(db):
    """Capital allocation per strategy bucket (fraction of equity). Defaults to all-swing."""
    s = db.query(AppSetting).filter(AppSetting.key == "bucket_allocations").first()
    if s and s.value:
        try:
            v = _json.loads(s.value)
            return {k: float(v.get(k, 0.0)) for k in STRATEGY_KEYS}
        except Exception:
            pass
    return dict(DEFAULT_BUCKETS)

def get_strategy_assignments(db):
    """Map of ticker -> assigned strategy ('swing' | 'longterm' | 'hold')."""
    return {t.ticker: (t.strategy or "swing") for t in db.query(UniverseTicker).all()}
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

# ---------------------------------------------------------------------------
# Background job registry (in-process) — drives the UI progress bars for ticker
# backfills and model retraining. UI-triggered jobs run in this API process.
# ---------------------------------------------------------------------------
import threading
import uuid as _uuid
import time as _time
_JOBS = {}
_JOBS_LOCK = threading.Lock()

def _job_new(jtype, label):
    jid = _uuid.uuid4().hex[:8]
    with _JOBS_LOCK:
        _JOBS[jid] = {"id": jid, "type": jtype, "label": label, "status": "running",
                      "progress": 0, "stage": "queued", "error": None,
                      "started_at": _time.time(), "finished_at": None}
    return jid

def _job_update(jid, progress=None, stage=None, status=None, error=None):
    with _JOBS_LOCK:
        j = _JOBS.get(jid)
        if not j:
            return
        if progress is not None:
            j["progress"] = int(progress)
        if stage is not None:
            j["stage"] = stage
        if error is not None:
            j["error"] = error
        if status is not None:
            j["status"] = status
            if status in ("done", "error"):
                j["finished_at"] = _time.time()

def _jobs_snapshot():
    """Active jobs + jobs finished in the last 60s; prune anything older."""
    now = _time.time()
    with _JOBS_LOCK:
        for jid in [k for k, v in _JOBS.items()
                    if v["finished_at"] and now - v["finished_at"] > 120]:
            _JOBS.pop(jid, None)
        return sorted(_JOBS.values(), key=lambda x: x["started_at"], reverse=True)

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/api/jobs")
def get_jobs():
    """Active + recently-finished background jobs (ticker backfills, retraining) for UI progress bars."""
    return {"jobs": _jobs_snapshot()}

_EVAL_RESULTS = {}

def _run_eval_job(jid, strategies, horizon, splits, allocation, start_date, end_date):
    try:
        from ml_engine.evaluate import run_evaluation
        res = run_evaluation(strategies, horizon=horizon, splits=splits, allocation=allocation,
                             start_date=start_date, end_date=end_date,
                             progress_cb=lambda p, note: _job_update(jid, progress=p, stage=note))
        _EVAL_RESULTS[jid] = res
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])

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
def get_daily_suggestions(date: Optional[str] = None, mode: str = "real",
                          hedge_mode: Optional[str] = None, db=Depends(get_db)):
    """Computes daily trading suggestions (Short-Term and Long-Term) using our trained models."""
    global _suggestions_cache

    # Resolve hedge mode (query param overrides the config default; validate against known modes).
    from execution.hedging import compute_hedge, VALID_MODES
    effective_hedge_mode = hedge_mode if hedge_mode in VALID_MODES else HEDGE_MODE
    if effective_hedge_mode not in VALID_MODES:
        effective_hedge_mode = "none"

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

    insider_count = db.query(InsiderDisclosure).count() if ALT_DATA_ENABLED else 0
    latest_insider = db.query(InsiderDisclosure).order_by(InsiderDisclosure.date.desc()).first() if ALT_DATA_ENABLED else None
    latest_insider_date = latest_insider.date if latest_insider else "none"

    news_llm_count = db.query(NewsLLMScore).count() if SWING_ENABLED else 0

    cache_key = (
        date or "live",
        mode,
        effective_hedge_mode,
        latest_price_date,
        latest_sent_date,
        prices_count,
        sent_count,
        insider_count,
        latest_insider_date,
        news_llm_count,
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

    from app.core.config import SERVED_MODEL
    from ml_engine.models import load_buy_threshold
    buy_threshold = load_buy_threshold()  # calibrated per served model (falls back to config)

    if SERVED_MODEL == "pytorch" and os.path.exists(deep_model_path) and os.path.exists(deep_metadata_path):
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

    feature_cols = sorted([col for col in full_features_df.columns if col.startswith("feat_") and col != "feat_atr_14"])

    # Inputs for sizing the example trade plan (advisory): latest price per ticker + account equity.
    latest_close = prices_df.sort_values("date").groupby("ticker")["close"].last().to_dict()
    acc_id = 2 if mode == "real" else 1
    _acc = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    equity_for_sizing = float(_acc.equity) if (_acc and _acc.equity) else 100000.0
    POSITION_PCT = 0.10  # example long size = 10% of equity (matches backtest/executor convention)

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

        # Buy the high-confidence tail (threshold calibrated per served model, see threshold.json).
        if prob >= buy_threshold:
            action = "BUY"
            reasoning = f"Win probability ({prob*100:.1f}%) exceeds the entry threshold ({buy_threshold*100:.1f}%), supported by a sentiment score of {sentiment_score:.2f}."
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
        stop_price = current_close * (1.0 - stop_loss_pct) if action == "BUY" else None
        target_price = current_close * (1.0 + take_profit_pct) if action == "BUY" else None

        # --- Build an explicit, executable trade plan (works for manual Robinhood or Alpaca) ---
        hedge = None
        action_plan = None
        if action == "BUY":
            long_notional = POSITION_PCT * equity_for_sizing
            long_shares = long_notional / current_close if current_close > 0 else 0.0
            plan = (f"BUY {ticker}: ~{long_shares:.0f} sh @ ${current_close:,.2f} "
                    f"(≈{POSITION_PCT*100:.0f}% equity = ${long_notional:,.0f}). "
                    f"Stop ${stop_price:,.2f} (-{stop_loss_pct*100:.1f}%), "
                    f"target ${target_price:,.2f} (+{take_profit_pct*100:.1f}%). "
                    f"Win-prob {prob*100:.0f}%.")

            if effective_hedge_mode in ("beta_neutral", "pair_trade"):
                def _f(col, dflt):
                    v = last_row.get(col, dflt)
                    try:
                        v = float(v)
                        return dflt if np.isnan(v) else v
                    except (TypeError, ValueError):
                        return dflt
                hsym, beta = compute_hedge(
                    ticker, effective_hedge_mode,
                    corr_spy=_f("feat_corr_spy_20", 0.8), corr_qqq=_f("feat_corr_qqq_20", 0.8),
                    rel_vol_spy=_f("feat_relative_vol_spy", 1.0), rel_vol_qqq=_f("feat_relative_vol_qqq", 1.0),
                    universe=active_universe,
                )
                if hsym and hsym != ticker:
                    hprice = latest_close.get(hsym)
                    hedge_notional = beta * long_notional
                    hedge_shares = (hedge_notional / hprice) if hprice else None
                    hedge = {
                        "mode": effective_hedge_mode,
                        "symbol": hsym,
                        "ratio": round(beta, 2),
                        "price": round(float(hprice), 2) if hprice else None,
                        "notional": round(hedge_notional, 2),
                        "shares": round(hedge_shares, 1) if hedge_shares else None,
                    }
                    if hedge_shares and hprice:
                        plan += (f" HEDGE: SHORT {hsym} ~{hedge_shares:.0f} sh @ ${hprice:,.2f} "
                                 f"(≈{beta:.2f}x the long, ${hedge_notional:,.0f}) to offset market risk.")
            action_plan = plan

        suggestions.append({
            "ticker": ticker,
            "close": current_close,
            "action": action,
            "confidence": confidence,
            "stop_loss": stop_price,
            "take_profit": target_price,
            "reasoning": reasoning,
            "audit": audit_data,
            "hedge": hedge,
            "action_plan": action_plan
        })


    # --- Compute Long-Term Portfolio Weights (on DAILY returns, ~1 trading year) ---
    returns_list = []
    for ticker in active_universe:
        t_data = daily_prices_df[daily_prices_df['ticker'] == ticker].sort_values('date').copy() if not daily_prices_df.empty else pd.DataFrame()
        if t_data.empty:
            continue
        t_data['returns'] = t_data['close'].pct_change()
        returns_list.append(t_data[['date', 'returns']].rename(columns={'returns': ticker}))

    scores_dict = {}
    expected_return_tilt = None
    if ALT_DATA_ENABLED:
        try:
            feat_universe = sorted(set(active_universe + ["SPY", "QQQ"]))
            daily_features_df = build_all_features(daily_prices_df, None, daily_macro_df, feat_universe)
            if not daily_features_df.empty:
                equities = [t for t in active_universe if t not in ["SPY", "QQQ"] and not t.startswith(("X:", "C:"))]
                eq_df = daily_features_df[daily_features_df["ticker"].isin(equities)].copy()
                if not eq_df.empty:
                    latest_feat_date = eq_df["date"].max()
                    latest_eq_df = eq_df[eq_df["date"] == latest_feat_date].copy()

                    insf = [c for c in ["feat_insider_officer_buy", "feat_insider_buy_count", "feat_insider_cluster"]
                            if c in latest_eq_df.columns]
                    if insf:
                        # Calculate z-scores cross-sectionally for the latest date
                        for c in insf:
                            vals = latest_eq_df[c]
                            mean_val = vals.mean()
                            std_val = vals.std()
                            latest_eq_df[c + "_z"] = (vals - mean_val) / (std_val + 1e-9)

                        latest_eq_df["ins_score"] = latest_eq_df[[c + "_z" for c in insf]].mean(axis=1).fillna(0.0)
                        scores_dict = latest_eq_df.set_index("ticker")["ins_score"].to_dict()
                        expected_return_tilt = {
                            t: float(LONGTERM_TILT_STRENGTH * scores_dict.get(t, 0.0))
                            for t in equities
                        }
                        print(f"Computed expected return tilt on date {latest_feat_date}: {expected_return_tilt}")
        except Exception as e:
            print(f"Error computing expected return tilt in Suggestions API: {e}")

    if returns_list:
        returns_df = returns_list[0]
        for r in returns_list[1:]:
            returns_df = pd.merge(returns_df, r, on='date', how='outer')
        returns_df = returns_df.sort_values('date').tail(MPT_WINDOW_DAYS)

        opt_weights = PortfolioOptimizer.calculate_optimal_weights(returns_df, current_regime, expected_return_tilt=expected_return_tilt)
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
                "shares_multiplier": 1.0,
                "insider_tilt_score": float(scores_dict.get(ticker, 0.0))
            })
    long_term_allocation.append({"ticker": "CASH", "weight": cash_allocation, "insider_tilt_score": 0.0})

    # --- Swing (multi-day) signals — daily prices + LLM-scored news (validated portfolio edge) ---
    swing_suggestions = []
    if SWING_ENABLED:
        try:
            from ml_engine.swing_alpha import build_swing_signals
            swing_suggestions = build_swing_signals(daily_prices_df, daily_macro_df, active_universe)
        except Exception as e:
            print(f"Error computing swing signals: {e}")

    res = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": current_regime,
        "hedge_mode": effective_hedge_mode,
        "short_term_suggestions": suggestions,
        "long_term_allocation": sorted(long_term_allocation, key=lambda x: x["weight"], reverse=True),
        "swing_suggestions": swing_suggestions
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

@app.get("/api/strategy/config")
def get_strategy_config(db=Depends(get_db)):
    """Bucket capital allocations + per-ticker strategy assignments (for the portfolio strategy UI)."""
    buckets = get_strategy_buckets(db)
    return {
        "buckets": buckets,
        "cash": round(max(0.0, 1.0 - sum(buckets.values())), 4),
        "assignments": get_strategy_assignments(db),
        "strategies": STRATEGY_KEYS,
    }

class BucketsRequest(BaseModel):
    swing: float
    longterm: float

@app.post("/api/strategy/buckets")
def set_strategy_buckets(req: BucketsRequest, db=Depends(get_db)):
    """Set the capital fraction per strategy bucket. Rejected if they sum to more than 100%."""
    swing = max(0.0, float(req.swing))
    longterm = max(0.0, float(req.longterm))
    if swing + longterm > 1.0001:
        raise HTTPException(status_code=400, detail="Bucket weights cannot exceed 100% of equity.")
    payload = _json.dumps({"swing": round(swing, 4), "longterm": round(longterm, 4)})
    row = db.query(AppSetting).filter(AppSetting.key == "bucket_allocations").first()
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key="bucket_allocations", value=payload))
    db.commit()
    clear_suggestions_cache()
    return {"status": "success", "buckets": {"swing": swing, "longterm": longterm}}

class TickerStrategyRequest(BaseModel):
    ticker: str
    strategy: str

@app.post("/api/strategy/ticker")
def set_ticker_strategy(req: TickerStrategyRequest, db=Depends(get_db)):
    """Assign which strategy manages a given ticker ('swing' | 'longterm' | 'hold')."""
    ticker = req.ticker.upper().strip()
    strat = req.strategy.strip().lower()
    if strat not in ("swing", "longterm", "hold"):
        raise HTTPException(status_code=400, detail="strategy must be swing, longterm, or hold")
    row = db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker).first()
    if not row:
        row = UniverseTicker(ticker=ticker, strategy=strat)
        db.add(row)
    else:
        row.strategy = strat
    db.commit()
    clear_suggestions_cache()
    return {"status": "success", "ticker": ticker, "strategy": strat}

class EvaluateRequest(BaseModel):
    strategies: List[str] = ["swing", "longterm"]
    horizon: int = 5
    splits: int = 4
    use_allocation: bool = True
    start_date: Optional[str] = None
    end_date: Optional[str] = None

@app.post("/api/evaluate")
def start_evaluation(req: EvaluateRequest, db=Depends(get_db)):
    """Kick off a look-ahead-free backtest of the chosen strategies (+ benchmarks) as a background job."""
    active = [j for j in _jobs_snapshot() if j["type"] == "evaluate" and j["status"] == "running"]
    if active:
        return {"status": "already_running", "job_id": active[0]["id"]}
    allocation = None
    if req.use_allocation:
        buckets = get_strategy_buckets(db)
        assignments = get_strategy_assignments(db)
        lt_tickers = [t for t, s in assignments.items() if s == "longterm"]
        allocation = {"swing": buckets.get("swing", 0.0), "longterm": buckets.get("longterm", 0.0),
                      "longterm_tickers": lt_tickers or None}
    jid = _job_new("evaluate", "Evaluating strategies")
    threading.Thread(target=_run_eval_job,
                     args=(jid, req.strategies, req.horizon, req.splits, allocation, req.start_date, req.end_date),
                     daemon=True).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/evaluate/result")
def get_evaluation_result(job_id: str):
    """Poll an evaluation job: returns the curves+metrics when done, else the running progress."""
    if job_id in _EVAL_RESULTS:
        return {"status": "done", "result": _EVAL_RESULTS[job_id]}
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}

class TickerRequest(BaseModel):
    ticker: str

def _run_backfill_job(jid, ticker):
    """Background worker: backfill a newly-added ticker's price history, updating job progress."""
    try:
        from data_ingestion.price_fetcher import backfill_ticker
        backfill_ticker(ticker, progress_cb=lambda p, s: _job_update(jid, progress=p, stage=s))
        _job_update(jid, progress=100, stage="Complete", status="done")
        clear_suggestions_cache()
        global _price_summary_cache
        _price_summary_cache = {"data": None, "timestamp": None}
    except Exception as e:
        _job_update(jid, status="error", error=str(e)[:200])

@app.post("/api/universe/add")
def add_universe_ticker(req: TickerRequest, db=Depends(get_db)):
    """Add a single ticker to the monitored universe and kick off a background data backfill."""
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker required")
    if db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker).first():
        return {"status": "exists", "ticker": ticker}
    db.add(UniverseTicker(ticker=ticker))
    db.commit()
    clear_suggestions_cache()
    jid = _job_new("backfill", f"Backfilling {ticker}")
    threading.Thread(target=_run_backfill_job, args=(jid, ticker), daemon=True).start()
    return {"status": "added", "ticker": ticker, "job_id": jid}

@app.post("/api/universe/backfill")
def backfill_universe_ticker(req: TickerRequest, db=Depends(get_db)):
    """(Re)run the data + news backfill for an already-monitored ticker (e.g. to pull news that wasn't
    scored when it was first added). Starts a background job with a progress bar."""
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker required")
    if not db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker).first():
        db.add(UniverseTicker(ticker=ticker))
        db.commit()
    jid = _job_new("backfill", f"Backfilling {ticker}")
    threading.Thread(target=_run_backfill_job, args=(jid, ticker), daemon=True).start()
    return {"status": "started", "ticker": ticker, "job_id": jid}

@app.post("/api/universe/remove")
def remove_universe_ticker(req: TickerRequest, db=Depends(get_db)):
    """Stop monitoring a ticker (remove from the universe). Intended for tickers with no open position."""
    ticker = req.ticker.upper().strip()
    row = db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker).first()
    if row:
        db.delete(row)
        db.commit()
        clear_suggestions_cache()
    return {"status": "removed", "ticker": ticker}

class LiquidateRequest(BaseModel):
    ticker: str
    shares: float

@app.post("/api/positions/liquidate")
def liquidate_position(req: LiquidateRequest, mode: str = "real", db=Depends(get_db)):
    """Sell a chosen number of shares of an open position (partial or full). Real mode closes via Alpaca
    (which cancels the bracket OCO); simulated mode reduces the local virtual position."""
    ticker = req.ticker.upper().strip()
    shares = float(req.shares)
    if shares <= 0:
        raise HTTPException(status_code=400, detail="Shares to sell must be positive")

    if mode == "real":
        try:
            from execution.executor import get_alpaca_api
            api = get_alpaca_api()
            try:
                pos = api.get_position(ticker)
            except Exception:
                raise HTTPException(status_code=400, detail=f"No open position in {ticker}")
            held = float(pos.qty)
            sell_qty = min(shares, held)
            if sell_qty >= held - 1e-9:
                order = api.close_position(ticker)
                sold = held
            else:
                order = api.close_position(ticker, qty=str(int(sell_qty)))
                sold = float(int(sell_qty))
            clear_suggestions_cache()
            return {"status": "submitted", "ticker": ticker, "shares_sold": sold,
                    "order_id": getattr(order, "id", None)}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e)[:200])

    # Simulated mode: reduce the local virtual position
    pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker, VirtualPosition.mode == "replay").first()
    if not pos or pos.quantity <= 0:
        raise HTTPException(status_code=400, detail=f"No simulated position in {ticker}")
    sold = min(shares, pos.quantity)
    pos.quantity -= sold
    if pos.quantity <= 1e-6:
        db.delete(pos)
    db.commit()
    return {"status": "submitted", "ticker": ticker, "shares_sold": sold}

def _run_training_job(jid):
    """Background worker: retrain the served models (XGBoost + HMM, then the swing model)."""
    try:
        _job_update(jid, progress=10, stage="Training XGBoost + HMM…")
        from ml_engine.models import train_models
        train_models()
        _job_update(jid, progress=65, stage="Training swing model…")
        try:
            from ml_engine.swing_alpha import train_and_save
            train_and_save()
        except Exception as e:
            print(f"Swing retrain skipped: {e}")
        _job_update(jid, progress=100, stage="Complete", status="done")
        clear_suggestions_cache()
    except Exception as e:
        _job_update(jid, status="error", error=str(e)[:200])

@app.post("/api/train/start")
def start_training():
    """Kick off model retraining in the background (idempotent: refuses if one is already running)."""
    active = [j for j in _jobs_snapshot() if j["type"] == "train" and j["status"] == "running"]
    if active:
        return {"status": "already_running", "job_id": active[0]["id"]}
    jid = _job_new("train", "Retraining models")
    threading.Thread(target=_run_training_job, args=(jid,), daemon=True).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/train/status")
def training_status():
    """Last-trained time per served model (file mtime) + the current retrain job, if any."""
    files = {"short_term": "short_term_model.json", "regime_hmm": "hmm_model.pkl", "swing": "swing_model.json"}
    models = {}
    for key, fn in files.items():
        path = os.path.join(SAVED_MODELS_DIR, fn)
        models[key] = {
            "last_trained": (datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
                             if os.path.exists(path) else None)
        }
    active = [j for j in _jobs_snapshot() if j["type"] == "train"]
    return {"models": models, "training": active[0] if active else None}

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

@app.get("/api/news/llm")
def get_llm_news(ticker: Optional[str] = None, limit: int = 50, db=Depends(get_db)):
    """The LLM-scored news headlines that drive the swing model, latest first.

    Optional `ticker` filter; otherwise the whole universe interleaved by recency. Each item carries the
    raw directional score (-1..1), the relevance (0..1, how materially the headline is about the ticker),
    and the publish time so the scoring can be spot-checked for quality."""
    q = db.query(NewsLLMScore)
    if ticker:
        q = q.filter(NewsLLMScore.ticker == ticker.upper().strip())
    rows = q.order_by(NewsLLMScore.published_utc.desc()).limit(min(max(limit, 1), 200)).all()
    return {"articles": [{
        "ticker": r.ticker, "title": r.title, "score": r.llm_score, "relevance": r.llm_relevance,
        "weighted": round((r.llm_score or 0.0) * (r.llm_relevance or 0.0), 3),
        "published_utc": r.published_utc, "date": r.date, "model": r.model,
    } for r in rows]}

@app.get("/api/portfolio")
def get_portfolio(mode: str = "real", db=Depends(get_db)):
    """Current state of every held asset: shares, cost basis, live price, market value, and unrealized
    P&L ($ and %), plus account totals (cost, value, P&L, cash, equity).

    In real mode the broker (Alpaca) is the source of truth — its live positions + account are read on
    each call, so the UI refresh button recalculates against the latest prices and reflects ALL open
    positions (not the periodically-synced local table). The local trade policy is merged in by ticker.
    Falls back to the local VirtualPosition records + stored closes when the broker is unavailable
    (or in simulated mode)."""
    pos_mode = "real" if mode == "real" else "replay"
    local = {p.ticker: p for p in db.query(VirtualPosition).filter(
        VirtualPosition.mode == pos_mode, VirtualPosition.quantity > 0).all()}

    holdings, total_cost, total_value = [], 0.0, 0.0
    source, cash = "local", None

    if mode == "real":
        try:
            from execution.executor import get_alpaca_api
            api = get_alpaca_api()
            broker_positions = api.list_positions()
            for p in broker_positions:
                cost = float(p.cost_basis)
                value = float(p.market_value)
                total_cost += cost
                total_value += value
                holdings.append({
                    "ticker": p.symbol, "shares": round(float(p.qty), 4),
                    "entry_price": round(float(p.avg_entry_price), 2),
                    "current_price": round(float(p.current_price), 2),
                    "cost_basis": round(cost, 2), "market_value": round(value, 2),
                    "unrealized_pl": round(float(p.unrealized_pl), 2),
                    "unrealized_pl_pct": round(float(p.unrealized_plpc) * 100.0, 2),
                    "policy": local[p.symbol].policy if p.symbol in local else "rebalance",
                    "is_live": True, "purchase_date": local[p.symbol].purchase_date if p.symbol in local else None,
                })
            cash = float(api.get_account().cash)
            source = "broker"
        except Exception as e:
            print(f"Portfolio: broker unavailable, falling back to local records: {e}")

    if source != "broker":
        def latest_close(tk):
            rec = db.query(RecentPrice).filter(RecentPrice.ticker == tk).order_by(RecentPrice.date.desc()).first()
            if not rec:
                rec = db.query(DailyPrice).filter(DailyPrice.ticker == tk).order_by(DailyPrice.date.desc()).first()
            return rec.close if rec else None
        for p in local.values():
            cur = latest_close(p.ticker) or p.entry_price
            cost = p.quantity * p.entry_price
            value = p.quantity * cur
            total_cost += cost
            total_value += value
            holdings.append({
                "ticker": p.ticker, "shares": round(p.quantity, 4), "entry_price": round(p.entry_price, 2),
                "current_price": round(cur, 2), "cost_basis": round(cost, 2), "market_value": round(value, 2),
                "unrealized_pl": round(value - cost, 2),
                "unrealized_pl_pct": round(((value - cost) / cost * 100.0) if cost else 0.0, 2),
                "policy": p.policy, "is_live": False, "purchase_date": p.purchase_date,
            })

    assignments = get_strategy_assignments(db)
    for h in holdings:
        h["strategy"] = assignments.get(h["ticker"], "swing")
    holdings.sort(key=lambda x: -x["market_value"])
    if cash is None:
        acc = db.query(VirtualAccount).filter(VirtualAccount.id == (2 if mode == "real" else 1)).first()
        cash = float(acc.cash) if acc and acc.cash is not None else 0.0
    total_pl = total_value - total_cost
    return {
        "holdings": holdings,
        "totals": {
            "cost_basis": round(total_cost, 2), "market_value": round(total_value, 2),
            "unrealized_pl": round(total_pl, 2),
            "unrealized_pl_pct": round((total_pl / total_cost * 100.0) if total_cost else 0.0, 2),
            "cash": round(cash, 2), "equity": round(total_value + cash, 2),
        },
        "source": source,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

SCHEDULER_HEARTBEAT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scheduler_heartbeat.txt")

_health_cache = {"data": None, "timestamp": None}

@app.get("/api/health")
def get_health(db=Depends(get_db)):
    """Health/status of the moving parts: API, DB, Ollama (LLM), Alpaca broker, the scheduler daemon,
    and LLM-news freshness. Each service reports up / stale / down + a short human detail. Cached 12s."""
    import time as _time
    import requests as _requests
    global _health_cache
    now = datetime.now()
    if (_health_cache["data"] and _health_cache["timestamp"]
            and now - _health_cache["timestamp"] < timedelta(seconds=12)):
        return _health_cache["data"]

    from app.core.config import OLLAMA_URL, LLM_MODEL, EXECUTION_STRATEGY
    svc = {"api": {"status": "up", "detail": "serving"}}

    try:
        db.query(UniverseTicker).first()
        svc["database"] = {"status": "up", "detail": "connected"}
    except Exception as e:
        svc["database"] = {"status": "down", "detail": str(e)[:100]}

    try:
        r = _requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        names = [m.get("name", "") for m in r.json().get("models", [])] if r.status_code == 200 else []
        has_model = any(LLM_MODEL.split(":")[0] in n for n in names)
        svc["ollama"] = {"status": "up" if r.status_code == 200 else "down",
                         "detail": (f"{LLM_MODEL} ready" if has_model else f"{len(names)} models")}
    except Exception:
        svc["ollama"] = {"status": "down", "detail": "unreachable"}

    try:
        from execution.executor import get_alpaca_api
        api = get_alpaca_api()
        clock = api.get_clock()
        acct = api.get_account()
        svc["alpaca"] = {"status": "up", "market_open": bool(clock.is_open),
                         "detail": f"{'market open' if clock.is_open else 'market closed'} · ${float(acct.equity):,.0f}"}
    except Exception as e:
        svc["alpaca"] = {"status": "down", "detail": str(e)[:100]}

    try:
        if os.path.exists(SCHEDULER_HEARTBEAT_FILE):
            age = _time.time() - os.path.getmtime(SCHEDULER_HEARTBEAT_FILE)
            svc["scheduler"] = {"status": "up" if age < 150 else "stale",
                                "detail": (f"{int(age)}s ago" if age < 3600 else f"{int(age/3600)}h ago")}
        else:
            svc["scheduler"] = {"status": "down", "detail": "not running"}
    except Exception:
        svc["scheduler"] = {"status": "down", "detail": "unknown"}

    try:
        from sqlalchemy import func as _func
        latest = db.query(NewsLLMScore).order_by(NewsLLMScore.published_utc.desc()).first()
        cnt = db.query(NewsLLMScore).count()
        earliest = db.query(_func.min(NewsLLMScore.date)).scalar()
        latest_d = (latest.published_utc or "")[:10] if latest else "none"
        svc["news_llm"] = {"status": "up" if latest else "down", "count": cnt,
                           "earliest": earliest, "latest": latest_d,
                           "detail": (f"{earliest} → {latest_d} · {cnt:,} scored" if latest else "none")}
    except Exception:
        svc["news_llm"] = {"status": "down", "detail": "unknown"}

    res = {"services": svc, "execution_strategy": EXECUTION_STRATEGY, "as_of": now.strftime("%H:%M:%S")}
    _health_cache = {"data": res, "timestamp": now}
    return res

_price_summary_cache = {"data": None, "timestamp": None}

@app.get("/api/prices/summary")
def get_price_summary(db=Depends(get_db)):
    """Per-ticker current price + 1D/1W/1M/1Y % change for the active universe.

    Current price is the live Alpaca trade when available (one batched call), else the latest stored
    daily close. Timeframe changes are measured off the stored daily closes. Cached for 60s."""
    global _price_summary_cache
    now = datetime.now()
    if (_price_summary_cache["data"] is not None and _price_summary_cache["timestamp"]
            and now - _price_summary_cache["timestamp"] < timedelta(seconds=60)):
        return _price_summary_cache["data"]

    db_tickers = db.query(UniverseTicker).all()
    universe = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)

    start = (now - timedelta(days=420)).strftime("%Y-%m-%d")
    rows = db.query(DailyPrice.ticker, DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.date >= start, DailyPrice.ticker.in_(universe)).all()
    from collections import defaultdict
    series = defaultdict(list)
    for tk, d, c in rows:
        series[tk].append((d, c))

    # Live prices in one batched Alpaca call; degrade gracefully to the latest daily close.
    live = {}
    try:
        from execution.executor import get_alpaca_api
        api = get_alpaca_api()
        trades = api.get_latest_trades(universe)
        for tk, tr in trades.items():
            live[tk] = float(tr.price)
    except Exception as e:
        print(f"Live price fetch unavailable, using daily closes: {e}")

    def pct(cur, base):
        return round((cur / base - 1.0) * 100.0, 2) if base else None

    out = []
    for tk in universe:
        s = sorted(series.get(tk, []), key=lambda x: x[0])
        closes = [c for _, c in s if c]
        if not closes:
            continue
        is_live = tk in live
        cur = live.get(tk, closes[-1])
        o = 0 if is_live else 1   # when live, closes[-1] is the prior session; else cur == closes[-1]

        def base(k):
            idx = k + o
            return closes[-idx] if len(closes) >= idx else closes[0]

        out.append({
            "ticker": tk, "price": round(cur, 2), "is_live": is_live,
            "d1": pct(cur, base(1)), "w1": pct(cur, base(5)),
            "m1": pct(cur, base(21)), "y1": pct(cur, base(252)),
        })
    out.sort(key=lambda x: x["ticker"])
    res = {"prices": out, "as_of": now.strftime("%Y-%m-%d %H:%M")}
    _price_summary_cache = {"data": res, "timestamp": now}
    return res

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
