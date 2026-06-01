# Ampytech Trader: Product Manual & Workflow Documentation

Welcome to **Ampytech Trader**, a local, machine-learning-driven stock trading dashboard and simulation platform. This system utilizes advanced predictive modeling (XGBoost), market regime classification (Hidden Markov Models), Modern Portfolio Theory (MPT) asset allocation, and an interactive Virtual Alpaca Paper Broker to test and refine algorithmic strategies look-ahead free.

This document serves as your complete product manual, explaining the architecture, setup, CLI commands, and step-by-step workflows.

---

## 🗺️ System Architecture Overview

The system is organized as a clean monorepo containing a Python backend and a React/Next.js frontend:

```
ampytech-trader/
├── backend/
│   ├── app/                      # FastAPI Backend Server & Endpoints
│   │   ├── main.py               # Entry point, CORS setup, and API routes
│   │   ├── api/                  # Core endpoint handlers
│   │   └── database/             # SQLite schema (trading_system.db) & SQLAlchemy models
│   ├── data_ingestion/           # Ingestion pipelines
│   │   ├── price_fetcher.py      # Recent rolling 2-year yfinance daily bar fetcher
│   │   ├── macro_fetcher.py      # FRED economic indicator fetcher (Fed Funds, Yield Spread)
│   │   ├── crisis_fetcher.py     # Price history fetcher for historic market crisis eras
│   │   └── sentiment_fetcher.py  # Reddit (PRAW), NewsAPI, and Premium news folder scanner
│   ├── ml_engine/                # Machine learning training & feature logic
│   │   ├── features.py           # Engineered features (technicals, macro, and sentiment SMAs)
│   │   └── models.py             # XGBoost Breakout and HMM-based MPT Portfolio Allocator
│   ├── execution/                # Paper trade executor & scheduler
│   │   ├── executor.py           # Sizing (Fractional Kelly) and stop-loss check loop
│   │   └── scheduler.py          # Daily APScheduler loop (fetches data, updates recommendations)
│   ├── run.py                    # Master CLI command runner
│   └── requirements.txt          # Python dependencies
└── frontend/                     # Next.js & React Dashboard Web Interface
    ├── src/app/page.tsx          # Dashboard, Universe Editor, and Sentiment Verification
    └── src/app/globals.css       # Premium glassmorphic design system and custom CSS
```

---

## ⚡ Getting Started (Local Development Setup)

Follow these steps to initialize the database, train the models, and launch the dashboard.

### 1. Backend Server Setup
From the `backend/` directory:
```bash
# Navigate to the backend folder
cd backend

# Create a virtual environment and activate it
python3 -m venv venv
source venv/bin/activate

# Install required dependencies
pip install -r requirements.txt

# Initial data fetch (downloads 2-year stock prices, FRED indicators, and seeds sentiments)
python run.py fetch

# Train the ML models (XGBoost Breakout + Hidden Markov Model)
python run.py train

# Spin up the FastAPI server on port 8008
python run.py serve
```

### 2. Frontend Interface Setup
From the `frontend/` directory (open a new terminal tab):
```bash
# Navigate to the frontend folder
cd frontend

# Install Node dependencies
npm install

# Start the Next.js dev server on port 3002
npm run dev -- -p 3002
```
Now, open your web browser and navigate to: **`http://localhost:3002`**

---

## ⚙️ CLI Reference Table

All backend operations are centralized under the `backend/run.py` master utility script:

| CLI Command | Purpose | When to Use |
| :--- | :--- | :--- |
| `python run.py fetch` | Refreshes recent stock prices, FRED macro indices, scans for premium news, and processes active sentiment feeds. | Daily, before market open (around 09:00 ET). |
| `python run.py train` | Retrains both the short-term XGBoost classifier and the long-term HMM model on the latest 2-year rolling window. | Weekly (every Sunday) or after major strategy configuration changes. |
| `python run.py serve` | Launches the FastAPI backend API on `http://localhost:8008`. | Required to run concurrently with the frontend dashboard. |
| `python run.py backtest` | Executes a historical backtest of the ML strategies via PyBroker and prints overall return, Sharpe ratio, and drawdown. | To audit theoretical strategy performance over the past 2 years. |
| `python run.py simulate --days N` | Valuates the Virtual Broker forward using live cached prices for `N` trading days. | Daily, to watch how recommendations update and fill in real-time. |
| `python run.py backtest-virtual --months N` | Replays `N` months of historical trading day-by-day inside the Virtual Broker (fills at next-day open). | To stress-test the executor's look-ahead free stop-loss/take-profit bracket orders. |

