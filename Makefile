.PHONY: help default install fetch fetch-valuation fetch-market-stress fetch-forecasts backfill-news news-llm insider fundamentals classify train walkforward calibrate longterm-eval longterm-tilt swing-eval swing-train backtest \
        news-llm-batch news-llm-batch-collect llm-usage premium-ingest exec-timing stop-opt horizon-opt \
        db-backup db-backup-list db-restore db-restore-commit \
        files-backup files-backup-list files-verify files-restore files-restore-commit backup restore restore-commit \
        simulate backtest-virtual schedule serve serve-all serve-backend serve-frontend bootstrap lint popular-tickers add-ticker

# --- Overridable parameters (e.g. `make train EPOCHS=50`, `make walkforward SPLITS=8`) ---
EPOCHS ?= 10
DAYS   ?= 5
MONTHS ?= 6
SPLITS ?= 5
HORIZON ?= 21
SWING_HORIZON ?= 5
STRENGTH ?= 0.10
OOS_START ?= 2022-01-01
# Full LLM-news window: matches NEWS_LLM_START so `make news-llm` backfills everything to 2021.
START ?= 2021-01-01
NEWS_START ?= $(START)
LLM_MODEL ?= gemma4:e4b
PROVIDER ?=
WORKERS  ?=
BATCH_ID ?=
TICKER   ?=
TICKERS  ?=
BACKUP_KEEP ?= 10
RESTORE  ?=
VENV_PY := venv/bin/python3

# Default target: print help
default: help

help:
	@echo "========================================================================"
	@echo "                       AMPYTECH TRADER CLI TOOLBOX                      "
	@echo "========================================================================"
	@echo "Setup:"
	@echo "  make install           - Create backend venv + install Python & Node deps"
	@echo "  make bootstrap         - First run: fetch data then train models"
	@echo ""
	@echo "Data:"
	@echo "  make fetch             - Hourly+daily prices, macro, sentiment, crisis eras"
	@echo "  make fetch-forecasts   - Refresh Equity Advisor forecasts [TICKERS=ADBE,PINS]"
	@echo "  make backfill-news     - Backfill historical daily news sentiment (~2021->now)"
	@echo "  make insider           - Fetch REAL SEC Form 4 insider data (set SEC_USER_AGENT)"
	@echo "  make fundamentals      - Ingest company financials (Polygon) & derived ratios [TICKERS=NVDA,BYND]"
	@echo "  make classify          - Tier the universe by risk x fundamental-quality (quant + LLM)"
	@echo "  make news-llm          - LLM-score news headlines for the swing model [START=2021-01-01 PROVIDER=openai TICKERS=AAPL,NVDA]"
	@echo "  make news-llm-batch    - Same via OpenAI Batch API (cheapest, unattended; needs OPENAI_API_KEY)"
	@echo "  make news-llm-batch-collect BATCH_ID=<id> - Ingest a submitted batch's results"
	@echo "  make premium-ingest    - Ingest premium newsletter emails (IMAP) → swing news [DAYS=7 PREMIUM_FILE=path DRY_RUN=1]"
	@echo "  make llm-usage         - Show OpenAI token usage + est cost per model (refine pricing)"
	@echo "  make db-backup         - Back up the trading DB to Google Drive [BACKUP_KEEP=10]"
	@echo "  make db-backup-list    - List DB backups in the Google Drive folder"
	@echo "  make db-restore        - Restore a DB backup (newest, or RESTORE=<name>)"
	@echo "  make db-restore-commit - Restore the newest DB backup matching the current git commit"
	@echo "  make backup            - Back up both the database and files (models/configs) to Google Drive"
	@echo "  make restore           - Restore the newest database and files backup (or RESTORE=<name>)"
	@echo "  make restore-commit    - Restore the newest database and files backup matching the current git commit"
	@echo ""
	@echo "Models:"
	@echo "  make train             - Train XGBoost (hourly) + HMM (daily) + PyTorch  [EPOCHS=$(EPOCHS)]"
	@echo "  make walkforward       - Honest out-of-sample edge check (expanding folds) [SPLITS=$(SPLITS)]"
	@echo "  make calibrate         - Calibrate the served-model BUY threshold (-> threshold.json)"
	@echo "  make longterm-eval     - Test insider buying at the daily 1-3 month horizon [HORIZON=21]"
	@echo "  make longterm-tilt     - A/B backtest the insider-buy MPT tilt [STRENGTH=0.10]"
	@echo "  make swing-eval        - Swing model walk-forward + portfolio sim, WITH vs WITHOUT LLM news [SWING_HORIZON=5]"
	@echo "  make swing-train       - Train + save the production swing model served in the UI [SWING_HORIZON=5]"
	@echo "  make exec-timing       - Forward-walk: best time of day to enter swing trades [OOS_START=2022-01-01]"
	@echo "  make stop-opt          - Forward-walk: optimize swing stop-loss & take-profit params [OOS_START=2022-01-01]"
	@echo "  make horizon-opt       - Forward-walk: optimize swing holding horizon [OOS_START=2022-01-01]"
	@echo "  make backtest          - In-sample PyBroker audit (short- + long-term)"
	@echo ""
	@echo "Simulation:"
	@echo "  make simulate          - Forward Virtual Broker simulation              [DAYS=$(DAYS)]"
	@echo "  make backtest-virtual  - Look-ahead-free historical replay              [MONTHS=$(MONTHS)]"
	@echo ""
	@echo "Run / misc:"
	@echo "  make serve             - Launch FastAPI (:8008) + Next.js (:3002) together"
	@echo "  make serve-all         - Launch backend + frontend + scheduler together (one process, no duplicates)"
	@echo "  make serve-backend     - Launch only the FastAPI backend (:8008)"
	@echo "  make serve-frontend    - Launch only the Next.js frontend (:3002)"
	@echo "  make schedule          - Run the APScheduler daemon (unattended fetch/train/execute)"
	@echo "  make popular-tickers   - Find trending, active, gainer, and loser stocks"
	@echo "  make add-ticker TICKER=SYMBOL - Add a new symbol to your trading universe"
	@echo "  make lint              - Strip trailing whitespace / normalize line endings"
	@echo "========================================================================"

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------
install:
	@echo "📦 Installing backend (venv + requirements) and frontend (npm) dependencies..."
	cd backend && python3 -m venv venv && venv/bin/pip install --upgrade pip && venv/bin/pip install -r requirements.txt
	cd frontend && npm install
	@echo "✅ Install complete."

