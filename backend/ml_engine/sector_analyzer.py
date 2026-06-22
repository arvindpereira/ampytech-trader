"""Sector screening — GICS-aligned aggregates via research_framework."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.core.config import RESEARCH_MAX_TICKERS, TICKER_UNIVERSE
from ml_engine.intent_router import RoutedQuery
from ml_engine.rank_engine import rank_tickers
from ml_engine.research_framework import GICS_SECTOR_ETF, METHODOLOGY_VERSION, aggregate_sector_metrics
from ml_engine.sector_resolver import (
    match_sectors_in_query,
    merge_constituents,
    sector_handbook_for,
    seed_tickers,
)


def _norm_sector(name: str) -> str:
    return (name or "").strip()


def detect_sectors_in_query(query: str) -> List[str]:
    return match_sectors_in_query(query)


def list_sectors(db) -> List[str]:
    from app.database import CompanySnapshot
    from ml_engine.sector_resolver import list_sector_entries

    catalog = {_norm_sector(s.get("sector")) for s in list_sector_entries() if s.get("sector")}
    rows = db.query(CompanySnapshot.sector).filter(CompanySnapshot.sector.isnot(None)).distinct().all()
    db_sectors = {_norm_sector(r[0]) for r in rows if r[0]}
    return sorted(catalog | db_sectors)


def _latest_snaps_by_ticker(db, sector: str, as_of_date: Optional[str] = None) -> list:
    from app.database import CompanySnapshot

    q = db.query(CompanySnapshot).filter(CompanySnapshot.sector == _norm_sector(sector))
    if as_of_date:
        q = q.filter(CompanySnapshot.as_of_date == as_of_date)
    rows = q.order_by(CompanySnapshot.as_of_date.desc()).all()
    latest: Dict[str, object] = {}
    for r in rows:
        if r.ticker not in latest:
            latest[r.ticker] = r
    return list(latest.values())


def _market_caps(db, tickers: List[str]) -> Dict[str, float]:
    from app.database import TickerMetadata

    caps = {}
    for row in db.query(TickerMetadata).filter(TickerMetadata.ticker.in_(tickers)).all():
        if row.market_cap:
            caps[row.ticker] = float(row.market_cap)
    return caps


def _spy_momentum(db) -> Optional[float]:
    from app.database import CompanySnapshot

    row = (
        db.query(CompanySnapshot)
        .filter(CompanySnapshot.ticker == "SPY")
        .order_by(CompanySnapshot.as_of_date.desc())
        .first()
    )
    return row.momentum_3m if row else None


def sector_constituents(db, sector: str, limit: int = RESEARCH_MAX_TICKERS) -> List[str]:
    snaps = _latest_snaps_by_ticker(db, sector)
    universe = {t.upper() for t in TICKER_UNIVERSE}
    kb_seen: List[str] = []
    for s in snaps:
        t = (s.ticker or "").upper().strip()
        if t and t not in kb_seen and (t in universe or len(kb_seen) < limit):
            kb_seen.append(t)
        if len(kb_seen) >= limit:
            break
    return merge_constituents(kb_seen, sector, limit=limit)


def aggregate_sector(db, sector: str, as_of_date: Optional[str] = None) -> Dict:
    snaps = _latest_snaps_by_ticker(db, sector, as_of_date)
    handbook = sector_handbook_for([sector])
    if not snaps:
        seeds = seed_tickers(sector, limit=RESEARCH_MAX_TICKERS)
        return {
            "sector": sector,
            "ticker_count": len(seeds),
            "methodology_version": METHODOLOGY_VERSION,
            "etf_proxy": GICS_SECTOR_ETF.get(_norm_sector(sector)),
            "constituents": seeds,
            "handbook_only": True,
            "sector_handbook": handbook[0] if handbook else None,
        }
    tickers = [s.ticker for s in snaps]
    caps = _market_caps(db, tickers)
    agg = aggregate_sector_metrics(snaps, market_caps=caps, spy_momentum_3m=_spy_momentum(db))
    agg["etf_proxy"] = agg.get("etf_proxy") or GICS_SECTOR_ETF.get(_norm_sector(sector))
    agg["constituents"] = merge_constituents(tickers, sector, limit=RESEARCH_MAX_TICKERS)
    if handbook:
        agg["sector_handbook"] = handbook[0]
    return agg


def screen_sectors(db, sort_by: str = "upside", *, ascending: bool = False) -> List[Dict]:
    sectors = list_sectors(db)
    rows = []
    for sec in sectors:
        agg = aggregate_sector(db, sec)
        if agg.get("handbook_only"):
            agg["screen_score"] = 0.0
            rows.append(agg)
            continue
        if agg.get("ticker_count", 0) < 2:
            continue
        score_key = {
            "upside": "median_upside_pct",
            "momentum": "median_momentum_3m",
            "news": "median_news_score_30d",
            "quality": "median_quality",
            "rel_strength": "rel_strength_vs_spy",
        }.get(sort_by, "median_upside_pct")
        score = agg.get(score_key)
        if score is None:
            continue
        rows.append({**agg, "screen_score": score})

    rows.sort(key=lambda r: r.get("screen_score") or 0, reverse=not ascending)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def resolve_sector_screen(routed: RoutedQuery, db) -> Tuple[List[str], Dict]:
    low = routed.raw_query.lower()
    ascending = any(k in low for k in ("overvalued", "over-valued", "expensive"))
    sort_by = "momentum" if "momentum" in low or "perform" in low else "upside"
    if "relative strength" in low or "vs market" in low:
        sort_by = "rel_strength"

    sectors = detect_sectors_in_query(routed.raw_query)
    sector_rankings = screen_sectors(db, sort_by=sort_by, ascending=ascending)

    if not sectors and sector_rankings:
        sectors = [r["sector"] for r in sector_rankings[:2]]

    tickers: List[str] = []
    for sec in sectors:
        for t in sector_constituents(db, sec):
            if t not in tickers:
                tickers.append(t)

    if not tickers and sector_rankings:
        top = sector_rankings[0]
        tickers = list(top.get("constituents") or [])[:RESEARCH_MAX_TICKERS]
        sectors = [top["sector"]]

    if not tickers and sectors:
        for sec in sectors:
            for t in seed_tickers(sec):
                if t not in tickers:
                    tickers.append(t)

    if not tickers:
        from ml_engine.research_dossier import get_many

        facts = get_many(list(TICKER_UNIVERSE)[:20], db=db)
        ranked = rank_tickers(facts)
        tickers = [r["ticker"] for r in ranked[:RESEARCH_MAX_TICKERS]]

    meta = {
        "sectors": sectors,
        "sector_rankings": sector_rankings[:8],
        "sector_handbook": sector_handbook_for(sectors),
        "sort_by": sort_by,
        "methodology_version": METHODOLOGY_VERSION,
        "screen_framing": "overvalued" if ascending else "undervalued" if "undervalued" in low else "neutral",
        "handbook_source": "research_sectors.json",
    }
    return tickers[:RESEARCH_MAX_TICKERS], meta


def sector_facts_for_synthesis(db, sector: str) -> Dict:
    agg = aggregate_sector(db, sector)
    constituents = sector_constituents(db, sector, limit=8)
    from ml_engine.research_dossier import get_many

    facts = get_many(constituents, db=db)
    return {
        "aggregate": agg,
        "ranked_constituents": rank_tickers(facts),
        "facts_by_ticker": facts,
        "sector_handbook": agg.get("sector_handbook"),
    }
