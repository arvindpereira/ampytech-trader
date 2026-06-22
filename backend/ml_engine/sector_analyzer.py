"""Sector screening and aggregation for Research Analyst Phase 2b."""
from __future__ import annotations

import json
from statistics import mean
from typing import Dict, List, Optional, Tuple

from app.core.config import RESEARCH_MAX_TICKERS, TICKER_UNIVERSE
from ml_engine.intent_router import RoutedQuery
from ml_engine.rank_engine import rank_tickers

# Query phrases → canonical sector name (matches ticker_metadata.sector values)
_SECTOR_ALIASES = {
    "technology": "Technology",
    "tech": "Technology",
    "semiconductor": "Technology",
    "semiconductors": "Technology",
    "semi": "Technology",
    "chip": "Technology",
    "software": "Technology",
    "financial": "Financial Services",
    "financials": "Financial Services",
    "bank": "Financial Services",
    "banks": "Financial Services",
    "healthcare": "Healthcare",
    "health": "Healthcare",
    "pharma": "Healthcare",
    "energy": "Energy",
    "oil": "Energy",
    "consumer": "Consumer Defensive",
    "staples": "Consumer Defensive",
    "industrial": "Industrials",
    "industrials": "Industrials",
    "communication": "Communication Services",
    "telecom": "Communication Services",
    "real estate": "Real Estate",
    "utilities": "Utilities",
    "materials": "Basic Materials",
    "basic materials": "Basic Materials",
}

# Sector ETF proxies in our universe
_SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Energy": "XLE",
    "Healthcare": "XLV",
    "Consumer Defensive": "XLP",
}


def _norm_sector(name: str) -> str:
    return (name or "").strip()


def detect_sectors_in_query(query: str) -> List[str]:
    low = (query or "").lower()
    found: List[str] = []
    for phrase, sector in sorted(_SECTOR_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in low and sector not in found:
            found.append(sector)
    return found


def list_sectors(db) -> List[str]:
    from app.database import CompanySnapshot

    rows = db.query(CompanySnapshot.sector).filter(CompanySnapshot.sector.isnot(None)).distinct().all()
    sectors = sorted({_norm_sector(r[0]) for r in rows if r[0]})
    return sectors


def sector_constituents(db, sector: str, limit: int = RESEARCH_MAX_TICKERS) -> List[str]:
    from app.database import CompanySnapshot

    sector = _norm_sector(sector)
    rows = (
        db.query(CompanySnapshot.ticker)
        .filter(CompanySnapshot.sector == sector)
        .order_by(CompanySnapshot.as_of_date.desc())
        .all()
    )
    seen: List[str] = []
    universe = {t.upper() for t in TICKER_UNIVERSE}
    for (tk,) in rows:
        t = (tk or "").upper().strip()
        if t and t not in seen and (t in universe or len(seen) < limit):
            seen.append(t)
        if len(seen) >= limit:
            break
    return seen


def aggregate_sector(db, sector: str, as_of_date: Optional[str] = None) -> Dict:
    """Build sector-level facts from latest company snapshots."""
    from app.database import CompanySnapshot

    sector = _norm_sector(sector)
    q = db.query(CompanySnapshot).filter(CompanySnapshot.sector == sector)
    if as_of_date:
        q = q.filter(CompanySnapshot.as_of_date == as_of_date)
    rows = q.order_by(CompanySnapshot.as_of_date.desc()).all()
    latest_by_ticker: Dict[str, CompanySnapshot] = {}
    for r in rows:
        if r.ticker not in latest_by_ticker:
            latest_by_ticker[r.ticker] = r

    snaps = list(latest_by_ticker.values())
    if not snaps:
        return {"sector": sector, "ticker_count": 0}

    def _vals(attr):
        return [getattr(s, attr) for s in snaps if getattr(s, attr) is not None]

    upside_vals = _vals("upside_pct")
    mom_vals = _vals("momentum_3m")
    news_vals = _vals("news_score_30d")
    qual_vals = _vals("quality")

    return {
        "sector": sector,
        "ticker_count": len(snaps),
        "median_upside_pct": mean(upside_vals) if upside_vals else None,
        "median_momentum_3m": mean(mom_vals) if mom_vals else None,
        "median_news_score_30d": mean(news_vals) if news_vals else None,
        "median_quality": mean(qual_vals) if qual_vals else None,
        "etf_proxy": _SECTOR_ETF.get(sector),
        "constituents": [s.ticker for s in snaps[:RESEARCH_MAX_TICKERS]],
    }


def screen_sectors(db, sort_by: str = "upside") -> List[Dict]:
    """Rank sectors by aggregate metrics."""
    sectors = list_sectors(db)
    rows = []
    for sec in sectors:
        agg = aggregate_sector(db, sec)
        if agg.get("ticker_count", 0) < 2:
            continue
        score_key = {
            "upside": "median_upside_pct",
            "momentum": "median_momentum_3m",
            "news": "median_news_score_30d",
            "quality": "median_quality",
        }.get(sort_by, "median_upside_pct")
        score = agg.get(score_key)
        if score is None:
            continue
        rows.append({**agg, "screen_score": score})

    rows.sort(key=lambda r: r.get("screen_score") or 0, reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def resolve_sector_screen(
    routed: RoutedQuery,
    db,
) -> Tuple[List[str], Dict]:
    """Resolve tickers and metadata for sector_screen queries."""
    low = routed.raw_query.lower()
    sort_by = "momentum" if "momentum" in low or "perform" in low else "upside"
    if "overvalued" in low or "over-valued" in low or "expensive" in low:
        sort_by = "upside"  # rank ascending later for overvalued framing

    sectors = detect_sectors_in_query(routed.raw_query)
    sector_rankings = screen_sectors(db, sort_by=sort_by)

    if not sectors and sector_rankings:
        # Multi-sector screen — take top 2 sectors' constituents
        sectors = [r["sector"] for r in sector_rankings[:2]]

    tickers: List[str] = []
    for sec in sectors:
        for t in sector_constituents(db, sec):
            if t not in tickers:
                tickers.append(t)

    if not tickers and sectors:
        for sec in sectors:
            tickers.extend(sector_constituents(db, sec, limit=8))

    if not tickers and sector_rankings:
        # Default: constituents from top-ranked sector
        top = sector_rankings[0]
        tickers = list(top.get("constituents") or [])[:RESEARCH_MAX_TICKERS]
        sectors = [top["sector"]]

    if not tickers:
        # Fallback: rank known universe by snapshot
        from ml_engine.research_dossier import get_many

        facts = get_many(list(TICKER_UNIVERSE)[:20], db=db)
        ranked = rank_tickers(facts)
        tickers = [r["ticker"] for r in ranked[:RESEARCH_MAX_TICKERS]]

    meta = {
        "sectors": sectors,
        "sector_rankings": sector_rankings[:8],
        "sort_by": sort_by,
        "screen_framing": "overvalued" if "overvalued" in low else "undervalued" if "undervalued" in low else "neutral",
    }
    return tickers[:RESEARCH_MAX_TICKERS], meta


def sector_facts_for_synthesis(db, sector: str) -> Dict:
    """Sector aggregate block for LLM prompt."""
    agg = aggregate_sector(db, sector)
    constituents = sector_constituents(db, sector, limit=8)
    from ml_engine.research_dossier import get_many

    facts = get_many(constituents, db=db)
    ranked = rank_tickers(facts)
    return {
        "aggregate": agg,
        "ranked_constituents": ranked,
        "facts_by_ticker": facts,
    }
