"""Ingest external analyst/news items into external_analyst_items."""
import hashlib
import json
import time
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import (
    FINNHUB_API_KEY,
    MASSIVE_API_KEY,
    MASSIVE_BASE_URL,
    RESEARCH_KB_FINNHUB_SLEEP,
)
from app.database import ExternalAnalystItem, NewsLLMScore, SessionLocal


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _dedup_id(source: str, ticker: str, title: str, published_at: str) -> str:
    raw = f"{source}|{ticker}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _upsert_item(db, **kwargs) -> bool:
    sid = kwargs.get("source_id") or _dedup_id(
        kwargs["source"], kwargs.get("ticker") or "", kwargs.get("title") or "", kwargs.get("published_at") or ""
    )
    kwargs["source_id"] = sid
    existing = (
        db.query(ExternalAnalystItem)
        .filter(ExternalAnalystItem.source == kwargs["source"], ExternalAnalystItem.source_id == sid)
        .first()
    )
    if existing:
        return False
    db.add(ExternalAnalystItem(created_at=_now(), **kwargs))
    return True


def promote_news_headlines(db, ticker: str, limit: int = 5) -> int:
    """Promote top news_llm_scores headlines to external_analyst_items."""
    rows = (
        db.query(NewsLLMScore)
        .filter(NewsLLMScore.ticker == ticker.upper())
        .order_by(NewsLLMScore.published_utc.desc())
        .limit(50)
        .all()
    )
    rows = sorted(rows, key=lambda r: abs((r.llm_score or 0) * (r.llm_relevance or 0)), reverse=True)[:limit]
    added = 0
    for r in rows:
        title = (r.title or "")[:300]
        excerpt = f"LLM score {r.llm_score:+.2f} (relevance {r.llm_relevance:.2f})"
        if _upsert_item(
            db,
            ticker=ticker.upper(),
            source="news_llm",
            source_url=None,
            published_at=r.published_utc,
            title=title,
            excerpt=excerpt,
            raw_json=json.dumps({"article_id": r.article_id, "date": r.date}),
        ):
            added += 1
    return added


def _finnhub_get(path: str, params: dict) -> Optional[dict]:
    if not FINNHUB_API_KEY:
        return None
    params = {**params, "token": FINNHUB_API_KEY}
    try:
        r = requests.get(f"https://finnhub.io/api/v1{path}", params=params, timeout=20)
        if r.status_code in (401, 403, 404):
            return None
        r.raise_for_status()
        time.sleep(RESEARCH_KB_FINNHUB_SLEEP)
        return r.json()
    except Exception:
        return None


def fetch_finnhub_consensus(db, ticker: str) -> int:
    added = 0
    pt = _finnhub_get("/stock/price-target", {"symbol": ticker})
    if pt:
        text = json.dumps(pt)[:2000]
        if _upsert_item(
            db,
            ticker=ticker,
            source="finnhub:price-target",
            published_at=datetime.utcnow().date().isoformat(),
            title=f"{ticker} consensus price target",
            excerpt=text,
            raw_json=json.dumps(pt),
        ):
            added += 1
    rec = _finnhub_get("/stock/recommendation", {"symbol": ticker})
    if rec and isinstance(rec, list) and rec:
        latest = rec[0]
        if _upsert_item(
            db,
            ticker=ticker,
            source="finnhub:recommendation",
            published_at=latest.get("period") or datetime.utcnow().date().isoformat(),
            title=f"{ticker} recommendation trend",
            excerpt=json.dumps(latest)[:2000],
            raw_json=json.dumps(latest),
        ):
            added += 1
    return added


def _massive_rating_rows(ticker: str) -> List[dict]:
    if not MASSIVE_API_KEY:
        return []
    url = f"{MASSIVE_BASE_URL}/benzinga/v1/analyst-ratings"
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
    try:
        r = requests.get(url, params={"ticker": ticker, "limit": "20", "sort": "date.desc"}, headers=headers, timeout=25)
        if r.status_code in (401, 403, 404):
            return []
        r.raise_for_status()
        return (r.json() or {}).get("results") or []
    except Exception:
        return []


def fetch_massive_ratings(db, ticker: str) -> int:
    added = 0
    for rec in _massive_rating_rows(ticker):
        firm = rec.get("analyst") or rec.get("firm") or rec.get("analyst_name")
        rating = rec.get("rating") or rec.get("action_company") or rec.get("analyst_rating")
        pt = rec.get("price_target") or rec.get("target_price") or rec.get("pt")
        pub = rec.get("date") or rec.get("published") or rec.get("created_at")
        title = f"{firm or 'Analyst'}: {rating or 'rating'}"
        if _upsert_item(
            db,
            ticker=ticker,
            source="massive:benzinga",
            source_url=rec.get("url"),
            published_at=str(pub)[:10] if pub else None,
            title=title[:300],
            excerpt=json.dumps({k: rec.get(k) for k in ("rating", "action_company", "price_target") if rec.get(k)})[:2000],
            analyst_firm=str(firm)[:120] if firm else None,
            rating=str(rating)[:60] if rating else None,
            target_price=float(pt) if isinstance(pt, (int, float)) else None,
            raw_json=json.dumps(rec)[:4000],
        ):
            added += 1
    return added


def refresh_ticker(db, ticker: str) -> dict:
    ticker = ticker.upper().strip()
    stats = {"news": 0, "finnhub": 0, "massive": 0}
    stats["news"] = promote_news_headlines(db, ticker)
    stats["finnhub"] = fetch_finnhub_consensus(db, ticker)
    stats["massive"] = fetch_massive_ratings(db, ticker)
    db.commit()
    return stats


def refresh(tickers: Iterable[str], db=None) -> dict:
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    total = {"tickers": 0, "items_added": 0}
    try:
        for t in sorted({x.upper().strip() for x in tickers if x}):
            s = refresh_ticker(db, t)
            total["tickers"] += 1
            total["items_added"] += sum(s.values())
        return total
    finally:
        if close:
            db.close()


def recent_items(db, ticker: str, limit: int = 20) -> List[ExternalAnalystItem]:
    return (
        db.query(ExternalAnalystItem)
        .filter(ExternalAnalystItem.ticker == ticker.upper())
        .order_by(ExternalAnalystItem.published_at.desc(), ExternalAnalystItem.id.desc())
        .limit(limit)
        .all()
    )
