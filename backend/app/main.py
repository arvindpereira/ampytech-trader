import os
import sys
import pickle
from datetime import datetime, timedelta
from typing import List, Optional
import pandas as pd
import numpy as np
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
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
    SentimentSourceLog, InsiderDisclosure, NewsLLMScore, AppSetting,
    EquityLot, TaxProfile, AnalystForecast, TradingBlock, EquityVestSchedule
)

import json as _json
# high_risk = small opt-in sleeve driven by the AGGRESSIVE swing model on speculative-tier names.
from app.core.config import HIGH_RISK_CAP
STRATEGY_KEYS = ["swing", "longterm", "high_risk"]
DEFAULT_BUCKETS = {"swing": 1.0, "longterm": 0.0, "high_risk": 0.0}

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
# localhost + private LAN IPs (10.x, 192.168.x, 172.16–31.x) on common dev ports.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Saved models directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "ml_engine", "saved_models")
SCHEDULER_HEARTBEAT_FILE = os.path.join(BASE_DIR, "data", "scheduler_heartbeat.txt")

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

def _start_backfill(ticker):
    """Spawn a backfill job+thread for `ticker`, but only if one isn't already running.
    Returns the (existing or new) job id. Single entry point so we never spawn duplicate
    threads/progress bars for the same ticker (e.g. suggestions auto-heal firing on every poll)."""
    from data_ingestion.price_fetcher import FICTIONAL_TICKERS
    if ticker in FICTIONAL_TICKERS:
        return None  # synthetic ticker (e.g. SPACE) — nothing to fetch, don't create a job
    label = f"Backfilling {ticker}"
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job["type"] == "backfill" and job["label"] == label and job["status"] == "running":
                return job["id"]  # already in flight — reuse it, don't spawn another
        jid = _uuid.uuid4().hex[:8]
        _JOBS[jid] = {"id": jid, "type": "backfill", "label": label, "status": "running",
                      "progress": 0, "stage": "queued", "error": None,
                      "started_at": _time.time(), "finished_at": None}
    threading.Thread(target=_run_backfill_job, args=(jid, ticker), daemon=True).start()
    return jid

@app.on_event("startup")
def startup_event():
    init_db()

@app.get("/api/jobs")
def get_jobs():
    """Active + recently-finished background jobs (ticker backfills, retraining) for UI progress bars."""
    return {"jobs": _jobs_snapshot()}

_EVAL_RESULTS = {}
_SUGGEST_RESULTS = {}
_VALIDATE_RESULTS = {}
_EQUITY_ANALYZE_RESULTS = {}

def _run_suggest_job(jid, oos_start):
    try:
        from ml_engine.strategy_suggester import suggest_strategies
        res = suggest_strategies(oos_start=oos_start,
                                 progress_cb=lambda p, note: _job_update(jid, progress=p, stage=note))
        _SUGGEST_RESULTS[jid] = res
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])

def _run_validate_job(jid, oos_start):
    try:
        from ml_engine.strategy_suggester import validate_assignments
        res = validate_assignments(oos_start=oos_start,
                                   progress_cb=lambda p, note: _job_update(jid, progress=p, stage=note))
        _VALIDATE_RESULTS[jid] = res
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])

def _run_eval_job(jid, strategies, horizon, splits, allocation, start_date, end_date, oos_start,
                  exclude_premium=False):
    try:
        from ml_engine.evaluate import run_evaluation
        res = run_evaluation(strategies, horizon=horizon, splits=splits, allocation=allocation,
                             start_date=start_date, end_date=end_date, oos_start=oos_start,
                             exclude_premium=exclude_premium,
                             progress_cb=lambda p, note: _job_update(jid, progress=p, stage=note))
        # Final step: a powerful model writes a plain-English, honest interpretation of the results.
        from app.core.config import EXPERT_INTERP_ENABLED, OPENAI_API_KEY, OPENAI_EXPERT_MODEL
        res["_params"] = {"strategies": strategies, "horizon": horizon, "splits": splits,
                          "allocation": allocation, "oos_start": oos_start}
        if EXPERT_INTERP_ENABLED and OPENAI_API_KEY:
            _job_update(jid, progress=97, stage=f"Generating expert interpretation ({OPENAI_EXPERT_MODEL})…")
            from ml_engine.expert import interpret_evaluation
            res["interpretation"] = interpret_evaluation(res, res["_params"])
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


_regime_cache = {"regime": None, "ts": None}

def compute_current_regime(db):
    """Current HMM market regime ('growth'|'transition'|'crisis') on daily SPY + macro. Cached 5 min."""
    import time as _t
    now = _t.time()
    if _regime_cache["regime"] and _regime_cache["ts"] and now - _regime_cache["ts"] < 300:
        return _regime_cache["regime"]
    try:
        hmm_path = os.path.join(SAVED_MODELS_DIR, "hmm_model.pkl")
        meta_path = os.path.join(SAVED_MODELS_DIR, "hmm_metadata.pkl")
        if not (os.path.exists(hmm_path) and os.path.exists(meta_path)):
            return "growth"
        with open(hmm_path, "rb") as f:
            hmm = pickle.load(f)
        with open(meta_path, "rb") as f:
            sm = pickle.load(f)["state_mapping"]
        dp, dm = get_daily_data(db)
        spy = dp[dp["ticker"] == "SPY"].sort_values("date").copy() if not dp.empty else pd.DataFrame()
        if spy.empty:
            return "growth"
        feats = build_features_for_df(spy, sentiment_df=None, macro_df=dm)
        cols = ["feat_volatility_10", "feat_fed_funds", "feat_yield_spread"]
        last = feats[cols].tail(1)
        if last.isna().any().any():
            return "growth"
        regime = sm.get(hmm.predict(last.values)[0], "growth")
        _regime_cache.update(regime=regime, ts=now)
        return regime
    except Exception as e:
        print(f"Regime detection failed: {e}")
        return "growth"


_suggestions_cache = {}
_last_on_demand_fetch_time = 0.0

def clear_suggestions_cache():
    global _suggestions_cache
    _suggestions_cache.clear()
    print("Suggestions cache cleared successfully.")

def background_update_prices_and_signals():
    try:
        from data_ingestion.price_fetcher import fetch_recent_prices
        from app.database import SessionLocal
        print("Background Task: Fetching recent prices...")
        fetch_recent_prices()
        clear_suggestions_cache()
        db = SessionLocal()
        try:
            get_daily_suggestions(date=None, db=db)
        finally:
            db.close()
        print("Background Task: Price update and cache pre-population complete.")
    except Exception as e:
        print(f"Background Task: Price update failed: {e}")

