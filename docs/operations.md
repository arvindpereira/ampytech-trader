# Operations & Runbook

## 1. Setup

```bash
make install            # backend venv + Python deps + frontend npm deps
# Fill backend/.env: MASSIVE_API_KEY (Polygon), ALPACA_API_KEY/SECRET (paper), FRED, Reddit (optional),
#                    GOOGLE_OAUTH_CLIENT_ID/SECRET (for DB backup). Defaults point Alpaca at paper.
# Install + run Ollama locally with the LLM_MODEL (default gemma4:e4b) for news scoring.
# Optional: OPENAI_API_KEY to use OpenAI for fast bulk news backfills (else local Ollama is used) and
#           to write the Model-Evaluation "expert interpretation" (OPENAI_EXPERT_MODEL, default gpt-5.5;
#           disable with EXPERT_INTERP_ENABLED=false).
```

## 2. Day-to-day commands (Makefile → `run.py` / scripts)

The everyday targets are **dependency-aware and cache-aware**: each pipeline step writes a stamp under
`backend/.make/`, and a step is skipped while its stamp is still fresh (per-step TTL in minutes). So you
can re-run them freely and only stale work actually executes. `FORCE=1` invalidates every cache; per-step
windows are overridable (`DATA_TTL`, `NEWS_TTL`, `FUND_TTL`, `CLASSIFY_TTL`, `MODEL_TTL`); `make clean-cache`
wipes the stamps. The dependency graph is: `data → {train-core, classify(+fundamentals), news-recent}`,
`classify+news-recent → swing-train`, and `train = train-core + swing-train + crash-refresh`.

**Everyday (just these four cover normal use):**
- `make up` — bring up the **whole system in order**: refresh data → models (in dependency order, cache-aware) → then launch backend + frontend + scheduler. A same-day run is a fast no-op on the data/model side.
- `make data` — run **all** core fetches (prices hourly+daily, macro, crisis eras, sentiment, CAPE/valuation, market-stress) as one cached step. `make fetch` forces a refresh now.
- `make train` — train **all served models**: HMM regime + short-term XGBoost (`train-core`) + Core/Aggressive swing (`swing-train`), pulling `fundamentals`/`classify`/`news-recent` as needed, then refreshing crash odds. Depends on `data`, so fetches happen first.
- `make serve-all` — just launch backend + frontend + scheduler (no pipeline). `make serve` is backend+frontend only.

**Pipeline internals (run automatically by the above; call directly only for targeted work):**
- `make fundamentals [TICKERS=NVDA,AAPL]` — company financials (Polygon) → ratios (cached).
- `make classify` — risk × fundamental-quality tiering, quant + LLM overlay (cached; needs data + fundamentals).
- `make news-recent` — incremental LLM-scoring of the **last 7 days** of news for the swing model (cached; needs Ollama, or `PROVIDER=openai`).
- `make train-core` / `make swing-train [SWING_HORIZON=5]` — train just the core or just the swing models.
- `make train-deep [EPOCHS=]` — the optional PyTorch temporal-attention net (**not served by default**; `SERVED_MODEL=xgboost`). Run occasionally.
- `make crash-refresh [FORCE=1]` — refresh the Crash Radar snapshot + coherent odds if inputs changed.

**Evaluation & research (auto-fetch fresh data first):**
- `make walkforward [SPLITS=]` — honest OOS edge check. `make calibrate` — threshold calibration (needs the trained core model).
- `make swing-eval` / `make longterm-eval` / `make longterm-tilt` / `make exec-timing` / `make stop-opt` / `make horizon-opt` — research evaluations.
- `make backtest` — in-sample PyBroker audit (needs trained models). `make simulate [DAYS=]` / `make backtest-virtual [MONTHS=]` — Virtual Broker forward sim / look-ahead-free replay.

**Occasional data ops:**
- `make news-llm [START=2021-01-01 PROVIDER=openai TICKERS=AAPL,NVDA]` — full/range LLM news backfill. Default provider is local **Ollama** (free); `PROVIDER=openai` is a fast bulk backfill (10–50× faster, **<~$1** full backfill; needs `OPENAI_API_KEY`).
- `make news-llm-batch [START=… TICKERS=…]` — submit via OpenAI's **Batch API** (50% cheaper, unattended); `make news-llm-batch-collect BATCH_ID=<id>` ingests it (resumable).
- `make backfill-news` — backfill historical daily VADER news sentiment (~2021→now).
- `make premium-ingest [DAYS=7]` — pull **premium newsletter emails** (e.g. The Information) via IMAP, LLM-extract per-ticker scores. Set `IMAP_USER`/`IMAP_PASSWORD` (an **app-password**), `IMAP_HOST`, `PREMIUM_SENDER` in `.env`. Test/manual: `make premium-ingest PREMIUM_FILE=path.eml`; preview: `DRY_RUN=1`. Only derived scores (not article text) are stored.
- `make insider` — real SEC Form 4 (only when `ALT_DATA_ENABLED`). `make fetch-forecasts` — Equity Advisor forecasts. `make fetch-valuation` / `make fetch-market-stress` — fetch just that one source. `make popular-tickers` / `make add-ticker TICKER=SYM` — universe maintenance. `make llm-usage` — token usage/cost ledger.
- **Research Analyst & Sector Simulator Ops:**
  - `make research-kb-refresh` — daily materialization of company snapshot metrics and news sentiments.
  - `make research-sectors-refresh` — refreshes GICS mappings and ranked seeds for portfolio tickers only.
  - `make research-sectors-refresh-full` — full GICS catalog rebuild including static seeds and all universe tickers.
  - `make research-sectors-refresh-fast` — updates GICS resolver mapping from current DB data without fetching.
  - `make research-wiki-export` — processes draft threads and exports them to static markdown wiki pages.
  - `make research-wiki-serve [WIKI_PORT=4000]` — starts a local server on port `:4000` to serve the static wiki files.
  - `make research-wiki-serve-jekyll` — serves the wiki via Jekyll.
  - `make research-calibrate-factors` / `make research-calibrate-factors-dry` — executes Spearman rank IC walk-forward weight calibration.
  - `make import-equity-lots FILE=path.pdf [REPLACE=1] [FORCE_LLM=1]` — extracts external holdings from brokerage PDFs.


