import sys
import os
import time
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

def check_alpaca_authentication(api):
    """
    Checks if Alpaca API connection is valid by making a test get_account call.
    Raises Exception if connection fails or credentials are invalid.
    """
    try:
        api.get_account()
        return True
    except Exception as e:
        print(f"CRITICAL ERROR: Alpaca API Authentication failed: {e}")
        raise e

def sync_broker_orders(db, api):
    """
    Queries SQLite database for pending/submitted orders, checks status on Alpaca,
    and resolves the statuses accordingly.
    """
    if not api:
        return

    # Fetch orders that are not in a final state
    pending_orders = db.query(VirtualOrder).filter(
        VirtualOrder.status.in_(["pending", "submitted", "accepted", "partially_filled"]),
        VirtualOrder.mode == "real"
    ).all()

    if not pending_orders:
        return

    print(f"Syncing status for {len(pending_orders)} pending/submitted orders from broker...")

    for order in pending_orders:
        try:
            # Query actual broker state
            broker_order = api.get_order(order.id)
            status = broker_order.status.lower()

            # Map statuses
            if status == "filled":
                order.status = "filled"
                order.filled_price = float(broker_order.filled_avg_price) if broker_order.filled_avg_price else order.filled_price

                # Update database VirtualPosition directly for the fill
                pos = db.query(VirtualPosition).filter(
                    VirtualPosition.ticker == order.ticker,
                    VirtualPosition.mode == "real"
                ).first()
                qty = float(order.qty)
                price = float(order.filled_price) if order.filled_price else 100.0

                if order.side == "buy":
                    if pos:
                        new_qty = pos.quantity + qty
                        pos.entry_price = ((pos.quantity * pos.entry_price) + (qty * price)) / new_qty
                        pos.quantity = new_qty
                    else:
                        pos = VirtualPosition(ticker=order.ticker, mode="real", quantity=qty, entry_price=price, policy="rebalance")
                        db.add(pos)
                elif order.side == "sell":
                    if pos:
                        pos.quantity = max(0.0, pos.quantity - qty)
                        if pos.quantity <= 0.0001:
                            db.delete(pos)

                db.commit()
                print(f"Order {order.id} resolved as FILLED. Updated position for {order.ticker}.")

            elif status in ["canceled", "rejected", "expired"]:
                order.status = status
                db.commit()
                print(f"Order {order.id} resolved as {status.upper()}.")

            elif status == "partially_filled":
                order.status = "partially_filled"
                filled_qty = float(broker_order.filled_qty) if broker_order.filled_qty else 0.0
                print(f"Order {order.id} is PARTIALLY FILLED: {filled_qty}/{order.qty} shares.")
                db.commit()

        except Exception as e:
            print(f"Error syncing order {order.id}: {e}")

