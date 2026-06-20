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

**Data**
- `make fetch` — hourly+daily prices, macro, sentiment, crisis eras.
- `make fetch-valuation` — fetch Robert Shiller CAPE and compute Buffett Indicator ratios.
- `make fetch-market-stress` — fetch credit spreads, financial conditions, Sahm Rule, building permits, and Excess Bond Premium (EBP).
- `make fundamentals [TICKERS=NVDA,AAPL]` — fetch company financial statements (Polygon API) and calculate ratios.
- `make classify` — tier the universe by risk × fundamental-quality (runs quantitative metrics + LLM overlay).
- `make news-llm [START=2021-01-01 PROVIDER=openai TICKERS=AAPL,NVDA]` — LLM-score news for the swing model. Default provider is local **Ollama** (free); `PROVIDER=openai` is a fast bulk backfill (10–50× faster, **<~$1** full backfill; needs `OPENAI_API_KEY`). Batches score concurrently.
- `make news-llm-batch [START=… TICKERS=…]` — submit the same via OpenAI's **Batch API** (50% cheaper, unattended, up to 24h); `make news-llm-batch-collect BATCH_ID=<id>` ingests it (resumable).
- `make premium-ingest [DAYS=7]` — pull **premium newsletter emails** (e.g. The Information) via IMAP, LLM-extract per-ticker scores into the swing news feed. Set `IMAP_USER`/`IMAP_PASSWORD` (an **app-password**, not your main password), `IMAP_HOST`, `PREMIUM_SENDER` in `.env`. Runs in the daily scheduler job when creds are present. Test/manual: `make premium-ingest PREMIUM_FILE=path.eml` (also `.html/.txt/.md`); preview without scoring: `make premium-ingest DRY_RUN=1`. Only content you receive by email is read, and only derived scores (not article text) are stored.
- `make insider` — real SEC Form 4 (only when `ALT_DATA_ENABLED`).

**Models**
- `make train [EPOCHS=]` — XGBoost (hourly breakout) + HMM regime + PyTorch.
- `make swing-train [SWING_HORIZON=5]` — train + save both the **Core** (`swing_model.json`) and **Aggressive** (`swing_aggressive_model.json`) swing models served in the UI.
- `make walkforward [SPLITS=]` / `make calibrate` — honest OOS check / threshold calibration.
- `make swing-eval` / `make longterm-eval` / `make longterm-tilt` — research evaluations.
- `make backtest` — in-sample PyBroker audit.

**Backups (Google Drive)** — the DB and large artifacts are **not** in git/LFS; back them up here. Every backup is **commit-stamped** so a restore matches the code version that produced it.
- `make backup [BACKUP_KEEP=10]` — upload BOTH a DB copy and a files zip. The files zip = trained models (`ml_engine/saved_models/`), archived premium news, and **all cached JSON in `backend/data/`** (Crash Radar `crash_forecast_state.json`, the cached scenario wargame + AI analyst `wargame_cache.json`, IPO markers, LLM pricing, premium-ingest state). The OAuth token `gdrive_token.json` is excluded.
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
| Daily execution | 09:45 | `run_execution` (bucket-aware swing + MPT on Alpaca) |
| Intraday news + re-exec | Mon–Fri 10:00–16:00 hourly | score recent news → re-run swing signals → re-execute (market-open guarded) |
| Intraday price fetch | Mon–Fri 09:00–16:00 every 5 min | refresh recent prices + suggestions cache |
| Weekly retrain | Sun 18:00 | `train_models()` + swing `train_and_save()` |
| Heartbeat | every 60 s | writes `data/scheduler_heartbeat.txt` (feeds `/api/health`) |

A reload of the API (or the scheduler) kills its in-process background jobs; long backfills are launched
as standalone processes and are resumable.

## 4. Retraining + restarting

```bash
# 1. retrain legacy hourly XGBoost + daily HMM regime models:
cd backend && venv/bin/python3 ml_engine/models.py --train

# 2. refresh company financials & risk-quality classification:
make fundamentals
make classify

# 3. train production Core and Aggressive swing models on 2021→present:
make swing-train

# 4. restart servers (scheduler can stay up):
make serve-backend    # in one terminal
make serve-frontend   # in another
# verify: http://localhost:8008/api/train/status  +  http://localhost:8008/api/health
```

You can also retrain from the UI (Portfolio tab → **Model Training → Retrain**, a background job).

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