@app.get("/api/suggestions")
def get_daily_suggestions(date: Optional[str] = None, mode: str = "real",
                          hedge_mode: Optional[str] = None,
                          background_tasks: BackgroundTasks = None,
                          db=Depends(get_db)):
    """Computes daily trading suggestions (Short-Term and Long-Term) using our trained models."""
    global _suggestions_cache

    # Resolve hedge mode (query param overrides the config default; validate against known modes).
    from execution.hedging import compute_hedge, VALID_MODES
    effective_hedge_mode = hedge_mode if hedge_mode in VALID_MODES else HEDGE_MODE
    if effective_hedge_mode not in VALID_MODES:
        effective_hedge_mode = "none"

    # Reconcile positions/cash with Alpaca broker if in real mode and not viewing historical date
    if mode == "real" and not date:
        from execution.executor import get_alpaca_api, sync_broker_positions
        try:
            api = get_alpaca_api()
            if api:
                sync_broker_positions(db, api)
        except Exception as e:
            print(f"Failed to reconcile Alpaca positions during suggestions fetch: {e}")

    # Load stock universe dynamically from DB to establish part of cache key
    db_tickers = db.query(UniverseTicker).all()
    active_universe = sorted([t.ticker for t in db_tickers]) if db_tickers else sorted(TICKER_UNIVERSE)

    # Auto-heal: Check if any active universe tickers are missing price data
    if not date:
        from data_ingestion.price_fetcher import FICTIONAL_TICKERS
        for ticker in active_universe:
            if ticker in FICTIONAL_TICKERS:
                continue  # synthetic ticker (e.g. SPACE) has no real data to fetch — don't loop backfills
            has_data = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).first() is not None
            if not has_data:
                # _start_backfill is a no-op if one is already in flight, so polling is safe.
                print(f"Suggestions Auto-Heal: Ticker {ticker} is missing price records. Ensuring backfill...")
                _start_backfill(ticker)

    # Establish latest dates/states as part of cache key
    latest_price = db.query(RecentPrice).order_by(RecentPrice.date.desc()).first()
    latest_price_date = latest_price.date if latest_price else "none"

    # Check if we should trigger an on-demand background fetch of prices (only for live updates in real mode)
    if mode == "real" and not date:
        # Determine if scheduler is running
        scheduler_running = False
        if os.path.exists(SCHEDULER_HEARTBEAT_FILE):
            try:
                age = _time.time() - os.path.getmtime(SCHEDULER_HEARTBEAT_FILE)
                scheduler_running = age < 150
            except Exception:
                pass

        if not scheduler_running:
            # Check if latest price in DB is older than 5 minutes (300 seconds)
            global _last_on_demand_fetch_time
            now = _time.time()
            if now - _last_on_demand_fetch_time > 300:
                is_stale = True
                if latest_price:
                    try:
                        latest_dt = datetime.strptime(latest_price.date, "%Y-%m-%d %H:%M:%S")
                        if datetime.now() - latest_dt < timedelta(minutes=5):
                            is_stale = False
                    except Exception:
                        pass

                if is_stale and background_tasks:
                    _last_on_demand_fetch_time = now
                    print("API Suggestions: Latest price is stale (>5m) and scheduler is offline. Triggering background update...")
                    background_tasks.add_task(background_update_prices_and_signals)

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

    # Load virtual account and current positions for rebalancing action calculation
    acc_id = 2 if mode == "real" else 1
    account = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    portfolio_equity = float(account.equity) if account else 100000.0

    positions = {p.ticker: p for p in db.query(VirtualPosition).filter(VirtualPosition.mode == mode).all()}

    def get_ticker_price(tk):
        if prices_df.empty:
            return 0.0
        tk_df = prices_df[prices_df["ticker"] == tk]
        if tk_df.empty:
            return 0.0
        return float(tk_df.sort_values("date", ascending=False).iloc[0]["close"])

    buckets = get_strategy_buckets(db)
    budget_fraction = buckets.get("longterm", 0.0)

    long_term_allocation = []
    # Combine active universe tickers and currently held tickers (with quantity > 0)
    all_rebalance_tickers = sorted(list(set(active_universe) | {t for t, p in positions.items() if p.quantity > 0.01}))

    for ticker in all_rebalance_tickers:
        weight = scaled_weights.get(ticker, 0.0)
        cur_price = get_ticker_price(ticker)
        pos = positions.get(ticker)
        current_shares = pos.quantity if pos else 0.0

        # Skip if trace weight AND no shares held
        if weight <= 0.01 and current_shares <= 0.01:
            continue

        entry_price = pos.entry_price if pos else 0.0
        current_value = current_shares * cur_price

        target_value = portfolio_equity * weight * budget_fraction
        target_shares = target_value / cur_price if cur_price > 0 else 0.0

        suggested_action = "Hold"
        price_dev = 0.0
        if entry_price > 0.0:
            price_dev = (cur_price - entry_price) / entry_price

        # Check policy overrides first
        if pos and pos.policy == "lock":
            suggested_action = "Hold: Position policy is set to LOCK (trades disabled)."
        elif pos and pos.policy == "liquidate":
            suggested_action = f"SELL: Position policy is set to LIQUIDATE. Close all {current_shares:.1f} shares."
        elif weight == 0.0 and current_shares > 0.0:
            # Ticker held but not in the target active universe
            suggested_action = f"SELL: Liquidate position. Ticker is not in active target universe."
        else:
            diff_shares = target_shares - current_shares

            if diff_shares > 0.01:
                # Underweight
                if current_shares == 0.0:
                    suggested_action = f"BUY: Target {target_shares:.1f} shares. Position is new."
                elif price_dev <= -0.03:
                    suggested_action = f"BUY: Scaled grid tranche. Price ({cur_price:.2f}) fell {abs(price_dev)*100:.1f}% below cost basis ({entry_price:.2f})."
                else:
                    trigger_price = entry_price * 0.97
                    suggested_action = f"Hold: Wait for price to drop below {trigger_price:.2f} (currently {price_dev*100:+.1f}% from cost basis)."
            elif diff_shares < -0.01:
                # Overweight
                if price_dev >= 0.05:
                    from execution.executor import get_long_term_available_shares
                    sim_date_str = date or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    lt_shares = get_long_term_available_shares(db, ticker, sim_date_str)
                    if lt_shares > 0.01:
                        suggested_action = f"SELL: Lock profit. Price ({cur_price:.2f}) is up {price_dev*100:.1f}% from cost basis ({entry_price:.2f}). {lt_shares:.1f} long-term shares eligible."
                    else:
                        suggested_action = f"Hold: Overweight, but 0 shares qualify as long-term (>365 days held). Skipping to avoid short-term tax."
                else:
                    suggested_action = f"Hold: Overweight by {abs(diff_shares):.1f} shares. Price is only up {price_dev*100:+.1f}% (wait for +5% profit lock target)."

        long_term_allocation.append({
            "ticker": ticker,
            "weight": weight,
            "shares_multiplier": 1.0,
            "insider_tilt_score": float(scores_dict.get(ticker, 0.0)),
            "current_shares": float(current_shares),
            "entry_price": float(entry_price),
            "current_price": float(cur_price),
            "current_value": float(current_value),
            "target_shares": float(target_shares),
            "target_value": float(target_value),
            "suggested_action": suggested_action
        })

    # Remaining cash
    total_allocated_value = sum(a["current_value"] for a in long_term_allocation)
    cash_alloc_val = portfolio_equity * cash_allocation
    long_term_allocation.append({
        "ticker": "CASH",
        "weight": cash_allocation,
        "insider_tilt_score": 0.0,
        "current_shares": 0.0,
        "entry_price": 1.0,
        "current_price": 1.0,
        "current_value": float(portfolio_equity - total_allocated_value),
        "target_shares": float(cash_alloc_val),
        "target_value": float(cash_alloc_val),
        "suggested_action": "Hold cash buffer"
    })

    # --- Swing (multi-day) signals — daily prices + LLM-scored news (validated portfolio edge) ---
    # CORE book uses the core model on core+quality_growth names; the small HIGH-RISK sleeve uses the
    # aggressive model restricted to speculative-tier names.
    swing_suggestions, high_risk_suggestions = [], []
    if SWING_ENABLED:
        try:
            from ml_engine.swing_alpha import (build_swing_signals, load_swing_model,
                                               tickers_for_tiers, CORE_TIERS, HIGH_RISK_TIERS)
            core_names = set(tickers_for_tiers(CORE_TIERS, include_unrated=True))
            spec_names = set(tickers_for_tiers(HIGH_RISK_TIERS))
            core_uni = [t for t in active_universe if t in core_names] or active_universe
            swing_suggestions = build_swing_signals(daily_prices_df, daily_macro_df, core_uni)
            hr_uni = [t for t in active_universe if t in spec_names]
            if hr_uni:
                agg_m, agg_meta = load_swing_model(aggressive=True)
                if agg_m is not None:
                    high_risk_suggestions = build_swing_signals(daily_prices_df, daily_macro_df, hr_uni,
                                                                model=agg_m, meta=agg_meta, top_n=3)
        except Exception as e:
            print(f"Error computing swing signals: {e}")

    res = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "regime": current_regime,
        "hedge_mode": effective_hedge_mode,
        "short_term_suggestions": suggestions,
        "long_term_allocation": sorted(long_term_allocation, key=lambda x: x["weight"], reverse=True),
        "swing_suggestions": swing_suggestions,
        "high_risk_suggestions": high_risk_suggestions
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
        return {
            "metrics": {
                "total_return": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0
            },
            "equity_curve": []
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
from pydantic import BaseModel, StrictInt
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

def _retrain_status(db):
    """Whether tier overrides postdate the trained models (so a retrain would bake them in)."""
    from app.database import TickerClassification, AppSetting
    n_over = db.query(TickerClassification).filter(TickerClassification.tier_override.isnot(None)).count()
    changed = db.query(AppSetting).filter(AppSetting.key == "tier_overrides_changed_at").first()
    changed_at = changed.value if changed else None
    trained_at = None
    try:
        from ml_engine.swing_alpha import load_swing_model
        _, meta = load_swing_model()
        trained_at = (meta or {}).get("trained_at")
    except Exception:
        pass
    recommended = bool(n_over > 0 and changed_at and (not trained_at or changed_at > trained_at))
    return {"override_count": n_over, "retrain_recommended": recommended,
            "overrides_changed_at": changed_at, "models_trained_at": trained_at}

@app.get("/api/strategy/config")
def get_strategy_config(db=Depends(get_db)):
    """Bucket capital allocations + per-ticker strategy assignments + the live regime overlay."""
    from app.core.config import REGIME_OVERLAY_ENABLED, REGIME_SWING_FACTORS
    buckets = get_strategy_buckets(db)
    regime = compute_current_regime(db) if REGIME_OVERLAY_ENABLED else "growth"
    factor = REGIME_SWING_FACTORS.get(regime, 1.0) if REGIME_OVERLAY_ENABLED else 1.0
    return {
        "buckets": buckets,
        "cash": round(max(0.0, 1.0 - sum(buckets.values())), 4),
        "assignments": get_strategy_assignments(db),
        "strategies": STRATEGY_KEYS,
        "regime": regime,
        "overlay_enabled": REGIME_OVERLAY_ENABLED,
        "swing_factor": factor,
        "effective_swing": round(buckets.get("swing", 0.0) * factor, 4),
        "overlay_active": factor < 1.0,
        "retrain": _retrain_status(db),
        "auto_trading_paused": _auto_trading_paused(db),
    }

class BucketsRequest(BaseModel):
    swing: float
    longterm: float
    high_risk: float = 0.0

@app.post("/api/strategy/buckets")
def set_strategy_buckets(req: BucketsRequest, db=Depends(get_db)):
    """Set the capital fraction per strategy bucket. Rejected if they sum to more than 100%."""
    from app.services.account_strategy import StrategyValidationError, validate_buckets
    try:
        buckets = validate_buckets({"swing": req.swing, "longterm": req.longterm,
                                    "high_risk": req.high_risk})
    except StrategyValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = _json.dumps({key: round(value, 4) for key, value in buckets.items()})
    row = db.query(AppSetting).filter(AppSetting.key == "bucket_allocations").first()
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key="bucket_allocations", value=payload))
    db.commit()
    clear_suggestions_cache()
    return {"status": "success", "buckets": buckets}

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
    oos_start: Optional[str] = None
    exclude_premium: bool = False

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
                      "high_risk": min(buckets.get("high_risk", 0.0), HIGH_RISK_CAP),
                      "longterm_tickers": lt_tickers or None}
    jid = _job_new("evaluate", "Evaluating strategies")
    threading.Thread(target=_run_eval_job,
                     args=(jid, req.strategies, req.horizon, req.splits, allocation, req.start_date,
                           req.end_date, req.oos_start, req.exclude_premium),
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

class TierOverrideRequest(BaseModel):
    ticker: str
    tier: Optional[str] = None   # core | quality_growth | speculative | value_trap; null = clear override

@app.post("/api/classification/override")
def set_tier_override(req: TierOverrideRequest, db=Depends(get_db)):
    """Manually override a ticker's tier (wins over the computed one); null clears it. Re-derives all
    effective tiers (respecting overrides) so routing/serving update immediately."""
    from app.database import TickerClassification
    tk = req.ticker.upper().strip()
    if req.tier not in (None, "core", "quality_growth", "speculative", "value_trap"):
        raise HTTPException(status_code=400, detail="Invalid tier.")
    row = db.query(TickerClassification).filter(TickerClassification.ticker == tk).first()
    if not row:
        row = TickerClassification(ticker=tk)
        db.add(row)
    row.tier_override = req.tier
    if req.tier:
        row.tier = req.tier                       # immediate effect
    # stamp when overrides last changed, so the UI can flag "retrain recommended" vs the trained models
    from app.database import AppSetting
    ts = datetime.now().isoformat(timespec="seconds")
    stamp = db.query(AppSetting).filter(AppSetting.key == "tier_overrides_changed_at").first()
    if stamp:
        stamp.value = ts
    else:
        db.add(AppSetting(key="tier_overrides_changed_at", value=ts))
    db.commit()
    try:
        from ml_engine.classify import classify_universe   # re-derive effective tiers (no LLM, fast)
        classify_universe(run_llm=False)
    except Exception as e:
        print(f"Reclassify after override failed: {e}")
    clear_suggestions_cache()
    return {"status": "ok", "ticker": tk, "tier_override": req.tier}

@app.get("/api/classification")
def get_classification(db=Depends(get_db)):
    """Per-ticker risk × fundamental-quality tier (for the UI badges). Returns {ticker: {tier, quality,
    volatility, dd_2022, distressed, verdict}}."""
    from app.database import TickerClassification
    out = {}
    for c in db.query(TickerClassification).all():
        out[c.ticker] = {"tier": c.tier, "quality": c.quality, "volatility": c.volatility,
                         "dd_2022": c.dd_2022, "distressed": c.distressed, "verdict": c.llm_verdict,
                         "overridden": bool(c.tier_override)}
    return out

@app.get("/api/premium/value")
def premium_value():
    """Forward predictive value of premium-newsletter signals (e.g. The Information): coverage + a
    hit-rate / directional-edge study over closed forward windows. Populates as data accumulates."""
    from ml_engine.premium_eval import premium_value_report
    return premium_value_report()

@app.get("/api/llm/usage")
def llm_usage(since: str = None):
    """Accumulated token usage + estimated cost per model (from the DB usage ledger), across every
    provider the server uses (OpenAI + local Ollama). Cost is recomputed from current pricing."""
    from app.core.llm_cost import usage_summary
    return usage_summary(since=since)

class CalibrateRequest(BaseModel):
    model: str
    actual_cost: float
    since: str = None

@app.post("/api/llm/calibrate")
def llm_calibrate(req: CalibrateRequest):
    """Scale a model's pricing so its estimated cost over the window matches the real `actual_cost` you
    read from the OpenAI dashboard. Persists to llm_pricing.json; future estimates use the new rate."""
    from app.core.llm_cost import calibrate_model
    return calibrate_model(req.model, req.actual_cost, since=req.since)

@app.post("/api/evaluate/interpret")
def regenerate_interpretation(job_id: str):
    """(Re)generate the plain-English expert interpretation for a finished evaluation result."""
    res = _EVAL_RESULTS.get(job_id)
    if not res:
        raise HTTPException(status_code=404, detail="No evaluation result for that job_id.")
    from ml_engine.expert import interpret_evaluation
    res["interpretation"] = interpret_evaluation(res, res.get("_params") or {})
    return {"status": "done", "interpretation": res["interpretation"]}

@app.post("/api/strategy/suggest")
def start_strategy_suggest(oos_start: str = "2022-01-01"):
    """Kick off the per-ticker strategy suggester (swing vs longterm vs hold) as a background job."""
    active = [j for j in _jobs_snapshot() if j["type"] == "suggest" and j["status"] == "running"]
    if active:
        return {"status": "already_running", "job_id": active[0]["id"]}
    jid = _job_new("suggest", "Suggesting strategies")
    threading.Thread(target=_run_suggest_job, args=(jid, oos_start), daemon=True).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/strategy/suggest/result")
def get_strategy_suggest_result(job_id: str):
    """Poll the strategy-suggester job: per-ticker recommendations when done, else running progress."""
    if job_id in _SUGGEST_RESULTS:
        return {"status": "done", "result": _SUGGEST_RESULTS[job_id]}
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}

@app.post("/api/strategy/validate")
def start_strategy_validate(oos_start: str = "2022-01-01"):
    """Backtest current vs suggested per-ticker assignments (blended OOS) to see if the suggestions help."""
    active = [j for j in _jobs_snapshot() if j["type"] == "validate" and j["status"] == "running"]
    if active:
        return {"status": "already_running", "job_id": active[0]["id"]}
    jid = _job_new("validate", "Validating suggestions")
    threading.Thread(target=_run_validate_job, args=(jid, oos_start), daemon=True).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/strategy/validate/result")
def get_strategy_validate_result(job_id: str):
    """Poll the validation job: scheme comparison + verdict when done, else running progress."""
    if job_id in _VALIDATE_RESULTS:
        return {"status": "done", "result": _VALIDATE_RESULTS[job_id]}
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
        res = backfill_ticker(ticker, progress_cb=lambda p, s: _job_update(jid, progress=p, stage=s))
        total = (res or {}).get("news_total", 0) or 0
        latest = (res or {}).get("news_latest")
        summary = (f"Complete — prices +{(res or {}).get('daily', 0)} daily / "
                   f"+{(res or {}).get('hourly', 0)} hourly; "
                   f"news +{(res or {}).get('news', 0)} new ({total:,} scored"
                   + (f", latest {latest}" if latest else "") + ")")
        _job_update(jid, progress=100, stage=summary, status="done")
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
    jid = _start_backfill(ticker)
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
    jid = _start_backfill(ticker)
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
        _job_update(jid, progress=65, stage="Training swing models (core + aggressive)…")
        try:
            from ml_engine.swing_alpha import train_both
            train_both()   # retrains BOTH the core + aggressive models on the current tiers
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
    files = {
        "short_term": "short_term_model.json",
        "regime_hmm": "hmm_model.pkl",
        "swing": "swing_model.json",
        "swing_aggressive": "swing_aggressive_model.json"
    }
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


class EquityLotRequest(BaseModel):
    id: Optional[int] = None
    ticker: str
    account_label: Optional[str] = None
    lot_type: str = "other"
    shares: float
    cost_basis_per_share: float
    acquisition_date: str
    notes: Optional[str] = None


class TaxProfileRequest(BaseModel):
    filing_status: str = "single"
    ordinary_income: float = 0.0
    magi: float = 0.0
    state_ltcg_rate: float = 0.0
    state_stcg_rate: float = 0.0
    carryover_loss: float = 0.0
    tax_year: int = 2026


class EquityAnalyzeRequest(BaseModel):
    objective: str = "raise_cash"
    target_amount: float = 0.0
    target_ticker: Optional[str] = None
    long_term_grace_days: int = 45


class EquityLotSellRequest(BaseModel):
    shares: float                         # how many to sell from this lot
    sale_price: Optional[float] = None    # defaults to latest known price
    sale_date: Optional[str] = None       # YYYY-MM-DD; defaults to today
    add_wash_sale_block: bool = False     # if the sale is a loss, block re-buys 31 days


class TradingBlockRequest(BaseModel):
    ticker: str
    block_type: str = "wash_sale"            # 'wash_sale' | 'permanent'
    reason: Optional[str] = None
    account_label: Optional[str] = None
    sale_date: Optional[str] = None          # YYYY-MM-DD (wash_sale)
    realized_loss: Optional[float] = None
    shares: Optional[float] = None
    window_days: int = 31                     # wash-sale window (30d + 1 buffer)


class AutoTradingRequest(BaseModel):
    paused: bool


class EquityAutoTradeBlockRequest(BaseModel):
    ticker: str
    blocked: bool


class EquityVestScheduleRequest(BaseModel):
    ticker: str
    lot_type: str = "rsu"
    cadence: str = "quarterly"
    vest_day: int = 20
    vest_months: Optional[List[int]] = None
    next_vest_date: Optional[str] = None
    est_shares: Optional[float] = None
    vesting_complete: bool = False
    notes: Optional[str] = None


def _forecast_dict(row):
    if not row:
        return None
    return {
        "ticker": row.ticker, "as_of_date": row.as_of_date, "current_price": row.current_price,
        "target_mean": row.target_mean, "target_high": row.target_high, "target_low": row.target_low,
        "target_median": row.target_median, "num_analysts": row.num_analysts,
        "recommendation_mean": row.recommendation_mean, "recommendation_key": row.recommendation_key,
        "strong_buy": row.strong_buy, "buy": row.buy, "hold": row.hold, "sell": row.sell,
        "strong_sell": row.strong_sell, "upside_pct": row.upside_pct, "source": row.source,
    }


def _tax_profile_dict(row):
    if not row:
        return {
            "filing_status": "single", "ordinary_income": 0.0, "magi": 0.0,
            "state_ltcg_rate": 0.0, "state_stcg_rate": 0.0, "carryover_loss": 0.0,
            "tax_year": datetime.now().year,
        }
    return {
        "filing_status": row.filing_status, "ordinary_income": row.ordinary_income, "magi": row.magi,
        "state_ltcg_rate": row.state_ltcg_rate, "state_stcg_rate": row.state_stcg_rate,
        "carryover_loss": row.carryover_loss, "tax_year": row.tax_year,
    }


