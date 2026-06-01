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
    REDDIT_USER_AGENT
)
from app.database import init_db, SessionLocal, TickerSentiment, SentimentSourceLog

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

def score_and_log_sources(db, ticker, date_str, source, items):
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

        # Calculate VADER polarity
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
            score=compound
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
        # Collect recent submissions
        submissions_objs = []
        for sub_name in ["wallstreetbets", "stocks"]:
            subreddit = reddit.subreddit(sub_name)
            for submission in subreddit.hot(limit=100):
                submissions_objs.append(submission)

        # Group submissions by ticker mention
        ticker_items = {ticker: [] for ticker in TICKER_UNIVERSE}
        for sub in submissions_objs:
            title = sub.title
            text = sub.selftext or ""
            url = f"https://www.reddit.com{sub.permalink}"

            upper_content = (title + " " + text).upper()
            for ticker in TICKER_UNIVERSE:
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

def fetch_news_sentiment(db, date_str):
    if not NEWS_API_KEY and not FINNHUB_API_KEY:
        print("NewsAPI & Finnhub keys missing. Skipping live news fetch.")
        return False

    print("Fetching news sentiment from live API...")
    try:
        ticker_items = {ticker: [] for ticker in TICKER_UNIVERSE}

        if NEWS_API_KEY:
            for ticker in TICKER_UNIVERSE:
                url = f"https://newsapi.org/v2/everything?q={ticker}&pageSize=20&apiKey={NEWS_API_KEY}"
                res = requests.get(url)
                if res.status_code == 200:
                    articles = res.json().get("articles", [])
                    for art in articles:
                        title = art.get("title", "")
                        desc = art.get("description", "")
                        link = art.get("url", "")
                        ticker_items[ticker].append({
                            "title": title,
                            "text": desc[:500] if desc else "",
                            "url": link
                        })

        elif FINNHUB_API_KEY:
            end_t = datetime.now()
            start_t = end_t - timedelta(days=1)
            start_str = start_t.strftime("%Y-%m-%d")
            end_str = end_t.strftime("%Y-%m-%d")

            for ticker in TICKER_UNIVERSE:
                if ticker in ["SPY", "QQQ"]:
                    continue
                url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={start_str}&to={end_str}&token={FINNHUB_API_KEY}"
                res = requests.get(url)
                if res.status_code == 200:
                    news = res.json()
                    for item in news[:20]:
                        ticker_items[ticker].append({
                            "title": item.get("headline", ""),
                            "text": item.get("summary", "")[:500],
                            "url": item.get("url", "")
                        })

        # Score and store
        for ticker, items in ticker_items.items():
            if not items:
                continue

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
    ticker_items = {ticker: [] for ticker in TICKER_UNIVERSE}

    for filename in files:
        filepath = os.path.join(premium_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            detected_ticker = None
            filename_upper = filename.upper()

            # Match ticker in filename
            for ticker in TICKER_UNIVERSE:
                if ticker in filename_upper:
                    detected_ticker = ticker
                    break

            # Fallback to scanning content
            if not detected_ticker:
                for ticker in TICKER_UNIVERSE:
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
    for ticker in TICKER_UNIVERSE:
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
                mock_items.append({
                    "title": random.choice(headline_templates),
                    "text": f"Simulated detail logs for {ticker} tracking indicators and market social buzz.",
                    "url": f"https://finance.yahoo.com/quote/{ticker}"
                })

            score, pos, neg = score_and_log_sources(db, ticker, date_str, source, mock_items)

            s_rec = TickerSentiment(
                ticker=ticker,
                date=date_str,
                sentiment_score=score,
                positive_ratio=pos,
                negative_ratio=neg,
                mention_count=mentions,
                source=source
            )
            db.add(s_rec)
            new_records += 1

    db.commit()
    print(f"Successfully simulated sentiment scores and logged sources. Added {new_records} records.")

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
            generate_mock_sentiment(db, date_str)

    db.close()
    print("\nSentiment processing completed.\n")

if __name__ == "__main__":
    fetch_sentiment()
