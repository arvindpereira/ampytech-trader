.PHONY: help default install fetch backfill-news insider train walkforward calibrate longterm-eval longterm-tilt backtest \
        simulate backtest-virtual schedule serve serve-backend serve-frontend bootstrap lint popular-tickers add-ticker

# --- Overridable parameters (e.g. `make train EPOCHS=50`, `make walkforward SPLITS=8`) ---
EPOCHS ?= 100
DAYS   ?= 5
MONTHS ?= 6
SPLITS ?= 5
HORIZON ?= 21
STRENGTH ?= 0.10
TICKER   ?=
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
	@echo "  make backfill-news     - Backfill historical daily news sentiment (~2021->now)"
	@echo "  make insider           - Fetch REAL SEC Form 4 insider data (set SEC_USER_AGENT)"
	@echo ""
	@echo "Models:"
	@echo "  make train             - Train XGBoost (hourly) + HMM (daily) + PyTorch  [EPOCHS=$(EPOCHS)]"
	@echo "  make walkforward       - Honest out-of-sample edge check (expanding folds) [SPLITS=$(SPLITS)]"
	@echo "  make calibrate         - Calibrate the served-model BUY threshold (-> threshold.json)"
	@echo "  make longterm-eval     - Test insider buying at the daily 1-3 month horizon [HORIZON=21]"
	@echo "  make longterm-tilt     - A/B backtest the insider-buy MPT tilt [STRENGTH=0.10]"
	@echo "  make backtest          - In-sample PyBroker audit (short- + long-term)"
	@echo ""
	@echo "Simulation:"
	@echo "  make simulate          - Forward Virtual Broker simulation              [DAYS=$(DAYS)]"
	@echo "  make backtest-virtual  - Look-ahead-free historical replay              [MONTHS=$(MONTHS)]"
	@echo ""
	@echo "Run / misc:"
	@echo "  make serve             - Launch FastAPI (:8008) + Next.js (:3002) together"
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

bootstrap: fetch train
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

backfill-news:
	@echo "========================================================================"
	@echo "📰 Backfilling historical daily news sentiment (~2021 -> now)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/sentiment_fetcher.py --backfill
	@echo "✅ News sentiment backfill complete."

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

schedule:
	@echo "========================================================================"
	@echo "⏰ Starting APScheduler daemon (daily fetch/inference/execution + weekly retrain)..."
	@echo "========================================================================"
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

