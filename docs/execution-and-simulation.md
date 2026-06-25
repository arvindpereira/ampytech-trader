# Execution, Virtual Broker & Simulation

Three distinct ways the bot "trades", easy to confuse:

| Mechanism | Code | Account / data | Purpose |
| :-- | :-- | :-- | :-- |
| **Alpaca execution (Paper/Live)** | `execution/executor.py:run_execution` | Real Alpaca paper or live accounts | The live bot — places real-money or paper orders |
| **Virtual broker** | `app/main.py:/api/virtual_alpaca/*` | SQLite-backed mock of the Alpaca REST API | UI holdings/orders without a broker (falls back for paper when offline) |
| **Simulation / replay** | `execution/simulator.py` | Replay account (`mode='replay'`) | Forward simulation / look-ahead-free historical replay |

## Bucket-aware execution (`run_execution`)

The default strategy is **swing** (`EXECUTION_STRATEGY=swing`). Each cycle:

1. Connect to Alpaca, authenticate, and **sync** broker orders + positions into SQLite for all configured/enabled accounts (Paper and/or Live).
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

The local database supports three account contexts:
1. **Simulated/Replay**: Account ID 1, positions/orders tagged with `mode='replay'`. Used for historical backtesting and forward simulations.
2. **Alpaca Paper**: Account ID 2, positions/orders tagged with `mode='paper'`. Mirrors your Alpaca Paper trading account (or falls back to the in-process mock if credentials are omitted).
3. **Alpaca Live**: Account ID 3, positions/orders tagged with `mode='live'`. Accesses your real-money Alpaca trading account (must be configured manually with `ALPACA_LIVE_API_KEY` and `ALPACA_LIVE_SECRET_KEY`).

A `data/sim_date.txt` file flips the server into replay-as-of-a-date while a simulation runs (`/api/simulate`, `/api/backtest-virtual`). The replay path is look-ahead-free (orders fill at the next bar's open). `evaluate_virtual_broker_daily` marks positions to market, applies stop/take-profit, and logs equity vs SPY/QQQ/BRK-B.

## Posture State Machine & Defensive Playbook

When the Crash Radar tab's stance rebalancing is applied, or in automated mode, the portfolio exposure is governed by a **Posture State Machine** and a **Two-Signal Glide Path**:

### 1. Posture States & Transitions
- **Normal** ($I_t < 40$): Default state. Focus on maximum return/Sharpe (MPT allocation).
- **Froth** ($I_t \ge 65$, SPY > 200 SMA): High valuation/risk, but trend remains positive. Hold existing equity holdings, raise cash to 10%, halt new swing entries.
- **De-Risk** ($I_t \ge 65$, SPY < 200 SMA or credit spreads widening): Reduce equities, increase cash reserves to 30%, size portfolio hedges.
- **Protect** ($I_t \ge 80$): Extreme systemic fragility. Shift to 50% Cash/Treasuries, 10% convex tail hedges, tilt remaining equity to defensive sectors (staples, utility).
- **Deploy** (extreme equity drawdown reached, e.g., $-20\%$, $-35\%$): Re-deploy cash reserves in tranches.
- **Recover** ($I_t < 65$, SPY crosses above 50 SMA): Scale back cash, remove tail hedges, transition back to Normal.

### 2. Simplex Preservation Glide Path
To prevent abrupt capital reallocation whipsaws, the target portfolio weight vector $\mathbf{w}(z)$ is blended dynamically:
\[ \mathbf{w}(z) = (1 - d(z)) \mathbf{w}_{\text{agg}} + d(z) \mathbf{w}_{\text{def}} \]
where $d(z) \in [0, 1]$ is the sigmoid blending coefficient based on the standardized risk score $z_t = (I_t - 50)/15$, adjusted by the trend gate strength $\theta_{\text{eff}} = \theta + \gamma \cdot T_t$.

**Proof of Simplex Preservation**:
Since both the aggressive portfolio $\mathbf{w}_{\text{agg}}$ and the defensive portfolio $\mathbf{w}_{\text{def}}$ lie on the simplex (all weights $\ge 0$ and sum to $1.0$):
\[ \sum_{i} w_i(z) = (1 - d(z))\sum_{i} w_{\text{agg}, i} + d(z)\sum_{i} w_{\text{def}, i} = (1 - d(z))\cdot 1 + d(z)\cdot 1 = 1.0 \]
Since $d(z) \in [0, 1]$, all blended weights are guaranteed to remain non-negative.

### 3. Defensive Allocations
- **Buffett Stance**: Target cash reserve = $\min(50\%, I_t \times 0.6)$. Re-deployed in tranches on SPY market dips (25% tranche at $-10\%$ drawdown, 35% tranche at $-20\%$ drawdown, 40% tranche at $-35\%$ drawdown).
- **Dalio Stance (All-Weather Risk Parity)**: US Equities (30%), Long-Term Treasuries (40%), Intermediate Treasuries (15%), Gold (7.5%), Commodities (7.5%).
- **Taleb Stance (Barbell)**: 90% Safe capital (T-Bills, BIL, FDIC cash) and 10% highly speculative convex assets (options, tail hedges).
- **Regime-Aware Safe Asset Selection**:
  - *Stagflation / Inflationary (Breakevens > 2.5% or rising Fed Funds)*: Allocates defensive capital to Gold (GLD), Commodities (GSG), and short-term T-Bills (BIL), excluding long-term bonds.
  - *Deflationary Bust (Breakevens <= 2.5% and widening credit)*: Allocates to Long-Term Treasuries (TLT) and Cash (BIL).

### 4. Severity Custodial Checklist
Surfaced as guidelines based on market drawdown levels:
- **Tier 1: Correction (-10% to -20%)**: Lock margin accounts (zero leverage), audit high-beta specs.
- **Tier 2: Bear Market (-20% to -35%)**: Diversify cash holdings across multiple banking institutions (staying below FDIC $250k limits).
- **Tier 3: Systemic Crisis (-35% to -55%)**: Verify SIPC broker coverage details ($500k limit). Hold cash reserves in direct short-term US Treasury Bills.
- **Tier 4: Depression (> -55%)**: Maintain direct custodial access to vault safe-haven assets.

## Hedging (optional)

`HEDGE_MODE` (`none`/`beta_neutral`/`pair_trade`) attaches an offsetting short plan to swing BUYs; the short leg is only placed on **real Alpaca** (the virtual broker can't short) — otherwise it's shown in the UI trade plan for manual execution.
