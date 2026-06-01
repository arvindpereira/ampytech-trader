import sys
import os
import argparse
import time
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_ingestion.price_fetcher import fetch_recent_prices
from data_ingestion.macro_fetcher import fetch_macro_indicators
from data_ingestion.sentiment_fetcher import fetch_sentiment
from ml_engine.models import train_models
from execution.executor import run_execution

def daily_data_fetch_job():
    print(f"\n[{datetime.now()}] Triggering Daily Data Ingestion Job...")
    try:
        fetch_recent_prices()
        fetch_macro_indicators()
        fetch_sentiment()
        print("Daily Data Ingestion Job completed successfully.")
    except Exception as e:
        print(f"Error during Daily Data Ingestion: {e}")

def daily_trade_inference_job():
    print(f"\n[{datetime.now()}] Triggering Daily Trade Inference Job...")
    # Daily inference is run dynamically in executor.py, but we log the status here
    print("Daily Trade Inference completed (latest model state is loaded).")

def daily_trade_execution_job():
    print(f"\n[{datetime.now()}] Triggering Daily Trade Execution Job...")
    try:
        run_execution()
        print("Daily Trade Execution Job completed.")
    except Exception as e:
        print(f"Error during Daily Trade Execution: {e}")

def weekly_model_retrain_job():
    print(f"\n[{datetime.now()}] Triggering Weekly Model Retraining Job...")
    try:
        train_models()
        print("Weekly Model Retraining completed successfully.")
    except Exception as e:
        print(f"Error during Weekly Model Retraining: {e}")

def test_single_scheduler_tick():
    """Runs a single pass of the entire sequence immediately for developer verification."""
    print("\n==================================================")
    print("=== STARTING SCHEDULER DEVELOPMENT VERIFICATION ===")
    print("==================================================")
    
    daily_data_fetch_job()
    weekly_model_retrain_job() # Train models once so we have files ready
    daily_trade_execution_job()
    
    print("==================================================")
    print("=== SCHEDULER DEVELOPMENT VERIFICATION DONE =====")
    print("==================================================\n")

def main():
    parser = argparse.ArgumentParser(description="Ampytech Trader Scheduler Daemon")
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run a single pipeline pass immediately for verification and exit"
    )
    args = parser.parse_args()
    
    if args.test:
        test_single_scheduler_tick()
        return
        
    scheduler = BlockingScheduler()
    
    # Configure Timezone for Eastern Time (New York Stock Market timezone)
    market_tz = "America/New_York"
    
    # 1. Daily Ingestion at 09:00 EST
    scheduler.add_job(
        daily_data_fetch_job,
        trigger=CronTrigger(hour=9, minute=0, timezone=market_tz),
        id="daily_data_fetch",
        name="Fetch daily price and sentiment data"
    )
    
    # 2. Daily Inference at 09:15 EST
    scheduler.add_job(
        daily_trade_inference_job,
        trigger=CronTrigger(hour=9, minute=15, timezone=market_tz),
        id="daily_inference",
        name="Generate daily predictions"
    )
    
    # 3. Daily Execution at 09:45 EST (15 mins after Market Open)
    scheduler.add_job(
        daily_trade_execution_job,
        trigger=CronTrigger(hour=9, minute=45, timezone=market_tz),
        id="daily_execution",
        name="Execute trades on Alpaca/Simulated"
    )
    
    # 4. Weekly Retraining every Sunday at 18:00 EST (before market week starts)
    scheduler.add_job(
        weekly_model_retrain_job,
        trigger=CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=market_tz),
        id="weekly_retrain",
        name="Retrain models on rolling window"
    )
    
    print("Starting APScheduler Background Worker Daemon (Blocking Mode)...")
    print("Scheduled jobs:")
    for job in scheduler.get_jobs():
        print(f" - {job.name} (ID: {job.id}): {job.trigger}")
        
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Scheduler daemon stopped.")

if __name__ == "__main__":
    main()
