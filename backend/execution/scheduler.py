import sys
import os
import argparse
import time
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta
from data_ingestion.price_fetcher import fetch_recent_prices, fetch_daily_history
from data_ingestion.macro_fetcher import fetch_macro_indicators
from data_ingestion.sentiment_fetcher import fetch_sentiment
from data_ingestion.news_llm import fetch_and_score as score_news_llm
from ml_engine.models import train_models
from ml_engine.swing_alpha import train_and_save as train_swing_model
from execution.executor import run_execution
from app.core.config import SWING_ENABLED

def daily_data_fetch_job():
    print(f"\n[{datetime.now()}] Triggering Daily Data Ingestion Job...")
    try:
        fetch_recent_prices()
        fetch_daily_history()
        fetch_macro_indicators()
        fetch_sentiment()
        # Keep the swing model's LLM-news features current: incrementally score the last week's
        # headlines (resumable — already-scored article ids are skipped). Needs local Ollama running.
        if SWING_ENABLED:
            start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            score_news_llm(start=start)
        print("Daily Data Ingestion Job completed successfully.")
    except Exception as e:
        print(f"Error during Daily Data Ingestion: {e}")

def intraday_news_scoring_job():
    """Scores fresh headlines through the trading day so the swing model isn't waiting until the next
    morning. Cheap + resumable: only the last 2 days are queried and already-scored ids are skipped.
    Needs local Ollama running."""
    print(f"\n[{datetime.now()}] Triggering Intraday LLM News Scoring Job...")
    try:
        start = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        score_news_llm(start=start)
        print("Intraday LLM News Scoring completed.")
    except Exception as e:
        print(f"Error during Intraday LLM News Scoring: {e}")

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
        if SWING_ENABLED:
            train_swing_model()   # refresh the served swing model on the latest data
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

    # 5. Intraday LLM news scoring — hourly during market hours so swing signals use same-day news
    if SWING_ENABLED:
        scheduler.add_job(
            intraday_news_scoring_job,
            trigger=CronTrigger(day_of_week="mon-fri", hour="10-16", minute=0, timezone=market_tz),
            id="intraday_news_scoring",
            name="Intraday LLM news scoring (hourly, market hours)"
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
