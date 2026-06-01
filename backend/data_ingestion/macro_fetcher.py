import sys
import os
from datetime import datetime, timedelta
import pandas as pd
import requests

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import FRED_API_KEY
from app.database import init_db, SessionLocal, MacroIndicator

# Macro series IDs from FRED
MACRO_SERIES = {
    "DFF": "fed_funds",         # Daily Effective Federal Funds Rate
    "T10Y2Y": "yield_spread"    # 10-Year Treasury Constant Maturity Minus 2-Year Treasury Constant Maturity
}

def fetch_macro_indicators():
    init_db()
    db = SessionLocal()
    
    print("Starting macro indicator fetch from FRED...")
    
    # We will fetch macro data for the default 2 years + a buffer to support backtesting overlaps
    two_years_ago = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    
    for series_id, clean_name in MACRO_SERIES.items():
        # Retrieve FRED data
        # We prefer public CSV URLs to bypass API key requirements, but support API keys if set
        try:
            if FRED_API_KEY:
                print(f"Fetching {clean_name} ({series_id}) using FRED API Key...")
                url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                
                observations = data.get("observations", [])
                df = pd.DataFrame(observations)
                if not df.empty:
                    df = df.rename(columns={"date": "date", "value": "value"})
                    df = df[["date", "value"]]
            else:
                print(f"Fetching {clean_name} ({series_id}) using public FRED CSV fallback...")
                csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
                df = pd.read_csv(csv_url)
                df = df.rename(columns={"observation_date": "date", series_id: "value"})
                
            if df.empty:
                print(f"No macro data returned for {clean_name}.")
                continue
                
            # Filter and clean the data
            df = df[df["date"] >= two_years_ago]
            
            # Clean missing values marked as "." in FRED CSV or parsed as NaN or empty
            df = df.dropna(subset=["value"])
            df = df[df["value"].astype(str).str.strip() != "."]
            df = df[df["value"].astype(str).str.strip() != ""]
            
            new_records = 0
            for _, row in df.iterrows():
                date_str = str(row["date"])
                val = float(row["value"])
                
                # Check database to prevent duplicates
                existing = db.query(MacroIndicator).filter(
                    MacroIndicator.date == date_str,
                    MacroIndicator.indicator_name == clean_name
                ).first()
                
                if existing:
                    # Update value if it changed (rare, but useful for revisions)
                    if existing.value != val:
                        existing.value = val
                    continue
                    
                macro_record = MacroIndicator(
                    date=date_str,
                    indicator_name=clean_name,
                    value=val
                )
                db.add(macro_record)
                new_records += 1
                
            db.commit()
            print(f"Successfully processed {clean_name}. Added {new_records} new daily records.")
            
        except Exception as e:
            print(f"Failed to fetch macro series {series_id}: {e}")
            db.rollback()
            
    db.close()
    print("Macro indicator fetch completed.\n")

if __name__ == "__main__":
    fetch_macro_indicators()
