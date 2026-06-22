"""Generate research reports using templates + tiered LLM."""
from typing import Any, Callable, Dict, List, Optional

ProgressCb = Optional[Callable[[int, str], None]]

from ml_engine.analyst_synthesizer import synthesize_sector, synthesize_spillover, synthesize_theme, synthesize_ticker
from ml_engine.intent_router import RoutedQuery, is_stub_intent
from ml_engine.rank_engine import rank_tickers
from ml_engine.research_dossier import coverage_pct, get_many
from ml_engine.research_llm_router import RouteDecision, decide
from ml_engine.research_templates import (
    event_spillover_shell,
    lookup_template,
    sector_screen_shell,
    stub_shell,
    theme_rank_shell,
    ticker_outlook_shell,
)
from ml_engine.theme_resolver import load_themes, resolve


def _tier_label(tier: str) -> str:
    return {
        "lookup": "lookup",
        "standard": "GPT-4o mini",
        "premium": "Premium AI",
        "expert": "Premium AI",
        "local": "local AI",
    }.get(tier, tier)


def build_report(
    routed: RoutedQuery,
    facts_by_ticker: Dict[str, Dict],
    items_by_ticker: Dict[str, list],
    route: RouteDecision,
    progress_cb: ProgressCb = None,
    news_by_ticker: Optional[Dict[str, list]] = None,
    expansion: Optional[Dict] = None,
    web_items: Optional[list] = None,
) -> Dict[str, Any]:
    def progress(pct: int, stage: str) -> None:
        if progress_cb:
            progress_cb(pct, stage)

    news_by_ticker = news_by_ticker or {}
    expansion = expansion or {}
    web_items = web_items or []

    if is_stub_intent(routed.intent):
        progress(80, "Building placeholder report")
        return stub_shell(
            routed.intent,
            f"{routed.intent} is planned for Phase 3. Partial snapshot data may be available for detected tickers.",
        )

    if route.tier == "lookup" and len(routed.tickers) == 1:
        t = routed.tickers[0]
        progress(80, "Building lookup report from snapshot")
        return lookup_template(t, facts_by_ticker.get(t, {}))

    if routed.intent == "sector_screen":
        sectors = expansion.get("sectors") or routed.sectors or []
        sector_rankings = expansion.get("sector_rankings") or []
        tickers = list(facts_by_ticker.keys())
        progress(10, "Ranking sector constituents")
        ranked = rank_tickers({t: facts_by_ticker.get(t, {}) for t in tickers})
        progress(35, f"Synthesizing sector screen ({_tier_label(route.tier)})")
        syn = synthesize_sector(
            sectors,
            sector_rankings,
            ranked,
            facts_by_ticker,
            items_by_ticker,
            tier=route.tier,
            query=routed.raw_query,
            news_by_ticker=news_by_ticker,
            web_items=web_items,
        )
        if syn.get("error"):
            syn = {"caveats": syn.get("caveats", [])}
        progress(85, "Assembling sector report")
        return sector_screen_shell(sectors, sector_rankings, ranked, syn, expansion)

    if routed.intent == "theme_rank":
        theme_id = routed.theme or "custom"
        theme_label = (load_themes().get(theme_id) or {}).get("label") or theme_id
        tickers = resolve(theme_id if routed.theme else None, routed.tickers)
        if not tickers:
            return stub_shell("theme_rank", "No tickers resolved for theme. Add tickers or pick a known theme.")
        facts = {t: facts_by_ticker.get(t, {}) for t in tickers}
        progress(10, "Ranking companies")
        ranked = rank_tickers(facts)
        progress(35, f"Synthesizing theme narrative ({_tier_label(route.tier)})")
        syn = synthesize_theme(
            theme_label, ranked, facts, items_by_ticker, tier=route.tier, query=routed.raw_query,
            news_by_ticker=news_by_ticker,
        )
        if syn.get("error"):
            syn = {"caveats": syn.get("caveats", [])}
        progress(85, "Assembling report template")
        return theme_rank_shell(theme_label, ranked, syn)

    if routed.intent == "event_spillover":
        primary = routed.tickers[0] if routed.tickers else None
        if not primary:
            return stub_shell("event_spillover", "No event ticker detected. Mention a symbol like MU or Micron.")
        related = [t for t in facts_by_ticker if t != primary]
        progress(35, f"Synthesizing earnings spillover ({_tier_label(route.tier)})")
        syn = synthesize_spillover(
            primary,
            related,
            facts_by_ticker,
            items_by_ticker,
            tier=route.tier,
            query=routed.raw_query,
            news_by_ticker=news_by_ticker,
        )
        if syn.get("error"):
            syn = {"caveats": syn.get("caveats", [])}
        progress(85, "Assembling spillover report")
        return event_spillover_shell(primary, related, facts_by_ticker, syn, expansion)

    if routed.intent == "ticker_outlook":
        ticker = routed.tickers[0] if routed.tickers else None
        if not ticker:
            return stub_shell("ticker_outlook", "No ticker detected. Mention a symbol like NVDA.")
        facts = facts_by_ticker.get(ticker, {})
        items = items_by_ticker.get(ticker, [])
        progress(35, f"Synthesizing outlook ({_tier_label(route.tier)})")
        syn = synthesize_ticker(
            ticker, facts, items, tier=route.tier, query=routed.raw_query,
            news_rows=news_by_ticker.get(ticker, []),
        )
        if syn.get("error"):
            syn = {"caveats": syn.get("caveats", [])}
        progress(85, "Assembling report template")
        report = ticker_outlook_shell(ticker, facts, syn)
        if syn.get("tldr"):
            report["tldr"] = syn["tldr"]
        if syn.get("outlook_narrative"):
            report["outlook_narrative"] = syn["outlook_narrative"]
        report["catalysts"] = syn.get("catalysts") or report["catalysts"]
        report["risks"] = syn.get("risks") or report["risks"]
        report["caveats"] = syn.get("caveats") or report["caveats"]
        return report

    return stub_shell(routed.intent, "Unsupported intent.")


