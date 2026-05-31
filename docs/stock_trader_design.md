# Stock Trading System Architecture & Implementation Options

To build a robust trading system that delivers both **short-term (high-volatility/momentum)** and **long-term (steady growth)** suggestions, we must integrate market data, macro indicators, social/news sentiment, historical backtesting, and machine learning models. 

This document explores the architectural options, technologies, and data pipelines to train, build, and deploy this system.

---

## 1. System Architecture Overview

A modern quantitative trading system consists of five core components:

```mermaid
graph TD
    DataPipeline[1. Data Ingestion & Prep] --> ML_Engine[2. ML & Sentiment Engine]
    ML_Engine --> Backtester[3. Backtesting Framework]
    Backtester --> RiskExecution[4. Risk Management & Execution]
    RiskExecution --> UserDashboard[5. Premium UI Dashboard]
    
    subgraph Data Sources
        Reddit[Reddit APIs / News API]
        FRED[Macro FRED API]
        Historical[Price/Volume Data]
    end
    
    Data Sources --> DataPipeline
```

---

## 2. Core Components & Technical Options

### A. Data Ingestion (Prices, Macro, & Volume)
We need high-quality historical data for model training/backtesting, and real-time APIs for live signal generation.

| Service | Best For | Pros | Cons |
| :--- | :--- | :--- | :--- |
| **yfinance** | Free historical research | Extremely easy to use; no API keys or setup required. | Lacks reliable real-time WebSockets; scraping-based (subject to Yahoo rate-limiting). |
| **Alpaca Market Data API** | Paper/Live integration | Free tier available; native Python SDK; unified with execution API; real-time WebSockets. | Free tier has rate limits and 15-min delay on certain indicators. |
| **FRED API (Federal Reserve)** | Macroeconomic indicators | Free API access to US interest rates, yield curve spreads, and inflation indices. | Requires free API key; daily series (interest rates, yield curve) updated with a 1-2 business day lag; not suitable for intraday use. |
| **Polygon.io** | High-fidelity institutional data | Ultra-fast; comprehensive tick-level historical data. | Expensive for retail developers ($29–$199+/mo). |
| **EODHD APIs** | Global coverage & Fundamentals | Vast range of data (fundamental, historical, sentiment, alternative data). | Paid subscription required. |

* **Recommendation**: Use **yfinance** for local historical research, **FRED API** for macro-economic context, and **Alpaca** for real-time paper trading and current price ingestion.

---

### B. Sentiment & Internet Scouring (X, News, Reddit)
Sentiment analysis will drive our **short-term, high-volatility strategies**. Social signals on high-momentum stocks correlate with near-term retail price volume spikes.

#### 1. News & Social Media Data Extraction
* **Official X API (v2)**: High compliance and reliability, but extremely expensive (virtually no free tier for data harvesting).
* **Reddit Data (via PRAW)**: The official Python Reddit API Wrapper provides access to active retail communities (`r/wallstreetbets`, `r/stocks`) for tracking retail interest and ticker mention frequency. (Requires application Client ID and Client Secret; read-only credentials are sufficient).
* **NewsAPI.org / Finnhub.io**: news headline fetchers. Note: news API free tiers restrict lookups to the past 30 days and limit requests to 100/day. Python fetchers must local-cache requests to prevent daily rate-limit exhaustion during testing.
* **Alternative: Pre-calculated Sentiment APIs**: EODHD or Stocktwits API provide aggregated sentiment scores per ticker, bypassing raw text cleaning and NLP overhead.

