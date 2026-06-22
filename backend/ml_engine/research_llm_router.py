"""Tiered LLM routing for research queries."""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from ml_engine.intent_router import RoutedQuery, is_stub_intent

INTENT_BASE = {
    "ticker_outlook": 0.2,
    "theme_rank": 0.5,
    "event_spillover": 0.55,
    "sector_screen": 0.7,
    "cross_theme": 0.85,
    "crowding_risk": 0.85,
}

_LOOKUP_PATTERNS = [
    re.compile(r"consensus\s+target", re.I),
    re.compile(r"price\s+target", re.I),
    re.compile(r"what(?:'s| is)\s+.+\s+target", re.I),
    re.compile(r"analyst\s+target", re.I),
    re.compile(r"current\s+price", re.I),
]


@dataclass
class RouteDecision:
    tier: str  # lookup | local | expert
    complexity: float
    reason: str
    use_search: bool = False


def complexity_score(routed: RoutedQuery, coverage_by_ticker: Dict[str, float]) -> float:
    base = INTENT_BASE.get(routed.intent, 0.5)
    score = base
    n = len(routed.tickers)
    if n > 3:
        score += 0.15
    if coverage_by_ticker:
        if min(coverage_by_ticker.values()) < 0.5:
            score += 0.15
    if routed.deep_research:
        score += 0.25
    return min(1.0, score)


def is_lookup_query(routed: RoutedQuery) -> bool:
    if routed.intent != "ticker_outlook" or len(routed.tickers) != 1:
        return False
    q = routed.raw_query
    return any(p.search(q) for p in _LOOKUP_PATTERNS)


def decide(routed: RoutedQuery, coverage_by_ticker: Optional[Dict[str, float]] = None) -> RouteDecision:
    coverage_by_ticker = coverage_by_ticker or {}
    if is_stub_intent(routed.intent):
        return RouteDecision("expert", 0.9, f"stub intent {routed.intent}", False)

    if is_lookup_query(routed):
        return RouteDecision("lookup", 0.1, "single-ticker fact lookup", False)

    c = complexity_score(routed, coverage_by_ticker)
    if c < 0.45 and routed.intent == "ticker_outlook" and len(routed.tickers) <= 2:
        return RouteDecision("local", c, "low complexity ticker outlook", False)
    if c >= 0.45 or routed.deep_research or routed.intent in (
        "cross_theme", "crowding_risk", "theme_rank", "event_spillover"
    ):
        use_search = routed.deep_research and bool(coverage_by_ticker) and min(coverage_by_ticker.values(), default=1) < 0.6
        return RouteDecision("expert", c, "complex or multi-ticker research", use_search)
    return RouteDecision("local", c, "default local tier", False)


def generation_info(route: RouteDecision) -> dict:
    """Human-readable provenance for how a report was generated."""
    from app.core.config import (
        OPENAI_API_KEY,
        OPENAI_EXPERT_MODEL,
        RESEARCH_EXPERT_MODEL,
        RESEARCH_LOCAL_MODEL,
    )

    if route.tier == "lookup":
        return {
            "agent": "Research Analyst",
            "tier": "lookup",
            "model": None,
            "provider": None,
            "note": (
                "Generated from the local knowledge base only (template lookup — no LLM). "
                "All figures are snapshot facts; there is no AI narrative."
            ),
        }

    if route.tier == "local":
        return {
            "agent": "Research Analyst (local)",
            "tier": "local",
            "model": RESEARCH_LOCAL_MODEL,
            "provider": "ollama",
            "note": (
                f"Narrative synthesized by local Ollama ({RESEARCH_LOCAL_MODEL}). "
                "Computed ranks and snapshot numbers are deterministic; prose quality is typically lower than expert tier."
            ),
        }

    if OPENAI_API_KEY:
        model = RESEARCH_EXPERT_MODEL or OPENAI_EXPERT_MODEL
        return {
            "agent": "Research Analyst (expert)",
            "tier": "expert",
            "model": model,
            "provider": "openai",
            "note": (
                f"Narrative synthesized by OpenAI ({model}, expert tier). "
                "Computed ranks and snapshot numbers are deterministic; only prose sections vary by model."
            ),
        }

    return {
        "agent": "Research Analyst (local fallback)",
        "tier": "expert",
        "model": RESEARCH_LOCAL_MODEL,
        "provider": "ollama",
        "note": (
            f"Expert tier selected but OPENAI_API_KEY is unset — narrative used Ollama ({RESEARCH_LOCAL_MODEL}) instead."
        ),
    }


def stamp_generation(report: dict, route: RouteDecision) -> dict:
    """Attach generation metadata and a plain-English note to the report JSON."""
    template = report.get("template")
    if template == "stub":
        info = {
            "agent": "Research Analyst (stub)",
            "tier": route.tier,
            "model": None,
            "provider": None,
            "note": (
                "This intent is not fully implemented yet — placeholder report only, no LLM synthesis. "
                f"Router tier was {route.tier}."
            ),
        }
    elif template == "lookup":
        info = generation_info(RouteDecision("lookup", route.complexity, route.reason))
    else:
        info = generation_info(route)

    report = dict(report)
    report["generation"] = info
    report["generation_note"] = info["note"]
    return report