def prepare_context(
    tickers: List[str],
    db,
    query: str = "",
    web_items: Optional[list] = None,
) -> tuple:
    from data_ingestion.analyst_content_fetcher import recent_items
    from ml_engine.context_expander import recent_news_headlines
    from ml_engine.news_retriever import retrieve_for_query

    facts_by_ticker = get_many(tickers, db=db)
    items_by_ticker = {t: recent_items(db, t) for t in tickers}
    news_by_ticker = {t: recent_news_headlines(db, t) for t in tickers}

    if query.strip():
        try:
            retrieval = retrieve_for_query(query, tickers, db=db)
            for row in retrieval.get("news_rows") or []:
                tk = (row.ticker or "").upper()
                if tk in news_by_ticker:
                    if not any(n.article_id == row.article_id for n in news_by_ticker[tk]):
                        news_by_ticker[tk].append(row)
            for it in (retrieval.get("promoted_items") or []) + (retrieval.get("extra_items") or []):
                tk = (it.ticker or (tickers[0] if tickers else "")).upper()
                if tk in items_by_ticker:
                    if not any(x.id == it.id for x in items_by_ticker[tk]):
                        items_by_ticker[tk].append(it)
        except Exception:
            pass

    if web_items:
        for it in web_items:
            tk = (it.ticker or "").upper()
            if tk and tk in items_by_ticker:
                if not any(x.id == it.id for x in items_by_ticker[tk]):
                    items_by_ticker[tk].append(it)
            elif tickers:
                primary = tickers[0]
                if not any(x.id == it.id for x in items_by_ticker.get(primary, [])):
                    items_by_ticker.setdefault(primary, []).append(it)
    coverage = {t: coverage_pct(facts_by_ticker.get(t, {})) for t in tickers}
    return facts_by_ticker, items_by_ticker, coverage, news_by_ticker
