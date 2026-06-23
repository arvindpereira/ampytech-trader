.PHONY: help default install bootstrap up \
        data fetch fetch-valuation fetch-market-stress fetch-forecasts backfill-news \
        news-llm news-recent news-llm-batch news-llm-batch-collect llm-usage premium-ingest insider fundamentals classify \
        train train-core swing-train train-deep walkforward calibrate \
        longterm-eval longterm-tilt swing-eval exec-timing stop-opt horizon-opt backtest \
        crash-refresh crash-forecast crash-backfill \
        simulate backtest-virtual \
        serve serve-all serve-backend serve-frontend schedule \
        popular-tickers add-ticker lint test clean-cache _expire \
        db-backup db-backup-list db-verify db-restore db-restore-commit \
        files-backup files-backup-list files-verify files-restore files-restore-commit \
        backup restore restore-commit

# --- Overridable parameters (e.g. `make train EPOCHS=50`, `make walkforward SPLITS=8`) ---
EPOCHS ?= 10
SEQ    ?= 20
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
FRONTEND_HOST ?= 0.0.0.0
FRONTEND_PORT ?= 3002
BACKEND_PORT  ?= 8008
WIKI_PORT     ?= 4000

# ----------------------------------------------------------------------------
# Cache-aware pipeline: TTL stamp files (so `make data`/`make train`/`make up`
# are no-ops when inputs are still fresh). Each stamp records the last successful
# run; `_expire` deletes any stamp older than its TTL (minutes) before the graph
# is evaluated, so a stale step rebuilds. `FORCE=1` invalidates every stamp.
# ----------------------------------------------------------------------------
STAMP_DIR := backend/.make
DATA_STAMP        := $(STAMP_DIR)/data.stamp
FUND_STAMP        := $(STAMP_DIR)/fundamentals.stamp
CLASSIFY_STAMP    := $(STAMP_DIR)/classify.stamp
NEWS_STAMP        := $(STAMP_DIR)/news.stamp
CORE_MODEL_STAMP  := $(STAMP_DIR)/train_core.stamp
SWING_MODEL_STAMP := $(STAMP_DIR)/train_swing.stamp

# Freshness windows in MINUTES (override on the CLI, e.g. `make up DATA_TTL=60`).
DATA_TTL     ?= 720      # 12h  — prices/macro/sentiment/valuation/market-stress
NEWS_TTL     ?= 720      # 12h  — incremental LLM news scoring
FUND_TTL     ?= 10080    # 7d   — company fundamentals
CLASSIFY_TTL ?= 10080    # 7d   — risk×quality tiering
MODEL_TTL    ?= 10080    # 7d   — trained models (matches the weekly retrain cadence)

# Recent-news window for the cache-aware incremental scoring step (last 7 days).
NEWS_RECENT_START := $(shell date -v-7d +%Y-%m-%d 2>/dev/null || date -d '7 days ago' +%Y-%m-%d 2>/dev/null)

# Default target: print help
default: help

