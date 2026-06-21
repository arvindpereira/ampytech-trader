import sys
import os
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
import pandas as pd

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import FRED_API_KEY
from app.database import init_db, SessionLocal, MacroIndicator, DailyPrice

def scrape_shiller_cape():
    """Scrapes Shiller CAPE from multpl.com (table of monthly values)."""
    url = "https://www.multpl.com/shiller-pe/table/by-month"
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        print(f"Scraping Shiller CAPE from {url}...")
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        table = soup.find("table", {"id": "multpl-table"})
        if not table:
            # try finding any table
            table = soup.find("table")
        if not table:
            raise ValueError("No table found on multpl.com page.")

        data = []
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) == 2:
                try:
                    date_str = cols[0].text.strip()
                    val_str = cols[1].text.strip().split()[0]
                    # Parse date e.g. "Jun 1, 2026"
                    date = pd.to_datetime(date_str)
                    value = float(val_str)
                    data.append({"date": date.strftime("%Y-%m-%d"), "value": value})
                except Exception:
                    continue

        df = pd.DataFrame(data)
        if df.empty:
            raise ValueError("Parsed dataframe is empty.")
        print(f"✓ Scraped {len(df)} CAPE observations.")
        return df
    except Exception as e:
        print(f"  ⚠ Failed to scrape multpl.com: {e}")
        # Fallback: try parsing Robert Shiller's official Yale Excel file
        return parse_yale_cape()

def parse_yale_cape():
    """Fallback: Downloads and parses Robert Shiller's Yale Excel sheet."""
    yale_url = "http://www.econ.yale.edu/~shiller/data/ie_data.xls"
    print(f"Attempting to download Shiller data from Yale: {yale_url}...")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(yale_url, headers=headers, timeout=20)
        res.raise_for_status()
        temp_xls = "temp_ie_data.xls"
        with open(temp_xls, "wb") as f:
            f.write(res.content)

        # Read the Excel sheet (skip metadata headers)
        df_raw = pd.read_excel(temp_xls, sheet_name="Data", skiprows=7)
        # Clean up temp file
        if os.path.exists(temp_xls):
            os.remove(temp_xls)

        # We need Date (Column 0 or 'Date') and CAPE (Column 9 or 'CAPE')
        # Columns: Date, P, D, E, CPI, Fraction, Rate, Real P, Real D, Real E, CAPE
        df = df_raw.iloc[:, [0, 10]].dropna() # CAPE is index 10
        df.columns = ["date_frac", "value"]

        data = []
        for _, row in df.iterrows():
            try:
                frac = float(row["date_frac"])
                year = int(frac)
                month = int(round((frac - year) * 12)) + 1
                if month > 12:
                    month = 12
                if month < 1:
                    month = 1
                date_str = f"{year}-{month:02d}-01"
                value = float(row["value"])
                data.append({"date": date_str, "value": value})
            except Exception:
                continue

        res_df = pd.DataFrame(data)
        print(f"✓ Parsed {len(res_df)} CAPE observations from Yale Excel.")
        return res_df
    except Exception as e:
        print(f"  ⚠ Failed to parse Yale Excel sheet: {e}")
        return pd.DataFrame()

