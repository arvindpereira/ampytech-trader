# Architecture

## 1. Processes & ports

A local monorepo of independent processes sharing one SQLite file.

| Process | Command | Port | Role |
| :-- | :-- | :-- | :-- |
| FastAPI backend | `make serve-backend` (`run.py serve`, uvicorn `--reload`) | 8008 | suggestions, portfolio, strategy/eval jobs, virtual broker, external portfolio, health |
| Next.js frontend | `make serve-frontend` | 3002 | dashboard (6 tabs) |
| Scheduler daemon | `make schedule` | — | daily + intraday + weekly cron jobs (see [operations.md](./operations.md)) |
| Ollama | external (local) | 11434 | LLM news scoring for the swing model |
| Ingestion / training / backup | `run.py` / `scripts/db_backup.py` (one-shot) | — | fetch, train, swing-train, db/files backup |

All Python shares `backend/` (FastAPI app, `app/services/`, `ml_engine/`, `data_ingestion/`, `execution/`).

## 2. Frontend tabs

`dashboard` (signals + regime), `virtual_perf` (virtual broker performance), `editor` (strategy
buckets / per-ticker assignment / universe), `advisor` (Equity Advisor — tax lots, sell planning,
wash-sale guard), `crash` (Crash Radar / defensive playbook + war-game), `external` (External
Portfolio Manager — Robinhood/Vanguard accounts, per-account strategy, suggestions, consolidated view),
`research` (Research Analyst — AI inquiry, intent routing, citation resolvent),
`sectors` (Sector Simulator — GICS benchmarks weight comparison delta heatmap).

## 3. Models & signal engine (shared, global)

- **Core / Aggressive Swing** (`ml_engine/swing_alpha.py`): XGBoost on technicals + LLM-news;
  Core trades Hot/Solid/Unrated tiers, Aggressive trades Speculative. `swing_model.json`,
  `swing_aggressive_model.json`.
- **Long-term** (MPT/regime rebalance) and **HMM regime** (`hmm_model.pkl`); legacy short-term model.
- **Tier classification** (`ticker_classification`): risk × fundamental-quality → Hot / Solid /
  Long-shot / Cold. **Covers the ~40-ticker trade universe only** (see Known Issues).
- `GET /api/suggestions` → regime + features + inference → `{swing_suggestions,
  high_risk_suggestions, long_term_allocation, short_term_suggestions}`, cached on data freshness.
- These models are **trained once on shared data**; all downstream consumers (main bot, external
  accounts) read the same signal snapshot. No per-account models.

## 4. Equity Advisor & guards

- `ml_engine/tax_advisor.py` (HIFO loss-harvest, LT/ST, wash-sale flags), `equity_lots` /
  `tax_profile`. Concentration rollup + LLM narrative in `app/main.py`.
- **Trading guard** (`TradingBlock` + executor checks): per-ticker wash-sale / never-trade blocks +
  a global `auto_trading_paused` kill-switch.

## 5. External Portfolio Manager (Robinhood / Vanguard)

- **Import**: `data_ingestion/import_external_csv.py` (Robinhood transaction CSV → holdings, anchored
  by a per-account statement snapshot in `external_statement_holdings`, seeded from
  `data_ingestion/anchors/*.csv`, auto-updated by monthly PDF via `external_importer.py`).
  Source files stashed to `data/import_sources/` (backed up, gitignored).
- **Valuation**: `_latest_external_price` (recent→daily→cost fallback); held tickers priced via
  `fetch_equity_advisor_prices`; `equity_universe_sync` adds them to the universe.
- **Per-account strategy** (`app/services/account_strategy.py`, `ExternalAccount.strategy_mode /
  aggression / buckets_json`): `GET /api/external/suggestions` builds a target allocation =
  `a·growth + (1−a)·defensive` where `a = aggression/100`, then diffs vs holdings → BUY/SELL.
  - **growth** = preserve unsignalled holdings + deploy free capacity into model BUY signals by
    per-account buckets (swing/longterm/high_risk).
  - **defensive endpoints** = fixed ETF templates: `all_weather` (SPY/TLT/IEF/GLD/GSG),
    `barbell` (BIL/QQQ), `glide_path` (all_weather blended with the crash-radar safe mix by
    `de_risk_coefficient`).
  - `POST /api/external/accounts/{label}/strategy` sets mode/aggression/buckets.

## 6. Crash Radar / Defensive Strategist & War-game

- `ml_engine/defensive_strategist.py::build_defensive_playbook(preset)` → Dalio All-Weather,
  Taleb barbell, glide-path de-risk, safe-asset mix, `de_risk_coefficient`, regret matrix.
