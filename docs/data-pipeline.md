# Data Pipeline & Storage

Ingestion writes into a shared SQLite DB (`backend/data/trading_system.db`). The DB is **not** tracked
in git/LFS — back it up with `make db-backup` (see [operations.md](./operations.md)).

## Ingestion sources (`data_ingestion/`)

| Script | Source | Writes | Notes |
| :-- | :-- | :-- | :-- |
| `price_fetcher.py` | Massive/Polygon (hourly ~5y) + Yahoo (daily 1998+) | `recent_prices`, `daily_prices` | computes SMA/RSI/MACD/ATR locally; `backfill_ticker()` does a single new ticker (prices **+ news**) |
| `macro_fetcher.py` | Massive/FRED | `macro_indicators` | treasury yields, fed funds |
| `market_stress_fetcher.py` | FRED / FRB | `macro_indicators` | credit spreads (hy, ig), NFCI (financial conditions, leverage), SLOOS tightening supply, labor indicators (initial claims, Sahm rule), Excess Bond Premium (EBP) |
| `valuation_fetcher.py` | Yale CAPE / Multpl / Polygon WILL5000 | `macro_indicators` | Shiller CAPE, Buffett Indicator (market cap/GDP) |
| `sentiment_fetcher.py` | News API / Reddit / premium uploads | `ticker_sentiments`, `sentiment_source_logs` | VADER-scored; `is_mock` flag separates real vs mock |
| `news_llm.py` | Polygon news → **LLM (Ollama or OpenAI)** | `news_llm_scores` | per-ticker directional + relevance score; the **swing** edge; dense from ~2021. Pluggable provider (`NEWS_LLM_PROVIDER`); batches score **concurrently** |
| `premium_ingest.py` + `premium_llm.py` | **Premium newsletter emails** (e.g. The Information) via IMAP → LLM | `news_llm_scores` | reads subscriber emails you receive, LLM-extracts which **universe tickers** an article materially affects (incl. indirect/private-company knock-ons), writes scores tagged `premium:<source>`. Only derived scores are stored, not article text |
| `fundamentals_fetcher.py` | Polygon Financials API | `ticker_fundamentals` | Ingests company income statements, balance sheets, and cash flows per fiscal period. Computes derived growth/profitability ratios. Run via `make fundamentals` |
| `alternative_fetcher.py` | SEC EDGAR Form 4 | `insider_disclosures`, `congress_disclosures` | only when `ALT_DATA_ENABLED` |
| `crisis_fetcher.py` | yfinance | `crisis_prices` | historic crash eras for stress display |
| `popular_tickers.py` | yfinance scrape | `universe_tickers` | popular/trending helper |
| `analyst_fetcher.py` | Benzinga analyst ratings via Massive | `analyst_forecasts` | Ingests consensus price targets and ratings. Caches price-only row if benzinga is unavailable. |
| `earnings_content_fetcher.py` | Finnhub EPS and transcripts | `earnings_transcripts`, `earnings_estimate_snapshots`, `earnings_surprises` | Snaps consensus estimates, reported EPS surprises, and earnings call transcripts (requires Finnhub Professional+ for full transcript text). |
| `sector_catalog_refresh.py` | Local GICS sector catalog & seed configuration | `research_sectors.json` catalog | Refreshes and maps GICS sector hierarchies, cap-ranked seed tickers, and matches portfolio allocations. |
| `research_kb_refresh.py` | Materializer | `company_snapshots`, `sector_snapshots` | Runs daily to consolidate all stock-level facts, news sentiments, analyst metrics, and sector-level median aggregates. |

`run.py fetch` runs the core fetchers sequentially; `make news-llm` runs the news scorer; both are also
driven by the scheduler.

**News-LLM scoring providers.** `news_llm.py` scores headlines concurrently (thread pool) and supports
two providers via `NEWS_LLM_PROVIDER`:
- **`ollama`** (default) — local `gemma4:e4b`, free + private. Used by the recurring daily/intraday
  scheduler jobs.