def _lot_dict(row):
    return {
        "id": row.id, "ticker": row.ticker, "account_label": row.account_label,
        "lot_type": row.lot_type, "shares": row.shares,
        "cost_basis_per_share": row.cost_basis_per_share,
        "acquisition_date": row.acquisition_date, "notes": row.notes,
        "created_at": row.created_at,
    }


def _equity_narrative(result):
    rec = result.get("recommendation", {})
    picks = rec.get("picks", [])
    obj = {"raise_cash": "raise cash", "harvest_loss": "harvest losses",
           "exit_ticker": "exit the position"}.get(rec.get("objective"), "plan")
    if not picks:
        return ("No lots were selected for this plan — likely nothing matched your target, or you have no "
                "qualifying lots. Try a different objective or target amount.")
    gross = rec.get("gross_proceeds", 0.0)
    tax = rec.get("estimated_tax", 0.0)
    savings = rec.get("estimated_tax_savings", 0.0)
    net = rec.get("net_cash", 0.0)
    n = len(picks)
    msg = (f"To {obj}, the plan sells {n} lot{'s' if n != 1 else ''} for about ${gross:,.0f} in proceeds. ")
    if tax > 0:
        msg += f"You'd owe roughly ${tax:,.0f} in tax, leaving about ${net:,.0f} in cash. "
    else:
        msg += f"There's no tax owed, so you keep about ${net:,.0f} in cash. "
    if savings > 0:
        msg += f"It also harvests about ${savings:,.0f} of tax savings (losses that offset other gains). "
    msg += "Estimates are approximate and run conservative — this is planning help, not tax advice."
    return msg


def _equity_universe_strategy(db, ticker: str) -> str:
    row = db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker.upper()).first()
    return (row.strategy if row and row.strategy else "—")


def _concentration_rollup(classified_lots, db):
    """Aggregate classified lots into a per-ticker concentration + harvestable-loss view, enriched
    with the fundamental tier/quality. Drives the advisor UI table and grounds the LLM narrative."""
    from app.database.models import TickerClassification
    TIER_LABEL = {"quality_growth": "Hot", "core": "Solid", "speculative": "Long-shot", "value_trap": "Cold"}
    by_ticker = {}
    for l in classified_lots:
        t = l["ticker"]
        r = by_ticker.setdefault(t, {"ticker": t, "shares": 0.0, "cost": 0.0, "market_value": 0.0,
                                     "unrealized_gain": 0.0, "lt_shares": 0.0, "st_shares": 0.0,
                                     "harvestable_loss": 0.0, "current_price": l.get("current_price")})
        sh = l["shares"]
        r["shares"] += sh
        r["cost"] += sh * l["cost_basis_per_share"]
        r["market_value"] += l["market_value"]
        r["unrealized_gain"] += l["unrealized_gain"]
        r["lt_shares" if l["is_long_term"] else "st_shares"] += sh
        if l["unrealized_gain"] < 0:
            r["harvestable_loss"] += l["unrealized_gain"]
    total_mv = sum(r["market_value"] for r in by_ticker.values()) or 1.0
    cls = {c.ticker: c for c in db.query(TickerClassification).all()}
    out = []
    for r in by_ticker.values():
        c = cls.get(r["ticker"])
        tier = (getattr(c, "tier_override", None) or getattr(c, "tier", None)) if c else None
        r["avg_cost_basis"] = r["cost"] / r["shares"] if r["shares"] else 0.0
        r["unrealized_pct"] = (r["unrealized_gain"] / r["cost"]) if r["cost"] else None
        r["weight"] = r["market_value"] / total_mv
        r["tier"] = tier
        r["tier_label"] = TIER_LABEL.get(tier, tier)
        r["quality"] = getattr(c, "quality", None) if c else None
        out.append(r)
    for r in out:
        r["recommendation"] = _ticker_recommendation(r, total_mv)
    out.sort(key=lambda r: r["market_value"], reverse=True)
    return out


def _lot_recommendation(lot):
    """Tax-aware, deterministic per-lot suggestion. Advisory only — not tax advice.
    `lot` is a classify_lots() dict (has unrealized_gain, is_long_term, days_to_long_term)."""
    gain = lot.get("unrealized_gain", 0.0) or 0.0
    if gain < 0:
        return {"action": "harvest", "label": "Harvest loss",
                "detail": f"Down {abs(round(gain)):,} — sell to bank a tax loss (highest-cost lots first)."}
    d2lt = lot.get("days_to_long_term") or 0
    if not lot.get("is_long_term") and 0 < d2lt <= 45:
        return {"action": "wait", "label": f"Hold {d2lt}d → LT",
                "detail": f"Gain — wait {d2lt} days for lower long-term tax before selling."}
    if lot.get("is_long_term"):
        return {"action": "sellable", "label": "LT gain — sell-eligible",
                "detail": "Long-term gain; sells at the lower LT rate if you need cash or want to diversify."}
    return {"action": "hold", "label": "Hold (ST gain)",
            "detail": "Short-term gain; selling now is taxed at your ordinary rate."}


def _ticker_recommendation(row, total_mv):
    """Per-ticker steer combining tax (net unrealized), fundamental tier, and concentration."""
    weight = row.get("weight") or (row["market_value"] / total_mv if total_mv else 0)
    tier = row.get("tier")
    concentrated = weight > 0.25
    if row.get("harvestable_loss", 0) < 0 and (row.get("unrealized_gain", 0) or 0) < 0:
        base = {"action": "trim", "label": "Harvest + rotate",
                "detail": "Underwater — harvest the loss and rotate into higher-quality names."}
    elif tier == "speculative":
        base = {"action": "trim", "label": "Trim (speculative)",
                "detail": "Low-quality/high-risk tier — trim into stronger names, mind wash-sale on any loss lots."}
    elif tier == "value_trap":
        base = {"action": "trim", "label": "Reduce (weak)",
                "detail": "Weak-quality tier — reduce over time."}
    elif tier in ("quality_growth", "core"):
        base = {"action": "hold", "label": "Quality — hold",
                "detail": "Solid fundamentals; hold, trim only to manage concentration."}
    else:
        base = {"action": "hold", "label": "Hold",
                "detail": "No strong signal — hold and revisit."}
    if concentrated:
        base = {"action": "trim", "label": base["label"] + " · concentrated ⚠",
                "detail": f"{base['detail']} This is {round(weight * 100)}% of your tracked equity — diversifying lowers single-name risk."}
    return base


def _wash_sale_guard_hint(rec, window_days=31):
    """From a sell plan, summarize which loss-harvest tickers should get a re-buy block + the
    suggested blocked-until date, so the UI can offer one-click protection."""
    by_ticker = {}
    for p in rec.get("picks", []):
        if p.get("gain", 0) < 0:
            agg = by_ticker.setdefault(p["ticker"], {"ticker": p["ticker"], "realized_loss": 0.0, "shares": 0.0})
            agg["realized_loss"] += p["gain"]
            agg["shares"] += p.get("sell_shares", 0.0)
    if not by_ticker:
        return None
    sale_date = datetime.now().date()
    blocked_until = (sale_date + timedelta(days=window_days)).isoformat()
    return {
        "sale_date": sale_date.isoformat(),
        "window_days": window_days,
        "blocked_until": blocked_until,
        "tickers": sorted(by_ticker.values(), key=lambda x: x["realized_loss"]),
    }


def _equity_llm_narrative(result, db):
    """Plain-English advisory narrative grounded in the rollup, tiers, and the sell plan. Uses local
    Ollama by default (private — tax PII never leaves the machine). Falls back to the deterministic
    narrative on any error / model offline."""
    try:
        import requests
        from app.core.config import OLLAMA_URL, LLM_MODEL, NEWS_LLM_PROVIDER
        conc = result.get("concentration", [])
        rec = result.get("recommendation", {})
        guard = result.get("wash_sale_guard")
        # Top current swing BUY alternatives (best-effort; never block the narrative on it).
        alts = []
        try:
            sugs = get_daily_suggestions(date=None, db=db) or {}
            for s in (sugs.get("swing_suggestions") or [])[:8]:
                if s.get("action") == "BUY":
                    alts.append(s.get("ticker"))
        except Exception:
            pass
        ctx = {
            "positions": [{
                "ticker": r["ticker"], "market_value": round(r["market_value"]),
                "weight_pct": round(r["weight"] * 100, 1), "unrealized_pct": round((r["unrealized_pct"] or 0) * 100, 1),
                "harvestable_loss": round(r["harvestable_loss"]), "tier": r.get("tier_label"),
                "quality": r.get("quality"),
                "lt_shares": round(r["lt_shares"]), "st_shares": round(r["st_shares"]),
            } for r in conc],
            "objective": rec.get("objective"),
            "plan_proceeds": round(rec.get("gross_proceeds", 0)),
            "plan_realized_gain": round(rec.get("realized_gain", 0)),
            "plan_tax_savings": round(rec.get("estimated_tax_savings", 0)),
            "wash_sale_block_until": guard.get("blocked_until") if guard else None,
            "buy_alternatives": alts[:6],
        }
        prompt = (
            "You are a concise, practical portfolio + tax-aware advisor. Using ONLY the JSON facts "
            "below, write a short brief (120-180 words) for a self-directed investor. Cover, in plain "
            "English: (1) any over-concentration or deeply-underwater single-stock risk; (2) the "
            "tax-loss-harvesting opportunity (sell highest-cost lots first to realize the most loss); "
            "(3) the wash-sale caution — do not re-buy within ~30 days, and employer RSU vests can "
            "trip it; (4) where freed cash could rotate (the higher-quality buy alternatives). Be "
            "direct, use the real numbers, no preamble, no markdown headers. End with one sentence "
            "noting this is decision-support, not tax advice.\n\nFACTS:\n" + _json.dumps(ctx)
        )
        if NEWS_LLM_PROVIDER == "openai":
            from app.core.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
            if OPENAI_API_KEY:
                r = requests.post(f"{OPENAI_BASE_URL}/chat/completions",
                                  headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                                  json={"model": OPENAI_MODEL, "temperature": 0.2,
                                        "messages": [{"role": "user", "content": prompt}]}, timeout=60)
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"].strip()
                if text:
                    return text
        # default: local Ollama
        r = requests.post(f"{OLLAMA_URL}/api/generate",
                          json={"model": LLM_MODEL, "prompt": prompt, "stream": False,
                                "options": {"temperature": 0.2, "num_predict": 320}}, timeout=120)
        r.raise_for_status()
        text = (r.json().get("response") or "").strip()
        # strip any <think> blocks some models emit
        if "</think>" in text:
            text = text.split("</think>", 1)[1].strip()
        if text:
            return text
    except Exception as e:
        print(f"LLM narrative unavailable, using deterministic fallback: {e}")
    return _equity_narrative(result)


def _run_equity_analyze_job(jid, req_data):
    from app.database import SessionLocal
    from data_ingestion.analyst_fetcher import refresh_forecasts
    from ml_engine.tax_advisor import annual_plan, classify_lots, recommend_sale, wash_sale_flags
    db = SessionLocal()
    try:
        _job_update(jid, progress=10, stage="Loading lots and tax profile")
        lots = db.query(EquityLot).order_by(EquityLot.ticker.asc(), EquityLot.acquisition_date.asc()).all()
        profile = db.query(TaxProfile).filter(TaxProfile.id == 1).first()
        if not profile:
            profile = TaxProfile(id=1)
            db.add(profile)
            db.commit()
        tickers = sorted({l.ticker for l in lots})
        _job_update(jid, progress=30, stage="Refreshing Massive forecast snapshots")
        forecasts = refresh_forecasts(tickers, db=db) if tickers else []
        prices = {f.ticker: f.current_price for f in forecasts if f and f.current_price is not None}
        _job_update(jid, progress=60, stage="Running tax-aware lot heuristic")
        classified = classify_lots(lots, prices=prices)
        rec = recommend_sale(lots, profile, req_data.get("objective"), req_data.get("target_amount", 0.0),
                             req_data.get("long_term_grace_days", 45), target_ticker=req_data.get("target_ticker"),
                             prices=prices)
        washes = wash_sale_flags(lots, rec.get("picks", []))
        yearly = annual_plan(lots, profile, prices=prices)
        result = {
            "lots": classified,
            "concentration": _concentration_rollup(classified, db),
            "forecasts": [_forecast_dict(f) for f in forecasts if f],
            "profile": _tax_profile_dict(profile),
            "recommendation": rec,
            "wash_sale_warnings": washes,
            "wash_sale_guard": _wash_sale_guard_hint(rec),
            "annual_plan": yearly,
            "disclaimer": "Decision-support only, not tax advice. Tax constants and analyst data are approximate/best-effort.",
        }
        _job_update(jid, progress=90, stage="Writing narrative")
        result["narrative"] = _equity_llm_narrative(result, db)
        _EQUITY_ANALYZE_RESULTS[jid] = result
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])
    finally:
        db.close()


@app.get("/api/equity/lots")
def get_equity_lots(db=Depends(get_db)):
    lots = db.query(EquityLot).order_by(EquityLot.ticker.asc(), EquityLot.acquisition_date.asc()).all()
    from data_ingestion.analyst_fetcher import latest_or_refresh
    from data_ingestion.price_fetcher import fetch_equity_advisor_prices
    from data_ingestion.equity_universe_sync import (
        get_equity_auto_trade_blocks, sync_equity_advisor_universe,
    )
    from ml_engine.tax_advisor import classify_lots
    tickers = sorted({l.ticker for l in lots})
    if tickers:
        sync_equity_advisor_universe(db)
        fetch_equity_advisor_prices(db, tickers=tickers)
    blocked = set(get_equity_auto_trade_blocks(db))
    prices = {}
    forecasts = {}
    from data_ingestion.analyst_fetcher import ensure_equity_price
    for ticker in tickers:
        f = latest_or_refresh(ticker, db, stale_days=1)
        price = (f.current_price if f and f.current_price is not None else None) or ensure_equity_price(db, ticker)
        fd = _forecast_dict(f)
        if fd and price is not None:
            if fd.get("current_price") is None:
                fd["current_price"] = price
            if fd.get("upside_pct") is None and fd.get("target_mean") is not None and price:
                fd["upside_pct"] = (fd["target_mean"] - price) / price
            prices[ticker] = price
        forecasts[ticker] = fd
    classified = classify_lots(lots, prices=prices)
    for l in classified:
        l["recommendation"] = _lot_recommendation(l)
    aggregate = _concentration_rollup(classified, db)
    for row in aggregate:
        row["auto_trade_blocked"] = row["ticker"] in blocked
        row["universe_strategy"] = _equity_universe_strategy(db, row["ticker"])
    from ml_engine.vest_schedule import ensure_vest_schedules
    vest_schedules = ensure_vest_schedules(db)
    return {"lots": classified, "forecasts": forecasts, "aggregate": aggregate,
            "auto_trade_blocked": sorted(blocked), "vest_schedules": vest_schedules}


