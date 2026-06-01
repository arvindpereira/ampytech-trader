import sys
import os
from datetime import datetime
import pandas as pd
import requests

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    ALPACA_BASE_URL,
    TICKER_UNIVERSE
)
from app.database import SessionLocal, init_db, ExecutedTrade
from ml_engine.models import PortfolioOptimizer

def get_alpaca_api():
    """Initializes and returns the Alpaca REST API object, or None if keys are missing."""
    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        return None
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            key_id=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            base_url=ALPACA_BASE_URL,
            api_version='v2'
        )
        # Test connection
        api.get_account()
        return api
    except Exception as e:
        print(f"Warning: Failed to connect to Alpaca API: {e}")
        return None

def execute_local_paper_trade(db, suggestions_data):
    """Executes trades in mock paper-trading mode, logging them in SQLite database."""
    print("--- Executing Local Paper Trade (Simulated Fills) ---")
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Simple simulated cash tracking
    # Load past trades to calculate total current simulated portfolio cash
    all_trades = db.query(ExecutedTrade).all()
    sim_cash = 100000.0  # Starting cash
    holdings = {}       # Ticker -> Qty
    
    for t in all_trades:
        cost = t.price * t.shares
        if t.action == "BUY":
            sim_cash -= cost
            holdings[t.ticker] = holdings.get(t.ticker, 0.0) + t.shares
        elif t.action == "SELL":
            sim_cash += cost
            holdings[t.ticker] = holdings.get(t.ticker, 0.0) - t.shares
            
    # Process suggestions
    short_term_sugs = suggestions_data.get("short_term_suggestions", [])
    
    for sug in short_term_sugs:
        ticker = sug["ticker"]
        action = sug["action"]
        close_price = sug["close"]
        confidence = sug["confidence"]
        
        # Max position limit (10% of portfolio value per trade)
        portfolio_equity = sim_cash + sum(holdings.get(t, 0.0) * close_price for t in holdings)
        max_trade_value = portfolio_equity * 0.1
        
        if action == "BUY":
            # Check if we already own this ticker
            if holdings.get(ticker, 0.0) > 0.0:
                continue
                
            # Sizing via Fractional Kelly (Half-Kelly)
            # Payoff ratio b is mocked as 2.5 (our target R:R ratio)
            payoff_ratio = 2.5
            k_percent = PortfolioOptimizer.calculate_fractional_kelly(confidence, payoff_ratio, fraction=0.2) # 20% Fractional Kelly
            
            # Sizing value is min of Kelly allocation or maximum cash limit
            trade_value = min(max_trade_value, portfolio_equity * k_percent)
            
            if trade_value > sim_cash or trade_value < 100.0:
                print(f"Skipping BUY {ticker} due to insufficient simulated cash (${sim_cash:.2f}).")
                continue
                
            shares = trade_value / close_price
            
            trade = ExecutedTrade(
                ticker=ticker,
                date=date_str,
                action="BUY",
                price=close_price,
                shares=shares,
                value=trade_value,
                status="simulated"
            )
            db.add(trade)
            sim_cash -= trade_value
            holdings[ticker] = holdings.get(ticker, 0.0) + shares
            print(f"Simulated BUY: {shares:.2f} shares of {ticker} at ${close_price:.2f} (Value: ${trade_value:.2f})")
            
        elif action == "SELL":
            # Check if we currently own shares of this ticker to sell
            qty_owned = holdings.get(ticker, 0.0)
            if qty_owned > 0.0:
                trade_value = qty_owned * close_price
                trade = ExecutedTrade(
                    ticker=ticker,
                    date=date_str,
                    action="SELL",
                    price=close_price,
                    shares=qty_owned,
                    value=trade_value,
                    status="simulated"
                )
                db.add(trade)
                sim_cash += trade_value
                holdings[ticker] = 0.0
                print(f"Simulated SELL: {qty_owned:.2f} shares of {ticker} at ${close_price:.2f} (Value: ${trade_value:.2f})")
                
    db.commit()
    print(f"Simulated executions completed. Current cash: ${sim_cash:.2f}, Portfolio equity: ${portfolio_equity:.2f}\n")

