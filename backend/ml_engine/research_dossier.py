"""Read company knowledge-base snapshots for research queries."""
import json
from datetime import date
from typing import Any, Dict, List, Optional

from app.database import CompanySnapshot, SessionLocal


def _field(value, as_of: str, source: str, coverage: str = "full") -> Dict[str, Any]:
    if value is None:
        return {"value": None, "as_of": as_of, "source": source, "coverage": "missing"}
    return {"value": value, "as_of": as_of, "source": source, "coverage": coverage}


def snapshot_row_to_dict(row: CompanySnapshot) -> Dict[str, Any]:
    if not row:
        return {}
    as_of = row.as_of_date or date.today().isoformat()
    if row.facts_json:
        try:
            return json.loads(row.facts_json)
        except Exception:
            pass
    return {
        "ticker": _field(row.ticker, as_of, "company_snapshots"),
        "price": _field(row.price, as_of, "daily_prices"),
        "momentum_1w": _field(row.momentum_1w, as_of, "daily_prices"),
        "momentum_1m": _field(row.momentum_1m, as_of, "daily_prices"),
        "momentum_3m": _field(row.momentum_3m, as_of, "daily_prices"),
        "momentum_1y": _field(row.momentum_1y, as_of, "daily_prices"),
        "tier": _field(row.tier, as_of, "ticker_classification"),
        "quality": _field(row.quality, as_of, "ticker_classification"),
        "volatility": _field(row.volatility, as_of, "ticker_classification"),
        "verdict": _field(row.verdict, as_of, "ticker_classification"),
        "target_mean": _field(row.target_mean, as_of, "analyst_fetcher"),
        "target_high": _field(row.target_high, as_of, "analyst_fetcher"),
        "target_low": _field(row.target_low, as_of, "analyst_fetcher"),
        "num_analysts": _field(row.num_analysts, as_of, "analyst_fetcher"),
        "upside_pct": _field(row.upside_pct, as_of, "analyst_fetcher"),
        "recommendation_key": _field(row.recommendation_key, as_of, "analyst_fetcher"),
        "news_score_7d": _field(row.news_score_7d, as_of, "news_llm_scores"),
        "news_score_30d": _field(row.news_score_30d, as_of, "news_llm_scores"),
        "sector": _field(row.sector, as_of, "ticker_metadata"),
        "industry": _field(row.industry, as_of, "ticker_metadata"),
        "coverage_pct": row.coverage_pct,
    }


def get_snapshot(db, ticker: str, as_of_date: Optional[str] = None) -> Optional[CompanySnapshot]:
    ticker = ticker.upper().strip()
    q = db.query(CompanySnapshot).filter(CompanySnapshot.ticker == ticker)
    if as_of_date:
        return q.filter(CompanySnapshot.as_of_date == as_of_date).first()
    return q.order_by(CompanySnapshot.as_of_date.desc()).first()


def _attach_internal_target(db, facts: Dict[str, Any], ticker: str) -> Dict[str, Any]:
    from app.database import InternalPriceTarget

    row = (
        db.query(InternalPriceTarget)
        .filter(InternalPriceTarget.ticker == ticker.upper())
        .order_by(InternalPriceTarget.as_of_date.desc())
        .first()
    )
    if row and row.target_price is not None:
        as_of = row.as_of_date or date.today().isoformat()
        facts = dict(facts)
        facts["internal_target_12m"] = {
            "value": row.target_price,
            "as_of": as_of,
            "source": f"internal_price_targets ({row.method})",
            "coverage": "full",
            "confidence": row.confidence,
            "horizon_date": row.horizon_date,
        }
    return facts


def get(ticker: str, db=None) -> Dict[str, Any]:
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        row = get_snapshot(db, ticker)
        return snapshot_row_to_dict(row)
    finally:
        if close:
            db.close()


def get_many(tickers: List[str], db=None) -> Dict[str, Dict[str, Any]]:
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        out = {}
        for t in tickers:
            facts = snapshot_row_to_dict(get_snapshot(db, t))
            out[t.upper().strip()] = _attach_internal_target(db, facts, t)
        return out
    finally:
        if close:
            db.close()


def coverage_pct(facts: Dict[str, Any]) -> float:
    if not facts:
        return 0.0
    keys = [k for k in facts if k not in ("coverage_pct", "top_headlines")]
    if not keys:
        return 0.0
    full = sum(1 for k in keys if isinstance(facts.get(k), dict) and facts[k].get("coverage") == "full")
    return round(full / len(keys), 3)
