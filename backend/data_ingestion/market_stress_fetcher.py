import sys
import os
from datetime import datetime, timedelta
import requests
import pandas as pd

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import FRED_API_KEY
from app.database import init_db, SessionLocal, MacroIndicator

FRED_SERIES_MAP = {
    "vix": "VIXCLS",
    "hy_spread": "BAMLH0A0HYM2",
    "ig_spread": "BAMLC0A0CM",
    "nfci": "NFCI",
    "nfci_leverage": "NFCILEVERAGE",
    "sloos_tightening": "DRTSCILM",
    "building_permits": "PERMIT",
    "initial_claims_4w": "IC4WSA",
    "sahm_indicator": "SAHMREALTIME",
    "term_spread_10y3m": "T10Y3M",
    "fed_funds": "DFF",
    "margin_debt_quarterly": "BOGZ1FL663067003Q"
}

def get_pub_date(ref_date_str, series_id):
    """Aligns a FRED reference date with its standard publication date to avoid look-ahead bias."""
    ref_date = pd.to_datetime(ref_date_str)
    if series_id in ["VIXCLS", "BAMLH0A0HYM2", "BAMLC0A0CM", "T10Y3M", "DFF"]:
        # Daily: published next calendar day (1-day lag)
        pub_date = ref_date + timedelta(days=1)
    elif series_id in ["NFCI", "NFCILEVERAGE"]:
        # Weekly: published same Friday (reference date is Friday, we shift by 1 day for safety)
        pub_date = ref_date + timedelta(days=1)
    elif series_id == "IC4WSA":
        # Weekly initial claims: week ends Saturday, published Thursday (5 days later)
        pub_date = ref_date + timedelta(days=5)
    elif series_id == "PERMIT":
        # Monthly building permits: published ~17 days after month ends (reference date is 1st of month)
        # So we add 30 days (for the month) + 17 days = 47 days.
        pub_date = ref_date + timedelta(days=47)
    elif series_id == "SAHMREALTIME":
        # Monthly Sahm: published with employment report, ~7 days after month ends.
        # Add 30 days (for the month) + 7 days = 37 days.
        pub_date = ref_date + timedelta(days=37)
    elif series_id == "DRTSCILM":
        # Quarterly SLOOS: published ~35 days after quarter ends.
        # Add 90 days (for the quarter) + 35 days = 125 days.
        pub_date = ref_date + timedelta(days=125)
    elif series_id == "BOGZ1FL663067003Q":
        # Quarterly Flow of Funds margin debt: published ~75 days after quarter ends.
        # Add 90 days (for the quarter) + 75 days = 165 days.
        pub_date = ref_date + timedelta(days=165)
    else:
        # Default: 30 days lag
        pub_date = ref_date + timedelta(days=30)
    return pub_date.strftime("%Y-%m-%d")

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
                    data.append({"ref_date": o["date"], "value": val})
                except ValueError:
                    continue
            return pd.DataFrame(data)
        except Exception as e:
            print(f"  ⚠ Failed to fetch FRED series via API {series_id}: {e}. Falling back to CSV...")

    # Keyless CSV fallback
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        df = pd.read_csv(url)
        df.columns = ["ref_date", "value"]
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna()
        return df
    except Exception as e:
        print(f"  ⚠ Keyless CSV fallback failed for FRED series {series_id}: {e}")
        return pd.DataFrame()

