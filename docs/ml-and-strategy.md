# ML Models & Strategy Logic

The bot produces two outputs every time `/api/suggestions` is called:
1. **Short-term suggestions** — per-ticker BUY/SELL/HOLD breakout calls.
2. **Long-term allocation** — portfolio weights under the current market regime.

```mermaid
flowchart TB
    DB[(recent_prices<br/>ticker_sentiments<br/>macro_indicators)] --> FE[features.py<br/>build_all_features]
    FE --> ST{Short-term}
    FE --> LT{Long-term}
    ST --> XGB[XGBoost breakout<br/>short_term_model.json]
    ST --> PT[PyTorch GRU+Attn<br/>temporal_attention_model.pth]
    LT --> HMM[HMM regime<br/>hmm_model.pkl]
    HMM --> MPT[MPT Sharpe-max<br/>SciPy SLSQP Solver]
    XGB & PT --> SUG[BUY/SELL/HOLD<br/>+ stop/target]
    MPT --> ALLOC[weights + CASH]
```

## 1. Feature engineering (`ml_engine/features.py`)

`build_all_features(prices, sentiment, macro, universe)`:
1. Per ticker (needs ≥ 50 rows): `build_features_for_df()` computes technicals, merges sentiment & macro,
   builds the target, then **shifts every feature by 1 row** (look-ahead mitigation) into `feat_*` columns.
2. Concatenate all tickers, then `add_cross_ticker_features()` adds SPY/QQQ-relative features.

**Feature groups** (all emitted as `feat_*`, all shift(1)):

| Group | Features |
| :-- | :-- |
| Normalized Price / Vol | `returns`, `volatility_10`, `parkinson_vol_10` (rolling extreme-value), `ma_ratio`, `volume_ratio` (rolling volume multiplier), `returns_vol_adj` |
| Stationary Techs | `rsi_14`, `bb_width`, `atr_ratio` (stationary volatility scaled by close), `close_to_sma10`, `close_to_sma50`, `high_low_ratio`, `close_to_bb_mid`, `macd_ratio`, `macd_signal_ratio` (all absolute prices like open/high/low/close/bb_mid are excluded to maintain stationarity) |
| Sentiment (Decayed) | `news_sentiment_score`, `reddit_sentiment_score`, `news/reddit_mention_count`, `combined_sentiment_decayed` (0.6·news+0.4·reddit scored via 7-bar half-life EMA), `sent_sma_3`, `sent_sma_7`, `sent_momentum` |
| Macro | `fed_funds`, `yield_spread` |
| Alternative Disclosures | `insider_buying_ratio` / `insider_buying_30d` (rolling 30d corporate buying value / close), `congress_buying_ratio` / `congress_buying_90d` (rolling 90d Congressional buying value / close) |
| Cross-ticker | `relative_return_spy/qqq`, `relative_vol_spy/qqq`, `cum_rel_ret_spy_50`, `rank_return` (rank returns per day), `rank_volatility` (rank volatility per day), `rank_volume_ratio` (rank volume multiplier per day), `rank_sentiment` (rank sentiment per day), `corr_spy_20`, `corr_qqq_20` |

