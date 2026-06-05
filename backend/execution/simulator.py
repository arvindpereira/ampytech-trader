import os
import sys
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import (
    SessionLocal, RecentPrice, VirtualAccount, VirtualPosition, VirtualOrder, BrokerPerformanceLog
)
from app.main import set_sim_date, get_daily_suggestions
from execution.executor import get_alpaca_api, execute_alpaca_live_paper_trade, evaluate_virtual_broker_daily

def run_historical_replay(months_count):
    """
    Replays historical trading day-by-day over the past months.
    Resets replay account to $100k, processes recommendations look-ahead free,
    simulates fills on next-day open, checks stops, and records benchmark metrics.
    """
    db = SessionLocal()
    try:
        db.query(VirtualPosition).filter(VirtualPosition.mode == "replay").delete()
        db.query(VirtualOrder).filter(VirtualOrder.mode == "replay").delete()
        db.query(BrokerPerformanceLog).filter(BrokerPerformanceLog.mode == "replay").delete()

        account = db.query(VirtualAccount).filter(VirtualAccount.id == 1).first()
        if account:
            account.cash = 100000.0
            account.buying_power = 100000.0
            account.equity = 100000.0
        else:
            account = VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
            db.add(account)
        db.commit()

        # Get start date
        start_date = (datetime.now() - timedelta(days=months_count * 30)).strftime("%Y-%m-%d")

        # Find all trading days (unique dates in RecentPrice for SPY) since start_date
        trading_days = db.query(RecentPrice.date).filter(RecentPrice.ticker == "SPY", RecentPrice.date >= start_date).order_by(RecentPrice.date.asc()).all()
        trading_days = [d[0] for d in trading_days]

        if not trading_days:
            print(f"No cached trading days found in database since {start_date}. Run python run.py fetch first.")
            return

        print(f"Starting historical replay over {len(trading_days)} trading days (since {start_date})...")

        api = get_alpaca_api(force_virtual=True)
        if not api:
            print("Error: Alpaca REST client could not be initialized")
            return

        for i, sim_date in enumerate(trading_days):
            print(f"\n=== Replaying day {i+1}/{len(trading_days)}: {sim_date} ===")
            # 1. Set current simulation date context
            set_sim_date(sim_date)

            # 2. Get recommendations based on data up to sim_date - 1 (look-ahead free)
            ref_date = datetime.strptime(sim_date.split(" ")[0].split("T")[0], "%Y-%m-%d")
            prev_date_str = (ref_date - timedelta(days=1)).strftime("%Y-%m-%d")

            try:
                suggestions = get_daily_suggestions(date=prev_date_str, db=db)
            except Exception as e:
                print(f"Skipping day {sim_date} suggestions fetch failed: {e}")
                continue

            # 3. Execute trades (fires requests to local server which fills at open on sim_date)
            execute_alpaca_live_paper_trade(api, db, suggestions)

            # 4. Evaluate stops and log daily valuation at close of sim_date
            evaluate_virtual_broker_daily(db, sim_date, mode="replay")

        print("\n=== Historical Replay Completed successfully ===")
    finally:
        set_sim_date("") # Clear sim date
        db.close()

def run_forward_simulation(days_count):
    """
    Simulates forward trading over the last N cached trading days.
    """
    db = SessionLocal()
    try:
        # We don't reset the account, but we clear live performance logs for the simulation range to overwrite
        # Find all trading days (unique dates in RecentPrice for SPY)
        all_days = db.query(RecentPrice.date).filter(RecentPrice.ticker == "SPY").order_by(RecentPrice.date.desc()).all()
        trading_days = sorted([d[0] for d in all_days[:days_count]])

        if not trading_days:
            print("No cached trading days found in database. Run python run.py fetch first.")
            return

        print(f"Starting forward simulation over last {len(trading_days)} days...")

        api = get_alpaca_api(force_virtual=True)
        if not api:
            print("Error: Alpaca REST client could not be initialized")
            return

        for i, sim_date in enumerate(trading_days):
            print(f"\n=== Simulating day {i+1}/{len(trading_days)}: {sim_date} ===")
            set_sim_date(sim_date)

            ref_date = datetime.strptime(sim_date.split(" ")[0].split("T")[0], "%Y-%m-%d")
            prev_date_str = (ref_date - timedelta(days=1)).strftime("%Y-%m-%d")

            try:
                suggestions = get_daily_suggestions(date=prev_date_str, db=db)
            except Exception as e:
                print(f"Skipping day {sim_date} suggestions fetch failed: {e}")
                continue

            execute_alpaca_live_paper_trade(api, db, suggestions)

            evaluate_virtual_broker_daily(db, sim_date, mode="live")

        print("\n=== Forward Simulation Completed successfully ===")
    finally:
        set_sim_date("") # Clear sim date
        db.close()
