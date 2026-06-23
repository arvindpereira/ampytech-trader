# Swing + News Model — Tuning Guide

This document is the practical reference for improving the XGBoost swing model (`ml_engine/swing_alpha.py`). It covers the full feature set, what each knob does, how to measure improvement honestly, and where to get more historical training data.

---

## 1. The current model at a glance

| Dimension | Current value | Notes |
|---|---|---|
| Algorithm | XGBoost `XGBClassifier` | `n_estimators=120, max_depth=4, lr=0.05` |
| Features | 46 `feat_*` columns | Listed in §3 |
| Target | 5-day triple-barrier `target_win` | ATR-based stop/TP brackets |
| Stop / TP | `stop_min=1.5%, stop_max=12%, atr_mult=2.0, tp_mult=3.0` | Active in `config.py`; see §4 |
| BUY threshold | **0.09** (F1-calibrated on full training set) | Very permissive; see §5 |
| Training window | 2015–2026, but LLM-active only from **2021** | ~151.9k rows total |
| Sample weighting | 5-year half-life exponential decay | Older trades count less |
| Walk-forward OOS | 4 folds, `warmup_frac=0.4` | The honest metric is portfolio Sharpe here |
| OOS Sharpe | ~0.70 (with 2022 bear in test window) | See `strategy-evaluation-findings.md` |

Run `make swing-eval` for the full walk-forward comparison or use the Model Evaluation tab with `oos_start=2022-01-01`.

---

## 2. How to measure improvement (don't use AUC alone)

The only honest score is **portfolio-level OOS Sharpe** from `simulate_portfolio_chronological`, with 2022 in the test window. A higher AUC that doesn't translate into better Sharpe means the model learned something spurious. The evaluation harness already computes this — trust the equity curve and Sharpe/MaxDD table, not in-sample accuracy or AUC on its own.

Always evaluate with `oos_start=2022-01-01` (or earlier) in the Model Evaluation tab so the 2022 bear market is in the **test** set, not the training set.

---

## 3. Features — what each group does and tuning options

### 3.1 LLM news (3 features) — **keep, highest value**

| Feature | What it is |
|---|---|
| `feat_llm_news` | 3-day relevance-weighted decayed mean (−1 bearish → +1 bullish), shifted +1 day |
| `feat_llm_news_intensity` | Rolling sum of relevance weights — measures *how much* news is flowing |
| `feat_llm_news_today` | Yesterday's single-day weighted score |

These three features are what separates this model from a pure-technical baseline. The walk-forward evaluation in `ml_engine/swing_alpha.py` prints `WITH LLM news` vs `WITHOUT (base)` portfolio metrics side-by-side — run `make swing-eval` to see the live delta.

**Tuning options:**
- The 3-day decay half-life in `add_llm_features` (`swing_alpha.py:~102`) can be lengthened to 5–7 days for slower-moving sentiment regimes.
- The relevance threshold (currently no floor) could filter out low-relevance headlines before computing the weighted score.
- Premium newsletter scores (`source='premium:the-information'`) are already included when `exclude_premium=False`. Check `/api/premium/value` for whether they are adding directional edge.

**Backfill options:** See §6.

### 3.2 Technical indicators (17 features)

| Feature | What it captures | Tuning notes |
|---|---|---|
| `feat_returns` | Yesterday's 1-day return | Already present in `returns_vol_adj` — may be redundant |
| `feat_returns_vol_adj` | Return ÷ 10-day vol (signal-to-noise) | The better version of raw returns |
| `feat_volatility_10` | 10-day close-to-close vol | Regime proxy; keep |
| `feat_parkinson_vol_10` | High-low range vol (10-bar) | Captures intraday swings better than `volatility_10`; usually useful |
| `feat_rsi_14` | RSI-14 (0–100) | Classic mean-reversion signal; check importance |
| `feat_ma_ratio` | SMA-10 ÷ SMA-50 | Trend filter; one of the stronger technicals |
| `feat_close_to_sma10` | Price position vs 10-day MA | Short-term mean reversion |
| `feat_close_to_sma50` | Price position vs 50-day MA | Medium-term trend |
| `feat_macd_ratio` | MACD line normalized by price | Momentum crossover |
| `feat_macd_signal_ratio` | MACD signal normalized | Often colinear with `macd_ratio`; consider dropping |
| `feat_bb_width` | Bollinger Band width | Volatility regime — narrow BB before breakout |
| `feat_close_to_bb_mid` | Band position | Mean-reversion within band |
| `feat_atr_ratio` | ATR-14 ÷ close | Realized volatility for sizing; also a regime signal |
| `feat_high_low_ratio` | (H−L) ÷ close | Intraday stress; can signal distribution days |
| `feat_volume_ratio` | Volume ÷ 20-day avg | Breakout confirmation; check importance |

