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

The Makefile is **dependency-aware and cache-aware**: each pipeline step stamps `backend/.make/` and is skipped while still fresh (per-step TTL), so the everyday targets below can be re-run freely and only stale work executes. `FORCE=1` rebuilds everything; `make clean-cache` clears the stamps. Most operations ultimately run through the `backend/run.py` master utility script.

| CLI Command / Make Target | Purpose | When to Use |
| :--- | :--- | :--- |
| `make up` | Bring up the **whole system in order**: refresh data → train all served models (in dependency order, cache-aware) → launch backend + frontend + scheduler. | The one command to start working; same-day runs are a fast no-op on data/models. |
| `make data` | Run **all** core data fetches as one cached step (prices hourly+daily, macro, crisis eras, sentiment, CAPE/valuation, market-stress). `make fetch` forces a refresh now. | Whenever you want fresh inputs; cheap when already fresh. |
| `make train` | Train **all served models**: HMM regime + short-term XGBoost + Core/Aggressive swing (pulling `fundamentals`/`classify`/`news-recent` as deps), then refresh crash odds. Depends on `data`. | After new data, or weekly. `make train-deep` adds the optional PyTorch net. |
| `python run.py fetch` / `make fetch` | Force-refresh recent stock prices, FRED macro indices, sentiment, valuation, and market-stress inputs (invalidates the data cache). | Daily, before market open (around 09:00 ET). |
| `make fundamentals` | Ingests company financials (Polygon financials API) and calculates derived balance sheet/income statement ratios. | As needed when adding new tickers or quarterly updates. |
| `make classify` | Runs the risk × quality universe classification (blending quant ratios and LLM qualitative overlay) to assign tickers to routing tiers. | Weekly or after updating company financials/LLM flags. |
| `make train-core` (`python run.py train` adds the PyTorch net) | Lower-level: retrain just the short-term hourly XGBoost + daily HMM regime models. Prefer `make train` (above) for the full served set. | When iterating on only the core models. |
| `python run.py swing-train` / `make swing-train` | Retrains both the **Core** swing model (on core/quality_growth/unrated names) and the **Aggressive** swing model (on all names); cache-aware, depends on `classify` + `news-recent`. | Pulled in automatically by `make train`. |
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

### Workflow 6: Monitoring Stress & Defensive Stances (Crash Radar)
The **Crash Radar** (Tab 5) serves as a risk management console that quantifies systemic market stress look-ahead free and blends allocation postures to protect capital.

#### 1. Out-of-Sample Risk Timeline
- **Visual Heatmap**: The card at the top displays a 5-year weekly line/area chart of the out-of-sample Composite Crash-Risk Index.
- **Severity Coloring**: The line and fill colors shift dynamically based on thresholds: **Calm** ($<40$, green), **Elevated** ($40-65$, blue), **High** ($65-80$, orange), and **Extreme** ($\ge 80$, red), letting you immediately identify historical crisis regimes (such as the 2022 bear market).

#### 2. Risk Metrics & Sigmoid Knobs
- **Composite Gauge**: Evaluates the real-time index score (from 0 to 100) alongside systemic trigger reasons (e.g., CAPE valuation percentile, inverted yields, or credit widening).
- **Glide-Path Policy Curve**: Use the sliders to customize:
  - **De-risking Threshold ($\theta$)**: Standardized score above which de-allocation of equities begins.
  - **Steepness ($k$)**: Speed of transitioning equity capital into cash.
  - **Trend Gate Strength ($\gamma$)**: Extent to which active uptrends (SPY above 200 SMA) offset de-risking actions.
  - Select presets (**conservative**, **balanced**, **aggressive**) to quickly adjust policy sensitivities.

#### 3. Strategic Playbook & Stance Rebalancing
- **Stance Overview**: Displays target cash buffers and safe asset mixes (automatically routing between a *Stagflation* branch containing Gold/TIPS/Commodities and a *Deflationary Bust* branch containing TLT/Cash depending on breakeven inflation). Safe-asset chips show **real tickers** (e.g. `GLD`, `TLT`, `BIL`, `TIP`) with their latest prices.
- **Custodian Checklist**: Details real-world custody precautions (e.g. FDIC bank limits, short Treasury bill holdings, SIPC limits) matching the current drawdown severity.
- **Preview-Then-Apply Rebalance**: Clicking **"Preview Rebalancing"** runs a **read-only** plan (`GET /api/crash/apply/preview`) that diffs your current paper holdings against the target stance weights — using your active glide-path knobs — and shows a validation summary plus the exact buy/sell orders (symbol, side, shares, real price) **before** anything executes. Only after reviewing does the gated **"Confirm & Apply (Paper)"** button place those orders on the Alpaca paper account. Nothing touches your portfolio until you confirm.

