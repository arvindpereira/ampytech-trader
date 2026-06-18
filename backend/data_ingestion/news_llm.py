"""LLM-scored news headlines for the SWING (multi-day) strategy.

Fetches Polygon/Massive news per ticker, scores each headline's directional impact on THAT ticker over
the next few trading days with a local Ollama model (default gemma4:e4b — fast, JSON-clean, free), and
stores per-headline scores in `news_llm_scores`. Point-in-time: scores are keyed on the publish date and
shifted +1 day in feature engineering, so no look-ahead.

Usage:
  ALT/Polygon key in .env, Ollama running locally.
  python data_ingestion/news_llm.py --start 2023-01-01 --tickers AAPL,NVDA
"""
import sys
import os
import time
import json
import argparse
import urllib.parse
from datetime import datetime, timedelta
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, NewsLLMScore, UniverseTicker
from app.core.config import (
    TICKER_UNIVERSE, MASSIVE_API_KEY, MASSIVE_BASE_URL,
    OLLAMA_URL, LLM_MODEL, NEWS_LLM_START,
)

NON_NEWS = {"SPACE"}   # fictional ticker, no real news
BATCH = 15             # headlines per LLM call (sweet spot for gemma4:e4b)


def _ollama_score(ticker, headlines, model=None, retries=2):
    """Scores a batch of headlines for `ticker`. Returns list of (score, relevance) aligned to input."""
    model = model or LLM_MODEL
    numbered = "\n".join(f"{i+1}. {h[:240]}" for i, h in enumerate(headlines))
    prompt = (
        "/no_think\n"
        f"You are an equity analyst. For the stock {ticker}, rate how each headline is likely to move "
        f"{ticker}'s share price over the NEXT FEW TRADING DAYS.\n"
        'Return ONLY JSON: {"scores":[{"i":1,"s":<float -1..1>,"rel":<float 0..1>}, ...]} with one entry '
        "per headline. s: -1 very bearish, 0 neutral, +1 very bullish. rel: 0 if the headline is not "
        f"materially about {ticker}, 1 if directly and materially about it.\nHeadlines:\n{numbered}"
    )
    for _ in range(retries):
        try:
            r = requests.post(f"{OLLAMA_URL}/api/generate",
                              json={"model": model, "prompt": prompt, "stream": False, "format": "json",
                                    "options": {"temperature": 0, "num_predict": 40 * len(headlines) + 60}},
                              timeout=180)
            data = json.loads(r.json().get("response", "") or "{}")
            scores = {int(e["i"]): (float(e.get("s", 0.0)), float(e.get("rel", 0.0)))
                      for e in data.get("scores", []) if "i" in e}
            return [scores.get(i + 1, (0.0, 0.0)) for i in range(len(headlines))]
        except Exception:
            time.sleep(0.5)
    return [(0.0, 0.0)] * len(headlines)


def _fetch_news(ticker, start, end, headers, max_pages=80):
    """Pages Polygon news for a ticker in [start,end]. Returns [(article_id, published_utc, title)]."""
    enc = urllib.parse.quote(ticker)
    url = (f"{MASSIVE_BASE_URL}/v2/reference/news?ticker={enc}"
           f"&published_utc.gte={start}T00:00:00Z&published_utc.lte={end}T23:59:59Z"
           f"&order=asc&sort=published_utc&limit=1000")
    out, pages = [], 0
    while url and pages < max_pages:
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code != 200:
                break
            j = r.json()
            for a in (j.get("results") or []):
                aid = a.get("id") or a.get("article_url") or (a.get("published_utc", "") + a.get("title", "")[:40])
                out.append((str(aid), a.get("published_utc", ""), a.get("title", "") or ""))
            url = j.get("next_url")
            pages += 1
            time.sleep(0.15)
        except Exception:
            break
    return out


def fetch_and_score(start=None, end=None, tickers=None, model=None, progress_cb=None):
    """Fetches + LLM-scores news per ticker, upserting into news_llm_scores (skips already-scored ids).

    `progress_cb(fraction, note)` (optional) is called as batches complete (fraction 0..1 over all
    tickers/batches) so a UI can show a progress bar during long backfills."""
    start = start or NEWS_LLM_START
    end = end or datetime.now().strftime("%Y-%m-%d")
    model = model or LLM_MODEL
    if not MASSIVE_API_KEY:
        print("No MASSIVE_API_KEY; cannot fetch news.")
        return
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}

    init_db()
    db = SessionLocal()
    if tickers is None:
        db_tickers = db.query(UniverseTicker).all()
        tickers = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)
    tickers = [t for t in tickers if t not in NON_NEWS and not t.startswith(("X:", "C:"))]
    print(f"LLM news scoring ({model}) for {len(tickers)} tickers, {start}→{end}...")

    grand = 0
    for ti, ticker in enumerate(tickers):
        # Optimize fetch range: use latest scored news date minus 2 days as fallback start to avoid fetching old news.
        ticker_start = start
        latest_rec = db.query(NewsLLMScore).filter(NewsLLMScore.ticker == ticker).order_by(NewsLLMScore.published_utc.desc()).first()
        if latest_rec and latest_rec.published_utc:
            try:
                latest_dt = datetime.strptime(latest_rec.published_utc[:10], "%Y-%m-%d")
                safe_start_dt = latest_dt - timedelta(days=2)
                safe_start = safe_start_dt.strftime("%Y-%m-%d")
                if not start or safe_start > start:
                    ticker_start = safe_start
            except Exception:
                pass

        arts = _fetch_news(ticker, ticker_start, end, headers)
        if not arts:
            print(f"  {ticker}: no headlines.")
            if progress_cb:
                progress_cb((ti + 1) / len(tickers), f"{ticker}: no headlines")
            continue
        existing = {r[0] for r in db.query(NewsLLMScore.article_id).filter(NewsLLMScore.ticker == ticker).all()}
        todo = [a for a in arts if a[0] not in existing]
        scored = 0
        n_batches = max(1, (len(todo) + BATCH - 1) // BATCH)
        for bi, k in enumerate(range(0, len(todo), BATCH)):
            chunk = todo[k:k + BATCH]
            results = _ollama_score(ticker, [t for _, _, t in chunk], model=model)
            for (aid, pub, title), (s, rel) in zip(chunk, results):
                db.add(NewsLLMScore(ticker=ticker, article_id=aid, date=(pub or "")[:10],
                                    published_utc=pub, title=title[:300], llm_score=s,
                                    llm_relevance=rel, model=model))
                scored += 1
            db.commit()
            if progress_cb:
                frac = (ti + (bi + 1) / n_batches) / len(tickers)
                progress_cb(frac, f"{ticker}: scored {scored}/{len(todo)} new headlines")
        grand += scored
        print(f"  {ticker}: {len(arts)} headlines ({len(todo)} new) → scored {scored}.")
    db.close()
    print(f"LLM news scoring complete: {grand} new headline scores.\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="LLM-score news headlines for the swing model")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--tickers", default=None, help="comma-separated; default = universe")
    p.add_argument("--model", default=None)
    a = p.parse_args()
    tk = a.tickers.split(",") if a.tickers else None
    fetch_and_score(start=a.start, end=a.end, tickers=tk, model=a.model)
