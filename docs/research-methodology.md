# Research Analyst — Methodology

This document defines how the Research Analyst knowledge base (RKB) is structured,
how scores are computed, and what financial conventions we follow. **LLMs narrate
only** — every number in a report traceable to a snapshot field or `item:N` citation.

## Design principles

1. **Materialize once, read many** — `company_snapshots` / `sector_snapshots` refreshed daily.
2. **Deterministic ranks** — composite scores are code, not model output.
3. **Provenance** — each fact carries `source`, `as_of`, `coverage`.
4. **Conservative defaults** — medians over means, caps on extremes, explicit caveats.

## Taxonomy — GICS-aligned sectors

Sector labels come from `ticker_metadata.sector` (Yahoo/Polygon), which follows
**GICS-style** sector names (e.g. `Technology`, `Financial Services`, `Healthcare`).

A structured **sector handbook** (`backend/data/research_sectors.json`) seeds all
11 GICS sectors with:

- Canonical sector name mapping (GICS label → our metadata sector key)
- SPDR ETF proxies (XLK, XLF, …) and index references
- Representative large-cap **seed tickers** per sector (from `perplexity_sectors.md`)
- Query **keywords** for intent detection (`sector_resolver.py`)

Live price, upside, and momentum always come from `company_snapshots` on refresh.
Seed tickers fill coverage gaps when the RKB has few names in a sector. Handbook
context is injected into sector-screen LLM synthesis as structural RAG (not live quotes).

Refresh seeds and portfolio classification:

```bash
make research-sectors-refresh      # Yahoo metadata + cap-ranked seeds + portfolio map
make research-sectors-refresh-fast # Re-rank from DB only (no Yahoo fetch)
```

`research_kb_refresh` also runs sector refresh at the end. Portfolio holdings include
`equity_lots`, `virtual_positions`, and `external_statement_holdings`.

API: `GET /api/research/sectors`, `GET /api/research/portfolio/sectors`

Sector ETF proxies in our universe (standard SPDR sector funds):

| GICS sector (approx.) | ETF proxy |
|----------------------|-----------|
| Technology | XLK |
| Financial Services | XLF |
| Energy | XLE |
| Healthcare | XLV |
| Consumer Defensive | XLP |

Screens aggregate **constituents in the RKB universe** with that sector label —
not full index membership. Coverage gaps are surfaced in `coverage_pct` and caveats.

## Stock-level composite score

Used by `rank_engine` for theme ranks, sector constituent ordering, and spillover tables.

Multi-factor blend inspired by practitioner **quality + value + momentum + sentiment**
frameworks (cf. Asness et al. quality factor; Jegadeesh-Titman momentum; sell-side
relative value via consensus upside):

| Factor | Weight | Snapshot field | Rationale |
|--------|--------|----------------|-----------|
| Quality | 30% | `quality` + `tier` | Profitability/stability overlay from `ticker_classification` |
| Value / upside | 25% | `upside_pct` | `(consensus_target − price) / price` — standard sell-side relative value |
| Sentiment | 25% | `news_score_30d` | LLM-scored headline direction × relevance (30d window) |
| Momentum | 20% | `momentum_3m` | 3-month price return — cross-sectional momentum signal |

Components are normalized to [0, 1] per ticker; final scores are **min-max normalized
within the peer set** for ranking (not absolute forecasts).

Implementation: `backend/ml_engine/research_framework.py` → `stock_component_scores()`.

### Walk-forward calibration (optional)

Default weights above are practitioner priors. Run:

```bash
make research-calibrate-factors
```

This executes `ml_engine/factor_calibrator.py`, which builds a panel from historical
`company_snapshots`, computes 63-day forward returns from `daily_prices`, and
walk-forward grid-searches weights to maximize Spearman rank IC vs forward return.
Results are written to `backend/data/research_factor_weights.json`.

`get_stock_factor_weights()` loads calibrated weights when present; otherwise defaults
apply. Requires daily `make research-kb-refresh` history.

