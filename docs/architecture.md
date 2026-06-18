# Architecture

## 1. Processes & ports

A local monorepo of independent processes sharing one SQLite file.

| Process | Command | Port | Role |
| :-- | :-- | :-- | :-- |
| FastAPI backend | `make serve-backend` (`run.py serve`, uvicorn `--reload`) | 8008 | suggestions, portfolio, strategy/eval/suggest jobs, virtual broker, health |
| Next.js frontend | `make serve-frontend` | 3002 | dashboard (3 tabs) |
| Scheduler daemon | `make schedule` | — | daily + intraday + weekly cron jobs (see [operations.md](./operations.md)) |
| Ollama | external (local) | 11434 | LLM news scoring for the swing model |
| Ingestion / training / backup | `run.py` / `scripts/db_backup.py` (one-shot) | — | fetch, train, swing-train, db-backup |

All Python shares `backend/` (FastAPI app, `ml_engine/`, `data_ingestion/`, `execution/`).

## 2. Components

```mermaid
flowchart TB
    subgraph backend
        cfg[app/core/config.py]
        db[(SQLite)]
        class[ml_engine/classify.py]
        feat[ml_engine/features.py]
        swing[ml_engine/swing_alpha.py<br/>Core & Aggressive]
        lt[ml_engine/longterm_alpha.py]
        models[ml_engine/models.py<br/>XGBoost · HMM]
        sugg[ml_engine/strategy_suggester.py]
        evalm[ml_engine/evaluate.py]
        api[app/main.py<br/>FastAPI]
        exe[execution/executor.py]
        sch[execution/scheduler.py]
    end
    di[data_ingestion/*] --> db
    db --> class & feat
    class -.tiers.-> swing
    feat --> swing & lt & models
    swing & models -.saved_models/*model.json,pkl.-> api
    db --> api
    api --> sugg & evalm
    api --> exe --> alp[Alpaca paper]
    sch --> di & api & exe
    ui[Next.js UI] <--> api
```

## 3. Key request flows

- **`GET /api/suggestions`** → load recent + daily data → HMM regime → build features → run swing inference (`build_swing_signals`) using both the Core model (for Hot, Solid, and Unrated tickers) and the Aggressive model (for Speculative tickers) + MPT weights (`PortfolioOptimizer`) → `{regime, swing_suggestions, high_risk_suggestions, long_term_allocation, short_term_suggestions}`. Cached on data freshness.
- **Suggester / validation / evaluation** → `POST` starts a **background job** (in-process registry) → the UI polls `…/result?job_id` for progress then results.
- **Execution** (`run_execution`, scheduler or manual) → sync broker → read suggestions + buckets + per-ticker strategy + regime overlay + high-risk sleeve limits (restricted to Speculative tickers under the Aggressive model suggestions and capped at 5% of total equity) + volatility target scaling (`min(1, 0.35 / name_vol)`) → place orders (bracket OCO / MPT rebalances) on Alpaca.

## 4. Models on disk (`ml_engine/saved_models/`)

- **Core Swing Model**: `swing_model.json` (+ `swing_metadata.pkl`)
- **Aggressive Swing Model**: `swing_aggressive_model.json` (+ `swing_aggressive_metadata.pkl`)
- **HMM Regime Model**: `hmm_model.pkl` (+ `hmm_metadata.pkl`)
- **Legacy models**: `short_term_model.json` (+ `threshold.json`), and PyTorch attention `temporal_attention_model.pth` (opt-in).
Retrain via `make swing-train` (trains both swing models) / `make train` (trains short-term and HMM) / UI Retrain button.

## 5. Persistence & backup

One SQLite DB holds everything. It is **not** in git/LFS (runtime churn); back it up to Google Drive
with `make db-backup` (commit-stamped). Saved models, the Drive token, and the heartbeat file live under
`backend/data/` and `ml_engine/saved_models/` (gitignored where appropriate).

## 6. Deployment posture

Single-user, local. Alpaca is **paper** by default. No auth on the API (localhost only). Secrets live in
`backend/.env` (gitignored). Not hardened for multi-user or public exposure.