- `ml_engine/wargame.py` walk-forward backtests (`run_preset_comparison`, `run_scenario_comparison`,
  `run_simulation`) over historical crash eras. Currently main-portfolio scoped.

## 7. Persistence & backup

One SQLite DB holds everything; **not** in git/LFS. `make backup` = DB + models + caches + anchor
CSVs + imported broker sources to Google Drive (commit-stamped). Secrets in `backend/.env`.

## 8. Known issues — External Portfolio strategy (as of 2026-06-21)

The per-account strategy produces **incoherent defensive suggestions** (e.g. in a `glide_path`
account it proposes *SELL BRK-B, BUY BYND*). Root causes:

1. **No per-name risk/quality awareness.** "Defensive" means *rotate into fixed ETF templates*
   (SPY/TLT/GLD/BIL…), which contain **none of the user's actual holdings**. A held quality/defensive
   name (BRK-B) is therefore trimmed toward 0; the engine has no concept that BRK-B *is* a safe asset.
2. **External holdings are unclassified.** `ticker_classification` covers only the trade universe;
   BRK.B, BYND, HOOD, etc. have **no tier/quality/volatility** — so the system has no data to know
   BRK-B is safe and BYND is speculative. (The classification + feature pipeline needs to be
   **separated from the trade universe** and run over all held tickers.)
3. **Growth bleeds into defensive modes.** `target = a·growth + (1−a)·defensive` with default
   `aggression=60` (and Individual currently 32) keeps a large growth fraction, which deploys the
   `high_risk` bucket into speculative BUY signals (BYND) even when the user intends to de-risk.
4. **Aggression semantics are ambiguous** — it conflates "how much to deploy" with "growth vs ETF
   basket," and never expresses "keep my quality names, shed my risky ones."
5. **Ticker normalization** (`BRK.B` vs `BRK-B`) differs across holdings, templates, prices, and the
   UI — a latent source of mismatched targets.

### Consolidation opportunities (for the fix)
- Extract a **ticker/classification/feature service** usable over *any* ticker set (not just the
  trade universe), so external holdings get tiers, volatility, and quality.
- The per-account knobs are **pure allocation rules, not ML** — they require **no additional model
  training or per-portfolio decision trees**. Shared models stay shared.
- The External Portfolio Manager needs **evaluation/visualization** (target vs current weights,
  per-name risk tier, why-this-order reason codes, and the per-account war-game) to surface bugs.

## 9. Research Analyst (Interactive Inquiry & Wiki)

- **Purpose**: Provides a dedicated interactive research assistant to perform targeted analysis on specific stocks, thematic sectors, and portfolio read-throughs.
- **Components**:
  - `intent_router.py`: Classifies queries into specific templates (`ticker_outlook`, `earnings_report`, `theme_rank`, `sector_screen`).
  - `context_expander.py`: Resolves primary tickers, peer groupings, and references portfolio holdings for crowding and spillover analysis.
  - `research_dossier.py` & `research_kb_refresh.py`: Manages the company snapshot database (`company_snapshots`), downloading key metrics, financials, and news logs into local tables.
  - `news_retriever.py`: Fetches recent headlines and premium content, matching relevance against query parameters.
  - `research_llm_router.py` & `research_analyst.py`: Decides LLM selection tier (standard/premium/local), synthesizes narratives, and applies structured JSON report templates.
  - `citation_resolver.py`: Attaches precise local citations and links individual statement holdings, news headlines, and snapshots back to the source text.
  - `research_wiki_export.py`: Handles publishing draft reports by rendering them as markdown files in the local wiki (`research-wiki/`).
  - `feedback_analytics.py`: Records user critiques and feedback logs on rejected draft reports.

## 10. Sector Exposure Simulator & Heatmap

- **Purpose**: Tracks consolidated sector allocations across all accounts and compares them to broad benchmarks to highlight active bets and structural tilts.
- **Components**:
  - `sector_resolver.py`: Classifies individual tickers into canonical GICS sectors and industry classifications, storing the mappings in `research_sectors.json`.
  - `sector_exposure_analyzer.py`: Pulls active positions from internal trading accounts (Alpaca) and external brokerage accounts (EquityLot), calculates real-time valuations, and matches them to GICS sectors.
  - **S&P 500 GICS Benchmark**: Loads benchmark GICS sector weights from `sp500_sector_weights.json`.
  - **Exposure Heatmap**: Calculates active sector tilts ($\Delta = \text{Portfolio Weight} - \text{Benchmark Weight}$) and fires warning alerts if a sector is overweight/underweight by more than 5 percentage points. Displays a visual color-coded heatmap grid (Red = overweight, Blue = underweight) and details industry breakdowns and stock holdings.
