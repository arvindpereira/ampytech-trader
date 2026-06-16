# PR #2 Review — Stage 18 & 19 (Stationarity, SciPy MPT, Disclosures, Hedging)

**Reviewer:** Claude (cross-check of branch `strategy-optimization` vs `main`)
**Scope reviewed:** full diff (27 files, +2934 / −489), with code read of `alternative_fetcher.py`,
`features.py`, `models.py`, `backtest.py`, `config.py`, `deep_models.py`, `test_alternative_data.py`,
`scratch/grid_search_threshold.py`, and the served model artifacts.

---

## TL;DR verdict

The **infrastructure work is good and largely correct**: the SciPy SLSQP Sharpe optimizer is a real upgrade
over Monte-Carlo; the stationary feature rewrite is sound; the disclosure features are **point-in-time
correct and unit-tested for look-ahead**; train/inference/backtest now share one consistent 40-feature set;
and the walk-forward comparison harness is methodologically clean.

**But the headline claims are not supported by the evidence, and should not be merged as-stated:**

1. **The "alternative data" is 100% synthetic random noise** (`random.seed(42)`), uncorrelated with prices —
   yet it is **enabled by default** and **baked into the served model**. The reported "alt data improves
   AUC 0.698 → 0.700" is within noise; random features cannot add real signal.
2. **The 0.23 buy threshold is overfit** — it was grid-searched on the *same* pooled walk-forward OOS
   predictions it is then evaluated on. That inflates the reported edge.
3. **Hedging exists only in `backtest.py`**, not in the live executor or suggestions API, and depends on
   shorting the virtual broker can't do — so the "WITH Hedging" drawdown numbers don't describe the
   deployable bot.
4. **In-sample backtest returns (489 %–1085 %) are conflated with out-of-sample results.** The only
   trustworthy read is the walk-forward, and there the edge is thin and — at the live threshold — flat to
   slightly negative.

**Bottom line:** PR #2 is a net-positive *engineering* refactor (solver, stationarity, evaluation harness,
a real disclosure pipeline ready for real data). It is **not** evidence that the bot got better at making
money. Merge the infra after the fixes below; do **not** present the alt-data / hedging / in-sample numbers
as improvements.

---

## What's genuinely good (keep)

- **SciPy SLSQP MPT solver** (`models.py:62-87`): proper Sharpe maximization with sum-to-1, long-only,
  per-name caps (25 % normal / 10 % crisis), Ledoit-Wolf covariance, equal-weight fallback. Real upgrade.
- **Stationary features** (`features.py:362-371`): dropping absolute `open/high/low/close/bb_mid` in favor
  of ratios (SMA distance, Bollinger distance, High-Low range, vol-adjusted returns, Parkinson vol) is the
  right call and addresses the non-stationarity flagged earlier.
- **Disclosure features are point-in-time correct** — keyed on *disclosure* date (not transaction date),
  shifted 1 day at daily level + 1 bar at feature level, and **explicitly unit-tested for look-ahead**
  (`test_alternative_data.py:test_feature_calculation`). This is exactly the discipline we want.
- **Feature-set consistency maintained**: training (`models.py:223`), inference (`main.py:243`), and
  walk-forward (`models.py:322`) all use `sorted(feat_* except feat_atr_14)`; PyTorch metadata matches
  (both 40 features). The PR-#1-class mismatch bug did **not** regress.
- **Walk-forward comparison harness** (`models.py:316-`): trains with- and without-alt models on identical
  folds/seed and reports pooled OOS AUC + net-return-by-percentile. Good design — it's the harness that
  *reveals* the alt data adds nothing.

---

## Critical issues (ranked, actionable)

