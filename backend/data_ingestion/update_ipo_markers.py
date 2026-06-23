import os
import sys
import json
import requests
from datetime import datetime

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, VirtualPosition, EquityLot
from app.core.config import TICKER_UNIVERSE, BASE_DIR
from data_ingestion.price_fetcher import map_ticker_to_yahoo

def update_ipo_markers():
    # 1. Load existing ipo_markers.json
    ipo_path = os.path.join(BASE_DIR, "data", "ipo_markers.json")
    ipo_markers = {}
    if os.path.exists(ipo_path):
        try:
            with open(ipo_path) as f:
                ipo_markers = json.load(f)
        except Exception as e:
            print(f"Error loading ipo_markers.json: {e}")

    # 2. Query database for held tickers
    db = SessionLocal()
    try:
        held_virtual = [r[0] for r in db.query(VirtualPosition.ticker).filter(
            VirtualPosition.mode == "real",
            VirtualPosition.quantity > 0
        ).all()]
        held_equity = [r[0] for r in db.query(EquityLot.ticker).filter(
            EquityLot.shares > 0
        ).all()]
    except Exception as e:
        print(f"Error querying DB for holdings: {e}")
        held_virtual, held_equity = [], []
    finally:
        db.close()

    held_set = {t.upper().strip() for t in held_virtual + held_equity if t}
    universe_set = {t.upper().strip() for t in TICKER_UNIVERSE if t}
    all_tickers = sorted(held_set.union(universe_set))

    print(f"Found {len(all_tickers)} unique tickers across universe and holdings.")

    updated_count = 0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    # 3. Query Yahoo for any missing tickers
    for ticker in all_tickers:
        # Filter out fictional test tickers
        if ticker in ["XYZ", "ZZZZ", "KDK", "CBRS", "SPCX"]:
            continue

        if ticker in ipo_markers:
            continue

        print(f"Fetching listing date for {ticker}...")
        yahoo_symbol = map_ticker_to_yahoo(ticker)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?range=max&interval=1d"
        try:
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 200:
                data = res.json()
                chart = data.get("chart", {}).get("result", [])
                if chart:
                    timestamps = chart[0].get("timestamp", [])
                    if timestamps:
                        first_ts = timestamps[0]
                        first_dt = datetime.fromtimestamp(first_ts)
                        ipo_date = first_dt.strftime("%Y-%m-%d")
                        ipo_markers[ticker] = ipo_date
                        print(f"-> {ticker} listed on {ipo_date}")
                        updated_count += 1
                    else:
                        print(f"-> No timestamps for {ticker}")
                else:
                    print(f"-> No chart result for {ticker}")
            else:
                print(f"-> Yahoo API status {res.status_code} for {ticker}")
        except Exception as e:
            print(f"-> Error fetching {ticker}: {e}")

    # 4. Save updated ipo_markers.json
    if updated_count > 0:
        try:
            os.makedirs(os.path.dirname(ipo_path), exist_ok=True)
            with open(ipo_path, "w") as f:
                json.dump(ipo_markers, f, indent=4, sort_keys=True)
            print(f"Successfully updated ipo_markers.json. Added {updated_count} new IPO dates.")
        except Exception as e:
            print(f"Error saving updated ipo_markers.json: {e}")
    else:
        print("No new IPO dates were found or added.")

if __name__ == "__main__":
    update_ipo_markers()
