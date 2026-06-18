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
│   │   ├── sentiment_fetcher.py  # Reddit (PRAW), NewsAPI, and Premium news folder scanner
│   │   ├── fundamentals_fetcher.py # Polygon financials statement API fetcher
│   │   ├── premium_ingest.py     # IMAP subscriber email scraper
│   │   └── premium_llm.py        # LLM extractor for premium emails
│   ├── ml_engine/                # Machine learning training, classification & feature logic
│   │   ├── features.py           # Engineered features (technicals, macro, and sentiment SMAs)
│   │   ├── fundamental_quality.py # Quantitative fundamental quality scoring
│   │   ├── fundamental_llm.py    # LLM moat/durability quality overlay
│   │   ├── classify.py           # Risk × quality grid classification
│   │   ├── swing_alpha.py        # Core and aggressive swing XGBoost strategy engine
│   │   └── models.py             # Short-term XGBoost and HMM-based MPT Portfolio Allocator
│   ├── execution/                # Paper trade executor & scheduler
│   │   ├── executor.py           # Sizing (volatility scaling) and stop-loss check loop
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

| CLI Command / Make Target | Purpose | When to Use |
| :--- | :--- | :--- |
| `python run.py fetch` / `make fetch` | Refreshes recent stock prices, FRED macro indices, scans for premium news, and processes active sentiment feeds. | Daily, before market open (around 09:00 ET). |
| `make fundamentals` | Ingests company financials (Polygon financials API) and calculates derived balance sheet/income statement ratios. | As needed when adding new tickers or quarterly updates. |
| `make classify` | Runs the risk × quality universe classification (blending quant ratios and LLM qualitative overlay) to assign tickers to routing tiers. | Weekly or after updating company financials/LLM flags. |
| `python run.py train` / `make train` | Retrains the legacy short-term hourly XGBoost model and the daily HMM macro regime model. | Weekly (every Sunday). |
| `python run.py swing-train` / `make swing-train` | Retrains both the **Core** swing model (on core/quality_growth/unrated names) and the **Aggressive** swing model (on all names). | Weekly, after running classification. |
| `python run.py news-llm` / `make news-llm` | Scores news headlines using local Ollama (or OpenAI) to generate directional signals. | Daily or in bulk backfills. |
| `make premium-ingest` | Polls IMAP subscriber email folder for premium newsletters (e.g. The Information) and LLM-extracts ticker sentiment impact. | Daily (via scheduler) or manually via files. |
| `python run.py serve` / `make serve` | Launches FastAPI backend (port 8008) and Next.js frontend (port 3002). | Required to run the full dashboard web application. |
| `python run.py simulate --days N` / `make simulate` | Valuates the Virtual Broker forward using live cached prices for `N` trading days. | Daily, to test real-time recommendation updates. |
| `python run.py backtest-virtual --months N` / `make backtest-virtual` | Replays `N` months of historical trading day-by-day inside the Virtual Broker (fills at next-day open). | To stress-test stop-loss/take-profit brackets and capital sleeves. |

---

## 📖 Key Workflows Guide

Here is a step-by-step product guide for each core workflow of the platform.

### Workflow 1: Daily Ingestion, Classification & Model Predictions
This workflow updates the database with market indicators, company financials, and sentiment data, then generates trading recommendations.

1. **Scheduling**: In live production, the APScheduler daemon (`python run.py schedule`) handles this automatically.
2. **Data Syncing**: The pipeline runs `fetch` to retrieve price bars and macro indicators. If needed, `make fundamentals` can be run to fetch company financials (revenues, FCF, assets, etc.) from Polygon.
3. **Risk × Quality Classification**: Running `make classify` blends quantitative financial ratios and qualitative LLM overlays (e.g. flagging turnaround situations or adjusting for one-off gains) to tier the universe:
   * **Hot (quality_growth)**: Strong fundamentals, high volatility.
   * **Solid (core)**: Solid fundamentals, low volatility.
   * **Long-shot (speculative)**: Weak fundamentals, high volatility.
   * **Cold (value_trap)**: Weak fundamentals, low volatility (to be avoided).
4. **Inference Execution**:
   * The HMM model evaluates macro volatility to classify the current market regime (as `growth`, `transition`, or `crisis`).
   * The swing strategy runs predictions using two separate XGBoost models:
     * **Core Model**: Trained on core, quality_growth, and unrated tickers. Used to trade stable, quality companies.
     * **Aggressive Model**: Trained on all universe tickers. Used to find breakout signals in speculative ("Long-shot") tickers.
5. **Dashboard Reflection**: Refreshing the dashboard shows the updated suggestions, displaying visual badges representing each stock's classification tier.

---

### Workflow 2: Understanding Dashboard Strategy Suggestions
Open the dashboard and look at the **"Daily Strategy Recommendations"** panel. It has two main strategies:

#### A. Swing + News Suggestions (Split Core vs. Aggressive Sleeves)
* **Goal**: Target multi-day price expansions driven by technical trends and LLM-scored news sentiment.
* **Mechanism**: Displays recommendations (`BUY`, `HOLD`) and conviction percentages, split into:
  * **Core Swing**: Suggester signals for Solid and Hot tickers using the Core swing model.
  * **High-Risk Sleeve**: Speculative ("Long-shot") tickers evaluated by the Aggressive swing model.
* **Volatility Sizing**: Instead of a raw fixed 10% sizing, position sizes are dynamically scaled downward for high-volatility names using a volatility target scaling factor: `min(1.0, SWING_VOL_TARGET / name_vol)` (where `SWING_VOL_TARGET` = 0.35). This prevents volatile names from dominating the portfolio risk. The total allocation to the speculative `high_risk` sleeve is hard-capped at 5% of total equity.

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