- **`openai`** — `gpt-4o-mini` via REST, a fast opt-in for bulk backfills (10–50× faster; **<~$1** for a
  full 2021→now universe backfill). Needs `OPENAI_API_KEY` in `backend/.env`. Run with
  `make news-llm PROVIDER=openai`, or the cheapest unattended `make news-llm-batch` (OpenAI Batch API).

The per-stock UI **Backfill** button automatically uses OpenAI when `OPENAI_API_KEY` is set, else Ollama.
Scoring is **resumable** (already-scored `article_id`s are skipped) and **idempotent** (upsert on
`(ticker, article_id)`), so re-runs and concurrent backfills are safe.

## Database schema (SQLite, `app/database/models.py`)

**Prices & market data**
- `recent_prices` — hourly bars (+ indicators).
- `daily_prices` — daily bars 1998+ (+ indicators); features + MPT + regime use these.
- `crisis_prices` — historic crash-era daily bars.
- `macro_indicators` — `(date, indicator_name, value)`.
- `crash_risk_snapshots` — **`as_of_date` PK**, composite risk index, risk band, posture, trigger reasons, subscores (valuation, monetary, credit, etc.), debt cycle read, and experimental forecasts. Acts as timeline cache.

**Fundamentals & Quality Tiers**
- `ticker_fundamentals` — **`(ticker, end_date)` PK**, financial statement line items (revenues, gross profit, capex, assets, debt) + computed ratios (margins, FCF, ROE, debt-to-equity). Feeds fundamental classification.
- `ticker_classification` — **`ticker` PK**, blended quantitative quality score (`quant_quality`), qualitative overlay (`llm_quality`), volatility, 2022 bear drawdown, computed tier, manual tier override (`tier_override`), and LLM text verdict. Determines core vs high-risk execution sleeves.
- `llm_usage` — API request log tracking provider, model, purpose, token counts, and estimated cost per call. Powers cost analytics widgets.

**Signals & news**
- `ticker_sentiments` — per-(ticker,date,source) aggregate VADER sentiment (`is_mock`).
- `sentiment_source_logs` — individual articles/posts with scores + URLs (`is_mock`).
- `news_llm_scores` — **`(ticker, article_id)` PK**, `date`, `published_utc` (full ISO timestamp), `title`,
  `llm_score` (−1..1), `llm_relevance` (0..1), `model` (the scoring LLM), `source` (`polygon` headlines vs
  `premium:the-information` newsletter — lets models filter/weight premium news separately). Drives swing.
- `insider_disclosures`, `congress_disclosures` — SEC Form 4 / congressional trades (alt-data, off by default).

**Universe, strategy & settings**
- `universe_tickers` — `ticker` (PK) + **`strategy`** (`swing`/`longterm`/`hold`) per-ticker assignment.
- `app_settings` — generic key/value; holds the **capital-bucket allocations** (JSON).
- `ticker_metadata` — `ticker` (PK), sector, industry, market_cap, exchange from Yahoo/Polygon. Used by sector exposure and research snapshot aggregation.

**Accounts & execution (virtual broker)**
- `virtual_accounts` — id 1 (replay) / id 2 (real) cash + equity.
- `virtual_positions` — `(ticker, mode)` PK, qty, entry_price, policy, purchase_date.
- `virtual_orders` — order log (mode, side, brackets, fill, sim_date).
- `executed_trades` — historical executed-trade log.
- `broker_performance_logs` — daily equity vs SPY/QQQ/BRK snapshots.

**Equity Advisor (Tab 4)**
- `equity_lots` — one row per tax lot (RSU/ESPP/other): `ticker`, `account_label`, `lot_type`, `shares`, `cost_basis_per_share`, `acquisition_date`, `notes`. Non-external lots only (external accounts use `ExternalAccount`/`EquityLot` overlap handled by label filter).
- `equity_vest_schedules` — `(ticker, lot_type)` PK, vest cadence, next vest date, estimated shares, `vesting_complete`.
- `equity_auto_trade_blocks` — `ticker` PK, boolean `blocked`; gates whether the auto-trader may buy a ticker held externally (e.g. PINS during a wash-sale window). Syncs with `trading_blocks`.
- `tax_profile` — single row (id=1): `filing_status`, `ordinary_income`, `magi`, `state_ltcg_rate`, `state_stcg_rate`, `carryover_loss`, `tax_year`. Drives HIFO lot tax estimates.
- `ticker_analyst_forecasts` — `(ticker, as_of_date)` PK: consensus price targets (mean/high/low/median), num_analysts, recommendation, upside %. All fields nullable; source-tagged.
- `trading_blocks` — per-ticker active BUY guards: `block_type` (`wash_sale`/`permanent`), `blocked_until`, `sale_date`, `realized_loss`, `shares`. Opportunistically expired by the API on read.

