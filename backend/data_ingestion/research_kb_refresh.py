"""Daily research knowledge-base refresh — materialize company_snapshots."""
import json
import os
import sys
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Dict, Iterable, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import TICKER_UNIVERSE
from app.database import (
    CompanySnapshot,
    DailyPrice,
    InternalPriceTarget,
    NewsLLMScore,
    ResearchWatchlist,
    SectorSnapshot,
    SessionLocal,
    TickerClassification,
    TickerFundamental,
    TickerMetadata,
    UniverseTicker,
    init_db,
)
from data_ingestion.analyst_content_fetcher import refresh as refresh_analyst_items
from data_ingestion.analyst_fetcher import snapshot_forecast


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _field(value, source: str, as_of: str, coverage: str = "full") -> dict:
    if value is None:
        return {"value": None, "as_of": as_of, "source": source, "coverage": "missing"}
    return {"value": value, "as_of": as_of, "source": source, "coverage": coverage}


def _momentum(db, ticker: str, days: int) -> Optional[float]:
    rows = (
        db.query(DailyPrice.close, DailyPrice.date)
        .filter(DailyPrice.ticker == ticker)
        .order_by(DailyPrice.date.desc())
        .limit(days + 5)
        .all()
    )
    if len(rows) < 2:
        return None
    cur = float(rows[0][0])
    idx = min(len(rows) - 1, days)
    base = float(rows[idx][0])
    if not base:
        return None
    return (cur - base) / base


def _news_agg(db, ticker: str, days: int) -> tuple:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = (
        db.query(NewsLLMScore)
        .filter(NewsLLMScore.ticker == ticker, NewsLLMScore.date >= cutoff)
        .all()
    )
    if not rows:
        return None, 0
    weighted = [(r.llm_score or 0) * (r.llm_relevance or 0) for r in rows]
    return mean(weighted), len(rows)


def _fundamentals_summary(db, ticker: str) -> Optional[dict]:
    row = (
        db.query(TickerFundamental)
        .filter(TickerFundamental.ticker == ticker)
        .order_by(TickerFundamental.end_date.desc())
        .first()
    )
    if not row:
        return None
    return {
        "gross_margin": row.gross_margin,
        "operating_margin": row.operating_margin,
        "fcf_margin": row.fcf_margin,
        "roe": row.roe,
        "debt_to_equity": row.debt_to_equity,
        "end_date": row.end_date,
    }


def refresh_tickers(db=None) -> List[str]:
    if db is None:
        db = SessionLocal()
    tickers = set(TICKER_UNIVERSE)
    for row in db.query(UniverseTicker).all():
        tickers.add(row.ticker)
    for row in db.query(ResearchWatchlist).all():
        tickers.add(row.ticker)
    return sorted(tickers)