@app.post("/api/equity/lots")
def upsert_equity_lot(req: EquityLotRequest, db=Depends(get_db)):
    ticker = req.ticker.upper().strip()
    if req.lot_type not in ("rsu", "espp", "other"):
        raise HTTPException(status_code=400, detail="lot_type must be rsu, espp, or other")
    if req.shares <= 0 or req.cost_basis_per_share < 0:
        raise HTTPException(status_code=400, detail="shares must be positive and cost basis non-negative")
    try:
        datetime.strptime(req.acquisition_date[:10], "%Y-%m-%d")
    except Exception:
        raise HTTPException(status_code=400, detail="acquisition_date must be YYYY-MM-DD")
    row = db.query(EquityLot).filter(EquityLot.id == req.id).first() if req.id else None
    if not row:
        row = EquityLot(created_at=datetime.now().isoformat(timespec="seconds"))
        db.add(row)
    row.ticker = ticker
    row.account_label = req.account_label
    row.lot_type = req.lot_type
    row.shares = req.shares
    row.cost_basis_per_share = req.cost_basis_per_share
    row.acquisition_date = req.acquisition_date[:10]
    row.notes = req.notes
    db.commit()
    db.refresh(row)
    return {"status": "success", "lot": _lot_dict(row)}


@app.delete("/api/equity/lots/{lot_id}")
def delete_equity_lot(lot_id: int, db=Depends(get_db)):
    row = db.query(EquityLot).filter(EquityLot.id == lot_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    db.delete(row)
    db.commit()
    return {"status": "success"}


@app.post("/api/equity/lots/import")
async def import_equity_lots_pdf(
    file: UploadFile = File(...),
    force_llm: bool = Form(False),
    replace_ticker_account: bool = Form(False),
    db=Depends(get_db),
):
    """Import tax lots from a brokerage/stock-plan PDF (Schwab, E*TRADE, or LLM fallback)."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a .pdf export")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="PDF too large (max 15 MB)")
    try:
        from data_ingestion.equity_lot_importer import import_equity_lot_pdf
        result = import_equity_lot_pdf(
            db, data, filename=file.filename,
            force_llm=force_llm, replace_ticker_account=replace_ticker_account,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    if result.get("inserted", 0) > 0 and result.get("tickers"):
        try:
            from data_ingestion.price_fetcher import fetch_equity_advisor_prices
            from data_ingestion.equity_universe_sync import sync_equity_advisor_universe
            sync_equity_advisor_universe(db)
            fetch_equity_advisor_prices(db, tickers=result["tickers"])
        except Exception:
            pass
    if result.get("inserted", 0) == 0 and result.get("parsed_count", 0) == 0:
        raise HTTPException(
            status_code=422,
            detail={"message": "No lots extracted from PDF", "warnings": result.get("warnings", [])},
        )
    return {"status": "success", **result}


# --- External Portfolio Manager (Tab 6) Endpoints ---
from app.database import ExternalAccount, ExternalOrder, ExternalTransaction
from pydantic import BaseModel

class ExternalAccountRequest(BaseModel):
    account_label: str
    cash: float
    risk_profile: str

class UpdateCashRequest(BaseModel):
    cash: float

class ExternalBucketsRequest(BaseModel):
    swing: float
    longterm: float
    high_risk: float = 0.0

class ExternalStrategyRequest(BaseModel):
    strategy_mode: str
    aggression: StrictInt
    buckets: Optional[ExternalBucketsRequest] = None

class ConfirmOrderRequest(BaseModel):
    ticker: str
    side: str  # 'BUY' | 'SELL'
    qty: float
    filled_price: float
    execution_date: str  # YYYY-MM-DD
    time_in_force: str  # 'DAY' | 'GTC_90'

def _latest_external_price(db, symbol, fallback_price):
    from app.database import RecentPrice, DailyPrice
    r = db.query(RecentPrice).filter(RecentPrice.ticker == symbol).order_by(RecentPrice.date.desc()).first()
    if r and r.close:
        return float(r.close)
    d = db.query(DailyPrice).filter(DailyPrice.ticker == symbol).order_by(DailyPrice.date.desc()).first()
    if d and d.close:
        return float(d.close)
    return float(fallback_price)

def _external_price_with_source(db, symbol, fallback_price):
    from app.database import RecentPrice, DailyPrice
    r = db.query(RecentPrice).filter(RecentPrice.ticker == symbol).order_by(RecentPrice.date.desc()).first()
    if r and r.close:
        return float(r.close), False
    d = db.query(DailyPrice).filter(DailyPrice.ticker == symbol).order_by(DailyPrice.date.desc()).first()
    if d and d.close:
        return float(d.close), False
    return float(fallback_price), True

def _latest_cached_model_signals():
    """Read the newest shared suggestion snapshot without triggering model/broker side effects."""
    for key, value in reversed(_suggestions_cache.items()):
        if len(key) >= 2 and key[0] == "live" and key[1] == "real":
            return value
    return None

@app.get("/api/external/accounts")
def get_external_accounts(db=Depends(get_db)):
    from app.services.account_strategy import effective_buckets
    accounts = db.query(ExternalAccount).all()
    # If empty, seed default Robinhood and Vanguard accounts
    if not accounts:
        now_str = datetime.now().isoformat(timespec="seconds")
        rh = ExternalAccount(account_label="Robinhood", cash=0.0, risk_profile="balanced", created_at=now_str, updated_at=now_str)
        vg = ExternalAccount(account_label="Vanguard", cash=0.0, risk_profile="balanced", created_at=now_str, updated_at=now_str)
        db.add(rh)
        db.add(vg)
        db.commit()
        accounts = [rh, vg]

    results = []
    for acct in accounts:
        # Calculate total equity
        lots = db.query(EquityLot).filter(EquityLot.account_label == acct.account_label).all()
        holdings_value = 0.0
        for lot in lots:
            price = _latest_external_price(db, lot.ticker, lot.cost_basis_per_share)
            holdings_value += lot.shares * price

        results.append({
            "account_label": acct.account_label,
            "cash": acct.cash,
            "holdings_value": round(holdings_value, 2),
            "total_value": round(acct.cash + holdings_value, 2),
            "risk_profile": acct.risk_profile,
            "strategy_mode": acct.strategy_mode,
            "aggression": acct.aggression,
            "buckets": effective_buckets(acct, get_strategy_buckets(db)),
            "inherits_global_buckets": acct.buckets_json is None,
            "created_at": acct.created_at,
            "updated_at": acct.updated_at
        })
    return results

@app.post("/api/external/accounts/{account_label}/strategy")
def update_external_account_strategy(account_label: str, req: ExternalStrategyRequest,
                                     db=Depends(get_db)):
    from app.services.account_strategy import (
        STRATEGY_MODES, StrategyValidationError, effective_buckets, validate_buckets,
    )
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    if req.strategy_mode not in STRATEGY_MODES:
        raise HTTPException(status_code=400, detail=f"strategy_mode must be one of {', '.join(STRATEGY_MODES)}")
    if isinstance(req.aggression, bool) or not 0 <= req.aggression <= 100:
        raise HTTPException(status_code=400, detail="aggression must be from 0 through 100")
    raw_buckets = req.buckets.model_dump() if req.buckets is not None and hasattr(req.buckets, "model_dump") else (
        req.buckets.dict() if req.buckets is not None else None
    )
    try:
        validated = validate_buckets(raw_buckets)
    except StrategyValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    acct.strategy_mode = req.strategy_mode
    acct.aggression = req.aggression
    acct.buckets_json = _json.dumps(validated, sort_keys=True) if validated is not None else None
    acct.updated_at = datetime.now().isoformat(timespec="seconds")
    db.commit()
    return {"status": "success", "account_label": account_label,
            "strategy_mode": acct.strategy_mode, "aggression": acct.aggression,
            "buckets": effective_buckets(acct, get_strategy_buckets(db)),
            "inherits_global_buckets": acct.buckets_json is None}

@app.post("/api/external/accounts")
def create_or_update_external_account(req: ExternalAccountRequest, db=Depends(get_db)):
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == req.account_label).first()
    now_str = datetime.now().isoformat(timespec="seconds")
    if not acct:
        acct = ExternalAccount(
            account_label=req.account_label,
            cash=req.cash,
            risk_profile=req.risk_profile,
            created_at=now_str,
            updated_at=now_str
        )
        db.add(acct)
    else:
        acct.cash = req.cash
        acct.risk_profile = req.risk_profile
        acct.updated_at = now_str
    db.commit()
    return {"status": "success", "account_label": acct.account_label}

@app.post("/api/external/accounts/{account_label}/cash")
def update_external_account_cash(account_label: str, req: UpdateCashRequest, db=Depends(get_db)):
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")
    acct.cash = req.cash
    acct.updated_at = datetime.now().isoformat(timespec="seconds")
    db.commit()
    return {"status": "success", "account_label": acct.account_label, "cash": acct.cash}

@app.get("/api/external/positions")
def get_external_positions(account_label: str, db=Depends(get_db)):
    lots = db.query(EquityLot).filter(EquityLot.account_label == account_label).order_by(EquityLot.ticker.asc(), EquityLot.acquisition_date.asc()).all()

    # Group by ticker
    grouped = {}
    for lot in lots:
        ticker = lot.ticker.upper()
        if ticker not in grouped:
            grouped[ticker] = []
        grouped[ticker].append(lot)

    results = []
    for ticker, ticker_lots in grouped.items():
        total_shares = sum(l.shares for l in ticker_lots)
        if total_shares <= 0:
            continue
        total_cost = sum(l.shares * l.cost_basis_per_share for l in ticker_lots)
        avg_cost = total_cost / total_shares

        price = _latest_external_price(db, ticker, avg_cost)
        mkt_val = total_shares * price
        gain = mkt_val - total_cost
        gain_pct = (gain / total_cost * 100.0) if total_cost > 0 else 0.0

        lots_list = [{
            "id": l.id,
            "acquisition_date": l.acquisition_date,
            "shares": l.shares,
            "cost_basis_per_share": l.cost_basis_per_share,
            "notes": l.notes
        } for l in ticker_lots]

        results.append({
            "ticker": ticker,
            "total_shares": round(total_shares, 6),
            "average_cost": round(avg_cost, 4),
            "current_price": round(price, 2),
            "market_value": round(mkt_val, 2),
            "unrealized_gain": round(gain, 2),
            "unrealized_gain_pct": round(gain_pct, 2),
            "lots": lots_list
        })
    return results

@app.post("/api/external/import")
async def import_external_portfolio_pdf(
    file: UploadFile = File(...),
    force_llm: bool = Form(False),
    override_account: Optional[str] = Form(None),
    db=Depends(get_db)
):
    name = (file.filename or "").lower()
    if not (name.endswith(".pdf") or name.endswith(".csv")):
        raise HTTPException(status_code=400, detail="Upload a .pdf statement or a .csv transaction export")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 15 MB)")
    # Robinhood transaction CSV → reconstruct holdings (with the 2026-05-31 statement as basis/snapshot anchor).
    if name.endswith(".csv"):
        try:
            _stash_import_source(file.filename, data)   # keep a backed-up copy of the source export
            from data_ingestion.import_external_csv import import_robinhood_csv
            result = import_robinhood_csv(data, override_account=override_account)
            if result.get("status") != "success":
                raise HTTPException(status_code=422, detail=result.get("detail", "Could not parse CSV"))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not parse CSV: {e}")
    else:
        try:
            from data_ingestion.external_importer import import_external_pdf
            result = import_external_pdf(
                db, data, filename=file.filename,
                force_llm=force_llm, override_account=override_account
            )
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")

    # Whichever path ran: held names are often outside the trade universe (no price rows), so they'd
    # otherwise be valued at COST basis. Pull current daily prices for this account's holdings so the
    # portfolio value is accurate. Works for both the CSV reconstruction and monthly PDF statements.
    _refresh_external_prices(db, result.get("account_label"), result.get("tickers"))
    return result


def _refresh_external_prices(db, account_label, tickers=None):
    """Fetch current daily prices for an external account's holdings (or an explicit ticker list)."""
    try:
        from data_ingestion.price_fetcher import fetch_equity_advisor_prices
        if not tickers and account_label:
            tickers = [t[0] for t in db.query(EquityLot.ticker)
                       .filter(EquityLot.account_label == account_label).distinct().all() if t[0]]
        if tickers:
            fetch_equity_advisor_prices(db, tickers=sorted({t.upper().strip() for t in tickers}))
    except Exception as e:
        print(f"External price refresh failed (non-fatal): {e}")


def _stash_import_source(filename, data):
    """Keep a copy of an uploaded broker export in data/import_sources/ so it's captured by
    `make backup` (and re-importable). Best-effort — never blocks the import."""
    try:
        from app.core.config import DATA_STORAGE_DIR
        safe = os.path.basename(filename or "import.csv")
        dest_dir = os.path.join(DATA_STORAGE_DIR, "import_sources")
        os.makedirs(dest_dir, exist_ok=True)
        with open(os.path.join(dest_dir, safe), "wb") as f:
            f.write(data)
    except Exception as e:
        print(f"Could not stash import source (non-fatal): {e}")

@app.get("/api/external/suggestions")
def get_external_suggestions(account_label: str, db=Depends(get_db)):
    from app.services.account_strategy import (
        StrategyValidationError, build_account_target, effective_buckets,
        generate_trade_proposals,
    )
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    cash = float(acct.cash)
    lots = db.query(EquityLot).filter(EquityLot.account_label == account_label).all()

    current_values, quantities, prices, fallback_tickers = {}, {}, {}, set()
    portfolio_value = cash
    for lot in lots:
        price, fallback = _external_price_with_source(db, lot.ticker, lot.cost_basis_per_share)
        prices[lot.ticker] = price
        if fallback:
            fallback_tickers.add(lot.ticker)
        quantities[lot.ticker] = quantities.get(lot.ticker, 0.0) + lot.shares
        current_values[lot.ticker] = current_values.get(lot.ticker, 0.0) + (lot.shares * price)
        portfolio_value += (lot.shares * price)

    buckets = effective_buckets(acct, get_strategy_buckets(db))
    base = {"account_label": account_label, "portfolio_value": round(portfolio_value, 2),
            "strategy_mode": acct.strategy_mode, "aggression": acct.aggression,
            "effective_buckets": buckets, "target_weights": {}, "cash_target_weight": 1.0,
            "target_reason_codes": {}, "crash_risk_coefficient": None,
            "suggestions": [], "turnover_pct": 0.0, "warnings": []}
    if portfolio_value <= 0:
        base["warnings"] = ["Portfolio value is zero or negative"]
        return base

    current_weights = {ticker: value / portfolio_value for ticker, value in current_values.items()}
    snapshot = _latest_cached_model_signals()
    warnings = []
    if snapshot is None:
        warnings.append("No cached model signals are available; unsignalled holdings are preserved")
    safe_mix, glide_coefficient = None, None
    if acct.strategy_mode == "glide_path":
        from ml_engine.defensive_strategist import build_defensive_playbook
        playbook = build_defensive_playbook(preset_name="balanced")
        safe_mix = playbook.get("stances", {}).get("safe_asset_selection", {}).get("mix")
        glide_coefficient = playbook.get("de_risk_coefficient")
        if not safe_mix or glide_coefficient is None:
            base["target_weights"] = current_weights
            base["cash_target_weight"] = cash / portfolio_value
            base["warnings"] = warnings + [playbook.get("error", "No defensive snapshot is available")]
            return base
    try:
        target = build_account_target(current_weights, acct.strategy_mode, acct.aggression,
                                      buckets, snapshot=snapshot, safe_mix=safe_mix,
                                      glide_coefficient=glide_coefficient)
    except StrategyValidationError as exc:
        base["target_weights"] = current_weights
        base["cash_target_weight"] = cash / portfolio_value
        base["warnings"] = warnings + [str(exc)]
        return base

    for ticker in target["target_weights"]:
        if ticker not in prices:
            price, fallback = _external_price_with_source(db, ticker, 100.0)
            prices[ticker] = price
            if fallback:
                fallback_tickers.add(ticker)
    suggestions, turnover, order_warnings = generate_trade_proposals(
        target["target_weights"], target["cash_target_weight"], portfolio_value, cash,
        quantities, prices, fallback_tickers,
    )
    base.update(target_weights={k: round(v, 8) for k, v in target["target_weights"].items()},
                cash_target_weight=round(target["cash_target_weight"], 8),
                target_reason_codes=target["target_reason_codes"],
                crash_risk_coefficient=glide_coefficient,
                suggestions=suggestions, turnover_pct=round(turnover, 6),
                warnings=warnings + order_warnings)
    return base

@app.post("/api/external/orders/confirm")
def confirm_external_order(req: ConfirmOrderRequest, account_label: str, db=Depends(get_db)):
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    qty = req.qty
    price = req.filled_price
    trade_val = qty * price
    now_str = datetime.now().isoformat(timespec="seconds")

    if req.side.upper() == "BUY":
        if acct.cash < trade_val - 1.0:
            acct.cash = 0.0
        else:
            acct.cash = round(acct.cash - trade_val, 2)

        lot = EquityLot(
            ticker=req.ticker.upper(),
            account_label=account_label,
            lot_type="other",
            shares=qty,
            cost_basis_per_share=price,
            acquisition_date=req.execution_date,
            notes=f"Confirmed manual BUY order",
            created_at=now_str
        )
        db.add(lot)
    else:
        lots = db.query(EquityLot).filter(
            EquityLot.account_label == account_label,
            EquityLot.ticker == req.ticker.upper()
        ).order_by(EquityLot.acquisition_date.asc()).all()

        total_held = sum(l.shares for l in lots)
        if total_held < qty - 1e-6:
            raise HTTPException(status_code=400, detail=f"Insufficient shares held to sell {qty}. Held: {total_held}")

        acct.cash = round(acct.cash + trade_val, 2)

        remaining_to_sell = qty
        for lot in lots:
            if remaining_to_sell <= 0:
                break
            if lot.shares <= remaining_to_sell + 1e-6:
                remaining_to_sell -= lot.shares
                db.delete(lot)
            else:
                lot.shares = round(lot.shares - remaining_to_sell, 6)
                remaining_to_sell = 0.0

    tx = ExternalTransaction(
        account_label=account_label,
        ticker=req.ticker.upper(),
        side=req.side.upper(),
        qty=qty,
        price=price,
        execution_date=req.execution_date,
        raw_details=f"Confirmed manual trade. TIF: {req.time_in_force}",
        created_at=now_str
    )
    db.add(tx)

    # Also log in external_orders table as confirmed
    db_order = ExternalOrder(
        account_label=account_label,
        ticker=req.ticker.upper(),
        side=req.side.upper(),
        qty=qty,
        limit_price=price,
        time_in_force=req.time_in_force,
        status="confirmed_filled",
        filled_price=price,
        filled_qty=qty,
        execution_date=req.execution_date,
        created_at=now_str,
        updated_at=now_str
    )
    db.add(db_order)

    db.commit()
    return {"status": "success", "account_label": account_label, "cash": acct.cash}

@app.post("/api/external/reconcile")
def reconcile_external_portfolio(account_label: str, db=Depends(get_db)):
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if not acct:
        raise HTTPException(status_code=404, detail="Account not found")

    txs = db.query(ExternalTransaction).filter(ExternalTransaction.account_label == account_label).all()
    orders = db.query(ExternalOrder).filter(ExternalOrder.account_label == account_label).all()

    matched_tx_ids = set()
    reconciled_orders_count = 0
    new_trades_imported = 0

    for order in orders:
        if order.status in ("reconciled", "cancelled"):
            continue

        match = None
        for tx in txs:
            if tx.id in matched_tx_ids:
                continue
            if tx.ticker.upper() != order.ticker.upper():
                continue
            if tx.side.upper() != order.side.upper():
                continue
            if abs(tx.qty - order.qty) > 0.1:
                continue

            price_diff = abs(tx.price - order.limit_price) / order.limit_price if order.limit_price > 0 else 0.0
            if price_diff > 0.05:
                continue

            try:
                o_dt = datetime.strptime(order.created_at[:10], "%Y-%m-%d")
                tx_dt = datetime.strptime(tx.execution_date, "%Y-%m-%d")
                if abs((tx_dt - o_dt).days) > 10:
                    continue
            except Exception:
                pass

            match = tx
            break

        if match:
            matched_tx_ids.add(match.id)
            if order.status == "proposed":
                qty = match.qty
                price = match.price
                trade_val = qty * price
                now_str = datetime.now().isoformat(timespec="seconds")

                if order.side.upper() == "BUY":
                    acct.cash = round(acct.cash - trade_val, 2)
                    lot = EquityLot(
                        ticker=order.ticker.upper(),
                        account_label=account_label,
                        lot_type="other",
                        shares=qty,
                        cost_basis_per_share=price,
                        acquisition_date=match.execution_date,
                        notes=f"Reconciled from Statement ({match.execution_date})",
                        created_at=now_str
                    )
                    db.add(lot)
                else:
                    lots = db.query(EquityLot).filter(
                        EquityLot.account_label == account_label,
                        EquityLot.ticker == order.ticker.upper()
                    ).order_by(EquityLot.acquisition_date.asc()).all()

                    acct.cash = round(acct.cash + trade_val, 2)
                    remaining = qty
                    for lot in lots:
                        if remaining <= 0:
                            break
                        if lot.shares <= remaining + 1e-6:
                            remaining -= lot.shares
                            db.delete(lot)
                        else:
                            lot.shares = round(lot.shares - remaining, 6)
                            remaining = 0.0

            order.status = "reconciled"
            order.filled_price = match.price
            order.filled_qty = match.qty
            order.execution_date = match.execution_date
            order.updated_at = datetime.now().isoformat(timespec="seconds")
            reconciled_orders_count += 1

    for tx in txs:
        if tx.id in matched_tx_ids:
            continue

        qty = tx.qty
        price = tx.price
        trade_val = qty * price
        now_str = datetime.now().isoformat(timespec="seconds")

        if tx.side.upper() == "BUY":
            acct.cash = round(acct.cash - trade_val, 2)
            lot = EquityLot(
                ticker=tx.ticker.upper(),
                account_label=account_label,
                lot_type="other",
                shares=qty,
                cost_basis_per_share=price,
                acquisition_date=tx.execution_date,
                notes=f"Unmatched trade imported from statement",
                created_at=now_str
            )
            db.add(lot)
        else:
            lots = db.query(EquityLot).filter(
                EquityLot.account_label == account_label,
                EquityLot.ticker == tx.ticker.upper()
            ).order_by(EquityLot.acquisition_date.asc()).all()

            total_held = sum(l.shares for l in lots)
            acct.cash = round(acct.cash + trade_val, 2)

            sell_qty = min(qty, total_held)
            remaining = sell_qty
            for lot in lots:
                if remaining <= 0:
                    break
                if lot.shares <= remaining + 1e-6:
                    remaining -= lot.shares
                    db.delete(lot)
                else:
                    lot.shares = round(lot.shares - remaining, 6)
                    remaining = 0.0

        new_ord = ExternalOrder(
            account_label=account_label,
            ticker=tx.ticker.upper(),
            side=tx.side.upper(),
            qty=qty,
            limit_price=price,
            time_in_force="DAY",
            status="reconciled",
            filled_price=price,
            filled_qty=qty,
            execution_date=tx.execution_date,
            created_at=now_str,
            updated_at=now_str
        )
        db.add(new_ord)
        new_trades_imported += 1

    db.commit()
    return {
        "status": "success",
        "reconciled_orders": reconciled_orders_count,
        "new_trades_imported": new_trades_imported
    }


class RobinhoodSyncRequest(BaseModel):
    username: str
    password: str
    mfa_secret: Optional[str] = None
    mfa_code: Optional[str] = None
    account_label: str = "Robinhood"

@app.post("/api/external/sync/robinhood")
def sync_robinhood_api(req: RobinhoodSyncRequest, db=Depends(get_db)):
    import builtins
    import pyotp
    import robin_stocks.robinhood as r
    from datetime import datetime

    # Create account if it doesn't exist
    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == req.account_label).first()
    now_str = datetime.now().isoformat(timespec="seconds")
    if not acct:
        acct = ExternalAccount(
            account_label=req.account_label,
            cash=0.0,
            risk_profile="balanced",
            created_at=now_str,
            updated_at=now_str
        )
        db.add(acct)
        db.commit()

    # Generate TOTP code if secret is provided
    mfa = req.mfa_code
    if req.mfa_secret and req.mfa_secret.strip():
        try:
            totp = pyotp.TOTP(req.mfa_secret.strip().replace(" ", ""))
            mfa = totp.now()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to generate MFA code from secret: {e}")

    # Temporarily override builtins.input to prevent uvicorn terminal hangs
    original_input = builtins.input
    def mock_input(prompt=""):
        raise ValueError("MFA code required but stdin is blocked")
    builtins.input = mock_input

    try:
        # Perform login
        login_res = r.login(
            username=req.username.strip(),
            password=req.password,
            mfa_code=mfa,
            store_session=False
        )
    except Exception as e:
        err_msg = str(e)
        if "challenge" in err_msg.lower() or "mfa" in err_msg.lower() or "passcode" in err_msg.lower() or "input" in err_msg.lower():
            return {
                "status": "mfa_required",
                "message": "Two-factor authentication code is required. Please provide mfa_code or mfa_secret."
            }
        raise HTTPException(status_code=400, detail=f"Robinhood login failed: {err_msg}")
    finally:
        # Restore standard input
        builtins.input = original_input

    try:
        # 1. Fetch cash & sweep balances
        phoenix = r.account.load_phoenix_account()
        cash = 0.0
        if phoenix:
            crypto_cash = float(phoenix.get("crypto_buying_power", {}).get("amount", 0.0))
            cash_avail = float(phoenix.get("cash_available_from_sweep", {}).get("amount", 0.0))
            cash_held = float(phoenix.get("cash", {}).get("amount", 0.0))
            cash = max(cash_avail, cash_held, crypto_cash)
            if cash <= 0:
                portfolio = r.profiles.load_portfolio_profile()
                cash = float(portfolio.get("cash", 0.0)) if portfolio else 0.0
        else:
            portfolio = r.profiles.load_portfolio_profile()
            cash = float(portfolio.get("cash", 0.0)) if portfolio else 0.0

        acct.cash = round(cash, 2)
        acct.updated_at = now_str

        # 2. Fetch current holdings
        holdings = r.build_holdings()
        db.query(EquityLot).filter(EquityLot.account_label == req.account_label).delete()

        inserted_lots = 0
        for ticker, h_info in holdings.items():
            qty = float(h_info.get("quantity", 0.0))
            avg_price = float(h_info.get("average_buy_price", 0.0))
            if qty <= 0:
                continue

            lot = EquityLot(
                ticker=ticker.upper(),
                account_label=req.account_label,
                lot_type="other",
                shares=qty,
                cost_basis_per_share=avg_price,
                acquisition_date=datetime.now().strftime("%Y-%m-%d"),
                notes="Synced via Robinhood API Integration",
                created_at=now_str
            )
            db.add(lot)
            inserted_lots += 1

        # 3. Fetch historical orders
        orders = r.orders.get_all_stock_orders()
        recent_orders = orders[:100] if orders else []

        instrument_cache = {}
        txs_inserted = 0

        existing_txs = db.query(ExternalTransaction).filter(ExternalTransaction.account_label == req.account_label).all()
        existing_fingerprints = {
            (t.execution_date, t.ticker, t.side, round(t.qty, 4), round(t.price, 4))
            for t in existing_txs
        }

        for order in recent_orders:
            if order.get("state") != "filled":
                continue

            qty = float(order.get("cumulative_quantity") or 0.0)
            if qty <= 0:
                continue

            price = float(order.get("average_price") or 0.0)
            side = order.get("side", "").upper()
            created_at_str = order.get("last_transaction_at") or order.get("created_at") or ""
            if not created_at_str:
                continue

            date_str = created_at_str[:10]
            instrument = order.get("instrument")
            if not instrument:
                continue

            if instrument not in instrument_cache:
                try:
                    symbol = r.get_symbol_by_url(instrument)
                    if symbol:
                        instrument_cache[instrument] = symbol.upper()
                except Exception:
                    continue

            ticker = instrument_cache.get(instrument)
            if not ticker:
                continue

            fingerprint = (date_str, ticker, side, round(qty, 4), round(price, 4))
            if fingerprint in existing_fingerprints:
                continue

            db_tx = ExternalTransaction(
                account_label=req.account_label,
                ticker=ticker,
                side=side,
                qty=qty,
                price=price,
                execution_date=date_str,
                raw_details=f"Synced via Robinhood API (Order ID: {order.get('id')})",
                created_at=now_str
            )
            db.add(db_tx)

            db_order = ExternalOrder(
                account_label=req.account_label,
                ticker=ticker,
                side=side,
                qty=qty,
                limit_price=price,
                time_in_force=order.get("time_in_force", "DAY").upper(),
                status="reconciled",
                filled_price=price,
                filled_qty=qty,
                execution_date=date_str,
                created_at=now_str,
                updated_at=now_str
            )
            db.add(db_order)
            txs_inserted += 1

        db.commit()
        r.logout()

        return {
            "status": "success",
            "account_label": req.account_label,
            "cash": acct.cash,
            "positions_synced": inserted_lots,
            "transactions_synced": txs_inserted
        }
    except Exception as e:
        db.rollback()
        try:
            r.logout()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to fetch Robinhood portfolio: {e}")


