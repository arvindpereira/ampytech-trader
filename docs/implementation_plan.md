# Implementation Plan - Stock Trading System

This plan outlines the stage-by-stage construction of a usable, local ML-driven stock trading system. By the end of the build, we will have a running backend pipeline, backtested machine learning models, a premium dashboard UI, and a paper-trading execution script connected to the Alpaca API.

---

## Technical Context & Decisions

Based on user review and technical auditing, we have aligned on the following:

1. **Initial Stock Universe (20 Tickers)**: We will target a liquid, high-volume basket representing diverse sectors and index trackers:
   * **Indices**: `SPY` (S&P 500), `QQQ` (Nasdaq 100)
   * **Tech/Semiconductors**: `AAPL`, `MSFT`, `NVDA`, `AMD`, `GOOGL`, `AMZN`, `META`, `NFLX`, `TSM`
   * **Finance/Energy/Retail**: `JPM`, `V`, `XOM`, `WMT`, `PG`
   * **Healthcare/Biotech**: `JNJ`, `LLY`, `UNH`
   * **Automotive**: `TSLA`

2. **Historical Training & Stress-Testing Horizon**:
   * **Default Window**: 2 years of rolling daily historical price and active sentiment (Reddit/News) data for near-term trend and sentiment feature engineering.
   * **Macro Crisis Eras (for Long-Term Strategy)**: The data ingestion pipeline will retrieve three additional historical periods to extract **regime-level meta-learnings** — how markets behave under stress, which sectors rotate in/out, and how asset correlations shift. 
     - Because historical Reddit/news data is not freely retrievable via standard APIs for any prior period (including 2020), **historical crisis modeling will rely strictly on price action, volume, sector relative strength, and macro indicators (interest rates, yield curve) via the FRED API**.
     - The crisis universe for each period will include: broad market and sector ETFs available at the time (e.g., `XLK`, `XLF`, `XLE`, `XLV`, `XLP` for all eras; `GLD` and `TLT` are restricted to the 2008 GFC and 2020 COVID eras as they did not exist during the Dot-Com era), plus representative index components. Note: `QQQ` must use a start date of `1999-03-10` (its inception date) for the Dot-Com era; `crisis_universes.yaml` should support per-ticker date overrides within each era config.
     - **Crisis Eras**:
       1. **Dot-Com Bubble & Bust**: Jan 1, 1999 – Dec 31, 2002 (price action through the Nasdaq trough).
       2. **2008 Financial Crisis**: Jan 1, 2007 – Dec 31, 2009 (systemic liquidity drawdowns).
       3. **COVID-19 Market Crash & Recovery**: Jan 1, 2020 – Dec 31, 2020 (rapid crash and recovery).

3. **Database Selection**: **SQLite** local file database (`backend/data/trading_system.db`) for rapid setup and local execution.

4. **Sentiment Data Source**:
   * **Live / Near-Term**: **NewsAPI / Finnhub** for news headlines, and **Reddit (r/wallstreetbets, r/stocks)** via the official **PRAW** library. 
     - **Credential Requirements**: Only read-only client keys (Client ID, Client Secret, User Agent) are required. Usernames and passwords are not collected.
     - **Rate-Limiting Protection**: To avoid rate limit exhaustion (NewsAPI free tier limits to 100 queries/day) during development restarts, all ingestion scripts must verify if data for the current day already exists in SQLite before hitting external APIs.
   * **Historical Backtesting**: Because standard APIs do not provide free access to historical news or Reddit data for prior years, backtests of past periods will use price-only and macro features. Incorporating historical news text is a future enhancement dependent on sourcing a specific labeled dataset.

---

## Proposed Project Structure

