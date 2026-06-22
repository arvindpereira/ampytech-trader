"""Structured report templates for Research Analyst — ensures consistent, high-quality output.

Templates define required sections and placeholder slots. Computed numbers are injected before any LLM
narrative pass; the LLM only fills labeled prose slots and must not alter numeric fields.
"""
from typing import Any, Dict, List, Optional


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    try:
        return f"{float(x) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_price(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _field_val(facts: Dict[str, Any], key: str):
    f = facts.get(key) or {}
    return f.get("value") if isinstance(f, dict) else None


def lookup_template(ticker: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    """Template-only answer for trivial snapshot lookups (no LLM)."""
    price = _field_val(facts, "price")
    target = _field_val(facts, "target_mean")
    upside = _field_val(facts, "upside_pct")
    num = _field_val(facts, "num_analysts")
    rec = _field_val(facts, "recommendation_key")
    tier = _field_val(facts, "tier")
    as_of = (facts.get("price") or {}).get("as_of", "n/a")

    tldr = (
        f"{ticker}: price {_fmt_price(price)}, consensus target {_fmt_price(target)} "
        f"({num or '?'} analysts, {rec or 'n/a'}), implied upside {_fmt_pct(upside)}. "
        f"Classification tier: {tier or 'unrated'}. Data as of {as_of}."
    )
    return {
        "template": "lookup",
        "tldr": tldr,
        "snapshot_facts": {
            "price": price,
            "target_mean": target,
            "upside_pct": upside,
            "num_analysts": num,
            "recommendation_key": rec,
            "tier": tier,
            "as_of": as_of,
        },
        "caveats": [
            "Consensus target is a point-in-time snapshot, not a dated forecast.",
            "Reply generated from local knowledge base without LLM.",
        ],
    }


def ticker_outlook_shell(ticker: str, facts: Dict[str, Any], synthesis: Optional[Dict] = None) -> Dict[str, Any]:
    """Pre-filled template structure for single-ticker outlook reports."""
    syn = synthesis or {}
    return {
        "template": "ticker_outlook",
        "ticker": ticker,
        "tldr": syn.get("tldr") or "",
        "snapshot_summary": {
            "price": _field_val(facts, "price"),
            "target_mean": _field_val(facts, "target_mean"),
            "target_high": _field_val(facts, "target_high"),
            "target_low": _field_val(facts, "target_low"),
            "upside_pct": _field_val(facts, "upside_pct"),
            "num_analysts": _field_val(facts, "num_analysts"),
            "recommendation_key": _field_val(facts, "recommendation_key"),
            "tier": _field_val(facts, "tier"),
            "quality": _field_val(facts, "quality"),
            "news_score_30d": _field_val(facts, "news_score_30d"),
            "momentum_3m": _field_val(facts, "momentum_3m"),
        },
        "consensus_view": syn.get("consensus_view") or {"text": "", "sources": []},
        "recent_actions": syn.get("recent_actions") or [],
        "news_sentiment_summary": syn.get("news_sentiment_summary") or {"text": "", "sources": []},
        "third_party_highlights": syn.get("third_party_highlights") or [],
        "outlook_narrative": syn.get("outlook_narrative") or "",
        "catalysts": syn.get("catalysts") or [],
        "risks": syn.get("risks") or [],
        "caveats": syn.get("caveats") or [
            "Not investment advice. Analyst data may be incomplete or delayed.",
        ],
    }


def theme_rank_shell(
    theme: str,
    ranked: List[Dict[str, Any]],
    synthesis: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Pre-filled template for theme ranking reports."""
    syn = synthesis or {}
    winners = [r for r in ranked if r.get("rank", 99) <= max(1, len(ranked) // 3)]
    losers = list(reversed(ranked[-max(1, len(ranked) // 3):])) if ranked else []
    return {
        "template": "theme_rank",
        "theme": theme,
        "tldr": syn.get("tldr") or "",
        "ranked_companies": ranked,
        "winners_summary": syn.get("winners_summary") or "",
        "losers_summary": syn.get("losers_summary") or "",
        "winners_tickers": [w["ticker"] for w in winners],
        "losers_tickers": [l["ticker"] for l in losers],
        "theme_narrative": syn.get("theme_narrative") or "",
        "catalysts": syn.get("catalysts") or [],
        "risks": syn.get("risks") or [],
        "caveats": syn.get("caveats") or [
            "Rank order is computed from local snapshot scores — not a guarantee of future performance.",
            "Theme membership is configurable and may omit relevant names.",
        ],
    }


def stub_shell(intent: str, message: str) -> Dict[str, Any]:
    return {
        "template": "stub",
        "intent": intent,
        "tldr": message,
        "caveats": ["This intent is planned for a future phase."],
    }


# LLM prompt scaffolding — JSON keys the model must return per template
TICKER_OUTLOOK_LLM_SCHEMA = {
    "tldr": "2-3 sentence bottom line",
    "outlook_narrative": "1-2 paragraphs on 12m outlook using ONLY provided facts",
    "consensus_view": {"text": "...", "sources": ["item:1", "snapshot:target_mean"]},
    "catalysts": ["..."],
    "risks": ["..."],
    "caveats": ["..."],
}

THEME_RANK_LLM_SCHEMA = {
    "tldr": "2-3 sentence bottom line",
    "theme_narrative": "paragraph on theme outlook",
    "winners_summary": "why top-ranked names lead",
    "losers_summary": "why bottom-ranked names lag",
    "catalysts": ["..."],
    "risks": ["..."],
    "caveats": ["..."],
}

EVENT_SPILLOVER_LLM_SCHEMA = {
    "tldr": "2-3 sentence bottom line on event read-through",
    "event_summary": "what happened or is expected for the primary ticker",
    "holdings_impact": [
        {"ticker": "NVDA", "impact": "likely positive/neutral/negative and why", "sources": ["snapshot:momentum_3m"]}
    ],
    "spillover_narrative": "1-2 paragraphs connecting primary event to related holdings",
    "catalysts": ["..."],
    "risks": ["..."],
    "caveats": ["..."],
}

SECTOR_SCREEN_LLM_SCHEMA = {
    "tldr": "2-3 sentence bottom line on sector opportunity/risk",
    "sector_narrative": "1-2 paragraphs on sector outlook using aggregate metrics and constituent ranks",
    "standouts": [{"ticker": "NVDA", "why": "brief reason", "sources": ["snapshot:upside_pct"]}],
    "laggards": [{"ticker": "INTC", "why": "brief reason", "sources": ["snapshot:momentum_3m"]}],
    "catalysts": ["..."],
    "risks": ["..."],
    "caveats": ["..."],
}

CROSS_THEME_LLM_SCHEMA = {
    "tldr": "2-3 sentence bottom line on cross-theme demand linkage",
    "linkage_narrative": "how themes interact, shared enablers, demand read-through",
    "overlap_analysis": "tickers bridging multiple themes and why they matter",
    "catalysts": ["..."],
    "risks": ["..."],
    "caveats": ["..."],
}

CROWDING_LLM_SCHEMA = {
    "tldr": "2-3 sentence crowding / concentration assessment",
    "crowding_narrative": "portfolio heat, speculative exposure, concentration",
    "watch_list": [{"ticker": "NVDA", "concern": "why it adds crowding risk", "sources": ["snapshot:tier"]}],
    "de_risk_ideas": ["..."],
    "catalysts": ["..."],
    "risks": ["..."],
    "caveats": ["..."],
}


def event_spillover_shell(
    primary: str,
    related: List[str],
    facts_by_ticker: Dict[str, Dict],
    synthesis: Optional[Dict] = None,
    expansion: Optional[Dict] = None,
) -> Dict[str, Any]:
    syn = synthesis or {}
    exp = expansion or {}
    holdings = []
    for t in related:
        facts = facts_by_ticker.get(t, {})
        holdings.append({
            "ticker": t,
            "price": _field_val(facts, "price"),
            "momentum_3m": _field_val(facts, "momentum_3m"),
            "news_score_30d": _field_val(facts, "news_score_30d"),
            "sector": _field_val(facts, "sector"),
            "impact": next(
                (h.get("impact") for h in syn.get("holdings_impact", []) if h.get("ticker") == t),
                "",
            ),
        })
    caveats = list(syn.get("caveats") or [])
    if exp.get("sector_peers"):
        caveats.append(
            f"Related holdings expanded from your portfolio ({', '.join(exp['sector_peers'][:8])})."
        )
    return {
        "template": "event_spillover",
        "primary_ticker": primary,
        "related_holdings": holdings,
        "tldr": syn.get("tldr") or "",
        "event_summary": syn.get("event_summary") or "",
        "spillover_narrative": syn.get("spillover_narrative") or "",
        "holdings_impact": syn.get("holdings_impact") or [],
        "catalysts": syn.get("catalysts") or [],
        "risks": syn.get("risks") or [],
        "caveats": caveats or [
            "Read-through analysis uses local snapshots and news — not a live earnings transcript.",
        ],
    }


def sector_screen_shell(
    sectors: List[str],
    sector_rankings: List[Dict[str, Any]],
    ranked_tickers: List[Dict[str, Any]],
    synthesis: Optional[Dict] = None,
    expansion: Optional[Dict] = None,
) -> Dict[str, Any]:
    syn = synthesis or {}
    exp = expansion or {}
    label = ", ".join(sectors) if sectors else "Multi-sector"
    return {
        "template": "sector_screen",
        "sectors": sectors,
        "sector_rankings": sector_rankings,
        "ranked_companies": ranked_tickers,
        "tldr": syn.get("tldr") or "",
        "sector_narrative": syn.get("sector_narrative") or "",
        "standouts": syn.get("standouts") or [],
        "laggards": syn.get("laggards") or [],
        "catalysts": syn.get("catalysts") or [],
        "risks": syn.get("risks") or [],
        "caveats": syn.get("caveats") or [
            f"Sector screen: {label}. Ranks computed from local snapshots — not a live screener.",
            "Web search snippets included when SEARCH_API_KEY is configured.",
        ],
        "screen_framing": exp.get("screen_framing"),
        "methodology_version": exp.get("methodology_version"),
    }


def cross_theme_shell(
    theme_labels: Dict[str, str],
    overlap: List[str],
    ranked: List[Dict[str, Any]],
    synthesis: Optional[Dict] = None,
    expansion: Optional[Dict] = None,
) -> Dict[str, Any]:
    syn = synthesis or {}
    exp = expansion or {}
    return {
        "template": "cross_theme",
        "themes": list(theme_labels.values()),
        "theme_ids": list(theme_labels.keys()),
        "overlap_tickers": overlap,
        "ranked_companies": ranked,
        "tldr": syn.get("tldr") or "",
        "linkage_narrative": syn.get("linkage_narrative") or "",
        "overlap_analysis": syn.get("overlap_analysis") or "",
        "catalysts": syn.get("catalysts") or [],
        "risks": syn.get("risks") or [],
        "caveats": syn.get("caveats") or [
            "Cross-theme linkage uses configured theme membership — not supply-chain IO tables.",
        ],
        "methodology_version": exp.get("methodology_version"),
        "framework_note": exp.get("framework_note"),
    }


def crowding_shell(
    meta: Dict[str, Any],
    synthesis: Optional[Dict] = None,
) -> Dict[str, Any]:
    syn = synthesis or {}
    return {
        "template": "crowding_risk",
        "crowding_score": meta.get("crowding_score"),
        "crowding_label": meta.get("crowding_label"),
        "speculative_pct": meta.get("speculative_pct"),
        "speculative_tickers": meta.get("speculative_tickers") or [],
        "holdings": meta.get("holdings") or [],
        "ranked_companies": meta.get("ranked_by_heat") or [],
        "tldr": syn.get("tldr") or "",
        "crowding_narrative": syn.get("crowding_narrative") or "",
        "watch_list": syn.get("watch_list") or [],
        "de_risk_ideas": syn.get("de_risk_ideas") or [],
        "catalysts": syn.get("catalysts") or [],
        "risks": syn.get("risks") or [],
        "caveats": syn.get("caveats") or [
            "Crowding score is a heuristic — not a formal bubble indicator.",
        ],
        "methodology_version": meta.get("methodology_version"),
        "framework_note": meta.get("framework_note"),
    }