@app.post("/api/equity/lots/{lot_id}/sell")
def sell_equity_lot(lot_id: int, req: EquityLotSellRequest, db=Depends(get_db)):
    """Record selling some/all shares of a specific lot. Reduces the lot (deletes it if fully sold),
    computes the realized gain/loss, and — when it's a loss and requested — drops a 31-day wash-sale
    block so the auto-trader can't re-buy the name inside the window."""
    row = db.query(EquityLot).filter(EquityLot.id == lot_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found")
    sell_shares = float(req.shares)
    if sell_shares <= 0 or sell_shares > row.shares + 1e-9:
        raise HTTPException(status_code=400, detail=f"shares must be between 0 and {row.shares}")
    # Sale price: explicit > latest local price > cost basis (so a missing price never fabricates a gain).
    price = req.sale_price
    if price is None:
        from data_ingestion.analyst_fetcher import latest_or_refresh
        f = latest_or_refresh(row.ticker, db, stale_days=1)
        price = f.current_price if (f and f.current_price is not None) else row.cost_basis_per_share
    try:
        sale_date = datetime.strptime((req.sale_date or datetime.now().date().isoformat())[:10], "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="sale_date must be YYYY-MM-DD")
    realized_gain = (float(price) - row.cost_basis_per_share) * sell_shares
    proceeds = float(price) * sell_shares
    ticker = row.ticker

    fully_sold = abs(sell_shares - row.shares) < 1e-9
    if fully_sold:
        db.delete(row)
        remaining = 0.0
    else:
        row.shares = round(row.shares - sell_shares, 6)
        note = f"Sold {sell_shares:g} sh @ ${float(price):.2f} on {sale_date.isoformat()}"
        row.notes = (f"{row.notes} | {note}" if row.notes else note)
        remaining = row.shares

    block_created = False
    if realized_gain < 0 and req.add_wash_sale_block:
        blocked_until = (sale_date + timedelta(days=31)).isoformat()
        db.add(TradingBlock(
            ticker=ticker, block_type="wash_sale",
            reason=f"Loss sale {sale_date.isoformat()} (~{round(realized_gain):,}) — no re-buys until {blocked_until}.",
            sale_date=sale_date.isoformat(), realized_loss=realized_gain, shares=sell_shares,
            blocked_until=blocked_until, active=True, created_at=datetime.now().isoformat(timespec="seconds"),
        ))
        block_created = True
    db.commit()
    return {
        "status": "success", "ticker": ticker, "sold_shares": sell_shares, "remaining_shares": remaining,
        "sale_price": float(price), "proceeds": proceeds, "realized_gain": realized_gain,
        "is_loss": realized_gain < 0, "fully_sold": fully_sold,
        "wash_sale_suggested": realized_gain < 0, "wash_sale_block_created": block_created,
    }


@app.get("/api/equity/tax-profile")
def get_tax_profile(db=Depends(get_db)):
    row = db.query(TaxProfile).filter(TaxProfile.id == 1).first()
    return _tax_profile_dict(row)


@app.post("/api/equity/tax-profile")
def upsert_tax_profile(req: TaxProfileRequest, db=Depends(get_db)):
    if req.filing_status not in ("single", "married_joint", "married_separate", "head_of_household"):
        raise HTTPException(status_code=400, detail="filing_status must be single, married_joint, married_separate, or head_of_household")
    if not (2000 <= int(req.tax_year) <= 2100):
        raise HTTPException(status_code=400, detail="tax_year out of range")
    for fld, val in (("ordinary_income", req.ordinary_income), ("magi", req.magi), ("carryover_loss", req.carryover_loss)):
        if val < 0:
            raise HTTPException(status_code=400, detail=f"{fld} cannot be negative")
    for fld, val in (("state_ltcg_rate", req.state_ltcg_rate), ("state_stcg_rate", req.state_stcg_rate)):
        if not (0.0 <= val <= 1.0):
            raise HTTPException(status_code=400, detail=f"{fld} must be a decimal between 0 and 1 (e.g. 0.093 for 9.3%)")
    row = db.query(TaxProfile).filter(TaxProfile.id == 1).first()
    if not row:
        row = TaxProfile(id=1)
        db.add(row)
    row.filing_status = req.filing_status
    row.ordinary_income = req.ordinary_income
    row.magi = req.magi
    row.state_ltcg_rate = req.state_ltcg_rate
    row.state_stcg_rate = req.state_stcg_rate
    row.carryover_loss = req.carryover_loss
    row.tax_year = req.tax_year
    db.commit()
    return {"status": "success", "profile": _tax_profile_dict(row)}


@app.get("/api/equity/forecast/{ticker}")
def get_equity_forecast(ticker: str, db=Depends(get_db)):
    from data_ingestion.analyst_fetcher import latest_or_refresh, ensure_equity_price
    ticker = ticker.upper().strip()
    row = latest_or_refresh(ticker, db, stale_days=1)
    if not row:
        raise HTTPException(status_code=404, detail="No forecast or local price data available for ticker")
    out = _forecast_dict(row)
    if out and out.get("current_price") is None:
        price = ensure_equity_price(db, ticker)
        if price is not None:
            out["current_price"] = price
            if out.get("target_mean") is not None:
                out["upside_pct"] = (out["target_mean"] - price) / price
    if not out or out.get("current_price") is None:
        raise HTTPException(status_code=404, detail="No forecast or local price data available for ticker")
    return out


@app.get("/api/equity/grant-timeline/{ticker}")
def get_equity_grant_timeline(ticker: str, db=Depends(get_db)):
    """Per-stock grant timeline for the deep-dive chart. From the earliest grant to today returns:
    the daily market price, the running SHARE-WEIGHTED average cost basis (steps as each grant vests),
    and the share of granted shares that are in-the-money (price > their own lot basis) vs underwater.
    The line is downsampled for payload size but every grant day is preserved. Powers the GrantTimeline
    UI component (reusable across any held ticker)."""
    ticker = ticker.upper().strip()
    lots = db.query(EquityLot).filter(EquityLot.ticker == ticker).order_by(
        EquityLot.acquisition_date.asc()).all()
    if not lots:
        raise HTTPException(status_code=404, detail=f"No grants/lots recorded for {ticker}")

    from data_ingestion.price_fetcher import ensure_equity_daily_prices
    ensure_equity_daily_prices(db, ticker)

    start_date = min(l.acquisition_date[:10] for l in lots)
    rows = db.query(DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.ticker == ticker, DailyPrice.date >= start_date
    ).order_by(DailyPrice.date.asc()).all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No daily price history for {ticker}")

    grants_by_date = {}
    for l in lots:
        grants_by_date.setdefault(l.acquisition_date[:10], []).append(l)
    grant_dates_sorted = sorted(grants_by_date.keys())
    grant_dates = set(grant_dates_sorted)

    vested = []          # (shares, basis) for every grant vested up to the current day
    gi = 0
    total_points = len(rows)
    step = max(1, total_points // 750)   # keep ~750 line points; grant days always kept

    series = []
    for i, (d, close) in enumerate(rows):
        d10 = d[:10]
        while gi < len(grant_dates_sorted) and grant_dates_sorted[gi] <= d10:
            for l in grants_by_date[grant_dates_sorted[gi]]:
                vested.append((l.shares, l.cost_basis_per_share))
            gi += 1
        if not vested:
            continue
        total_shares = sum(s for s, _ in vested)
        cost_sum = sum(s * b for s, b in vested)
        avg_basis = cost_sum / total_shares if total_shares else 0.0
        profit_shares = sum(s for s, b in vested if close > b)
        profitable_pct = 100.0 * profit_shares / total_shares if total_shares else 0.0
        if i % step == 0 or d10 in grant_dates or i == total_points - 1:
            series.append({
                "date": d10,
                "price": round(close, 2),
                "avg_basis": round(avg_basis, 2),
                "shares_held": round(total_shares, 2),
                "profitable_pct": round(profitable_pct, 1),
                "underwater_pct": round(100.0 - profitable_pct, 1),
                "gain_pct": round((close / avg_basis - 1.0) * 100.0, 1) if avg_basis else 0.0,
                "is_grant": d10 in grant_dates,
            })

    grant_markers = []
    for d in grant_dates_sorted:
        day_lots = grants_by_date[d]
        sh = sum(l.shares for l in day_lots)
        wbasis = (sum(l.shares * l.cost_basis_per_share for l in day_lots) / sh) if sh else 0.0
        mp = next((c for (dt, c) in rows if dt[:10] >= d), None)
        grant_markers.append({
            "date": d, "shares": round(sh, 2), "basis": round(wbasis, 2),
            "price_at_grant": round(mp, 2) if mp is not None else None,
            "lot_type": day_lots[0].lot_type,
        })

    latest_close = rows[-1][1]
    total_shares = sum(l.shares for l in lots)
    cost_sum = sum(l.shares * l.cost_basis_per_share for l in lots)
    avg_basis = (cost_sum / total_shares) if total_shares else 0.0
    profit_shares = sum(l.shares for l in lots if latest_close > l.cost_basis_per_share)
    summary = {
        "ticker": ticker,
        "current_price": round(latest_close, 2),
        "avg_basis": round(avg_basis, 2),
        "total_shares": round(total_shares, 2),
        "market_value": round(latest_close * total_shares, 2),
        "cost_value": round(cost_sum, 2),
        "unrealized_gain": round((latest_close - avg_basis) * total_shares, 2),
        "unrealized_gain_pct": round((latest_close / avg_basis - 1.0) * 100.0, 1) if avg_basis else 0.0,
        "profitable_pct": round(100.0 * profit_shares / total_shares, 1) if total_shares else 0.0,
        "num_grants": len(lots),
        "first_grant": grant_dates_sorted[0],
        "as_of": rows[-1][0][:10],
    }
    from ml_engine.vest_schedule import ensure_vest_schedules, schedule_dict
    ensure_vest_schedules(db)
    vest_rows = db.query(EquityVestSchedule).filter(EquityVestSchedule.ticker == ticker).all()
    vest_schedules = [schedule_dict(v) for v in vest_rows]
    upcoming_vests = []
    for vs in vest_schedules:
        if vs.get("vesting_complete"):
            continue
        for u in vs.get("upcoming", [])[:2]:
            upcoming_vests.append({**u, "lot_type": vs["lot_type"], "cadence": vs["cadence"]})
    return {"summary": summary, "series": series, "grants": grant_markers,
            "vest_schedules": vest_schedules, "upcoming_vests": upcoming_vests}


@app.post("/api/equity/analyze")
def start_equity_analyze(req: EquityAnalyzeRequest):
    if req.objective not in ("raise_cash", "harvest_loss", "exit_ticker"):
        raise HTTPException(status_code=400, detail="objective must be raise_cash, harvest_loss, or exit_ticker")
    active = [j for j in _jobs_snapshot() if j["type"] == "equity_analyze" and j["status"] == "running"]
    if active:
        return {"status": "already_running", "job_id": active[0]["id"]}
    jid = _job_new("equity_analyze", "Analyzing equity lots")
    threading.Thread(target=_run_equity_analyze_job, args=(jid, req.dict()), daemon=True).start()
    return {"status": "started", "job_id": jid}


@app.get("/api/equity/analyze/result")
def get_equity_analyze_result(job_id: str):
    if job_id in _EQUITY_ANALYZE_RESULTS:
        return {"status": "done", "result": _EQUITY_ANALYZE_RESULTS[job_id]}
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}


AUTO_TRADING_PAUSED_KEY = "auto_trading_paused"


def _auto_trading_paused(db) -> bool:
    row = db.query(AppSetting).filter(AppSetting.key == AUTO_TRADING_PAUSED_KEY).first()
    return bool(row and str(row.value).lower() in ("true", "1", "yes"))


def _block_dict(b):
    days_remaining = None
    if b.blocked_until:
        try:
            days_remaining = max(0, (datetime.strptime(b.blocked_until, "%Y-%m-%d").date() - datetime.now().date()).days)
        except Exception:
            days_remaining = None
    return {
        "id": b.id, "ticker": b.ticker, "block_type": b.block_type, "reason": b.reason,
        "account_label": b.account_label, "sale_date": b.sale_date, "realized_loss": b.realized_loss,
        "shares": b.shares, "blocked_until": b.blocked_until, "active": b.active,
        "created_at": b.created_at, "days_remaining": days_remaining,
    }


@app.get("/api/equity/trading-blocks")
def list_trading_blocks(db=Depends(get_db)):
    """Active BUY guards + the global auto-trading pause state (UI safety panel)."""
    today = datetime.now().date().isoformat()
    rows = db.query(TradingBlock).filter(TradingBlock.active == True).all()  # noqa: E712
    # Opportunistically retire expired wash-sale blocks so the list stays clean.
    fresh = []
    changed = False
    for b in rows:
        if b.block_type == "wash_sale" and b.blocked_until and b.blocked_until < today:
            b.active = False
            changed = True
            continue
        fresh.append(b)
    if changed:
        db.commit()
    fresh.sort(key=lambda b: (b.block_type != "wash_sale", b.ticker))
    return {"blocks": [_block_dict(b) for b in fresh], "auto_trading_paused": _auto_trading_paused(db)}


@app.post("/api/equity/auto-trade-block")
def set_equity_auto_trade_block(req: EquityAutoTradeBlockRequest, db=Depends(get_db)):
    """Toggle whether the auto-trader may buy a ticker you hold externally (e.g. PINS for wash-sale harvest)."""
    from data_ingestion.equity_universe_sync import set_equity_auto_trade_block as _set_block
    ticker = req.ticker.upper().strip()
    if not db.query(EquityLot).filter(EquityLot.ticker == ticker).first():
        raise HTTPException(status_code=404, detail=f"No equity lots for {ticker}")
    blocked = _set_block(db, ticker, req.blocked)
    return {"status": "success", "ticker": ticker, "blocked": req.blocked, "auto_trade_blocked": blocked}


@app.get("/api/equity/vest-schedules")
def get_equity_vest_schedules(db=Depends(get_db)):
    from ml_engine.vest_schedule import ensure_vest_schedules
    return {"schedules": ensure_vest_schedules(db)}


@app.post("/api/equity/vest-schedules")
def upsert_equity_vest_schedule(req: EquityVestScheduleRequest, db=Depends(get_db)):
    from ml_engine.vest_schedule import upsert_vest_schedule
    try:
        row = upsert_vest_schedule(db, req.dict())
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "schedule": row}


@app.post("/api/equity/trading-blocks")
def create_trading_block(req: TradingBlockRequest, db=Depends(get_db)):
    ticker = req.ticker.upper().strip()
    if req.block_type not in ("wash_sale", "permanent"):
        raise HTTPException(status_code=400, detail="block_type must be wash_sale or permanent")
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    blocked_until = None
    sale_date = None
    if req.block_type == "wash_sale":
        try:
            sd = datetime.strptime((req.sale_date or datetime.now().date().isoformat())[:10], "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(status_code=400, detail="sale_date must be YYYY-MM-DD")
        if not (1 <= int(req.window_days) <= 120):
            raise HTTPException(status_code=400, detail="window_days out of range (1-120)")
        sale_date = sd.isoformat()
        blocked_until = (sd + timedelta(days=int(req.window_days))).isoformat()
        reason = req.reason or (f"Wash-sale guard: loss sale {sale_date}"
                                f"{(' of ' + format(req.shares, ',.0f') + ' sh') if req.shares else ''}"
                                f" — no re-buys until {blocked_until}.")
    else:
        reason = req.reason or f"Never-trade: held/managed externally ({req.account_label or 'manual'})."
    row = TradingBlock(
        ticker=ticker, block_type=req.block_type, reason=reason, account_label=req.account_label,
        sale_date=sale_date, realized_loss=req.realized_loss, shares=req.shares,
        blocked_until=blocked_until, active=True, created_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "block": _block_dict(row)}


@app.delete("/api/equity/trading-blocks/{block_id}")
def release_trading_block(block_id: int, db=Depends(get_db)):
    row = db.query(TradingBlock).filter(TradingBlock.id == block_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Block not found")
    row.active = False
    db.commit()
    return {"status": "success", "block": _block_dict(row)}


@app.post("/api/execution/auto-trading")
def set_auto_trading(req: AutoTradingRequest, db=Depends(get_db)):
    """Global kill-switch. paused=True freezes ALL auto-trading (buys and sells)."""
    row = db.query(AppSetting).filter(AppSetting.key == AUTO_TRADING_PAUSED_KEY).first()
    if not row:
        row = AppSetting(key=AUTO_TRADING_PAUSED_KEY)
        db.add(row)
    row.value = "true" if req.paused else "false"
    db.commit()
    return {"status": "success", "auto_trading_paused": req.paused}


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
                         "next_open": clock.next_open.isoformat() if clock.next_open else None,
                         "next_close": clock.next_close.isoformat() if clock.next_close else None,
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

    # Fetch latest dates in DB for prices, sentiment, macro, and news
    last_refreshed = {}
    try:
        latest_recent = db.query(RecentPrice).order_by(RecentPrice.date.desc()).first()
        last_refreshed["prices_hourly"] = latest_recent.date if latest_recent else "none"
    except Exception:
        last_refreshed["prices_hourly"] = "error"

    try:
        latest_daily = db.query(DailyPrice).order_by(DailyPrice.date.desc()).first()
        last_refreshed["prices_daily"] = latest_daily.date if latest_daily else "none"
    except Exception:
        last_refreshed["prices_daily"] = "error"

    try:
        latest_macro = db.query(MacroIndicator).order_by(MacroIndicator.date.desc()).first()
        last_refreshed["macro"] = latest_macro.date if latest_macro else "none"
    except Exception:
        last_refreshed["macro"] = "error"

    try:
        latest_sent = db.query(TickerSentiment).order_by(TickerSentiment.date.desc()).first()
        last_refreshed["sentiment"] = latest_sent.date if latest_sent else "none"
    except Exception:
        last_refreshed["sentiment"] = "error"

    try:
        latest_news_llm = db.query(NewsLLMScore).order_by(NewsLLMScore.published_utc.desc()).first()
        last_refreshed["news_llm"] = latest_news_llm.published_utc[:16].replace("T", " ") if latest_news_llm and latest_news_llm.published_utc else "none"
    except Exception:
        last_refreshed["news_llm"] = "error"

    res = {
        "services": svc,
        "execution_strategy": EXECUTION_STRATEGY,
        "as_of": now.strftime("%H:%M:%S"),
        "last_refreshed": last_refreshed
    }
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


# ---------------------------------------------------------------------------
# Component 4: Crash Radar (Tab 5) API Endpoints
# ---------------------------------------------------------------------------
from pydantic import BaseModel as _BaseModel

_FORECAST_RESULTS = {}
_WARGAME_RESULTS = {}
_SCENARIO_RESULTS = {}

class WargameSweepRequest(_BaseModel):
    theta_range: Optional[dict] = None
    k_range: Optional[dict] = None
    gamma_range: Optional[dict] = None

class ScenarioComparisonRequest(_BaseModel):
    theta: Optional[float] = None
    k: Optional[float] = None
    gamma: Optional[float] = None

class WargameInterpretRequest(_BaseModel):
    comparison: dict

class ApplyRebalancingRequest(_BaseModel):
    confirm_execution: bool
    target_posture: str
    preset: Optional[str] = "balanced"
    theta: Optional[float] = None
    k: Optional[float] = None
    gamma: Optional[float] = None

def _run_forecast_job(jid):
    try:
        _job_update(jid, progress=10, stage="Loading data")
        from ml_engine.crash_model import train_and_evaluate_forecast
        _job_update(jid, progress=30, stage="Training drawdown models")
        results = train_and_evaluate_forecast()

        # Save to database
        _job_update(jid, progress=85, stage="Saving to database")
        from ml_engine.crash_radar import get_latest_date
        from app.database import SessionLocal, CrashRiskSnapshot
        db = SessionLocal()
        latest_dt = get_latest_date()
        snap = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest_dt).first()
        if snap:
            snap.experimental_forecast_odds = _json.dumps(results)
            db.add(snap)
            db.commit()
        db.close()

        _FORECAST_RESULTS[jid] = results
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])

