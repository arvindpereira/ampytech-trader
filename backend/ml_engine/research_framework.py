"""Documented, deterministic research scoring — see docs/research-methodology.md."""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional, Sequence

METHODOLOGY_VERSION = "2026.06-v1"

# Multi-factor stock composite (sum = 1.0). Rationale in docs/research-methodology.md.
STOCK_FACTOR_WEIGHTS = {
    "quality": 0.30,
    "upside": 0.25,
    "news": 0.25,
    "momentum": 0.20,
}

TIER_SCORES = {
    "quality_growth": 0.9,
    "core": 0.75,
    "speculative": 0.35,
    "value_trap": 0.15,
}

# GICS-aligned sector → SPDR sector ETF proxy (subset of our universe)
GICS_SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Defensive": "XLP",
    "Communication Services": "XLC",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _val(facts: Dict[str, Any], key: str, default: float = 0.0) -> float:
    f = facts.get(key) or {}
    if not isinstance(f, dict):
        return default
    if f.get("coverage") == "missing":
        return default
    return _safe_float(f.get("value"), default)


def stock_component_scores(facts: Dict[str, Any]) -> Dict[str, float]:
    """Normalized factor components in [0, 1] for one ticker snapshot."""
    tier = _val(facts, "tier", 0.5)
    tier_s = TIER_SCORES.get(str(tier) if tier else "", 0.5)
    quality = _val(facts, "quality", tier_s)
    upside = _val(facts, "upside_pct", 0.0)
    upside_n = max(-0.5, min(1.0, upside))
    news = _val(facts, "news_score_30d", 0.0)
    news_n = (news + 1) / 2
    mom = (_val(facts, "momentum_3m", 0.0) + 0.5) / 1.0
    mom_n = max(0.0, min(1.0, mom))
    return {
        "quality": quality if quality else tier_s,
        "upside": upside_n,
        "news": news_n,
        "momentum": mom_n,
    }


def composite_stock_score(facts: Dict[str, Any]) -> float:
    comp = stock_component_scores(facts)
    return sum(STOCK_FACTOR_WEIGHTS[k] * comp[k] for k in STOCK_FACTOR_WEIGHTS)


def _robust_median(vals: Sequence[float]) -> Optional[float]:
    clean = [float(v) for v in vals if v is not None]
    return median(clean) if clean else None


def _breadth_positive(vals: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return round(sum(1 for v in clean if v > 0) / len(clean), 3)


def aggregate_sector_metrics(
    snaps: list,
    *,
    market_caps: Optional[Dict[str, float]] = None,
    spy_momentum_3m: Optional[float] = None,
) -> Dict[str, Any]:
    """Equal-weighted median sector aggregates + breadth + optional cap-weight upside."""
    if not snaps:
        return {"ticker_count": 0, "methodology_version": METHODOLOGY_VERSION}

    upside = [s.upside_pct for s in snaps if s.upside_pct is not None]
    mom = [s.momentum_3m for s in snaps if s.momentum_3m is not None]
    news = [s.news_score_30d for s in snaps if s.news_score_30d is not None]
    qual = [s.quality for s in snaps if s.quality is not None]

    med_mom = _robust_median(mom)
    rel_strength = None
    if med_mom is not None and spy_momentum_3m is not None:
        rel_strength = round(med_mom - spy_momentum_3m, 4)

    cap_weighted_upside = None
    if market_caps:
        num, den = 0.0, 0.0
        for s in snaps:
            cap = market_caps.get(s.ticker)
            if cap and s.upside_pct is not None:
                num += cap * s.upside_pct
                den += cap
        if den > 0:
            cap_weighted_upside = round(num / den, 4)

    sector = snaps[0].sector if snaps else ""
    return {
        "sector": sector,
        "ticker_count": len(snaps),
        "aggregation": "equal_weight_median",
        "methodology_version": METHODOLOGY_VERSION,
        "median_upside_pct": _robust_median(upside),
        "median_momentum_3m": med_mom,
        "median_news_score_30d": _robust_median(news),
        "median_quality": _robust_median(qual),
        "breadth_upside_positive": _breadth_positive(upside),
        "breadth_momentum_positive": _breadth_positive(mom),
        "cap_weighted_upside_pct": cap_weighted_upside,
        "rel_strength_vs_spy": rel_strength,
        "etf_proxy": GICS_SECTOR_ETF.get(sector or ""),
        "constituents": [s.ticker for s in snaps],
    }


def compute_internal_target(
    consensus_target: float,
    num_analysts: Optional[int],
    momentum_3m: Optional[float],
    price: Optional[float],
) -> Dict[str, Any]:
    """12m internal target blend — documented in research-methodology.md."""
    target = float(consensus_target)
    method = "consensus_anchor"
    confidence = 0.55
    if num_analysts and num_analysts >= 5:
        confidence = 0.70
    if momentum_3m is not None and price:
        target = target * (1 + 0.05 * momentum_3m)
        method = "consensus_plus_momentum_tilt"
        confidence = min(0.85, confidence + 0.05)
    return {
        "target_price": round(target, 2),
        "method": method,
        "confidence": round(confidence, 2),
        "notes": f"Consensus anchor {consensus_target}; methodology {METHODOLOGY_VERSION}",
    }
