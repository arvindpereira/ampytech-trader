# Strategy Suggester — Plan (per-ticker: Swing vs Long-term MPT vs Hold)

> **Status: PLAN, not built.** This drafts an evidence-driven recommender that, for each ticker in the
> universe, suggests which strategy bucket should manage it — feeding the per-stock strategy dropdown in
> the Portfolio tab. It deliberately avoids a black box: every suggestion comes with the numbers behind
> it and a confidence, and the defaults are conservative given [the evaluation findings](./strategy-evaluation-findings.md)
> (swing is a bull amplifier; MPT carries resilience but its absolute returns are survivorship-inflated).

## Objective

Map each ticker → `swing` | `longterm` | `hold`, with a one-line rationale + confidence, surfaced in the
Portfolio tab as suggestions the user can **accept (one click) or override**. The bot recommends; the
user sets the high-level allocation and limits (already built: capital buckets + per-ticker strategy).

## Guiding principle (from the findings)

- **Swing only earns its place for a ticker that shows a genuine, news-driven, out-of-sample edge** —
  not just because it's volatile. The default is **not** swing.
- **Long-term MPT is the default for stable compounders / defensive names** and anything the optimizer
  reliably wants to hold.
- **Hold** for names with thin news *and* weak/again-unproven long-term quality (monitor only).
- When evidence is weak or conflicting, **prefer longterm/hold over swing** (swing's downside is the
  documented risk).

## Per-ticker evidence signals (all computable from the existing harness)

1. **Swing OOS edge (per ticker).** From `backtest_swing_curve`'s pooled OOS frames (already has
   `ticker, prob, selected_threshold, trade_ret`), grouped by ticker over the **2022+** window:
   - # swing trades taken, win rate, **mean trade return / expectancy**, and a per-ticker trade Sharpe.
   - Critically, performance **in 2022** vs **in the bull years** (does it only work in up-markets?).
   - Require a minimum trade count for significance; sparse → not enough evidence for swing.
2. **News responsiveness.** From `news_llm_scores`: per-ticker news **volume** and the **correlation of
   the relevance-weighted daily news score with next-N-day returns**. High volume + positive
   predictive correlation → swing-suitable; sparse or zero correlation → not.
3. **Long-term quality / MPT affinity.** From daily returns: trailing **Sharpe**, **max drawdown**,
   12-1 **momentum**, and **how often / how much weight the MPT optimizer assigns the ticker** across
   the backtest rebalances. Frequently-weighted, high-Sharpe, lower-drawdown → longterm-suitable.
4. **Bear behavior.** The ticker's **2022 (and COVID) return/drawdown** — defensive names (positive or
   shallow in bears) lean longterm/hold; high-beta amplifiers are only swing candidates *if* signal 1
   is strong.
5. **Liquidity/volatility** sanity (already have a volatility screener) — extreme illiquidity → hold.

## Scoring → recommendation

A transparent, weighted rubric (tunable), not an opaque model:

```
swing_score    = f(swing_OOS_expectancy, swing_trade_Sharpe, news_corr, news_volume)   # gated on min trades
longterm_score = g(trailing_Sharpe, -max_drawdown, momentum, mpt_weight_share, bear_resilience)
```

- Recommend **swing** only if `swing_score` clears a bar **and** swing's 2022 behavior isn't
  catastrophic **and** it beats that ticker's longterm_score by a margin.
- Else recommend **longterm** if `longterm_score` clears a bar.
- Else **hold**.
- Emit **confidence** = function of evidence strength (trade count, correlation significance, history
  length) and the margin between the two scores. Low confidence is shown as such.

Each row returns: `{ticker, recommended, confidence, swing_score, longterm_score, rationale, evidence:{...}}`
where `rationale` is a plain sentence ("32 swing trades OOS, +0.6%/trade, news corr 0.21, but −31% in
2022 → longterm").

## UX (Portfolio tab)

- A **"Suggest strategies"** button runs the analysis as a **background job** (reuse the job registry +
  progress bar, like evaluation/backfill — it's compute-heavy: it walk-forwards swing + scans news).
- Results annotate each ticker row: a **suggested-strategy chip** + confidence + hover rationale, shown
  next to the existing strategy dropdown.
- **Accept all / accept per-row** applies suggestions to the per-ticker `strategy` assignments
  (existing `/api/strategy/ticker`); the user can still override any.
- Always-visible caveat banner: suggestions are based on a single-regime, survivorship-biased history;
  they bias conservative.

## Validation (does it actually help?)

Before trusting it, **backtest the suggester itself**: build the blended OOS curve using the *suggested*
assignments vs the current/naive (all-swing) assignments, over the 2022+ window. The suggester is only
worth shipping if the suggested split improves the **blended** risk-adjusted return / drawdown
out-of-sample. Wire this as a one-click comparison in the Model Evaluation tab.

## Phasing

- **v1 — static per-ticker suggestions** (signals 1–4, rubric, UX, validation). Conservative defaults.
- **v2 — regime-aware overlay. ✅ SHIPPED.** Since swing fails in bears, the executor now shrinks the
  swing bucket's *effective* capital by a regime factor (`REGIME_SWING_FACTORS`: crisis ×0.25,
  transition ×0.6, growth ×1.0) when the HMM regime turns defensive; the freed capital is held as cash
  (no force-selling — consistent with the soft-cap policy), and it lifts automatically as the regime
  recovers. Surfaced in the Portfolio allocation card ("Regime overlay active") and in
  `/api/strategy/config` (`regime`, `swing_factor`, `effective_swing`). Toggle with `REGIME_OVERLAY_ENABLED`.
- **v3 — periodic auto-refresh** (weekly, alongside retrain) with change-alerts, never auto-applying
  without the user opting in.

## Honesty guardrails

- Never imply precision we don't have: show trade counts, correlations, and confidence; mark
  low-evidence tickers explicitly.
- Default away from swing under uncertainty.
- Surface, don't hide, that MPT's absolute backtest returns are survivorship-inflated — the suggester
  uses MPT's *relative/risk* behavior, not its absolute return, as the longterm signal.
