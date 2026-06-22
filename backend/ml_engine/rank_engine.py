"""Deterministic theme ranking from company snapshot facts."""
from typing import Any, Dict, List

from ml_engine.research_dossier import coverage_pct

WEIGHTS = {
    "quality": 0.30,
    "upside": 0.25,
    "news": 0.25,
    "momentum": 0.20,
}


def _val(facts: Dict[str, Any], key: str, default=0.0) -> float:
    f = facts.get(key) or {}
    if not isinstance(f, dict):
        return default
    if f.get("coverage") == "missing":
        return default
    v = f.get("value")
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _tier_score(tier: str) -> float:
    return {
        "quality_growth": 0.9,
        "core": 0.75,
        "speculative": 0.35,
        "value_trap": 0.15,
    }.get(tier or "", 0.5)


def _component_scores(facts: Dict[str, Any]) -> Dict[str, float]:
    tier = _val(facts, "tier", 0.5)
    tier_s = _tier_score(str(tier) if tier else "")
    quality = _val(facts, "quality", tier_s)
    upside = _val(facts, "upside_pct", 0.0)
    upside_n = max(-0.5, min(1.0, upside))  # cap extreme upside
    news = _val(facts, "news_score_30d", 0.0)
    news_n = (news + 1) / 2  # [-1,1] -> [0,1]
    mom = (_val(facts, "momentum_3m", 0.0) + 0.5) / 1.0
    mom_n = max(0.0, min(1.0, mom))
    return {
        "quality": quality if quality else tier_s,
        "upside": upside_n,
        "news": news_n,
        "momentum": mom_n,
    }


def rank_tickers(facts_by_ticker: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not facts_by_ticker:
        return []
    raw = {}
    for ticker, facts in facts_by_ticker.items():
        comp = _component_scores(facts)
        score = sum(WEIGHTS[k] * comp[k] for k in WEIGHTS)
        cov = coverage_pct(facts)
        raw[ticker] = {"score": score, "breakdown": comp, "coverage_pct": cov}

    scores = [v["score"] for v in raw.values()]
    lo, hi = min(scores), max(scores)
    span = hi - lo if hi > lo else 1.0

    ranked = []
    for ticker, meta in raw.items():
        norm = (meta["score"] - lo) / span
        ranked.append({
            "ticker": ticker,
            "score": round(norm, 4),
            "score_breakdown": {k: round(v, 3) for k, v in meta["breakdown"].items()},
            "coverage_pct": meta["coverage_pct"],
        })
    ranked.sort(key=lambda x: (-x["score"], x["ticker"]))
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
    return ranked
