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

from app.core.config import TICKER_UNIVERSE
from app.database import SessionLocal, RecentPrice, CrisisPrice, MacroIndicator
from ml_engine.features import build_features_for_df
from ml_engine.models import PortfolioOptimizer

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
            "high": p.high, "low": p.low, "close": p.close, "volume": p.volume
        } for p in prices])
        
    macro = db.query(MacroIndicator).all()
    macro_df = pd.DataFrame([{
        "date": m.date, "indicator_name": m.indicator_name, "value": m.value
    } for m in macro]) if macro else pd.DataFrame()
    
    db.close()
    return prices_df, macro_df

def run_short_term_backtest(prices_df, macro_df, model_path):
    """Simulates the short-term strategy on historical prices using PyBroker."""
    print("Pre-calculating features and model predictions for short-term backtest...")
    
    # Load XGBoost model
    if not os.path.exists(model_path):
        print(f"XGBoost model file missing at {model_path}. Train the model first.")
        return
        
    model = xgb.XGBClassifier()
    model.load_model(model_path)
    
    # Process features and run inference
    all_tickers_data = []
    feature_cols = None
    
    for ticker in prices_df['ticker'].unique():
        ticker_prices = prices_df[prices_df['ticker'] == ticker].sort_values('date').copy()
        if len(ticker_prices) < 50:
            continue
            
        t_feat = build_features_for_df(ticker_prices, sentiment_df=None, macro_df=macro_df)
        
        # Features list
        if feature_cols is None:
            feature_cols = [col for col in t_feat.columns if col.startswith("feat_")]
            
        # Predict probability
        t_feat['pred_prob'] = model.predict_proba(t_feat[feature_cols])[:, 1]
        all_tickers_data.append(t_feat)
        
    if not all_tickers_data:
        print("Error: Insufficient history to generate backtesting features.")
        return
        
    backtest_df = pd.concat(all_tickers_data, ignore_index=True)
    
    # Setup PyBroker Strategy
    # PyBroker requires columns: [date, symbol, open, high, low, close, volume]
    pyb_df = backtest_df[[
        'date', 'ticker', 'open', 'high', 'low', 'close', 'volume', 'pred_prob', 'feat_atr_14'
    ]].copy()
    pyb_df = pyb_df.rename(columns={'ticker': 'symbol'})
    pyb_df['date'] = pd.to_datetime(pyb_df['date'])
    
    # Register data in PyBroker
    pybroker_data = pyb_df
    
    # Sizing and Trading execution logic
    def short_term_exec(ctx: ExecContext):
        # Retrieve prediction probability for the current bar
        prob = ctx.indicator('pred_prob')[-1]
        atr = ctx.indicator('feat_atr_14')[-1]
        
        # Entry rule: Probability of breakout >= 55%
        if prob >= 0.55 and not ctx.long_pos():
            # Sizing calculation based on risk: 10% maximum cash allocation
            # Put Stop loss at 2.0x ATR below close
            stop_loss_pct = min(0.05, max(0.015, (2.0 * atr) / ctx.close[-1]))
            take_profit_pct = stop_loss_pct * 2.5 # 2.5 Risk-to-reward ratio
            
            # Place buy order
            ctx.buy_shares = ctx.calc_target_shares(0.1)
            # Register stop-loss and take-profit (stop_profit) in execution context
            close_price = ctx.close[-1]
            ctx.stop_loss = stop_loss_pct * close_price
            ctx.stop_profit = take_profit_pct * close_price
            
        else:
            # Exit rule: If we hold a position and it has been open for 3 bars (3 days), close it
            # (This acts as a time-stop fallback if take-profit or stop-loss is not hit)
            pos = ctx.long_pos()
            if pos and pos.bars >= 3:
                ctx.sell_all_shares()

    # Register columns first
    pybroker.register_columns(['pred_prob', 'feat_atr_14'])
    
    # Register Indicators
    pred_prob_ind = pybroker.indicator('pred_prob', lambda data: data.pred_prob)
    atr_ind = pybroker.indicator('feat_atr_14', lambda data: data.feat_atr_14)
    
    # Configure and run PyBroker Backtest
    config = StrategyConfig(initial_cash=100000, fee_mode=pybroker.FeeMode.ORDER_PERCENT, fee_amount=0.05) # 0.05% commissions/slippage fee
    strategy = Strategy(pybroker_data, start_date=pyb_df['date'].min(), end_date=pyb_df['date'].max(), config=config)
    strategy.add_execution(short_term_exec, TICKER_UNIVERSE, indicators=[pred_prob_ind, atr_ind])
    
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
    X_hmm = spy_features[hmm_feature_cols].values
    states = hmm_model.predict(X_hmm)
    regimes = [state_mapping[s] for s in states]
    spy_features['regime'] = regimes
    
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
    returns_df['month_year'] = pd.to_datetime(returns_df['date']).dt.to_period('M')
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
        
        # Run short term backtest
        run_short_term_backtest(prices_df, macro_df, model_path)
        
        # Run long term portfolio rebalance simulation
        run_long_term_backtest(prices_df, macro_df, hmm_path, metadata_path)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Backtesting execution failed: {e}")

if __name__ == "__main__":
    main()