bootstrap: popular-tickers add-ticker fetch train
	@echo "✅ Bootstrap complete. Run 'make serve' to start the app."

# ----------------------------------------------------------------------------
# Data ingestion
# ----------------------------------------------------------------------------
fetch:
	@echo "========================================================================"
	@echo "🔄 Ingesting hourly (Massive) + daily (Yahoo) prices, macro, sentiment, crisis eras..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py fetch
	@echo "✅ Data fetch complete."

fetch-valuation:
	cd backend && $(VENV_PY) data_ingestion/valuation_fetcher.py

fetch-market-stress:
	cd backend && $(VENV_PY) data_ingestion/market_stress_fetcher.py

fetch-forecasts:
	@echo "========================================================================"
	@echo "📈 Refreshing Equity Advisor forecast snapshots [TICKERS=$(if $(TICKERS),$(TICKERS),ADBE,PINS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py fetch-forecasts --tickers $(if $(TICKERS),$(TICKERS),ADBE,PINS)
	@echo "✅ Forecast refresh complete."

backfill-news:
	@echo "========================================================================"
	@echo "📰 Backfilling historical daily news sentiment (~2021 -> now)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/sentiment_fetcher.py --backfill
	@echo "✅ News sentiment backfill complete."

fundamentals:
	@echo "========================================================================"
	@echo "📊 Ingesting company fundamentals (Polygon/Massive financials) → ticker_fundamentals$(if $(TICKERS), [TICKERS=$(TICKERS)])..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/fundamentals_fetcher.py $(if $(TICKERS),--tickers $(TICKERS))

classify:
	@echo "========================================================================"
	@echo "🏷️  Classifying universe into risk × fundamental-quality tiers (quant + LLM overlay)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.classify --run-llm

insider:
	@echo "========================================================================"
	@echo "🏛️  Fetching REAL SEC EDGAR Form 4 insider transactions (set SEC_USER_AGENT)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/alternative_fetcher.py
	@echo "✅ Insider Form 4 ingest complete (enable ALT_DATA_ENABLED to use in features)."

# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------
train:
	@echo "========================================================================"
	@echo "🧠 Training short-term XGBoost (hourly) + HMM regime (daily) + PyTorch ($(EPOCHS) epochs)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py train --epochs $(EPOCHS)
	@echo "✅ Training complete. Artifacts in backend/ml_engine/saved_models/"