def _run_wargame_job(jid, theta_range, k_range, gamma_range):
    try:
        _job_update(jid, progress=15, stage="Constructing scenario ensemble")
        from ml_engine.wargame import run_wargame_sweep
        _job_update(jid, progress=40, stage="Running sweeps")
        res = run_wargame_sweep(theta_range, k_range, gamma_range)
        _WARGAME_RESULTS[jid] = res
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])

def _run_scenario_job(jid, custom_knobs):
    try:
        from ml_engine.wargame import run_scenario_comparison, save_wargame_comparison
        res = run_scenario_comparison(
            custom_knobs=custom_knobs,
            progress_cb=lambda p, s: _job_update(jid, progress=p, stage=s),
        )
        _SCENARIO_RESULTS[jid] = res
        # Persist so the last comparison renders by default on the next page load.
        try:
            from ml_engine.crash_model import crash_data_fingerprint
            save_wargame_comparison(res, crash_data_fingerprint())
        except Exception as ce:
            print(f"⚠ Could not cache scenario comparison: {ce}")
        _job_update(jid, progress=100, stage="Complete", status="done")
    except Exception as e:
        import traceback; traceback.print_exc()
        _job_update(jid, status="error", error=str(e)[:200])

@app.get("/api/crash/index")
def get_crash_index():
    """Returns the latest CrashRiskSnapshot, generating it on the fly if needed."""
    from app.database import SessionLocal, CrashRiskSnapshot
    from ml_engine.crash_radar import compute_composite_index, get_latest_date, persist_crash_snapshot, is_valid_snapshot
    db = SessionLocal()
    try:
        latest_dt = get_latest_date()
        snap = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest_dt).first()
        if not is_valid_snapshot(snap):
            # Generate/repair on the fly (handles missing OR corrupt/partial rows)
            fresh, _ = compute_composite_index(latest_dt)
            persist_crash_snapshot(fresh)
            # reload
            db.expire_all()
            snap = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest_dt).first()

        if not snap:
            raise HTTPException(status_code=404, detail="No crash risk snapshot available.")

        # Parse fields
        trigger_reasons = []
        if snap.trigger_reasons:
            try:
                trigger_reasons = _json.loads(snap.trigger_reasons)
            except Exception:
                trigger_reasons = [snap.trigger_reasons]

        debt_cycle = {}
        if snap.debt_cycle_read:
            try:
                debt_cycle = _json.loads(snap.debt_cycle_read)
            except Exception:
                pass

        forecast_odds = []
        if snap.experimental_forecast_odds:
            try:
                forecast_odds = _json.loads(snap.experimental_forecast_odds)
            except Exception:
                pass

        return {
            "as_of_date": snap.as_of_date,
            "composite_index": snap.composite_index,
            "risk_band": snap.risk_band,
            "current_posture": snap.current_posture,
            "trigger_reasons": trigger_reasons,
            "buckets": {
                "valuation": snap.valuation_subscore or 50.0,
                "monetary": snap.monetary_subscore or 50.0,
                "credit": snap.credit_subscore or 50.0,
                "financial_conditions": snap.financial_conditions_subscore or 50.0,
                "lending": snap.lending_subscore or 50.0,
                "labor": snap.labor_subscore or 50.0,
                "real_activity": snap.real_activity_subscore or 50.0,
                "internals": snap.internals_subscore or 50.0,
                "cycle": snap.cycle_subscore or 50.0,
                "hmm_regime": snap.hmm_regime_subscore or 50.0
            },
            "debt_cycle_metrics": debt_cycle,
            "experimental_forecast_odds": forecast_odds
        }
    finally:
        db.close()