We will organize the repository as a clean monorepo:
```
ampytech-trader/
├── backend/
│   ├── app/                    # FastAPI Application
│   │   ├── main.py             # Server entry point (configures CORS middleware)
│   │   ├── api/                # Endpoints (suggestions, sentiment, portfolio)
│   │   ├── core/               # Configuration and security (config.py)
│   │   └── database/           # DB schema, initialization, and session handlers
│   ├── data/                   # Directory to store SQLite database file
│   ├── data_ingestion/         # Price, Macro, & Sentiment fetchers
│   │   ├── price_fetcher.py    # yfinance current/historical ingestion
│   │   ├── macro_fetcher.py    # FRED API interest rates & macro indicator ingestion
│   │   ├── crisis_fetcher.py   # Historical crisis data loader (1999-2002, 2007-2009, 2020)
│   │   ├── crisis_universes.yaml # Per-era ticker lists for crisis ingestion
│   │   └── sentiment_fetcher.py# Live News & Reddit scraping/sentiment pipelines
│   ├── ml_engine/              # Training & Feature Engineering
│   │   ├── features.py         # Tabular technical & sentiment feature creation
│   │   └── models.py           # Short-Term XGBoost & Long-Term Portfolio Optimization
│   ├── backtesting/            # Backtesting scripts (PyBroker runner)
│   ├── execution/              # Alpaca trade executor and scheduler
│   │   ├── executor.py         # Alpaca integration and trade placement
│   │   └── scheduler.py        # APScheduler routine jobs
│   ├── requirements.txt        # Python dependencies
│   └── run.py                  # CLI runner: fetch, train, backtest, serve
├── frontend/                   # Next.js/React Dashboard
│   ├── package.json
│   ├── src/
│   │   ├── app/                # Next.js App Router (dashboard UI)
│   │   ├── components/         # Sleek glassmorphic components
│   │   └── lib/                # API integration wrappers
└── docs/
    ├── stock_trader_design.md  # General Architecture design
    └── implementation_plan.md  # Detailed implementation sub-plans (this file)
```

---

## Detailed Build Stages

### Stage 0: CLI Runner

#### [NEW] [backend/run.py](backend/run.py)
The top-level entry point for all pipeline stages. All developer operations are triggered through this script to avoid ad-hoc execution of individual files.

| Command | Action |
| :--- | :--- |
| `python run.py fetch` | Run all data ingestion (prices, macro, crisis, sentiment) |
| `python run.py train` | Retrain and save all model artifacts (XGBoost + HMM) |
| `python run.py backtest` | Run the PyBroker backtest suite and print metrics |
| `python run.py serve` | Start the FastAPI backend server |
| `python run.py schedule` | Start the APScheduler background trading loop |

---

### Stage 1: Data Ingestion & Caching (Prices, Macro, Sentiment)

In this stage, we construct the local database schemas and write automated loaders to cache market prices, FRED macro indices, and active text sentiment.

#### [NEW] [backend/data_ingestion/crisis_fetcher.py](backend/data_ingestion/crisis_fetcher.py)
* **Goal**: Download and cache historical data for the designated crisis eras using a **crisis-era universe** — sector ETFs and representative large-cap names from each period.
* **Mechanism**: Use `yfinance` to pull OHLCV data for the crisis universe over each interval.
* **Storage**: Write data to a distinct `crisis_prices` table in the SQLite database, with an `era` column tagging each row (`dotcom`, `gfc`, `covid`).

#### [NEW] [backend/data_ingestion/price_fetcher.py](backend/data_ingestion/price_fetcher.py)
* **Goal**: Ingest recent 2-year rolling daily OHLCV bars for the 20 active tickers.
* **Mechanism**: Check if today's bars are already cached. If not, fetch daily bars via `yfinance`. Write to a `recent_prices` table in SQLite.

#### [NEW] [backend/data_ingestion/macro_fetcher.py](backend/data_ingestion/macro_fetcher.py)
* **Goal**: Ingest historical and current macro-economic metrics (interest rates, yield curve spreads) to model macro cycles.
* **Mechanism**: Query the Federal Reserve Bank of St. Louis (FRED) API. Write to a `macro_indicators` table. Requires a free FRED API key.