walkforward:
	@echo "========================================================================"
	@echo "🔬 Walk-forward out-of-sample evaluation ($(SPLITS) expanding folds) — the honest edge check..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py walkforward --splits $(SPLITS)

calibrate:
	@echo "========================================================================"
	@echo "🎯 Calibrating the served-model BUY threshold (writes saved_models/threshold.json)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py calibrate

longterm-eval:
	@echo "========================================================================"
	@echo "🔭 Long-term insider-alpha walk-forward (daily, ~1-3 month horizon) [HORIZON=$(HORIZON)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py longterm-eval --horizon $(HORIZON)

exec-timing:
	@echo "========================================================================"
	@echo "🕐 Execution-timing forward-walk: when to enter swing trades (open→close) [OOS_START=$(OOS_START) SWING_HORIZON=$(SWING_HORIZON) SPLITS=$(SPLITS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.exec_timing --horizon $(SWING_HORIZON) --splits $(SPLITS) --oos-start $(OOS_START)

stop-opt:
	@echo "========================================================================"
	@echo "🛑 Stop/TP optimization forward-walk for the swing strategy [OOS_START=$(OOS_START) SWING_HORIZON=$(SWING_HORIZON) SPLITS=$(SPLITS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.stop_opt --horizon $(SWING_HORIZON) --splits $(SPLITS) --oos-start $(OOS_START)

horizon-opt:
	@echo "========================================================================"
	@echo "📆 Holding-horizon optimization forward-walk for the swing strategy [OOS_START=$(OOS_START) SPLITS=$(SPLITS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.horizon_opt --splits $(SPLITS) --oos-start $(OOS_START)

news-llm:
	@echo "========================================================================"
	@echo "🗞️  LLM-scoring news headlines [START=$(NEWS_START)$(if $(PROVIDER), PROVIDER=$(PROVIDER))$(if $(TICKERS), TICKERS=$(TICKERS))]..."
	@echo "    (default provider = local Ollama; PROVIDER=openai for a fast bulk backfill)"
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/news_llm.py --start $(NEWS_START) \
		$(if $(TICKERS),--tickers $(TICKERS)) $(if $(PROVIDER),--provider $(PROVIDER)) $(if $(WORKERS),--workers $(WORKERS))

news-llm-batch:
	@echo "========================================================================"
	@echo "🗞️  Submitting OpenAI Batch news-scoring job (cheapest, unattended) [START=$(NEWS_START)$(if $(TICKERS), TICKERS=$(TICKERS))]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/news_llm.py --start $(NEWS_START) --batch \
		$(if $(TICKERS),--tickers $(TICKERS))

news-llm-batch-collect:
	@echo "========================================================================"
	@echo "🗞️  Ingesting OpenAI Batch results [BATCH_ID=$(BATCH_ID)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/news_llm.py --collect $(BATCH_ID)

premium-ingest:
	@echo "========================================================================"
	@echo "📬 Ingesting premium newsletter articles via IMAP → news_llm_scores$(if $(PREMIUM_FILE), [FILE=$(PREMIUM_FILE)], [DAYS=$(DAYS)])..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/premium_ingest.py $(if $(PREMIUM_FILE),--file $(PREMIUM_FILE),--days $(DAYS)) $(if $(DRY_RUN),--dry-run)

llm-usage:
	@echo "========================================================================"
	@echo "📊 OpenAI token usage + estimated cost per model (from the usage ledger)..."
	@echo "    Divide your real dashboard spend by these tokens to refine pricing."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -c "from app.core.llm_cost import usage_summary; import json; print(json.dumps(usage_summary(), indent=2))"

db-backup:
	@echo "========================================================================"
	@echo "☁️  Backing up the trading DB to Google Drive (keeps newest $(BACKUP_KEEP))..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) scripts/db_backup.py --keep $(BACKUP_KEEP)

db-backup-list:
	cd backend && $(VENV_PY) scripts/db_backup.py --list

db-verify:
	cd backend && $(VENV_PY) scripts/db_backup.py --verify $(RESTORE)

db-restore:
	cd backend && $(VENV_PY) scripts/db_backup.py --restore $(RESTORE)

db-restore-commit:
	cd backend && $(VENV_PY) scripts/db_backup.py --restore-commit

files-backup:
	@echo "========================================================================"
	@echo "☁️  Backing up trading files (models/configs) to Google Drive (keeps newest $(BACKUP_KEEP))..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) scripts/db_backup.py --files --keep $(BACKUP_KEEP)