help:
	@echo "========================================================================"
	@echo "                       AMPYTECH TRADER CLI TOOLBOX                      "
	@echo "========================================================================"
	@echo "Everyday (cache-aware — re-run freely; steps skip when still fresh):"
	@echo "  make up                - Bring up the WHOLE system: refresh data+models in order, then serve all"
	@echo "  make data              - All data fetches in one dependency-aware, cached step (FORCE=1 to refetch)"
	@echo "  make train             - Train ALL served models (HMM + XGBoost + Core/Aggressive swing); depends on data"
	@echo "  make serve-all         - Just launch backend + frontend + scheduler (no pipeline)"
	@echo "  make serve             - Just launch backend (:8008) + frontend (:3002, LAN-friendly)"
	@echo "  Cache knobs: FORCE=1 (rebuild everything) · DATA_TTL/MODEL_TTL=<minutes> · make clean-cache"
	@echo ""
	@echo "Setup:"
	@echo "  make install           - Create backend venv + install Python & Node deps"
	@echo "  make bootstrap         - First run: install, seed universe, fetch data, train models"
	@echo ""
	@echo "Pipeline internals (run automatically by data/train/up; usually no need to call directly):"
	@echo "  make fetch             - Force a data refresh now (invalidates the data cache, then runs data)"
	@echo "  make fundamentals      - Company financials → ratios (cached) [TICKERS=NVDA,BYND]"
	@echo "  make classify          - Risk×quality universe tiering (cached; needs data + fundamentals)"
	@echo "  make news-recent       - Incremental LLM-scoring of the last 7d of news (cached; needs Ollama)"
	@echo "  make train-core        - Train only the HMM regime + short-term XGBoost models (cached)"
	@echo "  make swing-train       - Train only the Core + Aggressive swing models (cached) [SWING_HORIZON=5]"
	@echo "  make train-deep        - Train deep swing model (GRU+Attn, daily+LLM, walk-forward comparable to XGBoost) [EPOCHS=$(EPOCHS) SEQ=$(SEQ)]"
	@echo "  make crash-refresh     - Refresh crash snapshot + coherent odds IF inputs changed (FORCE=1 to force)"
	@echo ""
	@echo "Evaluation & research (auto-fetch fresh data first):"
	@echo "  make walkforward       - Honest out-of-sample edge check (expanding folds) [SPLITS=$(SPLITS)]"
	@echo "  make calibrate         - Calibrate the served-model BUY threshold (needs trained core model)"
	@echo "  make swing-eval        - Swing walk-forward + portfolio sim, WITH vs WITHOUT LLM news [SWING_HORIZON=5]"
	@echo "  make longterm-eval     - Insider buying at the daily 1-3 month horizon [HORIZON=21]"
	@echo "  make longterm-tilt     - A/B backtest the insider-buy MPT tilt [STRENGTH=0.10]"
	@echo "  make exec-timing       - Forward-walk: best time of day to enter swing trades [OOS_START=2022-01-01]"
	@echo "  make stop-opt          - Forward-walk: optimize swing stop-loss & take-profit params"
	@echo "  make horizon-opt       - Forward-walk: optimize swing holding horizon"
	@echo "  make backtest          - In-sample PyBroker audit (short- + long-term); needs trained models"
	@echo "  make simulate          - Forward Virtual Broker simulation [DAYS=$(DAYS)]"
	@echo "  make backtest-virtual  - Look-ahead-free historical replay [MONTHS=$(MONTHS)]"
	@echo ""
	@echo "Occasional data ops (run when needed, not part of the everyday pipeline):"
	@echo "  make news-llm          - Full/range LLM news backfill [START=2021-01-01 PROVIDER=openai TICKERS=AAPL,NVDA]"
	@echo "  make news-llm-batch    - Same via OpenAI Batch API (cheapest, unattended; needs OPENAI_API_KEY)"
	@echo "  make news-llm-batch-collect BATCH_ID=<id> - Ingest a submitted batch's results"
	@echo "  make backfill-news     - Backfill historical daily news sentiment (~2021->now)"
	@echo "  make premium-ingest    - Ingest premium newsletter emails (IMAP) → swing news [DAYS=7 PREMIUM_FILE=path]"
	@echo "  make insider           - Fetch REAL SEC Form 4 insider data (set SEC_USER_AGENT; needs ALT_DATA_ENABLED)"
	@echo "  make fetch-forecasts   - Refresh Equity Advisor forecasts [TICKERS=ADBE,PINS]"
	@echo "  make fetch-valuation   - Fetch only Shiller CAPE + Buffett Indicator"
	@echo "  make fetch-market-stress - Fetch only credit spreads / financial-conditions / EBP"
	@echo "  make popular-tickers   - Find trending, active, gainer, and loser stocks"
	@echo "  make add-ticker TICKER=SYMBOL - Add a new symbol to your trading universe"
	@echo "  make refresh-metadata  - Backfill company profiles (name, CEO, sector, website…) [TICKERS=NVDA,AAPL FORCE=1]"
	@echo "  make llm-usage         - Show OpenAI token usage + est cost per model"
	@echo ""
	@echo "Crash Radar:"
	@echo "  make crash-forecast    - Recompute coherent drawdown odds for the latest snapshot only"
	@echo "  make crash-backfill    - Recompute coherent point-in-time odds for the ENTIRE snapshot history"
	@echo ""
	@echo "Backups (Google Drive, commit-stamped):"
	@echo "  make backup            - Back up the DB + files (models, caches, anchor CSVs & imported broker sources) [BACKUP_KEEP=10]"
	@echo "  make restore           - Restore the newest DB + files backup (or RESTORE=<name>)"
	@echo "  make restore-commit    - Restore the newest backup matching the current git commit"
	@echo "  make db-backup / files-backup / *-list / *-verify - Operate on just one side"
	@echo ""
	@echo "Misc:"
	@echo "  make lint              - Strip trailing whitespace / normalize line endings"
	@echo "  make test              - Run the test suite against an isolated throwaway DB"
	@echo "  make clean-cache       - Delete pipeline stamp files (forces a full refresh next run)"
	@echo "========================================================================"

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------
install:
	@echo "📦 Installing backend (venv + requirements) and frontend (npm) dependencies..."
	cd backend && python3 -m venv venv && venv/bin/pip install --upgrade pip && venv/bin/pip install -r requirements.txt
	cd frontend && npm install
	@echo "✅ Install complete."

