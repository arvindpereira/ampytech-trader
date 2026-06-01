import sys
import os
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import TICKER_UNIVERSE
from app.database import init_db, SessionLocal, RecentPrice

def fetch_recent_prices():
    init_db()  # Ensure database schema is initialized
    db = SessionLocal()
    
    end_date = datetime.now()
    # Default to 2 years ago if db is empty
    default_start_date = end_date - timedelta(days=730)
    
    print(f"Starting recent price fetch for {len(TICKER_UNIVERSE)} tickers...")
    
    tickers_to_fetch = sorted(list(set(TICKER_UNIVERSE + ["BRK-B"])))
    for ticker in tickers_to_fetch:
        # Check latest date in database
        latest_record = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_code = db.query(RecentPrice)\
            .filter(RecentPrice.ticker == ticker)\
            .order_by(RecentPrice.date.desc())\
            .first()
            
        if latest_record:
            # Start fetching from the day after the latest date cached
            start_date_str = latest_record.date
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d") + timedelta(days=1)
        else:
            start_date = default_start_date
            
        # Avoid fetching if start_date is in the future relative to end_date
        if start_date >= end_date:
            print(f"Ticker {ticker} is already up to date. Latest cached date: {latest_record.date}")
            continue
            
        print(f"Fetching {ticker} from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...")
        
        try:
            # yfinance download
            df = yf.download(
                ticker, 
                start=start_date.strftime('%Y-%m-%d'),
                end=(end_date + timedelta(days=1)).strftime('%Y-%m-%d'), # yfinance end date is exclusive
                progress=False
            )
            
            if df.empty:
                print(f"No new price data returned for {ticker}.")
                continue
                
            # Flatten MultiIndex columns if present (common in newer yfinance versions)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            # Reset index to get Date column
            df = df.reset_index()
            
            new_records = []
            for _, row in df.iterrows():
                # Handle potential pandas Timestamp conversion
                date_val = row['Date']
                if hasattr(date_val, 'strftime'):
                    date_str = date_val.strftime("%Y-%m-%d")
                else:
                    date_str = str(date_val)[:10]
                
                # Fetch row values, handling pandas Series types
                def get_float(val):
                    # Handle if yfinance returns single values inside a series
                    if hasattr(val, 'item'):
                        return float(val.item())
                    return float(val)
                
                # Double check to prevent duplicate entries
                existing = db.query(RecentPrice).filter(
                    RecentPrice.ticker == ticker,
                    RecentPrice.date == date_str
                ).first()
                
                if existing:
                    continue
                    
                price_record = RecentPrice(
                    ticker=ticker,
                    date=date_str,
                    open=get_float(row['Open']),
                    high=get_float(row['High']),
                    low=get_float(row['Low']),
                    close=get_float(row['Close']),
                    volume=get_float(row['Volume'])
                )
                new_records.append(price_record)
                
            if new_records:
                db.bulk_save_objects(new_records)
                db.commit()
                print(f"Added {len(new_records)} new daily price records for {ticker}.")
            else:
                print(f"No new records added for {ticker} (already cached).")
                
        except Exception as e:
            print(f"Failed to fetch data for {ticker}: {e}")
            db.rollback()
            
    db.close()
    print("Recent price fetch completed.\n")

if __name__ == "__main__":
    fetch_recent_prices()
