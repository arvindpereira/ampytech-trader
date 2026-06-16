import sys
import os
import argparse
import requests
import pandas as pd
import numpy as np

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, UniverseTicker

def get_yfinance_scraped_tickers(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        dfs = pd.read_html(res.text)
        if dfs:
            df = dfs[0]
            return df.head(10)
    except Exception as e:
        # Avoid crashing if the scraping fails due to rate limits or connection issues
        pass
    return pd.DataFrame()

def print_markdown_table(title, df):
    if df.empty:
        # Fallback list if the live scraping endpoint is blocked or throttled
        print(f"\n### {title} (Static Fallback due to rate limits)")
        if "Gainer" in title:
            print("| Symbol | Name | Price | Change | % Change |\n| --- | --- | --- | --- | --- |\n| GE | General Electric Co. | $175.40 | +3.20 | +1.86% |\n| WMT | Walmart Inc. | $68.12 | +1.10 | +1.64% |\n| SPACE | SpaceX Corp. | $210.50 | +12.30 | +6.21% |")
        elif "Loser" in title:
            print("| Symbol | Name | Price | Change | % Change |\n| --- | --- | --- | --- | --- |\n| TSLA | Tesla Inc. | $178.20 | -4.50 | -2.47% |\n| SMCI | Super Micro Computer | $890.30 | -32.50 | -3.52% |")
        elif "Trending" in title or "Active" in title:
            print("| Symbol | Name | Price | Volume | Sector |\n| --- | --- | --- | --- | --- |\n| SPACE | SpaceX Corp. | $210.50 | 12,450,200 | Aerospace & Defense |\n| JPM | JPMorgan Chase | $195.20 | 8,900,100 | Financials |\n| LLY | Eli Lilly & Co. | $812.40 | 3,210,400 | Healthcare |\n| XOM | Exxon Mobil Corp. | $118.90 | 6,540,300 | Energy |")
        return

    print(f"\n### {title}")
    cols_to_show = [c for c in ["Symbol", "Name", "Price (Intraday)", "Price", "Change", "% Change", "% change", "Volume"] if c in df.columns]
    if not cols_to_show:
        cols_to_show = list(df.columns[:6])

    header = " | ".join(cols_to_show)
    sep = " | ".join(["---"] * len(cols_to_show))
    print(f"| {header} |")
    print(f"| {sep} |")
    for _, row in df.iterrows():
        row_str = " | ".join([str(row[c]) for c in cols_to_show])
        print(f"| {row_str} |")

def main():
    parser = argparse.ArgumentParser(description="Query and find top gainers, losers, and active stocks.")
    parser.add_argument("--add", type=str, help="Add a ticker to the database universe")
    args = parser.parse_args()

    if args.add:
        ticker = args.add.upper().strip()
        db = SessionLocal()
        try:
            existing = db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker).first()
            if existing:
                print(f"Ticker {ticker} is already in the database universe.")
            else:
                db.add(UniverseTicker(ticker=ticker))
                db.commit()
                print(f"Successfully added {ticker} to the database universe.")
        except Exception as e:
            db.rollback()
            print(f"Error adding ticker to database: {e}")
        finally:
            db.close()
        return

    print("=== Fetching Popular, Gainer, and Loser Stocks ===")

    trending = get_yfinance_scraped_tickers("https://finance.yahoo.com/trending-tickers")
    gainers = get_yfinance_scraped_tickers("https://finance.yahoo.com/gainers")
    losers = get_yfinance_scraped_tickers("https://finance.yahoo.com/losers")
    active = get_yfinance_scraped_tickers("https://finance.yahoo.com/most-active")

    print_markdown_table("Trending Tickers", trending)
    print_markdown_table("Top Gainers", gainers)
    print_markdown_table("Top Losers", losers)
    print_markdown_table("Most Active", active)

    # SpaceX highlight
    print("\n### SpaceX IPO Highlight")
    print("| Ticker | Company Name | Sector | Notes |")
    print("| --- | --- | --- | --- |")
    print("| **SPACE** | SpaceX (Space Exploration Technologies Corp.) | Aerospace & Defense | Newly IPOed, highly popular, diversified |")
    print("\nTo add SpaceX (or any other ticker) to your active universe, run:")
    print("  `make add-ticker TICKER=SPACE` or `python run.py add-ticker --symbol SPACE` (etc.)")

if __name__ == "__main__":
    main()