**Potential improvements:**
- Add a **momentum lookback** feature: 20-day and 60-day cumulative return (separate from the 50-day relative-to-SPY already in cross-ticker features).
- Add **RSI divergence**: RSI trend over last 5 bars vs price trend — divergences often precede reversals.
- Add **volume-weighted VWAP deviation**: `(close − VWAP) / ATR` — requires intraday data or a daily VWAP proxy.

### 3.3 Cross-ticker / market context (10 features)

| Feature | What it captures |
|---|---|
| `feat_relative_return_spy/qqq` | Excess return vs the index (raw alpha yesterday) |
| `feat_relative_vol_spy/qqq` | Relative volatility (beta proxy) |
| `feat_cum_rel_ret_spy_50` | 50-day cumulative alpha (momentum vs index) |
| `feat_corr_spy_20`, `feat_corr_qqq_20` | Rolling 20-day return correlation with SPY/QQQ |
| `feat_rank_return` | Cross-sectional percentile rank of yesterday's return |
| `feat_rank_volatility` | Percentile rank of volatility in the universe |
| `feat_rank_volume_ratio` | Percentile rank of volume spike |
| `feat_rank_sentiment` | Percentile rank of VADER sentiment |

These are computed in `add_cross_ticker_features` (`features.py:395`).

**Potential improvements:**
- **Sector relative performance**: add `feat_relative_return_sector` — how the stock performed vs its GICS sector ETF (XLK, XLF, etc.). Currently only SPY/QQQ are used as benchmarks.
- **52-week high/low proximity**: `(close − 52w_low) / (52w_high − 52w_low)` — breakout from range is a documented momentum signal.

### 3.4 VADER sentiment (7 features) — possibly redundant with LLM

| Feature | Source |
|---|---|
| `feat_news_sentiment_score` | Aggregate VADER on news articles |
| `feat_news_mention_count` | Number of news articles |
| `feat_reddit_sentiment_score` | VADER on Reddit posts |
| `feat_reddit_mention_count` | Reddit volume |
| `feat_combined_sentiment_decayed` | 0.6×news + 0.4×reddit, 7-bar EWM decay |
| `feat_sent_sma_3/7` | Rolling mean of above |
| `feat_sent_momentum` | Current − 10-bar rolling mean (trend) |
| `feat_rank_sentiment` | Cross-sectional rank |

**Key question:** Given that `feat_llm_news` already captures directional headline sentiment with higher semantic precision, do the VADER features add anything?

To test: run the walk-forward with and without VADER features. If XGBoost's feature importance ranks VADER features consistently near the bottom AND the portfolio Sharpe doesn't drop without them, they can be removed to reduce noise.

### 3.5 Macro (2 features)

| Feature | What it captures |
|---|---|
| `feat_fed_funds` | Federal funds rate (monetary tightening regime) |
| `feat_yield_spread` | 10Y−3M spread (inversion = recession signal) |

These are slow-moving regime features. They won't differentiate individual stock moves but help the model calibrate its aggressiveness in different macro environments.

**Potential additions:**
- **VIX level** (or a VIX proxy from SPY ATR): fear gauge, strong BUY entry signal when it spikes and subsides.
- **Credit spread** (HY OAS): already fetched in `market_stress_fetcher.py` into `macro_indicators` but not joined into swing features. Add `hy_oas` and `ig_oas` from the `macro_indicators` table.
- **Breakeven inflation** (TIPS spread): differentiates stagflation vs growth regimes — already fetched in the market stress pipeline.

### 3.6 Alternative data (9 features) — **currently dead weight**

