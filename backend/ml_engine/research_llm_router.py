"""Tiered LLM routing for research queries."""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from ml_engine.intent_router import RoutedQuery, is_stub_intent

INTENT_BASE = {
    "ticker_outlook": 0.2,
    "theme_rank": 0.5,
    "event_spillover": 0.55,
    "earnings_report": 0.75,
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

# Rough token budgets for pricing UI (input, output).
_SYNTHESIS_TOKEN_EST = {
    "ticker_outlook": (4500, 900),
    "theme_rank": (3500, 1100),
    "event_spillover": (4000, 1000),
    "earnings_report": (9000, 1400),
    "sector_screen": (5500, 1200),
    "cross_theme": (6000, 1400),
    "crowding_risk": (6000, 1400),
}
_PER_TICKER_INPUT = 1200


@dataclass
class RouteDecision:
    tier: str  # lookup | standard | premium | local
    complexity: float
    reason: str
    use_search: bool = False
    model: Optional[str] = None


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


def _openai_available() -> bool:
    from app.core.config import OPENAI_API_KEY
    return bool(OPENAI_API_KEY)


def model_for_tier(tier: str) -> Optional[str]:
    from app.core.config import (
        OPENAI_EXPERT_MODEL,
        OPENAI_MODEL,
        RESEARCH_LOCAL_MODEL,
        RESEARCH_PREMIUM_MODEL,
        RESEARCH_STANDARD_MODEL,
    )

    if tier == "lookup":
        return None
    if tier == "premium":
        return RESEARCH_PREMIUM_MODEL or OPENAI_EXPERT_MODEL
    if tier == "standard":
        return RESEARCH_STANDARD_MODEL or OPENAI_MODEL
    if tier == "local":
        return RESEARCH_LOCAL_MODEL
    return RESEARCH_STANDARD_MODEL or OPENAI_MODEL


def estimate_synthesis_tokens(intent: str, ticker_count: int) -> tuple:
    base_in, base_out = _SYNTHESIS_TOKEN_EST.get(intent, (5000, 1000))
    extra = max(0, ticker_count - 1) * _PER_TICKER_INPUT
    return base_in + extra, base_out


def estimate_cost_for_tier(tier: str, intent: str, ticker_count: int) -> dict:
    """USD estimate for UI before running synthesis."""
    from app.core.config import OPENAI_API_KEY, RESEARCH_LOCAL_MODEL, RESEARCH_PREMIUM_MODEL
    from app.core.llm_cost import estimate_cost

    if tier == "lookup":
        return {
            "tier": "lookup",
            "model": None,
            "est_cost_usd": 0.0,
            "openai_available": bool(OPENAI_API_KEY),
        }

    if not _openai_available():
        return {
            "tier": "local",
            "model": RESEARCH_LOCAL_MODEL,
            "est_cost_usd": 0.0,
            "openai_available": False,
        }

    model = model_for_tier(tier)
    pin, pout = estimate_synthesis_tokens(intent, ticker_count)
    cost = estimate_cost(model, pin, pout) if model else None
    return {
        "tier": tier,
        "model": model,
        "est_cost_usd": round(cost, 4) if cost is not None else None,
        "est_input_tokens": pin,
        "est_output_tokens": pout,
        "openai_available": True,
        "premium_model": RESEARCH_PREMIUM_MODEL,
    }


def decide(
    routed: RoutedQuery,
    coverage_by_ticker: Optional[Dict[str, float]] = None,
    *,
    use_premium: bool = False,
) -> RouteDecision:
    coverage_by_ticker = coverage_by_ticker or {}

    if is_stub_intent(routed.intent):
        tier = "premium" if use_premium and _openai_available() else "standard"
        if not _openai_available():
            tier = "local"
        return RouteDecision(
            tier, 0.9, f"stub intent {routed.intent}", False, model_for_tier(tier)
        )

    if is_lookup_query(routed):
        return RouteDecision("lookup", 0.1, "single-ticker fact lookup", False, None)

    c = complexity_score(routed, coverage_by_ticker)

    if use_premium and _openai_available():
        use_search = routed.deep_research and bool(coverage_by_ticker) and min(
            coverage_by_ticker.values(), default=1
        ) < 0.6
        return RouteDecision(
            "premium",
            c,
            "user requested premium model",
            use_search,
            model_for_tier("premium"),
        )

    if not _openai_available():
        return RouteDecision(
            "local", c, "OPENAI_API_KEY unset — Ollama fallback", False, model_for_tier("local")
        )

    # Premium requires explicit user opt-in (use_premium=True). No auto-escalation by intent or complexity.
    return RouteDecision(
        "standard",
        c,
        "standard synthesis (gpt-4o-mini)",
        bool(coverage_by_ticker) and min(coverage_by_ticker.values(), default=1) < 0.5,
        model_for_tier("standard"),
    )


def generation_info(route: RouteDecision) -> dict:
    """Human-readable provenance for how a report was generated."""
    from app.core.config import OPENAI_API_KEY, RESEARCH_LOCAL_MODEL, RESEARCH_PREMIUM_MODEL, RESEARCH_STANDARD_MODEL

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
                "Set OPENAI_API_KEY for higher-quality GPT-4o-mini synthesis."
            ),
        }

    if route.tier == "standard":
        return {
            "agent": "Research Analyst",
            "tier": "standard",
            "model": route.model or RESEARCH_STANDARD_MODEL,
            "provider": "openai",
            "note": (
                f"Narrative synthesized by OpenAI ({route.model or RESEARCH_STANDARD_MODEL}). "
                "Computed ranks and snapshot numbers are deterministic. "
                "Use Premium AI for a deeper pass with {premium}.".format(premium=RESEARCH_PREMIUM_MODEL)
            ),
        }

    if route.tier == "premium" and OPENAI_API_KEY:
        model = route.model or RESEARCH_PREMIUM_MODEL
        return {
            "agent": "Research Analyst (Premium)",
            "tier": "premium",
            "model": model,
            "provider": "openai",
            "note": (
                f"Narrative synthesized by OpenAI premium model ({model}). "
                "Computed ranks and snapshot numbers are deterministic; prose uses the strongest tier."
            ),
        }

    return {
        "agent": "Research Analyst (local fallback)",
        "tier": route.tier,
        "model": RESEARCH_LOCAL_MODEL,
        "provider": "ollama",
        "note": (
            f"Premium tier selected but OPENAI_API_KEY is unset — narrative used Ollama ({RESEARCH_LOCAL_MODEL}) instead."
        ),
    }


def upgrade_offer(route: RouteDecision, intent: str, ticker_count: int) -> dict:
    """Metadata for the 'Use Premium AI' button after a standard report."""
    if route.tier in ("lookup", "premium", "local"):
        return {"available": False}
    est = estimate_cost_for_tier("premium", intent, ticker_count)
    return {
        "available": est.get("openai_available", False),
        "premium_model": est.get("premium_model") or est.get("model"),
        "est_cost_usd": est.get("est_cost_usd"),
        "est_input_tokens": est.get("est_input_tokens"),
        "est_output_tokens": est.get("est_output_tokens"),
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
