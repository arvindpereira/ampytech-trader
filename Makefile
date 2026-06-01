.PHONY: help default serve fetch train backtest simulate backtest-virtual lint

# Default target: Launches both servers and outputs their locations
default:
	@$(MAKE) serve

help:
	@echo "========================================================================"
	@echo "                       AMPYTECH TRADER CLI TOOLBOX                      "
	@echo "========================================================================"
	@echo "Available Makefile targets:"
	@echo "  make serve             - Launches FastAPI (port 8008) & Next.js (port 3002) in parallel"
	@echo "  make fetch             - Synchronizes stock prices, macro statistics, and sentiments"
	@echo "  make train             - Re-trains XGBoost Breakout and HMM-MPT models"
	@echo "  make backtest          - Audits theoretical model backtest return & drawdown (2yr)"
	@echo "  make simulate          - Runs forward daily simulations on the Virtual Broker"
	@echo "  make backtest-virtual  - Runs day-by-day historical replay loop over past N months"
	@echo "  make lint              - Programmatically strips trailing whitespaces and formats files"
	@echo "========================================================================"

serve:
	@echo "========================================================================"
	@echo "Launching Ampytech Trader full stack..."
	@echo "🚀 Backend API: http://localhost:8008"
	@echo "🎨 Web Interface: http://localhost:3002"
	@echo "Press Ctrl+C to terminate both servers."
	@echo "========================================================================"
	@bash -c 'trap "kill 0" EXIT; (cd backend && venv/bin/python3 run.py serve) & (cd frontend && npm run dev -- -p 3002) & wait'

fetch:
	@echo "========================================================================"
	@echo "🔄 Running ingestion pipelines (OHLCV prices, FRED macro indicators, news/Reddit sentiment)..."
	@echo "========================================================================"
	cd backend && venv/bin/python3 run.py fetch
	@echo "✅ Data fetch complete."

train:
	@echo "========================================================================"
	@echo "🧠 Training XGBoost breakout models & HMM portfolio optimizer..."
	@echo "========================================================================"
	cd backend && venv/bin/python3 run.py train
	@echo "✅ Training complete. Artifacts saved in backend/ml_engine/saved_models/"

backtest:
	@echo "========================================================================"
	@echo "📈 Running theoretical PyBroker strategy backtest suite (past 2 years)..."
	@echo "========================================================================"
	cd backend && venv/bin/python3 run.py backtest

simulate:
	@echo "========================================================================"
	@echo "🔮 Executing forward Virtual Broker simulation for 5 trading days..."
	@echo "========================================================================"
	cd backend && venv/bin/python3 run.py simulate --days 5
	@echo "✅ Simulation run complete."

backtest-virtual:
	@echo "========================================================================"
	@echo "⏳ Executing look-ahead free historical replay simulation (6 months)..."
	@echo "========================================================================"
	cd backend && venv/bin/python3 run.py backtest-virtual --months 6
	@echo "✅ Historical replay simulation complete."

lint:
	@echo "========================================================================"
	@echo "🧹 Cleaning up trailing whitespace errors and formatting line endings..."
	@echo "========================================================================"
	python3 backend/lint.py
	@echo "✅ Formatting complete. Ready to review and commit!"
