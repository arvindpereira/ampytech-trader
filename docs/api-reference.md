# API Reference

FastAPI app (`app/main.py`) on `http://localhost:8008`. CORS allows `localhost:3000–3003`.
All routes are unauthenticated (local-only). `mode` is usually `real` | `simulated`/`replay`.

## Suggestions & data

| Method | Path | Purpose | Key params |
| :-- | :-- | :-- | :-- |
| GET | `/api/suggestions` | Compute short-term signals + long-term allocation + regime | `date?`, `mode=real` |
| GET | `/api/sentiment` | Latest-date sentiment aggregates per ticker/source | `mode=real` |
| GET | `/api/sentiment/sources` | Individual source logs (news/reddit/premium) for a ticker | `ticker`, `date?`, `mode` |
| POST | `/api/sentiment/premium` | Ingest pasted premium article → VADER score → recompute aggregates | body: `ticker,title,text,url?` |
| GET | `/api/performance` | Equity curve + metrics (portfolio vs SPY/QQQ/BRK-B) | `mode=live` |
| GET | `/api/screener/volatile` | 30-day realized vol for a fixed high-beta candidate list (live yfinance) | `refresh?` |

`/api/suggestions` response shape:
```jsonc
{
  "date": "2026-06-14",
  "regime": "growth|transition|crisis",
  "hedge_mode": "none|beta_neutral|pair_trade",
  "short_term_suggestions": [
    { "ticker","close","action":"BUY|SELL|HOLD","confidence",
      "stop_loss","take_profit","reasoning","audit": {...},
      "hedge": { "symbol","ratio","price","shares" } | null,   // when hedge_mode != none
      "action_plan": "BUY … sh @ $… stop … target … [HEDGE: SHORT …]" }   // executable plan string
  ],
  "long_term_allocation": [
    { "ticker","weight","shares_multiplier","insider_tilt_score" },   // tilt score nonzero only if ALT_DATA_ENABLED
    { "ticker":"CASH","weight" }
  ]
}
```

Query params: `date?`, `mode=real|simulated`, `hedge_mode=none|beta_neutral|pair_trade`. The long-term
weights are MPT (SciPy SLSQP) optionally tilted by the buy-side insider score (`LONGTERM_TILT_STRENGTH`,
active only when `ALT_DATA_ENABLED`).

> ⚠️ `/api/performance` with `mode != live` **fabricates** a 100-day random-walk equity curve and
> hard-coded metrics (Sharpe 1.78, etc.) when no logs exist, "so the UI looks beautiful". `mode=live`
> with no logs returns zeros. Treat non-live performance as cosmetic unless backed by a real replay.

## Universe & holdings

| Method | Path | Purpose |
| :-- | :-- | :-- |
| GET | `/api/universe` | Current editable universe (from `universe_tickers`) |
| GET | `/api/universe/supported` | Static `TICKER_UNIVERSE` from config |
| POST | `/api/universe` | Replace universe (clears suggestions cache) |
| GET / POST / DELETE | `/api/holdings[/{ticker}]` | CRUD user holdings + policy (`rebalance/lock/liquidate`), per `mode` |
| POST | `/api/account` | Set cash balance for the mode's account |

## Virtual Alpaca broker (mock)

| Method | Path | Purpose |
| :-- | :-- | :-- |
| GET | `/api/virtual_alpaca/v2/account` | Cash, equity (= cash + mark-to-market positions) |
| GET | `/api/virtual_alpaca/v2/positions` | Open positions with unrealized P/L |
| POST | `/api/virtual_alpaca/v2/orders` | Place buy/sell; fills at sim-date open or latest close |
| DELETE | `/api/virtual_alpaca/v2/positions/{symbol}` | Close a position |

Effective mode/account here is overridden by `data/sim_date.txt` (see
[execution-and-simulation.md](./execution-and-simulation.md)).

## Simulation & ops triggers

| Method | Path | Purpose |
| :-- | :-- | :-- |
| POST | `/api/simulate?days=N` | Run forward simulation (background task) |
| POST | `/api/backtest-virtual?months=N` | Run historical replay (background task) |
| POST | `/api/reconcile` | Sync local DB with real Alpaca (needs keys) |

## In-memory caches

- `_suggestions_cache` — keyed on (`date`, `mode`, latest price/sentiment dates, row counts, universe).
  Cleared by universe/premium/simulate/backtest mutations via `clear_suggestions_cache()`.
- `_volatile_screener_cache` — 4-hour TTL.
