"""LLM-scored news headlines for the SWING (multi-day) strategy.

Fetches Polygon/Massive news per ticker, scores each headline's directional impact on THAT ticker over
the next few trading days, and stores per-headline scores in `news_llm_scores`. Point-in-time: scores
are keyed on the publish date and shifted +1 day in feature engineering, so no look-ahead.

Scoring provider is pluggable (`NEWS_LLM_PROVIDER`):
  - "ollama" (default): local gemma4:e4b — free + private; used by the recurring daily/intraday jobs.
  - "openai": gpt-4o-mini via REST — a fast opt-in for bulk backfills (10-50x faster; ~<$1 full backfill).
Both run many batches concurrently. An optional OpenAI Batch-API path (--batch) is the cheapest
unattended way to do a one-time huge backfill.

Usage:
  python data_ingestion/news_llm.py --start 2023-01-01 --tickers AAPL,NVDA            # local Ollama
  python data_ingestion/news_llm.py --start 2021-01-01 --provider openai              # fast backfill
  python data_ingestion/news_llm.py --start 2021-01-01 --provider openai --batch      # cheapest, unattended
  python data_ingestion/news_llm.py --collect batch_abc123                            # resume batch ingest
"""
import sys
import os
import time
import json
import argparse
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, NewsLLMScore, UniverseTicker
from app.core.config import (
    TICKER_UNIVERSE, MASSIVE_API_KEY, MASSIVE_BASE_URL,
    OLLAMA_URL, LLM_MODEL, NEWS_LLM_START,
    NEWS_LLM_PROVIDER, NEWS_LLM_WORKERS, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL,
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_DATA_URL, NEWS_USE_ALPACA,
)
from app.core.text import normalize_headline

from app.core.llm_cost import estimate_cost, record_usage

NON_NEWS = {"SPACE"}   # fictional ticker, no real news
BATCH = 15             # headlines per LLM call (sweet spot for gemma4:e4b and gpt-4o-mini)
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _est_cost(model, prompt_tok, completion_tok, batch=False):
    return estimate_cost(model, prompt_tok, completion_tok, batch=batch) or 0.0


def _fmt_tok(n):
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return str(int(n))


def _bar(frac, width=22):
    fill = int(frac * width)
    return "[" + "#" * fill + "-" * (width - fill) + "]"


# --------------------------------------------------------------------------------------------------
# Prompt + parsing (shared across providers so scores are comparable)
# --------------------------------------------------------------------------------------------------
def _build_prompt(ticker, headlines, no_think=False):
    numbered = "\n".join(f"{i+1}. {h[:240]}" for i, h in enumerate(headlines))
    return (
        ("/no_think\n" if no_think else "")
        + f"You are an equity analyst. For the stock {ticker}, rate how each headline is likely to move "
        f"{ticker}'s share price over the NEXT FEW TRADING DAYS.\n"
        'Return ONLY JSON: {"scores":[{"i":1,"s":<float -1..1>,"rel":<float 0..1>}, ...]} with one entry '
        "per headline. s: -1 very bearish, 0 neutral, +1 very bullish. rel: 0 if the headline is not "
        f"materially about {ticker}, 1 if directly and materially about it.\nHeadlines:\n{numbered}"
    )


def _parse_scores(text, n):
    """Parse a model's JSON response into a list of (score, relevance) aligned to the n headlines."""
    data = json.loads(text or "{}")
    scores = {int(e["i"]): (float(e.get("s", 0.0)), float(e.get("rel", 0.0)))
              for e in data.get("scores", []) if "i" in e}
    return [scores.get(i + 1, (0.0, 0.0)) for i in range(n)]


# --------------------------------------------------------------------------------------------------
# Providers — each returns a list of (score, relevance) aligned to `headlines`
# --------------------------------------------------------------------------------------------------
def _ollama_score(ticker, headlines, model=None, retries=2):
    """Returns (scores, usage) — usage = {'prompt': n, 'completion': n} token counts."""
    model = model or LLM_MODEL
    prompt = _build_prompt(ticker, headlines, no_think=True)
    for _ in range(retries):
        try:
            r = requests.post(f"{OLLAMA_URL}/api/generate",
                              json={"model": model, "prompt": prompt, "stream": False, "format": "json",
                                    "options": {"temperature": 0, "num_predict": 40 * len(headlines) + 60}},
                              timeout=180)
            data = r.json()
            usage = {"prompt": data.get("prompt_eval_count", 0) or 0,
                     "completion": data.get("eval_count", 0) or 0}
            return _parse_scores(data.get("response", ""), len(headlines)), usage
        except Exception:
            time.sleep(0.5)
    return [(0.0, 0.0)] * len(headlines), {"prompt": 0, "completion": 0}


