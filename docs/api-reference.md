# API Reference

FastAPI app (`app/main.py`) on `http://localhost:8008`. CORS allows `localhost:3000–3003`. All routes
are unauthenticated (local-only). `mode` is usually `real` | `simulated`/`replay`. Heavy operations
(evaluation, suggester, retrain, ticker backfill) run as **background jobs** tracked in an in-process
registry and polled for progress.

## Suggestions & market state

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/suggestions?mode&hedge_mode&date` | `{date, regime, hedge_mode, short_term_suggestions[], swing_suggestions[], high_risk_suggestions[], long_term_allocation[]}`. The main daily output. `swing_suggestions` (the core swing book) and `high_risk_suggestions` (speculative book): per-equity `{ticker, close, action, confidence, stop_loss, take_profit, horizon_days, llm_news, llm_news_intensity, reasoning}`, top-N ranked. Cached, keyed on data freshness. |
| `GET /api/sentiment?mode` | Latest per-ticker aggregate VADER sentiment. |
| `GET /api/sentiment/sources?ticker&date&mode` | Individual article/Reddit/premium items + scores + links. |
| `POST /api/sentiment/premium` | Ingest a paywalled article; VADER-scores it and recomputes aggregates. |
| `GET /api/news/llm?ticker&limit` | LLM-scored news headlines (latest first) with `{score, relevance, weighted, published_utc}`. |
| `GET /api/prices/summary` | Per-ticker live price + 1D/1W/1M/1Y change (batched Alpaca quote, falls back to last close). 60s cache. |
| `GET /api/screener/volatile?refresh` | 30-day historical volatility for a candidate list (yfinance). |
| `GET /api/health` | Service status (api/database/ollama/alpaca/scheduler/news_llm) + news coverage span + execution strategy. 12s cache. |
| `GET /api/performance?mode` | Equity curve + metrics vs SPY/QQQ/BRK from `broker_performance_logs`. |
| `GET /api/premium/value` | Forward predictive value metrics of premium-newsletter signals (e.g. The Information): coverage + hit-rate / directional edge. |

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
| `GET /api/classification` | Returns per-ticker risk × fundamental-quality tier details: `{ticker: {tier, quality, volatility, dd_2022, distressed, verdict, overridden}}`. |
| `POST /api/classification/override` | Set manual tier override for a ticker: `{ticker, tier: 'core'\|'quality_growth'\|'speculative'\|'value_trap'\|null}`. |

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

## Crash Radar (Tab 5)

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/crash/index` | `{as_of_date, composite_index, risk_band, current_posture, trigger_reasons[], buckets{}, debt_cycle_metrics{}}`. Returns the current Composite Risk Index, its components, posture stance, and structural debt-cycle metrics. |
| `GET /api/crash/timeline` | `[{date, composite_index, risk_band, current_posture}]`. Returns 5-year weekly out-of-sample (OOS) risk snapshot timeline. |
| `POST /api/crash/forecast` → `GET /api/crash/forecast/result?job_id` | Spawns a background job to run the experimental regularized drawdown-odds models (Ridge/Lasso with Lopez de Prado's purged/embargoed CV) and retrieves the forecast probabilities. |
| `GET /api/crash/playbook?preset` | `{preset, de_risk_coefficient, stances: {buffett, safe_asset_selection, dalio, taleb, minsky}}`. Returns target asset weights and custodial guidelines for the selected preset (`conservative`\|`balanced`\|`aggressive`). |
| `GET /api/crash/compare?...` | Walk-forward backtest comparing the glide-path presets (and current custom knobs) vs Buy & Hold. Analysis only. |
| `POST /api/crash/wargame` → `GET /api/crash/wargame/result?job_id` | Spawns a background job to sweep parameter ranges over a scenario ensemble (GFC, Dot-Com, 2022, and bootstrap paths) and retrieves minimax regret heatmaps and Pareto-optimal knobs. |
| `POST /api/crash/wargame/scenarios` `{theta?,k?,gamma?}` → `GET /api/crash/wargame/scenarios/result?job_id` | Replays every defensive policy (Buy & Hold → static → glide-path/custom) across historical bears + synthetic crashes; returns per-scenario equity curves + ranked metrics. Read-only; result is cached to disk. |
| `POST /api/crash/wargame/interpret` `{comparison}` | OpenAI wargame analyst: plain-English summary of a scenario comparison (TLDR, knobs, per-policy findings, regime insights, "best for you"). Cached to disk on success. |
| `GET /api/crash/wargame/cache` | Last cached scenario comparison + analyst (so they render by default) with `*_generated_at` timestamps and `*_stale` flags (true when new data has arrived since). |
| `GET /api/crash/status` | Timing metadata for the Crash Radar artifacts (index, forecast, wargame, analyst): `last_run/last_refresh`, `next_scheduled` (weekday 9:30 ET data-gated job), and `stale` flags. Drives the "Last updated / Next auto-update" badges. |
| `GET /api/crash/apply/preview?target_posture&preset&theta&k&gamma` | **Read-only** rebalance plan: diffs current paper holdings vs target stance weights and returns the summary, validation, and exact orders (symbol, side, shares, real price) — without executing. |
| POST /api/crash/apply {confirm_execution, target_posture, preset, theta?, k?, gamma?} | Executes the previewed rebalancing transactions to align the paper portfolio with the active defensive stance (gated on confirm_execution). |

## External Portfolio Manager (Tab 6)

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/external/accounts` | `[{account_label, cash, holdings_value, total_value, risk_profile, strategy_mode, aggression, de_risk_policy}]`. |
| `POST /api/external/accounts` | Creates or updates an external account (risk profile and cash). |
| `DELETE /api/external/accounts/{account_label}` | Deletes the account and cascades to clear equity lots, statement holdings, orders, transactions, and trade blocks. |
| `POST /api/external/accounts/{account_label}/cash` | Updates cash balance manually. |
| `GET /api/external/positions?account_label` | Grouped positions + individual tax lots (filters out ESPP/RSU lots). |
| `POST /api/external/accounts/{account_label}/strategy` | Set `strategy_mode` (`growth`\|`de_risk`\|`glide_path`\|`all_weather`\|`barbell`), `aggression` (0–100), `buckets`, and `de_risk_policy` (`rotate`\|`shed_beta`\|null). |
| `GET /api/external/suggestions?account_label` | Builds `target = a·growth + (1−a)·defensive`, diffs vs holdings → BUY/SELL proposals. Response includes `de_risk_policy`, `recommended_policy`, `recommendation_reason`, `book_beta`, `tiers`, `current_weights`, `target_reason_codes`. |
| `POST /api/external/accounts/{label}/wargame?lookback_years=N` | Walk-forward each strategy mode over `N` years of real prices (monthly rebalancing, partial-entry). Returns equity curves + Sharpe/CAGR/MDD per mode. |
| `POST /api/external/accounts/{label}/crash-stress?era=gfc` | Replay each strategy mode through a historical crash era (`gfc`\|`dot_com`\|`covid`\|`2022`) using an SPY-beta proxy. Returns per-mode equity curves + max drawdown. |
| `POST /api/external/accounts/{label}/policy-compare?lookback_years=N&era=gfc` | Run de_risk under both policies (rotate vs shed-to-cash) side-by-side. Returns curves, crash drawdown, portfolio beta, cash target, and the model's recommended policy + reason. |
| `POST /api/external/import` | Upload a Robinhood/Vanguard PDF statement to import positions and auto-populate the statement anchor. |
| `POST /api/external/sync/robinhood` | Sync holdings directly via Robinhood API credentials (`username`, `password`, `mfa_secret`, `account_label`). Requires `robin_stocks` installed. |
| `GET /api/external/orders/pending` | Lists all pending/proposed external orders (`status == "proposed"`). |
| `POST /api/external/orders/confirm` | Confirms a proposed order, adjusting position/lots (FIFO) and cash. |
| `POST /api/external/reconcile?account_label` | Cross-references monthly transaction logs, de-dupes, and updates holdings. |

## Equity Advisor (Tab 4)

RSU/ESPP lot management, tax-optimized sell planning, wash-sale guards, and vesting schedules.

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/equity/lots` | All equity lots (non-external) with live prices, analyst forecasts, HIFO classifications, per-lot sell recommendations, concentration rollup, auto-trade-block flags, and upcoming vest schedules. |
| `POST /api/equity/lots` | Create or update a lot `{ticker, account_label, lot_type (rsu\|espp\|other), shares, cost_basis_per_share, acquisition_date, notes, id?}`. |
| `DELETE /api/equity/lots/{lot_id}` | Delete a lot. |
| `POST /api/equity/lots/import` | Upload a brokerage PDF to auto-extract lots (LLM-parsed; `force_llm`, `replace_ticker_account` form params). |
| `POST /api/equity/lots/{lot_id}/sell` | Record a sale against a specific lot: `{shares, price}`. Returns proceeds, realized gain, and whether a wash-sale block was auto-created. |
| `GET /api/equity/tax-profile` | Active tax profile (`filing_status`, `ordinary_income`, `magi`, `state_ltcg_rate`, `state_stcg_rate`, `carryover_loss`, `tax_year`). |
| `POST /api/equity/tax-profile` | Upsert the tax profile. Validates filing status, year range, and non-negative values. |
| `GET /api/equity/forecast/{ticker}` | Analyst consensus price target, upside %, rating, and current price for a ticker. |
| `GET /api/equity/grant-timeline/{ticker}` | Full grant history → daily price + running weighted-average cost basis + % of shares in-the-money, downsampled to ~750 points. Includes grant markers and upcoming vest events. Powers the `GrantTimeline` chart. |
| `POST /api/equity/analyze` → `GET /api/equity/analyze/result?job_id` | Background job: tax-optimized sell plan for an objective (`raise_cash`\|`harvest_loss`\|`exit_ticker`) with HIFO lot selection, wash-sale flags, and LLM narrative. |
| `GET /api/equity/trading-blocks` | Active wash-sale / never-trade BUY guards + the global `auto_trading_paused` state. Opportunistically retires expired wash-sale blocks. |
| `POST /api/equity/trading-blocks` | Create a block: `{ticker, block_type (wash_sale\|permanent), sale_date?, window_days?, shares?, realized_loss?, account_label?, reason?}`. Wash-sale blocks auto-compute `blocked_until`. |
| `DELETE /api/equity/trading-blocks/{block_id}` | Deactivate (release) a block. |
| `POST /api/equity/auto-trade-block` | Toggle whether the auto-trader may buy a ticker held externally: `{ticker, blocked: bool}`. |
| `GET /api/equity/vest-schedules` | All vest schedules with upcoming vest events and `vesting_complete` flag. |
| `POST /api/equity/vest-schedules` | Upsert a vest schedule for a ticker (cadence, next vest, total shares, etc.). |
| `POST /api/execution/auto-trading` | Global auto-trading kill-switch: `{paused: bool}`. `paused=true` freezes all auto buys and sells. |

## Research Analyst (Research tab)

| Method · Path | Returns |
| :-- | :-- |
| `POST /api/research/query` | Spawns a background research analyst query job (`research_query`) using intent routing (`ticker_outlook`\|`earnings_report`\|`theme_rank`\|`sector_screen`) to prepare custom research reports. |
| `GET /api/research/query/result?job_id` | Status, progress, and synthesized JSON report result for a research query job. |
| `GET /api/research/snapshot/{ticker}` | Returns GICS sectors, financials, price trends, and KB facts loaded for a specific ticker. |
| `GET /api/research/kb/status` | Ticker coverage count and last refresh timestamp of the company snapshot knowledge base. |
| `POST /api/research/kb/refresh` | Spawns a background thread (`research_kb`) to refresh snap data and analyst items across active tickers. |
| `GET /api/research/methodology` | Factor weights, calibration metadata, and active GICS sector mappings. |
| `GET /api/research/themes` | List of recognized investment themes and aliases. |
| `GET /api/research/sectors` | Cap-ranked large-cap names and portfolio counts by GICS sector. |
| `GET /api/research/portfolio/sectors` | Classified portfolio holdings grouped by sector. |
| `GET /api/research/threads` | List of saved research threads and summaries. |
| `GET /api/research/thread/{thread_id}` | Full thread metadata and all user/assistant message transcripts. |
| `POST /api/research/thread/{thread_id}/publish` | Publishes a research thread, rendering and exporting it to the local markdown wiki. |
| `POST /api/research/thread/{thread_id}/reject` | Marks a draft report as rejected and appends qualitative feedback critique notes. |
| `GET /api/research/library` | Fetches published reports cataloged in the library. |
| `GET /api/research/premium/estimate` | Cost/complexity estimates for re-synthesizing a query using premium LLM models. |

## Sector Exposure Simulator (embedded in External Portfolio tab)

| Method · Path | Returns |
| :-- | :-- |
| `GET /api/portfolio/sector-exposure?mode` | Consolidated portfolio GICS sector weights, benchmark deltas (vs. S&P 500), drift alerts (exceeding 5pp), GICS industry breakdowns, and stock holdings. |

## Virtual broker (Alpaca-shaped, SQLite-backed)

`GET /api/virtual_alpaca/v2/account`, `GET/POST /api/virtual_alpaca/v2/positions`,
`POST /api/virtual_alpaca/v2/orders`, `DELETE /api/virtual_alpaca/v2/positions/{symbol}`,
`POST /api/reconcile` (sync local state with the real Alpaca broker),
`POST /api/simulate?days`, `POST /api/backtest-virtual?months` (forward sim / look-ahead-free replay).

> Note: the DB backup/restore (Google Drive) is **CLI/Make only** (`scripts/db_backup.py`), not an API
> route — see [operations.md](./operations.md).
