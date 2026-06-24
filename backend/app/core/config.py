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

# --- Google Drive backup (so the DB can leave Git LFS without losing data) ----------------------
# OAuth "Desktop app" client (Google Cloud Console → APIs & Services → Credentials, with the Drive API
# enabled). First `make db-backup` opens a browser to consent; the token is cached in data/gdrive_token.json.
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1v694--8X1YkvPp-3oRQ4no8hN0xmWCdY")
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

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
NEWS_LLM_START = os.getenv("NEWS_LLM_START", "2021-01-01")  # how far back to score headlines (~5y, dense from 2021)

# News-LLM scoring provider. Default "ollama" keeps the recurring daily/intraday jobs free + local.
# "openai" is a fast opt-in for bulk backfills (10-50x faster; ~<$1 for a full backfill). Headlines
# are public data, so there's no privacy concern sending them out.
NEWS_LLM_PROVIDER = os.getenv("NEWS_LLM_PROVIDER", "ollama")
NEWS_LLM_WORKERS = int(os.getenv("NEWS_LLM_WORKERS", "12"))   # concurrent batches when provider=openai
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")       # cheap, fast, reliable JSON mode
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Premium-newsletter ingestion (e.g. The Information). Reads articles you legitimately receive by email
# via IMAP, has the LLM extract which universe tickers are materially affected, and stores per-ticker
# scores into news_llm_scores so they feed the swing model. Only derived scores are kept in the DB.
IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")           # use an app-password, never your main password
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
PREMIUM_SENDER = os.getenv("PREMIUM_SENDER", "theinformation.com")   # From-address filter
PREMIUM_SOURCE_TAG = os.getenv("PREMIUM_SOURCE_TAG", "the-information")
# Skip recurring digest/community emails (substring match on subject, case-insensitive) — they're not
# single-story articles and just burn scoring calls. Comma-separated; override to taste.
PREMIUM_SKIP_SUBJECTS = os.getenv(
    "PREMIUM_SKIP_SUBJECTS",
    "Top Posts Today,The Briefing,Monday Readout,Weekend Readout,The Information Finance")
PREMIUM_LLM_MODEL = os.getenv("PREMIUM_LLM_MODEL", "")   # "" -> OPENAI_MODEL (key set) else LLM_MODEL
PREMIUM_BODY_CHARS = int(os.getenv("PREMIUM_BODY_CHARS", "8000"))    # chars of article body sent to LLM
PREMIUM_REL_MIN = float(os.getenv("PREMIUM_REL_MIN", "0.3"))         # drop low-relevance mentions
PREMIUM_ABS_MIN = float(os.getenv("PREMIUM_ABS_MIN", "0.15"))        # drop near-neutral (no direction) mentions
PREMIUM_MAX_MENTIONS = int(os.getenv("PREMIUM_MAX_MENTIONS", "3"))   # cap tickers per article (top by rel*|s|)

# Model for eval/wargame "expert interpretation" (Model Evaluation + Crash tab). gpt-5.4 gives a
# strong read without the gpt-5.5 price spike.
OPENAI_EXPERT_MODEL = os.getenv("OPENAI_EXPERT_MODEL", "gpt-5.4")
EXPERT_INTERP_ENABLED = os.getenv("EXPERT_INTERP_ENABLED", "true").lower() == "true"

# Research Analyst: standard = cheap default (gpt-5.4-nano); premium = explicit user opt-in only.
# Note: OPENAI_MODEL stays as gpt-4o-mini for news scoring (different use-case, high volume).
RESEARCH_STANDARD_MODEL = os.getenv("RESEARCH_STANDARD_MODEL", "gpt-5.4-nano")
RESEARCH_PREMIUM_MODEL = os.getenv("RESEARCH_PREMIUM_MODEL", OPENAI_EXPERT_MODEL)  # gpt-5.4
# Back-compat alias
RESEARCH_EXPERT_MODEL = os.getenv("RESEARCH_EXPERT_MODEL", RESEARCH_PREMIUM_MODEL)
RESEARCH_LOCAL_MODEL = os.getenv("RESEARCH_LOCAL_MODEL", LLM_MODEL)
# Complexity threshold kept for reference but auto-escalation is disabled — premium requires explicit user opt-in.
RESEARCH_PREMIUM_COMPLEXITY = float(os.getenv("RESEARCH_PREMIUM_COMPLEXITY", "0.75"))
RESEARCH_MAX_TICKERS = int(os.getenv("RESEARCH_MAX_TICKERS", "12"))
RESEARCH_KB_FINNHUB_SLEEP = float(os.getenv("RESEARCH_KB_FINNHUB_SLEEP", "1.1"))  # stay under 60 RPM