def _openai_score(ticker, headlines, model=None, retries=4):
    """Score a batch via OpenAI chat completions (JSON mode). Backs off on 429/5xx."""
    model = model or OPENAI_MODEL
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; cannot use the openai provider.")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": _build_prompt(ticker, headlines)}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    for attempt in range(retries):
        try:
            r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=120)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = float(r.headers.get("Retry-After", 0)) or min(2 ** attempt, 30)
                time.sleep(wait)
                continue
            r.raise_for_status()
            j = r.json()
            content = j["choices"][0]["message"]["content"]
            u = j.get("usage", {}) or {}
            usage = {"prompt": u.get("prompt_tokens", 0), "completion": u.get("completion_tokens", 0)}
            return _parse_scores(content, len(headlines)), usage
        except Exception:
            time.sleep(min(2 ** attempt, 30))
    return [(0.0, 0.0)] * len(headlines), {"prompt": 0, "completion": 0}


def score_batch(ticker, headlines, provider, model):
    """Returns (scores, usage). usage has 'prompt'/'completion' token counts."""
    if provider == "openai":
        return _openai_score(ticker, headlines, model=model)
    return _ollama_score(ticker, headlines, model=model)


# --------------------------------------------------------------------------------------------------
# News fetch — pluggable sources (Polygon/Massive + Alpaca/Benzinga), merged + deduped by headline
# --------------------------------------------------------------------------------------------------
def _alpaca_enabled():
    return NEWS_USE_ALPACA and bool(ALPACA_API_KEY and ALPACA_SECRET_KEY)


def _news_source_available():
    return bool(MASSIVE_API_KEY) or _alpaca_enabled()


def _norm_title(t):
    """Normalize a headline for cross-source dedup (lowercase, collapse non-alphanumerics)."""
    return normalize_headline(t)


def _fetch_news_massive(ticker, start, end, headers, max_pages=80):
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


def _fetch_news_alpaca(ticker, start, end, max_pages=80):
    """Pages Alpaca's (Benzinga) news for a ticker in [start,end]. Free with any Alpaca account.
    Returns [(article_id, published_utc, title)] with article_ids prefixed 'alpaca:' so they never
    collide with Polygon ids in the (ticker, article_id) primary key."""
    enc = urllib.parse.quote(ticker)
    base = (f"{ALPACA_DATA_URL}/v1beta1/news?symbols={enc}"
            f"&start={start}T00:00:00Z&end={end}T23:59:59Z"
            f"&sort=asc&limit=50")
    headers = {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}
    out, token, pages = [], None, 0
    while pages < max_pages:
        url = base + (f"&page_token={urllib.parse.quote(token)}" if token else "")
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code != 200:
                break
            j = r.json()
            for a in (j.get("news") or []):
                aid = a.get("id")
                if aid is None:
                    continue
                pub = a.get("created_at", "") or a.get("updated_at", "") or ""
                out.append((f"alpaca:{aid}", pub, a.get("headline", "") or ""))
            token = j.get("next_page_token")
            pages += 1
            if not token:
                break
            time.sleep(0.15)
        except Exception:
            break
    return out


def _fetch_news(ticker, start, end, headers, max_pages=80):
    """Combined per-ticker news from Polygon/Massive + Alpaca, deduped by normalized headline
    (Polygon kept on collision). Returns [(article_id, published_utc, title)]."""
    massive = _fetch_news_massive(ticker, start, end, headers, max_pages=max_pages) if MASSIVE_API_KEY else []
    if not _alpaca_enabled():
        return massive
    alpaca = _fetch_news_alpaca(ticker, start, end, max_pages=max_pages)
    if not alpaca:
        return massive
    seen = {_norm_title(t) for _, _, t in massive}
    merged = list(massive)
    for aid, pub, title in alpaca:
        key = _norm_title(title)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append((aid, pub, title))
    return merged


# --------------------------------------------------------------------------------------------------
# Work building + DB write (shared by the concurrent and batch paths)
# --------------------------------------------------------------------------------------------------
def _resolve(provider, model):
    provider = provider or NEWS_LLM_PROVIDER
    if model is None:
        model = OPENAI_MODEL if provider == "openai" else LLM_MODEL
    return provider, model


