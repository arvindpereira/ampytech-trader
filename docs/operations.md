# Operations & Runbook

## 1. Setup

```bash
make install            # backend venv + Python deps + frontend npm deps
# Fill backend/.env: MASSIVE_API_KEY (Polygon), ALPACA_API_KEY/SECRET (paper), FRED, Reddit (optional),
#                    GOOGLE_OAUTH_CLIENT_ID/SECRET (for DB backup). Defaults point Alpaca at paper.
# Install + run Ollama locally with the LLM_MODEL (default gemma4:e4b) for news scoring.
```

## 2. Day-to-day commands (Makefile → `run.py` / scripts)

**Data**
- `make fetch` — hourly+daily prices, macro, sentiment, crisis eras.
- `make news-llm [NEWS_START=2021-01-01]` — LLM-score news (Ollama) for the swing model.
- `make insider` — real SEC Form 4 (only when `ALT_DATA_ENABLED`).

**Models**
- `make train [EPOCHS=]` — XGBoost (hourly) + HMM regime + PyTorch.
- `make swing-train [SWING_HORIZON=5]` — train + save the served swing model.
- `make walkforward [SPLITS=]` / `make calibrate` — honest OOS check / threshold calibration.
- `make swing-eval` / `make longterm-eval` / `make longterm-tilt` — research evaluations.
- `make backtest` — in-sample PyBroker audit.

**DB backup (Google Drive)** — the DB is **not** in git/LFS; back it up here:
- `make db-backup [BACKUP_KEEP=10]` — upload a commit-stamped copy.
- `make db-backup-list` — list backups (with their commit).
- `make db-verify` — download + validate a backup WITHOUT touching the live DB.
- `make db-restore` / `make db-restore-commit` — restore newest / the one matching the current commit.

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
# retrain the served models, then restart the servers (scheduler can stay up):
cd backend && venv/bin/python3 ml_engine/models.py --train   # XGBoost + HMM
make swing-train                                             # swing (now trains on 2021→present)
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

`GET /api/health` (and the navbar pills) report **Ollama, Alpaca, scheduler, DB, news** status. The
swing pipeline needs **Ollama up**; if it stops, news scoring stalls (degrades gracefully). The
intraday loop and execution require the **Alpaca paper** creds and (for execution) an open market.