# Web search (Phase 2) — optional deep research
SEARCH_API_PROVIDER = os.getenv("SEARCH_API_PROVIDER", "")  # tavily | brave
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", "")
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "8"))

# Phase 2c — semantic / keyword retrieval over news KB
RESEARCH_RETRIEVAL_ENABLED = os.getenv("RESEARCH_RETRIEVAL_ENABLED", "true").lower() == "true"
RESEARCH_RETRIEVAL_MODE = os.getenv("RESEARCH_RETRIEVAL_MODE", "hybrid")  # keyword | semantic | hybrid
RESEARCH_RETRIEVAL_LIMIT = int(os.getenv("RESEARCH_RETRIEVAL_LIMIT", "12"))
RESEARCH_RETRIEVAL_DAYS = int(os.getenv("RESEARCH_RETRIEVAL_DAYS", "90"))
RESEARCH_EMBED_MODEL = os.getenv("RESEARCH_EMBED_MODEL", "nomic-embed-text")

# --- Swing (multi-day) strategy ---------------------------------------------------------------
# The DAILY, multi-day model. Walk-forward + capital-aware portfolio sim showed the LLM-scored news
# features add a real portfolio-level edge (higher return/Sharpe, lower drawdown) over a technicals-only
# baseline — the first strategy in this repo to clear that bar. Served via /api/suggestions; the bracket
# numbers below MUST match the triple-barrier labels build_all_features used to train it.
SWING_ENABLED = os.getenv("SWING_ENABLED", "True").lower() in ("true", "1", "yes")
SWING_HORIZON_DAYS = int(os.getenv("SWING_HORIZON_DAYS", "5"))   # holding horizon in trading days
SWING_ATR_STOP_MULT = float(os.getenv("SWING_ATR_STOP_MULT", "2.0"))
# tp/stop tuned via `make stop-opt` (OOS from 2022): widening the stop cap to 12% and raising the
# reward:risk to 3.0 lifted OOS Sharpe 0.95→1.32 with slightly lower max drawdown — the tight 5% cap
# was getting whipsawed and cutting winners.
SWING_TP_MULT = float(os.getenv("SWING_TP_MULT", "3.0"))
SWING_STOP_MIN = float(os.getenv("SWING_STOP_MIN", "0.015"))
SWING_STOP_MAX = float(os.getenv("SWING_STOP_MAX", "0.12"))
# Cap concurrent BUYs to the highest-conviction names, matching the ≤10 open positions the portfolio
# simulation used to validate the edge. Lower-ranked above-threshold candidates are demoted to HOLD.
SWING_TOP_N = int(os.getenv("SWING_TOP_N", "10"))
# Small high-risk sleeve (aggressive model on speculative-tier names) — hard cap as a fraction of equity.
HIGH_RISK_CAP = float(os.getenv("HIGH_RISK_CAP", "0.05"))
HIGH_RISK_TOP_N = int(os.getenv("HIGH_RISK_TOP_N", "3"))
# When True, each swing/high-risk position is sized as (sleeve allocation ÷ top-N) so position size
# auto-scales with the bucket you pick (no size-vs-cap mismatch). When False, uses fixed SWING_POSITION_PCT.
SWING_AUTOSIZE = os.getenv("SWING_AUTOSIZE", "True").lower() in ("true", "1", "yes")
# Fixed fraction of equity per swing position. The portfolio sim that validated the edge used fixed 10%
# allocations (NOT Kelly) — the model's win-prob is a ranking score, not a calibrated Kelly input, so
# Kelly sizing would zero most positions. Keep this in lock-step with that sim.
SWING_POSITION_PCT = float(os.getenv("SWING_POSITION_PCT", "0.10"))
# Long-term (MPT) grid thresholds, relative to a position's cost basis: add a tranche when price is
# this far BELOW cost, take profit when this far ABOVE cost. Also used to surface buy/sell target
# prices on holdings in the dashboard.
GRID_BUY_DIP = float(os.getenv("GRID_BUY_DIP", "0.03"))
GRID_TP_GAIN = float(os.getenv("GRID_TP_GAIN", "0.05"))
# Max size of a single grid tranche, as a fraction of equity — keeps buys small so a continued
# drop just means more (cheaper) tranches on later runs rather than one big buy at the first dip.
GRID_TRANCHE_PCT = float(os.getenv("GRID_TRANCHE_PCT", "0.02"))
# Per-name volatility cap: scale each position by min(1, target/name_vol) so high-beta names get
# smaller allocations. Set SWING_VOL_TARGET=0 to disable.
SWING_VOL_TARGET = float(os.getenv("SWING_VOL_TARGET", "0.35"))
# Which strategy actually places intraday bracket trades on the broker: 'swing' (validated edge) or
# 'short_term' (legacy hourly model — net-negative in portfolio sim, kept only for comparison).
EXECUTION_STRATEGY = os.getenv("EXECUTION_STRATEGY", "swing").lower()
# Whether to ALSO run the long-term MPT grid/tranche rebalancer alongside swing. Off by default: swing
# alone targets ~100% of equity, so enabling both would deploy the long-term book on margin.
LONGTERM_GRID_ENABLED = os.getenv("LONGTERM_GRID_ENABLED", "False").lower() in ("true", "1", "yes")
# Re-execute swing trades intraday (hourly, market hours) right after each fresh-news signal re-run —
# replacing exited slots and running horizon exits without waiting for the next 09:45 cycle. Only acts
# while the market is open; with ~100% deployed it mostly fills slots freed by bracket/horizon exits.
INTRADAY_EXECUTION_ENABLED = os.getenv("INTRADAY_EXECUTION_ENABLED", "True").lower() in ("true", "1", "yes")

