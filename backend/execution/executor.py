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
from app.database import (
    SessionLocal, init_db, ExecutedTrade,
    RecentPrice, VirtualAccount, VirtualPosition, VirtualOrder, BrokerPerformanceLog
)
from ml_engine.models import PortfolioOptimizer
import numpy as np

def get_alpaca_api(force_virtual=False):
    """Initializes and returns the Alpaca REST API object, defaulting to virtual broker if keys are missing."""
    if force_virtual or not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        base_url = "http://localhost:8008/api/virtual_alpaca"
        key_id = "VIRTUAL"
        secret_key = "VIRTUAL"
    else:
        base_url = ALPACA_BASE_URL or "https://paper-api.alpaca.markets"
        key_id = ALPACA_API_KEY
        secret_key = ALPACA_SECRET_KEY

    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            key_id=key_id,
            secret_key=secret_key,
            base_url=base_url,
            api_version='v2'
        )
        return api
    except Exception as e:
        print(f"Warning: Failed to connect to Alpaca API at {base_url}: {e}")
        if "localhost" in base_url or "127.0.0.1" in base_url:
            import alpaca_trade_api as tradeapi
            return tradeapi.REST(key_id=key_id, secret_key=secret_key, base_url=base_url, api_version='v2')
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

    # Load user holdings policies from db
    positions_db = {p.ticker: p for p in db.query(VirtualPosition).all()}

    # Process liquidations first
    for ticker, pos_info in positions_db.items():
        if pos_info.policy == "liquidate" and ticker in active_tickers:
            print(f"Holding policy for {ticker} is 'liquidate'. Closing position...")
            try:
                api.close_position(ticker)
                pos_info.quantity = 0.0
                db.commit()
            except Exception as e:
                print(f"Failed to close position for liquidated asset {ticker}: {e}")

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
            # Check locked policy
            if ticker in positions_db and positions_db[ticker].policy == "lock":
                print(f"Ticker {ticker} is locked. Skipping BUY execution.")
                continue

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
            # Check locked policy
            if ticker in positions_db and positions_db[ticker].policy == "lock":
                print(f"Ticker {ticker} is locked. Skipping SELL execution.")
                continue

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

    from app.main import get_daily_suggestions

    try:
        print("Retrieving daily trade recommendations...")
        suggestions_data = get_daily_suggestions(None, db)

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

def evaluate_virtual_broker_daily(db, sim_date=None, mode="live"):
    """
    Performs daily post-market stops evaluation, updates account equity,
    compares performance against indices, and logs snapshots.
    """
    if not sim_date:
        sim_date = datetime.now().strftime("%Y-%m-%d")

    print(f"--- Evaluating Virtual Broker for date: {sim_date} (mode: {mode}) ---")

    # 1. Load Virtual Account
    account = db.query(VirtualAccount).filter(VirtualAccount.id == 1).first()
    if not account:
        account = VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
        db.add(account)
        db.commit()

    # 2. Stop/Take Profit evaluation for active positions
    positions = db.query(VirtualPosition).filter(VirtualPosition.quantity > 0).all()

    for pos in positions:
        ticker = pos.ticker
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if not price_rec:
            continue

        order = db.query(VirtualOrder).filter(
            VirtualOrder.ticker == ticker,
            VirtualOrder.status == "filled",
            VirtualOrder.side == "buy",
            (VirtualOrder.stop_loss.isnot(None) | VirtualOrder.take_profit.isnot(None))
        ).order_by(VirtualOrder.created_at.desc()).first()

        if order:
            triggered = False
            fill_price = 0.0
            trigger_side = None

            if order.stop_loss and price_rec.low <= order.stop_loss:
                triggered = True
                fill_price = order.stop_loss
                trigger_side = "stop_loss"
            elif order.take_profit and price_rec.high >= order.take_profit:
                triggered = True
                fill_price = order.take_profit
                trigger_side = "take_profit"

            if triggered:
                qty = pos.quantity
                revenue = qty * fill_price
                account.cash += revenue
                account.buying_power = account.cash

                close_order_id = f"trigger-{datetime.now().timestamp()}-{np.random.randint(1000, 9999)}"
                close_order = VirtualOrder(
                    id=close_order_id,
                    ticker=ticker,
                    qty=qty,
                    side="sell",
                    type="market",
                    status="filled",
                    filled_price=fill_price,
                    created_at=datetime.now().isoformat(),
                    sim_date=sim_date
                )
                db.add(close_order)
                db.delete(pos)

                print(f"Triggered {trigger_side} for {ticker} at price ${fill_price:.2f}. Sold {qty:.2f} shares.")
                db.commit()

    # 3. Calculate portfolio value at close of sim_date
    positions = db.query(VirtualPosition).filter(VirtualPosition.quantity > 0).all()
    portfolio_value = account.cash

    for pos in positions:
        ticker = pos.ticker
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if not price_rec:
            price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date <= sim_date).order_by(RecentPrice.date.desc()).first()

        close_price = price_rec.close if price_rec else pos.entry_price
        portfolio_value += pos.quantity * close_price

    account.equity = portfolio_value
    db.commit()

    # 4. Benchmark index values
    def get_bench_close(symbol):
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == symbol, RecentPrice.date == sim_date).first()
        if not price_rec:
            price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == symbol, RecentPrice.date <= sim_date).order_by(RecentPrice.date.desc()).first()
        return price_rec.close if price_rec else 100.0

    spy_close = get_bench_close("SPY")
    qqq_close = get_bench_close("QQQ")
    brk_close = get_bench_close("BRK-B")

    perf_log = db.query(BrokerPerformanceLog).filter(
        BrokerPerformanceLog.date == sim_date,
        BrokerPerformanceLog.mode == mode
    ).first()

    if perf_log:
        perf_log.portfolio_value = portfolio_value
        perf_log.spy_value = spy_close
        perf_log.qqq_value = qqq_close
        perf_log.brk_value = brk_close
    else:
        perf_log = BrokerPerformanceLog(
            date=sim_date,
            mode=mode,
            portfolio_value=portfolio_value,
            spy_value=spy_close,
            qqq_value=qqq_close,
            brk_value=brk_close
        )
        db.add(perf_log)

    db.commit()
    print(f"Log written for {sim_date}: Equity: ${portfolio_value:.2f} | SPY: ${spy_close:.2f} | QQQ: ${qqq_close:.2f} | BRK-B: ${brk_close:.2f}")

if __name__ == "__main__":
    run_execution()
