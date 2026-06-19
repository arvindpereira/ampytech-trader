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
    TICKER_UNIVERSE,
    HEDGE_MODE,
    EXECUTION_STRATEGY,
    SWING_POSITION_PCT,
    SWING_HORIZON_DAYS,
    LONGTERM_GRID_ENABLED,
    REGIME_OVERLAY_ENABLED,
    REGIME_SWING_FACTORS,
)
from app.database import (
    SessionLocal, init_db, ExecutedTrade, RecentPrice,
    VirtualAccount, VirtualPosition, VirtualOrder, BrokerPerformanceLog,
    UniverseTicker, AppSetting, TradingBlock
)
from ml_engine.models import PortfolioOptimizer
import numpy as np


# ---------------------------------------------------------------------------
# Trading safety guards (wash-sale / never-trade blocks + global kill-switch)
# ---------------------------------------------------------------------------
AUTO_TRADING_PAUSED_KEY = "auto_trading_paused"


def auto_trading_paused(db):
    """Global kill-switch. When set, run_execution() halts ALL trading (buys and sells)."""
    row = db.query(AppSetting).filter(AppSetting.key == AUTO_TRADING_PAUSED_KEY).first()
    return bool(row and str(row.value).lower() in ("true", "1", "yes"))


def buy_block_reason(ticker, db, as_of=None):
    """Return a human-readable reason if BUYING `ticker` is currently blocked, else None.

    Blocks the auto-trader from re-buying a name inside the wash-sale window (so a harvested
    loss isn't disallowed) or a name flagged never-trade. Expired wash-sale blocks are
    deactivated opportunistically. Sells are never blocked here — only the global pause stops
    sells — so tax-loss exits and stop-losses still fire.
    """
    if not ticker:
        return None
    today = (as_of or datetime.now().date()).isoformat()
    blocks = db.query(TradingBlock).filter(
        TradingBlock.ticker == ticker.upper(), TradingBlock.active == True  # noqa: E712
    ).all()
    expired = False
    for b in blocks:
        if b.block_type == "permanent" or not b.blocked_until:
            return b.reason or f"{ticker} is flagged never-trade ({b.account_label or 'manual'})."
        if b.blocked_until >= today:
            return (b.reason or
                    f"{ticker} is in a wash-sale block until {b.blocked_until} "
                    f"(loss sale {b.sale_date or '?'}).")
        # past blocked_until -> retire it so the list stays clean
        b.active = False
        expired = True
    if expired:
        db.commit()
    return None

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

def _is_real_alpaca():
    """True only when real Alpaca credentials are configured (the virtual broker mock cannot short)."""
    return bool(ALPACA_API_KEY and ALPACA_SECRET_KEY)


def _maybe_place_hedge(api, db, sug, long_shares, long_price, mode, date_str):
    """Places the offsetting short hedge for a just-opened long, sized to the ACTUAL long notional.

    Guarded: only on real Alpaca (which supports shorting). On the virtual broker it logs and skips so
    backtests/sims stay long-only and the user knows to place the hedge manually (see the UI trade plan).
    """
    hedge = sug.get("hedge") if isinstance(sug, dict) else None
    if not hedge or not hedge.get("symbol"):
        return
    hsym = hedge["symbol"]
    ratio = float(hedge.get("ratio") or 0.0)
    hprice = hedge.get("price")
    if ratio <= 0 or not hprice:
        return

    if not _is_real_alpaca():
        print(f"Hedge for {sug['ticker']} ({HEDGE_MODE}): SHORT {hsym} skipped — virtual broker can't short. "
              f"Execute manually if desired (see trade plan).")
        return

    long_notional = long_shares * long_price
    hedge_shares = int((ratio * long_notional) / hprice)
    if hedge_shares < 1:
        return
    try:
        h_order = api.submit_order(symbol=hsym, qty=hedge_shares, side="sell",
                                   type="market", time_in_force="gtc")
        db.add(VirtualOrder(
            id=h_order.id, mode=mode, ticker=hsym, qty=float(hedge_shares),
            side="sell", type="market", status="submitted", created_at=datetime.now().isoformat()
        ))
        db.commit()
        print(f"Hedge placed: SHORT {hedge_shares} {hsym} (ratio {ratio:.2f}x ${long_notional:,.0f} long in {sug['ticker']}).")
    except Exception as e:
        print(f"Failed to place hedge short {hsym} for {sug['ticker']}: {e}")
        db.rollback()


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
            blocked = buy_block_reason(ticker, db)
            if blocked:
                print(f"🔒 Skipping BUY {ticker}: {blocked}")
                continue
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
            blocked = buy_block_reason(ticker, db)
            if blocked:
                print(f"🔒 Skipping BUY {ticker}: {blocked}")
                continue
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

                # Optional market-risk hedge (real Alpaca only; sized to the actual long notional).
                if HEDGE_MODE in ("beta_neutral", "pair_trade"):
                    _maybe_place_hedge(api, db, sug, shares, close_price, mode, date_str)
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

