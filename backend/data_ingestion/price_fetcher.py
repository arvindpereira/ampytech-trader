import sys
import os
from datetime import datetime, timedelta
import pandas as pd
import requests

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import TICKER_UNIVERSE, MASSIVE_API_KEY, MASSIVE_BASE_URL, DATA_TIMESPAN, DATA_MULTIPLIER, DATA_LOOKBACK_DAYS
from app.database import init_db, SessionLocal, RecentPrice, UniverseTicker
import urllib.parse

def fetch_massive_indicator(indicator_type, ticker, params):
    ticker_encoded = urllib.parse.quote(ticker)
    url = f"{MASSIVE_BASE_URL}/v1/indicators/{indicator_type}/{ticker_encoded}"
    headers = {}
    if MASSIVE_API_KEY:
        headers["Authorization"] = f"Bearer {MASSIVE_API_KEY}"

    import time
    max_retries = 5
    backoff_sec = 2.0

    for attempt in range(max_retries):
        time.sleep(0.5)  # Throttle a bit to prevent aggressive hits
        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 429:
                print(f"Rate limited (429) for indicator {indicator_type} on {ticker}. Retrying in {backoff_sec} seconds...")
                time.sleep(backoff_sec)
                backoff_sec *= 2.0
                continue
            response.raise_for_status()
            data = response.json()
            return data.get("results", {}).get("values", [])
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to fetch indicator {indicator_type} for {ticker}: {e}")
                return []
            time.sleep(backoff_sec)
            backoff_sec *= 2.0
    return []

def timestamp_to_str(epoch_ms):
    dt = datetime.fromtimestamp(epoch_ms / 1000.0)
    if DATA_TIMESPAN in ["minute", "hour"]:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        return dt.strftime("%Y-%m-%d")

def map_ticker_to_yahoo(ticker):
    if ticker.startswith("X:"):
        return ticker[2:].replace("USD", "-USD")
    elif ticker.startswith("C:"):
        return ticker[2:] + "=X"
    return ticker

def fetch_yahoo_prices(ticker, start_date, end_date):
    yahoo_symbol = map_ticker_to_yahoo(ticker)
    p1 = int(start_date.timestamp())
    p2 = int(end_date.timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}?period1={p1}&period2={p2}&interval=1d"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}

    import time
    max_retries = 5
    backoff_sec = 2.0

    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 429:
                time.sleep(backoff_sec)
                backoff_sec *= 2.0
                continue
            res.raise_for_status()
            data = res.json()
            chart = data.get("chart", {}).get("result", [])
            if not chart:
                return []
            chart = chart[0]
            timestamps = chart.get("timestamp", [])
            quotes = chart.get("indicators", {}).get("quote", [])
            if not quotes:
                return []
            quotes = quotes[0]

            opens = quotes.get("open", [])
            highs = quotes.get("high", [])
            lows = quotes.get("low", [])
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])

            bars = []
            for i in range(len(timestamps)):
                t_ms = timestamps[i] * 1000
                o = opens[i]
                h = highs[i]
                l = lows[i]
                c = closes[i]
                v = volumes[i]

                if o is None or h is None or l is None or c is None:
                    continue

                bars.append({
                    "t": t_ms,
                    "o": float(o),
                    "h": float(h),
                    "l": float(l),
                    "c": float(c),
                    "v": float(v) if v is not None else 0.0
                })
            return bars
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to fetch Yahoo data for {ticker}: {e}")
                return []
            time.sleep(backoff_sec)
            backoff_sec *= 2.0
    return []

