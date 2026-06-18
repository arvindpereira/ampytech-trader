# ML & Strategy

How the bot turns data into trades: feature engineering, the models/strategies, the per-stock
suggester, the regime overlay, and the honest out-of-sample evaluation harness.

> Read alongside [strategy-evaluation-findings.md](./strategy-evaluation-findings.md) (the OOS verdict)
> and [strategy-suggester-plan.md](./strategy-suggester-plan.md) (the suggester design).

## Strategies at a glance

| Strategy | Horizon | Signal | Status | Code |
| :-- | :-- | :-- | :-- | :-- |
| **Swing + News** | ~5 trading days | XGBoost on daily technicals **+ LLM-scored news** | **Default tradeable**; real edge in bull regimes, amplifies bears | `ml_engine/swing_alpha.py` |
| **Long-term MPT** | weeks–months | regime-aware max-Sharpe optimizer over the universe | Bear-resilient; absolute returns survivorship-inflated | `ml_engine/longterm_alpha.py`, `models.py:PortfolioOptimizer` |
| **Regime HMM** | — | 3-state HMM on daily SPY vol + macro → growth/transition/crisis | Drives MPT scaling + the swing **regime overlay** | `ml_engine/models.py:train_models` |
| **Short-term (legacy)** | ~2 trading days (hourly) | XGBoost breakout | **Net-negative; not executed by default** | `ml_engine/models.py`, `deep_models.py` (PyTorch, opt-in) |

The PyTorch temporal-attention model (`deep_models.py`) exists but `SERVED_MODEL=xgboost`, so it is not
served by default.

## Feature engineering (`ml_engine/features.py`)

- **Technicals**: SMA-10/50, RSI-14, MACD, ATR-14, volatility, returns; cross-ticker features
  (correlation to SPY/QQQ, relative volume) via `add_cross_ticker_features`.
- **Macro**: fed funds, yield spread (joined from `macro_indicators`).
- **LLM-news features** (the swing edge), `swing_alpha.add_llm_features`: from `news_llm_scores`, a
  per-(ticker,date) relevance-weighted score `Σ(score·rel)/Σrel`. Three features — a 3-day decayed
  weighted mean (`feat_llm_news`), a material-news intensity (`feat_llm_news_intensity`), and today's
  score (`feat_llm_news_today`) — all **shifted +1 day** so a day's news can't inform that same day.
- **Labels — triple-barrier** (`triple_barrier_outcomes`): for each entry, did price hit the
  take-profit (ATR-scaled) before the stop within the horizon? `target_win` ∈ {0,1} and `trade_ret`
  (realized). The same ATR brackets label training data **and** size live stop/take-profit orders, so
  the target matches the executed trade.
- Point-in-time: technicals/news use only data through *T−1*; only the label looks forward.

## LLM-scored news (`data_ingestion/news_llm.py`)

The swing strategy's distinctive input. For each ticker it pages Polygon/Massive news and asks a local
**Ollama** model (`gemma4:e4b` — fast, JSON-clean, free) to rate each headline's directional impact on
that ticker over the next few days: `{s: -1..1, rel: 0..1}`. Results upsert into `news_llm_scores`
(resumable — already-scored article ids are skipped; the per-ticker fetch window is trimmed to just past
the latest stored date). Coverage is dense from **~2021** (≈226k headline scores across ~50 tickers).
Run via `make news-llm` or the scheduler; needs Ollama running locally.

## Swing model (`ml_engine/swing_alpha.py`)

- `load_swing_data` builds daily features + LLM-news features + the horizon triple-barrier target.
- `train_and_save` trains the production XGBoost on the full LLM-active window (now **2021→present,
  ~54k samples incl. the 2022 bear**) and persists `saved_models/swing_model.json` + metadata
  (feature columns, calibrated threshold, horizon).