def close_aged_swing_positions(api, db, mode="real", horizon_days=None, allowed_tickers=None):
    """Closes swing positions held past their intended horizon that haven't hit a bracket.

    The validated strategy exits at the triple-barrier OR at the horizon; Alpaca brackets only cover the
    stop/take-profit, so this provides the time-based leg. Identifies swing entries as filled BUY orders
    carrying a stop+take-profit, and closes any whose age exceeds ~horizon trading days.
    `allowed_tickers` restricts to positions currently assigned the swing strategy."""
    horizon_days = horizon_days or SWING_HORIZON_DAYS
    max_cal_days = int(horizon_days * 1.5) + 1   # ~trading days → calendar days, with a small buffer
    try:
        open_positions = {p.symbol: p for p in api.list_positions()}
    except Exception as e:
        print(f"Could not list positions for horizon exit: {e}")
        return
    if not open_positions:
        return

    now = datetime.now()
    for ticker, pos in open_positions.items():
        if allowed_tickers is not None and ticker not in allowed_tickers:
            continue
        entry = db.query(VirtualOrder).filter(
            VirtualOrder.ticker == ticker, VirtualOrder.mode == mode, VirtualOrder.side == "buy",
            (VirtualOrder.stop_loss.isnot(None) | VirtualOrder.take_profit.isnot(None))
        ).order_by(VirtualOrder.created_at.desc()).first()
        if not entry or not entry.created_at:
            continue
        try:
            opened = datetime.fromisoformat(entry.created_at.split("T")[0] if "T" in entry.created_at else entry.created_at.split(" ")[0])
        except ValueError:
            continue
        age = (now - opened).days
        if age >= max_cal_days:
            print(f"Swing horizon exit: {ticker} held {age}d (≥ ~{horizon_days} trading days). Closing.")
            try:
                api.close_position(ticker)   # cancels the bracket OCO and flattens
                db.add(VirtualOrder(id=f"swing-horizon-{ticker}-{int(time.time())}", mode=mode, ticker=ticker,
                                    qty=float(pos.qty), side="sell", type="market", status="submitted",
                                    created_at=now.isoformat()))
                db.commit()
            except Exception as e:
                print(f"Failed horizon exit for {ticker}: {e}")
                db.rollback()


