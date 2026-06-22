# Architecture

## 1. Processes & ports

A local monorepo of independent processes sharing one SQLite file.

| Process | Command | Port | Role |
| :-- | :-- | :-- | :-- |
| FastAPI backend | `make serve-backend` (`run.py serve`, uvicorn `--reload`) | 8008 | suggestions, portfolio, strategy/eval jobs, virtual broker, external portfolio, health |
| Next.js frontend | `make serve-frontend` | 3002 | dashboard (7 tabs) |
| Scheduler daemon | `make schedule` | — | daily + intraday + weekly cron jobs (see [operations.md](./operations.md)) |
| Ollama | external (local) | 11434 | LLM news scoring for the swing model |
| Ingestion / training / backup | `run.py` / `scripts/db_backup.py` (one-shot) | — | fetch, train, swing-train, db/files backup |

All Python shares `backend/` (FastAPI app, `app/services/`, `ml_engine/`, `data_ingestion/`, `execution/`).

## 2. Frontend tabs

Seven tabs (in order): `dashboard` (signals + regime), `virtual_perf` (virtual broker performance +
LLM cost ledger), `editor` (strategy buckets / per-ticker assignment / universe), `advisor` (Equity
Advisor — RSU/ESPP lots, tax profile, sell planning, wash-sale guard, grant timeline),
`crash` (Crash Radar / defensive playbook + war-game), `external` (External Portfolio Manager —
Robinhood/Vanguard accounts, per-account strategy, suggestions, consolidated view with the Sector
Exposure heatmap embedded), `research` (Research Analyst — AI inquiry, intent routing, citation
resolver, wiki).

The Sector Exposure heatmap (`SectorExposurePanel`) is rendered inside the external tab's
consolidated-holdings section — it is **not** a separate tab.

## 3. Models & signal engine (shared, global)

- **Core / Aggressive Swing** (`ml_engine/swing_alpha.py`): XGBoost on technicals + LLM-news;
  Core trades Hot/Solid/Unrated tiers, Aggressive trades Speculative. `swing_model.json`,
  `swing_aggressive_model.json`.
- **Long-term** (MPT/regime rebalance) and **HMM regime** (`hmm_model.pkl`); legacy short-term model.
- **Tier classification** (`ticker_classification`): risk × fundamental-quality → Hot / Solid /
  Long-shot / Cold. Covers the ~40-ticker trade universe; external holdings are classified separately
  via `classify_tickers()` (see §5).
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

- **Import**: `data_ingestion/import_external_csv.py` (Robinhood transaction CSV → holdings,
  anchored by per-account statement snapshots in `external_statement_holdings`, seeded from
  `data_ingestion/anchors/*.csv`, auto-updated by monthly PDF via `external_importer.py`).
  Source files stashed to `data/import_sources/` (backed up, gitignored).
- **Valuation**: `_latest_external_price` (recent→daily→cost fallback); held tickers priced via
  `fetch_equity_advisor_prices`; `equity_universe_sync` adds them to the universe so prices stay fresh.
- **Per-account strategy** (`app/services/account_strategy.py`; `ExternalAccount.strategy_mode /
  aggression / buckets_json / de_risk_policy`):
  `GET /api/external/suggestions` builds `target = a·growth + (1−a)·defensive` where
  `a = aggression/100`, then diffs vs holdings → BUY/SELL proposals.
  - **growth** — preserve unsignalled holdings + deploy free capacity into model BUY signals by
    per-account buckets (swing/longterm/high_risk). High-risk bucket is scaled by `a` so a
    defensive account never opens fresh speculative positions.
  - **all_weather** (`SPY/TLT/IEF/GLD/GSG`) and **barbell** (`BIL/QQQ`) — explicit basket-rotation
    modes for Dalio/Taleb-style hedges. These rotate into fixed ETF templates.
  - **de_risk / glide_path** — **holdings-aware** defensive endpoint:
    `holdings_defensive_target(current_weights, classifications, de_risk_coefficient, beta_weight)`
    ranks each held name by tier × volatility × beta factor; over-weights low-vol/quality names
    (BRK.B), trims speculative/high-vol (BYND), routes de-risked weight to cash. Scaled by the
    crash-radar `de_risk_coefficient`. Does **not** rotate into ETF templates — quality names are
    kept in-kind.
  - **De-risk policy** (`ExternalAccount.de_risk_policy`): `rotate` (keep quality equity, ride out
    crashes) vs `shed_beta` (cut high-beta names, raise cash). `Auto` follows
    `recommend_de_risk_policy(coef, book_beta)` — recommends `shed_beta` when crash risk is high
    (coef ≥ 0.25) or portfolio beta is elevated (≥ 1.10), else `rotate`. Policy stored as
    `de_risk_policy` column; `beta_weight = 1.0` for shed_beta else `0.0`.
  - **Tickers classified** beyond the trade universe via `classify_tickers(held_names)`:
    full tier where fundamentals exist, volatility-only tier for ETFs/ADRs — so every held name has
    a `tier`, `volatility`, and `beta` for the defensiveness ranking.
  - `POST /api/external/accounts/{label}/strategy` sets mode / aggression / buckets / de_risk_policy.