# Regime-aware overlay: swing was shown to AMPLIFY bear drawdowns (−25% in 2022 vs −20% S&P), so when
# the HMM regime turns defensive, shrink the swing bucket's effective capital (freed capital becomes
# cash — we don't force-sell, consistent with the soft-cap policy). Long-term MPT is left at its bucket.
REGIME_OVERLAY_ENABLED = os.getenv("REGIME_OVERLAY_ENABLED", "True").lower() in ("true", "1", "yes")
REGIME_SWING_FACTORS = {
    "crisis": float(os.getenv("REGIME_SWING_CRISIS", "0.25")),       # crisis → swing capital ×0.25
    "transition": float(os.getenv("REGIME_SWING_TRANSITION", "0.6")),  # transition → ×0.6
    "growth": 1.0,
}

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
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "")

# Reddit PRAW Credentials (Read-only)
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "ampytech-trader:v1.0.0 (by /u/arvind)")

# Alpaca API Credentials (Paper Trading Defaults). The ALPACA_* set is the PAPER account.
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Alpaca LIVE (real-money) account credentials — a SEPARATE account from the paper one above.
# Kept in env (never the DB, which is backed up to Google Drive). Absent by default; when unset the
# live account is treated as unconfigured and is never traded (see execution/accounts.py).
ALPACA_LIVE_API_KEY = os.getenv("ALPACA_LIVE_API_KEY", "")
ALPACA_LIVE_SECRET_KEY = os.getenv("ALPACA_LIVE_SECRET_KEY", "")
ALPACA_LIVE_BASE_URL = os.getenv("ALPACA_LIVE_BASE_URL", "https://api.alpaca.markets")

# Alpaca market-data host (separate from the trading host). Used for the free, Benzinga-sourced
# news endpoint (/v1beta1/news), which is included with any Alpaca account at no extra cost.
ALPACA_DATA_URL = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
# Merge Alpaca (Benzinga) headlines into the LLM news feed alongside Polygon/Massive. On by default
NEWS_USE_ALPACA = os.getenv("NEWS_USE_ALPACA", "True").lower() in ("true", "1", "yes")

# --- Short-Term Portfolio Simulation & Throttling/Kelly Config ---
PORTFOLIO_MAX_SIGNALS_PER_BAR = int(os.getenv("PORTFOLIO_MAX_SIGNALS_PER_BAR", "0"))  # 0 = unlimited
PORTFOLIO_MAX_OPEN_POSITIONS = int(os.getenv("PORTFOLIO_MAX_OPEN_POSITIONS", "10"))
PORTFOLIO_USE_KELLY = os.getenv("PORTFOLIO_USE_KELLY", "False").lower() in ("true", "1", "yes")
PORTFOLIO_KELLY_SCALE = float(os.getenv("PORTFOLIO_KELLY_SCALE", "0.25"))
PORTFOLIO_KELLY_MIN = float(os.getenv("PORTFOLIO_KELLY_MIN", "0.01"))
PORTFOLIO_KELLY_MAX = float(os.getenv("PORTFOLIO_KELLY_MAX", "0.10"))
