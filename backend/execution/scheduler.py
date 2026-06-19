import sys
import os
import argparse
import time
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

HEARTBEAT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "data", "scheduler_heartbeat.txt")

PIDFILE = os.path.join(os.path.dirname(HEARTBEAT_FILE), "scheduler.pid")


def _write_heartbeat():
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(datetime.now().isoformat())
    except Exception as e:
        print(f"Heartbeat write failed: {e}")


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False          # no such process
    except PermissionError:
        return True           # exists (owned by another user)
    except OSError:
        return False
    return True


def _acquire_singleton(force=False):
    """Refuse to start a second scheduler if one is already running (the root cause of duplicate
    daemons + the stale-import failures). Writes a PID lock and clears it on clean exit."""
    import atexit
    try:
        if os.path.exists(PIDFILE):
            old = int((open(PIDFILE).read() or "0").strip() or 0)
            if old and old != os.getpid() and _pid_alive(old):
                if not force:
                    print(f"⛔ A scheduler is already running (PID {old}). It won't start a duplicate.\n"
                          f"   Use `make schedule` (restarts cleanly) or pass --force to override.")
                    return False
                print(f"⚠ --force: starting despite existing scheduler PID {old}.")
        os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: os.path.exists(PIDFILE) and os.remove(PIDFILE))
        return True
    except Exception as e:
        print(f"Singleton guard warning (continuing): {e}")
        return True

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import timedelta
from data_ingestion.price_fetcher import fetch_recent_prices, fetch_daily_history
from data_ingestion.macro_fetcher import fetch_macro_indicators
from data_ingestion.sentiment_fetcher import fetch_sentiment
from data_ingestion.news_llm import fetch_and_score as score_news_llm
from ml_engine.models import train_models
from ml_engine.swing_alpha import train_both as train_swing_model
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
        # Pull premium newsletter articles (e.g. The Information) you received by email and LLM-score
        # them into the same news_llm_scores feed. Only runs if IMAP creds are configured.
        from app.core.config import IMAP_USER, IMAP_PASSWORD
        if SWING_ENABLED and IMAP_USER and IMAP_PASSWORD:
            try:
                from data_ingestion.premium_ingest import ingest_imap
                ingest_imap(days=7)
            except Exception as e:
                print(f"Premium newsletter ingest skipped: {e}")
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
        # Re-run the swing signals off the fresh news so the served/UI signals are immediately current.
        try:
            from app.main import get_daily_suggestions, clear_suggestions_cache
            from app.database import SessionLocal
            clear_suggestions_cache()
            db = SessionLocal()
            try:
                get_daily_suggestions(date=None, db=db)
            finally:
                db.close()
            print("Swing signals re-run on fresh news.")
        except Exception as e:
            print(f"Signal re-run after scoring failed: {e}")

        # Re-execute the swing book off the fresh signals (only while the market is open).
        from app.core.config import INTRADAY_EXECUTION_ENABLED, EXECUTION_STRATEGY
        if INTRADAY_EXECUTION_ENABLED and EXECUTION_STRATEGY == "swing":
            try:
                from execution.executor import get_alpaca_api, run_execution
                api = get_alpaca_api()
                if api and api.get_clock().is_open:
                    print("Market open — running intraday swing re-execution...")
                    run_execution()
                else:
                    print("Market closed — skipping intraday execution.")
            except Exception as e:
                print(f"Intraday re-execution failed: {e}")
        print("Intraday LLM News Scoring completed.")
    except Exception as e:
        print(f"Error during Intraday LLM News Scoring: {e}")

def intraday_price_fetch_job():
    """Fetches intraday prices every 5 minutes during market hours so suggestions and models are current."""
    print(f"\n[{datetime.now()}] Triggering Intraday Price Fetch Job...")
    try:
        fetch_recent_prices()

        # Clear suggestions cache and pre-populate
        try:
            from app.main import get_daily_suggestions, clear_suggestions_cache
            from app.database import SessionLocal
            clear_suggestions_cache()
            db = SessionLocal()
            try:
                get_daily_suggestions(date=None, db=db)
            finally:
                db.close()
            print("Suggestions cache cleared and refreshed with fresh prices.")
        except Exception as e:
            print(f"Signal re-run after price fetch failed: {e}")

        print("Intraday Price Fetch completed successfully.")
    except Exception as e:
        print(f"Error during Intraday Price Fetch: {e}")

def heartbeat_job():
    _write_heartbeat()

def daily_trade_inference_job():
    print(f"\n[{datetime.now()}] Triggering Daily Trade Inference Job...")
    # Daily inference is run dynamically in executor.py, but we log the status here
    print("Daily Trade Inference completed (latest model state is loaded).")

def _market_is_open():
    """Returns (is_open, detail) from Alpaca's clock. On any error returns (None, reason) so callers can
    decide whether to proceed — we never want a transient clock failure to silently halt the daily run."""
    try:
        from execution.executor import get_alpaca_api
        api = get_alpaca_api()
        if not api:
            return None, "no Alpaca API connection"
        clock = api.get_clock()
        if clock.is_open:
            return True, "market open"
        return False, f"market closed — next open {clock.next_open}"
    except Exception as e:
        return None, f"clock check failed: {e}"


def daily_trade_execution_job():
    print(f"\n[{datetime.now()}] Triggering Daily Trade Execution Job...")
    # Skip cleanly on weekends/holidays. The 09:45 ET cron fires every calendar weekday regardless of
    # market holidays (e.g. Juneteenth), so guard here the same way the intraday job does — otherwise we'd
    # queue GTC orders against a closed market. If the clock check itself fails we proceed (fail-open) so a
    # transient broker hiccup doesn't skip a real trading day.
    is_open, detail = _market_is_open()
    if is_open is False:
        print(f"Skipping Daily Trade Execution — {detail}.")
        return
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
    parser.add_argument("--force", action="store_true",
                        help="start even if another scheduler appears to be running")
    args = parser.parse_args()

    if args.test:
        test_single_scheduler_tick()
        return

    if not _acquire_singleton(force=args.force):
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
            name="Intraday LLM news scoring + swing re-execution (hourly, market hours)"
        )

    # 6. Intraday price fetch — every 5 minutes during market hours Monday-Friday (9 AM to 5 PM Eastern)
    scheduler.add_job(
        intraday_price_fetch_job,
        trigger=CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5", timezone=market_tz),
        id="intraday_price_fetch",
        name="Fetch intraday prices (every 5 minutes, market hours)"
    )

    # 7. Liveness heartbeat (every minute) so /api/health can report the daemon as up.
    scheduler.add_job(heartbeat_job, trigger=IntervalTrigger(seconds=60),
                      id="heartbeat", name="Scheduler liveness heartbeat")
    _write_heartbeat()

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