def materialize_ticker(db, ticker: str, as_of_date: Optional[str] = None) -> CompanySnapshot:
    ticker = ticker.upper().strip()
    as_of = as_of_date or _today()
    cls = db.query(TickerClassification).filter(TickerClassification.ticker == ticker).first()
    meta = db.query(TickerMetadata).filter(TickerMetadata.ticker == ticker).first()
    forecast = snapshot_forecast(ticker, db=db, refresh=False)

    price_row = (
        db.query(DailyPrice)
        .filter(DailyPrice.ticker == ticker)
        .order_by(DailyPrice.date.desc())
        .first()
    )
    price = float(price_row.close) if price_row else (forecast.current_price if forecast else None)

    news_7, _ = _news_agg(db, ticker, 7)
    news_30, news_cnt = _news_agg(db, ticker, 30)
    fund = _fundamentals_summary(db, ticker)

    try:
        from data_ingestion.earnings_content_fetcher import earnings_facts_for_ticker

        earnings = earnings_facts_for_ticker(db, ticker)
    except Exception:
        earnings = {}

    facts = {
        "ticker": _field(ticker, "universe", as_of),
        "price": _field(price, "daily_prices", as_of),
        "momentum_1w": _field(_momentum(db, ticker, 5), "daily_prices", as_of),
        "momentum_1m": _field(_momentum(db, ticker, 21), "daily_prices", as_of),
        "momentum_3m": _field(_momentum(db, ticker, 63), "daily_prices", as_of),
        "momentum_1y": _field(_momentum(db, ticker, 252), "daily_prices", as_of),
        "tier": _field(cls.tier_override or cls.tier if cls else None, "ticker_classification", as_of),
        "quality": _field(cls.quality if cls else None, "ticker_classification", as_of),
        "volatility": _field(cls.volatility if cls else None, "ticker_classification", as_of),
        "verdict": _field(cls.llm_verdict if cls else None, "ticker_classification", as_of),
        "target_mean": _field(forecast.target_mean if forecast else None, "analyst_fetcher", as_of),
        "target_high": _field(forecast.target_high if forecast else None, "analyst_fetcher", as_of),
        "target_low": _field(forecast.target_low if forecast else None, "analyst_fetcher", as_of),
        "num_analysts": _field(forecast.num_analysts if forecast else None, "analyst_fetcher", as_of),
        "upside_pct": _field(forecast.upside_pct if forecast else None, "analyst_fetcher", as_of),
        "recommendation_key": _field(forecast.recommendation_key if forecast else None, "analyst_fetcher", as_of),
        "news_score_7d": _field(news_7, "news_llm_scores", as_of),
        "news_score_30d": _field(news_30, "news_llm_scores", as_of),
        "fundamentals_summary": _field(fund, "ticker_fundamentals", as_of, "full" if fund else "missing"),
        "sector": _field(meta.sector if meta else None, "ticker_metadata", as_of),
        "industry": _field(meta.industry if meta else None, "ticker_metadata", as_of),
        "eps_revision_30d": _field(earnings.get("eps_revision_30d"), "earnings_estimate_snapshots", as_of),
        "next_eps_avg": _field(
            (earnings.get("next_eps_estimate") or {}).get("eps_avg"),
            "earnings_estimate_snapshots",
            as_of,
        ),
        "last_earnings_surprise_pct": _field(
            earnings.get("last_earnings_surprise_pct"), "earnings_surprises", as_of
        ),
        "latest_transcript_period": _field(
            earnings.get("latest_transcript_period"), "earnings_transcripts", as_of,
            "full" if earnings.get("has_transcript") else "missing",
        ),
    }
    keys = [k for k in facts if k != "fundamentals_summary"]
    full = sum(1 for k in keys if facts[k].get("coverage") == "full")
    coverage = round(full / max(len(keys), 1), 3)

    row = {
        "ticker": ticker,
        "as_of_date": as_of,
        "price": price,
        "momentum_1w": facts["momentum_1w"]["value"],
        "momentum_1m": facts["momentum_1m"]["value"],
        "momentum_3m": facts["momentum_3m"]["value"],
        "momentum_1y": facts["momentum_1y"]["value"],
        "tier": facts["tier"]["value"],
        "quality": facts["quality"]["value"],
        "volatility": facts["volatility"]["value"],
        "verdict": facts["verdict"]["value"],
        "target_mean": facts["target_mean"]["value"],
        "target_high": facts["target_high"]["value"],
        "target_low": facts["target_low"]["value"],
        "num_analysts": int(facts["num_analysts"]["value"]) if facts["num_analysts"]["value"] is not None else None,
        "upside_pct": facts["upside_pct"]["value"],
        "recommendation_key": facts["recommendation_key"]["value"],
        "news_score_7d": news_7,
        "news_score_30d": news_30,
        "news_headline_count_30d": news_cnt,
        "sector": facts["sector"]["value"],
        "industry": facts["industry"]["value"],
        "coverage_pct": coverage,
        "facts_json": json.dumps({**facts, "coverage_pct": coverage}),
        "refreshed_at": _now(),
    }
    stmt = sqlite_insert(CompanySnapshot).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "as_of_date"],
        set_={k: stmt.excluded[k] for k in row if k not in ("ticker", "as_of_date")},
    )
    db.execute(stmt)
    db.commit()
    _write_internal_target(db, ticker, as_of, price, forecast)
    return db.query(CompanySnapshot).filter(
        CompanySnapshot.ticker == ticker, CompanySnapshot.as_of_date == as_of
    ).first()


