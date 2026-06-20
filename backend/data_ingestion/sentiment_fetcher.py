import sys
import os
from datetime import datetime, timedelta
import random
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    TICKER_UNIVERSE,
    NEWS_API_KEY,
    FINNHUB_API_KEY,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    MASSIVE_API_KEY,
    MASSIVE_BASE_URL,
    NEWS_HISTORY_START,
)
from app.database import init_db, SessionLocal, TickerSentiment, SentimentSourceLog, UniverseTicker

def get_active_universe(db):
    db_tickers = db.query(UniverseTicker).all()
    return [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE

def get_praw_reddit():
    """Tries to initialize praw. Returns None if credentials missing or fails."""
    if not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None
    try:
        import praw
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT
        )
        # Test connection quickly
        reddit.read_only = True
        return reddit
    except Exception as e:
        print(f"Warning: Reddit PRAW failed to initialize: {e}")
        return None

def score_and_log_sources(db, ticker, date_str, source, items, is_mock=False):
    """
    Computes sentiment for each individual item, logs it in SentimentSourceLog,
    and returns the aggregated average score, positive ratio, and negative ratio.
    """
    if not items:
        return 0.0, 0.0, 0.0

    analyzer = SentimentIntensityAnalyzer()
    scores = []
    pos_count = 0
    neg_count = 0

    # Clear existing source logs for this ticker, date, and source to prevent duplicates on rerun
    db.query(SentimentSourceLog).filter(
        SentimentSourceLog.ticker == ticker,
        SentimentSourceLog.date == date_str,
        SentimentSourceLog.source == source
    ).delete()

    for item in items:
        title = item.get("title", "")
        text = item.get("text", "")
        url = item.get("url", "")

        # Calculate VADER polarity or use pre-analyzed score if available
        pre_score = item.get("pre_score")
        if pre_score is not None:
            compound = pre_score
        else:
            full_content = (title + ". " + text) if text else title
            vs = analyzer.polarity_scores(full_content)
            compound = vs['compound']
        scores.append(compound)

        if compound > 0.05:
            pos_count += 1
        elif compound < -0.05:
            neg_count += 1

        # Write individual source log
        log_rec = SentimentSourceLog(
            ticker=ticker,
            date=date_str,
            source=source,
            title=title[:250],
            text=text[:1000] if text else None,
            url=url,
            score=compound,
            is_mock=is_mock
        )
        db.add(log_rec)

    db.commit()

    avg_score = sum(scores) / len(scores)
    pos_ratio = pos_count / len(items)
    neg_ratio = neg_count / len(items)

    return avg_score, pos_ratio, neg_ratio

def fetch_reddit_sentiment(db, date_str):
    reddit = get_praw_reddit()
    if not reddit:
        print("Reddit credentials missing or invalid. Skipping live Reddit fetch.")
        return False

    print("Fetching live Reddit sentiment from r/wallstreetbets and r/stocks...")
    try:
        active_universe = get_active_universe(db)
        # Collect recent submissions
        submissions_objs = []
        for sub_name in ["wallstreetbets", "stocks"]:
            subreddit = reddit.subreddit(sub_name)
            for submission in subreddit.hot(limit=100):
                submissions_objs.append(submission)

        # Group submissions by ticker mention
        ticker_items = {ticker: [] for ticker in active_universe}
        for sub in submissions_objs:
            title = sub.title
            text = sub.selftext or ""
            url = f"https://www.reddit.com{sub.permalink}"

            upper_content = (title + " " + text).upper()
            for ticker in active_universe:
                # Add word boundary check
                if f" {ticker} " in f" {upper_content} ":
                    ticker_items[ticker].append({
                        "title": title,
                        "text": text[:500],
                        "url": url
                    })

        # Score each ticker and save aggregates
        for ticker, items in ticker_items.items():
            if not items:
                continue

            score, pos, neg = score_and_log_sources(db, ticker, date_str, "reddit", items)

            existing = db.query(TickerSentiment).filter(
                TickerSentiment.ticker == ticker,
                TickerSentiment.date == date_str,
                TickerSentiment.source == "reddit"
            ).first()

            if existing:
                existing.sentiment_score = score
                existing.positive_ratio = pos
                existing.negative_ratio = neg
                existing.mention_count = len(items)
            else:
                s_rec = TickerSentiment(
                    ticker=ticker,
                    date=date_str,
                    sentiment_score=score,
                    positive_ratio=pos,
                    negative_ratio=neg,
                    mention_count=len(items),
                    source="reddit"
                )
                db.add(s_rec)
        db.commit()
        print("Reddit sentiment update complete.")
        return True
    except Exception as e:
        print(f"Failed to fetch Reddit data: {e}")
        db.rollback()
        return False

