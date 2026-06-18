import os
import sys
import pickle
import pandas as pd
import numpy as np
import xgboost as xgb
import pybroker
from pybroker import Strategy, ExecContext, StrategyConfig

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    TICKER_UNIVERSE, SHORT_TERM_HORIZON_BARS, SHORT_TERM_ATR_STOP_MULT,
    SHORT_TERM_TP_MULT, SHORT_TERM_STOP_MIN, SHORT_TERM_STOP_MAX, SHORT_TERM_BUY_THRESHOLD,
    HEDGE_MODE, SERVED_MODEL,
)
from app.database import SessionLocal, RecentPrice, DailyPrice, CrisisPrice, MacroIndicator
from ml_engine.features import build_features_for_df
from ml_engine.models import PortfolioOptimizer, load_buy_threshold

# Shared dictionary to store the target hedge allocations and active long/hedge mappings
active_hedges = {}       # hedge_symbol -> target short allocation (float)
current_hedges = {}      # symbol -> (hedge_symbol, hedge_value)
active_longs = set()     # set of symbols currently long

def get_hedge_info(ctx, hedge_mode):
    """Backtest adapter around the shared hedging logic (reads rolling stats from the ExecContext)."""
    from execution.hedging import compute_hedge
    try:
        corr_spy = ctx.indicator('feat_corr_spy_20')[-1]
        corr_qqq = ctx.indicator('feat_corr_qqq_20')[-1]
        rel_vol_spy = ctx.indicator('feat_relative_vol_spy')[-1]
        rel_vol_qqq = ctx.indicator('feat_relative_vol_qqq')[-1]
    except Exception:
        corr_spy, corr_qqq, rel_vol_spy, rel_vol_qqq = 0.8, 0.8, 1.0, 1.0
    return compute_hedge(ctx.symbol, hedge_mode, corr_spy, corr_qqq,
                         rel_vol_spy, rel_vol_qqq, TICKER_UNIVERSE)

# Set pybroker config to use local cache
pybroker.enable_caches('ampytech_trader', 'pybroker_cache')

# Saved models directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MODELS_DIR = os.path.join(BASE_DIR, "ml_engine", "saved_models")

def load_backtest_data(era=None):
    """Loads data from either recent_prices or crisis_prices depending on the target era."""
    db = SessionLocal()

    if era:
        print(f"Loading database records for historical crisis era: {era.upper()}...")
        prices = db.query(CrisisPrice).filter(CrisisPrice.era == era).all()
        if not prices:
            db.close()
            raise ValueError(f"No historical crisis data found for era {era}. Run ingestion first.")
        prices_df = pd.DataFrame([{
            "ticker": p.ticker, "date": p.date, "open": p.open,
            "high": p.high, "low": p.low, "close": p.close, "volume": p.volume
        } for p in prices])
    else:
        print("Loading database records for recent 2-year window...")
        prices = db.query(RecentPrice).all()
        if not prices:
            db.close()
            raise ValueError("No recent price data found. Run ingestion first.")
        prices_df = pd.DataFrame([{
            "ticker": p.ticker, "date": p.date, "open": p.open,
            "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
            "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
            "macd": p.macd, "macd_signal": p.macd_signal
        } for p in prices])

    macro = db.query(MacroIndicator).all()
    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()

    db.close()
    return prices_df, macro_df


def load_daily_prices_df():
    """Loads the full DAILY history (daily_prices) for the long-term regime/MPT backtest."""
    db = SessionLocal()
    prices = db.query(DailyPrice).all()
    db.close()
    if not prices:
        return pd.DataFrame()
    return pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open, "high": p.high,
        "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal,
    } for p in prices])

