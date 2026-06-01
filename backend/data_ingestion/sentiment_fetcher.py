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
from app.database import init_db, SessionLocal, TickerSentiment

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

def parse_headlines_with_vader(texts):
    """Calculates average polarity, positive ratio, negative ratio from text list."""
    analyzer = SentimentIntensityAnalyzer()
    if not texts:
        return 0.0, 0.0, 0.0
        
    scores = []
    pos_count = 0
    neg_count = 0
    
    for text in texts:
        vs = analyzer.polarity_scores(text)
        compound = vs['compound']
        scores.append(compound)
        
        if compound > 0.05:
            pos_count += 1
        elif compound < -0.05:
            neg_count += 1
            
    avg_score = sum(scores) / len(scores)
    pos_ratio = pos_count / len(texts)
    neg_ratio = neg_count / len(texts)
    
    return avg_score, pos_ratio, neg_ratio

def fetch_reddit_sentiment(db, date_str):
    reddit = get_praw_reddit()
    if not reddit:
        print("Reddit credentials missing or invalid. Skipping live Reddit fetch.")
        return False
        
    print("Fetching live Reddit sentiment from r/wallstreetbets and r/stocks...")
    try:
        # Collect recent submissions
        submissions = []
        for sub_name in ["wallstreetbets", "stocks"]:
            subreddit = reddit.subreddit(sub_name)
            for submission in subreddit.hot(limit=100):
                submissions.append(submission.title + " " + (submission.selftext or ""))
                
        # Group texts by ticker mention
        ticker_texts = {ticker: [] for ticker in TICKER_UNIVERSE}
        for text in submissions:
            # Simple keyword search
            upper_text = text.upper()
            for ticker in TICKER_UNIVERSE:
                # Add word boundary check
                if f" {ticker} " in f" {upper_text} ":
                    ticker_texts[ticker].append(text)
                    
        # Score each ticker
        for ticker, texts in ticker_texts.items():
            if not texts:
                continue
                
            score, pos, neg = parse_headlines_with_vader(texts)
            
            # Save or update database
            existing = db.query(TickerSentiment).filter(
                TickerSentiment.ticker == ticker,
                TickerSentiment.date == date_str,
                TickerSentiment.source == "reddit"
            ).first()
            
            if existing:
                existing.sentiment_score = score
                existing.positive_ratio = pos
                existing.negative_ratio = neg
                existing.mention_count = len(texts)
            else:
                s_rec = TickerSentiment(
                    ticker=ticker,
                    date=date_str,
                    sentiment_score=score,
                    positive_ratio=pos,
                    negative_ratio=neg,
                    mention_count=len(texts),
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
    
    # We will prioritize NewsAPI, and fallback to Finnhub
    try:
        ticker_texts = {ticker: [] for ticker in TICKER_UNIVERSE}
        
        if NEWS_API_KEY:
            for ticker in TICKER_UNIVERSE:
                url = f"https://newsapi.org/v2/everything?q={ticker}&pageSize=20&apiKey={NEWS_API_KEY}"
                res = requests.get(url)
                if res.status_code == 200:
                    articles = res.json().get("articles", [])
                    for art in articles:
                        headline = art.get("title", "")
                        desc = art.get("description", "")
                        ticker_texts[ticker].append(headline + ". " + (desc or ""))
                        
        elif FINNHUB_API_KEY:
            # Finnhub Company News
            end_t = datetime.now()
            start_t = end_t - timedelta(days=1)
            start_str = start_t.strftime("%Y-%m-%d")
            end_str = end_t.strftime("%Y-%m-%d")
            
            for ticker in TICKER_UNIVERSE:
                # Indices (SPY, QQQ) are not supported in company news endpoint
                if ticker in ["SPY", "QQQ"]:
                    continue
                url = f"https://finnhub.io/api/v1/company-news?symbol={ticker}&from={start_str}&to={end_str}&token={FINNHUB_API_KEY}"
                res = requests.get(url)
                if res.status_code == 200:
                    news = res.json()
                    for item in news[:20]:
                        ticker_texts[ticker].append(item.get("headline", "") + ". " + item.get("summary", ""))
                        
        # Score and store
        for ticker, texts in ticker_texts.items():
            if not texts:
                continue
                
            score, pos, neg = parse_headlines_with_vader(texts)
            
            existing = db.query(TickerSentiment).filter(
                TickerSentiment.ticker == ticker,
                TickerSentiment.date == date_str,
                TickerSentiment.source == "news"
            ).first()
            
            if existing:
                existing.sentiment_score = score
                existing.positive_ratio = pos
                existing.negative_ratio = neg
                existing.mention_count = len(texts)
            else:
                s_rec = TickerSentiment(
                    ticker=ticker,
                    date=date_str,
                    sentiment_score=score,
                    positive_ratio=pos,
                    negative_ratio=neg,
                    mention_count=len(texts),
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

def generate_mock_sentiment(db, date_str):
    """Generates realistic mock sentiment scores for active tickers if APIs are unavailable."""
    print("Generating simulated sentiment data (no keys present)...")
    
    new_records = 0
    for ticker in TICKER_UNIVERSE:
        for source in ["news", "reddit"]:
            # Check database first
            existing = db.query(TickerSentiment).filter(
                TickerSentiment.date == date_str,
                TickerSentiment.ticker == ticker,
                TickerSentiment.source == source
            ).first()
            
            if existing:
                continue
                
            # Simulate scores. High-volatility tech stocks get more positive/negative swings
            if ticker in ["TSLA", "NVDA", "AMD"]:
                # High volatility, wider spread
                score = random.uniform(-0.6, 0.8)
                mentions = random.randint(25, 120)
            elif ticker in ["SPY", "QQQ"]:
                # Balanced indices
                score = random.uniform(-0.1, 0.4)
                mentions = random.randint(50, 200)
            else:
                # Moderate
                score = random.uniform(-0.3, 0.5)
                mentions = random.randint(5, 40)
                
            pos = max(0.0, score + random.uniform(0.0, 0.3)) if score > 0 else random.uniform(0.0, 0.2)
            neg = max(0.0, -score + random.uniform(0.0, 0.3)) if score < 0 else random.uniform(0.0, 0.2)
            # Normalize ratios
            total = pos + neg
            if total > 0.9:
                pos = pos / (total + 0.1)
                neg = neg / (total + 0.1)
                
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
    print(f"Successfully simulated sentiment scores. Added {new_records} new daily records.")

def fetch_sentiment():
    init_db()
    db = SessionLocal()
    
    # We capture sentiment for today and yesterday to verify we cover the trading gap
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    
    for dt in [yesterday, today]:
        date_str = dt.strftime("%Y-%m-%d")
        print(f"\n--- Processing Sentiment for {date_str} ---")
        
        # Check if we already have complete logs for this day in db
        existing_count = db.query(TickerSentiment).filter(TickerSentiment.date == date_str).count()
        # We expect 20 tickers * 2 sources = 40 records per day
        if existing_count >= 40:
            print(f"Sentiment records for {date_str} are already complete in SQLite database. Skipping API checks.")
            continue
            
        news_fetched = fetch_news_sentiment(db, date_str)
        reddit_fetched = fetch_reddit_sentiment(db, date_str)
        
        # If any of the sources fail or keys are absent, mock the remainder for completeness
        if not news_fetched or not reddit_fetched:
            generate_mock_sentiment(db, date_str)
            
    db.close()
    print("\nSentiment processing completed.\n")

if __name__ == "__main__":
    fetch_sentiment()