def fetch_single_ticker_news(ticker, date_str):
    """Fetches news for a single ticker from the active configured API."""
    import time
    if MASSIVE_API_KEY:
        import urllib.parse
        start_date_utc = f"{date_str}T00:00:00Z"
        ticker_encoded = urllib.parse.quote(ticker)
        url = f"{MASSIVE_BASE_URL}/v2/reference/news?ticker={ticker_encoded}&published_utc_gte={start_date_utc}&limit=20"
        headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}

        max_retries = 5
        backoff_sec = 2.0
        res = None

        for attempt in range(max_retries):
            try:
                # Sleep a little to prevent hitting rate limits too aggressively in parallel
                time.sleep(0.1)
                res = requests.get(url, headers=headers, timeout=15)
                if res.status_code == 429:
                    print(f"Rate limited (429) on news for {ticker}. Retrying in {backoff_sec} seconds...")
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue
                res.raise_for_status()
                articles = res.json().get("results", [])

                items = []
                for art in articles:
                    title = art.get("title", "")
                    desc = art.get("description", "") or art.get("summary", "") or ""
                    link = art.get("url", "")

                    pre_score = None
                    insights = art.get("insights", [])
                    for insight in insights:
                        if insight.get("ticker", "").upper() == ticker.upper():
                            sent = insight.get("sentiment", "").lower()
                            if sent == "positive":
                                pre_score = 0.8
                            elif sent == "negative":
                                pre_score = -0.8
                            elif sent == "neutral":
                                pre_score = 0.0
                            break

                    items.append({
                        "title": title,
                        "text": desc[:500],
                        "url": link,
                        "pre_score": pre_score
                    })
                return ticker, items
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Failed to fetch Massive news for {ticker}: {e}")
                    return ticker, []
                time.sleep(backoff_sec)
                backoff_sec *= 2.0

    elif NEWS_API_KEY:
        url = f"https://newsapi.org/v2/everything?q={ticker}&pageSize=20&apiKey={NEWS_API_KEY}"
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                articles = res.json().get("articles", [])
                items = []
                for art in articles:
                    title = art.get("title", "")
                    desc = art.get("description", "")
                    link = art.get("url", "")
                    items.append({
                        "title": title,
                        "text": desc[:500] if desc else "",
                        "url": link
                    })
                return ticker, items
        except Exception as e:
            print(f"Failed to fetch NewsAPI news for {ticker}: {e}")
            return ticker, []

    elif FINNHUB_API_KEY:
        end_t = datetime.now()
        start_t = end_t - timedelta(days=1)
        start_str = start_t.strftime("%Y-%m-%d")
        end_str = end_t.strftime("%Y-%m-%d")
        url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={start_str}&to={end_str}&token={FINNHUB_API_KEY}"
        try:
            res = requests.get(url, timeout=15)
            if res.status_code == 200:
                news = res.json()
                items = []
                for item in news[:20]:
                    items.append({
                        "title": item.get("headline", ""),
                        "text": item.get("summary", "")[:500],
                        "url": item.get("url", "")
                    })
                return ticker, items
        except Exception as e:
            print(f"Failed to fetch Finnhub news for {ticker}: {e}")
            return ticker, []

    return ticker, []