**Target (`target_win`) — triple-barrier:** `1` only if, within `SHORT_TERM_HORIZON_BARS` (14) bars, the
**take-profit is touched before the stop** (path-dependent, intrabar high/low); `0` if the stop hits first
or neither does (timeout); same-bar ambiguity is scored conservatively as a loss; NaN on the censored tail.
Brackets are ATR-based (`stop = clip(2·ATR/close, 1.5%, 5%)`, `tp = 2.5·stop`) — the **same `config.py`
params used for live orders and the backtest time-stop**, so the label equals the trade as executed. This
   replaced the old "did the high touch +2%" breakout target, which scored a volatility *touch* (inflated AUC
   0.925) rather than an exitable trade. Judged by the full **walk-forward + capital-aware portfolio
   simulation** (`run.py walkforward`): pooled AUC 0.710 and a faint per-trade tail edge (+0.0048 in the top
   0.1%), but as a **real capital-constrained portfolio the strategy LOSES 27–40%** (negative Sharpe) — not
   deployable. See [current-state-and-gaps.md §1b](./current-state-and-gaps.md#1b-validation-results).

**Entry threshold (calibrated, not hand-set):** the BUY cutoff is derived per served model by
`calibrate_threshold()` to hit a target selectivity (`SHORT_TERM_SIGNAL_RATE`, default top 0.5%) on a
held-out slice, and written to `saved_models/threshold.json` (latest ≈ **0.1335**). Inference/backtest read
it via `load_buy_threshold()`, falling back to `SHORT_TERM_BUY_THRESHOLD` only if absent. This replaced the
old hand-set 0.23 (which was tuned on a different model than served — PR#2 review C6).

**Sentiment & macro join (fixed):** `build_features_for_df` derives `cal_date = date[:10]` and joins
daily-grained news/macro on it, so a day's sentiment/macro broadcasts across all of that day's hourly bars
(previously the join silently failed and these features were always 0). Mock rows are excluded from
training. Sentiment is a **short-term-only** input; the long-term/regime model is price+macro.

> **Resolution (Stage 19 — done):** short-term features/targets run on hourly `recent_prices` with a
> bar-aware horizon; the regime + MPT long-term path now reads daily `daily_prices`. Both features and solvers
> have been fully updated. The regime classification is daily-based and weight allocation uses the SciPy solver.
> Exposes look-ahead protected Congressional and SEC Form 4 insider buying features and supports beta-neutral / pair-trade long-short hedging.

## 2. Short-term model A — XGBoost (`models.py`)

- `XGBClassifier(n_estimators=100, max_depth=4, lr=0.05, subsample=0.8, colsample_bytree=0.8)`.
- Trained on all `feat_*` columns (hourly `recent_prices` + real sentiment + macro) vs `target_breakout`.
- **Sample weights = exponential temporal decay**, 5-year half-life (recent rows weigh more).
- **Out-of-sample eval**: a time-ordered 80/20 split prints ROC-AUC + precision@BUY before the production
  model is refit on all data.
- Saved to `ml_engine/saved_models/short_term_model.json`.

## 3. Short-term model B — PyTorch Temporal Attention (`deep_models.py`)

- `LightTemporalAttentionNet`: GRU (hidden 32) → scaled dot-product self-attention → mean-pool → sigmoid.
- Input = sequences of **10 consecutive rows** per ticker; features standardized with mean/std saved to
  `temporal_attention_metadata.pkl`.
- Same 5-year temporal-decay sample weights; `BCELoss`, Adam lr 0.005, default 30 epochs (Makefile uses 100).
- Saved to `temporal_attention_model.pth`.

**Which model serves** is explicit via `SERVED_MODEL` (default `xgboost`; `pytorch` opt-in) — no longer
"whatever `.pth` exists" (PR#2 review C6/C14). When `pytorch` is selected, a ticker with < 10 valid rows
falls back to XGBoost for that ticker. Both output a single probability `prob ∈ [0,1]`.

```mermaid
flowchart LR
    A[ticker features] --> B{SERVED_MODEL}
    B -->|pytorch & >=10 rows| D[PyTorch prob]
    B -->|pytorch & <10 rows| E[XGBoost prob]
    B -->|xgboost default| E
    D & E --> F{"prob ≥ calibrated BUY threshold (threshold.json) → BUY<br/>prob ≤ SELL_THRESHOLD (0.02) → SELL<br/>else HOLD"}
```

## 4. Long-term model — HMM regime + MPT (`models.py`)

**Regime (HMM):** `GaussianHMM(n_components=3, covariance_type="diag")` fit on **daily** SPY
`[feat_volatility_10, feat_fed_funds, feat_yield_spread]` from `daily_prices` (multi-decade, 1998→ — so the
regime model now actually sees real bull/bear/crisis history incl. dot-com & 2008, not 5 years of hourly).
The 3 states are sorted by mean volatility and mapped → `growth` (lowest) / `transition` / `crisis`
(highest). Saved to `hmm_model.pkl` + `hmm_metadata.pkl`. At inference the **last daily SPY row** sets the
current regime.

**Allocation (MPT):** `PortfolioOptimizer.calculate_optimal_weights(daily_returns[-252:], regime)` — fed
**daily** returns (≈1 trading year) from the `DailyPrice` database table:
- Expected returns = annualized mean; covariance = **Ledoit-Wolf shrinkage**, annualized.
- **SciPy Optimizer**: Solves for Maximum Sharpe Ratio using `scipy.optimize.minimize` (SLSQP), enforcing constraints (weights sum to 1.0, long-only, max asset concentration of 25% in growth regimes and 10% cash/defensive concentration in crisis regimes).
- In `crisis` regime the MPT allocation scales all optimal weights by 0.5 (keeping ~50% cash) to manage risk.

## 5. Position sizing — Fractional Kelly (`models.py`)

Used by the **executor**, not the suggestions endpoint:
- `f* = (b·p − q)/b`, clipped to [0,1], then × fraction. `p` = model confidence, `b` = payoff ratio
  (hard-coded 2.5), `fraction = 0.2` (so ~1/5-Kelly).
- Final trade value = `min(10% of equity, equity·f*)`; skipped if < $100.

## 6. Training & artifacts

```mermaid
flowchart LR
    subgraph train["run.py train  (= make train, epochs 100)"]
        M1[models.py --train<br/>XGBoost + HMM]
        M2[deep_models.py --train<br/>PyTorch]
    end
    M1 --> J[short_term_model.json]
    M1 --> P1[hmm_model.pkl]
    M1 --> P2[hmm_metadata.pkl]
    M2 --> T1[temporal_attention_model.pth]
    M2 --> T2[temporal_attention_metadata.pkl]
```

All artifacts live in `backend/ml_engine/saved_models/` (plus `threshold.json` from calibration). They are
**gitignored** (regenerated by `make train`), so a fresh clone serves nothing until trained.

> **Held-out evaluation:** `train_models` now prints an out-of-sample ROC-AUC and precision@BUY from a
> time-ordered 80/20 split (XGBoost) before refitting on all data. PyTorch still reports in-sample
> loss/accuracy. For full strategy P&L, also run `run.py backtest` (PyBroker) — see
> [execution-and-simulation.md](./execution-and-simulation.md). Surfacing these metrics in the UI is still open.
