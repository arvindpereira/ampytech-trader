"""Refresh GICS sector handbook: cap-ranked seeds + portfolio classification.

Usage:
    python data_ingestion/sector_catalog_refresh.py
    python data_ingestion/sector_catalog_refresh.py --top 8 --no-fetch
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import BASE_DIR, TICKER_UNIVERSE
from app.database import SessionLocal, TickerMetadata, init_db
from data_ingestion.ticker_metadata_fetcher import is_equity_ticker, refresh_tickers
from ml_engine.portfolio_holdings import all_holdings, holdings_by_ticker, portfolio_tickers
from ml_engine.sector_resolver import canonical_sector, invalidate_cache, list_sector_entries, load_catalog, _catalog_path


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _canonical_sector(sector_raw: Optional[str]) -> Optional[str]:
    return canonical_sector(sector_raw)


def _collect_refresh_universe(db, *, portfolio_only: bool = False) -> List[str]:
    if portfolio_only:
        return portfolio_tickers(db)
    catalog = load_catalog()
    tickers = set(TICKER_UNIVERSE)
    tickers.update(portfolio_tickers(db))
    for entry in catalog.get("sectors") or []:
        for row in entry.get("seed_tickers") or []:
            if row.get("ticker"):
                tickers.add(row["ticker"].upper())
    return sorted(tickers)


def _metadata_rows(db) -> List[TickerMetadata]:
    return db.query(TickerMetadata).filter(TickerMetadata.sector.isnot(None)).all()


def _build_seed_row(rank: int, meta: TickerMetadata, notes: str = "") -> dict:
    return {
        "rank": rank,
        "ticker": meta.ticker,
        "company": meta.ticker,
        "exchange": None,
        "subsector": meta.industry,
        "notes": notes or f"market_cap={int(meta.market_cap or 0):,}",
        "market_cap": meta.market_cap,
    }


def _classify_portfolio(db) -> tuple:
    from app.database import CompanySnapshot, TickerMetadata

    by_ticker = holdings_by_ticker(db)
    classification: List[dict] = []
    by_sector: Dict[str, List[dict]] = {}

    for ticker, holds in sorted(by_ticker.items()):
        meta = db.query(TickerMetadata).filter(TickerMetadata.ticker == ticker).first()
        industry = meta.industry if meta else None
        sector = _normalize_sector_label(meta.sector if meta else None, industry)
        if not sector:
            snap = (
                db.query(CompanySnapshot)
                .filter(CompanySnapshot.ticker == ticker)
                .order_by(CompanySnapshot.as_of_date.desc())
                .first()
            )
            if snap:
                industry = industry or snap.industry
                sector = _normalize_sector_label(snap.sector, industry)
        sources = sorted({h["source"] for h in holds})
        accounts = sorted({h.get("account") for h in holds if h.get("account")})
        row = {
            "ticker": ticker,
            "sector": sector,
            "industry": industry,
            "market_cap": meta.market_cap if meta else None,
            "sources": sources,
            "accounts": accounts,
            "is_equity": is_equity_ticker(ticker),
        }
        classification.append(row)
        if sector:
            by_sector.setdefault(sector, []).append({
                "ticker": ticker,
                "industry": industry,
                "sources": sources,
                "accounts": accounts,
            })

    for sec in by_sector:
        by_sector[sec].sort(key=lambda r: r["ticker"])
    return classification, by_sector


def _known_sectors() -> set:
    return {e.get("sector") for e in list_sector_entries() if e.get("sector")}


def _normalize_sector_label(sector: Optional[str], industry: Optional[str]) -> Optional[str]:
    from data_ingestion.ticker_metadata_fetcher import infer_sector_from_industry

    known = _known_sectors()
    sec = _canonical_sector(sector) if sector else None
    if sec in known:
        return sec
    sec = _canonical_sector(industry) if industry else None
    if sec in known:
        return sec
    sec = infer_sector_from_industry(industry) or infer_sector_from_industry(sector)
    return _canonical_sector(sec) if sec else None


def _backfill_metadata_sectors(db) -> int:
    """Promote Finnhub broad industry labels to sector without re-fetching."""
    from data_ingestion.ticker_metadata_fetcher import infer_sector_from_industry

    updated = 0
    known = _known_sectors()
    rows = db.query(TickerMetadata).all()
    for r in rows:
        sec = _normalize_sector_label(r.sector, r.industry)
        if sec and sec in known and r.sector != sec:
            r.sector = sec
            updated += 1
    if updated:
        db.commit()
    return updated


def refresh_catalog(db=None, *, top_n: int = 5, fetch: bool = True, portfolio_only: bool = False, force: bool = False) -> dict:
    init_db()
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        universe = _collect_refresh_universe(db, portfolio_only=portfolio_only)
        fetch_stats = {"skipped": True}
        if fetch:
            fetch_stats = refresh_tickers(universe, db=db, force=force)

        backfilled = _backfill_metadata_sectors(db)

        classified, portfolio_by_sector = _classify_portfolio(db)
        meta_rows = [r for r in _metadata_rows(db) if is_equity_ticker(r.ticker)]

        catalog = load_catalog()
        sectors_out = []
        seed_stats = {}

        for entry in catalog.get("sectors") or []:
            sector_key = entry.get("sector")
            candidates = [
                r for r in meta_rows
                if _canonical_sector(r.sector) == sector_key and r.market_cap
            ]
            candidates.sort(key=lambda r: r.market_cap or 0, reverse=True)

            # Portfolio names in this sector (always included in handbook)
            port = list(portfolio_by_sector.get(sector_key) or [])
            port_tickers = {p["ticker"] for p in port}

            seeds = []
            rank = 1
            for meta in candidates[:top_n]:
                note = "portfolio holding" if meta.ticker in port_tickers else "cap-ranked seed"
                seeds.append(_build_seed_row(rank, meta, notes=note))
                rank += 1

            # If fewer than top_n from DB, keep prior static seeds not yet present
            if len(seeds) < top_n:
                existing = {s["ticker"] for s in seeds}
                for old in entry.get("seed_tickers") or []:
                    tk = old.get("ticker")
                    if tk and tk not in existing:
                        seeds.append({**old, "rank": rank, "notes": (old.get("notes") or "") + " (static fallback)"})
                        rank += 1
                        existing.add(tk)
                    if len(seeds) >= top_n:
                        break

            seed_stats[sector_key] = len(seeds)
            sectors_out.append({
                **entry,
                "seed_tickers": seeds[:top_n],
                "portfolio_holdings": port,
                "classified_ticker_count": len(candidates),
            })

        out = {
            **{k: v for k, v in catalog.items() if k != "sectors"},
            "last_refreshed_at": _now(),
            "seeds_top_n": top_n,
            "metadata_universe_size": len(universe),
            "classified_equity_count": len([r for r in meta_rows if r.market_cap]),
            "portfolio_classification": classified,
            "portfolio_by_sector": portfolio_by_sector,
            "sectors": sectors_out,
        }
        path = _catalog_path()
        with open(path, "w") as f:
            json.dump(out, f, indent=2)

        # Invalidate sector_resolver cache
        import ml_engine.sector_resolver as sr
        sr.invalidate_cache()

        return {
            "status": "ok",
            "path": path,
            "fetch": fetch_stats,
            "portfolio_tickers": len(classified),
            "sectors_with_seeds": seed_stats,
            "portfolio_by_sector_counts": {k: len(v) for k, v in portfolio_by_sector.items()},
            "metadata_backfilled": backfilled,
        }
    finally:
        if close:
            db.close()


def main():
    p = argparse.ArgumentParser(description="Refresh sector handbook seeds and portfolio classification")
    p.add_argument("--top", type=int, default=5, help="Top N cap-ranked seeds per sector")
    p.add_argument("--no-fetch", action="store_true", help="Skip metadata fetch")
    p.add_argument("--portfolio-only", action="store_true", help="Fetch metadata for portfolio tickers only")
    p.add_argument("--force", action="store_true", help="Re-fetch metadata even if recently updated")
    args = p.parse_args()
    result = refresh_catalog(
        top_n=args.top, fetch=not args.no_fetch, portfolio_only=args.portfolio_only, force=args.force
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