## Earnings depth (transcripts & revisions)

Ingested via Finnhub (`FINNHUB_API_KEY`):

| Endpoint | Table | Use |
|----------|-------|-----|
| `/stock/eps-estimate` | `earnings_estimate_snapshots` | EPS consensus snapshots → `eps_revision_30d` |
| `/stock/earnings` | `earnings_surprises` | Reported vs estimated EPS |
| `/stock/transcripts` | `earnings_transcripts` | Call text + `external_analyst_items` citations |

Transcripts require **Finnhub Professional+**; free tier returns 403 and analysis
falls back to estimates, surprises, and news. Use the `earnings_report` intent for
transcript-grounded synthesis.

## Sector snapshots

Sector-level metrics aggregate **latest** `company_snapshots` per constituent.

### Aggregation method: equal-weighted medians

Industry screens commonly use **median** statistics to reduce mega-cap distortion
when cap weights are unavailable or incomplete. We therefore publish:

- `median_upside_pct` — median consensus implied upside across constituents
- `median_momentum_3m` — median 3m return
- `median_news_score_30d` — median sentiment
- `median_quality` — median classification quality score
- `breadth_upside_positive` — % of constituents with upside > 0 (earnings breadth proxy)
- `breadth_momentum_positive` — % with positive 3m momentum
- `rel_strength_vs_spy` — sector median momentum minus SPY median momentum

When `ticker_metadata.market_cap` is available, we also compute **cap-weighted mean
upside** as a secondary line (`cap_weighted_upside_pct`) for comparison.

Sector screen ranking uses the user framing:

- **Undervalued** → sort by `median_upside_pct` descending
- **Overvalued** → sort ascending (low/negative upside)
- **Momentum** → sort by `median_momentum_3m`

Implementation: `research_framework.aggregate_sector_metrics()` → persisted in
`sector_snapshots.facts_json` on `make research-kb-refresh`.

## Internal price targets (12m)

Separate from sell-side consensus (`target_mean` in snapshots). Blended view:

1. **Base** = consensus mean target (standard IB relative-value anchor).
2. **Momentum tilt** (optional) = multiply by `(1 + 0.05 × momentum_3m)` — small
   overlay consistent with 3–12 month momentum literature; capped implicitly by
   momentum normalization in snapshots.
3. **Confidence** rises with analyst count (≥5 analysts → 0.70 base) and when
   momentum adjustment applies.

Stored in `internal_price_targets`; exposed on snapshots as `internal_target_12m`.
**Not a price forecast** — a reproducible blend for narrative comparison vs consensus.

## Structured RAG (no vector DB)

Query-time context expansion uses **SQLite only**:

| Layer | Source |
|-------|--------|
| Entity resolution | Aliases + universe filter (`intent_router`) |
| Portfolio context | `EquityLot`, `VirtualPosition`, `universe_tickers` |
| Sector peers | GICS sector/industry match (`context_expander`) |
| Recent news | `news_llm_scores` 14d SQL pull |
| Query-relevant news | BM25 + optional Ollama hybrid (`news_retriever`) |
| Web fallback | Tavily/Brave when coverage < 50% (`web_search_fetcher`) |

This is **structured retrieval** — tables and deterministic joins — not learned embeddings
of the full sell-side corpus. Phase 2c adds BM25/semantic re-rank on headlines only.

## What we are not

- Not a live factor backtest in production — weights are calibrated offline when you run `make research-calibrate-factors`.
- Not full GICS index membership — limited to `TICKER_UNIVERSE` + watchlist + holdings.
- Not investment advice — snapshots can be stale; consensus can be wrong in regime shifts.
- Not a substitute for reading SEC filings — transcripts are Finnhub-sourced when available.

## Versioning

`research_framework.METHODOLOGY_VERSION` is stamped into `sector_snapshots.facts_json`
and report `methodology_version` when present. Bump when weights or aggregation change.