**Backups (Google Drive)** — the DB and large artifacts are **not** in git/LFS; back them up here. Every backup is **commit-stamped** so a restore matches the code version that produced it.
- `make backup [BACKUP_KEEP=10]` — upload BOTH a DB copy and a files zip. The files zip = trained models (`ml_engine/saved_models/`), archived premium news, and **all cached JSON in `backend/data/`** (Crash Radar `crash_forecast_state.json`, the cached scenario wargame + AI analyst `wargame_cache.json`, the GICS resolver database `research_sectors.json`, IPO markers, LLM pricing, premium-ingest state). The OAuth token `gdrive_token.json` is excluded.
- `make db-backup` / `make files-backup` — upload just one side.
- `make db-backup-list` / `make files-backup-list` — list backups (with their commit).
- `make db-verify` / `make files-verify` — download + validate a backup WITHOUT touching the live data.
- `make restore` / `make restore-commit` — restore newest / the one matching the current commit (DB + files). Existing files move aside to `*.pre-restore`.

**Run**
- `make serve-backend` (FastAPI :8008) · `make serve-frontend` (Next.js :3002) · `make schedule` (daemon).
- `make simulate [DAYS=]` / `make backtest-virtual [MONTHS=]` · `make lint`.

## 3. Scheduler (`make schedule`, `execution/scheduler.py`, America/New_York)

| Job | When | What |
| :-- | :-- | :-- |
| Daily data fetch | 09:00 | prices/macro/sentiment + LLM-score the last week's news |
| Daily inference | 09:15 | log/refresh predictions |
| Crash Radar refresh | Mon–Fri 09:30 | recomputes coherent drawdown odds and updates wargame cache when data fingerprint changes |
| Daily execution | 09:45 | `run_execution` (bucket-aware swing + MPT on Alpaca) |
| Research KB refresh | Mon–Fri 10:00 | materializes stock snapshot metrics and refreshes sector classifications |
| Intraday news + re-exec | Mon–Fri 10:00–16:00 hourly | score recent news → re-run swing signals → re-execute (market-open guarded) |
| Intraday price fetch | Mon–Fri 09:00–16:00 every 5 min | refresh recent prices + suggestions cache |
| Weekly retrain | Sun 18:00 | `train_models()` + swing `train_and_save()` |
| Heartbeat | every 60 s | writes `data/scheduler_heartbeat.txt` (feeds `/api/health`) |

A reload of the API (or the scheduler) kills its in-process background jobs; long backfills are launched
as standalone processes and are resumable.

## 4. Retraining + restarting

The dependency-aware targets collapse the old multi-step dance into one command:

```bash
# Refresh data (if stale) + retrain ALL served models in dependency order
# (fundamentals → classify, news-recent → swing; data → core; then crash odds):
make train                 # add FORCE=1 to ignore caches and rebuild everything

# Or do the whole thing AND (re)launch backend + frontend + scheduler:
make up

# Just restart the servers (scheduler can stay up):
make serve-backend         # in one terminal
make serve-frontend        # in another
# verify: http://localhost:8008/api/train/status  +  http://localhost:8008/api/health
```

Run a single layer when that's all you need: `make train-core`, `make swing-train`, or `make train-deep`
(the optional PyTorch net, not served by default). You can also retrain from the UI
(Portfolio tab → **Model Training → Retrain**, a background job).

## 5. Google-Drive DB backup — one-time auth

1. Google Cloud Console → enable the **Drive API** → create an OAuth **Desktop-app** client.
2. Put `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` in `backend/.env` (folder defaults to
   `GOOGLE_DRIVE_FOLDER_ID`).
3. Add yourself as a **Test user** on the OAuth consent screen (or publish the app — `drive.file` is
   non-sensitive, so production needs no verification and avoids the 7-day test-token expiry).
4. `make db-backup` → browser consent once; token cached in `data/gdrive_token.json` (gitignored),
   refreshed automatically thereafter. Backups are stamped with the git commit; restore the matching
   one with `make db-restore-commit`.

> The DB is intentionally untracked in git/LFS (runtime churn bloated storage). The last in-repo snapshot
> is in history at commit `313081e`. ~1.1 GB of historical LFS objects remain on GitHub (capped, not
> reclaimed — would need repo recreation).

## 6. Health & monitoring

`GET /api/health` (and the navbar pills) report **Ollama, Alpaca, scheduler, DB, news** status. With the
default Ollama provider the swing pipeline needs **Ollama up**; if it stops, news scoring stalls
(degrades gracefully) unless `OPENAI_API_KEY` is set, in which case backfills use OpenAI instead. The
intraday loop and execution require the **Alpaca paper** creds and (for execution) an open market.
