"""Portfolio-aware ticker expansion for research queries (Phase 2a structured RAG)."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.core.config import RESEARCH_MAX_TICKERS
from ml_engine.intent_router import RoutedQuery

_SPILLOVER_KEYWORDS = (
    "earnings",
    "impact",
    "impacted",
    "affect",
    "affected",
    "spillover",
    "read-through",
    "read through",
    "ripple",
    "my portfolio",
    "my holdings",
    "holdings",
    "other stocks",
    "other names",
    "how might",
    "how will",
)

_SEMICONDUCTOR_HINTS = ("semiconductor", "semi ", "memory", "chip", "dram", "nand", "hbm")


def is_spillover_query(query: str) -> bool:
    low = (query or "").lower()
    return any(k in low for k in _SPILLOVER_KEYWORDS)


def portfolio_tickers(db) -> List[str]:
    """Tickers the user actually holds or monitors (external + virtual + universe)."""
    from app.database import EquityLot, UniverseTicker, VirtualPosition

    tickers: set[str] = set()
    for row in db.query(EquityLot.ticker).distinct().all():
        if row[0]:
            tickers.add(row[0].upper().strip())
    for row in db.query(UniverseTicker.ticker).all():
        if row.ticker:
            tickers.add(row.ticker.upper().strip())
    for row in db.query(VirtualPosition).filter(VirtualPosition.quantity > 0).all():
        tickers.add(row.ticker.upper().strip())
    return sorted(tickers)


def _sector_for_ticker(db, ticker: str) -> Tuple[Optional[str], Optional[str]]:
    from app.database import CompanySnapshot, TickerMetadata

    ticker = ticker.upper().strip()
    meta = db.query(TickerMetadata).filter(TickerMetadata.ticker == ticker).first()
    if meta and (meta.sector or meta.industry):
        return meta.sector, meta.industry
    snap = (
        db.query(CompanySnapshot)
        .filter(CompanySnapshot.ticker == ticker)
        .order_by(CompanySnapshot.as_of_date.desc())
        .first()
    )
    if snap:
        return snap.sector, snap.industry
    return None, None


def _append_unique(out: List[str], ticker: str) -> None:
    tk = ticker.upper().strip()
    if tk and tk not in out:
        out.append(tk)


def expand_spillover_tickers(
    primary: str,
    routed: RoutedQuery,
    db,
    portfolio: Optional[List[str]] = None,
) -> Tuple[List[str], Dict]:
    """Primary event ticker + portfolio peers in same sector/industry (and theme fallback)."""
    portfolio = portfolio if portfolio is not None else portfolio_tickers(db)
    primary = primary.upper().strip()
    sector, industry = _sector_for_ticker(db, primary)
    related: List[str] = []

    for t in portfolio:
        if t == primary:
            continue
        s, i = _sector_for_ticker(db, t)
        if sector and s and s.lower() == sector.lower():
            _append_unique(related, t)
        elif industry and i and i.lower() == industry.lower():
            _append_unique(related, t)

    low = routed.raw_query.lower()
    if any(h in low for h in _SEMICONDUCTOR_HINTS):
        from ml_engine.theme_resolver import resolve

        for t in resolve("ai_infrastructure", None):
            if t in portfolio and t != primary:
                _append_unique(related, t)

    tickers = [primary]
    for t in related:
        _append_unique(tickers, t)

    meta = {
        "primary": primary,
        "portfolio": portfolio,
        "sector_peers": related,
        "sector": sector,
        "industry": industry,
    }
    return tickers[:RESEARCH_MAX_TICKERS], meta


def resolve_query_tickers(
    routed: RoutedQuery,
    db,
    extra_tickers: Optional[List[str]] = None,
) -> Tuple[List[str], Dict]:
    """Resolve the full ticker set for a routed query, with expansion metadata."""
    meta: Dict = {}

    if routed.intent == "theme_rank":
        from ml_engine.theme_resolver import resolve

        tickers = resolve(routed.theme, routed.tickers)
        meta["theme"] = routed.theme
        if extra_tickers:
            for t in extra_tickers:
                _append_unique(tickers, t)
        return tickers[:RESEARCH_MAX_TICKERS], meta

    if routed.intent == "event_spillover":
        if not routed.tickers:
            return [], {"error": "no_primary_ticker"}
        primary = routed.tickers[0]
        tickers, spill = expand_spillover_tickers(primary, routed, db)
        meta.update(spill)
        if extra_tickers:
            for t in extra_tickers:
                _append_unique(tickers, t)
        return tickers[:RESEARCH_MAX_TICKERS], meta

    tickers = list(routed.tickers or [])
    if extra_tickers:
        for t in extra_tickers:
            _append_unique(tickers, t)
    return tickers[:RESEARCH_MAX_TICKERS], meta


def recent_news_headlines(db, ticker: str, limit: int = 8, days: int = 14):
    """Recent scored headlines for synthesis context."""
    from datetime import date, timedelta

    from app.database import NewsLLMScore

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return (
        db.query(NewsLLMScore)
        .filter(NewsLLMScore.ticker == ticker.upper().strip(), NewsLLMScore.date >= cutoff)
        .order_by(NewsLLMScore.published_utc.desc())
        .limit(limit)
        .all()
    )