#### [NEW] [backend/data_ingestion/sentiment_fetcher.py](backend/data_ingestion/sentiment_fetcher.py)
* **Goal**: Parse active text streams and record rolling sentiment averages.
* **Mechanism**:
  1. Verify if today's news and Reddit logs exist in the local database. If yes, skip to prevent API limits exhaustion.
  2. Retrieve recent news headlines using keys from **NewsAPI** and **Finnhub** (limited to past 30 days).
  3. Fetch top posts from `/r/wallstreetbets` and `/r/stocks` using **PRAW** (using read-only application keys).
  4. Feed raw text into `vaderSentiment.SentimentIntensityAnalyzer`.
  5. Aggregate sentiment scores daily per ticker and save them to a `ticker_sentiment` table.

---

### Stage 2: Machine Learning Strategy & Backtesting Suite

Here, we design features, build modeling scripts, and backtest performance under crisis conditions.

#### [NEW] [backend/ml_engine/features.py](backend/ml_engine/features.py)
* **Engineered Features**:
  * Technicals: RSI (14-day), MACD, Bollinger Bands width, Average True Range (ATR, for volatility), rolling volumes.
  * Sentiment (Recent 2yr Only): 3-day and 7-day simple moving averages of sentiment polarity, sentiment momentum (current day score vs. 10-day average), and volume of mentions.
  * Macro: Daily Federal Funds Rate, 10-Year vs. 2-Year Treasury Yield Spread.
* **Look-Ahead Bias Rule**: All features for day T must be computable using only data available at market close on day T-1. All rolling calculations must use `.shift(1)` before being joined to the target label. This must be verified before any backtest result is trusted.

#### [NEW] [backend/ml_engine/models.py](backend/ml_engine/models.py)
* **Short-Term Classifier (XGBoost/LightGBM)**:
  * Targets high-volatility breakouts. Trains on rolling 2-year technical and sentiment features to output the probability that a ticker will rise by $\ge 2\%$ in the next 3 trading days.
* **Long-Term Strategy Optimizer**:
  * Fits an unsupervised **Hidden Markov Model (HMM)** on volatility and macro features (FRED). Post-fitting, hidden states are mapped to human-readable regime labels based on volatility distributions.
  * Uses moving average crossovers to establish long-term trend directions.
  * Fits covariance estimators (e.g., Ledoit-Wolf shrinkage) on historical crisis datasets to produce stable covariance matrices that reflect drawdown behavior during recessions and tech busts.
  * Uses **Mean-Variance Optimization** to solve for portfolio weights that maximize the Sharpe Ratio subject to position constraints, informed by the crisis-derived covariance structure and current HMM regime.
  * Employs a conservative **Fractional Kelly Criterion** (e.g., Half-Kelly `0.5 * f*` or Quarter-Kelly `0.25 * f*`) to scale individual position exposure based on signal win rates and payoffs, protecting the portfolio against parameter estimation errors.

#### [NEW] [backend/backtesting/backtest.py](backend/backtesting/backtest.py)
* Setup `PyBroker` backtest suite.
* **Crisis stress-testing**: Run backtests specifically on the Dot-Com, 2008, and COVID eras using price, volume, and macro features to evaluate drawdown profiles and model survival.
* Save the best-performing model artifacts inside `backend/ml_engine/saved_models/`.

---

### Stage 3: FastAPI Backend & Premium Web Interface

We expose predictions via standard REST endpoints and build an intuitive, visually stunning web UI.