---

## 📖 Key Workflows Guide

Here is a step-by-step product guide for each core workflow of the platform.

### Workflow 1: Daily Ingestion & Automated Model Predictions
This workflow updates the database with the latest market indicators and prepares trading recommendations for the day.

1. **Scheduling**: In live production, the APScheduler daemon (`python run.py schedule`) handles this automatically.
2. **Data Syncing**: The pipeline runs `fetch`, querying `yfinance` for daily OHLCV bars and the FRED API for the latest daily interest rates and yield spreads.
3. **Rate Limit Protection**: The fetcher checks SQLite before hitting external APIs. If today's market bars and news sentiment metrics are already cached, it skips external requests, preventing free-tier API lockout.
4. **Inference Execution**:
   - The HMM model evaluates macro volatility to classify the current market regime (as `growth`, `transition`, or `crisis`).
   - The short-term XGBoost model runs predictions for all universe tickers, calculating the probability of a $\ge 2\%$ breakout in the next 3 days.
5. **Dashboard Reflection**: Refreshing the dashboard shows the updated suggestions under the **"Suggestions Dashboard"** tab.

---

### Workflow 2: Understanding Dashboard Strategy Suggestions
Open the dashboard and look at the **"Daily Strategy Recommendations"** panel. It has two strategies:

#### A. Short-Term Volatility (Breakout Signal Table)
* **Goal**: Target short-term price expansions.
* **Mechanism**: Displays a table of target tickers, current close prices, ML recommendation (`BUY`, `SELL`, `HOLD`), confidence percentage (XGBoost probability), Stop-Loss, and Take-Profit limits.
* **Kelly Sizing**: When executing, the system uses a **Fractional Kelly Criterion** (Half-Kelly) that scales position sizes based on historical signal win rates, capping risk at a maximum of `10%` of equity per position.

#### B. Long-Term MPT Weights (Regime Allocator)
* **Goal**: Optimize long-term asset weights for stable growth.
* **Mechanism**: Solves for weights maximizing the Sharpe Ratio under the current HMM market regime.
* **Crisis Covariance**: During `crisis` regimes, the optimizer replaces standard covariance matrices with stable matrices derived from historical recessions (using Ledoit-Wolf shrinkage on GFC and Dot-Com crash data). This tilts the portfolio towards defensive sector ETFs and cash to minimize drawdowns.

---

### Workflow 3: Virtual Broker Replays & Forward Simulations
The **Virtual Alpaca Paper Trading Broker** allows you to test trading models on a simulated account starting with **$100,000.00**.

#### Running a Historical Replay (Look-Ahead Free Backtest)
1. Navigate to the **"Universe & Portfolio Editor"** tab on the web app.
2. Under the **"Replay & Simulation Engine"** card, select the number of months (e.g., `6`) and click **"Run Replay"** (or run `python run.py backtest-virtual --months 6` in your terminal).
3. **Execution Logic**:
   - The system loops day-by-day.
   - For any trading date $T$, features are engineered strictly using data up to $T-1$ (preventing future information leakage).
   - If the model triggers a buy, the virtual broker simulates a purchase filling at the **open price of day $T$**.
   - If the stock gaps down past your stop-loss overnight, the stop checker simulates a fill at the stop-loss boundary to protect your cash.
4. Compare performance: Go to the **"Virtual Broker Performance"** tab. The Recharts chart plots your **Strategy Portfolio** equity curve against `SPY` (S&P 500), `QQQ` (Nasdaq 100), and Berkshire Hathaway (`BRK-B`).

---

### Workflow 4: Customizing Universe, Cash, and Asset Policies
You can adapt the simulator to match your real-world portfolio holdings and preferences.