def _build_work(db, start, end, tickers):
    """Serial fetch + dedup. Returns (work, total_headlines) where each work item is
    (ticker, idx, chunk_meta) and chunk_meta is a list of (article_id, published_utc, title)."""
    start = start or NEWS_LLM_START
    end = end or datetime.now().strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
    if tickers is None:
        db_tickers = db.query(UniverseTicker).all()
        tickers = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)
    tickers = [t for t in tickers if t not in NON_NEWS and not t.startswith(("X:", "C:"))]
    print(f"🔎 Fetching news for {len(tickers)} ticker(s), {start}→{end} (skipping already-scored)…")

    work, total, with_news = [], 0, 0
    for ticker in tickers:
        # Incremental: resume from the latest already-scored date minus 2 days to avoid refetching old news.
        ticker_start = start
        latest_rec = (db.query(NewsLLMScore).filter(NewsLLMScore.ticker == ticker)
                      .order_by(NewsLLMScore.published_utc.desc()).first())
        if latest_rec and latest_rec.published_utc:
            try:
                latest_dt = datetime.strptime(latest_rec.published_utc[:10], "%Y-%m-%d")
                safe_start = (latest_dt - timedelta(days=2)).strftime("%Y-%m-%d")
                if not start or safe_start > start:
                    ticker_start = safe_start
            except Exception:
                pass

        arts = _fetch_news(ticker, ticker_start, end, headers)
        if not arts:
            continue
        # Skip anything already stored: by exact article_id, AND by (date, normalized headline) so we
        # don't pay to re-score a story another source already gave us on a prior run (cross-source dedup).
        stored = db.query(NewsLLMScore.article_id, NewsLLMScore.date, NewsLLMScore.title).filter(
            NewsLLMScore.ticker == ticker).all()
        existing_ids = {r[0] for r in stored}
        existing_titles = {(r[1], normalize_headline(r[2])) for r in stored if r[2]}
        seen_titles = set()
        todo = []
        for aid, pub, title in arts:
            if aid in existing_ids:
                continue
            key = (((pub or "")[:10]), normalize_headline(title))
            if key[1] and (key in existing_titles or key in seen_titles):
                continue
            if key[1]:
                seen_titles.add(key)
            todo.append((aid, pub, title))
        if todo:
            with_news += 1
            print(f"   • {ticker}: {len(arts)} fetched → {len(todo)} new to score")
        for idx, k in enumerate(range(0, len(todo), BATCH)):
            chunk = todo[k:k + BATCH]
            work.append((ticker, idx, chunk))
            total += len(chunk)
    print(f"🔎 Fetch complete: {total} new headlines across {with_news} ticker(s) → {len(work)} batches.")
    return work, total


def _rows_for(ticker, chunk, results, model):
    return [{"ticker": ticker, "article_id": aid, "date": (pub or "")[:10], "published_utc": pub,
             "title": (title or "")[:300], "llm_score": s, "llm_relevance": rel, "model": model,
             "source": "alpaca" if str(aid).startswith("alpaca:") else "polygon"}
            for (aid, pub, title), (s, rel) in zip(chunk, results)]


def _upsert_scores(db, rows):
    """Idempotent insert (skip duplicate (ticker, article_id)) — batches complete out of order."""
    if not rows:
        return
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    stmt = sqlite_insert(NewsLLMScore).values(rows).on_conflict_do_nothing(
        index_elements=["ticker", "article_id"])
    db.execute(stmt)