def execute_swing_paper_trades(api, db, suggestions_data, mode="real", allowed_tickers=None, budget=None,
                               suggestions_key="swing_suggestions", label="swing"):
    """Places swing (multi-day) bracket trades on Alpaca paper from `swing_suggestions`.

    Sizing is a FIXED fraction of equity (SWING_POSITION_PCT), matching the capital-aware portfolio
    simulation that validated the edge — deliberately NOT Kelly, whose input would be the model's
    low ranking-prob and would zero most positions. Entries are bracket orders (stop + take-profit);
    the time-based horizon exit is handled separately by close_aged_swing_positions().

    `allowed_tickers` (set) restricts buys to stocks assigned the swing strategy; `budget` (float, $)
    is a SOFT cap on total NEW capital deployed this run (the swing bucket's remaining allocation)."""
    print(f"--- Executing {label} (Multi-Day) Paper Trades via Alpaca API ---")
    date_str = datetime.now().strftime("%Y-%m-%d")

    swing_sugs = suggestions_data.get(suggestions_key, [])
    buys = [s for s in swing_sugs if s.get("action") == "BUY"]
    if allowed_tickers is not None:
        buys = [s for s in buys if s["ticker"] in allowed_tickers]
    if not buys:
        print(f"No {label} BUY signals for assigned tickers today.")
        return

    account = api.get_account()
    portfolio_equity = float(account.equity)
    buying_power = float(account.buying_power)
    remaining_budget = buying_power if budget is None else min(budget, buying_power)
    print(f"Alpaca Equity: ${portfolio_equity:.2f} | BP: ${buying_power:.2f} | swing BUYs: {len(buys)} | "
          f"bucket budget: ${remaining_budget:.0f}")

    open_positions = api.list_positions()
    active_tickers = [p.symbol for p in open_positions]
    positions_db = {p.ticker: p for p in db.query(VirtualPosition).filter(VirtualPosition.mode == mode).all()}

    # Per-name volatility caps: trim high-beta names' position size (size x min(1, target/name_vol)).
    from app.core.config import SWING_VOL_TARGET
    vol_map = {}
    if SWING_VOL_TARGET > 0:
        try:
            from app.database import TickerClassification
            vol_map = {c.ticker: c.volatility for c in db.query(TickerClassification).all() if c.volatility}
        except Exception:
            vol_map = {}

    for sug in buys:
        ticker = sug["ticker"]
        close_price = sug["close"]
        stop_loss = sug.get("stop_loss")
        take_profit = sug.get("take_profit")

        blocked = buy_block_reason(ticker, db)
        if blocked:
            print(f"🔒 Skipping BUY {ticker}: {blocked}")
            continue
        if ticker in positions_db and positions_db[ticker].policy == "lock":
            print(f"{ticker} is locked. Skipping.")
            continue
        if ticker in active_tickers:
            print(f"Already holding {ticker}. Skipping.")
            continue
        if not stop_loss or not take_profit or close_price <= 0:
            print(f"{ticker} missing bracket levels. Skipping.")
            continue
        if remaining_budget < 100.0:
            print(f"Swing bucket budget exhausted (${remaining_budget:.0f} left). Stopping new buys.")
            break

        # Recompute brackets off the LIVE entry price, not the (possibly stale) stored close. The model
        # gives bracket WIDTHS as percentages; anchoring them to a stale close can put the stop above the
        # live price and get the order rejected. Falls back to the stored close if no live quote.
        sl_pct = 1.0 - (stop_loss / close_price)
        tp_pct = (take_profit / close_price) - 1.0
        try:
            entry_price = float(api.get_latest_trade(ticker).price)
        except Exception:
            entry_price = close_price
        if entry_price <= 0:
            entry_price = close_price
        stop_price = round(entry_price * (1.0 - sl_pct), 2)
        target_price = round(entry_price * (1.0 + tp_pct), 2)

        vol_scale = 1.0
        if SWING_VOL_TARGET > 0 and vol_map.get(ticker):
            vol_scale = min(1.0, SWING_VOL_TARGET / vol_map[ticker])   # high-vol → smaller position
        trade_value = min(portfolio_equity * SWING_POSITION_PCT * vol_scale, remaining_budget)
        shares = int(trade_value / entry_price)
        if shares < 1 or trade_value < 100.0:
            print(f"Skipping {ticker}: position too small (${trade_value:.0f}).")
            continue

        stop_loss, take_profit = stop_price, target_price
        print(f"Submitting swing bracket order for {ticker}: {shares} sh @ ~${entry_price:.2f} "
              f"(stop ${stop_loss:.2f}, target ${take_profit:.2f}, conf {sug['confidence']*100:.0f}%)...")
        try:
            order = api.submit_order(
                symbol=ticker, qty=shares, side="buy", type="market", time_in_force="gtc",
                order_class="bracket",
                take_profit=dict(limit_price=round(take_profit, 2)),
                stop_loss=dict(stop_price=round(stop_loss, 2)),
            )
            db.add(ExecutedTrade(ticker=ticker, date=date_str, action="BUY", price=entry_price,
                                 shares=float(shares), value=float(shares * entry_price), status="filled"))
            if not db.query(VirtualOrder).filter(VirtualOrder.id == order.id).first():
                db.add(VirtualOrder(id=order.id, mode=mode, ticker=ticker, qty=float(shares), side="buy",
                                    type="market", status="submitted",
                                    stop_loss=float(stop_loss), take_profit=float(take_profit),
                                    created_at=datetime.now().isoformat()))
            db.commit()
            buying_power -= shares * entry_price
            remaining_budget -= shares * entry_price
            print(f"  Order submitted. ID: {order.id}")
        except Exception as e:
            print(f"Failed to submit swing order for {ticker}: {e}")
            db.rollback()

    print("Swing paper executions complete.\n")


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

    def get_order_date(o):
        if o.sim_date:
            return datetime.strptime(o.sim_date.split(" ")[0].split("T")[0], "%Y-%m-%d").date()
        if "T" in o.created_at:
            return datetime.strptime(o.created_at.split("T")[0], "%Y-%m-%d").date()
        return datetime.strptime(o.created_at.split(" ")[0], "%Y-%m-%d").date()

    buys = db.query(VirtualOrder).filter(
        VirtualOrder.ticker == ticker,
        VirtualOrder.side == "buy",
        VirtualOrder.status == "filled"
    ).all()
    buys = [b for b in buys if get_order_date(b) <= current_date]

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
    sells = [s for s in sells if get_order_date(s) <= current_date]

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