def fetch_excess_bond_premium():
    """Downloads and parses the Excess Bond Premium (EBP) CSV from the Federal Reserve Board."""
    url = "https://www.federalreserve.gov/econres/notes/feds-notes/ebp_csv.csv"
    print(f"Downloading Excess Bond Premium from {url}...")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()

        # Load CSV
        lines = res.text.strip().split("\n")
        # Check if first line is headers
        df = pd.read_csv(requests.compat.StringIO(res.text))

        # Identify columns dynamically
        date_col = next((c for c in df.columns if "date" in c.lower()), None)
        ebp_col = next((c for c in df.columns if "ebp" in c.lower()), None)
        prob_col = next((c for c in df.columns if "prob" in c.lower() or "est" in c.lower()), None)

        if not date_col or not ebp_col:
            raise ValueError(f"Could not identify EBP or Date columns. Headers: {df.columns.tolist()}")

        ebp_data = []
        prob_data = []

        for _, row in df.iterrows():
            try:
                ref_date_str = str(row[date_col]).strip()
                ref_date = pd.to_datetime(ref_date_str)
                # EBP is monthly, published on the 4th business day of the following month.
                # Shift by 5 calendar days after month end.
                # E.g. ref_date Q-end or month-end is typically 1st of next month or last of current.
                # Let's align ref_date to month end, then add 5 days.
                month_end = ref_date + pd.offsets.MonthEnd(0)
                pub_date = (month_end + timedelta(days=5)).strftime("%Y-%m-%d")

                ebp_val = float(row[ebp_col])
                ebp_data.append({"date": pub_date, "value": ebp_val})

                if prob_col:
                    prob_val = float(row[prob_col])
                    prob_data.append({"date": pub_date, "value": prob_val})
            except Exception:
                continue

        return pd.DataFrame(ebp_data), pd.DataFrame(prob_data)
    except Exception as e:
        print(f"  ⚠ Failed to fetch Excess Bond Premium: {e}")
        return pd.DataFrame(), pd.DataFrame()

def run_market_stress_fetcher():
    init_db()
    db = SessionLocal()

    # 1. Fetch FRED stress indicators
    for indicator_name, series_id in FRED_SERIES_MAP.items():
        print(f"Fetching FRED series {series_id} ({indicator_name})...")
        df = fetch_fred_series(series_id)
        if not df.empty:
            existing_records = db.query(MacroIndicator).filter(MacroIndicator.indicator_name == indicator_name).all()
            existing_map = {r.date: r for r in existing_records}
            added, updated = 0, 0
            for _, row in df.iterrows():
                # Calculate publication date to ensure point-in-time correctness
                pub_d = get_pub_date(row["ref_date"], series_id)
                v = row["value"]
                if pub_d in existing_map:
                    if existing_map[pub_d].value != v:
                        existing_map[pub_d].value = v
                        db.add(existing_map[pub_d])
                        updated += 1
                else:
                    db.add(MacroIndicator(date=pub_d, indicator_name=indicator_name, value=v))
                    added += 1
            db.commit()
            print(f"✓ FRED series {series_id}: added {added}, updated {updated} records.")

    # 2. Fetch Excess Bond Premium (EBP) CSV
    ebp_df, prob_df = fetch_excess_bond_premium()
    if not ebp_df.empty:
        existing_records = db.query(MacroIndicator).filter(MacroIndicator.indicator_name == "excess_bond_premium").all()
        existing_map = {r.date: r for r in existing_records}
        added, updated = 0, 0
        for _, row in ebp_df.iterrows():
            d = row["date"]
            v = row["value"]
            if d in existing_map:
                if existing_map[d].value != v:
                    existing_map[d].value = v
                    db.add(existing_map[d])
                    updated += 1
            else:
                db.add(MacroIndicator(date=d, indicator_name="excess_bond_premium", value=v))
                added += 1
        db.commit()
        print(f"✓ Excess Bond Premium: added {added}, updated {updated} records.")

    if not prob_df.empty:
        existing_records = db.query(MacroIndicator).filter(MacroIndicator.indicator_name == "ebp_recession_prob").all()
        existing_map = {r.date: r for r in existing_records}
        added, updated = 0, 0
        for _, row in prob_df.iterrows():
            d = row["date"]
            v = row["value"]
            if d in existing_map:
                if existing_map[d].value != v:
                    existing_map[d].value = v
                    db.add(existing_map[d])
                    updated += 1
            else:
                db.add(MacroIndicator(date=d, indicator_name="ebp_recession_prob", value=v))
                added += 1
        db.commit()
        print(f"✓ EBP Recession Probability: added {added}, updated {updated} records.")

    db.close()

if __name__ == "__main__":
    run_market_stress_fetcher()