- **Per-account war-game** (`app/services/account_wargame.py`):
  - `POST /api/external/accounts/{label}/wargame?lookback_years=N` — walk-forward each strategy
    mode (growth / de_risk / all_weather / barbell) over `N` years of real prices with monthly
    rebalancing and partial-window entry (no look-ahead). Returns equity curves + Sharpe/CAGR/MDD.
  - `POST /api/external/accounts/{label}/crash-stress?era=gfc` — maps holdings to an SPY-beta proxy
    and replays them through a historical crash era's SPY path; returns per-mode equity curves.
  - `POST /api/external/accounts/{label}/policy-compare?lookback_years=N&era=gfc` — runs de_risk
    under both policies (rotate vs shed-to-cash) side-by-side; returns curves, crash drawdown,
    portfolio beta, and cash target to visualize the upside-vs-protection tradeoff.

## 6. Crash Radar / Defensive Strategist & War-game

- `ml_engine/defensive_strategist.py::build_defensive_playbook(preset)` → Dalio All-Weather,
  Taleb barbell, glide-path de-risk, safe-asset mix, `de_risk_coefficient`, regret matrix.
- `ml_engine/wargame.py` walk-forward backtests (`run_preset_comparison`, `run_scenario_comparison`,
  `run_simulation`) over historical crash eras. Currently main-portfolio scoped.

## 7. Persistence & backup

One SQLite DB holds everything; **not** in git/LFS. `make backup` = DB + models + caches + anchor
CSVs + imported broker sources to Google Drive (commit-stamped). Secrets in `backend/.env`.

## 8. External Portfolio strategy — design notes

The per-account strategy was redesigned to be **holdings-aware and risk-tier-aware** (fixed in PRs
#24–#28, mid-2026). Key design decisions recorded here for context:

- **Why holdings-aware defensiveness?** The original design rotated into fixed ETF templates
  (SPY/TLT/GLD/BIL) in defensive modes, trimming quality names (BRK.B) to 0 while leaving
  speculative names untouched. The replacement ranks *each held name* by its own tier + vol + beta
  so quality stays and speculative is shed.
- **Why no per-account ML models?** The per-account knobs (aggression, mode, de_risk_policy) are
  pure allocation rules applied on top of the global shared signals. No additional model training or
  per-portfolio decision trees are needed.
- **Why the explicit de_risk_policy toggle?** Crash tests showed that "keep-quality" (rotate) holds
  high-beta quality equities (NVDA/META, beta ~2.0) and still falls ~56% in a GFC scenario.
  "Shed-to-cash" raises cash aggressively by cutting high-beta names even when they are quality.
  The tradeoff is upside (rotate wins in recovery) vs protection (shed-to-cash wins in the crash).
  This is a user preference, so it is an explicit setting with a model recommendation rather than
  buried in the aggression slider.
- **Ticker normalization**: `canonical_ticker()` normalizes to uppercase + `.` for class shares
  (BRK.B), applied at classification, target build, pricing, and UI response.

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

Embedded in the External Portfolio tab's consolidated-holdings section (not a separate tab).

- **Purpose**: Tracks consolidated sector allocations across all accounts and compares them to broad benchmarks to highlight active bets and structural tilts.
- **Components**:
  - `sector_resolver.py`: Classifies individual tickers into canonical GICS sectors and industry classifications, storing the mappings in `research_sectors.json`.
  - `sector_exposure_analyzer.py`: Pulls active positions from internal trading accounts (Alpaca) and external brokerage accounts (EquityLot), calculates real-time valuations, and matches them to GICS sectors.
  - **S&P 500 GICS Benchmark**: Loads benchmark GICS sector weights from `sp500_sector_weights.json`.
  - **Exposure Heatmap**: Calculates active sector tilts ($\Delta = \text{Portfolio Weight} - \text{Benchmark Weight}$) and fires warning alerts if a sector is overweight/underweight by more than 5 percentage points. Displays a visual color-coded heatmap grid (Red = overweight, Blue = underweight) and details industry breakdowns and stock holdings.
