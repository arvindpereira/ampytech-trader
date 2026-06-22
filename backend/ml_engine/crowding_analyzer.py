"""Portfolio crowding / concentration risk (Phase 3)."""
from __future__ import annotations

from statistics import mean
from typing import Dict, List, Tuple

from ml_engine.context_expander import portfolio_tickers
from ml_engine.intent_router import RoutedQuery
from ml_engine.rank_engine import rank_tickers
from ml_engine.research_dossier import get_many
from ml_engine.research_framework import METHODOLOGY_VERSION, stock_component_scores


def _tier_label(facts: dict) -> str:
    t = facts.get("tier") or {}
    return str(t.get("value") or "")


def analyze_crowding(routed: RoutedQuery, db) -> Tuple[List[str], Dict]:
    """Heuristic crowding score from portfolio concentration + factor heat."""
    holdings = portfolio_tickers(db)
    if not holdings:
        holdings = list(routed.tickers or [])[:12]
    if not holdings:
        return [], {"error": "no_holdings", "methodology_version": METHODOLOGY_VERSION}

    facts = get_many(holdings, db=db)
    speculative = [t for t, f in facts.items() if _tier_label(f) == "speculative"]
    spec_pct = len(speculative) / max(len(holdings), 1)

    moms = [stock_component_scores(f)["momentum"] for f in facts.values()]
    news = [stock_component_scores(f)["news"] for f in facts.values()]
    avg_mom = mean(moms) if moms else 0.0
    avg_news = mean(news) if news else 0.0

    # Herfindahl on equal weights as concentration proxy (1/n = diversified)
    n = len(holdings)
    hhi = sum((1 / n) ** 2 for _ in holdings) if n else 1.0
    hhi_norm = min(1.0, hhi * n)  # 1.0 when single name

    # Crowding score 0–1: high spec %, high momentum heat, concentrated book
    heat_mom = max(0.0, min(1.0, (avg_mom + 0.2) / 0.6))
    heat_news = max(0.0, min(1.0, (avg_news + 0.5) / 1.0))
    crowding = round(0.35 * spec_pct + 0.25 * heat_mom + 0.20 * heat_news + 0.20 * hhi_norm, 3)

    ranked = rank_tickers(facts)
    ranked_heat = sorted(ranked, key=lambda r: r.get("score", 0), reverse=True)

    meta = {
        "holdings": holdings,
        "speculative_pct": round(spec_pct, 3),
        "speculative_tickers": speculative,
        "avg_momentum_3m": round(avg_mom, 4),
        "avg_news_score_30d": round(avg_news, 4),
        "concentration_hhi": round(hhi, 4),
        "crowding_score": crowding,
        "crowding_label": (
            "elevated" if crowding >= 0.55 else "moderate" if crowding >= 0.35 else "low"
        ),
        "methodology_version": METHODOLOGY_VERSION,
        "framework_note": (
            "Crowding heuristic blends speculative-tier weight, momentum/news heat, and "
            "equal-weight concentration — not a formal bubble indicator. "
            "See docs/research-methodology.md."
        ),
        "ranked_by_heat": ranked_heat[:10],
    }
    return holdings, meta