#### [NEW] [backend/app/main.py](backend/app/main.py) & Endpoints
* **CORS Setup**: Configure FastAPI with `CORSMiddleware` to allow local origins (specifically `http://localhost:3000` where the Next.js dev server runs) to prevent frontend fetch blocking.
* **API Endpoints**:
  * `/api/suggestions`: Returns recommendations for today, detailing: target ticker, strategy (Short/Long term), direction (BUY/SELL/HOLD), entry, stop-loss, take-profit, confidence, and reasoning.
  * `/api/sentiment`: Supplies aggregated sentiment charts per ticker.
  * `/api/performance`: Delivers historical backtest curves and simulation metrics.

#### [NEW] [frontend/src/app/page.tsx](frontend/src/app/page.tsx) & Web App UI
* Create a **premium dark-mode dashboard** featuring:
  * Glassmorphism layout (`backdrop-filter: blur(12px)`) with glowing border highlights.
  * Custom interactive chart showing simulated strategy growth vs. standard S&P 500 performance.
  * Split suggestions pane: **Short-Term (High Volatility)** breakout alerts and **Long-Term Asset Allocation** weights.
  * Interactive "Stress Test" view showing how our current models would have navigated historical crises (2008, COVID, Dot-Com) based on our Stage 2 backtesting results.

---

### Stage 4: Risk Control & Alpaca Paper Trading Integration

This stage connects our model to live data feeds, manages risks, and executes trades.

#### [NEW] [backend/execution/executor.py](backend/execution/executor.py)
* **Execution Logic**:
  * Read recommended trades from SQLite / database.
  * Connect to the Alpaca API using API credentials.
  * Fetch current equity, buying power, and active positions.
  * Implement Risk Controls: Maximum position exposure ($10\%$ of total equity per short-term trade), stop-loss/take-profit limit orders, and fractional share sizing for long-term monthly allocations. Sizing calculations apply the **Fractional Kelly** framework.
  * Execute trades via Alpaca's REST endpoint.

#### [NEW] [backend/execution/scheduler.py](backend/execution/scheduler.py)
* Run `APScheduler` background service.
* **Model Lifecycle**: Models are not retrained daily. The short-term XGBoost classifier and HMM are retrained on a weekly cadence (every Sunday before market open) using the latest rolling 2-year window. Daily runs at 09:15 ET load the most recently saved model artifacts from `backend/ml_engine/saved_models/` and run inference only. A manual retraining trigger via `run.py` is also available for out-of-cycle refreshes.
* **Schedule** (all times Eastern Time, ET — UTC-5 in winter, UTC-4 in summer):
  * **09:00 ET**: Daily data fetch (prices, macro indicators, and news sentiment refresh).
  * **09:15 ET**: Run ML/HMM models to update daily trade recommendations.
  * **09:45 ET**: (15 minutes after Market Open, to avoid opening auction volatility and wide spreads) Send execution orders to Alpaca.
  * **15:45 ET**: Portfolio update (read equity values and log performance).

---

## Verification Plan

### Automated / Semi-Automated Tests
1. **Data Ingestion Verification**: Run Python integration test scripts to verify yfinance, FRED, and news fetchers populate the SQLite database.
2. **Look-Ahead Bias Check**: Before trusting any backtest result, verify that no feature column for row T contains data from T+1 or later. Concretely: assert that the feature matrix constructed by `features.py` on a truncated dataset (data up to date X) is identical to the corresponding rows of the full feature matrix. Any discrepancy indicates a leakage bug.
3. **Backtest Runner**: Verify the backtest completes and outputs performance metrics (Sharpe, drawdown) to console. Cross-check short-term model performance against a naive baseline (e.g., always predict the S&P 500 direction) — if the model dramatically outperforms on in-sample data, re-examine for leakage before declaring success.
4. **API Integration Test**: Test FastAPI endpoint latency and JSON structures via curl/Postman.
5. **UI Visual Polish**: Spin up Next.js dev server and check dashboard functionality, charts loading, and mobile responsiveness.

### Manual Verification
- Deploy paper trading keys to the backend environment, run a dry-run execution script, and verify that mock orders are sent to the Alpaca developer dashboard.