def run_short_term_backtest(prices_df, macro_df, model_path):
    """Simulates the short-term strategy on historical prices using PyBroker."""
    print("Pre-calculating features and model predictions for short-term backtest...")

    # Check for PyTorch sequence model
    deep_model_path = os.path.join(SAVED_MODELS_DIR, "temporal_attention_model.pth")
    deep_metadata_path = os.path.join(SAVED_MODELS_DIR, "temporal_attention_metadata.pkl")

    use_pytorch = False
    deep_model = None
    scaler_metadata = None
    buy_threshold = load_buy_threshold()  # calibrated per served model (falls back to config)

    if SERVED_MODEL == "pytorch" and os.path.exists(deep_model_path) and os.path.exists(deep_metadata_path):
        import torch
        from ml_engine.deep_models import LightTemporalAttentionNet, prepare_sequences
        try:
            print("Loading PyTorch Temporal Attention Model...")
            with open(deep_metadata_path, "rb") as f:
                scaler_metadata = pickle.load(f)
            input_dim = len(scaler_metadata["feature_cols"])
            deep_model = LightTemporalAttentionNet(input_dim=input_dim, hidden_dim=32)
            deep_model.load_state_dict(torch.load(deep_model_path))
            deep_model.eval()
            use_pytorch = True
        except Exception as e:
            print(f"Failed to load PyTorch model, falling back to XGBoost. Error: {e}")

    # Fallback to XGBoost model
    xgb_model = None
    if not use_pytorch:
        if not os.path.exists(model_path):
            print(f"XGBoost model file missing at {model_path} and no PyTorch model found. Train models first.")
            return
        print("Loading XGBoost Classifier...")
        xgb_model = xgb.XGBClassifier()
        xgb_model.load_model(model_path)

    # Process features and run inference
    from ml_engine.features import build_all_features
    tickers_to_process = list(prices_df['ticker'].unique())
    backtest_df = build_all_features(prices_df, None, macro_df, tickers_to_process)

    if backtest_df.empty:
        print("Error: Insufficient history to generate backtesting features.")
        return

    feature_cols = sorted([col for col in backtest_df.columns if col.startswith("feat_") and col != "feat_atr_14"])
    backtest_df['pred_prob'] = 0.0

    # Perform prediction per ticker to avoid cross-ticker sequence bleed
    for ticker in backtest_df['ticker'].unique():
        t_mask = backtest_df['ticker'] == ticker
        t_feat = backtest_df[t_mask].copy()

        if use_pytorch and deep_model is not None and scaler_metadata is not None:
            f_cols = scaler_metadata["feature_cols"]
            t_feat_valid = t_feat.dropna(subset=f_cols).copy()
            if len(t_feat_valid) >= 10:
                from ml_engine.deep_models import prepare_sequences
                import torch
                X_seq, _, _, _ = prepare_sequences(
                    t_feat_valid, f_cols, seq_len=10, fit_scaler=False, scaler_metadata=scaler_metadata
                )
                if len(X_seq) > 0:
                    with torch.no_grad():
                        inputs = torch.tensor(X_seq, dtype=torch.float32)
                        outputs = deep_model(inputs).squeeze(1).numpy()
                    preds = np.zeros(len(t_feat_valid))
                    preds[10 - 1:] = outputs
                    t_feat_valid['pred_prob'] = preds

                    pred_map = dict(zip(t_feat_valid['date'], t_feat_valid['pred_prob']))
                    backtest_df.loc[t_mask, 'pred_prob'] = backtest_df.loc[t_mask, 'date'].map(pred_map).fillna(0.0)
            else:
                backtest_df.loc[t_mask, 'pred_prob'] = 0.0
        else:
            preds = xgb_model.predict_proba(t_feat[feature_cols])[:, 1]
            backtest_df.loc[t_mask, 'pred_prob'] = preds

    # Setup PyBroker Strategy
    # PyBroker requires columns: [date, symbol, open, high, low, close, volume]
    pyb_df = backtest_df[[
        'date', 'ticker', 'open', 'high', 'low', 'close', 'volume', 'pred_prob', 'feat_atr_14',
        'feat_corr_spy_20', 'feat_corr_qqq_20', 'feat_relative_vol_spy', 'feat_relative_vol_qqq'
    ]].copy()
    pyb_df = pyb_df.rename(columns={'ticker': 'symbol'})
    pyb_df['date'] = pd.to_datetime(pyb_df['date'], format='mixed')

    # Register data in PyBroker
    pybroker_data = pyb_df

    # Sizing and Trading execution logic
    def short_term_exec(ctx: ExecContext):
        # 1. Check if a previously active long position was closed (by stop loss or take profit)
        is_long = ctx.long_pos() is not None
        was_long = ctx.symbol in active_longs

        if was_long and not is_long:
            active_longs.remove(ctx.symbol)
            if ctx.symbol in current_hedges:
                hedge_symbol, hedge_val = current_hedges.pop(ctx.symbol)
                active_hedges[hedge_symbol] = max(0.0, active_hedges.get(hedge_symbol, 0.0) - hedge_val)

        # 2. Check if this symbol is a benchmark index
        BENCHMARKS = {"SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP"}
        if ctx.symbol in BENCHMARKS:
            target_hedge = active_hedges.get(ctx.symbol, 0.0)
            if target_hedge > 0.0:
                if not ctx.short_pos():
                    ctx.sell_shares = ctx.calc_target_shares(target_hedge)
            elif ctx.short_pos():
                ctx.cover_all_shares()
            return

        # 3. For standard stock tickers: check if we need to manage short hedge position
        target_hedge = active_hedges.get(ctx.symbol, 0.0)
        if target_hedge > 0.0 and not is_long:
            # Manage short hedge
            if not ctx.short_pos():
                ctx.sell_shares = ctx.calc_target_shares(target_hedge)
        elif target_hedge <= 0.0 and ctx.short_pos():
            ctx.cover_all_shares()

        # 4. Long entry/exit logic
        # Retrieve prediction probability for the current bar
        prob = ctx.indicator('pred_prob')[-1]
        atr = ctx.indicator('feat_atr_14')[-1]

        # Entry rule: model win-probability above the calibrated high-confidence threshold
        # Do not buy if this ticker is currently being used as a short hedge to avoid position collision
        if prob >= buy_threshold and not is_long and target_hedge <= 0.0:
            # Same ATR brackets the triple-barrier label assumes (target ≈ execution).
            stop_loss_pct = min(SHORT_TERM_STOP_MAX, max(SHORT_TERM_STOP_MIN, (SHORT_TERM_ATR_STOP_MULT * atr) / ctx.close[-1]))
            take_profit_pct = stop_loss_pct * SHORT_TERM_TP_MULT

            # Place buy order (10% target allocation)
            ctx.buy_shares = ctx.calc_target_shares(0.1)
            close_price = ctx.close[-1]
            ctx.stop_loss = stop_loss_pct * close_price
            ctx.stop_profit = take_profit_pct * close_price

            # Record active long
            active_longs.add(ctx.symbol)

            # If hedge mode is active, set up short hedge
            if HEDGE_MODE in ('beta_neutral', 'pair_trade'):
                hedge_symbol, beta = get_hedge_info(ctx, HEDGE_MODE)
                if hedge_symbol:
                    hedge_val = 0.1 * beta
                    current_hedges[ctx.symbol] = (hedge_symbol, hedge_val)
                    active_hedges[hedge_symbol] = active_hedges.get(hedge_symbol, 0.0) + hedge_val

        else:
            # Vertical barrier: close after the label horizon if neither bracket has triggered,
            # matching the triple-barrier timeout so backtest exits == labelled outcomes.
            if is_long:
                pos = ctx.long_pos()
                if pos and pos.bars >= SHORT_TERM_HORIZON_BARS:
                    ctx.sell_all_shares()
                    # Clean up long tracking and hedges
                    if ctx.symbol in active_longs:
                        active_longs.remove(ctx.symbol)
                    if ctx.symbol in current_hedges:
                        hedge_symbol, hedge_val = current_hedges.pop(ctx.symbol)
                        active_hedges[hedge_symbol] = max(0.0, active_hedges.get(hedge_symbol, 0.0) - hedge_val)

    # Register columns first
    pybroker.register_columns([
        'pred_prob', 'feat_atr_14',
        'feat_corr_spy_20', 'feat_corr_qqq_20',
        'feat_relative_vol_spy', 'feat_relative_vol_qqq'
    ])

    # Register Indicators
    pred_prob_ind = pybroker.indicator('pred_prob', lambda data: data.pred_prob)
    atr_ind = pybroker.indicator('feat_atr_14', lambda data: data.feat_atr_14)
    corr_spy_ind = pybroker.indicator('feat_corr_spy_20', lambda data: data.feat_corr_spy_20)
    corr_qqq_ind = pybroker.indicator('feat_corr_qqq_20', lambda data: data.feat_corr_qqq_20)
    rel_vol_spy_ind = pybroker.indicator('feat_relative_vol_spy', lambda data: data.feat_relative_vol_spy)
    rel_vol_qqq_ind = pybroker.indicator('feat_relative_vol_qqq', lambda data: data.feat_relative_vol_qqq)

    # Configure and run PyBroker Backtest
    config = StrategyConfig(initial_cash=100000, fee_mode=pybroker.FeeMode.ORDER_PERCENT, fee_amount=0.05) # 0.05% commissions/slippage fee
    strategy = Strategy(pybroker_data, start_date=pyb_df['date'].min(), end_date=pyb_df['date'].max(), config=config)
    strategy.add_execution(short_term_exec, TICKER_UNIVERSE, indicators=[
        pred_prob_ind, atr_ind, corr_spy_ind, corr_qqq_ind, rel_vol_spy_ind, rel_vol_qqq_ind
    ])

    result = strategy.backtest()
    metrics = result.metrics

    # Print metrics
    print("\n--- Short-Term Strategy Performance Metrics ---")
    print(f"Total Return: {metrics.total_return_pct:.2f}%")
    print(f"Annualized Sharpe Ratio: {metrics.sharpe:.2f}")
    print(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}%")
    print(f"Win Rate: {metrics.win_rate:.2f}%")
    print(f"Total Completed Trades: {metrics.trade_count}")
    print("--------------------------------------------------\n")
    return result