def _horizon_12m(as_of: str) -> str:
    from datetime import date as dt_date
    d = dt_date.fromisoformat(as_of)
    try:
        return d.replace(year=d.year + 1).isoformat()
    except ValueError:
        return d.replace(year=d.year + 1, day=28).isoformat()


def _write_internal_target(db, ticker: str, as_of: str, price: Optional[float], forecast) -> None:
    from ml_engine.research_framework import compute_internal_target

    if not forecast or forecast.target_mean is None:
        return
    mom = _momentum(db, ticker, 63)
    blended = compute_internal_target(
        float(forecast.target_mean),
        forecast.num_analysts,
        mom,
        price,
    )
    horizon = _horizon_12m(as_of)
    row = {
        "ticker": ticker,
        "as_of_date": as_of,
        "horizon_date": horizon,
        "target_price": blended["target_price"],
        "method": blended["method"],
        "confidence": blended["confidence"],
        "notes": blended["notes"],
        "refreshed_at": _now(),
    }
    stmt = sqlite_insert(InternalPriceTarget).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "as_of_date", "horizon_date"],
        set_={k: stmt.excluded[k] for k in row if k not in ("ticker", "as_of_date", "horizon_date")},
    )
    db.execute(stmt)
    db.commit()


def materialize_sector(db, sector_id: str, as_of_date: Optional[str] = None) -> Optional[SectorSnapshot]:
    from ml_engine.sector_analyzer import aggregate_sector

    as_of = as_of_date or _today()
    agg = aggregate_sector(db, sector_id, as_of_date=None)
    if not agg.get("ticker_count"):
        return None
    row = {
        "sector_id": sector_id,
        "as_of_date": as_of,
        "ticker_count": agg.get("ticker_count"),
        "median_upside_pct": agg.get("median_upside_pct"),
        "median_momentum_3m": agg.get("median_momentum_3m"),
        "median_news_score_30d": agg.get("median_news_score_30d"),
        "median_quality": agg.get("median_quality"),
        "etf_proxy": agg.get("etf_proxy"),
        "facts_json": json.dumps(agg),
        "refreshed_at": _now(),
    }
    stmt = sqlite_insert(SectorSnapshot).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["sector_id", "as_of_date"],
        set_={k: stmt.excluded[k] for k in row if k not in ("sector_id", "as_of_date")},
    )
    db.execute(stmt)
    db.commit()
    return db.query(SectorSnapshot).filter(
        SectorSnapshot.sector_id == sector_id, SectorSnapshot.as_of_date == as_of
    ).first()


def run_refresh(tickers: Optional[Iterable[str]] = None, fetch_analyst: bool = True) -> Dict:
    init_db()
    db = SessionLocal()
    try:
        universe = list(tickers) if tickers else refresh_tickers(db)
        if fetch_analyst:
            refresh_analyst_items(universe, db=db)
        for t in universe:
            materialize_ticker(db, t)
        from ml_engine.sector_analyzer import list_sectors

        for sec in list_sectors(db):
            materialize_sector(db, sec)
        try:
            from data_ingestion.sector_catalog_refresh import refresh_catalog

            refresh_catalog(db=db, top_n=5, fetch=True, portfolio_only=True)
        except Exception:
            pass
        return {"status": "ok", "tickers": len(universe), "as_of": _today()}
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Refresh research knowledge base")
    p.add_argument("--ticker", default="")
    p.add_argument("--no-analyst", action="store_true")
    a = p.parse_args()
    tickers = [a.ticker] if a.ticker else None
    print(run_refresh(tickers=tickers, fetch_analyst=not a.no_analyst))