### C1 — Synthetic alt-data presented as signal, enabled by default, in the served model ⛔
**Problem.** `alternative_fetcher.py` *fabricates* Congress/insider trades with `random.*` and a fixed seed,
with transaction types drawn from fixed probabilities and "buying clusters" placed at **random dates
uncorrelated with returns**. `ALT_DATA_ENABLED` defaults to **`True`** (`config.py:98`), `run.py fetch`
seeds it every run (`run.py:27`), and the saved model includes the 4 alt features (verified: 40-feature
model has `insider`/`congress`).
**Why it matters.** Random features carry no information; the 0.698→0.700 AUC delta is noise. Worse, the
*deployed* model is partly keyed on noise, and a reader of the PR would reasonably believe Congress/insider
signal helps — it doesn't, because there is no real data here. At the live 0.23 threshold the PR's own
table shows the alt model collapses to **28 signals in ~3 years** (vs 313 without alt) — i.e. the noise
features mostly *degrade* the tradable strategy.
**Action.**
- Set `ALT_DATA_ENABLED=False` by default until a **real** source is wired.
- Replace the seeder with a real fetcher (Quiver Quantitative / Capitol Trades / Unusual Whales for STOCK
  Act; SEC EDGAR Form 4 ATOM feed for insiders). Keep the (good) point-in-time feature code unchanged.
- Until then, rename `seed_alternative_data` → make it explicit (`seed_SYNTHETIC_alternative_data`) and print
  a loud "SYNTHETIC / NOT REAL" warning so no one mistakes it for signal.
- Re-run the with/without-alt walk-forward only after real data exists; drop the "alt improves AUC" claim
  from the PR description now.

### C2 — 0.23 buy threshold is overfit to the OOS test set ⛔
**Problem.** `scratch/grid_search_threshold.py` runs `walk_forward_evaluate`, then grid-searches thresholds
0.05–0.40 on the **pooled OOS predictions** and picks the max-total-net one (→ 0.23). The threshold is
chosen on the same data used to report its performance.
**Why it matters.** This is hyperparameter selection on the test set — the +0.0046/trade "at 0.23" is
optimistically biased. The true forward number is lower.
**Action.** Select the threshold with **nested/walk-forward selection**: choose it on fold *k*'s data, apply
it to fold *k+1* (unseen), and report only those forward results. Alternatively fix the threshold by a
principled rule (e.g. predicted-prob percentile, or the EV break-even `1/(1+payoff)` on *validation* folds)
and report its performance on later untouched folds.

### C3 — Hedging is backtest-only and not deployable 🟠
**Problem.** `beta_neutral` / `pair_trade` live only inside `backtest.py:short_term_exec` via module-global
state (`active_hedges`, `current_hedges`, `active_longs`). The live executor (`executor.py`) and suggestions
API do not hedge, and the virtual broker can't short.
**Why it matters.** The "WITH Hedging" drawdown improvement (−7.5 % vs −11.4 %) describes a strategy the bot
**cannot actually run**. It's also computed in-sample (see C4). Module-global state additionally leaks
between backtest runs in the same process.
**Action.** Either (a) implement hedging in the live path (executor + virtual broker short support + capital
/margin accounting) and then claim it, or (b) clearly label hedging as **research-only / backtest-only** and
drop it from headline results. Move hedge state off module globals into the strategy context.