def sync_broker_positions(db, api):
    """
    Compares local database VirtualPosition values with live positions on Alpaca.
    Corrects discrepancies and logs synthetic transactions to maintain FIFO consistency.
    """
    if not api:
        return

    print("Running broker positions reconciliation...")
    try:
        # 1. Fetch live positions from broker
        broker_positions = api.list_positions()
        broker_pos_dict = {p.symbol: p for p in broker_positions}

        # 2. Fetch local positions from SQLite
        local_positions = db.query(VirtualPosition).filter(VirtualPosition.mode == "real").all()
        local_pos_dict = {p.ticker: p for p in local_positions}

        # 3. Align positions
        for ticker, b_pos in broker_pos_dict.items():
            b_qty = float(b_pos.qty)
            b_price = float(b_pos.avg_entry_price)

            l_pos = local_pos_dict.get(ticker)

            if not l_pos:
                print(f"Reconcile: {ticker} held on broker ({b_qty} shares) but missing locally. Creating local position.")
                l_pos = VirtualPosition(ticker=ticker, mode="real", quantity=b_qty, entry_price=b_price, policy="rebalance")
                db.add(l_pos)

                # Create synthetic buy order for FIFO lot
                sync_order_id = f"sync-buy-{ticker}-{int(time.time())}"
                sync_order = VirtualOrder(
                    id=sync_order_id,
                    mode="real",
                    ticker=ticker,
                    qty=b_qty,
                    side="buy",
                    type="market",
                    status="filled",
                    filled_price=b_price,
                    created_at=datetime.now().isoformat()
                )
                db.add(sync_order)
                db.commit()

            elif abs(l_pos.quantity - b_qty) > 0.0001:
                diff = b_qty - l_pos.quantity
                print(f"Reconcile: {ticker} quantity mismatch. Local: {l_pos.quantity:.4f}, Broker: {b_qty:.4f}. Diff: {diff:+.4f}.")

                l_pos.quantity = b_qty
                l_pos.entry_price = b_price

                action = "buy" if diff > 0 else "sell"
                sync_order_id = f"sync-{action}-{ticker}-{int(time.time())}"
                sync_order = VirtualOrder(
                    id=sync_order_id,
                    mode="real",
                    ticker=ticker,
                    qty=abs(diff),
                    side=action,
                    type="market",
                    status="filled",
                    filled_price=b_price,
                    created_at=datetime.now().isoformat()
                )
                db.add(sync_order)
                db.commit()

            elif abs(l_pos.entry_price - b_price) > 0.01:
                print(f"Reconcile: {ticker} entry price mismatch. Local: ${l_pos.entry_price:.2f}, Broker: ${b_price:.2f}. Updating cost basis.")
                l_pos.entry_price = b_price
                db.commit()

        # 4. Remove local positions closed on broker
        for ticker, l_pos in local_pos_dict.items():
            if ticker not in broker_pos_dict and l_pos.quantity > 0:
                print(f"Reconcile: {ticker} held locally ({l_pos.quantity} shares) but closed on broker. Deleting local position.")

                sync_order_id = f"sync-sell-{ticker}-{int(time.time())}"
                sync_order = VirtualOrder(
                    id=sync_order_id,
                    mode="real",
                    ticker=ticker,
                    qty=l_pos.quantity,
                    side="sell",
                    type="market",
                    status="filled",
                    filled_price=l_pos.entry_price,
                    created_at=datetime.now().isoformat()
                )
                db.add(sync_order)
                db.delete(l_pos)
                db.commit()

        # 5. Sync cash balance
        account = db.query(VirtualAccount).filter(VirtualAccount.id == 2).first()
        try:
            broker_account = api.get_account()
            b_cash = float(broker_account.cash)
            if account:
                if abs(account.cash - b_cash) > 0.01:
                    print(f"Reconcile: Cash balance mismatch. Local: ${account.cash:.2f}, Broker: ${b_cash:.2f}. Syncing cash.")
                    account.cash = b_cash
                    account.buying_power = b_cash
                    db.commit()
            else:
                account = VirtualAccount(id=2, cash=b_cash, buying_power=b_cash, equity=b_cash)
                db.add(account)
                db.commit()
        except Exception as e:
            print(f"Failed to sync cash balance: {e}")

        print("Broker positions reconciliation complete.")
    except Exception as e:
        print(f"Error during positions reconciliation: {e}")