files-backup-list:
	cd backend && $(VENV_PY) scripts/db_backup.py --files --list

files-verify:
	cd backend && $(VENV_PY) scripts/db_backup.py --files --verify $(RESTORE)

files-restore:
	cd backend && $(VENV_PY) scripts/db_backup.py --files --restore $(RESTORE)

files-restore-commit:
	cd backend && $(VENV_PY) scripts/db_backup.py --files --restore-commit

backup: db-backup files-backup

restore: db-restore files-restore

restore-commit: db-restore-commit files-restore-commit


swing-eval:
	@echo "========================================================================"
	@echo "🪁 Swing walk-forward + portfolio sim — WITH vs WITHOUT LLM news [SWING_HORIZON=$(SWING_HORIZON)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py swing-eval --horizon $(SWING_HORIZON) --splits $(SPLITS)

swing-train:
	@echo "========================================================================"
	@echo "🪁 Training + saving the production swing model [SWING_HORIZON=$(SWING_HORIZON)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py swing-train --horizon $(SWING_HORIZON)

longterm-tilt:
	@echo "========================================================================"
	@echo "🪙 Long-term MPT insider-buy tilt A/B backtest [STRENGTH=$(STRENGTH)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py longterm-tilt --tilt-strength $(STRENGTH)

backtest:
	@echo "========================================================================"
	@echo "📈 In-sample PyBroker audit (short-term hourly + long-term daily MPT)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py backtest

# ----------------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------------
simulate:
	@echo "========================================================================"
	@echo "🔮 Forward Virtual Broker simulation ($(DAYS) trading days)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py simulate --days $(DAYS)
	@echo "✅ Simulation run complete."

backtest-virtual:
	@echo "========================================================================"
	@echo "⏳ Look-ahead-free historical replay ($(MONTHS) months)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py backtest-virtual --months $(MONTHS)
	@echo "✅ Historical replay simulation complete."

# ----------------------------------------------------------------------------
# Run / misc
# ----------------------------------------------------------------------------
serve:
	@echo "========================================================================"
	@echo "Launching Ampytech Trader full stack..."
	@echo "🚀 Backend API: http://localhost:8008"
	@echo "🎨 Web Interface: http://localhost:3002"
	@echo "Press Ctrl+C to terminate both servers."
	@echo "========================================================================"
	@bash -c 'trap "kill 0" EXIT; (cd backend && $(VENV_PY) run.py serve) & (cd frontend && npm run dev -- -p 3002) & wait'

serve-backend:
	cd backend && $(VENV_PY) run.py serve

serve-frontend:
	cd frontend && npm run dev -- -p 3002

# One command for everything: backend (:8008) + frontend (:3002) + the scheduler daemon.
# Kills any pre-existing scheduler first so you never end up with duplicates; Ctrl+C stops all three.
serve-all:
	@echo "========================================================================"
	@echo "🚀 Backend :8008  🎨 Frontend :3002  ⏰ Scheduler — all together. Ctrl+C stops all."
	@echo "========================================================================"
	@pkill -f "run.py schedule" 2>/dev/null || true; pkill -f "execution/scheduler.py" 2>/dev/null || true; sleep 1
	@bash -c 'trap "kill 0; pkill -f \"execution/scheduler.py\" 2>/dev/null" EXIT; \
		(cd backend && $(VENV_PY) run.py serve) & \
		(cd frontend && npm run dev -- -p 3002) & \
		(cd backend && $(VENV_PY) run.py schedule) & wait'

schedule:
	@echo "========================================================================"
	@echo "⏰ Starting APScheduler daemon (daily fetch/inference/execution + weekly retrain)..."
	@echo "    Stopping any existing scheduler first (avoids duplicate daemons)..."
	@echo "========================================================================"
	@pkill -f "run.py schedule" 2>/dev/null || true; pkill -f "execution/scheduler.py" 2>/dev/null || true; sleep 1
	cd backend && $(VENV_PY) run.py schedule

lint:
	@echo "========================================================================"
	@echo "🧹 Cleaning up trailing whitespace errors and formatting line endings..."
	@echo "========================================================================"
	python3 backend/lint.py
	@echo "✅ Formatting complete. Ready to review and commit!"

popular-tickers:
	@echo "========================================================================"
	@echo "🔥 Fetching trending, active, gainer, and loser stocks..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py popular-tickers

add-ticker:
	@echo "========================================================================"
	@echo "➕ Adding ticker $(TICKER) to the database..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py add-ticker --symbol "$(TICKER)"
