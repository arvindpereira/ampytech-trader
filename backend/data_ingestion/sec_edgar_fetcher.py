"""SEC EDGAR 8-K filing fetcher.

Downloads 8-K filings from EDGAR for the active universe, constructs a short
descriptive headline from the item numbers, scores it with the same LLM pipeline
used for news, and stores results in `news_llm_scores` with source='sec:8k'.

8-K filings are the most reliable free signal source going back to ~1993:
  - Item 2.02 = earnings (results of operations) — biggest mover
  - Item 7.01 = Reg FD disclosure (guidance, forward-looking statements)
  - Item 1.01 = material agreements (acquisitions, partnerships, contracts)
  - Item 5.02 = executive changes (CEO departures are often bearish)

Usage:
  make fetch-8k [START=2015-01-01] [TICKERS=NVDA,AAPL]
  python data_ingestion/sec_edgar_fetcher.py --start 2015-01-01

Rate limits: SEC requires a User-Agent header and allows 10 req/sec max.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import OPENAI_API_KEY, TICKER_UNIVERSE

# SEC EDGAR requires a descriptive User-Agent (company + contact email).
_EDGAR_UA = "AmPyTech Trading Research bot@ampytech.com"
_EDGAR_HEADERS = {"User-Agent": _EDGAR_UA, "Accept": "application/json"}
_SLEEP = 0.12   # stay well under the 10 req/sec limit

# 8-K item number → human-readable description (for constructing scoreable headlines)
_ITEM_DESC = {
    "1.01": "Entry into Material Definitive Agreement",
    "1.02": "Termination of Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety Disclosure",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",  # ← earnings
    "2.03": "Creation of Direct Financial Obligation",
    "2.04": "Triggering Events Affecting Covered Securities",
    "2.05": "Cost Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modifications to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Certain Officers",           # ← exec changes
    "5.03": "Amendment to Articles of Incorporation or Bylaws",
    "5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "5.05": "Amendment to Registrant's Code of Ethics",
    "5.07": "Submission of Matters to Vote of Security Holders",
    "5.08": "Exempt Solicitation",
    "6.01": "ABS Informational and Computational Material",
    "7.01": "Regulation FD Disclosure",                            # ← guidance
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# Items that are mostly boilerplate / exhibits — skip scoring these when they
# are the ONLY item (e.g. a pure 9.01 contains only a press release attachment).
_BOILERPLATE_ONLY = {"9.01", "8.01"}


def _cik_from_ticker(ticker: str) -> Optional[str]:
    """Look up the 10-digit CIK for a ticker symbol via the SEC company tickers file.
    Downloads once and caches locally."""
    cache_path = os.path.join(os.path.dirname(__file__), ".sec_cik_cache.json")
    cache: dict = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    if ticker.upper() in cache:
        return cache[ticker.upper()]

    # Fetch the SEC master company tickers list (JSON, ~500 KB, cached after first download)
    try:
        r = requests.get("https://www.sec.gov/files/company_tickers.json",
                         headers=_EDGAR_HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        # Structure: {"0": {"cik_str": "1045810", "ticker": "NVDA", "title": "..."}, ...}
        for entry in data.values():
            t = (entry.get("ticker") or "").upper()
            cik = str(entry.get("cik_str") or "").zfill(10)
            if t:
                cache[t] = cik
        with open(cache_path, "w") as f:
            json.dump(cache, f)
        time.sleep(_SLEEP)
    except Exception as e:
        print(f"  EDGAR CIK lookup failed: {e}")
        return None

    return cache.get(ticker.upper())


def _fetch_filings_for_cik(cik: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch all 8-K filings for a CIK within [start_date, end_date].
    Handles pagination via the 'files' list for older submissions."""
    results = []
    urls = [f"https://data.sec.gov/submissions/CIK{cik}.json"]
    seen_files: set = set()

    while urls:
        url = urls.pop(0)
        try:
            r = requests.get(url, headers=_EDGAR_HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            time.sleep(_SLEEP)
        except Exception as e:
            print(f"  EDGAR fetch error ({url}): {e}")
            continue

        filings = data.get("filings") or {}
        recent = filings.get("recent") or {}
        forms         = recent.get("form", [])
        dates         = recent.get("filingDate", [])
        accessions    = recent.get("accessionNumber", [])
        items_list    = recent.get("items", [])

        for form, date, acc, items in zip(forms, dates, accessions, items_list):
            if form != "8-K":
                continue
            if date < start_date or date > end_date:
                continue
            results.append({
                "accession": acc,
                "date": date,
                "items": items or "",
            })

        # Queue older submission files (each covers ~40 older filings)
        for f in (filings.get("files") or []):
            fname = f.get("name") or ""
            if fname and fname not in seen_files:
                seen_files.add(fname)
                urls.append(f"https://data.sec.gov/submissions/{fname}")

    return results


def _filing_headline(ticker: str, date: str, items_str: str) -> Optional[str]:
    """Build a concise, scoreable headline from the 8-K item string."""
    if not items_str:
        return None
    item_codes = [i.strip() for i in items_str.replace(";", ",").split(",") if i.strip()]
    # Remove pure-boilerplate items unless they are the only item
    meaningful = [c for c in item_codes if c not in _BOILERPLATE_ONLY]
    codes_to_use = meaningful if meaningful else item_codes
    if not codes_to_use:
        return None
    descs = [_ITEM_DESC.get(c, f"Item {c}") for c in codes_to_use]
    return f"{ticker} SEC 8-K ({date}): {'; '.join(descs)}"


def fetch_and_score_8k(
    tickers: Optional[list] = None,
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    provider: str = "openai",
    dry_run: bool = False,
) -> dict:
    """Main entry point: fetch 8-K filings for `tickers`, score each with the LLM,
    and upsert into `news_llm_scores` with source='sec:8k'.

    Returns {'tickers_processed': N, 'filings_new': M, 'filings_skipped': K}
    """
    from app.database import NewsLLMScore, SessionLocal, UniverseTicker, init_db
    from data_ingestion.news_llm import score_batch, _upsert_scores
    from app.core.config import OPENAI_MODEL, LLM_MODEL
    from app.core.llm_cost import record_usage

    init_db()
    db = SessionLocal()
    end_date = end_date or datetime.now().strftime("%Y-%m-%d")
    model = OPENAI_MODEL if provider == "openai" else LLM_MODEL

    if tickers is None:
        rows = db.query(UniverseTicker.ticker).all()
        tickers = [r.ticker for r in rows] if rows else list(TICKER_UNIVERSE)

    stats = {"tickers_processed": 0, "filings_new": 0, "filings_skipped": 0}
    print(f"📋 SEC EDGAR 8-K fetch: {len(tickers)} tickers | {start_date}→{end_date}")

    try:
        for ticker in tickers:
            cik = _cik_from_ticker(ticker)
            if not cik:
                print(f"  {ticker}: no CIK found — skipping")
                continue

            # Check which accession numbers are already in DB for this ticker+source
            existing_ids = {
                r.article_id for r in
                db.query(NewsLLMScore.article_id)
                  .filter(NewsLLMScore.ticker == ticker,
                          NewsLLMScore.source == "sec:8k")
                  .all()
            }

            # Determine incremental start: use the latest scored 8-K date minus 3 days
            latest = (db.query(NewsLLMScore.date)
                        .filter(NewsLLMScore.ticker == ticker,
                                NewsLLMScore.source == "sec:8k")
                        .order_by(NewsLLMScore.date.desc()).first())
            incr_start = start_date
            if latest and latest[0]:
                try:
                    d = datetime.strptime(latest[0][:10], "%Y-%m-%d") - timedelta(days=3)
                    incr_start = max(start_date, d.strftime("%Y-%m-%d"))
                except Exception:
                    pass

            filings = _fetch_filings_for_cik(cik, incr_start, end_date)
            new_filings = [f for f in filings
                           if f"sec8k:{f['accession']}" not in existing_ids]

            if not new_filings:
                stats["filings_skipped"] += len(filings)
                continue

            print(f"  {ticker}: {len(filings)} filings found → {len(new_filings)} new to score")
            headlines = []
            for f in new_filings:
                text = _filing_headline(ticker, f["date"], f["items"])
                if not text:
                    stats["filings_skipped"] += 1
                    continue
                aid = f"sec8k:{f['accession']}"
                pub = f"{f['date']}T16:00:00Z"  # 8-Ks typically filed after market close
                headlines.append((aid, pub, text))

            if not headlines:
                continue
            if dry_run:
                for aid, pub, text in headlines:
                    print(f"    [dry-run] {text}")
                stats["filings_new"] += len(headlines)
                continue

            # Score in chunks of 50
            chunk_size = 50
            for i in range(0, len(headlines), chunk_size):
                chunk = headlines[i: i + chunk_size]
                scores, usage = score_batch(ticker, chunk, provider, model)
                if usage:
                    record_usage(db, provider, model, "sec_8k_scoring",
                                 usage.get("prompt", 0), usage.get("completion", 0))
                rows_to_upsert = []
                for (aid, pub, title), (score, relevance) in zip(chunk, scores):
                    rows_to_upsert.append({
                        "ticker": ticker,
                        "article_id": aid,
                        "date": pub[:10],
                        "published_utc": pub,
                        "title": title,
                        "llm_score": score,
                        "llm_relevance": relevance,
                        "model": model,
                        "source": "sec:8k",
                    })
                _upsert_scores(db, rows_to_upsert)
                db.commit()
                stats["filings_new"] += len(chunk)

            stats["tickers_processed"] += 1

    finally:
        db.close()

    print(f"\n✅ 8-K scoring complete: {stats['filings_new']} new | "
          f"{stats['filings_skipped']} skipped | {stats['tickers_processed']} tickers")
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SEC EDGAR 8-K fetcher + LLM scorer")
    parser.add_argument("--start",    default="2015-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",      default=None,         help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--tickers",  default=None,         help="Comma-separated tickers (default: full universe)")
    parser.add_argument("--provider", default="openai",     help="LLM provider: openai | ollama")
    parser.add_argument("--dry-run",  action="store_true",  help="Print headlines without scoring")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    fetch_and_score_8k(
        tickers=tickers,
        start_date=args.start,
        end_date=args.end,
        provider=args.provider,
        dry_run=args.dry_run,
    )