def run_long_term_backtest(prices_df, macro_df, hmm_path, metadata_path):
    """Simulates the long-term HMM regime-switching portfolio strategy."""
    print("Setting up long-term regime allocation backtest...")

    # Load HMM model
    if not os.path.exists(hmm_path) or not os.path.exists(metadata_path):
        print("HMM Model files missing. Train the model first.")
        return

    with open(hmm_path, "rb") as f:
        hmm_model = pickle.load(f)
    with open(metadata_path, "rb") as f:
        hmm_metadata = pickle.load(f)

    state_mapping = hmm_metadata["state_mapping"]

    # Reconstruct returns df and run HMM prediction daily
    spy_data = prices_df[prices_df['ticker'] == 'SPY'].sort_values('date').copy()
    if spy_data.empty:
        print("Error: SPY index data is required to determine market regimes.")
        return

    spy_features = build_features_for_df(spy_data, sentiment_df=None, macro_df=macro_df)
    hmm_feature_cols = ["feat_volatility_10", "feat_fed_funds", "feat_yield_spread"]

    # Run regime classification
    # Drop NaNs for HMM prediction to avoid scikit-learn check_array failures on cold-start periods
    spy_features_valid = spy_features.dropna(subset=hmm_feature_cols).copy()
    if not spy_features_valid.empty:
        X_hmm = spy_features_valid[hmm_feature_cols].values
        states = hmm_model.predict(X_hmm)
        regimes = [state_mapping[s] for s in states]
        spy_features_valid['regime'] = regimes

        # Merge back to spy_features
        spy_features = pd.merge(
            spy_features,
            spy_features_valid[['date', 'regime']],
            on='date',
            how='left'
        )
        spy_features['regime'] = spy_features['regime'].fillna('growth')
    else:
        spy_features['regime'] = 'growth'

    # Extract date mapping for regimes
    date_regimes = dict(zip(spy_features['date'], spy_features['regime']))

    # Pivot returns of all assets to calculate covariance
    returns_list = []
    for ticker in prices_df['ticker'].unique():
        t_data = prices_df[prices_df['ticker'] == ticker].sort_values('date').copy()
        t_data['returns'] = t_data['close'].pct_change()
        returns_list.append(t_data[['date', 'returns']].rename(columns={'returns': ticker}))

    # Merge returns
    returns_df = returns_list[0]
    for r in returns_list[1:]:
        returns_df = pd.merge(returns_df, r, on='date', how='outer')
    returns_df = returns_df.sort_values('date').set_index('date')

    # Rebalance weights monthly
    # Loop over date index, rebalance on the first trading day of each month
    portfolio_value = 100000.0
    weights = {}
    current_cash = portfolio_value

    # Group dates by month
    returns_df = returns_df.reset_index()
    returns_df['month_year'] = pd.to_datetime(returns_df['date'], format='mixed').dt.to_period('M')
    rebalance_dates = returns_df.groupby('month_year')['date'].first().values

    # Record tracking data
    performance_log = []

    # Simple manual portfolio simulation loop (often cleaner than PyBroker for complex covariance constraints)
    for idx, row in returns_df.iterrows():
        date_str = row['date']
        regime = date_regimes.get(date_str, "growth")

        # S&P Benchmark daily return
        spy_ret = row['SPY'] if 'SPY' in row and not pd.isna(row['SPY']) else 0.0

        # Calculate daily portfolio return based on active weights
        daily_ret = 0.0
        for ticker, w in weights.items():
            t_ret = row[ticker] if ticker in row and not pd.isna(row[ticker]) else 0.0
            daily_ret += w * t_ret

        portfolio_value = portfolio_value * (1.0 + daily_ret)

        # Rebalance triggers at the start of a month
        if date_str in rebalance_dates:
            # Load returns history up to day T (using 252 trading days sliding window)
            history = returns_df[returns_df['date'] < date_str].tail(252)
            if len(history) > 100:
                # If regime is crisis, shift allocation: 50% cash, optimize the remaining 50% in defensive sectors
                # Else standard optimization
                opt_weights = PortfolioOptimizer.calculate_optimal_weights(history, regime)

                # Settle weights
                if regime == "crisis":
                    weights = {t: w * 0.5 for t, w in opt_weights.items()}
                else:
                    weights = opt_weights

        performance_log.append({
            "date": date_str,
            "portfolio_value": portfolio_value,
            "regime": regime,
            "spy_value": spy_ret
        })

    perf_df = pd.DataFrame(performance_log)
    perf_df['portfolio_return'] = perf_df['portfolio_value'].pct_change()

    # Calculate performance metrics
    total_ret = (portfolio_value / 100000.0) - 1.0
    ann_std = perf_df['portfolio_return'].std() * np.sqrt(252)
    ann_ret = (portfolio_value / 100000.0) ** (252 / len(perf_df)) - 1.0
    sharpe = (ann_ret - 0.04) / (ann_std + 1e-9)

    # Max drawdown
    roll_max = perf_df['portfolio_value'].cummax()
    drawdown = (perf_df['portfolio_value'] - roll_max) / roll_max
    max_dd = drawdown.min()

    print("\n--- Long-Term MPT Rebalancing Portfolio Performance Metrics ---")
    print(f"Total Return: {total_ret * 100:.2f}%")
    print(f"Annualized Sharpe Ratio: {sharpe:.2f}")
    print(f"Max Drawdown: {max_dd * 100:.2f}%")
    print(f"Trading Days: {len(perf_df)}")
    print("-----------------------------------------------------------------\n")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ampytech Backtester and Stress Testing Runner")
    parser.add_argument(
        "--era",
        choices=["dotcom", "gfc", "covid", "recent"],
        default="recent",
        help="Target backtest testing window"
    )

    args = parser.parse_args()

    model_path = os.path.join(SAVED_MODELS_DIR, "short_term_model.json")
    hmm_path = os.path.join(SAVED_MODELS_DIR, "hmm_model.pkl")
    metadata_path = os.path.join(SAVED_MODELS_DIR, "hmm_metadata.pkl")

    # Set era to None if recent is specified to load RecentPrice table
    era = None if args.era == "recent" else args.era

    try:
        prices_df, macro_df = load_backtest_data(era=era)

        # Short-term backtest runs on the loaded series (hourly for 'recent', daily for crisis eras)
        run_short_term_backtest(prices_df, macro_df, model_path)

        # Long-term backtest always uses DAILY data: full daily history for 'recent',
        # or the crisis-era daily series.
        if era is None:
            daily_df = load_daily_prices_df()
            if daily_df.empty:
                print("No daily_prices found; run 'python run.py fetch' to populate daily history.")
            else:
                run_long_term_backtest(daily_df, macro_df, hmm_path, metadata_path)
        else:
            run_long_term_backtest(prices_df, macro_df, hmm_path, metadata_path)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Backtesting execution failed: {e}")

if __name__ == "__main__":
    main()
