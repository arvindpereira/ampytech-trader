import os

# Base directory setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables from .env file if it exists (run early so configurations can use them)
env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, val = stripped.split("=", 1)
                val_clean = val.strip()
                if (val_clean.startswith('"') and val_clean.endswith('"')) or (val_clean.startswith("'") and val_clean.endswith("'")):
                    val_clean = val_clean[1:-1]
                os.environ[key.strip()] = val_clean

# Ticker Universe definition
#
# Assembled from three tech-cycle "boom" cohorts plus benchmarks. Only tickers that
# still trade are included: dot-com casualties (Sun/SUNW, Yahoo/YHOO, AOL, WorldCom,
# Nortel, Lucent, JDSU) are unavailable from our data sources, so any pre-2003 history
# carries inherent survivorship bias. Renamed tickers are mapped to their current symbol
# (e.g. BlackBerry BBRY -> BB).
TICKER_UNIVERSE = [
    # Benchmarks: broad indices + sector ETFs (regime / MPT context)
    "SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP",
    # Dot-com / first internet-software boom survivors
    "MSFT", "CSCO", "INTC", "ORCL", "IBM", "QCOM", "AMD", "AMZN", "AAPL",
    # Mobile / smartphone-software boom leaders
    "GOOGL", "NVDA", "AVGO", "NOK", "BB",
    # AI boom leaders
    "META", "TSM", "ASML", "MU", "ARM", "PLTR", "SMCI",
    # Newly IPOed and diversified sectors
    "SPACE", "WMT", "XOM", "JPM", "LLY", "PG", "GE", "JNJ",
]

# Benchmark used for the long-term performance comparison (kept separate so it is always
# fetched even if removed from the tradable universe).
BENCHMARK_TICKER = "BRK-B"

# Database and Storage Config
DATA_STORAGE_DIR = os.getenv("DATA_STORAGE_DIR", "")
if not DATA_STORAGE_DIR:
    DATA_STORAGE_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_STORAGE_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_STORAGE_DIR, "trading_system.db")

# Data Collection Resolution & Range
#
# Two clean, never-mixed datasets:
#   * recent_prices  -> HOURLY bars from Massive/Polygon. The plan only serves a rolling
#                       ~5-year window (intraday history does not exist before ~2003 at any
#                       tier), so HOURLY_LOOKBACK_DAYS stays safely inside that window.
#   * daily_prices   -> DAILY bars from Yahoo Finance, full multi-decade history (survivors
#                       only), used for long-term / regime modelling and benchmarks.
DATA_TIMESPAN = os.getenv("DATA_TIMESPAN", "hour")
try:
    DATA_MULTIPLIER = int(os.getenv("DATA_MULTIPLIER", "1"))
except ValueError:
    DATA_MULTIPLIER = 1

# How far back to request HOURLY bars. ~4.6 years keeps us inside the Massive/Polygon
# 5-year entitlement (observed cutoff ~2021-09); requests before it return 403.
try:
    HOURLY_LOOKBACK_DAYS = int(os.getenv("HOURLY_LOOKBACK_DAYS", "1700"))
except ValueError:
    HOURLY_LOOKBACK_DAYS = 1700

# Start date for the full DAILY history (Yahoo). Spans the dot-com era for survivors.
DAILY_HISTORY_START = os.getenv("DAILY_HISTORY_START", "1998-01-01")

# --- Model horizon / window parameters (resolution-aware) ---------------------
# Short-term model trades on HOURLY bars. A regular US session is ~7 hourly bars;
# the breakout target looks ~2 trading days ahead.
HOURLY_BARS_PER_DAY = int(os.getenv("HOURLY_BARS_PER_DAY", "7"))
SHORT_TERM_HORIZON_BARS = int(os.getenv("SHORT_TERM_HORIZON_BARS", "14"))   # ~2 trading days
SEQ_LEN = int(os.getenv("SEQ_LEN", "10"))                                   # PyTorch sequence length (bars, ~1.5 trading days)

# Triple-barrier brackets — the SAME numbers are used to (a) label training data ("would this
# trade have hit take-profit before the stop?") and (b) size the live stop/take-profit orders,
# so the model's target matches the trade as actually executed.
SHORT_TERM_ATR_STOP_MULT = float(os.getenv("SHORT_TERM_ATR_STOP_MULT", "2.0"))  # stop = 2.0 * ATR
SHORT_TERM_TP_MULT = float(os.getenv("SHORT_TERM_TP_MULT", "2.5"))              # take-profit = 2.5 * stop
SHORT_TERM_STOP_MIN = float(os.getenv("SHORT_TERM_STOP_MIN", "0.015"))          # stop floor 1.5%
SHORT_TERM_STOP_MAX = float(os.getenv("SHORT_TERM_STOP_MAX", "0.05"))           # stop cap 5%

# Which short-term model actually serves (and is what the BUY threshold is calibrated against).
# 'xgboost' (default) is cheap to walk-forward/calibrate; 'pytorch' is opt-in/experimental. Making this
# explicit avoids "whatever .pth exists" silently serving an un-calibrated model (PR#2 review C6/C14).
SERVED_MODEL = os.getenv("SERVED_MODEL", "xgboost").lower()

