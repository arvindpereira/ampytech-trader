# Architecture

## 1. Processes & ports

The system is a local monorepo of independent processes that all share one SQLite file.

```mermaid
flowchart TB
    subgraph User["Developer machine"]
        FE["Next.js dashboard<br/>localhost:3002"]
        BE["FastAPI / Uvicorn<br/>localhost:8008<br/>(app.main:app)"]
        SCH["APScheduler daemon<br/>(execution/scheduler.py)<br/>optional, blocking"]
        CLI["run.py CLI<br/>fetch / train / backtest /<br/>simulate / backtest-virtual"]
        DB[("SQLite<br/>backend/data/<br/>trading_system.db")]
        MODELS["ml_engine/saved_models/<br/>*.json *.pkl *.pth"]
    end
    subgraph Ext["External services (optional / keyed)"]
        MASS["Massive API<br/>prices, news, treasury yields"]
        YAH["Yahoo Finance<br/>pre-2022 + crisis eras"]
        RED["Reddit (PRAW)"]
        NEWS["NewsAPI / Finnhub"]
        ALP["Alpaca paper API"]
    end

    FE -->|REST /api/*| BE
    BE <--> DB
    BE --> MODELS
    CLI --> DB
    CLI --> MODELS
    SCH --> DB
    SCH --> MODELS
    CLI -->|fetch| MASS & YAH & RED & NEWS
    SCH -->|fetch| MASS & YAH & RED & NEWS
    BE -.->|/api/reconcile, live exec| ALP
    SCH -.-> ALP
```

Key facts:
- **One SQLite DB** (`backend/data/trading_system.db`) is the single source of truth shared by every
  process. `check_same_thread=False`; FastAPI uses a per-request session.
- The backend serves **both** the data/suggestions API *and* a fake Alpaca broker under
  `/api/virtual_alpaca/v2/*`. The executor talks to that fake broker over HTTP (`localhost:8008`) using
  the real `alpaca_trade_api` client, so the same code path can later point at real Alpaca.
- Models are plain files loaded lazily by the API on each `/api/suggestions` call (with an in-memory
  result cache keyed on data state).
- The scheduler is **optional** and **not required** for the dashboard; it only matters for unattended
  daily fetch/train/execute.

## 2. Component responsibilities

```mermaid
flowchart LR
    subgraph data_ingestion
        price_fetcher.py
        macro_fetcher.py
        sentiment_fetcher.py
        crisis_fetcher.py
    end
    subgraph ml_engine
        features.py
        models.py["models.py<br/>XGBoost + HMM + MPT + Kelly"]
        deep_models.py["deep_models.py<br/>GRU+Attention"]
    end
    subgraph app
        main.py["main.py<br/>all FastAPI routes"]
        config.py
        database["database/ (models, connection)"]
    end
    subgraph execution
        executor.py["executor.py<br/>order placement, sizing,<br/>grid rebalance, reconcile, daily eval"]
        simulator.py["simulator.py<br/>forward sim + historical replay"]
        scheduler.py
    end
    BT["backtesting/backtest.py"]

    data_ingestion --> database
    features.py --> models.py & deep_models.py
    models.py & deep_models.py --> main.py
    main.py --> execution
    database --> ml_engine & app & execution & BT
```

| Module | Responsibility | Notes / gotchas |
| :-- | :-- | :-- |
| `data_ingestion/*` | Pull prices, macro, sentiment, crisis data into SQLite | Sources differ from README (see [data-pipeline.md](./data-pipeline.md)) |
| `ml_engine/features.py` | Build all features for one ticker + cross-ticker features | Computes stationary technical indicator ratios and Parkinson Volatility |
| `ml_engine/models.py` | Train XGBoost + HMM; MPT optimizer; Kelly sizing | MPT = SciPy SLSQP Solver maximizing Sharpe under dynamic constraints |
| `ml_engine/deep_models.py` | Train GRU+Self-Attention sequence classifier | Fully integrated sequence predictor (seq_len=10) |
| `app/main.py` | **Every** API route incl. suggestions + virtual broker | 1,150 lines; also computes suggestions inline |
| `execution/executor.py` | Sizing, bracket orders, long-term grid, Alpaca reconcile, daily stop eval | Talks to virtual broker over HTTP |
| `execution/simulator.py` | Forward sim & day-by-day historical replay | Drives executor; toggles global `sim_date.txt` |
| `backtesting/backtest.py` | PyBroker short-term backtest + manual MPT backtest | Separate from virtual-broker replay |
| `execution/scheduler.py` | APScheduler cron for daily fetch/infer/execute + weekly retrain | Optional daemon |

## 3. End-to-end: how a suggestion is produced

```mermaid
sequenceDiagram
    participant UI as Dashboard
    participant API as FastAPI /api/suggestions
    participant DB as SQLite
    participant MdL as Saved models

    UI->>API: GET /api/suggestions?mode=real
    API->>DB: latest price/sentiment dates + counts (cache key)
    alt cache hit
        API-->>UI: cached result
    else compute
        API->>DB: load 90d prices, macro, sentiment
        API->>MdL: load PyTorch (.pth) or XGBoost (.json) + HMM (.pkl)
        API->>API: build_all_features() per ticker
        API->>API: HMM regime from SPY vol+macro
        loop each ticker
            API->>API: predict breakout prob → BUY/SELL/HOLD + stop/target
        end
        API->>API: MPT weights from 252-row returns, scaled by regime
        API-->>UI: {regime, short_term_suggestions[], long_term_allocation[]}
    end
```

Thresholds (in `app/main.py`): **BUY if prob ≥ 0.15**, **SELL if prob ≤ 0.02**, else HOLD (config-driven).
Stop-loss = `clip(2·ATR/close, 1.5%, 5%)`, take-profit = `2.5 × stop`.

## 4. Execution / simulation flow

```mermaid
sequenceDiagram
    participant Sim as simulator.py
    participant API as FastAPI (suggestions + virtual broker)
    participant Exec as executor.py
    participant DB as SQLite

    Sim->>API: set_sim_date(T)  (writes data/sim_date.txt)
    Sim->>API: get_daily_suggestions(date=T-1)  (look-ahead free)
    Sim->>Exec: execute_alpaca_live_paper_trade(api, suggestions)
    Exec->>API: POST /api/virtual_alpaca/v2/orders (fills at open of T)
    API->>DB: write VirtualOrder + VirtualPosition, debit cash
    Exec->>Exec: long-term grid/tranche rebalance
    Sim->>Exec: evaluate_virtual_broker_daily(T)
    Exec->>DB: check stop/target vs day-T high/low, mark equity, log BrokerPerformanceLog
    Sim->>API: set_sim_date("")  (clears replay mode)
```

> **Concurrency caveat:** `sim_date.txt` is a **global** server flag. While a simulation/replay runs,
> the live dashboard's broker endpoints also switch into replay-as-of-T for everyone hitting the server.
> See [execution-and-simulation.md](./execution-and-simulation.md).