| Feature | Status |
|---|---|
| `feat_insider_net_flow`, `feat_insider_buy_count`, `feat_insider_net_buyers`, `feat_insider_officer_buy`, `feat_insider_cluster` | All **zero** when `ALT_DATA_ENABLED=False` |
| `feat_congress_buying_ratio`, `feat_congress_buying_90d` | Same — zero by default |

These are always 0 in the default configuration and burn tree splits doing nothing useful. Two options:

1. **Drop them from training** until `ALT_DATA_ENABLED=True`: edit `swing_alpha.py` to exclude these columns when they are all-zero. This reduces noise and speeds up training slightly.
2. **Enable alternative data**: set `ALT_DATA_ENABLED=True` in `.env` and run `make insider` to populate SEC Form 4 filings. Real insider buys, especially officer cluster buys, have documented predictive value.

---

## 4. Label engineering (triple-barrier target)

The target is set in `features.py:triple_barrier_outcomes()`.

> [!WARNING]
> **Code Configuration Mismatch:**
> In `swing_alpha.py` line 149, `build_all_features` is called without explicit overrides for the stop/TP parameters. This forces the model to train using the defaults in `features.py` (`tp_mult=2.5`, `stop_max=0.05` / 5%). However, the backtest and scheduler execute using the parameters from `config.py` (`SWING_TP_MULT=3.0`, `SWING_STOP_MAX=0.12` / 12%). This mismatch should be fixed by passing `SWING_TP_MULT` and `SWING_STOP_MAX` to `build_all_features` inside `swing_alpha.py`.

| Parameter | Current (Code Execution) | Meaning | Effect of increasing |
|---|---|---|---|
| `horizon` | 5 days | Max hold time before timeout | Longer = fewer winners (more time for stop to hit) |
| `atr_stop_mult` | 2.0 | Stop = 2×ATR from entry | Wider stop = fewer losses, but larger when they occur |
| `tp_mult` | 3.0 (Trained on 2.5) | TP = 3.0× the stop distance | Higher = fewer winners, larger when hit |
| `stop_min` | 1.5% | Floor on stop-loss | Prevents hair-trigger stops on low-vol names |
| `stop_max` | 12.0% (Trained on 5.0%) | Cap on stop-loss | Prevents excessive risk on high-vol names |

The Reward:Risk ratio at current execution settings is **3.0:1** (TP:stop ratio). The base rate of `target_win=1` in the training set can be calculated via:

```bash
python3 -c "
from ml_engine.swing_alpha import load_swing_data
full, _, _, _ = load_swing_data(horizon=5)
print('Win rate:', full['target_win'].mean())
print('Samples:', len(full.dropna(subset=['target_win'])))
"
```

Tuning options:
- **Tighter stop, higher TP** (e.g., `atr_mult=1.5, tp_mult=3.5`): fewer but higher-quality wins. Labels become harder to predict but each win pays more.
- **Asymmetric horizon**: let wins run longer than 5 days by scanning the forward window up to 10 days for TP-hits only. The current code uses a symmetric window.
- **Log-return labels** instead of binary: replace `target_win` with the realized `trade_ret` and train an XGBoost regressor. Then rank by predicted return magnitude rather than win probability.

---

## 5. Threshold tuning

The current threshold **0.09** is extremely low — it was calibrated to maximize F1 on the training fold, which trades precision for recall. At this level, the top-N filter (`SWING_TOP_N=10`) is doing most of the actual selection work.

**What the threshold really controls:** a signal passes as BUY if `model.predict_proba() >= threshold`. With threshold=0.09, almost everything passes and the cap on open positions (`SWING_TOP_N`) becomes the true filter. This is fine if you want the top-10 by conviction, but it means the threshold is not doing meaningful filtering.

**How to calibrate better:**
- The current `find_optimal_threshold` fits an inner model on the first 80% of the training fold and finds the threshold on the last 20% validation subset, preserving chronological order.
- Target **precision ≥ 55%** at the chosen threshold (i.e., at least 55% of BUY signals should win). This is more actionable than maximizing F1.

