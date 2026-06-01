import sys
import os
import yaml
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, CrisisPrice

def fetch_crisis_data():
    init_db()
    db = SessionLocal()
    
    # Load crisis universes yaml
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(backend_dir, "data_ingestion", "crisis_universes.yaml")
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        return
        
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
        
    print("Starting historical crisis eras price fetch...")
    
    for era, era_config in config.items():
        start_date_default = era_config["start_date"]
        end_date_default = era_config["end_date"]
        tickers = era_config.get("tickers", [])
        overrides = era_config.get("overrides", {})
        
        print(f"\n--- Processing Era: {era.upper()} ({start_date_default} to {end_date_default}) ---")
        
        # Add overridden tickers to the list if they are not already there
        all_tickers = list(set(tickers + list(overrides.keys())))
        
        for ticker in all_tickers:
            # Determine start and end date
            ticker_start = start_date_default
            ticker_end = end_date_default
            
            if ticker in overrides:
                ticker_start = overrides[ticker].get("start_date", start_date_default)
                ticker_end = overrides[ticker].get("end_date", end_date_default)
                
            # Check if we already have records for this ticker in this era
            existing_count = db.query(CrisisPrice).filter(
                CrisisPrice.ticker == ticker,
                CrisisPrice.era == era
            ).count()
            
            if existing_count > 0:
                print(f"Ticker {ticker} in era {era} already has {existing_count} cached records. Skipping.")
                continue
                
            print(f"Fetching {ticker} for {era}... ({ticker_start} to {ticker_end})")
            
            try:
                # Add 1 day to end_date because yfinance is exclusive of end_date
                end_dt = datetime.strptime(ticker_end, "%Y-%m-%d")
                end_dt_exclusive = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                
                df = yf.download(
                    ticker,
                    start=ticker_start,
                    end=end_dt_exclusive,
                    progress=False
                )
                
                if df.empty:
                    print(f"No price data returned for {ticker} in era {era}.")
                    continue
                    
                # Flatten MultiIndex columns if present (common in newer yfinance versions)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                    
                df = df.reset_index()
                
                new_records = []
                for _, row in df.iterrows():
                    date_val = row['Date']
                    if hasattr(date_val, 'strftime'):
                        date_str = date_val.strftime("%Y-%m-%d")
                    else:
                        date_str = str(date_val)[:10]
                        
                    def get_float(val):
                        if hasattr(val, 'item'):
                            return float(val.item())
                        return float(val)
                        
                    # Final safety check against duplicates
                    existing = db.query(CrisisPrice).filter(
                        CrisisPrice.ticker == ticker,
                        CrisisPrice.era == era,
                        CrisisPrice.date == date_str
                    ).first()
                    
                    if existing:
                        continue
                        
                    crisis_record = CrisisPrice(
                        ticker=ticker,
                        era=era,
                        date=date_str,
                        open=get_float(row['Open']),
                        high=get_float(row['High']),
                        low=get_float(row['Low']),
                        close=get_float(row['Close']),
                        volume=get_float(row['Volume'])
                    )
                    new_records.append(crisis_record)
                    
                if new_records:
                    db.bulk_save_objects(new_records)
                    db.commit()
                    print(f"Added {len(new_records)} records for {ticker} in era {era}.")
                else:
                    print(f"No new records added for {ticker} in era {era}.")
                    
            except Exception as e:
                print(f"Failed to ingest {ticker} for {era}: {e}")
                db.rollback()
                
    db.close()
    print("\nCrisis eras data fetch completed.\n")

if __name__ == "__main__":
    fetch_crisis_data()
