# Strategy Evaluation Findings (honest, out-of-sample)

> **TL;DR.** Once the **2022 bear market** is included in the out-of-sample test (made possible by
> backfilling LLM-scored news to 2021), the **Swing + News** strategy's apparent edge largely
> disappears: its risk-adjusted return falls to roughly market-level (Sharpe ≈ 0.70 vs S&P ≈ 0.66) and
> it **lost ~25% in 2022 — worse than the S&P's −20%**. Swing behaves like a **bull-market amplifier
> with no downside protection**. The **Long-term MPT** book was genuinely resilient in 2022 (+7.2%),
> but its eye-popping absolute returns are **inflated by survivorship bias** and should not be taken as
> a forward expectation. The **blended book** (capital split across both) is the most defensible: it
> cushioned the 2022 drawdown and has the lowest max drawdown of the group. Net: **bias toward
> long-term MPT for resilience, treat swing as a smaller, bull-leaning sleeve, and trust the *relative*
> signals far more than the *absolute* backtested returns.**

This document records how the strategies are evaluated and what the evaluation actually shows, so the
numbers are never read naively. It is the companion to the **Model Evaluation** tab
(`/api/evaluate`, `ml_engine/evaluate.py`).

## How we evaluate (look-ahead-free)

- **Swing + News** — expanding-window **walk-forward**. Each fold trains XGBoost only on data *strictly
  before* its test window; the entry threshold is tuned on an inner split of the training fold only;
  LLM-news features are **point-in-time** (relevance-weighted score shifted +1 day). Pooled OOS signals
  run through the capital-aware portfolio simulator (≤10 positions, 10%/position, fees).
- **OOS-start control** — `oos_start` (UI: "OOS test starts") fixes where testing begins, so the
  training set ends before it. Setting `oos_start = 2022-01-01` puts the 2022 bear in the **test** set
  rather than the training set. This is the single most important knob for an honest read.
- **Long-term MPT** — monthly-rebalanced max-Sharpe (regime-aware) weights using **only trailing
  returns** for the covariance; held between rebalances.
- **Benchmarks** — SPY, QQQ, BRK-B, buy-and-hold over the identical window.
- **Stress windows** — the MPT engine + benchmarks can be re-run over historical bears (2022, 2020
  COVID, 2008 GFC, dot-com). Swing is **not** evaluable pre-2021 (no LLM news there).

## The decisive result: swing with 2022 in the OOS window

Walk-forward, `oos_start = 2022-01-01`, OOS window **2022-01 → 2026-06**, current bucket blend:

| Series | Total | CAGR | Sharpe | Max DD |
| :-- | --: | --: | --: | --: |
| Swing + News | +93.8% | +16.0% | **0.70** | −29.8% |
| Long-term (MPT) | +581% | +54% | 1.98 | −21.4% |
| Blended (allocation) | +240.8% | +31.7% | 1.43 | −18.8% |
| S&P 500 | +57.1% | +10.7% | 0.66 | −25.4% |
| QQQ | +81.7% | +14.4% | 0.69 | −35.2% |
| Berkshire (BRK-B) | +64.6% | +11.8% | 0.73 | −26.6% |

**2022 calendar year only** (the actual bear):

| Series | 2022 return | Max DD |
| :-- | --: | --: |
| **Swing + News** | **−25.0%** | −29.3% |
| Long-term MPT | +7.2% | −15.2% |
| Blended | −10.2% | −18.8% |
| S&P 500 | −19.9% | −25.4% |
| QQQ | −33.7% | −35.2% |
| Berkshire | +2.7% | −26.6% |

### Why the swing numbers changed so much
Earlier swing evaluations showed Sharpe 1.1–1.75. Those runs used the default 40% warmup, which — once
news reached back to 2021 — pushed the OOS window to **2023+**, i.e. a pure bull run, with **2022 hidden
in the training set**. Moving 2022 into the test set is what collapses the Sharpe to ~0.70. This is not
a bug; it is the difference between an honest and a flattering test window. **Fold count and warmup
choice move the swing result by 2–3×** — a sign the per-trade edge is thin and regime-dependent.

## Honest synthesis

1. **Swing is a bull amplifier, not an all-weather edge.** Out-of-sample including a bear, it is
   roughly market-like on a risk-adjusted basis and *amplifies* drawdowns (−25% in 2022 vs −20% S&P).
   Its value is concentrated in trending/up markets driven by fresh news.
2. **Long-term MPT showed real *relative* downside resilience** (2022 +7.2% vs −20% S&P). That part is
   credible. Its **absolute** returns (+581% / 54% CAGR / Sharpe ~2) are **not** — they are inflated by
   **survivorship bias** (the universe is today's surviving winners; the optimizer "knew" to hold the
   NVDA-type names) and by riding a tech-heavy bull. Do not expect those returns forward.
3. **The blended book is the pragmatic answer.** Splitting capital cushioned 2022 (−10% vs swing's
   −25%) and gives the lowest max drawdown. Neither pure strategy is clearly good; the mix is defensible.
4. **Berkshire is the humbling benchmark.** It was *positive* in 2022 and dot-com with the shallowest
   drawdowns — a reminder that the bot's measurable edge here is return-in-bull-markets, not capital
   preservation.

## Practical guidance

- **Bias capital toward long-term MPT** for its bear resilience, but size expectations to *market-like
  or modestly-better* returns — not the backtested figures — because of survivorship bias.
- **Keep swing as a smaller, opportunistic sleeve** (news-driven names in benign regimes), not the core.
- **Re-evaluate with `oos_start = 2022-01-01` (or earlier) by default** — never quote a swing number
  whose OOS window is all-bull.
- **Watch the regime.** Swing's failure mode is precisely a bear; a regime-aware overlay (shrink swing
  in crisis) is the natural next safeguard.

## Caveats / limitations

- **Survivorship bias** in every backtest (fixed current universe applied to the past); worst pre-2020,
  meaningful even in 2022.
- **Single broad regime** of dense data (2021–2026): one real bear (2022). Small sample of stress.
- **LLM-news depth varies** by ticker; foreign ADRs and small names are thin.
- **MPT absolute returns are not a forward estimate.** Relative/risk behavior is the usable signal.
