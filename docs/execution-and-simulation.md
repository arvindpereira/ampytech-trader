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
2. Read the daily suggestions (`get_daily_suggestions`) → `swing_suggestions`, `long_term_allocation`, and the **regime**.
3. Read **capital buckets** (`app_settings`) and **per-ticker strategy** assignments (`universe_tickers.strategy`).
4. Compute each strategy's budget = `weight × equity − already-deployed`, a **soft cap** (it never opens new positions past the limit and never force-sells to rebalance down).
5. **Regime overlay**: scale the swing bucket by `REGIME_SWING_FACTORS[regime]` (growth ×1.0, transition ×0.6, crisis ×0.25); freed capital is held as cash.
6. **Swing sleeve** (`execute_swing_paper_trades`): for swing-assigned tickers, place fixed-fraction (`SWING_POSITION_PCT=10%`) **bracket** orders (stop + take-profit) anchored to the **live** price; horizon exits via `close_aged_swing_positions`. Sizing is deliberately **fixed-fraction, not Kelly** — the win-prob is a ranking score, and Kelly would zero most positions; this matches the validated portfolio sim.
7. **Long-term sleeve** (`execute_long_term_grid_trades`, when its bucket > 0): MPT grid/tranche rebalancing restricted to longterm-assigned tickers, scaled to the bucket.

The legacy hourly path (`execute_alpaca_live_paper_trade`, Kelly-sized) only runs if
`EXECUTION_STRATEGY=short_term` (net-negative; not default).

### Intraday re-execution
When `INTRADAY_EXECUTION_ENABLED`, the hourly intraday job (10:00–16:00 ET) re-scores news → re-runs
swing signals → re-executes (market-open guarded), so the book tracks fresh news without waiting for the
09:45 cycle. With ~full deployment it mostly fills slots freed by bracket/horizon exits.

### Liquidation
`POST /api/positions/liquidate` (UI Portfolio tab → **Liquidate** dialog) sells a chosen number of shares
— real mode via Alpaca `close_position` (which cancels that position's bracket OCO); simulated mode
reduces the local position.

## Sizing & policy notes

- **Position cap**: swing holds ≤ `SWING_TOP_N` (10) names, 10% equity each.
- **Per-ticker strategy** (`swing`/`longterm`/`hold`) governs which sleeve manages a name; `hold` =
  never traded. The old `rebalance`/`lock`/`liquidate` policy is superseded by this.
- **Brackets**: the same ATR-derived stop/take-profit used to label training data; re-anchored to the
  live fill price at order time.

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
