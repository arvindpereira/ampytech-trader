"""LLM multi-ticker scoring of full premium articles (e.g. The Information) into news_llm_scores.

Unlike news_llm.py (per-ticker headline scoring), a premium article is free text that may mention several
of your universe companies — or a private company (OpenAI, Stripe) whose news clearly moves a listed one.
This module asks the LLM to read the article and return, for ANY universe tickers it materially affects,
a directional score + relevance, then upserts those into news_llm_scores so they feed the swing model
exactly like headline scores (swing_alpha relevance-weights all rows by (ticker, date)).

Only the derived scores + a short title are stored in the DB — not the full article text.
"""
import sys
import os
import json
import hashlib
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, NewsLLMScore, UniverseTicker
from app.core.config import (
    TICKER_UNIVERSE, OLLAMA_URL, LLM_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL,
    PREMIUM_LLM_MODEL, PREMIUM_BODY_CHARS, PREMIUM_REL_MIN,
)
from app.core.llm_cost import estimate_cost, record_usage


def _universe(db):
    rows = db.query(UniverseTicker).all()
    tickers = [t.ticker for t in rows] if rows else list(TICKER_UNIVERSE)
    return [t for t in tickers if not t.startswith(("X:", "C:")) and t != "SPACE"]


def _resolve_provider_model():
    if OPENAI_API_KEY:
        return "openai", (PREMIUM_LLM_MODEL or OPENAI_MODEL)
    return "ollama", (PREMIUM_LLM_MODEL or LLM_MODEL)


def _prompt(title, body, tickers):
    return (
        "You are an equity analyst. Read the article and decide which of these US-listed tickers it "
        "materially affects over the NEXT FEW TRADING DAYS — directly (the company is discussed) or "
        "indirectly (a clear knock-on, e.g. a key supplier/customer/competitor, or a private company "
        "whose news clearly moves a listed one).\n"
        f"Candidate tickers (ONLY use these): {', '.join(tickers)}\n"
        "Map well-known company names to their ticker (e.g. 'Nvidia' -> NVDA). Ignore anything not in the list.\n"
        'Return ONLY JSON: {"mentions":[{"ticker":"NVDA","s":<float -1..1>,"rel":<float 0..1>,'
        '"why":"<≤12 words>"}, ...]}. s: -1 very bearish, 0 neutral, +1 very bullish. rel: 0 not material, '
        "1 highly material. Only include tickers you are reasonably confident are materially affected.\n\n"
        f"TITLE: {title}\n\nARTICLE:\n{body[:PREMIUM_BODY_CHARS]}"
    )


def _call_llm(prompt, provider, model):
    """Return (mentions_list, usage). mentions = [{ticker,s,rel,why}]."""
    if provider == "openai":
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "temperature": 0, "response_format": {"type": "json_object"}}
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=120)
        r.raise_for_status()
        j = r.json()
        data = json.loads(j["choices"][0]["message"]["content"])
        u = j.get("usage", {}) or {}
        return data.get("mentions", []), {"prompt": u.get("prompt_tokens", 0), "completion": u.get("completion_tokens", 0)}
    # ollama
    r = requests.post(f"{OLLAMA_URL}/api/generate",
                      json={"model": model, "prompt": "/no_think\n" + prompt, "stream": False,
                            "format": "json", "options": {"temperature": 0, "num_predict": 600}},
                      timeout=180)
    resp = r.json()
    data = json.loads(resp.get("response", "") or "{}")
    return data.get("mentions", []), {"prompt": resp.get("prompt_eval_count", 0) or 0,
                                      "completion": resp.get("eval_count", 0) or 0}


def _article_id(source_tag, url, title, date):
    h = hashlib.sha1(f"{url}|{title}|{date}".encode()).hexdigest()[:16]
    return f"prem:{source_tag}:{h}"


def ingest_article(title, body, date, url, source_tag, db=None):
    """LLM-score one article and upsert per-ticker rows into news_llm_scores.
    Returns the list of accepted mentions [{ticker,s,rel,why}]. `date` is YYYY-MM-DD (publish date)."""
    own = db is None
    db = db or SessionLocal()
    try:
        provider, model = _resolve_provider_model()
        tickers = _universe(db)
        try:
            mentions, usage = _call_llm(_prompt(title, body or "", tickers), provider, model)
        except Exception as e:
            print(f"  premium LLM scoring failed: {str(e)[:160]}")
            return []
        record_usage("premium_scoring", model, usage.get("prompt", 0), usage.get("completion", 0),
                     provider=provider, requests=1)

        valid = set(tickers)
        aid = _article_id(source_tag, url, title, date)
        rows, accepted = [], []
        for m in mentions:
            tk = str(m.get("ticker", "")).upper().strip()
            if tk not in valid:
                continue
            try:
                s = max(-1.0, min(1.0, float(m.get("s", 0.0))))
                rel = max(0.0, min(1.0, float(m.get("rel", 0.0))))
            except Exception:
                continue
            if rel < PREMIUM_REL_MIN:
                continue
            rows.append({"ticker": tk, "article_id": aid, "date": date, "published_utc": date,
                         "title": (title or "")[:300], "llm_score": s, "llm_relevance": rel,
                         "model": f"premium:{source_tag}"})
            accepted.append({"ticker": tk, "s": s, "rel": rel, "why": m.get("why", "")})
        if rows:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            db.execute(sqlite_insert(NewsLLMScore).values(rows).on_conflict_do_nothing(
                index_elements=["ticker", "article_id"]))
            db.commit()
        return accepted
    finally:
        if own:
            db.close()
