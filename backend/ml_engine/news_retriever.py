"""Phase 2c — BM25 keyword + optional Ollama embedding retrieval over news KB."""
from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import requests

from app.core.config import (
    OLLAMA_URL,
    RESEARCH_EMBED_MODEL,
    RESEARCH_RETRIEVAL_DAYS,
    RESEARCH_RETRIEVAL_ENABLED,
    RESEARCH_RETRIEVAL_LIMIT,
    RESEARCH_RETRIEVAL_MODE,
)
from app.database import ExternalAnalystItem, NewsLLMScore, SessionLocal


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


class _BM25:
    """Lightweight Okapi BM25 over tokenized documents."""

    def __init__(self, corpus: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [_tokenize(c) for c in corpus]
        self.doc_len = [len(d) for d in self.docs]
        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0.0
        df: Dict[str, int] = {}
        for doc in self.docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1
        self.df = df
        self.n = len(self.docs)

    def score(self, query: str) -> List[float]:
        q_terms = _tokenize(query)
        scores: List[float] = []
        for i, doc in enumerate(self.docs):
            s = 0.0
            for term in q_terms:
                if term not in self.df:
                    continue
                tf = doc.count(term)
                idf = math.log(1 + (self.n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[i] / max(self.avgdl, 1))
                s += idf * tf * (self.k1 + 1) / max(denom, 1e-9)
            scores.append(s)
        return scores


def _doc_text(row: NewsLLMScore) -> str:
    parts = [row.title or "", row.ticker or ""]
    if row.source:
        parts.append(row.source)
    return " ".join(parts)


def _candidate_news(db, tickers: List[str], days: int, max_rows: int) -> List[NewsLLMScore]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    q = db.query(NewsLLMScore).filter(NewsLLMScore.date >= cutoff)
    if tickers:
        q = q.filter(NewsLLMScore.ticker.in_([t.upper() for t in tickers]))
    return q.order_by(NewsLLMScore.published_utc.desc()).limit(max_rows).all()


def _candidate_analyst_items(db, tickers: List[str], limit: int) -> List[ExternalAnalystItem]:
    q = db.query(ExternalAnalystItem)
    if tickers:
        q = q.filter(ExternalAnalystItem.ticker.in_([t.upper() for t in tickers]))
    return q.order_by(ExternalAnalystItem.id.desc()).limit(limit).all()


def _ollama_available() -> bool:
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=2).status_code == 200
    except Exception:
        return False


def _embed_text(text: str, model: str) -> Optional[List[float]]:
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": model, "prompt": (text or "")[:2000]},
            timeout=60,
        )
        r.raise_for_status()
        emb = r.json().get("embedding")
        return emb if isinstance(emb, list) else None
    except Exception:
        return None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _load_cached_embedding(db, doc_key: str, model: str) -> Optional[np.ndarray]:
    from app.database import ResearchNewsEmbedding

    row = (
        db.query(ResearchNewsEmbedding)
        .filter(ResearchNewsEmbedding.doc_key == doc_key, ResearchNewsEmbedding.model == model)
        .first()
    )
    if not row or not row.embedding_json:
        return None
    try:
        return np.array(json.loads(row.embedding_json), dtype=np.float32)
    except Exception:
        return None


def _save_embedding(db, doc_key: str, model: str, vec: Sequence[float]) -> None:
    from datetime import datetime

    from app.database import ResearchNewsEmbedding

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    payload = json.dumps([round(float(x), 6) for x in vec])
    row = db.query(ResearchNewsEmbedding).filter(ResearchNewsEmbedding.doc_key == doc_key).first()
    if row:
        row.model = model
        row.embedding_json = payload
        row.updated_at = now
    else:
        db.add(ResearchNewsEmbedding(doc_key=doc_key, model=model, embedding_json=payload, updated_at=now))