def fetch_news_sentiment(db, date_str):
    if not MASSIVE_API_KEY and not NEWS_API_KEY and not FINNHUB_API_KEY:
        print("No API keys found for live news fetch (MASSIVE_API_KEY, NEWS_API_KEY, or FINNHUB_API_KEY). Skipping live news fetch.")
        return False

    print("Fetching news sentiment from live API in parallel...")
    try:
        active_universe = get_active_universe(db)
        ticker_items = {ticker: [] for ticker in active_universe}

        from concurrent.futures import ThreadPoolExecutor, as_completed

        print(f"Launching parallel news fetching for {len(active_universe)} tickers...")
        total_tickers = len(active_universe)
        completed = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(fetch_single_ticker_news, ticker, date_str): ticker
                for ticker in active_universe
            }

            for future in as_completed(futures):
                completed += 1
                ticker = futures[future]
                percent = int(completed / total_tickers * 100)
                try:
                    ticker_res, items = future.result()
                    if items:
                        ticker_items[ticker_res] = items
                    print(f"[News Sentiment Progress: {percent}%] Completed {completed}/{total_tickers} - {ticker} ({len(items) if items else 0} articles)", flush=True)
                except Exception as e:
                    print(f"[News Sentiment Progress: {percent}%] Error in parallel news fetch for {ticker}: {e}", flush=True)

        # Score and store sequentially on main thread
        print("Writing sentiment records to database sequentially...", flush=True)
        total_to_write = sum(1 for items in ticker_items.values() if items)
        written = 0
        for ticker, items in ticker_items.items():
            if not items:
                continue

            written += 1
            percent = int(written / total_to_write * 100)
            print(f"[News DB Progress: {percent}%] Writing {written}/{total_to_write} - {ticker}", flush=True)
            score, pos, neg = score_and_log_sources(db, ticker, date_str, "news", items)

            existing = db.query(TickerSentiment).filter(
                TickerSentiment.ticker == ticker,
                TickerSentiment.date == date_str,
                TickerSentiment.source == "news"
            ).first()

            if existing:
                existing.sentiment_score = score
                existing.positive_ratio = pos
                existing.negative_ratio = neg
                existing.mention_count = len(items)
            else:
                s_rec = TickerSentiment(
                    ticker=ticker,
                    date=date_str,
                    sentiment_score=score,
                    positive_ratio=pos,
                    negative_ratio=neg,
                    mention_count=len(items),
                    source="news"
                )
                db.add(s_rec)
        db.commit()
        print("News sentiment update complete.")
        return True
    except Exception as e:
        print(f"Failed to fetch News data: {e}")
        db.rollback()
        return False