@app.get("/api/crash/timeline")
def get_crash_timeline():
    """Returns the historical composite crash index timeline for the past 5 years."""
    from ml_engine.crash_radar import get_crash_index_timeline
    try:
        return get_crash_index_timeline()
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to calculate timeline: {e}")

@app.post("/api/crash/forecast")
def trigger_crash_forecast():
    """Spawns background job to calculate experimental drawdown odds."""
    jid = _job_new("crash_forecast", "Calculating experimental crash forecast")
    threading.Thread(target=_run_forecast_job, args=(jid,), daemon=True).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/crash/forecast/result")
def get_crash_forecast_result(job_id: str):
    """Polls experimental crash forecast status."""
    if job_id in _FORECAST_RESULTS:
        return {
            "status": "completed",
            "predictions": _FORECAST_RESULTS[job_id],
            "caveat": "Illustrative only. Small-sample model trained on 3-4 historical bear episodes since 1998."
        }
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}

@app.get("/api/crash/playbook")
def get_crash_playbook(preset: str = "balanced"):
    """Returns the defensive playbook configurations."""
    from ml_engine.defensive_strategist import build_defensive_playbook
    try:
        pb = build_defensive_playbook(preset_name=preset)
        return pb
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/crash/compare")
def compare_glide_presets(
    years: int = 5,
    theta: Optional[float] = None,
    k: Optional[float] = None,
    gamma: Optional[float] = None,
):
    """Read-only walk-forward backtest comparing the glide-path presets (and optional custom
    knobs) vs Buy & Hold over the real cached crash-risk history. Does NOT touch any portfolio."""
    from ml_engine.wargame import run_preset_comparison
    custom = None
    if theta is not None and k is not None and gamma is not None:
        custom = {"theta": theta, "k": k, "gamma": gamma}
    try:
        result = run_preset_comparison(lookback_years=years, custom_knobs=custom)
        if isinstance(result, dict) and result.get("error"):
            raise HTTPException(status_code=422, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/crash/wargame")
def trigger_wargame(req: WargameSweepRequest):
    """Spawns background wargame parameter sweep job."""
    jid = _job_new("crash_wargame", "Running knob sweeps over scenarios")
    threading.Thread(
        target=_run_wargame_job,
        args=(jid, req.theta_range, req.k_range, req.gamma_range),
        daemon=True
    ).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/crash/wargame/result")
def get_crash_wargame_result(job_id: str):
    """Polls wargame parameter sweep results."""
    if job_id in _WARGAME_RESULTS:
        return {
            "status": "completed",
            "result": _WARGAME_RESULTS[job_id]
        }
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}

@app.post("/api/crash/wargame/scenarios")
def trigger_scenario_comparison(req: ScenarioComparisonRequest):
    """Spawns a background job replaying every defensive policy across historical bear regimes
    and synthetic crashes. Read-only; passes the user's custom knobs through when supplied."""
    custom = None
    if req.theta is not None and req.k is not None and req.gamma is not None:
        custom = {"theta": req.theta, "k": req.k, "gamma": req.gamma}
    jid = _job_new("crash_scenarios", "Replaying policies over historical & synthetic crashes")
    threading.Thread(target=_run_scenario_job, args=(jid, custom), daemon=True).start()
    return {"status": "started", "job_id": jid}

@app.get("/api/crash/wargame/scenarios/result")
def get_scenario_comparison_result(job_id: str):
    """Polls the scenario comparison job."""
    if job_id in _SCENARIO_RESULTS:
        return {"status": "completed", "result": _SCENARIO_RESULTS[job_id]}
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}