#### 2. Natural Language Processing (NLP) Models
To convert raw text (tweets/news) into a numerical sentiment score (e.g., `-1.0` to `+1.0`):
* **FinBERT**: A specialized BERT model pre-trained on financial communication (ideal for news).
* **VADER Sentiment**: Extremely fast, rule-based, and highly tuned for social media text (slang, emojis).
* **LLM APIs (e.g., OpenAI GPT-4o, Google Gemini 2.5)**: Best for complex reasoning (e.g., summarizing an entire earnings report or judging a tweet's macro impact).

* **Recommendation**: Gather active news data using **NewsAPI** / **Finnhub**, and Reddit data using **PRAW**. Run them through **VADER** or a lightweight **FinBERT** classifier locally to keep the pipeline lightweight.

---

### C. Machine Learning Models (Strategy Engine)

To support both investment horizons, we will train distinct model structures:

```
                            ┌──► Short-Term Strategy (High Volatility)
                            │    - Features: Social Sentiment, Technical Indicators, 3-Day Price Momentum
                            │    - Models: XGBoost, Random Forests, LightGBM
                            │    - Goal: Predict next-day volatility/direction
Trading Model Pipelines ────┤
                            │
                            └──► Long-Term Strategy (Steady Growth)
                                 - Features: Macro Regimes, Sector Rotations, Trend Indexes
                                 - Models: Hidden Markov Models (HMM), Mean-Variance Portfolio Optimization (MPT)
                                 - Goal: Value investing, Trend following, Asset Allocation
```

#### 1. Short-Term Strategy: XGBoost / LightGBM on Tabular Features
* **Why**: High-volatility strategies rely heavily on custom engineered features (e.g., 3-day sentiment moving average, RSI, Bollinger Bands, social volume spikes).
* **Modeling Approach**: Classification (predicting whether a stock will rise by $\ge 2\%$ in the next 3 days).
* **Advantage**: Fast training, handles tabular data and mixed feature types (prices + sentiment scores) exceptionally well.

#### 2. Long-Term Strategy: Regime-Switching & Portfolio Optimization
* **Regime Classification (HMM)**: An unsupervised Hidden Markov Model trained on volatility and macro data (from FRED) to classify the market state (e.g., Expansion vs. High-Volatility Contraction).
* **Trend-Following Indicators**: Implement robust moving-average crossovers to establish long-term trend directions.
* **Modern Portfolio Theory (MPT) & Fractional Kelly Criterion**: Analytical optimization methods applied on top of model outputs. MPT (mean-variance optimization) constructs a diversified portfolio targeting a maximum Sharpe Ratio based on a shrinkage covariance estimator (e.g., Ledoit-Wolf); the **Fractional Kelly Criterion** (e.g., Half-Kelly) provides a conservative position-sizing formula to manage equity exposure based on historical win probabilities while buffering against estimation noise.

---

### D. Backtesting Frameworks
Before trading live, we must simulate our models on historical data.

* **PyBroker**: Native support for ML models and walk-forward analysis. Integrates cleanly with scikit-learn pipelines and handles feature caching well.
* **vectorbt**: Extremely fast, vectorized backtesting. Best for analyzing thousands of parameter combinations in seconds.

* **Recommendation**: **PyBroker** if we want a clean, modern integration with ML models (like Scikit-Learn), or **vectorbt** if we need raw speed.

---

### E. Live / Paper Trading Execution
* **Alpaca (Recommended)**: Offers a modern, developer-first JSON API for live and paper trading. No commission, supports fractional shares (great for long-term rebalancing), and has a comprehensive Python SDK.
* **Interactive Brokers (IBKR)**: Best for complex orders, options, and large scale, but the TWS API/Gateway is notoriously difficult to run and maintain.

---

## 3. Proposed Project Roadmap

We can build the application in four modular stages:

### Stage 1: Data Pipeline & Feature Engineering
* Set up a Python backend that pulls price data (`yfinance`/`Alpaca`), macro data (`FRED`), and active sentiment (`NewsAPI`/`Reddit`).
* Write a pipeline that merges price charts with rolling average sentiment scores and macro indicators.

### Stage 2: ML Training & Backtester
* Implement a local backtesting suite using `PyBroker` or `vectorbt`.
* Build and train models:
  1. **Short-Term Model**: XGBoost classifying high-momentum breakouts based on tech indicators and recent sentiment spikes.
  2. **Long-Term Model**: HMM-based regime classifier combined with Markowitz (mean-variance) portfolio optimization.
* Evaluate performance using Metrics: Sharpe Ratio, Max Drawdown, Win Rate.

### Stage 3: Python Backend & Next.js/React Frontend
* Build a local dashboard displaying daily suggestions and backtest metrics.
* Configure FastAPI with `CORSMiddleware` to allow Next.js requests from `http://localhost:3000`.
* Style the UI using modern dark-mode glassmorphism.

### Stage 4: Execution & Paper Trading
* Integrate Alpaca paper trading keys.
* Set up an automated scheduler (`APScheduler` in Python) to execute suggested trades slightly after market open and manage risk intraday.

---

## 4. Technical Stack Summary

* **Language**: Python (for Data/ML/Backtesting), Node.js/Next.js/React (for web interface)
* **ML Libraries**: `scikit-learn`, `xgboost`, `hmmlearn`, `vaderSentiment`
* **Data Sources**: `yfinance`, FRED API, Alpaca Market Data, Reddit (PRAW)
* **Backtester**: `PyBroker` or `vectorbt`
* **Execution**: Alpaca SDK
* **Database**: SQLite (provides a fast, local file-based database for development)
* **Frontend UI**: Next.js / Tailwind or Vanilla CSS, with shadcn-style component visuals, Lucide icons, and ApexCharts.

---

> [!NOTE]
> Setting up API credentials for Reddit API (PRAW), FRED, and Alpaca paper trading will be required once we begin implementing the data ingestion scripts. For local development, we can mock or use cached sentiment feeds to test our ML pipelines.

> [!TIP]
> To avoid overfitting, we will use Walk-Forward validation (training on a rolling window of 1-2 years, testing on the next 3 months, and repeating) rather than training a single model on 10 years of data.