def execute_alpaca_live_paper_trade(api, db, suggestions_data, mode="real"):
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
    positions_db = {p.ticker: p for p in db.query(VirtualPosition).filter(VirtualPosition.mode == mode).all()}

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

                # Log in SQLite ExecutedTrade
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

                # Log in SQLite VirtualOrder if not already exists (e.g., virtual broker already logged it)
                existing = db.query(VirtualOrder).filter(VirtualOrder.id == order.id).first()
                if not existing:
                    v_order = VirtualOrder(
                        id=order.id,
                        mode=mode,
                        ticker=ticker,
                        qty=float(shares),
                        side="buy",
                        type="market",
                        status="submitted",
                        stop_loss=float(stop_loss) if stop_loss else None,
                        take_profit=float(take_profit) if take_profit else None,
                        created_at=datetime.now().isoformat()
                    )
                    db.add(v_order)
                db.commit()
                print(f"Order submitted successfully. Logged order ID: {order.id}")
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

                    liq_order = api.close_position(ticker)

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

                    # Log liquidation in SQLite VirtualOrder if not already exists
                    existing = db.query(VirtualOrder).filter(VirtualOrder.id == liq_order.id).first()
                    if not existing:
                        v_order = VirtualOrder(
                            id=liq_order.id,
                            mode=mode,
                            ticker=ticker,
                            qty=float(shares_to_sell),
                            side="sell",
                            type="market",
                            status="submitted",
                            created_at=datetime.now().isoformat()
                        )
                        db.add(v_order)
                    db.commit()
                    print(f"Position closed successfully. Logged order ID: {liq_order.id}")
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
    current_date = datetime.strptime(current_date_str.split(" ")[0].split("T")[0], "%Y-%m-%d").date()

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
            return datetime.strptime(o.sim_date.split(" ")[0].split("T")[0], "%Y-%m-%d").date()
        if "T" in o.created_at:
            return datetime.strptime(o.created_at.split("T")[0], "%Y-%m-%d").date()
        return datetime.strptime(o.created_at.split(" ")[0], "%Y-%m-%d").date()

    sorted_buys = sorted(buys, key=get_order_date)
    total_buy_orders_qty = sum(b.qty for b in sorted_buys)

    lots = []
    # Manual holdings (difference between owned and virtually purchased)
    if total_owned > total_buy_orders_qty:
        manual_qty = total_owned - total_buy_orders_qty
        purchase_date = None
        if hasattr(pos, 'purchase_date') and pos.purchase_date:
            try:
                purchase_date = datetime.strptime(pos.purchase_date.split(" ")[0].split("T")[0], "%Y-%m-%d").date()
            except ValueError:
                pass
        if not purchase_date:
            purchase_date = current_date - timedelta(days=500)

        lots.append({
            "qty": manual_qty,
            "date": purchase_date
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
                        lt_order = api.submit_order(
                            symbol=ticker,
                            qty=n_shares,
                            side='buy',
                            type='market',
                            time_in_force='gtc'
                        )
                        existing = db.query(VirtualOrder).filter(VirtualOrder.id == lt_order.id).first()
                        if not existing:
                            v_order = VirtualOrder(
                                id=lt_order.id,
                                ticker=ticker,
                                qty=float(n_shares),
                                side="buy",
                                type="market",
                                status="submitted",
                                created_at=datetime.now().isoformat()
                            )
                            db.add(v_order)
                        db.commit()
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
                        lt_order = api.submit_order(
                            symbol=ticker,
                            qty=m_shares,
                            side='sell',
                            type='market',
                            time_in_force='gtc'
                        )
                        existing = db.query(VirtualOrder).filter(VirtualOrder.id == lt_order.id).first()
                        if not existing:
                            v_order = VirtualOrder(
                                id=lt_order.id,
                                ticker=ticker,
                                qty=float(m_shares),
                                side="sell",
                                type="market",
                                status="submitted",
                                created_at=datetime.now().isoformat()
                            )
                            db.add(v_order)
                        db.commit()
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
        # Connect to Alpaca
        api = get_alpaca_api()

        if api:
            # 1. Perform authentication check
            print("Verifying Alpaca credentials...")
            check_alpaca_authentication(api)

            # 2. Sync broker order states & reconcile positions at startup
            print("Synchronizing with broker...")
            sync_broker_orders(db, api)
            sync_broker_positions(db, api)

            # 3. Retrieve daily recommendations
            print("Retrieving daily trade recommendations...")
            suggestions_data = get_daily_suggestions(date=None, db=db)

            # 4. Run execution
            execute_alpaca_live_paper_trade(api, db, suggestions_data)

            # 5. Final sync to capture immediate fills
            print("Running final synchronization post-trade...")
            sync_broker_orders(db, api)
            sync_broker_positions(db, api)
        else:
            print("No Alpaca API connection. Falling back to local simulated execution...")
            print("Retrieving daily trade recommendations...")
            suggestions_data = get_daily_suggestions(date=None, db=db)
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
    acc_id = 2 if mode == "real" else 1
    pos_mode = "real" if mode == "real" else "replay"

    account = db.query(VirtualAccount).filter(VirtualAccount.id == acc_id).first()
    if not account:
        account = VirtualAccount(id=acc_id, cash=100000.0, buying_power=100000.0, equity=100000.0)
        db.add(account)
        db.commit()

    # 2. Stop/Take Profit evaluation for active positions
    positions = db.query(VirtualPosition).filter(VirtualPosition.quantity > 0, VirtualPosition.mode == pos_mode).all()

    for pos in positions:
        ticker = pos.ticker
        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if not price_rec:
            continue

        order = db.query(VirtualOrder).filter(
            VirtualOrder.ticker == ticker,
            VirtualOrder.status == "filled",
            VirtualOrder.side == "buy",
            VirtualOrder.mode == pos_mode,
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
                    mode=pos_mode,
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
    positions = db.query(VirtualPosition).filter(VirtualPosition.quantity > 0, VirtualPosition.mode == pos_mode).all()
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