def fetch_fred_series(series_id):
    """Fetches series observations from FRED API using JSON format, falling back to keyless CSV."""
    if FRED_API_KEY:
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json"
        try:
            res = requests.get(url, timeout=15)
            res.raise_for_status()
            obs = res.json().get("observations", [])
            data = []
            for o in obs:
                try:
                    val = float(o["value"])
                    data.append({"date": o["date"], "value": val})
                except ValueError:
                    continue
            return pd.DataFrame(data)
        except Exception as e:
            print(f"  ⚠ Failed to fetch FRED series via API {series_id}: {e}. Falling back to CSV...")

    # Keyless CSV fallback
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        df = pd.read_csv(url)
        df.columns = ["date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        return df
    except Exception as e:
        print(f"  ⚠ Keyless CSV fallback failed for FRED series {series_id}: {e}")
        return pd.DataFrame()

def compute_buffett_indicator(gdp_df, wilshire_df):
    """Computes Buffett Indicator = Wilshire 5000 / GDP with a 90-day publication lag on GDP."""
    if gdp_df.empty or wilshire_df.empty:
        return pd.DataFrame()

    # Sort both
    gdp_df = gdp_df.sort_values("date").reset_index(drop=True)
    wilshire_df = wilshire_df.sort_values("date").reset_index(drop=True)

    # Calculate publication dates for GDP (GDP date + 90 days)
    gdp_df["pub_date"] = gdp_df["date"].apply(lambda d: (pd.to_datetime(d) + timedelta(days=90)).strftime("%Y-%m-%d"))

    results = []
    for _, w_row in wilshire_df.iterrows():
        w_date = w_row["date"]
        w_val = w_row["value"]

        # Find latest GDP that was published as of w_date
        avail_gdp = gdp_df[gdp_df["pub_date"] <= w_date]
        if avail_gdp.empty:
            continue

        gdp_val = avail_gdp.iloc[-1]["value"]
        if gdp_val <= 0:
            continue

        # Wilshire 5000 is price index level, GDP is in billions.
        # To get a clean ratio close to percentage (e.g. 1.5 for 150%):
        # Wilshire 5000 index level is roughly scaled such that index level divided by GDP (in billions)
        # provides an indicative ratio. Let's record the raw ratio.
        ratio = float(w_val) / float(gdp_val)
        results.append({"date": w_date, "value": ratio})

    return pd.DataFrame(results)

def run_valuation_fetcher():
    init_db()
    db = SessionLocal()

    # 1. Fetch and store CAPE Ratio
    cape_df = scrape_shiller_cape()
    if not cape_df.empty:
        existing_records = db.query(MacroIndicator).filter(MacroIndicator.indicator_name == "cape").all()
        existing_map = {r.date: r for r in existing_records}
        added, updated = 0, 0
        for _, row in cape_df.iterrows():
            d = row["date"]
            v = row["value"]
            if d in existing_map:
                if existing_map[d].value != v:
                    existing_map[d].value = v
                    db.add(existing_map[d])
                    updated += 1
            else:
                db.add(MacroIndicator(date=d, indicator_name="cape", value=v))
                added += 1
        db.commit()
    print("Fetching FRED series for Buffett Indicator...")
    gdp_df = fetch_fred_series("GDP")
    wilshire_df = fetch_fred_series("SP500")
    if not wilshire_df.empty:
        # Scale S&P 500 index by 10x to approximate Wilshire 5000 index level
        wilshire_df["value"] = wilshire_df["value"] * 10.0
    else:
        print("  ⚠ SP500 is unavailable from FRED. Falling back to SPY close * 100 proxy from database...")
        spy_prices = db.query(DailyPrice.date, DailyPrice.close).filter(
            DailyPrice.ticker == "SPY"
        ).order_by(DailyPrice.date.asc()).all()
        if spy_prices:
            data = [{"date": r[0], "value": float(r[1]) * 100.0} for r in spy_prices]
            wilshire_df = pd.DataFrame(data)

    bi_df = compute_buffett_indicator(gdp_df, wilshire_df)
    if not bi_df.empty:
        existing_records = db.query(MacroIndicator).filter(MacroIndicator.indicator_name == "buffett_indicator").all()
        existing_map = {r.date: r for r in existing_records}
        added, updated = 0, 0
        for _, row in bi_df.iterrows():
            d = row["date"]
            v = row["value"]
            if d in existing_map:
                if existing_map[d].value != v:
                    existing_map[d].value = v
                    db.add(existing_map[d])
                    updated += 1
            else:
                db.add(MacroIndicator(date=d, indicator_name="buffett_indicator", value=v))
                added += 1
        db.commit()
        print(f"✓ Buffett Indicator: added {added}, updated {updated} records.")
    else:
        print("⚠ Buffett Indicator fetch returned empty dataset.")

    db.close()

if __name__ == "__main__":
    run_valuation_fetcher()
