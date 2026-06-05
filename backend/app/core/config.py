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
TICKER_UNIVERSE = [
    # Indices
    "SPY", "QQQ",
    # Tech / Semiconductors
    "AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "AMZN", "META", "NFLX", "TSM",
    # Finance / Energy / Retail
    "JPM", "V", "XOM", "WMT", "PG",
    # Healthcare / Biotech
    "JNJ", "LLY", "UNH",
    # Automotive
    "TSLA",
    # Cryptocurrency
    "X:BTCUSD", "X:ETHUSD",
    # Forex
    "C:EURUSD", "C:GBPUSD"
]

# Database and Storage Config
DATA_STORAGE_DIR = os.getenv("DATA_STORAGE_DIR", "")
if not DATA_STORAGE_DIR:
    DATA_STORAGE_DIR = os.path.join(BASE_DIR, "data")

os.makedirs(DATA_STORAGE_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_STORAGE_DIR, "trading_system.db")

# Data Collection Resolution & Range
DATA_TIMESPAN = os.getenv("DATA_TIMESPAN", "hour")
try:
    DATA_MULTIPLIER = int(os.getenv("DATA_MULTIPLIER", "1"))
except ValueError:
    DATA_MULTIPLIER = 1

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
