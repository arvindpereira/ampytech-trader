# Data Pipeline & Storage

Ingestion writes into a shared SQLite DB (`backend/data/trading_system.db`). The DB is **not** tracked
in git/LFS — back it up with `make db-backup` (see [operations.md](./operations.md)).

## Ingestion sources (`data_ingestion/`)

| Script | Source | Writes | Notes |
| :-- | :-- | :-- | :-- |
| `price_fetcher.py` | Massive/Polygon (hourly ~5y) + Yahoo (daily 1998+) | `recent_prices`, `daily_prices` | computes SMA/RSI/MACD/ATR locally; `backfill_ticker()` does a single new ticker (prices **+ news**) |
| `macro_fetcher.py` | Massive/FRED | `macro_indicators` | treasury yields, fed funds |
| `sentiment_fetcher.py` | News API / Reddit / premium uploads | `ticker_sentiments`, `sentiment_source_logs` | VADER-scored; `is_mock` flag separates real vs mock |
| `news_llm.py` | Polygon news → **LLM (Ollama or OpenAI)** | `news_llm_scores` | per-ticker directional + relevance score; the **swing** edge; dense from ~2021. Pluggable provider (`NEWS_LLM_PROVIDER`); batches score **concurrently** |
| `premium_ingest.py` + `premium_llm.py` | **Premium newsletter emails** (e.g. The Information) via IMAP → LLM | `news_llm_scores` | reads subscriber emails you receive, LLM-extracts which **universe tickers** an article materially affects (incl. indirect/private-company knock-ons), writes scores tagged `premium:<source>`. Only derived scores are stored, not article text |
| `alternative_fetcher.py` | SEC EDGAR Form 4 | `insider_disclosures`, `congress_disclosures` | only when `ALT_DATA_ENABLED` |
| `crisis_fetcher.py` | yfinance | `crisis_prices` | historic crash eras for stress display |
| `popular_tickers.py` | yfinance scrape | `universe_tickers` | popular/trending helper |

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

**Accounts & execution (virtual broker)**
- `virtual_accounts` — id 1 (replay) / id 2 (real) cash + equity.
- `virtual_positions` — `(ticker, mode)` PK, qty, entry_price, policy, purchase_date.
- `virtual_orders` — order log (mode, side, brackets, fill, sim_date).
- `executed_trades` — historical executed-trade log.
- `broker_performance_logs` — daily equity vs SPY/QQQ/BRK snapshots.

`init_db()` creates tables, runs idempotent **auto-migrations** (e.g. the `strategy` column on
`universe_tickers`, `mode`/`is_mock` columns), and seeds the universe + default accounts.

## Point-in-time correctness

Daily features for day *T* use data through *T−1*; LLM-news features are shifted **+1 day** so a day's
news can't inform that same day. Triple-barrier labels are the only forward-looking field. Replay fills
orders at the next bar's open. This is what makes the walk-forward evaluation honest.

## The "Massive" API

`MASSIVE_BASE_URL` is a Polygon-compatible endpoint (`MASSIVE_API_KEY`). Used for hourly aggregates,
macro, and the news feed. News coverage is dense from ~2021; foreign ADRs/small names are thin. Use the
**US-listed ticker symbol** the feed indexes (e.g. `TSM`, not `TSMC`).