def _semantic_rerank(
    db,
    query: str,
    scored_rows: List[Tuple[float, NewsLLMScore]],
    model: str,
    alpha: float = 0.55,
) -> List[Tuple[float, NewsLLMScore]]:
    """Blend BM25 with cosine similarity (hybrid). alpha = weight on semantic score."""
    q_emb = _load_cached_embedding(db, f"query:{hash(query)}", model)
    if q_emb is None:
        q_vec = _embed_text(query, model)
        if not q_vec:
            return scored_rows
        q_emb = np.array(q_vec, dtype=np.float32)
        _save_embedding(db, f"query:{hash(query)}", model, q_vec)
        db.commit()

    bm25_max = max((s for s, _ in scored_rows), default=1.0) or 1.0
    out: List[Tuple[float, NewsLLMScore]] = []
    for bm25_s, row in scored_rows:
        doc_key = f"news:{row.ticker}:{row.article_id}"
        d_emb = _load_cached_embedding(db, doc_key, model)
        if d_emb is None:
            d_vec = _embed_text(_doc_text(row), model)
            if d_vec:
                d_emb = np.array(d_vec, dtype=np.float32)
                _save_embedding(db, doc_key, model, d_vec)
        sem = _cosine(q_emb, d_emb) if d_emb is not None else 0.0
        bm25_norm = bm25_s / bm25_max
        hybrid = (1 - alpha) * bm25_norm + alpha * sem
        out.append((hybrid, row))
    db.commit()
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def search_news_bm25(
    query: str,
    tickers: List[str],
    db=None,
    *,
    limit: Optional[int] = None,
    days: Optional[int] = None,
    mode: Optional[str] = None,
) -> List[NewsLLMScore]:
    """Retrieve query-relevant headlines via BM25 (+ optional semantic re-rank)."""
    if not RESEARCH_RETRIEVAL_ENABLED or not (query or "").strip():
        return []

    limit = limit or RESEARCH_RETRIEVAL_LIMIT
    days = days or RESEARCH_RETRIEVAL_DAYS
    mode = (mode or RESEARCH_RETRIEVAL_MODE or "hybrid").lower()

    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        rows = _candidate_news(db, tickers, days, max_rows=800)
        if not rows:
            return []

        corpus = [_doc_text(r) for r in rows]
        bm25 = _BM25(corpus)
        scores = bm25.score(query)
        ranked = sorted(zip(scores, rows), key=lambda x: x[0], reverse=True)
        ranked = [(s, r) for s, r in ranked if s > 0][: max(limit * 3, 24)]

        if not ranked:
            ranked = sorted(zip(scores, rows), key=lambda x: x[0], reverse=True)[: max(limit * 3, 24)]

        use_semantic = mode in ("semantic", "hybrid") and _ollama_available()
        if use_semantic:
            ranked = _semantic_rerank(db, query, ranked, RESEARCH_EMBED_MODEL)

        return [r for _, r in ranked[:limit]]
    finally:
        if close:
            db.close()


def search_analyst_items_bm25(
    query: str,
    tickers: List[str],
    db=None,
    *,
    limit: int = 8,
) -> List[ExternalAnalystItem]:
    if not RESEARCH_RETRIEVAL_ENABLED or not (query or "").strip():
        return []
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        rows = _candidate_analyst_items(db, tickers, limit=200)
        if not rows:
            return []
        corpus = [f"{r.title or ''} {r.excerpt or ''} {r.ticker or ''}" for r in rows]
        scores = _BM25(corpus).score(query)
        ranked = sorted(zip(scores, rows), key=lambda x: x[0], reverse=True)
        return [r for s, r in ranked if s > 0][:limit] or [r for _, r in ranked[:limit]]
    finally:
        if close:
            db.close()


def promote_retrieved_news(db, rows: List[NewsLLMScore]) -> List[ExternalAnalystItem]:
    """Promote BM25 hits to external_analyst_items and return ORM rows for synthesis."""
    from data_ingestion.analyst_content_fetcher import _lookup_news_url, _upsert_item

    out: List[ExternalAnalystItem] = []
    for r in rows:
        ticker = (r.ticker or "").upper()
        title = (r.title or "")[:300]
        excerpt = f"Retrieved headline — LLM score {r.llm_score:+.2f} (rel {r.llm_relevance:.2f})"
        url = _lookup_news_url(db, ticker, title)
        sid = f"retrieval:{r.article_id}"
        if _upsert_item(
            db,
            ticker=ticker,
            source="news_retrieval",
            source_id=sid,
            source_url=url,
            published_at=r.published_utc or r.date,
            title=title,
            excerpt=excerpt,
            raw_json=json.dumps({"article_id": r.article_id, "retrieval": True}),
        ):
            db.flush()
        item = (
            db.query(ExternalAnalystItem)
            .filter(ExternalAnalystItem.source == "news_retrieval", ExternalAnalystItem.source_id == sid)
            .first()
        )
        if item:
            out.append(item)
    if out:
        db.commit()
    return out


def retrieve_for_query(
    query: str,
    tickers: List[str],
    db=None,
) -> Dict[str, List]:
    """Unified retrieval: BM25 news + analyst items; returns promoted items + news rows."""
    news_rows = search_news_bm25(query, tickers, db=db)
    promoted = promote_retrieved_news(db, news_rows) if news_rows else []
    extra_items = search_analyst_items_bm25(query, tickers, db=db, limit=6)
    return {
        "news_rows": news_rows,
        "promoted_items": promoted,
        "extra_items": extra_items,
    }