### C4 — In-sample backtest returns conflated with OOS 🟠
**Problem.** The PR table leads with "Short-Term Total Return 489 %–1085 %" and drawdowns from
`run.py backtest`, which trains on the full 2021→2026 span and backtests the same span (in-sample/overfit).
These sit beside the OOS walk-forward without a sharp distinction.
**Why it matters.** We already established (PR #1 review) that the in-sample PyBroker number is meaningless
for decisions; presenting +1085 % prominently re-introduces exactly the over-claim we fought to remove.
**Action.** In the PR description and docs, mark all `run.py backtest` numbers **IN-SAMPLE — not predictive**
and lead with the walk-forward OOS table as the only decision-grade evidence.

### C5 — Docs are internally inconsistent (0.15 vs 0.23; prose vs table) 🟠
**Problem.** `docs/current-state-and-gaps.md` §1b table (line 74) now shows the live 0.15 threshold at
**−0.0007 net/trade** (negative), but the prose just below (lines 79-82, 173, 201-202) still asserts
"**+0.27 %/trade at 0.15**" and "~300 signals/yr". The config threshold is now **0.23**, not 0.15.
**Why it matters.** The single most important doc contradicts itself on the headline edge number; a reader
can't tell what's true.
**Action.** Reconcile to one source of truth: state the threshold actually in `config.py` (0.23), the OOS
metrics for *that* threshold from a *non-overfit* selection (per C2), and delete the stale +0.27 %/0.15
prose. Note the regime/distribution shift explicitly.

### C6 — Served model is PyTorch, but the threshold was calibrated on XGBoost 🟠
**Problem.** `main.py` prefers the PyTorch `.pth` when present (it is). The 0.23 threshold was tuned via
`walk_forward_evaluate`, which trains **XGBoost**. XGBoost and the GRU produce different probability
distributions even on identical features, so 0.23 is not necessarily meaningful for the served model.
**Why it matters.** The live BUY rate / quality can differ materially from what was "calibrated."
**Action.** Calibrate (and walk-forward grid-search) the threshold against **the model that actually
serves**, or calibrate probabilities (Platt/isotonic) so a single threshold transfers. Make the served
model explicit/configurable rather than "whatever .pth exists" (this is open gap G14).

### C7 — `run.py fetch` reseeds synthetic data every run 🟡
**Problem.** `fetch` calls `alternative_fetcher.py`, which **deletes and regenerates** all disclosures each
time, interleaving synthetic-data generation with real price/macro/news ingestion.
**Why it matters.** Non-idempotent w.r.t. any real data later added; conflates fabricated and real sources in
one command; surprising side effect of a routine "fetch".
**Action.** Gate behind `ALT_DATA_ENABLED`, separate into its own `make`/`run.py` action, and make real
ingestion incremental (don't wipe). Tie to C1.

---

## Minor / nits

- **MPT expected returns = raw historical mean** (`models.py:46`). Mean-variance is notoriously sensitive to
  this; consider shrinkage toward the grand mean or a CAPM/Black-Litterman prior. (Not blocking.)
- **Alt ratio "stationarity"** (`features.py:338-339`): dividing a *dollar sum* by `close` yields an
  arbitrary-magnitude quantity (share-equivalent), not a bounded ratio; with real data, normalize by
  market cap or dollar-volume instead.
- **Crisis cash handling**: the solver still produces a fully-invested (sum=1) long-only book in crisis (just
  capped at 10 %/name). Confirm the crisis→cash scaling still happens in `main.py` (`regime_scalar`) and
  isn't lost in the daily-table rewire.
- **Deep model epochs 30→20** "to prevent overfitting" is asserted, not shown — add the train/val curve or
  early-stopping criterion that justifies it.
- **`.pth` is gitignored** (good) but that means a fresh clone serves XGBoost until `make train` runs;
  document the expected first-run model state.

---

## Recommended action checklist (prioritized)

**P0 — before treating any PR #2 result as real**
- [ ] C1: default `ALT_DATA_ENABLED=False`; relabel the seeder as SYNTHETIC; drop "alt improves AUC" claim.
- [ ] C4 + C5: reconcile docs; mark in-sample backtests as non-predictive; fix the 0.15/0.23 contradiction.

**P1 — before relying on the short-term signal**
- [ ] C2: re-select the threshold with nested/forward selection; report only forward results.
- [ ] C6: calibrate the threshold (and probabilities) against the *served* model; make served model explicit.
- [ ] Re-run walk-forward and record the honest, non-overfit edge as the single source of truth.

**P2 — to make hedging / alt-data actually count**
- [ ] C1: wire a real disclosures source (Quiver/EDGAR Form 4) into the existing point-in-time feature code.
- [ ] C3: implement hedging in the live executor (or clearly scope it research-only); de-globalize state.
- [ ] Portfolio-level walk-forward equity curve with capital/overlap/short constraints (still open).

---

## Decisions for you

1. **Alt data:** OK to flip `ALT_DATA_ENABLED=False` and treat Stage 19 as "pipeline ready, awaiting real
   data"? (Recommended.) Or do you want me to wire a real free source (SEC EDGAR Form 4 is free; STOCK Act
   via Quiver may need a key) now?
2. **Hedging:** keep as backtest-only research (label it so) or invest in making it live-deployable?
3. **Merge strategy:** PR #2 contains all of PR #1 plus Stage 18/19, so merging #2 supersedes #1 — do you
   want to close #1, or rebase #2 onto #1's branch to keep history linear?

*Honest summary: this PR meaningfully improves the machinery (solver, stationarity, evaluation, a correct
disclosure-feature pipeline), but its three headline wins — alt-data lift, hedged drawdown reduction, and
the calibrated threshold — are respectively noise, non-deployable, and overfit. Fix the framing + defaults,
keep the engineering.*

---

## Update — fixes applied (follow-up session)

Decisions taken with the user and implemented on branch `strategy-optimization`:

- **C1 (synthetic alt-data) — DONE.** `ALT_DATA_ENABLED` now defaults to **`False`** (`config.py`);
  `run.py fetch` only seeds disclosures when explicitly enabled; the seeder prints a loud "SYNTHETIC / NOT
  REAL" banner. Feature columns remain (inert / all-zero) so the 40-feature set is unchanged and models
  stay dimensionally consistent. **Models retrained with alt off** so the served model is not trained on
  noise. The disclosure *feature pipeline* (point-in-time correct, unit-tested) is kept, ready for a real
  source (SEC EDGAR Form 4 / Quiver) — that's the remaining P2.
- **C3 (hedging) — made deployable + advisory.** Hedge logic factored into `execution/hedging.py` (shared by
  backtest, API, executor). `/api/suggestions?hedge_mode=…` now returns a per-BUY **hedge leg + an explicit
  `action_plan`** (exact long & short shares/prices). The dashboard has a **Hedge overlay toggle**
  (None / Beta-Neutral / Pair Trade) and renders the trade plan under each BUY — so the user can execute
  manually (e.g. Robinhood). The executor places the hedge short on **real Alpaca only** (guarded; the
  virtual broker can't short, logs a skip). *Still open:* margin/exposure accounting and a live short-capable
  paper path; the in-sample hedged drawdown numbers remain in-sample (see C4).

- **C6 (served model vs threshold) — DONE.** `SERVED_MODEL` is now explicit (default `xgboost`; `pytorch`
  opt-in) so inference no longer silently serves "whatever `.pth` exists" (also closes C14). The BUY
  threshold is **calibrated to the served model**: `calibrate_threshold()` (`make calibrate`, auto-run by
  `make train`) trains the served model on a time-ordered 80%, then sets the cutoff to hit a fixed target
  selectivity (`SHORT_TERM_SIGNAL_RATE`, default top 0.5%) on the held-out 20% — a *selectivity prior*, not
  a returns-maximizing search, so it does **not** overfit the metric (unlike the old 0.23). It writes
  `saved_models/threshold.json` (`{threshold, oos_signals, oos_win_rate, oos_mean_net_ret, …}`) which
  inference + backtest read. First calibration: threshold **0.1335**, 481 OOS signals, win 9.1% (base 5.5%),
  **+0.16%/trade net** — modestly profitable at that selectivity, and a real number for the *served* model
  (the old 0.23 produced 0 signals on it).

- **C4/C5 (misleading docs) — DONE.** `current-state-and-gaps.md` §1b rewritten to one honest truth (fresh
  walk-forward: pooled AUC 0.698, edge only in the top ~0.1%, per-fold top-5% net negative in 4/5 folds),
  stale "+0.27%/0.15" prose removed, in-sample backtests explicitly flagged non-predictive, and the
  served-model/threshold sections (`ml-and-strategy.md`) corrected.
- **C1 real data — insider DONE & VALIDATED (negative result).** `alternative_fetcher.py` now ingests
  **real SEC EDGAR Form 4** insider transactions (free, no key): ticker→CIK via `company_tickers.json`,
  Form 4s from the submissions API, raw XML parsed (`parse_form4_xml`, unit-tested) for open-market P/S,
  keyed on the **filing date** (point-in-time). `make insider` / `run.py fetch` (when `ALT_DATA_ENABLED`).
  **Full 3-yr fetch (5,809 real transactions across 21 tickers) + walk-forward verdict: real insider
  features do NOT help the short-term model** — pooled OOS AUC 0.699 (with) vs 0.698 (without) = noise, and
  top-percentile net returns are statistically identical. **Why:** open-market *purchases* are extremely
  rare (only **152 of 5,809** transactions; most are sales) → the `insider_buying_ratio` feature is ~0 on
  99.9% of the 297k hourly bars, far too sparse to move the model. Insider *buying* is a known *weeks-to-
  months* signal, not an hourly-breakout one — so it's a candidate for the **long-term** book, not this
  short-term model. **`ALT_DATA_ENABLED` stays `False`.** (Foreign issuers NOK/ASML correctly returned 0
  Form 4s; TSM's 102 "purchases" look like a foreign-issuer data-quality artifact to scrub later.)
  Congress/STOCK Act remains synthetic (no free structured source wired).

- **Insider at the long-term horizon — deepened + conviction features → modest real signal at 3 months.**
  Deepened the data to **5 years / ~9.9k transactions** (16 US issuers; foreign issuers TSM/ASML/NOK/ARM/BB
  excluded as Section-16-exempt with unreliable filings), and replaced the sparse raw-purchase ratio with
  **conviction features** (`insider_net_flow`, `insider_net_buyers`, `insider_officer_buy`,
  `insider_buy_count`, `insider_cluster`) that use *all* transactions (net buy/sell pressure, officer buys,
  distinct buyers) — non-zero on ~80–90% of bars vs ~0 before. Re-tested via `longterm_alpha.py`: **21-day
  no help** (AUC 0.485 vs 0.502); **63-day (3-month) modest, consistent positive** — pooled AUC 0.517 vs
  0.507, top-10/20% picks-with-insider beat without (+16.8% vs +16.0%; +17.4% vs +14.8%), **4/5 folds favor
  insider**. First genuinely real-looking alt-data result, consistent with insider buying being a quarterly
  signal. Still research-grade (small effect, overlapping windows, one bad fold) → `ALT_DATA_ENABLED` stays
  `False`; the natural next step is a **quarterly long-term MPT allocation tilt** by the insider score, not
  enabling it in the short-term model.

- **Insider MPT tilt — implemented & backtested, it works (buy-side only).** `calculate_optimal_weights`
  now accepts an `expected_return_tilt` (Black-Litterman-style view added to expected returns), and
  `longterm_alpha.backtest_longterm_tilt` (`make longterm-tilt`) A/B-tests a monthly-rebalanced MPT book with
  vs without an insider tilt. **A net-flow tilt hurts** (insiders sell winners → underweights momentum), but
  a **buy-side** tilt (officer buys / buy count / clusters) helps: Sharpe **1.45→1.53**, return 572%→634%,
  drawdown −36%→−32%, peaking at strength ~0.1–0.2 and robust to start date. Single-regime / survivorship
  caveats apply. **Now wired into the live `/api/suggestions` allocator** (`LONGTERM_TILT_STRENGTH=0.15`,
  buy-side, dormant until `ALT_DATA_ENABLED`); each allocation returns an `insider_tilt_score`.

- **C2 (nested threshold) — DONE.** `find_optimal_threshold` selects each fold's BUY cutoff on the *train*
  fold (F1-optimized) and applies it to the unseen test fold — no test-set leakage.
- **Portfolio-level equity curve — DONE, and it's the headline.** `simulate_portfolio_chronological` (in the
  `walkforward` run) simulates the short-term strategy as a real capital-constrained book (max 10%/trade,
  ≤10 open, fees). Verdict: **−27% to −40% total return, negative Sharpe** over 2023→2026 — the strategy
  **loses money** despite a faint per-trade tail edge. This supersedes the earlier "small real edge" read.

Still outstanding: a short-term signal with **real portfolio-level edge** (the current one is net-negative),
a real **Congress/STOCK Act** source, fixing the PyBroker backtest Sharpe (G13), and the **fictional `SPACE`
ticker** (synthetic GE-proxy prices — flag or remove before real use; gaps doc G15).