def execute_alpaca_live_paper_trade(api, db, suggestions_data):
    """Executes trades via the live/paper Alpaca REST API."""
    print("--- Executing Live Paper Trades via Alpaca API ---")
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Retrieve account details
    account = api.get_account()
    portfolio_equity = float(account.equity)
    buying_power = float(account.buying_power)
    
    print(f"Alpaca Account Equity: ${portfolio_equity:.2f} | Buying Power: ${buying_power:.2f}")
    
    # Fetch open positions to avoid redundant buys
    open_positions = api.list_positions()
    active_tickers = [p.symbol for p in open_positions]
    
    short_term_sugs = suggestions_data.get("short_term_suggestions", [])
    
    for sug in short_term_sugs:
        ticker = sug["ticker"]
        action = sug["action"]
        close_price = sug["close"]
        confidence = sug["confidence"]
        stop_loss = sug["stop_loss"]
        take_profit = sug["take_profit"]
        
        # Max position limit (10% of portfolio value per trade)
        max_trade_value = portfolio_equity * 0.1
        
        if action == "BUY":
            if ticker in active_tickers:
                print(f"Already holding position in {ticker}. Skipping buy.")
                continue
                
            # Sizing via Fractional Kelly (Half-Kelly)
            payoff_ratio = 2.5
            k_percent = PortfolioOptimizer.calculate_fractional_kelly(confidence, payoff_ratio, fraction=0.2)
            
            trade_value = min(max_trade_value, portfolio_equity * k_percent)
            
            if trade_value > buying_power or trade_value < 100.0:
                print(f"Skipping buy {ticker} due to insufficient buying power.")
                continue
                
            shares = int(trade_value / close_price) # Keep to whole shares for paper simplicity
            if shares < 1:
                continue
                
            print(f"Submitting bracket market order for {ticker}: {shares} shares (Stop: {stop_loss:.2f}, Profit: {take_profit:.2f})...")
            
            try:
                # Place order with stop-loss and take-profit brackets
                order = api.submit_order(
                    symbol=ticker,
                    qty=shares,
                    side='buy',
                    type='market',
                    time_in_force='gtc',
                    order_class='bracket',
                    take_profit=dict(limit_price=round(take_profit, 2)),
                    stop_loss=dict(stop_price=round(stop_loss, 2))
                )
                
                # Log in SQLite
                trade = ExecutedTrade(
                    ticker=ticker,
                    date=date_str,
                    action="BUY",
                    price=close_price,
                    shares=float(shares),
                    value=float(shares * close_price),
                    status="filled"
                )
                db.add(trade)
                db.commit()
                print(f"Order filled successfully. Logged order ID: {order.id}")
            except Exception as e:
                print(f"Failed to submit bracket order for {ticker}: {e}")
                db.rollback()
                
        elif action == "SELL":
            if ticker in active_tickers:
                # Close the open position
                print(f"Closing position in {ticker}...")
                try:
                    # Retrieve exact position shares
                    pos = [p for p in open_positions if p.symbol == ticker][0]
                    shares_to_sell = pos.qty
                    
                    api.close_position(ticker)
                    
                    trade = ExecutedTrade(
                        ticker=ticker,
                        date=date_str,
                        action="SELL",
                        price=close_price,
                        shares=float(shares_to_sell),
                        value=float(float(shares_to_sell) * close_price),
                        status="filled"
                    )
                    db.add(trade)
                    db.commit()
                    print(f"Position closed successfully.")
                except Exception as e:
                    print(f"Failed to close position in {ticker}: {e}")
                    db.rollback()
                    
    print("Alpaca order executions complete.\n")

def run_execution():
    init_db()
    db = SessionLocal()
    
    # To run execution, we need today's suggestions.
    # We can fetch the suggestions by calling our internal FastAPI code logic
    # directly using database session (which avoids needing to have the server running).
    # We load app.api endpoint logics here
    from app.main import get_daily_suggestions
    
    try:
        print("Retrieving daily trade recommendations...")
        suggestions_data = get_daily_suggestions(db)
        
        # Connect to Alpaca
        api = get_alpaca_api()
        
        if api:
            execute_alpaca_live_paper_trade(api, db, suggestions_data)
        else:
            execute_local_paper_trade(db, suggestions_data)
            
    except Exception as e:
        print(f"Execution run failed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_execution()
