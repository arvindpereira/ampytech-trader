import sys
import os
from datetime import datetime, timedelta
import requests

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import MASSIVE_API_KEY, MASSIVE_BASE_URL, DATA_LOOKBACK_DAYS
from app.database import init_db, SessionLocal, MacroIndicator

def fetch_macro_indicators():
    init_db()
    db = SessionLocal()

    print("Starting macro indicator fetch from Massive.com...")
    if DATA_LOOKBACK_DAYS >= 10000:
        start_date_str = "1996-01-01"
    else:
        start_date_str = (datetime.now() - timedelta(days=DATA_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    url = f"{MASSIVE_BASE_URL}/fed/v1/treasury-yields?date.gte={start_date_str}&limit=1000"
    headers = {}
    if MASSIVE_API_KEY:
        headers["Authorization"] = f"Bearer {MASSIVE_API_KEY}"

    results = []
    try:
        while url:
            print(f"Fetching macro data page: {url}...")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            page_results = data.get("results", [])
            results.extend(page_results)
            url = data.get("next_url")

        if not results:
            print("No treasury yield data returned from Massive.com.")
            db.close()
            return

        # Pre-load all existing macro indicators to avoid N+1 queries
        print("Pre-loading existing macro indicators from database...")
        existing_records = db.query(MacroIndicator).all()
        existing_map = {(r.date, r.indicator_name): r for r in existing_records}

        # Sort results by date asc
        results = sorted(results, key=lambda x: x["date"])

        new_spread_records = 0
        new_funds_records = 0
        updated_records = 0

        for row in results:
            date_str = row["date"]
            if date_str < start_date_str:
                continue

            y10 = row.get("yield_10_year")
            y2 = row.get("yield_2_year")
            y3m = row.get("yield_3_month")

            if y10 is not None and y2 is not None:
                spread_val = float(y10) - float(y2)

                key = (date_str, "yield_spread")
                existing = existing_map.get(key)

                if existing:
                    if existing.value != spread_val:
                        existing.value = spread_val
                        db.add(existing)
                        updated_records += 1
                else:
                    db.add(MacroIndicator(date=date_str, indicator_name="yield_spread", value=spread_val))
                    new_spread_records += 1

            if y3m is not None:
                # 3-Month Treasury Yield is used as a proxy for the Daily Effective Fed Funds Rate
                funds_val = float(y3m)

                key = (date_str, "fed_funds")
                existing = existing_map.get(key)

                if existing:
                    if existing.value != funds_val:
                        existing.value = funds_val
                        db.add(existing)
                        updated_records += 1
                else:
                    db.add(MacroIndicator(date=date_str, indicator_name="fed_funds", value=funds_val))
                    new_funds_records += 1

        db.commit()
        print(f"Yield spread records added: {new_spread_records}")
        print(f"Fed funds rate records added: {new_funds_records}")
        print(f"Macro records updated: {updated_records}")

    except Exception as e:
        print(f"Failed to fetch treasury yields: {e}")
        db.rollback()

    db.close()
    print("Macro indicator fetch completed.\n")


if __name__ == "__main__":
    fetch_macro_indicators()