def compute_local_indicators_for_bars(bars):
    if not bars:
        return {}, {}, {}, {}
    import pandas as pd
    import numpy as np

    df = pd.DataFrame(bars)
    df = df.drop_duplicates(subset=['t']).sort_values('t').reset_index(drop=True)

    df['sma_10'] = df['c'].rolling(window=10).mean()
    df['sma_50'] = df['c'].rolling(window=50).mean()

    delta = df['c'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()

    avg_gain_vals = avg_gain.values
    avg_loss_vals = avg_loss.values
    gain_vals = gain.values
    loss_vals = loss.values

    for i in range(15, len(df)):
        if not np.isnan(avg_gain_vals[i-1]):
            avg_gain_vals[i] = (avg_gain_vals[i-1] * 13 + gain_vals[i]) / 14
        if not np.isnan(avg_loss_vals[i-1]):
            avg_loss_vals[i] = (avg_loss_vals[i-1] * 13 + loss_vals[i]) / 14

    df['rsi_14'] = 100 - (100 / (1 + avg_gain_vals / (avg_loss_vals + 1e-10)))

    ema_12 = df['c'].ewm(span=12, adjust=False).mean()
    ema_26 = df['c'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    sma_10_map = {}
    sma_50_map = {}
    rsi_14_map = {}
    macd_map = {}

    for _, row in df.iterrows():
        epoch_ms = row['t']
        date_str = timestamp_to_str(epoch_ms)
        if not np.isnan(row['sma_10']):
            sma_10_map[date_str] = float(row['sma_10'])
        if not np.isnan(row['sma_50']):
            sma_50_map[date_str] = float(row['sma_50'])
        if not np.isnan(row['rsi_14']):
            rsi_14_map[date_str] = float(row['rsi_14'])
        if not np.isnan(row['macd']) and not np.isnan(row['macd_signal']):
            macd_map[date_str] = (float(row['macd']), float(row['macd_signal']))

    return sma_10_map, sma_50_map, rsi_14_map, macd_map

def fetch_single_ticker_data(ticker, start_date, end_date):
    split_date = datetime(2022, 1, 1)

    # 1. Fetch pre-2022 from Yahoo Finance
    yahoo_results = []
    if start_date < split_date:
        fetch_end = min(end_date, split_date)
        print(f"Fetching Yahoo historical prices for {ticker}: {start_date.strftime('%Y-%m-%d')} to {fetch_end.strftime('%Y-%m-%d')}...")
        yahoo_results = fetch_yahoo_prices(ticker, start_date, fetch_end)

    # 2. Fetch post-2022 from Massive API
    massive_results = []
    if end_date > split_date:
        fetch_start = max(start_date, split_date)
        start_str = fetch_start.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        ticker_encoded = urllib.parse.quote(ticker)

        print(f"Fetching Massive API prices for {ticker}: {start_str} to {end_str}...")

        import time
        max_retries = 5
        backoff_sec = 2.0

        url = f"{MASSIVE_BASE_URL}/v2/aggs/ticker/{ticker_encoded}/range/{DATA_MULTIPLIER}/{DATA_TIMESPAN}/{start_str}/{end_str}"
        headers = {}
        if MASSIVE_API_KEY:
            headers["Authorization"] = f"Bearer {MASSIVE_API_KEY}"

        for attempt in range(max_retries):
            time.sleep(0.2)
            try:
                response = requests.get(url, headers=headers)
                if response.status_code == 429:
                    print(f"Rate limited (429) for {ticker}. Retrying in {backoff_sec} seconds...")
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0
                    continue
                response.raise_for_status()
                data = response.json()
                massive_results = data.get("results", [])
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Failed to fetch Massive API data for {ticker}: {e}")
                    # Fallback to Yahoo Finance for post-2022 if Massive fails
                    print(f"Falling back to Yahoo Finance for {ticker} post-2022...")
                    massive_results = fetch_yahoo_prices(ticker, fetch_start, end_date)
                else:
                    time.sleep(backoff_sec)
                    backoff_sec *= 2.0

    # Combine results
    combined_map = {bar['t']: bar for bar in yahoo_results}
    for bar in massive_results:
        combined_map[bar['t']] = bar

    combined_results = sorted(list(combined_map.values()), key=lambda x: x['t'])

    if not combined_results:
        print(f"No price data returned for {ticker} from any source.")
        return ticker, [], {}, {}, {}, {}

    # Compute technical indicators locally on combined series for consistency
    sma_10_map, sma_50_map, rsi_14_map, macd_map = compute_local_indicators_for_bars(combined_results)

    return ticker, combined_results, sma_10_map, sma_50_map, rsi_14_map, macd_map


def fetch_recent_prices():
    init_db()  # Ensure database schema is initialized
    db = SessionLocal()

    end_date = datetime.now()
    # Default lookback based on configured setting
    if DATA_LOOKBACK_DAYS >= 10000:
        default_start_date = datetime(1996, 1, 1)
    else:
        default_start_date = end_date - timedelta(days=DATA_LOOKBACK_DAYS)

    # Load stock universe dynamically from DB (honoring user edits)
    db_tickers = db.query(UniverseTicker).all()
    active_universe = [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE

    print(f"Starting recent price fetch for {len(active_universe)} tickers in {DATA_TIMESPAN} resolution (multiplier: {DATA_MULTIPLIER})...")

    tickers_to_fetch = sorted(list(set(active_universe + ["BRK-B"])))

    # Define a list of fetch tasks: (ticker, start_date, end_date)
    fetch_tasks = []
    for ticker in tickers_to_fetch:
        latest_record = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_by(RecentPrice.date.desc()).first()
        earliest_record = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_by(RecentPrice.date.asc()).first()

        # 1. Check if we need to backfill historical data
        if earliest_record:
            earliest_date_part = earliest_record.date.split(" ")[0].split("T")[0]
            earliest_dt = datetime.strptime(earliest_date_part, "%Y-%m-%d")
            # If default_start_date is before the earliest cached date, backfill it
            if default_start_date < earliest_dt - timedelta(days=5):
                print(f"Ticker {ticker} needs historical backfill: {default_start_date.strftime('%Y-%m-%d')} to {earliest_date_part}")
                fetch_tasks.append((ticker, default_start_date, earliest_dt))

        # 2. Check if we need to fetch recent data forward
        if latest_record:
            latest_date_part = latest_record.date.split(" ")[0].split("T")[0]
            latest_dt = datetime.strptime(latest_date_part, "%Y-%m-%d")
            if latest_dt < end_date - timedelta(days=1):
                fetch_tasks.append((ticker, latest_dt, end_date))
        else:
            # No records exist at all, fetch the full range
            print(f"Ticker {ticker} has no cached records. Fetching full range: {default_start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            fetch_tasks.append((ticker, default_start_date, end_date))

    db.close() # Close session for thread safety before parallel execution

    if not fetch_tasks:
        print("All tickers are already up to date.")
        return

    # Fetch data in parallel
    results_map = {}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print(f"Launching parallel fetching using ThreadPoolExecutor for {len(fetch_tasks)} tasks...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_single_ticker_data, ticker, start_date, end_date): ticker
            for ticker, start_date, end_date in fetch_tasks
        }

        for future in as_completed(futures):
            ticker = futures[future]
            try:
                ticker_res, results, sma_10_map, sma_50_map, rsi_14_map, macd_map = future.result()
                if results is not None:
                    if ticker_res in results_map:
                        prev_results, prev_sma, prev_sma_50, prev_rsi, prev_macd = results_map[ticker_res]

                        # Merge price bars and drop duplicate timestamps
                        combined_bars = {bar['t']: bar for bar in prev_results}
                        for bar in results:
                            combined_bars[bar['t']] = bar
                        new_results = sorted(list(combined_bars.values()), key=lambda x: x['t'])

                        prev_sma.update(sma_10_map)
                        prev_sma_50.update(sma_50_map)
                        prev_rsi.update(rsi_14_map)
                        prev_macd.update(macd_map)

                        results_map[ticker_res] = (new_results, prev_sma, prev_sma_50, prev_rsi, prev_macd)
                    else:
                        results_map[ticker_res] = (results, sma_10_map, sma_50_map, rsi_14_map, macd_map)
            except Exception as e:
                print(f"Error executing parallel fetch for {ticker}: {e}")

    # Write results sequentially to DB
    print("\nWriting fetched data to database sequentially...")
    db = SessionLocal()
    try:
        for ticker, (results, sma_10_map, sma_50_map, rsi_14_map, macd_map) in results_map.items():
            if not results:
                continue

            try:
                new_records = []
                updated_existing_count = 0

                # Pre-fetch all existing dates for this ticker to avoid N+1 queries
                print(f"Loading existing cached dates from database for {ticker}...")
                existing_prices = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).all()
                existing_map = {p.date: p for p in existing_prices}

                for bar in results:
                    epoch_ms = bar.get("t")
                    if not epoch_ms:
                        continue
                    dt = datetime.fromtimestamp(epoch_ms / 1000.0)

                    if DATA_TIMESPAN in ["minute", "hour"]:
                        date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        date_str = dt.strftime("%Y-%m-%d")

                    existing = existing_map.get(date_str)

                    macd_pair = macd_map.get(date_str)
                    if existing:
                        updated = False
                        if existing.sma_10 is None and date_str in sma_10_map:
                            existing.sma_10 = sma_10_map[date_str]
                            updated = True
                        if existing.sma_50 is None and date_str in sma_50_map:
                            existing.sma_50 = sma_50_map[date_str]
                            updated = True
                        if existing.rsi_14 is None and date_str in rsi_14_map:
                            existing.rsi_14 = rsi_14_map[date_str]
                            updated = True
                        if existing.macd is None and macd_pair:
                            existing.macd = macd_pair[0]
                            existing.macd_signal = macd_pair[1]
                            updated = True

                        if updated:
                            db.add(existing)
                            updated_existing_count += 1
                        continue

                    sma10_val = sma_10_map.get(date_str)
                    sma50_val = sma_50_map.get(date_str)
                    rsi14_val = rsi_14_map.get(date_str)
                    macd_val = macd_pair[0] if macd_pair else None
                    macd_sig_val = macd_pair[1] if macd_pair else None

                    price_record = RecentPrice(
                        ticker=ticker,
                        date=date_str,
                        open=float(bar["o"]),
                        high=float(bar["h"]),
                        low=float(bar["l"]),
                        close=float(bar["c"]),
                        volume=float(bar["v"]),
                        sma_10=sma10_val,
                        sma_50=sma50_val,
                        rsi_14=rsi14_val,
                        macd=macd_val,
                        macd_signal=macd_sig_val
                    )
                    new_records.append(price_record)

                if new_records or updated_existing_count > 0:
                    if new_records:
                        db.bulk_save_objects(new_records)
                    db.commit()
                    print(f"Added {len(new_records)} new price records and backfilled {updated_existing_count} existing records with technical indicators for {ticker}.")
                else:
                    print(f"No new records added or updated for {ticker} (already cached).")

            except Exception as e:
                print(f"Failed to ingest price data for {ticker}: {e}")
                db.rollback()

    finally:
        db.close()
    print("Recent price fetch completed.\n")

if __name__ == "__main__":
    fetch_recent_prices()
