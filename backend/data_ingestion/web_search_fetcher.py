"""Web search for Research Analyst — Tavily or Brave → cache + external_analyst_items."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from app.core.config import SEARCH_API_KEY, SEARCH_API_PROVIDER, SEARCH_MAX_RESULTS
from app.database import ExternalAnalystItem, SessionLocal, WebSearchCache
from data_ingestion.analyst_content_fetcher import _now, _upsert_item


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()


def _cache_get(db, query: str, max_age_hours: int = 24) -> Optional[List[dict]]:
    h = _query_hash(query)
    row = db.query(WebSearchCache).filter(WebSearchCache.query_hash == h).first()
    if not row:
        return None
    try:
        fetched = datetime.strptime(row.fetched_at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    if datetime.utcnow() - fetched > timedelta(hours=max_age_hours):
        return None
    try:
        return json.loads(row.results_json)
    except Exception:
        return None


def _cache_put(db, query: str, results: List[dict]) -> None:
    h = _query_hash(query)
    existing = db.query(WebSearchCache).filter(WebSearchCache.query_hash == h).first()
    payload = json.dumps(results)
    if existing:
        existing.results_json = payload
        existing.fetched_at = _now()
        existing.query_text = query
    else:
        db.add(WebSearchCache(
            query_hash=h,
            query_text=query,
            results_json=payload,
            fetched_at=_now(),
        ))


def _search_tavily(query: str) -> List[dict]:
    if not SEARCH_API_KEY:
        return []
    body = {
        "api_key": SEARCH_API_KEY,
        "query": query,
        "max_results": SEARCH_MAX_RESULTS,
        "include_answer": False,
    }
    r = requests.post("https://api.tavily.com/search", json=body, timeout=30)
    r.raise_for_status()
    out = []
    for item in r.json().get("results") or []:
        out.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "excerpt": (item.get("content") or "")[:800],
            "published_at": item.get("published_date"),
        })
    return out


def _search_brave(query: str) -> List[dict]:
    if not SEARCH_API_KEY:
        return []
    headers = {"X-Subscription-Token": SEARCH_API_KEY, "Accept": "application/json"}
    params = {"q": query, "count": SEARCH_MAX_RESULTS}
    r = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers=headers,
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for item in (r.json().get("web") or {}).get("results") or []:
        out.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "excerpt": (item.get("description") or "")[:800],
            "published_at": item.get("page_age"),
        })
    return out


def search_web(query: str, db=None, force: bool = False) -> List[dict]:
    """Return normalized search snippets; uses cache unless force=True."""
    if not SEARCH_API_KEY:
        return []

    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        if not force:
            cached = _cache_get(db, query)
            if cached is not None:
                return cached

        provider = (SEARCH_API_PROVIDER or "tavily").lower()
        if provider == "brave":
            results = _search_brave(query)
            source = "brave"
        else:
            results = _search_tavily(query)
            source = "tavily"

        if results:
            _cache_put(db, query, results)
            db.commit()
        return results
    except Exception:
        return _cache_get(db, query, max_age_hours=168) or []
    finally:
        if close:
            db.close()


def promote_search_results(
    db,
    results: List[dict],
    *,
    ticker: Optional[str] = None,
    sector_id: Optional[str] = None,
    provider: Optional[str] = None,
) -> int:
    """Insert search snippets into external_analyst_items for citation."""
    prov = provider or (SEARCH_API_PROVIDER or "tavily")
    source = f"{prov}:search"
    added = 0
    for item in results:
        title = item.get("title") or "Web result"
        if _upsert_item(
            db,
            ticker=ticker,
            sector_id=sector_id,
            source=source,
            source_url=item.get("url"),
            published_at=item.get("published_at"),
            title=title[:500],
            excerpt=(item.get("excerpt") or "")[:1200],
            raw_json=json.dumps(item)[:4000],
        ):
            added += 1
    if added:
        db.commit()
    return added


def build_research_query(user_query: str, tickers: List[str]) -> str:
    """Compose a search query from user text + tickers."""
    parts = [user_query.strip()]
    if tickers:
        parts.append(" ".join(tickers[:6]))
    parts.append("stock market news analyst")
    return " ".join(p for p in parts if p)


def fetch_for_research(
    user_query: str,
    tickers: List[str],
    db=None,
    *,
    force: bool = False,
) -> List[ExternalAnalystItem]:
    """Search web, cache, promote items, return freshly readable rows."""
    if not SEARCH_API_KEY:
        return []

    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        q = build_research_query(user_query, tickers)
        results = search_web(q, db=db, force=force)
        if not results:
            return []

        primary = tickers[0] if tickers else None
        promote_search_results(db, results, ticker=primary, provider=SEARCH_API_PROVIDER or "tavily")

        ids = []
        prov = f"{(SEARCH_API_PROVIDER or 'tavily')}:search"
        for item in results:
            from data_ingestion.analyst_content_fetcher import _dedup_id

            sid = _dedup_id(prov, primary or "", item.get("title") or "", item.get("published_at") or "")
            row = (
                db.query(ExternalAnalystItem)
                .filter(ExternalAnalystItem.source == prov, ExternalAnalystItem.source_id == sid)
                .first()
            )
            if row:
                ids.append(row.id)

        if not ids:
            return []
        return (
            db.query(ExternalAnalystItem)
            .filter(ExternalAnalystItem.id.in_(ids))
            .order_by(ExternalAnalystItem.id.asc())
            .all()
        )
    finally:
        if close:
            db.close()