# --------------------------------------------------------------------------------------------------
# Concurrent (real-time) path
# --------------------------------------------------------------------------------------------------
def fetch_and_score(start=None, end=None, tickers=None, model=None, provider=None, workers=None,
                    progress_cb=None):
    """Fetch + score news per ticker, concurrently, upserting into news_llm_scores (skips already-scored).

    `progress_cb(fraction, note)` is called as batches complete (fraction 0..1) for UI progress bars."""
    if not _news_source_available():
        print("No news source configured; set MASSIVE_API_KEY and/or Alpaca keys (NEWS_USE_ALPACA=True).")
        return
    provider, model = _resolve(provider, model)
    if workers is None:
        workers = NEWS_LLM_WORKERS if provider == "openai" else int(os.getenv("OLLAMA_NUM_PARALLEL", "2"))

    print(f"🗞️  News-LLM scoring | provider={provider} model={model} | {workers} concurrent workers")
    init_db()
    db = SessionLocal()
    try:
        work, total = _build_work(db, start, end, tickers)
        if not work:
            print("✅ Nothing new to score — all headlines already scored.")
            if progress_cb:
                progress_cb(1.0, "no new headlines")
            return
        print(f"⏳ Scoring {total} headlines in {len(work)} batches…")

        t0 = time.time()
        done, grand, ptok, ctok = 0, 0, 0, 0
        log_every = max(1, len(work) // 20)   # ~20 status lines over the run
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(score_batch, ticker, [t for _, _, t in chunk], provider, model):
                    (ticker, chunk) for ticker, _idx, chunk in work}
            for fut in as_completed(futs):
                ticker, chunk = futs[fut]
                try:
                    results, usage = fut.result()
                except Exception:
                    results, usage = [(0.0, 0.0)] * len(chunk), {"prompt": 0, "completion": 0}
                _upsert_scores(db, _rows_for(ticker, chunk, results, model))
                done += 1
                grand += len(chunk)
                ptok += usage.get("prompt", 0)
                ctok += usage.get("completion", 0)
                if done % 10 == 0 or done == len(work):
                    db.commit()
                frac = done / len(work)
                if done % log_every == 0 or done == len(work):
                    el = time.time() - t0
                    rate = grand / el if el > 0 else 0
                    eta = (total - grand) / rate if rate > 0 else 0
                    tok = f" | {_fmt_tok(ptok + ctok)} tok" if (ptok + ctok) else ""
                    cost = f" | ~${_est_cost(model, ptok, ctok):.3f}" if provider == "openai" else ""
                    print(f"  {_bar(frac)} {frac * 100:4.0f}% | {grand}/{total} headlines"
                          f"{tok}{cost} | {rate:.0f} hl/s | ETA {eta:3.0f}s")
                if progress_cb:
                    note = f"scored {grand}/{total} headlines ({ticker})"
                    if provider == "openai" and (ptok + ctok):
                        note += f" • {_fmt_tok(ptok + ctok)} tok • ~${_est_cost(model, ptok, ctok):.2f}"
                    progress_cb(frac, note)
        db.commit()
        if ptok or ctok:
            record_usage("news_scoring", model, ptok, ctok, provider=provider, requests=len(work))
        el = time.time() - t0
        summary = f"✅ Scored {grand} headlines in {el:.1f}s ({grand / el:.0f} hl/s)" if el > 0 else \
                  f"✅ Scored {grand} headlines"
        if provider == "openai":
            summary += (f" | tokens: {_fmt_tok(ptok)} in + {_fmt_tok(ctok)} out = {_fmt_tok(ptok + ctok)}"
                        f" | est cost ~${_est_cost(model, ptok, ctok):.4f}")
        elif (ptok + ctok):
            summary += f" | {_fmt_tok(ptok + ctok)} tokens"
        print(summary + "\n")
    finally:
        db.close()


# --------------------------------------------------------------------------------------------------
# OpenAI Batch-API path (cheapest, unattended)
# --------------------------------------------------------------------------------------------------
def _openai_headers():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; cannot use the OpenAI Batch API.")
    return {"Authorization": f"Bearer {OPENAI_API_KEY}"}


def submit_batch(start=None, end=None, tickers=None, model=None):
    """Build all work, upload a JSONL, and create an OpenAI Batch job. Returns the batch id.
    Persists the custom_id -> chunk-meta map to backend/data/news_batch_<id>.json for collection."""
    if not _news_source_available():
        print("No news source configured; set MASSIVE_API_KEY and/or Alpaca keys (NEWS_USE_ALPACA=True).")
        return None
    _, model = _resolve("openai", model)
    init_db()
    db = SessionLocal()
    try:
        work, total = _build_work(db, start, end, tickers)
    finally:
        db.close()
    if not work:
        print("Batch: nothing new to score.")
        return None

    os.makedirs(_DATA_DIR, exist_ok=True)
    lines, cmap = [], {}
    for ticker, idx, chunk in work:
        cid = f"{ticker}|{idx}"
        cmap[cid] = chunk
        lines.append(json.dumps({
            "custom_id": cid, "method": "POST", "url": "/v1/chat/completions",
            "body": {"model": model,
                     "messages": [{"role": "user",
                                   "content": _build_prompt(ticker, [t for _, _, t in chunk])}],
                     "temperature": 0, "response_format": {"type": "json_object"}},
        }))
    jsonl = "\n".join(lines).encode()
    print(f"Batch: uploading {len(work)} requests / {total} headlines ({model})...")

    fid = requests.post(f"{OPENAI_BASE_URL}/files", headers=_openai_headers(),
                        files={"file": ("news_batch.jsonl", jsonl)},
                        data={"purpose": "batch"}, timeout=120).json()["id"]
    batch = requests.post(f"{OPENAI_BASE_URL}/batches", headers=_openai_headers(),
                          json={"input_file_id": fid, "endpoint": "/v1/chat/completions",
                                "completion_window": "24h"}, timeout=60).json()
    batch_id = batch["id"]
    with open(os.path.join(_DATA_DIR, f"news_batch_{batch_id}.json"), "w") as f:
        json.dump({"batch_id": batch_id, "model": model, "map": cmap}, f)
    print(f"Batch submitted: {batch_id} (status {batch.get('status')}). "
          f"Collect with: --collect {batch_id}")
    return batch_id