- `build_swing_signals` does inference for the latest date per equity, ranks by win-probability, caps
  BUYs to `SWING_TOP_N` (10), and anchors stop/take-profit to the **live** price (so a stale-close
  bracket can't be rejected). Served as `swing_suggestions` in `/api/suggestions`.
- `backtest_swing_curve` / `swing_oos_frame` run the walk-forward OOS used by the evaluator + suggester.

## Long-term MPT (`ml_engine/longterm_alpha.py`, `models.py:PortfolioOptimizer`)

Monthly-rebalanced max-Sharpe weights (SciPy SLSQP) using **trailing-only** returns for the covariance
(look-ahead-free), scaled by the current regime. `backtest_longterm_curve` produces the OOS equity curve
(restrictable to the longterm-bucket tickers). An optional Black-Litterman-style insider-buy tilt exists
(`backtest_longterm_tilt`) but `ALT_DATA_ENABLED=False` by default.

## Strategy suggester (`ml_engine/strategy_suggester.py`)

Per-ticker recommendation of **swing / longterm / hold**, evidence-driven and conservative (defaults
away from swing). For each equity, out-of-sample with the 2022 bear in the test set, it measures:
1. **Swing OOS edge** — expectancy/win-rate of above-threshold signals (and 2022 behavior),
2. **News responsiveness** — volume + correlation of the daily news score with forward returns,
3. **Long-term quality** — trailing Sharpe / max drawdown / momentum,
4. **Bear behavior** — 2022 return/drawdown.

A z-scored rubric picks swing only when it clears a bar **and** beats the long-term score by a margin,
else longterm, else hold — each with a confidence + plain-English rationale. **Self-validation**
(`validate_assignments`) backtests the *suggested* vs *current* assignments' blended OOS curves (plus a
MPT-leaning variant) and returns a verdict; it confirmed the suggestions lift blended OOS Sharpe
(~1.48 → 1.77 at a 50/45 split). **v2 regime overlay** (in execution) shrinks swing capital in
defensive regimes. Surfaced in the Portfolio tab ("Suggest per-stock strategies", "Validate vs current").

## Evaluation harness (`ml_engine/evaluate.py`, Model Evaluation tab)

`run_evaluation` plots growth-of-$100k for the chosen strategies + a blended (by current buckets) curve
vs **SPY / QQQ / BRK-B**, fully out-of-sample:
- **Walk-forward** mode (default): swing trains only on data before each fold; `oos_start` fixes where
  testing begins (set `2022-01-01` to put the bear in the OOS window — the single most important knob).
- **Stress-window** mode (fixed start/end): the MPT engine + benchmarks over a historical bear; swing
  isn't run for a fixed window (use walk-forward + oos_start instead). Emits **caveats**, including a
  **survivorship-bias** warning for pre-2020 windows.

### The honest verdict (do not skip)
With 2022 in the OOS test, **swing's risk-adjusted edge largely evaporates** (Sharpe ~0.70, −25% in
2022 vs −20% S&P) — it's a bull-market amplifier. **MPT** was resilient in 2022 (+7%) but its absolute
backtest returns are **survivorship-inflated** (the universe is today's winners). The **blended** book
is the most defensible. Earlier rosy swing Sharpes (1.1–1.75) came from OOS windows that excluded 2022.
Full detail + tables: [strategy-evaluation-findings.md](./strategy-evaluation-findings.md).

## Key config (`app/core/config.py`)

`SWING_ENABLED`, `SWING_HORIZON_DAYS=5`, `SWING_TOP_N=10`, `SWING_POSITION_PCT=0.10`,
`SWING_ATR_STOP_MULT`/`TP_MULT`/`STOP_MIN`/`STOP_MAX`; `EXECUTION_STRATEGY=swing`;
`REGIME_OVERLAY_ENABLED` + `REGIME_SWING_FACTORS` (crisis ×0.25, transition ×0.6, growth ×1.0);
`OLLAMA_URL`, `LLM_MODEL`, `NEWS_LLM_START=2021-01-01`; `SERVED_MODEL=xgboost`; `MPT_WINDOW_DAYS=252`;
`ALT_DATA_ENABLED=False`.