def fetch_premium_news_sentiment(db, date_str):
    """
    Scans backend/data/premium_news/ for text files, analyzes sentiment,
    logs items to SentimentSourceLog, aggregates under source 'premium',
    and updates TickerSentiment.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    premium_dir = os.path.join(base_dir, "data", "premium_news")
    archive_dir = os.path.join(premium_dir, "archive")
    os.makedirs(premium_dir, exist_ok=True)
    os.makedirs(archive_dir, exist_ok=True)

    files = [f for f in os.listdir(premium_dir) if f.endswith(('.txt', '.md'))]
    if not files:
        return

    print(f"Found {len(files)} premium articles to process in premium_news/.")
    active_universe = get_active_universe(db)
    ticker_items = {ticker: [] for ticker in active_universe}

    for filename in files:
        filepath = os.path.join(premium_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            detected_ticker = None
            filename_upper = filename.upper()

            # Match ticker in filename
            for ticker in active_universe:
                if ticker in filename_upper:
                    detected_ticker = ticker
                    break

            # Fallback to scanning content
            if not detected_ticker:
                for ticker in active_universe:
                    if f" {ticker} " in f" {content.upper()} ":
                        detected_ticker = ticker
                        break

            if not detected_ticker:
                print(f"Skipping premium file {filename}: no ticker detected.")
                continue

            title = filename.replace(".txt", "").replace(".md", "").replace("_", " ")

            ticker_items[detected_ticker].append({
                "title": title,
                "text": content[:1000],
                "url": "local-premium-upload"
            })

            # Move to archive folder
            dest_filename = f"{date_str}_{filename}"
            os.rename(filepath, os.path.join(archive_dir, dest_filename))
            print(f"Processed and archived premium file: {filename} for ticker {detected_ticker}")

        except Exception as e:
            print(f"Error processing premium file {filename}: {e}")

    # Write to db
    for ticker, items in ticker_items.items():
        if not items:
            continue

        score, pos, neg = score_and_log_sources(db, ticker, date_str, "premium", items)

        existing = db.query(TickerSentiment).filter(
            TickerSentiment.ticker == ticker,
            TickerSentiment.date == date_str,
            TickerSentiment.source == "premium"
        ).first()

        if existing:
            existing.sentiment_score = score
            existing.positive_ratio = pos
            existing.negative_ratio = neg
            existing.mention_count = len(items)
        else:
            s_rec = TickerSentiment(
                ticker=ticker,
                date=date_str,
                sentiment_score=score,
                positive_ratio=pos,
                negative_ratio=neg,
                mention_count=len(items),
                source="premium"
            )
            db.add(s_rec)
    db.commit()

def generate_mock_sentiment(db, date_str):
    """Generates realistic mock sentiment scores for active tickers if APIs are unavailable."""
    print("Generating simulated sentiment data (no keys present)...")

    new_records = 0
    active_universe = get_active_universe(db)
    for ticker in active_universe:
        for source in ["news", "reddit"]:
            existing = db.query(TickerSentiment).filter(
                TickerSentiment.date == date_str,
                TickerSentiment.ticker == ticker,
                TickerSentiment.source == source
            ).first()

            if existing:
                continue

            if ticker in ["TSLA", "NVDA", "AMD"]:
                score = random.uniform(-0.6, 0.8)
                mentions = random.randint(3, 8)
            elif ticker in ["SPY", "QQQ"]:
                score = random.uniform(-0.1, 0.4)
                mentions = random.randint(4, 9)
            else:
                score = random.uniform(-0.3, 0.5)
                mentions = random.randint(2, 5)

            pos = max(0.0, score + random.uniform(0.0, 0.3)) if score > 0 else random.uniform(0.0, 0.2)
            neg = max(0.0, -score + random.uniform(0.0, 0.3)) if score < 0 else random.uniform(0.0, 0.2)

            # Seed mock individual source items so the UI still displays sample details
            mock_items = []
            for m_idx in range(mentions):
                headline_templates = [
                    f"Market outlook positive for {ticker} amid strong earnings expectations",
                    f"Analysis: Why {ticker} could see a break out in volume soon",
                    f"Retail trading discussion on {ticker} options gains momentum",
                    f"Reports: {ticker} launches product extension following regulatory approval",
                    f"Technical charts show {ticker} trading key levels of support"
                ] if score > 0 else [
                    f"Sellers gain control over {ticker} as volatility metrics rise",
                    f"Why {ticker} struggles to gain traction in high interest rate regime",
                    f"Retail options volume spikes on {ticker} downside puts",
                    f"Reports of regulatory headwinds limit momentum for {ticker}",
                    f"Technical charts show {ticker} break below moving average levels"
                ]
                if source == "news":
                    domains = ["bloomberg.com", "reuters.com", "cnbc.com", "finance.yahoo.com"]
                    domain = random.choice(domains)
                    item_url = f"https://{domain}/news/{ticker.lower()}-market-activity-{date_str}"
                else:  # reddit
                    subreddits = ["wallstreetbets", "stocks", "options", "investing"]
                    sub = random.choice(subreddits)
                    item_url = f"https://www.reddit.com/r/{sub}/comments/{ticker.lower()}_discussion_thread/"

                mock_items.append({
                    "title": random.choice(headline_templates),
                    "text": f"Simulated detail logs for {ticker} tracking indicators and market social buzz.",
                    "url": item_url
                })

            score, pos, neg = score_and_log_sources(db, ticker, date_str, source, mock_items, is_mock=True)

            s_rec = TickerSentiment(
                ticker=ticker,
                date=date_str,
                sentiment_score=score,
                positive_ratio=pos,
                negative_ratio=neg,
                mention_count=mentions,
                source=source,
                is_mock=True
            )
            db.add(s_rec)
            new_records += 1

    db.commit()
    print(f"Successfully simulated sentiment scores and logged sources. Added {new_records} records.")

def _insight_score(article, ticker):
    """Returns the publisher's pre-computed sentiment for `ticker` if present, else None."""
    for ins in (article.get("insights") or []):
        if ins.get("ticker", "").upper() == ticker.upper():
            s = ins.get("sentiment", "").lower()
            return {"positive": 0.8, "negative": -0.8, "neutral": 0.0}.get(s)
    return None