**External Portfolio Manager (Tab 6)**
- `external_accounts` — one row per brokerage account: `account_label` (PK), `cash`, `risk_profile`, `strategy_mode`, `aggression`, `buckets_json`, `de_risk_policy`.
- `external_statement_holdings` — statement-date anchor: `(account_label, ticker, statement_date)` PK, `shares`, `cost_basis_per_share`. Seeded from `data_ingestion/anchors/*.csv`; auto-updated by PDF import. Used as the basis reference for FIFO reconciliation.
- `external_orders` — proposed/confirmed order log for external accounts: `account_label`, `ticker`, `side`, `shares`, `price`, `status` (`proposed`/`confirmed`/`cancelled`).
- `external_transactions` — raw transaction rows from Robinhood CSV import (after deduplication).

**Research & Analyst Subsystem (Research tab)**
- `company_snapshots` — **`(ticker, as_of_date)` PK**, denormalized daily company state (price, momentum metrics, consensus forecasts, news scores, sector/industry details, facts JSON, coverage %). Used as RAG knowledge base.
- `external_analyst_items` — **`id` PK**, third-party analyst ratings, excerpts, targets, and dates (fetched from Finnhub transcripts or news APIs).
- `earnings_transcripts` — **`(ticker, finnhub_id)` PK**, earnings call transcript text, period (e.g. 2024Q1), call date, and excerpt.
- `earnings_estimate_snapshots` — **`(ticker, period, freq, as_of_date)` PK**, quarterly EPS estimates from analysts.
- `earnings_surprises` — **`(ticker, period)` PK**, reported vs estimated EPS surprise percentages.
- `research_watchlist` — **`ticker` PK**, tickers added by user to watchlist.
- `web_search_cache` — **`query_hash` PK**, cached web search results to bypass redundant API hits.
- `sector_snapshots` — **`(sector_id, as_of_date)` PK**, sector median upside, momentum, quality, and news sentiment metrics.
- `internal_price_targets` — **`(ticker, as_of_date, horizon_date)` PK**, proprietary blended/momentum-tilted 12-month target prices.
- `research_news_embeddings` — **`doc_key` PK**, cached vector embeddings for news.
- `research_threads` — **`id` PK**, research session logs, intent, summary, published wiki slugs, feedback notes, and status (draft, published, rejected).
- `research_messages` — **`id` PK**, transcripts of chat queries and structured JSON assistant report payloads.


`init_db()` creates tables via SQLAlchemy `create_all`, then runs an idempotent **migration dict**
(`connection.py::MIGRATIONS`) that `ALTER TABLE … ADD COLUMN` for columns added after the initial
schema (e.g. `strategy` on `universe_tickers`, `de_risk_policy` on `external_accounts`). New tables
never need a migration shim — `create_all` handles them. Seeds the universe + default accounts on
first run.

## Point-in-time correctness

Daily features for day *T* use data through *T−1*; LLM-news features are shifted **+1 day** so a day's
news can't inform that same day. Triple-barrier labels are the only forward-looking field. Replay fills
orders at the next bar's open. This is what makes the walk-forward evaluation honest.

## The "Massive" API

`MASSIVE_BASE_URL` is a Polygon-compatible endpoint (`MASSIVE_API_KEY`). Used for hourly aggregates,
macro, and the news feed. News coverage is dense from ~2021; foreign ADRs/small names are thin. Use the
**US-listed ticker symbol** the feed indexes (e.g. `TSM`, not `TSMC`).
