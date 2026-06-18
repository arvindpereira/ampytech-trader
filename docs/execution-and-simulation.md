# Execution, Virtual Broker & Simulation

Three distinct ways the bot "trades", easy to confuse:

| Mechanism | Code | Account / data | Purpose |
| :-- | :-- | :-- | :-- |
| **Alpaca paper execution** | `execution/executor.py:run_execution` | real Alpaca paper account | the live bot — places real paper orders |
| **Virtual broker** | `app/main.py:/api/virtual_alpaca/*` | SQLite-backed mock of the Alpaca REST API | UI holdings/orders without a broker; powers sim/replay |
| **Simulation / replay** | `execution/simulator.py` | replay account (`mode='replay'`) | forward sim / look-ahead-free historical replay |

## Bucket-aware execution (`run_execution`)

The default strategy is **swing** (`EXECUTION_STRATEGY=swing`). Each cycle:

1. Connect to Alpaca, authenticate, **sync** broker orders + positions into SQLite.
2. Read the daily suggestions (`get_daily_suggestions`) → `swing_suggestions`, `high_risk_suggestions`, `long_term_allocation`, and the HMM **regime**.
3. Read **capital buckets** (`app_settings`) and **per-ticker strategy** assignments (`universe_tickers.strategy`).
4. Compute each strategy's budget = `weight × equity − already-deployed` (soft cap).
5. **Regime overlay**: scale the swing bucket by `REGIME_SWING_FACTORS[regime]` (growth ×1.0, transition ×0.6, crisis ×0.25); freed capital is held as cash.
6. **Core Swing sleeve** (`execute_swing_paper_trades`): close aged positions, then open new ones for core-assigned tickers (excluding speculative/Long-shot names) utilizing the Core swing model predictions. Brackets (stop-loss and take-profit) are placed. Sizing is fixed-fraction scaled by volatility (see below).
7. **High-Risk sleeve**: for speculative-assigned ("Long-shot") tickers, evaluate recommendations using the Aggressive model. Sized against the `high_risk` bucket budget and capped at a maximum `HIGH_RISK_CAP` (5%) of total equity.
8. **Long-term sleeve** (`execute_long_term_grid_trades`, when its bucket > 0): MPT grid/tranche rebalancing restricted to longterm-assigned tickers, scaled to the bucket.
9. **Final Reconciliation**: Run `sync_broker_orders` and `sync_broker_positions` post-execution to align the database state with actual broker fills.

The legacy hourly path (`execute_alpaca_live_paper_trade`) only runs if
`EXECUTION_STRATEGY=short_term` (net-negative; not default).

### Intraday re-execution
When `INTRADAY_EXECUTION_ENABLED`, the hourly intraday job (10:00–16:00 ET) re-scores news → re-runs swing signals (both Core and Aggressive models) → re-executes, so the book tracks fresh news.

### Liquidation
`POST /api/positions/liquidate` sells a chosen number of shares — real mode via Alpaca `close_position` (which cancels that position's bracket OCO); simulated mode reduces the local position.

## Sizing & policy notes

- **Volatility Sizing Cap**: Position sizes for swing trades (both core and high-risk) are scaled dynamically based on trailing annualized volatility: `vol_scale = min(1.0, SWING_VOL_TARGET / name_vol)` where `SWING_VOL_TARGET` = 0.35. A volatile stock's purchase value is scaled down from the standard `SWING_POSITION_PCT=10%` to limit risk.
- **High-Risk Sleeve Cap**: Speculative ("Long-shot") tickers are restricted to the aggressive model sleeve, and their total market value is hard-capped at 5% of total equity (`HIGH_RISK_CAP`).
- **Brackets**: Stated stop-loss and take-profit percentages are re-anchored to the **live entry price** at fill time, preventing stale-close bracket rejection.
- **Per-ticker strategy** (`swing`/`longterm`/`hold`) governs which sleeve manages a name; `hold` = never traded.

## Real vs. simulated / replay

Two isolated virtual accounts: **real** (account id 2, positions `mode='real'`) mirrors the Alpaca paper
book; **replay**/simulated (id 1, `mode='replay'`) is for sims. A `data/sim_date.txt` file flips the
server into replay-as-of-a-date while a simulation runs (`/api/simulate`, `/api/backtest-virtual`). The
replay path is look-ahead-free (orders fill at the next bar's open). `evaluate_virtual_broker_daily`
marks positions to market, applies stop/take-profit, and logs equity vs SPY/QQQ/BRK-B.

## Hedging (optional)

`HEDGE_MODE` (`none`/`beta_neutral`/`pair_trade`) attaches an offsetting short plan to swing BUYs; the
short leg is only placed on **real Alpaca** (the virtual broker can't short) — otherwise it's shown in
the UI trade plan for manual execution.