1. Navigate to the **"Universe & Portfolio Editor"** tab:
   - **Strategy Universe**: Add new stock tickers (e.g. `TSM`, `LLY`) or remove existing ones. This updates the pool evaluated by the ML models.
   - **Cash Account Balance**: Type a custom cash value and click **"Set Cash Balance"** to reset your starting capital.
   - **Portfolio Asset Policies**: Add assets you already own by typing the ticker, shares owned, and cost basis. Assign an **Execution Policy**:
     * **`Rebalance`**: The optimizer will dynamically buy or sell shares to match MPT ML weights during rebalances.
     * **`Lock`**: The virtual broker will protect these shares. It will never trade them, maintaining your long-term position.
     * **`Liquidate`**: The broker will automatically submit a market sell order for these shares on the next cycle, returning the capital to cash.

---

### Workflow 5: Sentiment Verification & Premium Feed Ingestion
To verify *why* a model made a specific prediction based on public or private sentiment data:

#### A. Verifying Sentiment Source Logs
1. Click the **"Suggestions Dashboard"** tab.
2. In the right-hand sidebar under **"Social Sentiment Index"**, click any ticker (e.g. `TSLA` or `AAPL`).
3. The **"Sentiment Source Inspector"** will automatically retrieve the underlying database logs for that ticker.
4. You can view:
   - The source tag (`reddit`, `news`, or `premium`).
   - The individual post or article headline.
   - The exact VADER polarity score (e.g., `+0.62` compound).
   - Click **"Verify Source Link"** to open the source URL directly (such as the Reddit permalink or news article).

#### B. Ingesting Paywalled Articles (The Information)
If you subscribe to premium services like "The Information" or "Bloomberg" and want the models to incorporate speculative or exclusive pre-public analysis:

##### Method A: Manual Paste in Web UI
1. Scroll down to the **"Premium Feed Ingestion"** card on the dashboard.
2. Select the target stock ticker from the dropdown.
3. Paste the headline/title and copy-paste the paywalled text block into the preview box.
4. Input a source link for your future verification records.
5. Click **"Ingest & Analyze Feed"**.
6. **What happens**: The backend processes the text, computes its VADER compound sentiment score, logs the item under source type `premium` in `SentimentSourceLog`, and instantly recalculates the daily sentiment aggregates.

##### Method B: Drop Files in Folder
1. Drop text files directly into the directory: `backend/data/premium_news/` (e.g., save a file named `THEINFO_AAPL_2026-06-01.txt`).
2. Run `python run.py fetch` in your terminal.
3. The folder scanner will automatically parse the file, calculate the sentiment polarity, update the aggregates, and archive the processed file to `premium_news/archive/` tagged by date.

---

## 🔒 Look-Ahead Bias Mitigation Rules

To guarantee that your backtest matches real-world execution, the system implements two strict mathematical rules:

1. **Shift Rule ($T-1$)**: Technical indicators (RSI, MACD, Volume) and sentiment metrics (moving averages) for a trading day $T$ are calculated using only closing values up to day $T-1$. Predictions are made before the market opens on day $T$.
2. **Next-Day Open Fill**: The Virtual Broker does not fill buy orders at the close price of day $T-1$ (which is a common backtesting bug). Orders are filled at the **Open price of day $T$**, incorporating realistic execution gap slippage.

---

## 🎨 Glassmorphic Theme Design Tokens

If you wish to edit colors or visual components in `frontend/src/app/globals.css`, these variables define the design:

* **`--bg-dark`**: `#070913` (Deep space blue background)
* **`--bg-card`**: `rgba(16, 20, 38, 0.6)` (Semi-transparent card backing)
* **`--border-glass`**: `rgba(255, 255, 255, 0.06)` (Subtle borders)
* **`--border-glow`**: `rgba(0, 242, 254, 0.15)` (Glowing cyan outline on hover)
* **`--color-buy`**: `#00F2FE` (Cyan indicators for buy/bullish sentiment)
* **`--color-sell`**: `#FF4B6E` (Pinkish red indicators for sell/bearish sentiment)
* **`--color-gold`**: `#F59E0B` (Amber gold for premium uploader elements)