@app.post("/api/crash/wargame/interpret")
def interpret_wargame_endpoint(req: WargameInterpretRequest):
    """AI analyst: plain-English summary of a scenario comparison result (mirrors the tab-2
    expert interpretation). Synchronous OpenAI call. The result is cached to disk so it shows by
    default and is not re-billed on every page load."""
    from ml_engine.wargame_analyst import interpret_wargame
    from ml_engine.wargame import save_wargame_analyst
    try:
        result = interpret_wargame(req.comparison)
        try:
            from ml_engine.crash_model import crash_data_fingerprint
            save_wargame_analyst(result, crash_data_fingerprint())
        except Exception as ce:
            print(f"⚠ Could not cache wargame analyst: {ce}")
        return result
    except Exception as e:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _next_weekday_run_iso(hour, minute, tz="America/New_York"):
    """Next occurrence of a Mon–Fri HH:MM wall-clock time in the given timezone, as ISO-8601.

    Mirrors the Crash Radar scheduler trigger (CronTrigger mon-fri) so the UI can show when the
    next automatic refresh is due."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now()
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)
    while cand.weekday() >= 5:  # Sat=5, Sun=6
        cand += timedelta(days=1)
    return cand.isoformat()


@app.get("/api/crash/wargame/cache")
def get_wargame_cache():
    """Returns the last persisted scenario comparison + AI analyst so the Wargame card can render
    immediately without re-running anything. Includes staleness flags (true when new forecast data
    has arrived since the cached result was produced)."""
    from ml_engine.wargame import load_wargame_cache
    cache = load_wargame_cache()
    cur_fp = None
    try:
        from ml_engine.crash_model import crash_data_fingerprint
        cur_fp = crash_data_fingerprint()
    except Exception:
        pass
    comp_fp = cache.get("fingerprint")
    analyst_fp = cache.get("analyst_fingerprint")
    return {
        "comparison": cache.get("comparison"),
        "analyst": cache.get("analyst"),
        "comparison_generated_at": cache.get("comparison_generated_at"),
        "analyst_generated_at": cache.get("analyst_generated_at"),
        "comparison_stale": bool(cur_fp and comp_fp and cur_fp != comp_fp),
        "analyst_stale": bool(cur_fp and analyst_fp and cur_fp != analyst_fp),
        "next_scheduled": _next_weekday_run_iso(9, 30),
    }


@app.get("/api/crash/status")
def get_crash_status():
    """Timing metadata for the Crash Radar artifacts: when each was last refreshed and when the
    next automatic, data-gated refresh is due. Drives the "Last updated / Next auto-update" badges."""
    from app.database import SessionLocal, CrashRiskSnapshot
    from ml_engine.crash_radar import get_latest_date
    from ml_engine.wargame import load_wargame_cache

    forecast_updated_at = None
    try:
        from ml_engine.crash_model import _FORECAST_STATE_PATH
        with open(_FORECAST_STATE_PATH) as f:
            forecast_updated_at = _json.load(f).get("updated_at")
    except Exception:
        pass

    snap_created_at, as_of_date = None, None
    cur_fp = None
    db = SessionLocal()
    try:
        latest = get_latest_date()
        snap = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest).first()
        if snap:
            snap_created_at = snap.created_at
            as_of_date = snap.as_of_date
    finally:
        db.close()
    try:
        from ml_engine.crash_model import crash_data_fingerprint
        cur_fp = crash_data_fingerprint()
    except Exception:
        pass

    cache = load_wargame_cache()
    next_run = _next_weekday_run_iso(9, 30)
    schedule = "Weekdays 9:30 AM ET, only when new data has arrived"

    return {
        "data_fingerprint": (cur_fp or "")[:12],
        "next_scheduled": next_run,
        "index": {
            "last_refresh": forecast_updated_at or snap_created_at,
            "as_of_date": as_of_date,
            "next_scheduled": next_run,
            "schedule": schedule,
        },
        "forecast": {
            "last_refresh": forecast_updated_at or snap_created_at,
            "next_scheduled": next_run,
            "schedule": schedule,
        },
        "wargame": {
            "last_run": cache.get("comparison_generated_at"),
            "next_scheduled": next_run,
            "schedule": schedule,
            "stale": bool(cur_fp and cache.get("fingerprint") and cur_fp != cache.get("fingerprint")),
        },
        "analyst": {
            "last_run": cache.get("analyst_generated_at"),
            "next_scheduled": None,
            "schedule": "On demand (cached; not auto-run to control cost)",
            "stale": bool(cur_fp and cache.get("analyst_fingerprint") and cur_fp != cache.get("analyst_fingerprint")),
        },
    }

def _latest_paper_price(db, symbol):
    """Latest price for a paper-rebalance ticker: prefer hourly recent_prices, then fall back to
    daily_prices (where the defensive ETFs live, since they are outside the tradeable universe),
    and finally to a neutral $100 placeholder."""
    from app.database import RecentPrice, DailyPrice
    r = db.query(RecentPrice).filter(RecentPrice.ticker == symbol).order_by(RecentPrice.date.desc()).first()
    if r and r.close:
        return float(r.close)
    d = db.query(DailyPrice).filter(DailyPrice.ticker == symbol).order_by(DailyPrice.date.desc()).first()
    if d and d.close:
        return float(d.close)
    return 100.0


def _compute_rebalance_plan(db, preset: str = "balanced", custom_knobs=None):
    """
    Pure (read-only) computation of the defensive rebalancing plan: diffs target
    blended weights against current paper positions and returns the proposed
    orders plus validation. Shared by the preview (dry-run) and execute endpoints
    so the preview is guaranteed to match what execution will do.
    """
    from ml_engine.defensive_strategist import build_defensive_playbook
    from app.database import VirtualAccount, VirtualPosition, RecentPrice

    account = db.query(VirtualAccount).filter(VirtualAccount.id == 1).first()
    account_exists = account is not None
    cash = float(account.cash) if account else 100000.0

    positions = db.query(VirtualPosition).filter(VirtualPosition.mode == "virtual").all()

    def get_latest_price(symbol):
        return _latest_paper_price(db, symbol)

    portfolio_value = cash
    current_values = {}
    for pos in positions:
        price = get_latest_price(pos.ticker)
        current_values[pos.ticker] = float(pos.quantity) * price
        portfolio_value += current_values[pos.ticker]

    pb = build_defensive_playbook(preset_name=preset, custom_knobs=custom_knobs)
    if pb.get("error"):
        return {"error": pb["error"]}
    d = float(pb.get("de_risk_coefficient", 0.0))

    # Aggressive sleeve: current holdings proportions, or 100% SPY if account is empty
    has_positions = bool(current_values) and sum(current_values.values()) > 0
    if has_positions:
        total_pos_val = sum(current_values.values())
        w_agg = {t: v / total_pos_val for t, v in current_values.items()}
    else:
        w_agg = {"SPY": 1.0}

    # Defensive sleeve: the active inflation/deflation safe-asset mix
    safe_mix = pb.get("stances", {}).get("safe_asset_selection", {}).get("mix", {})
    w_def = {k: float(v) / 100.0 for k, v in safe_mix.items()}

    all_tickers = set(w_agg) | set(w_def)
    w_target = {t: (1.0 - d) * w_agg.get(t, 0.0) + d * w_def.get(t, 0.0) for t in all_tickers}

    orders = []
    for t in sorted(all_tickers):
        target_val = portfolio_value * w_target[t]
        curr_val = current_values.get(t, 0.0)
        diff_val = target_val - curr_val
        price = get_latest_price(t)
        cur_w = (curr_val / portfolio_value * 100.0) if portfolio_value > 0 else 0.0
        if abs(diff_val) > 50.0:
            orders.append({
                "ticker": t,
                "side": "buy" if diff_val > 0 else "sell",
                "qty": round(abs(diff_val) / price, 4),
                "price": round(price, 2),
                "value": round(abs(diff_val), 2),
                "current_weight": round(cur_w, 2),
                "target_weight": round(w_target[t] * 100.0, 2),
            })

    buy_total = sum(o["value"] for o in orders if o["side"] == "buy")
    sell_total = sum(o["value"] for o in orders if o["side"] == "sell")
    turnover = (buy_total + sell_total) / portfolio_value if portfolio_value > 0 else 0.0
    est_cash_after = cash + sell_total - buy_total

    warnings, errors = [], []
    if not account_exists:
        warnings.append("No paper account exists yet; a fresh $100,000 virtual account is created on execution.")
    if portfolio_value <= 0:
        errors.append("Portfolio value is zero or negative; nothing to rebalance.")
    if not has_positions:
        warnings.append("No current holdings — the aggressive sleeve defaults to 100% SPY before de-risking.")
    if d <= 0.001:
        warnings.append("De-risk coefficient is ~0% (the glide path says stay invested); few or no orders are expected.")
    if not orders and portfolio_value > 0:
        warnings.append("Portfolio is already within $50 of every target weight — no orders needed.")
    if buy_total > cash + sell_total + 1.0:
        warnings.append("Planned buys exceed cash available after sells; buy orders are capped to available cash on execution.")
    if turnover > 0.6:
        warnings.append(f"High turnover (~{turnover*100:.0f}% of the portfolio). Consider a more gradual preset.")

    return {
        "preset_applied": pb.get("preset_applied", preset),
        "de_risk_coefficient": d,
        "composite_index": pb.get("composite_index"),
        "risk_band": pb.get("risk_band"),
        "current_posture": pb.get("current_posture"),
        "as_of_date": pb.get("as_of_date"),
        "portfolio_value": round(portfolio_value, 2),
        "cash_before": round(cash, 2),
        "est_cash_after": round(est_cash_after, 2),
        "buy_total": round(buy_total, 2),
        "sell_total": round(sell_total, 2),
        "turnover_pct": round(turnover * 100.0, 1),
        "orders": orders,
        "validation": {"ok": len(errors) == 0, "warnings": warnings, "errors": errors},
    }


def _knobs_from(theta, k, gamma):
    if theta is not None and k is not None and gamma is not None:
        return {"theta": theta, "k": k, "gamma": gamma}
    return None


@app.get("/api/crash/apply/preview")
def preview_crash_rebalancing(
    preset: str = "balanced",
    theta: Optional[float] = None,
    k: Optional[float] = None,
    gamma: Optional[float] = None,
    db=Depends(get_db),
):
    """Read-only dry run: returns the exact orders + validation that 'apply' would
    execute, without touching the paper account."""
    plan = _compute_rebalance_plan(db, preset=preset, custom_knobs=_knobs_from(theta, k, gamma))
    if plan.get("error"):
        raise HTTPException(status_code=422, detail=plan["error"])
    plan["dry_run"] = True
    return plan


@app.post("/api/crash/apply")
def apply_crash_rebalancing(req: ApplyRebalancingRequest, db=Depends(get_db)):
    """
    Recomputes the validated rebalancing plan and executes its buy/sell orders in
    the paper/virtual account (id=1). Never places live trades.
    """
    if not req.confirm_execution:
        raise HTTPException(status_code=400, detail="Execution must be explicitly confirmed.")

    from app.database import VirtualAccount, VirtualPosition, VirtualOrder, RecentPrice

    plan = _compute_rebalance_plan(
        db, preset=req.preset or "balanced", custom_knobs=_knobs_from(req.theta, req.k, req.gamma)
    )
    if plan.get("error"):
        raise HTTPException(status_code=422, detail=plan["error"])
    if not plan["validation"]["ok"]:
        raise HTTPException(status_code=422, detail="; ".join(plan["validation"]["errors"]))

    # Ensure the paper account exists
    account = db.query(VirtualAccount).filter(VirtualAccount.id == 1).first()
    if not account:
        account = VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
        db.add(account)
        db.commit()

    def get_latest_price(symbol):
        return _latest_paper_price(db, symbol)

    orders_to_submit = plan["orders"]
    sell_orders = [o for o in orders_to_submit if o["side"] == "sell"]
    buy_orders = [o for o in orders_to_submit if o["side"] == "buy"]

    executed = []

    # Execute Sells
    for o in sell_orders:
        ticker = o["ticker"]
        qty = o["qty"]
        price = o["price"]

        pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker, VirtualPosition.mode == "virtual").first()
        if pos:
            qty_sold = min(pos.quantity, qty)
            revenue = qty_sold * price
            account.cash += revenue
            account.buying_power = account.cash

            pos.quantity -= qty_sold
            if pos.quantity <= 0.0001:
                db.delete(pos)

            order_id = _uuid.uuid4().hex[:8]
            v_order = VirtualOrder(
                id=order_id,
                mode="virtual",
                ticker=ticker,
                qty=qty_sold,
                side="sell",
                type="market",
                status="filled",
                filled_price=price,
                created_at=datetime.now().isoformat()
            )
            db.add(v_order)
            executed.append({"symbol": ticker, "side": "sell", "qty": qty_sold, "type": "market"})

    # Execute Buys
    for o in buy_orders:
        ticker = o["ticker"]
        qty = o["qty"]
        price = o["price"]

        cost = qty * price
        if cost > account.cash:
            cost = account.cash
            qty = cost / price

        if qty > 0.0001:
            account.cash -= cost
            account.buying_power = account.cash

            pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker, VirtualPosition.mode == "virtual").first()
            if pos:
                new_qty = pos.quantity + qty
                pos.entry_price = ((pos.quantity * pos.entry_price) + cost) / new_qty
                pos.quantity = new_qty
            else:
                pos = VirtualPosition(
                    ticker=ticker,
                    mode="virtual",
                    quantity=qty,
                    entry_price=price,
                    policy="rebalance"
                )
                db.add(pos)

            order_id = _uuid.uuid4().hex[:8]
            v_order = VirtualOrder(
                id=order_id,
                mode="virtual",
                ticker=ticker,
                qty=qty,
                side="buy",
                type="market",
                status="filled",
                filled_price=price,
                created_at=datetime.now().isoformat()
            )
            db.add(v_order)
            executed.append({"symbol": ticker, "side": "buy", "qty": qty, "type": "market"})

    final_positions = db.query(VirtualPosition).filter(VirtualPosition.mode == "virtual").all()
    final_equity = float(account.cash)
    for pos in final_positions:
        final_equity += pos.quantity * get_latest_price(pos.ticker)
    account.equity = final_equity

    db.commit()

    return {
        "status": "executed",
        "posture_applied": req.target_posture,
        "preset_applied": plan["preset_applied"],
        "de_risk_coefficient": plan["de_risk_coefficient"],
        "orders_submitted": executed,
        "cash_transferred_to_reserve": float(account.cash),
        "final_equity": float(account.equity),
    }
