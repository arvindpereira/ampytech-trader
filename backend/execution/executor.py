import sys
import os
from datetime import datetime, timedelta
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
    SessionLocal, init_db, ExecutedTrade, RecentPrice,
    VirtualAccount, VirtualPosition, VirtualOrder, BrokerPerformanceLog,
    UniverseTicker
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

    # Run long-term grid/tranche rebalancing
    from app.main import get_sim_date
    sim_date = get_sim_date()
    if not sim_date:
        sim_date = datetime.now().strftime("%Y-%m-%d")
    execute_long_term_grid_trades(db, api, suggestions_data, sim_date)

def get_long_term_available_shares(db, ticker, current_date_str):
    """
    Finds shares that have been held for more than 1 year (365 days)
    using FIFO matching on VirtualOrder execution logs. Manual initial holdings
    default to long-term.
    """
    current_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()

    pos = db.query(VirtualPosition).filter(VirtualPosition.ticker == ticker).first()
    if not pos or pos.quantity <= 0:
        return 0.0
    total_owned = pos.quantity

    buys = db.query(VirtualOrder).filter(
        VirtualOrder.ticker == ticker,
        VirtualOrder.side == "buy",
        VirtualOrder.status == "filled"
    ).all()

    def get_order_date(o):
        if o.sim_date:
            return datetime.strptime(o.sim_date, "%Y-%m-%d").date()
        if "T" in o.created_at:
            return datetime.strptime(o.created_at.split("T")[0], "%Y-%m-%d").date()
        return datetime.strptime(o.created_at, "%Y-%m-%d").date()

    sorted_buys = sorted(buys, key=get_order_date)
    total_buy_orders_qty = sum(b.qty for b in sorted_buys)

    lots = []
    # Manual holdings (difference between owned and virtually purchased) are assumed long-term
    if total_owned > total_buy_orders_qty:
        manual_qty = total_owned - total_buy_orders_qty
        lots.append({
            "qty": manual_qty,
            "date": current_date - timedelta(days=500)
        })

    for b in sorted_buys:
        lots.append({
            "qty": b.qty,
            "date": get_order_date(b)
        })

    sells = db.query(VirtualOrder).filter(
        VirtualOrder.ticker == ticker,
        VirtualOrder.side == "sell",
        VirtualOrder.status == "filled"
    ).all()

    sorted_sells = sorted(sells, key=get_order_date)
    total_sell_qty = sum(s.qty for s in sorted_sells)

    remaining_sell = total_sell_qty
    for lot in lots:
        if remaining_sell <= 0:
            break
        if lot["qty"] <= remaining_sell:
            remaining_sell -= lot["qty"]
            lot["qty"] = 0.0
        else:
            lot["qty"] -= remaining_sell
            remaining_sell = 0.0

    long_term_qty = 0.0
    for lot in lots:
        if lot["qty"] > 0:
            days_held = (current_date - lot["date"]).days
            if days_held >= 365:
                long_term_qty += lot["qty"]

    return min(long_term_qty, total_owned)

def execute_long_term_grid_trades(db, api, suggestions_data, sim_date):
    """
    Executes long-term grid/tranche-based buying and tax-optimized FIFO selling
    for assets with policy == 'rebalance'.
    """
    print("--- Running Long-Term Grid/Tranche Rebalancing ---")

    allocations = suggestions_data.get("long_term_allocation", [])
    target_weights = {a["ticker"]: a["weight"] for a in allocations}

    try:
        account = api.get_account()
        portfolio_equity = float(account.equity)
        buying_power = float(account.buying_power)
    except Exception as e:
        print(f"Failed to get account for long-term rebalance: {e}")
        return

    positions_db = {p.ticker: p for p in db.query(VirtualPosition).all()}

    # Load stock universe dynamically from DB (honoring user edits)
    db_tickers = db.query(UniverseTicker).all()
    active_universe = [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE

    for ticker in active_universe:
        if ticker == "CASH":
            continue

        target_weight = target_weights.get(ticker, 0.0)
        pos = positions_db.get(ticker)
        current_shares = pos.quantity if pos else 0.0
        entry_price = pos.entry_price if pos else 0.0
        policy = pos.policy if pos else "rebalance"

        if policy != "rebalance":
            continue

        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if not price_rec:
            price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date <= sim_date).order_by(RecentPrice.date.desc()).first()

        if not price_rec:
            continue

        current_price = price_rec.close
        target_shares = (portfolio_equity * target_weight) / current_price
        diff_shares = target_shares - current_shares

        price_dev = 0.0
        if entry_price > 0.0:
            price_dev = (current_price - entry_price) / entry_price

        tranche_cap_equity = portfolio_equity * 0.02
        tranche_cap_shares = tranche_cap_equity / current_price

        # Grid Buy: underweight AND (new position OR price fell at least 3% below entry price)
        if diff_shares > 0.01:
            should_buy = (current_shares == 0.0) or (price_dev <= -0.03)

            if should_buy:
                n_shares = min(diff_shares, tranche_cap_shares)
                if n_shares < 0.01:
                    continue

                cost = n_shares * current_price
                if cost > buying_power:
                    n_shares = buying_power / current_price

                if n_shares >= 0.01:
                    print(f"Long-Term Grid BUY: Symbol: {ticker}. Current: ${current_price:.2f}, Cost Basis: ${entry_price:.2f} ({price_dev*100:+.1f}%). Placing cautious tranche of {n_shares:.2f} shares.")
                    try:
                        api.submit_order(
                            symbol=ticker,
                            qty=n_shares,
                            side='buy',
                            type='market',
                            time_in_force='gtc'
                        )
                        buying_power -= (n_shares * current_price)
                    except Exception as e:
                        print(f"Failed to submit long-term buy order for {ticker}: {e}")

        # Grid Sell: overweight AND price increased at least 5% above cost basis
        elif diff_shares < -0.01 and current_shares > 0.0:
            if price_dev >= 0.05:
                m_shares = min(abs(diff_shares), tranche_cap_shares)

                # Verify age using FIFO holding period checker (must be held > 365 days)
                tax_eligible_shares = get_long_term_available_shares(db, ticker, sim_date)
                m_shares = min(m_shares, tax_eligible_shares)

                if m_shares >= 0.01:
                    print(f"Long-Term Grid SELL (Tax-Optimized FIFO): Symbol: {ticker}. Current: ${current_price:.2f}, Cost Basis: ${entry_price:.2f} ({price_dev*100:+.1f}%). Selling {m_shares:.2f} shares.")
                    try:
                        api.submit_order(
                            symbol=ticker,
                            qty=m_shares,
                            side='sell',
                            type='market',
                            time_in_force='gtc'
                        )
                    except Exception as e:
                        print(f"Failed to submit long-term sell order for {ticker}: {e}")
                else:
                    if abs(diff_shares) >= 0.01:
                        print(f"Long-Term Grid SELL skipped for {ticker}: Price is up {price_dev*100:+.1f}%, but no shares qualify as long-term (> 365 days held). Tax-eligible: {tax_eligible_shares:.2f} shares.")

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