bootstrap: install popular-tickers data train
	@echo "✅ Bootstrap complete. Run 'make up' to launch everything (or 'make serve-all')."

# ----------------------------------------------------------------------------
# Cache plumbing
# ----------------------------------------------------------------------------
$(STAMP_DIR):
	@mkdir -p $@

# Invalidate stale stamps (and everything when FORCE=1) before the graph is evaluated.
_expire: | $(STAMP_DIR)
	@if [ -n "$(FORCE)" ]; then echo "  ⏲  FORCE=1 — invalidating all pipeline caches"; rm -f $(STAMP_DIR)/*.stamp; fi
	@find $(DATA_STAMP)        -mmin +$(DATA_TTL)     -delete 2>/dev/null || true
	@find $(NEWS_STAMP)        -mmin +$(NEWS_TTL)     -delete 2>/dev/null || true
	@find $(FUND_STAMP)        -mmin +$(FUND_TTL)     -delete 2>/dev/null || true
	@find $(CLASSIFY_STAMP)    -mmin +$(CLASSIFY_TTL) -delete 2>/dev/null || true
	@find $(CORE_MODEL_STAMP)  -mmin +$(MODEL_TTL)    -delete 2>/dev/null || true
	@find $(SWING_MODEL_STAMP) -mmin +$(MODEL_TTL)    -delete 2>/dev/null || true

clean-cache:
	@echo "🧹 Clearing pipeline stamp cache ($(STAMP_DIR))..."
	@rm -rf $(STAMP_DIR)

# ----------------------------------------------------------------------------
# Everyday: the whole system in order, cache-aware
# ----------------------------------------------------------------------------
# `up` = make sure data + all models are fresh (in dependency order), THEN serve
# everything. Fast no-op on the data/model side when nothing has gone stale.
up: train
	@echo "========================================================================"
	@echo "🚀 Pipeline fresh — launching backend + frontend + scheduler. Ctrl+C stops all."
	@echo "========================================================================"
	@$(MAKE) serve-all

# ----------------------------------------------------------------------------
# Data ingestion (cache-aware umbrella + granular extras)
# ----------------------------------------------------------------------------
# One cached step for all core fetches (prices, macro, crisis eras, sentiment,
# valuation/CAPE, market-stress). Skips when the data cache is still fresh.
$(DATA_STAMP): | _expire
	@echo "========================================================================"
	@echo "🔄 Ingesting prices (hourly+daily), macro, crisis eras, sentiment, valuation, market-stress..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py fetch
	@touch $@

data: $(DATA_STAMP)
	@echo "✅ Data is fresh."

# Force a refresh now regardless of TTL (then re-stamp).
fetch:
	@rm -f $(DATA_STAMP)
	@$(MAKE) data

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

refresh-metadata:
	@echo "========================================================================"
	@echo "🏷️  Backfilling company profiles (name, CEO, sector, website…) for universe + held tickers"
	@echo "    [TICKERS=$(if $(TICKERS),$(TICKERS),all) FORCE=$(if $(FORCE),1,0)]"
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/ticker_metadata_fetcher.py $(if $(TICKERS),--tickers $(TICKERS),) $(if $(FORCE),--force,)
	@echo "✅ Metadata backfill complete."

research-kb-refresh:
	cd backend && $(VENV_PY) data_ingestion/research_kb_refresh.py

research-sectors-refresh:
	cd backend && $(VENV_PY) data_ingestion/sector_catalog_refresh.py --portfolio-only

research-sectors-refresh-full:
	cd backend && $(VENV_PY) data_ingestion/sector_catalog_refresh.py

research-sectors-refresh-fast:
	cd backend && $(VENV_PY) data_ingestion/sector_catalog_refresh.py --no-fetch

research-calibrate-factors:
	cd backend && $(VENV_PY) -m ml_engine.factor_calibrator

research-calibrate-factors-dry:
	cd backend && $(VENV_PY) -m ml_engine.factor_calibrator --no-write

research-wiki-export:
	cd backend && $(VENV_PY) -c "from ml_engine.research_wiki_export import rebuild_all; print(rebuild_all())"

# Serves static HTML from research-wiki/site (no Ruby/Jekyll). Publish a report first.
# Override port if busy: make research-wiki-serve WIKI_PORT=4001
research-wiki-serve: research-wiki-export
	@PORT=$(WIKI_PORT); \
	if lsof -ti:$$PORT >/dev/null 2>&1; then \
	  echo "Port $$PORT already in use (maybe a prior wiki server)."; \
	  echo "  Stop it:  kill $$(lsof -ti:$$PORT)"; \
	  echo "  Or use:   make research-wiki-serve WIKI_PORT=4001"; \
	  exit 1; \
	fi; \
	echo "Serving at http://localhost:$$PORT (Ctrl+C to stop)"; \
	cd research-wiki/site && $(CURDIR)/backend/$(VENV_PY) -m http.server $$PORT --bind 0.0.0.0

# Optional: Jekyll build (requires Ruby 3+ and: cd research-wiki && bundle install)
research-wiki-serve-jekyll: research-wiki-export
	cd research-wiki && bundle exec jekyll serve --host 0.0.0.0 --port $(WIKI_PORT)

import-equity-lots:
	@test -n "$(FILE)" || (echo "Usage: make import-equity-lots FILE=path/to/export.pdf [REPLACE=1] [FORCE_LLM=1]" && exit 1)
	cd backend && $(VENV_PY) run.py import-equity-lots --file "$(FILE)" $(if $(REPLACE),--replace,) $(if $(FORCE_LLM),--force-llm,)

backfill-news:
	@echo "========================================================================"
	@echo "📰 Backfilling historical daily news sentiment (~2021 -> now)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/sentiment_fetcher.py --backfill
	@echo "✅ News sentiment backfill complete."

# Company fundamentals (cached). Independent API, feeds classification.
$(FUND_STAMP): | _expire
	@echo "📊 Ingesting company fundamentals (Polygon financials) → ratios$(if $(TICKERS), [TICKERS=$(TICKERS)])..."
	cd backend && $(VENV_PY) data_ingestion/fundamentals_fetcher.py $(if $(TICKERS),--tickers $(TICKERS))
	@touch $@

fundamentals: $(FUND_STAMP)

# Risk×quality tiering (cached). Needs prices (data) + fundamentals.
$(CLASSIFY_STAMP): $(DATA_STAMP) $(FUND_STAMP) | _expire
	@echo "🏷️  Classifying universe into risk × fundamental-quality tiers (quant + LLM overlay)..."
	cd backend && $(VENV_PY) -m ml_engine.classify --run-llm
	@touch $@

classify: $(CLASSIFY_STAMP)

# Incremental, cache-aware LLM news scoring of the recent window (feeds swing).
$(NEWS_STAMP): $(DATA_STAMP) | _expire
	@echo "🗞️  Scoring the last 7 days of news headlines (resumable; needs Ollama or PROVIDER=openai)..."
	cd backend && $(VENV_PY) data_ingestion/news_llm.py --start $(NEWS_RECENT_START) \
		$(if $(PROVIDER),--provider $(PROVIDER)) $(if $(WORKERS),--workers $(WORKERS))
	@touch $@

news-recent: $(NEWS_STAMP)

insider:
	@echo "========================================================================"
	@echo "🏛️  Fetching REAL SEC EDGAR Form 4 insider transactions (set SEC_USER_AGENT)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) data_ingestion/alternative_fetcher.py
	@echo "✅ Insider Form 4 ingest complete (enable ALT_DATA_ENABLED to use in features)."

# ----------------------------------------------------------------------------
# Models (cache-aware, dependency-driven)
# ----------------------------------------------------------------------------
# HMM regime + short-term XGBoost (the deep PyTorch net is split out to train-deep).
$(CORE_MODEL_STAMP): $(DATA_STAMP) | _expire
	@echo "🧠 Training HMM regime + short-term XGBoost models..."
	cd backend && $(VENV_PY) ml_engine/models.py --train
	@touch $@

train-core: $(CORE_MODEL_STAMP)

# Production swing models (Core + Aggressive). Needs tiering + fresh news features.
$(SWING_MODEL_STAMP): $(CLASSIFY_STAMP) $(NEWS_STAMP) | _expire
	@echo "🪁 Training + saving the Core + Aggressive swing models [SWING_HORIZON=$(SWING_HORIZON)]..."
	cd backend && $(VENV_PY) run.py swing-train --horizon $(SWING_HORIZON)
	@touch $@

swing-train: $(SWING_MODEL_STAMP)

# Train EVERYTHING that gets served, in dependency order, then refresh crash odds.
train: $(CORE_MODEL_STAMP) $(SWING_MODEL_STAMP) crash-refresh
	@echo "✅ All served models trained (HMM + short-term XGBoost + swing Core/Aggressive); crash odds refreshed."
	@echo "   Artifacts in backend/ml_engine/saved_models/"

# Deep swing model: same daily-bar + LLM-news pipeline as XGBoost swing, GRU+Attention architecture.
# Run `make train-deep` after `make swing-train` to get comparison data in the Model Evaluation tab.
train-deep: $(DATA_STAMP)
	@echo "🧠 Training deep swing model (GRU+Attention, $(EPOCHS) epochs, seq=$(SEQ))..."
	cd backend && $(VENV_PY) ml_engine/deep_models.py --train --epochs $(EPOCHS) --seq $(SEQ)

walkforward: $(DATA_STAMP)
	@echo "========================================================================"
	@echo "🔬 Walk-forward out-of-sample evaluation ($(SPLITS) expanding folds) — the honest edge check..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py walkforward --splits $(SPLITS)

calibrate: $(CORE_MODEL_STAMP)
	@echo "========================================================================"
	@echo "🎯 Calibrating the served-model BUY threshold (writes saved_models/threshold.json)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py calibrate

longterm-eval: $(DATA_STAMP)
	@echo "========================================================================"
	@echo "🔭 Long-term insider-alpha walk-forward (daily, ~1-3 month horizon) [HORIZON=$(HORIZON)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py longterm-eval --horizon $(HORIZON)

longterm-tilt: $(DATA_STAMP)
	@echo "========================================================================"
	@echo "🪙 Long-term MPT insider-buy tilt A/B backtest [STRENGTH=$(STRENGTH)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py longterm-tilt --tilt-strength $(STRENGTH)

swing-eval: $(DATA_STAMP) $(CLASSIFY_STAMP) $(NEWS_STAMP)
	@echo "========================================================================"
	@echo "🪁 Swing walk-forward + portfolio sim — WITH vs WITHOUT LLM news [SWING_HORIZON=$(SWING_HORIZON)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py swing-eval --horizon $(SWING_HORIZON) --splits $(SPLITS)

exec-timing: $(DATA_STAMP) $(NEWS_STAMP)
	@echo "========================================================================"
	@echo "🕐 Execution-timing forward-walk: when to enter swing trades (open→close) [OOS_START=$(OOS_START) SWING_HORIZON=$(SWING_HORIZON) SPLITS=$(SPLITS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.exec_timing --horizon $(SWING_HORIZON) --splits $(SPLITS) --oos-start $(OOS_START)

stop-opt: $(DATA_STAMP) $(NEWS_STAMP)
	@echo "========================================================================"
	@echo "🛑 Stop/TP optimization forward-walk for the swing strategy [OOS_START=$(OOS_START) SWING_HORIZON=$(SWING_HORIZON) SPLITS=$(SPLITS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.stop_opt --horizon $(SWING_HORIZON) --splits $(SPLITS) --oos-start $(OOS_START)

horizon-opt: $(DATA_STAMP) $(NEWS_STAMP)
	@echo "========================================================================"
	@echo "📆 Holding-horizon optimization forward-walk for the swing strategy [OOS_START=$(OOS_START) SPLITS=$(SPLITS)]..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.horizon_opt --splits $(SPLITS) --oos-start $(OOS_START)

backtest: train
	@echo "========================================================================"
	@echo "📈 In-sample PyBroker audit (short-term hourly + long-term daily MPT)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py backtest

# ----------------------------------------------------------------------------
# Occasional / manual LLM-news + usage
# ----------------------------------------------------------------------------
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

# ----------------------------------------------------------------------------
# Crash Radar (defensive strategist)
# ----------------------------------------------------------------------------
crash-refresh:
	@echo "========================================================================"
	@echo "🛡️  Crash Radar: refreshing snapshot + coherent drawdown odds if data changed$(if $(FORCE), [FORCE]) ..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) -m ml_engine.crash_model --refresh $(if $(FORCE),--force)

crash-forecast:
	@echo "========================================================================"
	@echo "🛡️  Crash Radar: recomputing coherent drawdown odds for the latest snapshot..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py crash-forecast

crash-backfill:
	@echo "========================================================================"
	@echo "🛡️  Crash Radar: recomputing coherent point-in-time odds for ALL snapshots..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py crash-backfill

# ----------------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------------
simulate: $(DATA_STAMP)
	@echo "========================================================================"
	@echo "🔮 Forward Virtual Broker simulation ($(DAYS) trading days)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py simulate --days $(DAYS)
	@echo "✅ Simulation run complete."

backtest-virtual: $(DATA_STAMP)
	@echo "========================================================================"
	@echo "⏳ Look-ahead-free historical replay ($(MONTHS) months)..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run.py backtest-virtual --months $(MONTHS)
	@echo "✅ Historical replay simulation complete."

# ----------------------------------------------------------------------------
# Backups (Google Drive, commit-stamped)
# ----------------------------------------------------------------------------
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
	@echo "☁️  Backing up trading files (models, configs & cached JSON) to Google Drive (keeps newest $(BACKUP_KEEP))..."
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

# ----------------------------------------------------------------------------
# Run / misc
# ----------------------------------------------------------------------------
serve:
	@echo "========================================================================"
	@echo "Launching Ampytech Trader full stack..."
	@echo "🚀 Backend API: http://0.0.0.0:$(BACKEND_PORT) (also http://localhost:$(BACKEND_PORT))"
	@echo "🎨 Web Interface: http://$(FRONTEND_HOST):$(FRONTEND_PORT) (LAN: use this machine's IP, e.g. http://10.0.0.43:$(FRONTEND_PORT))"
	@echo "Press Ctrl+C to terminate both servers."
	@echo "========================================================================"
	@bash -c 'trap "kill 0" EXIT; (cd backend && $(VENV_PY) run.py serve) & (cd frontend && npm run dev -- -H $(FRONTEND_HOST) -p $(FRONTEND_PORT)) & wait'

serve-backend:
	cd backend && $(VENV_PY) run.py serve

serve-frontend:
	cd frontend && npm run dev -- -H $(FRONTEND_HOST) -p $(FRONTEND_PORT)

# Lean: backend (:8008) + frontend (:3002) + scheduler daemon, no pipeline.
# Kills any pre-existing scheduler first so you never end up with duplicates; Ctrl+C stops all three.
serve-all:
	@echo "========================================================================"
	@echo "🚀 Backend :$(BACKEND_PORT)  🎨 Frontend :$(FRONTEND_PORT) ($(FRONTEND_HOST))  ⏰ Scheduler — Ctrl+C stops all."
	@echo "   LAN: open http://<this-machine-ip>:$(FRONTEND_PORT)  (API follows the same host on :$(BACKEND_PORT))"
	@echo "========================================================================"
	@pkill -f "run.py schedule" 2>/dev/null || true; pkill -f "execution/scheduler.py" 2>/dev/null || true; sleep 1
	@bash -c 'trap "kill 0; pkill -f \"execution/scheduler.py\" 2>/dev/null" EXIT; \
		(cd backend && $(VENV_PY) run.py serve) & \
		(cd frontend && npm run dev -- -H $(FRONTEND_HOST) -p $(FRONTEND_PORT)) & \
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

test:
	@echo "========================================================================"
	@echo "🧪 Running test suite against an isolated throwaway database..."
	@echo "========================================================================"
	cd backend && $(VENV_PY) run_tests.py $(TESTS)
	@echo "✅ Tests complete."

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