def execute_long_term_grid_trades(db, api, suggestions_data, sim_date, allowed_tickers=None, budget_fraction=1.0):
    """
    Executes long-term grid/tranche-based buying and tax-optimized FIFO selling for the stocks assigned
    the 'longterm' strategy. `allowed_tickers` restricts the set; `budget_fraction` (0..1) scales the
    MPT target weights down to this bucket's share of equity (soft capital cap).
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

        if allowed_tickers is not None and ticker not in allowed_tickers:
            continue

        target_weight = target_weights.get(ticker, 0.0)
        pos = positions_db.get(ticker)
        current_shares = pos.quantity if pos else 0.0
        entry_price = pos.entry_price if pos else 0.0
        if pos and pos.policy == "lock":
            continue

        price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date == sim_date).first()
        if not price_rec:
            price_rec = db.query(RecentPrice).filter(RecentPrice.ticker == ticker, RecentPrice.date <= sim_date).order_by(RecentPrice.date.desc()).first()

        if not price_rec:
            continue

        current_price = price_rec.close
        target_shares = (portfolio_equity * target_weight * budget_fraction) / current_price
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
                            time_in_force='day'   # Alpaca requires DAY for fractional-share quantities
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
                            time_in_force='day'   # Alpaca requires DAY for fractional-share quantities
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

    # Global kill-switch: when auto-trading is paused, do nothing at all (no buys, no sells,
    # no broker sync side effects). Lets the user freeze the bot while harvesting losses in a
    # real account so it can't trip a wash sale.
    if auto_trading_paused(db):
        print("\n" + "=" * 60)
        print("⏸  AUTO-TRADING IS PAUSED — skipping execution entirely.")
        print("   Resume from the Equity Advisor tab (or POST /api/execution/auto-trading).")
        print("=" * 60 + "\n")
        db.close()
        return

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

            # 3. Retrieve daily recommendations (with hedge plan attached when hedging is enabled)
            print("Retrieving daily trade recommendations...")
            suggestions_data = get_daily_suggestions(date=None, hedge_mode=HEDGE_MODE, db=db)

            # 4. Bucket-aware, per-ticker-strategy execution. Each strategy trades only the tickers
            #    assigned to it and deploys at most its bucket's share of equity (soft cap: never opens
            #    NEW positions past the limit, but doesn't force-sell existing holdings).
            from app.main import get_strategy_buckets, get_strategy_assignments, get_sim_date
            buckets = get_strategy_buckets(db)
            assignments = get_strategy_assignments(db)
            swing_set = {t for t, s in assignments.items() if s == "swing"}
            longterm_set = {t for t, s in assignments.items() if s == "longterm"}
            # Speculative-tier names are split off into the small high-risk sleeve (aggressive model);
            # the core swing book excludes them so the two sleeves never trade the same name.
            try:
                from ml_engine.swing_alpha import tickers_for_tiers, HIGH_RISK_TIERS
                spec_set = set(tickers_for_tiers(HIGH_RISK_TIERS))
            except Exception as e:
                print(f"Tier lookup failed (high-risk sleeve disabled): {e}")
                spec_set = set()
            core_swing_set = swing_set - spec_set

            try:
                equity = float(api.get_account().equity)
                broker_pos = api.list_positions()
            except Exception as e:
                print(f"Could not read account for bucket budgets: {e}")
                equity, broker_pos = 0.0, []
            deployed = {"swing": 0.0, "longterm": 0.0, "hold": 0.0}
            for p in broker_pos:
                strat = assignments.get(p.symbol, "swing")
                deployed[strat] = deployed.get(strat, 0.0) + float(p.market_value)

            # Regime overlay: shrink swing's effective capital in defensive regimes (freed → cash).
            regime = suggestions_data.get("regime", "growth")
            swing_factor = REGIME_SWING_FACTORS.get(regime, 1.0) if REGIME_OVERLAY_ENABLED else 1.0
            swing_w_eff = buckets.get("swing", 0.0) * swing_factor
            if swing_factor < 1.0:
                print(f"Regime overlay: {regime} → swing weight {buckets.get('swing',0):.2f} × {swing_factor} "
                      f"= {swing_w_eff:.2f} (freed capital held as cash)")

            swing_budget = max(0.0, swing_w_eff * equity - deployed.get("swing", 0.0))
            longterm_frac = buckets.get("longterm", 0.0)
            print(f"Buckets: swing {buckets.get('swing',0)*100:.0f}%→{swing_w_eff*100:.0f}% (avail ${swing_budget:.0f}), "
                  f"longterm {longterm_frac*100:.0f}% | regime {regime} | assigned swing={len(swing_set)} longterm={len(longterm_set)}")

            # Swing bucket: horizon exits, then budget-capped entries (CORE names only — excl. speculative).
            close_aged_swing_positions(api, db, allowed_tickers=core_swing_set if core_swing_set else None)
            if buckets.get("swing", 0.0) > 0:
                execute_swing_paper_trades(api, db, suggestions_data,
                                           allowed_tickers=core_swing_set, budget=swing_budget)

            # High-risk sleeve: AGGRESSIVE model on speculative names, hard-capped at HIGH_RISK_CAP of equity.
            from app.core.config import HIGH_RISK_CAP
            hr_frac = min(HIGH_RISK_CAP, buckets.get("high_risk", 0.0))
            if hr_frac > 0 and spec_set:
                deployed_hr = sum(float(p.market_value) for p in broker_pos if p.symbol in spec_set)
                hr_budget = max(0.0, hr_frac * equity - deployed_hr)
                print(f"High-risk sleeve: {hr_frac*100:.1f}% cap (avail ${hr_budget:.0f}) over {len(spec_set)} speculative names")
                close_aged_swing_positions(api, db, allowed_tickers=spec_set)
                if hr_budget >= 100.0:
                    execute_swing_paper_trades(api, db, suggestions_data, allowed_tickers=spec_set,
                                               budget=hr_budget, suggestions_key="high_risk_suggestions",
                                               label="high-risk")

            # Long-term bucket: MPT grid restricted to longterm-assigned tickers, scaled to the bucket.
            if longterm_frac > 0 and longterm_set:
                execute_long_term_grid_trades(db, api, suggestions_data,
                                              get_sim_date() or datetime.now().strftime("%Y-%m-%d"),
                                              allowed_tickers=longterm_set, budget_fraction=longterm_frac)

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
