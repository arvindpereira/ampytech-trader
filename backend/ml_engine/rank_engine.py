"""Deterministic theme ranking from company snapshot facts."""
from typing import Any, Dict, List

from ml_engine.research_dossier import coverage_pct
from ml_engine.research_framework import STOCK_FACTOR_WEIGHTS, composite_stock_score, get_stock_factor_weights, stock_component_scores


def rank_tickers(facts_by_ticker: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not facts_by_ticker:
        return []
    raw = {}
    for ticker, facts in facts_by_ticker.items():
        comp = stock_component_scores(facts)
        score = composite_stock_score(facts)
        cov = coverage_pct(facts)
        raw[ticker] = {"score": score, "breakdown": comp, "coverage_pct": cov}

    scores = [v["score"] for v in raw.values()]
    lo, hi = min(scores), max(scores)
    span = hi - lo if hi > lo else 1.0
    fw = get_stock_factor_weights()

    ranked = []
    for ticker, meta in raw.items():
        norm = (meta["score"] - lo) / span
        ranked.append({
            "ticker": ticker,
            "score": round(norm, 4),
            "score_breakdown": {k: round(v, 3) for k, v in meta["breakdown"].items()},
            "factor_weights": dict(fw),
            "coverage_pct": meta["coverage_pct"],
        })
    ranked.sort(key=lambda x: (-x["score"], x["ticker"]))
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
    return ranked
