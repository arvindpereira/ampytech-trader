# Current State, Known Gaps & Honest Assessment

The most important doc: **what's trustworthy, what's weak, and what to believe about the numbers.** It
supersedes the earlier "mostly mock / mixed-resolution data" assessment — those data-quality issues are
fixed. The honest concern now is subtler: the strategies *work*, but mostly in bull markets.

## What's real and working

- **End-to-end pipeline**: ingestion → features → models → suggestions → Alpaca **Paper/Live** execution → scheduler (daily + intraday + weekly retrain) → UI. All live.
- **Real data, point-in-time**: prices (hourly ~5y, daily 1998+), macro, and **real LLM-scored news** (~226k headlines, dense from 2021). Features are *T−1*-only; news shifted +1 day. No mixed-resolution leakage; sentiment has a real-vs-mock flag.
- **Risk × Quality Grid Classification**: Automated tiering (`make classify`) blending quant quality (financial statement ratios) and qualitative LLM overlays, supporting manual DB overrides (`tier_override`).
- **Two-Model Swing Setup**: Core model (trained on Hot, Solid, and Unrated tickers) and Aggressive model (trained on all tickers, used to generate signals for speculative Long-shot tickers).
- **High-Risk Speculative Sleeve**: Small capital bucket (capped at 5% total equity) dedicated to speculative names under aggressive swing suggestions.
- **Volatility Sizing**: Per-ticker volatility target scaling (`min(1.0, 0.35 / name_vol)`) to dynamically reduce size for high-beta tickers.
- **Honest, look-ahead-free evaluation** (`make swing-eval`, Model Evaluation tab) with a movable `oos_start`, stress windows, and explicit caveats.
- **Per-stock strategy suggester** that is **self-validated** (following it lifted blended OOS Sharpe), plus a **regime overlay** that auto-shrinks swing in defensive regimes.
- **Crash Radar & Defensive Strategist**: Real-time Composite Crash-Risk Index (OOS, Winsorized, percentile and z-score normalized), Posture State Machine stances (Normal, Froth, De-Risk, Protect, Deploy, Recover), defensive playbooks (Buffett, Dalio, Taleb, Stagflation/Deflation asset branches), coherent (monotone-projected) experimental drawdown odds, a **policy-comparison Scenario Wargame** (Buy & Hold → static → glide-path/custom across Dot-Com/GFC/COVID/2022 + synthetic crashes) with an **OpenAI wargame analyst**, and a **preview-then-confirm** gated rebalance (read-only plan with real tickers/prices before any order). Crash artifacts refresh on a **data-change fingerprint** (not blindly on a clock); the wargame comparison + analyst are cached to disk and surface "last run / next auto-update / stale" indicators in the UI.
- **Capital buckets + soft caps**: execution never exceeds the user's per-strategy limits.
- **Data safety**: commit-stamped Google-Drive backups (DB + a files zip of models and **all cached JSON** — wargame/forecast caches included; OAuth token excluded) with verified restore.
- **Research Analyst (Research tab)**: Interactive AI-research query interface utilizing token-based intent routing, SQLite structured context retrieval (snapshots, news, estimates), pluggable LLM tiers (standard `gpt-4o-mini`, premium reasoners, local Ollama, or SQLite lookup), auto-inserted source citation anchors, and static wiki publishing site.
- **Sector Exposure Simulator & Heatmap** (embedded in External Portfolio tab): GICS-aligned sector/industry classifications (`research_sectors.json`), consolidated weight calculations merging trading account holdings (Alpaca Paper/Live API) and external broker records (`EquityLot`), comparison vs. S&P 500 GICS baseline (`sp500_sector_weights.json`), active tilt heatmap visualization, and automated allocation drift alerts exceeding 5 percentage points.

## The honest verdict on the strategies

1. **Swing + News is a bull-market amplifier, not an all-weather edge.** Out-of-sample *including the 2022 bear*, its Sharpe is ~0.70 ( An honest look at the two-model setup shows that segmenting high-risk speculative names into a separate 5% sleeve helps buffer core drawdown, but the core strategy remains highly dependent on general market direction).
2. **Long-term MPT** was genuinely bear-resilient (2022 +7%), but its **absolute backtest returns are survivorship-inflated** — the universe is today's surviving winners. Trust the *relative/risk* behavior, not the headline returns.
3. **The blended, MPT-leaning book is the defensible stance** — and what the suggester + default config now steer toward.
4. **The legacy hourly short-term model is net-negative** (the threshold calibrator says "no edge"). Kept for comparison; not executed by default.

See [strategy-evaluation-findings.md](./strategy-evaluation-findings.md) for the tables.

## Known caveats / limitations

- **Survivorship bias** in every backtest (a fixed current universe applied to the past) — worst pre-2020, real even in 2022. Pre-2020 stress numbers are flattered.
- **Single broad regime** of dense data (2021–2026): one real bear (2022). Small stress sample.
- **MPT absolute returns are not a forward estimate.**
- **HMM regime classifier** drives the overlay/scaling but hasn't been stress-validated as rigorously as the swing/MPT eval — treat the overlay as a sensible guardrail, not a precise bear-timer.
- **Experimental Drawdown Odds**: High regularized forecasting model is trained on only 3-4 historical bear episodes since 1998; outputs are strictly illustrative/experimental.
- **Scenario Wargame**: policy comparison replays assume the historical crisis envelopes (2000, 2008, 2020, 2022) and the synthetic crash shapes represent plausible future downside boundaries; the AI analyst summarizes those results and is not an independent forecast.
- **Ollama dependency**: swing news scoring stalls if Ollama is down (degrades gracefully to stale news or falls back to OpenAI if key is present).
- **GitHub LFS**: ~1.1 GB of historical DB snapshots remain (capped; would need repo recreation to reclaim). The DB is no longer tracked.
- **Fictional `SPACE` ticker** is synthetic (GE-proxy) — not a tradeable signal.
- **Finnhub Pro Transcript Dependency**: Loading earnings call transcripts requires a Finnhub Professional+ API token. If missing or returning 403, the research kb skips call texts and falls back to consensus estimates, reported EPS surprises, and headlines.
- **Benzinga Ratings Dependency**: Fetching consensus analyst ratings via Massive depends on Benzinga API developer credentials. If benzinga is unavailable, the fetcher caches a local price-only row as a fallback.

## Roadmap (optional, evidence-led)

- Pressure-test the regime overlay across a real regime shift; tune `REGIME_SWING_FACTORS`.
- Per-ticker / regime-conditional suggester refresh (v3: weekly auto-refresh with change alerts).
- Trade-attribution on swing (is the edge concentrated in a few names/episodes?).
- Curated X/social accounts as a secondary swing signal (deferred).
- Broaden the universe carefully to reduce survivorship bias in future evaluations.

## Bottom line

This is a working, honestly-evaluated personal trading bot — **not a proven alpha machine**. Its
realistic bar is *market-like-or-modestly-better returns with controllable drawdowns and limits you
set*, leaning on MPT for resilience and swing as a smaller, regime-gated bull sleeve. Size real capital
against the **drawdowns**, not the bull-market CAGRs.