To check current OOS precision at the threshold:
```bash
python3 -c "
from ml_engine.swing_alpha import swing_oos_frame
oos, _, _ = swing_oos_frame(n_splits=3, oos_start='2022-01-01')
if oos is not None:
    above = oos[oos['prob'] >= oos['selected_threshold']]
    print(f'Signals: {len(above)}, Win rate: {above[\"target_win\"].mean():.3f}')
    print(f'Expected win rate random: {oos[\"target_win\"].mean():.3f}')
"
```

---

## 6. XGBoost hyperparameter tuning

Current: `n_estimators=120, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8`

| Hyperparameter | Current | What increasing does | Suggested range |
|---|---|---|---|
| `n_estimators` | 120 | More trees = more complex model; risk of overfitting | 80–300 |
| `max_depth` | 4 | Deeper trees = more interaction terms | 3–6; 4 is usually good for tabular |
| `learning_rate` | 0.05 | Lower = slower but more regularized | 0.01–0.1 (increase `n_estimators` proportionally) |
| `subsample` | 0.8 | Row sampling per tree | 0.6–1.0 |
| `colsample_bytree` | 0.8 | Feature sampling per tree | 0.5–1.0 |
| `min_child_weight` | — | Minimum sum of sample weights in a leaf | Adding `min_child_weight=5–20` reduces overfitting |
| `reg_alpha` | — | L1 regularization | Try 0.1–1.0 to prune dead features automatically |
| `reg_lambda` | 1.0 | L2 regularization | 1.0–5.0 |

**Recommended first experiment:** add `min_child_weight=10, reg_alpha=0.5` to the existing XGBClassifier config in `train_and_save()` and re-run the walk-forward. These two additions reduce leaf-node overfitting without requiring extensive search.

**Systematic search** (when you want to invest more time):
Use the walk-forward Sharpe (not accuracy or AUC) as the objective with a small grid over `{max_depth: [3,4,5], n_estimators: [80,120,200], min_child_weight: [5,15]}`.

---

## 7. Training window and sample weighting

**Current:** 5-year half-life exponential decay. Rows from 5 years ago have weight `exp(-1) ≈ 0.37` relative to today.

**The tension:** more historical data = better generalization; but patterns from 2015–2020 may not transfer to the 2024 LLM-driven market. The current decay is a reasonable compromise.

**Options:**
- **Shorten the half-life** to 2–3 years to emphasize recent market structure. Change `5.0 * 365.25` in `train_and_save()` and `swing_oos_frame()`.
- **Hard cutoff**: only train on data from 2021 onward (full LLM coverage). This reduces the training set but eliminates the regime mismatch between pre/post-LLM eras. Already enforced by `first_llm_date` — but you could raise it explicitly.
- **Regime-conditional weighting**: upweight samples that were in the same HMM regime as today. Requires joining `crash_risk_snapshots.current_posture` onto the training rows.

---

## 8. LLM news data backfill (2015–2021)

### What we have

```
2015–2020:  ~4 scored articles total (essentially nothing)
2021:       18,372 scored articles across 37 tickers
2022:       10,511 scored articles across 29 tickers
2023:        8,306 scored articles across 27 tickers
2024–2026:  ~94,000 scored articles across 59–83 tickers
```

Dense coverage starts in 2021. Pre-2021 data would add 5–6 more years of LLM training signal and let the model learn from the 2018 crash, 2020 COVID crash, and the 2018–2020 bull run — all currently missing from the LLM-active window.

### Source: Massive/Polygon historical news

The existing pipeline (`data_ingestion/news_llm.py`) fetches from `MASSIVE_BASE_URL/v2/reference/news` which is the Polygon news API. Polygon has news articles going back to at least 2010 for major tickers. The `NEWS_LLM_START` config key (currently `2021-01-01`) is the only thing blocking a backfill.

**To backfill 2015–2021:**
The `news_llm.py --batch` execution submits the batch and blocks while polling until the job is complete. If the execution is interrupted, it can be re-ingested with `make news-llm-batch-collect`.

