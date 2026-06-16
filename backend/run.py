#!/usr/bin/env python
import argparse
import sys
import subprocess
import os

def run_command(command, description):
    print(f"=== Running: {description} ===")
    try:
        # Ensure we run from the backend directory context
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(command, check=True, env=env)
        print(f"=== Completed: {description} successfully ===\n")
        return result.returncode == 0
    except subprocess.CalledProcessError as e:
        print(f"Error during execution: {e}", file=sys.stderr)
        sys.exit(e.returncode)

def fetch():
    # Run the various fetchers sequentially
    scripts = [
        [sys.executable, "data_ingestion/price_fetcher.py"],
        [sys.executable, "data_ingestion/macro_fetcher.py"],
        [sys.executable, "data_ingestion/crisis_fetcher.py"],
        [sys.executable, "data_ingestion/sentiment_fetcher.py"],
    ]
    # Insider disclosures (real SEC EDGAR Form 4) are OFF by default — fetched only when ALT_DATA_ENABLED,
    # since the signal still needs walk-forward validation before it should drive the model.
    from app.core.config import ALT_DATA_ENABLED
    if ALT_DATA_ENABLED:
        scripts.append([sys.executable, "data_ingestion/alternative_fetcher.py"])  # real Form 4
    for script in scripts:
        script_name = os.path.basename(script[1])
        run_command(script, f"Data Ingestion ({script_name})")

def train(epochs=None):
    run_command([sys.executable, "ml_engine/models.py", "--train"], "ML Model Training")
    cmd = [sys.executable, "ml_engine/deep_models.py", "--train"]
    if epochs is not None:
        cmd.extend(["--epochs", str(epochs)])
    run_command(cmd, "PyTorch Temporal Attention Training")

def backtest():
    run_command([sys.executable, "backtesting/backtest.py"], "PyBroker Backtesting & Stress Tests")

def walkforward(splits=5):
    run_command([sys.executable, "ml_engine/models.py", "--walkforward", "--splits", str(splits)],
                "Walk-Forward Out-of-Sample Validation")

def calibrate():
    run_command([sys.executable, "ml_engine/models.py", "--calibrate"],
                "Calibrate Served-Model BUY Threshold")

def longterm_eval(horizon=21, splits=4):
    # This research eval always tests WITH insider features on, regardless of the production flag.
    os.environ["ALT_DATA_ENABLED"] = "True"
    run_command([sys.executable, "ml_engine/longterm_alpha.py", "--horizon", str(horizon), "--splits", str(splits)],
                "Long-Term Insider Alpha Walk-Forward (daily, multi-week horizon)")

def longterm_tilt(strength=0.10):
    os.environ["ALT_DATA_ENABLED"] = "True"
    run_command([sys.executable, "ml_engine/longterm_alpha.py", "--backtest-tilt",
                 "--tilt-strength", str(strength), "--start", "2022-01-01"],
                "Long-Term Insider-Tilt MPT A/B Backtest")

def news_llm(start=None):
    cmd = [sys.executable, "data_ingestion/news_llm.py"]
    if start:
        cmd += ["--start", start]
    run_command(cmd, "LLM News Scoring (local Ollama) for the swing model")

def serve():
    # Run Uvicorn to serve the FastAPI application
    run_command([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8008", "--reload"], "FastAPI Server")

def schedule():
    run_command([sys.executable, "execution/scheduler.py"], "APScheduler Background Worker Daemon")

def main():
    parser = argparse.ArgumentParser(description="Ampytech Trader Backend Command-Line Tool")
    parser.add_argument(
        "action",
        choices=["fetch", "train", "backtest", "walkforward", "calibrate", "longterm-eval", "longterm-tilt", "news-llm", "serve", "schedule", "simulate", "backtest-virtual", "popular-tickers", "add-ticker"],
        help="Pipeline stage to execute"
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Ticker symbol to add (for add-ticker action)"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=5,
        help="Number of days to simulate in forward mode"
    )
    parser.add_argument(
        "--months",
        type=int,
        default=6,
        help="Number of months to replay in backtest mode"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=None,
        help="Number of epochs to train the deep temporal attention model"
    )
    parser.add_argument(
        "--splits",
        type=int,
        default=5,
        help="Number of walk-forward folds"
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=21,
        help="Forward-return horizon (trading days) for longterm-eval"
    )
    parser.add_argument(
        "--tilt-strength",
        type=float,
        default=0.10,
        help="Insider-tilt strength for longterm-tilt"
    )

    args = parser.parse_args()

    # Change working directory to backend/ directory for consistency
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(backend_dir)

    if args.action == "fetch":
        fetch()
    elif args.action == "train":
        train(epochs=args.epochs)
    elif args.action == "backtest":
        backtest()
    elif args.action == "walkforward":
        walkforward(splits=args.splits)
    elif args.action == "calibrate":
        calibrate()
    elif args.action == "longterm-eval":
        longterm_eval(horizon=args.horizon, splits=args.splits)
    elif args.action == "longterm-tilt":
        longterm_tilt(strength=args.tilt_strength)
    elif args.action == "news-llm":
        news_llm(start=args.start if hasattr(args, "start") else None)
    elif args.action == "serve":
        serve()
    elif args.action == "schedule":
        schedule()
    elif args.action == "simulate":
        from execution.simulator import run_forward_simulation
        run_forward_simulation(args.days)
    elif args.action == "backtest-virtual":
        from execution.simulator import run_historical_replay
        run_historical_replay(args.months)
    elif args.action == "popular-tickers":
        run_command([sys.executable, "data_ingestion/popular_tickers.py"], "Fetch Popular & Trending Tickers")
    elif args.action == "add-ticker":
        if args.symbol:
            run_command([sys.executable, "data_ingestion/popular_tickers.py", "--add", args.symbol], f"Add Ticker {args.symbol}")
        else:
            print("Please specify a ticker symbol to add using --symbol.")

if __name__ == "__main__":
    main()