def collect_batch(batch_id, poll_seconds=20):
    """Poll a batch to completion, then ingest its results into news_llm_scores."""
    map_path = os.path.join(_DATA_DIR, f"news_batch_{batch_id}.json")
    if not os.path.exists(map_path):
        print(f"No saved map for {batch_id} ({map_path}); cannot ingest.")
        return
    saved = json.load(open(map_path))
    cmap, model = saved["map"], saved["model"]

    while True:
        b = requests.get(f"{OPENAI_BASE_URL}/batches/{batch_id}", headers=_openai_headers(),
                         timeout=60).json()
        status, counts = b.get("status"), b.get("request_counts", {})
        print(f"  batch {batch_id}: {status} "
              f"({counts.get('completed', 0)}/{counts.get('total', 0)})")
        if status in ("completed", "failed", "expired", "cancelled"):
            break
        time.sleep(poll_seconds)
    if status != "completed":
        print(f"Batch {batch_id} ended as {status}; nothing ingested.")
        return

    out = requests.get(f"{OPENAI_BASE_URL}/files/{b['output_file_id']}/content",
                       headers=_openai_headers(), timeout=120).text
    init_db()
    db = SessionLocal()
    grand, ptok, ctok = 0, 0, 0
    try:
        for i, line in enumerate(out.splitlines()):
            if not line.strip():
                continue
            rec = json.loads(line)
            cid = rec.get("custom_id")
            chunk = cmap.get(cid)
            if not chunk:
                continue
            ticker = cid.split("|")[0]
            try:
                body = rec["response"]["body"]
                content = body["choices"][0]["message"]["content"]
                results = _parse_scores(content, len(chunk))
                u = body.get("usage", {}) or {}
                ptok += u.get("prompt_tokens", 0)
                ctok += u.get("completion_tokens", 0)
            except Exception:
                results = [(0.0, 0.0)] * len(chunk)
            _upsert_scores(db, _rows_for(ticker, [tuple(c) for c in chunk], results, model))
            grand += len(chunk)
            if i % 50 == 0:
                db.commit()
        db.commit()
    finally:
        db.close()
    if ptok or ctok:
        record_usage("news_scoring", model, ptok, ctok, provider="openai",
                     requests=len(cmap), batch=True)
    print(f"✅ Batch {batch_id} ingested: {grand} headline scores | "
          f"tokens: {_fmt_tok(ptok)} in + {_fmt_tok(ctok)} out | "
          f"est cost ~${_est_cost(model, ptok, ctok, batch=True):.4f} (Batch API, 50% off)\n")


def fetch_and_score_batch(start=None, end=None, tickers=None, model=None):
    batch_id = submit_batch(start=start, end=end, tickers=tickers, model=model)
    if batch_id:
        collect_batch(batch_id)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="LLM-score news headlines for the swing model")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--tickers", default=None, help="comma-separated; default = universe")
    p.add_argument("--model", default=None)
    p.add_argument("--provider", default=None, choices=["ollama", "openai"])
    p.add_argument("--workers", type=int, default=None)
    p.add_argument("--batch", action="store_true", help="use the OpenAI Batch API (cheapest, unattended)")
    p.add_argument("--collect", default=None, help="ingest a previously-submitted batch id")
    a = p.parse_args()
    tk = a.tickers.split(",") if a.tickers else None
    if a.collect:
        collect_batch(a.collect)
    elif a.batch:
        fetch_and_score_batch(start=a.start, end=a.end, tickers=tk, model=a.model)
    else:
        fetch_and_score(start=a.start, end=a.end, tickers=tk, model=a.model,
                        provider=a.provider, workers=a.workers)