def backfill_news_sentiment(start_date_str=None):
    """Backfills DAILY news sentiment per ticker from Massive/Polygon news history
    (~2021->now), scoring each article with the publisher insight when available else VADER,
    and upserting aggregates into TickerSentiment(source='news', is_mock=False).

    Aggregates only (no per-article logs) to keep the table lean over years of history.
    """
    import urllib.parse
    from data_ingestion.price_fetcher import _massive_get

    if not MASSIVE_API_KEY:
        print("No MASSIVE_API_KEY; cannot backfill historical news. Skipping.")
        return

    init_db()
    db = SessionLocal()
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
    analyzer = SentimentIntensityAnalyzer()

    start = start_date_str or NEWS_HISTORY_START
    end = datetime.now().strftime("%Y-%m-%d")
    universe = get_active_universe(db)
    print(f"Backfilling news sentiment for {len(universe)} tickers ({start} -> {end})...")

    for idx, ticker in enumerate(universe, 1):
        percent = int((idx - 1) / len(universe) * 100)
        print(f"[News Backfill Progress: {percent}%] Processing ticker {idx}/{len(universe)} - {ticker}...", flush=True)
        enc = urllib.parse.quote(ticker)
        url = (f"{MASSIVE_BASE_URL}/v2/reference/news?ticker={enc}"
               f"&published_utc.gte={start}T00:00:00Z&published_utc.lte={end}T23:59:59Z"
               f"&order=asc&sort=published_utc&limit=1000")
        daily = {}   # cal_date -> [compound, ...]
        pages = 0
        while url:
            data = _massive_get(url, headers, ticker)
            if data is None:
                break
            for art in (data.get("results") or []):
                pub = (art.get("published_utc") or "")[:10]
                if not pub:
                    continue
                score = _insight_score(art, ticker)
                if score is None:
                    text = (art.get("title", "") or "") + ". " + (art.get("description", "") or "")
                    score = analyzer.polarity_scores(text)["compound"]
                daily.setdefault(pub, []).append(score)
            url = data.get("next_url")
            pages += 1
            if pages > 500:
                print(f"  {ticker}: stopping at {pages} pages.")
                break

        if not daily:
            print(f"  {ticker}: no historical news found.")
            continue

        # Upsert daily aggregates
        for date_str, scores in daily.items():
            avg = sum(scores) / len(scores)
            pos = sum(1 for s in scores if s > 0.05) / len(scores)
            neg = sum(1 for s in scores if s < -0.05) / len(scores)
            existing = db.query(TickerSentiment).filter(
                TickerSentiment.ticker == ticker,
                TickerSentiment.date == date_str,
                TickerSentiment.source == "news",
            ).first()
            if existing:
                existing.sentiment_score = avg
                existing.positive_ratio = pos
                existing.negative_ratio = neg
                existing.mention_count = len(scores)
                existing.is_mock = False
            else:
                db.add(TickerSentiment(
                    ticker=ticker, date=date_str, sentiment_score=avg,
                    positive_ratio=pos, negative_ratio=neg, mention_count=len(scores),
                    source="news", is_mock=False,
                ))
        db.commit()
        total_articles = sum(len(v) for v in daily.values())
        print(f"  {ticker}: {len(daily)} days, {total_articles} articles, {pages} pages.")

    db.close()
    print("News sentiment backfill complete.\n")


def fetch_sentiment():
    init_db()
    db = SessionLocal()

    today = datetime.now()
    yesterday = today - timedelta(days=1)

    for dt in [yesterday, today]:
        date_str = dt.strftime("%Y-%m-%d")
        print(f"\n--- Processing Sentiment for {date_str} ---")

        # 1. Premium news folder scanner runs first so it can process uploads on any day
        fetch_premium_news_sentiment(db, date_str)

        # Check database count to avoid API limit hits
        existing_count = db.query(TickerSentiment).filter(
            TickerSentiment.date == date_str,
            TickerSentiment.source != "premium"  # Ignore premium for standard api locks
        ).count()

        if existing_count >= 40:
            print(f"Sentiment records for {date_str} are already complete in SQLite database. Skipping API checks.")
            continue

        news_fetched = fetch_news_sentiment(db, date_str)
        reddit_fetched = fetch_reddit_sentiment(db, date_str)

        if not news_fetched or not reddit_fetched:
            print(f"Warning: News or Reddit API sentiment fetch was incomplete/failed for {date_str}. Mock fallback is disabled.")

    db.close()
    print("\nSentiment processing completed.\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sentiment ingestion")
    parser.add_argument("--backfill", action="store_true",
                        help="Backfill historical daily news sentiment (~2021->now) from Massive/Polygon.")
    parser.add_argument("--start", type=str, default=None, help="Backfill start date (YYYY-MM-DD).")
    args = parser.parse_args()

    if args.backfill:
        backfill_news_sentiment(start_date_str=args.start)
    else:
        fetch_sentiment()