# Entry/exit probability thresholds on the model's P(take-profit before stop). A fixed absolute threshold
# does NOT transfer between models (different prob scales), so it is CALIBRATED per served model by
# `calibrate_threshold()` (written to saved_models/threshold.json) to hit SHORT_TERM_SIGNAL_RATE on a
# time-ordered holdout. SHORT_TERM_BUY_THRESHOLD is only the fallback when threshold.json is absent.
SHORT_TERM_SIGNAL_RATE = float(os.getenv("SHORT_TERM_SIGNAL_RATE", "0.005"))  # target ~top 0.5% of bars
SHORT_TERM_BUY_THRESHOLD = float(os.getenv("SHORT_TERM_BUY_THRESHOLD", "0.23"))
SHORT_TERM_SELL_THRESHOLD = float(os.getenv("SHORT_TERM_SELL_THRESHOLD", "0.02"))

# Long-term model rebalances on DAILY bars; covariance/return window in trading days.
MPT_WINDOW_DAYS = int(os.getenv("MPT_WINDOW_DAYS", "252"))                  # ~1 trading year

# Alternative data and hedging configurations
# Insider data now has a REAL source (SEC EDGAR Form 4, free/no-key); Congress (STOCK Act) is still only
# synthetic. Kept OFF by default until the real insider signal is validated by walk-forward.
# See pr2_review_and_updates.md (C1).
ALT_DATA_ENABLED = os.getenv("ALT_DATA_ENABLED", "False").lower() == "true"

# SEC EDGAR requires a descriptive User-Agent with contact info; set SEC_USER_AGENT to your own.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "ampytech-trader research contact@example.com")
try:
    INSIDER_FETCH_DAYS = int(os.getenv("INSIDER_FETCH_DAYS", "365"))   # how much Form 4 history to pull
except ValueError:
    INSIDER_FETCH_DAYS = 365
try:
    INSIDER_LOOKBACK_DAYS = int(os.getenv("INSIDER_LOOKBACK_DAYS", "30"))
except ValueError:
    INSIDER_LOOKBACK_DAYS = 30
try:
    CONGRESS_LOOKBACK_DAYS = int(os.getenv("CONGRESS_LOOKBACK_DAYS", "90"))
except ValueError:
    CONGRESS_LOOKBACK_DAYS = 90
HEDGE_MODE = os.getenv("HEDGE_MODE", "none")  # 'none', 'beta_neutral', 'pair_trade'

try:
    LONGTERM_TILT_STRENGTH = float(os.getenv("LONGTERM_TILT_STRENGTH", "0.15"))
except ValueError:
    LONGTERM_TILT_STRENGTH = 0.15

# Local LLM (Ollama) for scoring news headlines into per-ticker directional sentiment for the SWING model.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma4:e4b")   # local, fast, JSON-clean (qwen3.5 emits empty under json mode)
NEWS_LLM_START = os.getenv("NEWS_LLM_START", "2023-01-01")  # how far back to score headlines

# --- Swing (multi-day) strategy ---------------------------------------------------------------
# The DAILY, multi-day model. Walk-forward + capital-aware portfolio sim showed the LLM-scored news
# features add a real portfolio-level edge (higher return/Sharpe, lower drawdown) over a technicals-only
# baseline — the first strategy in this repo to clear that bar. Served via /api/suggestions; the bracket
# numbers below MUST match the triple-barrier labels build_all_features used to train it.
SWING_ENABLED = os.getenv("SWING_ENABLED", "True").lower() in ("true", "1", "yes")
SWING_HORIZON_DAYS = int(os.getenv("SWING_HORIZON_DAYS", "5"))   # holding horizon in trading days
SWING_ATR_STOP_MULT = float(os.getenv("SWING_ATR_STOP_MULT", "2.0"))
SWING_TP_MULT = float(os.getenv("SWING_TP_MULT", "2.5"))
SWING_STOP_MIN = float(os.getenv("SWING_STOP_MIN", "0.015"))
SWING_STOP_MAX = float(os.getenv("SWING_STOP_MAX", "0.05"))
# Cap concurrent BUYs to the highest-conviction names, matching the ≤10 open positions the portfolio
# simulation used to validate the edge. Lower-ranked above-threshold candidates are demoted to HOLD.
SWING_TOP_N = int(os.getenv("SWING_TOP_N", "10"))

# How far back news sentiment can be backfilled (Polygon news history starts ~2021).
NEWS_HISTORY_START = os.getenv("NEWS_HISTORY_START", "2021-01-01")

# Retained for backwards compatibility with macro/crisis fetchers.
try:
    DATA_LOOKBACK_DAYS = int(os.getenv("DATA_LOOKBACK_DAYS", "11000"))
except ValueError:
    DATA_LOOKBACK_DAYS = 11000


# API Keys and Credentials (read from environment, with sensible local defaults)
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "")
MASSIVE_BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# Reddit PRAW Credentials (Read-only)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ampytech-trader:v1.0.0 (by /u/arvind)")

# Alpaca API Credentials (Paper Trading Defaults)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