#### 4. Forecasts & Scenario Wargame
- **Purged CV Forecast (Experimental Drawdown Odds)**: Trigger a background job to run the regularized logistic drawdown-odds model, using Marcos López de Prado's Purged and Embargoed CV to estimate risk probability over 30/90/180 days. Odds are projected onto a logically-coherent grid (deeper drawdowns never more likely than shallower ones) and labeled with cross-validated AUC so you can see when the model has little skill beyond the base rate.
- **Scenario Wargame (policy comparison)**: Click **"Run Scenario Wargame"** to replay every defensive policy — from doing nothing (Buy & Hold) through static blends to fully glide-path-defensive, including your own custom knobs — across real bear markets (Dot-Com, GFC, COVID, 2022) and synthetic crashes. Instead of a raw grid, results render as **per-scenario equity-curve timelines** plus a ranked metrics table (return, max drawdown, Sharpe, turnover) versus a perfect-foresight ceiling. Read-only; never changes your portfolio.
- **AI Wargame Analyst**: An OpenAI-backed analyst (mirroring the Tab-2 evaluation interpreter) summarizes the comparison in plain English — what the knobs mean, how each strategy behaved, regime insights, and a "best for you" verdict.
- **Cached results & freshness indicators**: The last scenario comparison and analyst summary are **cached to disk** and shown by default on load (so the analyst isn't re-billed on every visit). Each card shows a **"Last updated / Next auto-update"** badge; the analyst is flagged **stale** when new input data has arrived since it was generated. The comparison auto-refreshes via the data-gated scheduler job; the analyst is regenerated on demand.

---

### Workflow 7: External Portfolio Manager (Manual Execution & Reconciliation)
The **External Portfolio Manager** (Tab 6) allows you to track and balance your external Vanguard and Robinhood accounts manually, using the platform's MPT allocations and swing signals.

#### 1. Ingesting Statement PDF Files
- **Initial Positions Ingestion**: Drag and drop your cost-basis/held-assets PDF report from Vanguard or Robinhood. The system parses the document (regex with LLM fallback), seeds your tax lots, and sets the initial cash balance.
- **Drawer Layout (Tax Lots)**: Your holdings are presented in a consolidated list. Click the arrow icon on any ticker to expand the drawer and inspect individual tax lots (cost basis, acquisition date, notes).

#### 2. Risk Profiles & Suggested Trades
- **Account-Specific Sensitivities**: Select the risk profile (`conservative`, `balanced`, or `aggressive`) for each account individually.
- **Manual Recommendations**: The engine computes recommended buy/sell orders (Day limits for technical breakouts, 90-day GTC limit orders for long-term rebalancing) matching the account's specific risk posture and the active Glide Path.
- **Manual Confirmations**: Execute the suggested order on your broker, then click **"Confirm Fill"** in the UI to manually log the transaction price and execution date. Local tax lots (FIFO deduction on sales) and cash are updated instantly.

#### 3. Monthly PDF Reconciliation
- **Deduplicating Trades**: Upload your monthly transaction history PDF. The system cross-references the imported entries against your manually confirmed fills to de-duplicate matching trades.
- **Importing Unrecorded Trades**: Any external transaction found in the PDF that wasn't logged in the app is imported as a new transaction and used to adjust your local cash and holdings automatically.

---

### Workflow 8: Data Safety & Commit-Stamped Backups
The large SQLite database lives outside Git; instead it (and the non-DB artifacts) are backed up to a Google Drive folder as **commit-stamped** snapshots, so a restore can always be matched to the code version that produced it.

- `make backup` — uploads two artifacts: the **database** (`trading_system_<ts>__<sha>.db`) and a **files zip** (`trading_files_<ts>__<sha>.zip`). The files zip contains trained models (`ml_engine/saved_models/`), archived premium news, and **all cached JSON in `backend/data/`** — including the Crash Radar forecast state (`crash_forecast_state.json`), the cached scenario wargame + AI analyst (`wargame_cache.json`), IPO markers, LLM pricing, and premium-ingest state. The secret OAuth token (`gdrive_token.json`) is explicitly excluded.
- `make restore` / `make restore-commit` — restores the newest backup (or the newest one matching the current commit). Existing files are moved aside to `*.pre-restore` first.
- `make db-backup-list` / `make files-backup-list` — list available snapshots with their commit and timestamp.
- `make backup BACKUP_KEEP=10` — after uploading, prune all but the newest N snapshots.

Auth uses an OAuth "Desktop app" client (`GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` in `.env`); the token is cached locally and refreshed automatically.

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
