import os

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
    "TSLA"
]

# Database Config
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "data", "trading_system.db")

# API Keys and Credentials (read from environment, with sensible local defaults)
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