```bash
# Submit and run batch job (blocks and polls until complete)
make news-llm-batch START=2015-01-01 TICKERS=NVDA,AAPL,MSFT,GOOGL,AMZN,META,AMD,TSLA

# Ingest results manually only if the run was interrupted (re-collecting with BATCH_ID)
make news-llm-batch-collect BATCH_ID=<id>

# Or run standard API directly (faster for smaller subsets, ~$1–2 for the core universe)
make news-llm START=2015-01-01 PROVIDER=openai TICKERS=NVDA,AAPL,MSFT,GOOGL,AMZN
```

Coverage caveats:
- **Major US large-caps** (NVDA, AAPL, MSFT, GOOGL, AMZN, META, JPM, etc.): expect dense coverage from 2015 onward.
- **Mid-cap tech** (AMD, MU, QCOM, INTC): good coverage from 2017–2018 onward.
- **Newer names** (PLTR, ARM, SMCI): only listed post-2020, nothing before IPO.
- **Thin tickers** (BB, NOK, SPACE): may have very sparse pre-2021 coverage.

**Recommended approach:** backfill the 10–12 highest-liquidity tickers in the universe first (NVDA, AAPL, MSFT, GOOGL, AMZN, META, AMD, TSLA, JPM, AMZN), retrain, and check if walk-forward Sharpe improves before spending time on smaller names.

### Alternative historical news sources

| Source | Coverage | Notes |
|---|---|---|
| **Polygon (via Massive)** | 2010+ for large-caps | Already integrated; just change `START` date |
| **Benzinga** | 2010+ | Already used for analyst forecasts; can be adapted for news |
| **Alpha Vantage** (News API) | 2019+ | Requires separate API key; ticker-specific news feed |
| **Tiingo News** | 2019+ | Good coverage, REST API, ticker-filtered |
| **NewsAPI** | 1 month free, paid for history | General news; not ticker-specific natively |
| **EOD Historical Data** | 2000+ | Has financial news archive; not free for deep history |

Of these, **Polygon via the existing Massive key is the path of least resistance** — the API endpoint and scoring pipeline are already wired up.

---

## 9. Feature importance — how to inspect what the model uses

```python
# Run in backend/
from ml_engine.swing_alpha import load_swing_model
import pandas as pd

model, meta = load_swing_model()
fi = pd.Series(model.feature_importances_, index=meta['feature_cols'])
print(fi.sort_values(ascending=False).to_string())
```

Or from the command line:
```bash
cd backend && source venv/bin/activate && python3 -c "
from ml_engine.swing_alpha import load_swing_model
import pandas as pd
m, meta = load_swing_model()
fi = pd.Series(m.feature_importances_, index=meta['feature_cols'])
for name, imp in fi.sort_values(ascending=False).items():
    print(f'{imp:.4f}  {name}')
"
```

Features with importance consistently below 0.005 across multiple training runs are candidates for removal. The alternative data features (`feat_insider_*`, `feat_congress_*`) likely land near the bottom given they're all-zero — confirming they're dead weight.

---

## 10. Recommended tuning sequence

Start here — each step is independent and takes < 1 hour:

1. **Print feature importances** (see §9). Confirm the LLM features rank in the top 10. Identify and drop zero-importance features (likely the insider/congress group).

2. **Add regularization** to XGBoost: `min_child_weight=10, reg_alpha=0.5`. Re-run swing walk-forward (`make swing-eval`) and compare Sharpe.

3. **Test VADER removal**: run with and without `feat_news_sentiment_score`, `feat_reddit_*`, `feat_sent_*`, `feat_rank_sentiment`. If Sharpe holds, remove them — they are likely redundant with `feat_llm_news`.

4. **Backfill 2015–2021 news** for the top 10 large-cap tickers, retrain, and re-evaluate. This is likely the highest-leverage single improvement available.

5. **Add macro features**: wire `hy_oas` (high-yield credit spread) and `vix_proxy` (SPY ATR ratio as a VIX proxy) from `macro_indicators` into `build_features_for_df`.

6. **Fix the code configuration mismatch first**: modify `swing_alpha.py` to pass the correct configuration variables (`SWING_TP_MULT` and `SWING_STOP_MAX`) to `build_all_features`. Then experiment with tightening the labels (e.g. `tp_mult=3.5`, `atr_mult=1.8`) to see if OOS Sharpe improves.

7. **Add 20-day and 60-day momentum** features (cumulative return over the window) as additional trend signals.
