# API Reference

FastAPI app (`app/main.py`) on `http://localhost:8008`. CORS allows `localhost:3000–3003`. All routes
are unauthenticated (local-only). `mode` is usually `real` | `simulated`/`replay`. Heavy operations
(evaluation, suggester, retrain, ticker backfill) run as **background jobs** tracked in an in-process
registry and polled for progress.

## Suggestions & market state

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/suggestions?mode&hedge_mode&date` | `{date, regime, hedge_mode, short_term_suggestions[], swing_suggestions[], long_term_allocation[]}`. The main daily output. `swing_suggestions` (the default tradeable book): per-equity `{ticker, close, action, confidence, stop_loss, take_profit, horizon_days, llm_news, llm_news_intensity, reasoning}`, top-N ranked. Cached, keyed on data freshness incl. the LLM-news count. |
| `GET /api/sentiment?mode` | Latest per-ticker aggregate VADER sentiment. |
| `GET /api/sentiment/sources?ticker&date&mode` | Individual article/Reddit/premium items + scores + links. |
| `POST /api/sentiment/premium` | Ingest a paywalled article; VADER-scores it and recomputes aggregates. |
| `GET /api/news/llm?ticker&limit` | LLM-scored news headlines (latest first) with `{score, relevance, weighted, published_utc}`. |
| `GET /api/prices/summary` | Per-ticker live price + 1D/1W/1M/1Y change (batched Alpaca quote, falls back to last close). 60s cache. |
| `GET /api/screener/volatile?refresh` | 30-day historical volatility for a candidate list (yfinance). |
| `GET /api/health` | Service status (api/database/ollama/alpaca/scheduler/news_llm) + news coverage span + execution strategy. 12s cache. |
| `GET /api/performance?mode` | Equity curve + metrics vs SPY/QQQ/BRK from `broker_performance_logs`. |

## Portfolio, universe & per-stock strategy

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/portfolio?mode` | Every holding with shares, cost basis, **live price, market value, unrealized P&L $/%**, and its assigned strategy; plus totals (value/cost/P&L/cash/equity). Real mode reads the broker as source of truth. |
| `GET /api/universe` · `GET /api/universe/supported` | Current / supported tickers. |
| `POST /api/universe` | Replace the whole universe. |
| `POST /api/universe/add` `{ticker}` | Add a ticker + start a background **price+news backfill** job. |
| `POST /api/universe/backfill` `{ticker}` | (Re)run the backfill for an existing ticker. |
| `POST /api/universe/remove` `{ticker}` | Stop monitoring a ticker. |
| `GET /api/strategy/config` | Buckets, cash, per-ticker `assignments`, and the live **regime overlay** (`regime, swing_factor, effective_swing, overlay_active`). |
| `POST /api/strategy/buckets` `{swing, longterm}` | Set capital fractions (rejected if >100%). |
| `POST /api/strategy/ticker` `{ticker, strategy}` | Assign `swing`\|`longterm`\|`hold` to a ticker. |
| `POST /api/positions/liquidate?mode` `{ticker, shares}` | Sell N shares (partial/full) — real mode via Alpaca `close_position` (cancels the bracket OCO). |
| `GET /api/holdings` · `POST /api/holdings` · `DELETE /api/holdings/{ticker}` | Manual holdings CRUD. |
| `POST /api/account?mode` `{cash}` | Set the virtual account cash. |

## Suggester, validation & evaluation (background jobs)

| Method · Path | Returns |
| :-- | :-- |
| `POST /api/strategy/suggest?oos_start` → `GET /api/strategy/suggest/result?job_id` | Per-ticker recommendations `{ticker, recommended, confidence, rationale, swing/news/longterm/bear_2022, …}` + counts. |
| `POST /api/strategy/validate?oos_start` → `GET /api/strategy/validate/result?job_id` | Blended-OOS backtest of current vs suggested (vs 30/60) assignments + a verdict. |
| `POST /api/evaluate` `{strategies, splits, use_allocation, start_date, end_date, oos_start}` → `GET /api/evaluate/result?job_id` | Growth-of-$100k curves + metrics for the strategies, blended, and SPY/QQQ/BRK; `caveats` + `mode`. When `EXPERT_INTERP_ENABLED` + `OPENAI_API_KEY`, the result also carries `interpretation` — a powerful model's (`OPENAI_EXPERT_MODEL`) plain-English, honest read (tldr / what_was_tested / key_findings / strengths / weaknesses / shortcomings / verdict). |
| `POST /api/evaluate/interpret?job_id` | Re-generate the expert interpretation for a finished evaluation. |
| `GET /api/llm/usage?since=YYYY-MM-DD` | Token usage + estimated cost per model from the `llm_usage` ledger (every provider: OpenAI + local Ollama). Cost is recomputed from current pricing. Powers the Model-Evaluation "LLM Usage & Cost" widget. |
| `POST /api/llm/calibrate` `{model, actual_cost, since?}` | Scale a model's pricing so its estimated cost over the window matches your real OpenAI-dashboard cost; persists to `data/llm_pricing.json`. |
| `GET /api/jobs` | Active + recently-finished background jobs (for progress bars). |
| `POST /api/train/start` → `GET /api/train/status` | Retrain XGBoost+HMM+swing in the background; status reports each served model's last-trained time + progress. |

## Virtual broker (Alpaca-shaped, SQLite-backed)

`GET /api/virtual_alpaca/v2/account`, `GET/POST /api/virtual_alpaca/v2/positions`,
`POST /api/virtual_alpaca/v2/orders`, `DELETE /api/virtual_alpaca/v2/positions/{symbol}`,
`POST /api/reconcile` (sync local state with the real Alpaca broker),
`POST /api/simulate?days`, `POST /api/backtest-virtual?months` (forward sim / look-ahead-free replay).

> Note: the DB backup/restore (Google Drive) is **CLI/Make only** (`scripts/db_backup.py`), not an API
> route — see [operations.md](./operations.md).
