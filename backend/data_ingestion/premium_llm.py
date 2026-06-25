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
    PREMIUM_LLM_MODEL, PREMIUM_BODY_CHARS, PREMIUM_REL_MIN, PREMIUM_ABS_MIN, PREMIUM_MAX_MENTIONS,
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
        "You are an equity analyst reading ONE story from a tech newsletter. The SUBJECT line is the "
        "main story; the body may also contain unrelated teasers, ads, or links to OTHER stories — "
        "IGNORE those entirely and score only the MAIN story named in the subject.\n"
        f"From this candidate list ONLY: {', '.join(tickers)}\n"
        "Decide which tickers the MAIN story materially moves over the NEXT FEW TRADING DAYS:\n"
        "- DIRECT: the company is a central subject of the story → rel 0.8-1.0.\n"
        "- INDIRECT: a specific, strong second-order effect (key supplier/customer/competitor, or a "
        "private company whose news clearly moves a listed one, e.g. an OpenAI capex story → NVDA) → "
        "rel 0.3-0.5. Do NOT spray the whole mega-cap AI basket; include an indirect name only if the "
        "effect is concrete and clearly directional.\n"
        "Map company names to tickers ('Nvidia' -> NVDA). Omit anything not in the list. OMIT a ticker "
        "entirely if its expected move is roughly neutral (no clear direction) — do not return s near 0.\n"
        'Return ONLY JSON: {"mentions":[{"ticker":"NVDA","s":<-1..1>,"rel":<0..1>,"direct":true|false,'
        '"why":"<≤12 words>"}, ...]}. s: -1 very bearish … +1 very bullish. Prefer FEW high-conviction '
        "names over many weak ones.\n\n"
        f"SUBJECT: {title}\n\nBODY:\n{body[:PREMIUM_BODY_CHARS]}"
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
    h = hashlib.sha256(f"{url}|{title}|{date}".encode()).hexdigest()[:16]
    return f"prem:{source_tag}:{h}"


def ingest_article(title, body, date, url, source_tag, published_utc=None, db=None):
    """LLM-score one article and upsert per-ticker rows into news_llm_scores.

    `date` is YYYY-MM-DD (publish calendar date, used by the swing features); `published_utc` is the full
    ISO timestamp (preserved for point-in-time/auditing). Rows are tagged source=`premium:<tag>` so they
    can be filtered or weighted differently from headline ('polygon') news. Returns accepted mentions.
    """
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
        candidates = []
        for m in mentions:
            tk = str(m.get("ticker", "")).upper().strip()
            if tk not in valid:
                continue
            try:
                s = max(-1.0, min(1.0, float(m.get("s", 0.0))))
                rel = max(0.0, min(1.0, float(m.get("rel", 0.0))))
            except Exception:
                continue
            # Drop weak/neutral: must clear both the relevance floor AND have a real direction.
            if rel < PREMIUM_REL_MIN or abs(s) < PREMIUM_ABS_MIN:
                continue
            candidates.append({"ticker": tk, "s": s, "rel": rel, "direct": bool(m.get("direct", False)),
                               "why": m.get("why", "")})
        # Cap to the strongest few per article (by conviction = rel*|s|) to stop basket-spray.
        candidates.sort(key=lambda c: c["rel"] * abs(c["s"]), reverse=True)
        accepted = candidates[:PREMIUM_MAX_MENTIONS]

        aid = _article_id(source_tag, url, title, date)
        rows = [{"ticker": c["ticker"], "article_id": aid, "date": date,
                 "published_utc": published_utc or date, "title": (title or "")[:300],
                 "llm_score": c["s"], "llm_relevance": c["rel"], "model": model,
                 "source": f"premium:{source_tag}"} for c in accepted]
        if rows:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            db.execute(sqlite_insert(NewsLLMScore).values(rows).on_conflict_do_nothing(
                index_elements=["ticker", "article_id"]))
            db.commit()
        return accepted
    finally:
        if own:
            db.close()
