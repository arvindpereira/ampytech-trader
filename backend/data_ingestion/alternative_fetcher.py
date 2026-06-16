import sys
import os
import random
import time
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import requests

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, CongressDisclosure, InsiderDisclosure, UniverseTicker
from app.core.config import TICKER_UNIVERSE, SEC_USER_AGENT, INSIDER_FETCH_DAYS

# Benchmarks/ETFs have no Form 4 filings.
NON_EQUITY = {"SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP"}
# Foreign private issuers are exempt from Section 16 / Form 4; any "Form 4s" under their CIK are
# unreliable (e.g. TSM showed spurious "purchases"). Exclude them from insider analysis.
FOREIGN_NO_FORM4 = {"TSM", "ASML", "NOK", "ARM", "BB"}

POLITICIANS = [
    ("Nancy Pelosi", "house"),
    ("Tommy Tuberville", "senate"),
    ("Sheldon Whitehouse", "senate"),
    ("Mark Warner", "senate"),
    ("Ro Khanna", "house"),
    ("Michael McCaul", "house"),
    ("Diana Harshbarger", "house"),
    ("John Curtis", "house"),
    ("Dan Meuser", "house"),
    ("Kevin Hern", "house")
]

RELATIONSHIPS = ["CEO", "CFO", "Director", "COO", "10% Owner", "VP of Engineering", "General Counsel"]

def seed_alternative_data():
    print("=" * 78)
    print("WARNING: SEEDING *SYNTHETIC* (RANDOM) DISCLOSURES — NOT REAL DATA.")
    print("These carry NO predictive signal. For research/plumbing only. Do not trade on them.")
    print("Replace with a real source (SEC EDGAR Form 4 / Quiver STOCK Act) before relying on alt data.")
    print("=" * 78)
    init_db()
    db = SessionLocal()

    # Get active universe
    db_tickers = db.query(UniverseTicker).all()
    active_universe = [t.ticker for t in db_tickers] if db_tickers else TICKER_UNIVERSE
    active_universe = [t for t in active_universe if t not in ["SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP"]]

    print(f"Starting alternative disclosures seeding for {len(active_universe)} universe assets...")

    # Clear existing disclosures to ensure clean seeds
    try:
        db.query(CongressDisclosure).delete()
        db.query(InsiderDisclosure).delete()
        db.commit()
        print("Cleared existing congress and insider disclosures.")
    except Exception as e:
        db.rollback()
        print(f"Error clearing old data: {e}")

    # Set seed for reproducibility in backtests
    random.seed(42)

    # 1. Seed Congressional STOCK Act Disclosures
    print("Seeding Congressional trade disclosures...")
    congress_count = 0
    start_date = datetime(2021, 1, 1)
    end_date = datetime(2026, 6, 15)
    days_range = (end_date - start_date).days

    for ticker in active_universe:
        # Generate 6 to 12 congressional trades per ticker over the period
        num_trades = random.randint(6, 12)
        for _ in range(num_trades):
            # Select random date
            random_days = random.randint(0, days_range)
            tx_date = start_date + timedelta(days=random_days)
            # Public disclosure happens 15 to 45 days after the trade
            disc_date = tx_date + timedelta(days=random_days % 30 + 15)
            if disc_date > end_date:
                continue

            name, chamber = random.choice(POLITICIANS)
            tx_type = random.choices(["purchase", "sale"], weights=[0.65, 0.35])[0]
            
            amount_range = random.choice([
                "$1,000 - $15,000",
                "$15,001 - $50,000",
                "$50,001 - $100,000",
                "$100,001 - $250,000",
                "$250,001 - $500,000"
            ])
            # Midpoints mapping
            midpoints = {
                "$1,000 - $15,000": 8000.0,
                "$15,001 - $50,000": 32500.0,
                "$50,001 - $100,000": 75000.0,
                "$100,001 - $250,000": 175000.0,
                "$250,001 - $500,000": 375000.0
            }
            estimated_value = midpoints[amount_range]

            disc = CongressDisclosure(
                ticker=ticker,
                date=disc_date.strftime("%Y-%m-%d"),
                politician_name=name,
                chamber=chamber,
                transaction_type=tx_type,
                amount_range=amount_range,
                estimated_value=estimated_value
            )
            db.add(disc)
            congress_count += 1

    # 2. Seed Corporate Insider (SEC Form 4) Trades
    print("Seeding SEC Form 4 Corporate Insider disclosures...")
    insider_count = 0
    
    # We want to create "insider buying clusters" to give the machine learning model
    # a clear, clean statistical signal to learn from.
    # We will simulate high-conviction cluster purchase months for specific tickers,
    # alongside standard scattered insider activities.
    for ticker in active_universe:
        # Generate scattered corporate activities
        num_trades = random.randint(8, 15)
        for _ in range(num_trades):
            random_days = random.randint(0, days_range)
            tx_date = start_date + timedelta(days=random_days)
            # SEC Form 4 must be filed within 2 business days
            disc_date = tx_date + timedelta(days=random.randint(1, 2))
            if disc_date > end_date:
                continue

            insider_name = f"Insider {random.randint(100, 999)}"
            relationship = random.choice(RELATIONSHIPS)
            tx_type = random.choices(["purchase", "sale"], weights=[0.45, 0.55])[0] # sales are more common normally
            
            shares = random.randint(500, 10000)
            share_price = random.uniform(50.0, 400.0)
            total_value = shares * share_price

            insider = InsiderDisclosure(
                ticker=ticker,
                date=disc_date.strftime("%Y-%m-%d"),
                insider_name=insider_name,
                relationship=relationship,
                transaction_type=tx_type,
                shares=float(shares),
                share_price=float(share_price),
                total_value=float(total_value)
            )
            db.add(insider)
            insider_count += 1

        # Simulate 2 to 3 "Insider Buying Clusters" (e.g. CEO + CFO + Directors buying together)
        # for a subset of tickers to provide strong signal features.
        if random.random() > 0.3:
            num_clusters = random.randint(2, 3)
            for _ in range(num_clusters):
                cluster_days = random.randint(0, days_range)
                cluster_date = start_date + timedelta(days=cluster_days)
                
                # 3 to 5 insiders buying within the same 5-day window
                cluster_size = random.randint(3, 5)
                for i in range(cluster_size):
                    tx_date = cluster_date + timedelta(days=random.randint(0, 4))
                    disc_date = tx_date + timedelta(days=1)
                    if disc_date > end_date:
                        continue

                    insider_name = f"Cluster Insider {ticker} {i}"
                    relationship = "CEO" if i == 0 else ("CFO" if i == 1 else "Director")
                    shares = random.randint(2000, 15000)
                    share_price = random.uniform(10.0, 300.0)
                    total_value = shares * share_price

                    insider = InsiderDisclosure(
                        ticker=ticker,
                        date=disc_date.strftime("%Y-%m-%d"),
                        insider_name=insider_name,
                        relationship=relationship,
                        transaction_type="purchase",
                        shares=float(shares),
                        share_price=float(share_price),
                        total_value=float(total_value)
                    )
                    db.add(insider)
                    insider_count += 1

    try:
        db.commit()
        print(f"Successfully seeded alternative disclosures database:")
        print(f"  - {congress_count} Congressional disclosures loaded.")
        print(f"  - {insider_count} Corporate Insider disclosures loaded.")
    except Exception as e:
        db.rollback()
        print(f"Failed to commit seeded disclosures: {e}")
    
    db.close()


# ============================================================================
# REAL data: SEC EDGAR Form 4 insider transactions (free, no API key)
# ============================================================================

def _to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _relationship_label(rel_el):
    """Human-readable insider relationship from a Form 4 reportingOwnerRelationship element."""
    if rel_el is None:
        return "Insider"
    def is_set(tag):
        return (rel_el.findtext(tag) or "").strip().lower() in ("1", "true")
    if is_set("isOfficer"):
        return (rel_el.findtext("officerTitle") or "Officer").strip() or "Officer"
    if is_set("isDirector"):
        return "Director"
    if is_set("isTenPercentOwner"):
        return "10% Owner"
    return "Insider"


def parse_form4_xml(xml_bytes):
    """Parses a raw SEC Form 4 ownership XML (pure function, no network).

    Returns (insider_name, relationship, [transactions]) where each transaction is
    {date, code, shares, price, acquired} for NON-DERIVATIVE rows. `code` is the SEC
    transaction code (P=open-market purchase, S=open-market sale, A=award, M=exercise, ...).
    """
    root = ET.fromstring(xml_bytes)
    name = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip()
    relationship = _relationship_label(root.find(".//reportingOwner/reportingOwnerRelationship"))
    txns = []
    for t in root.findall(".//nonDerivativeTransaction"):
        date = (t.findtext(".//transactionDate/value") or "").strip()
        code = (t.findtext(".//transactionCoding/transactionCode") or "").strip()
        shares = _to_float(t.findtext(".//transactionShares/value"))
        price = _to_float(t.findtext(".//transactionPricePerShare/value"))
        ad = (t.findtext(".//transactionAcquiredDisposedCode/value") or "").strip()
        if not date or shares is None:
            continue
        txns.append({"date": date, "code": code, "shares": shares, "price": price or 0.0, "acquired": ad})
    return name, relationship, txns


def _sec_get(url, headers, raw=False, retries=3):
    """GETs an SEC URL with polite rate-limiting (<10 req/s) and basic retries."""
    backoff = 1.0
    for _ in range(retries):
        time.sleep(0.15)
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.content if raw else r.json()
            if r.status_code in (403, 429):
                time.sleep(backoff)
                backoff *= 2.0
                continue
            return None
        except Exception:
            time.sleep(0.5)
    return None


_CIK_CACHE = None


def load_cik_map(headers):
    """ticker -> CIK from SEC's free company_tickers.json (cached for the process)."""
    global _CIK_CACHE
    if _CIK_CACHE is None:
        data = _sec_get("https://www.sec.gov/files/company_tickers.json", headers)
        _CIK_CACHE = {v["ticker"]: v["cik_str"] for v in data.values()} if data else {}
    return _CIK_CACHE


def fetch_real_insider_data(lookback_days=None, max_filings_per_ticker=250):
    """Ingests REAL insider transactions from SEC EDGAR Form 4 filings, keyed on the FILING date
    (point-in-time: when the info became public, not the transaction date)."""
    lookback_days = lookback_days or INSIDER_FETCH_DAYS
    if "example.com" in SEC_USER_AGENT:
        print("NOTE: set SEC_USER_AGENT='Your Name your@email' in .env — SEC asks for a real contact.")
    headers = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    init_db()
    db = SessionLocal()
    db_tickers = db.query(UniverseTicker).all()
    universe = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)
    universe = [t for t in universe
                if t not in NON_EQUITY and t not in FOREIGN_NO_FORM4 and not t.startswith(("X:", "C:"))]

    cik_map = load_cik_map(headers)
    print(f"Fetching real SEC Form 4 insider data for {len(universe)} tickers (filings since {cutoff})...")

    total = 0
    for ticker in universe:
        cik = cik_map.get(ticker)
        if not cik:
            print(f"  {ticker}: no CIK on SEC; skipping.")
            continue
        cik10 = str(cik).zfill(10)
        subs = _sec_get(f"https://data.sec.gov/submissions/CIK{cik10}.json", headers)
        if not subs:
            print(f"  {ticker}: submissions fetch failed; skipping.")
            continue
        rec = subs.get("filings", {}).get("recent", {})
        forms = rec.get("form", [])
        f4 = [(rec["filingDate"][i], rec["accessionNumber"][i], rec["primaryDocument"][i])
              for i in range(len(forms))
              if forms[i] == "4" and rec["filingDate"][i] >= cutoff][:max_filings_per_ticker]

        # Replace any existing insider rows for this ticker (real run is authoritative).
        db.query(InsiderDisclosure).filter(InsiderDisclosure.ticker == ticker).delete()
        db.commit()

        rows = 0
        for filing_date, acc, primary in f4:
            accn = acc.replace("-", "")
            raw_doc = primary.split("/", 1)[1] if (primary.startswith("xsl") and "/" in primary) else primary
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{raw_doc}"
            xml = _sec_get(url, headers, raw=True)
            if not xml:
                continue
            try:
                name, rel, txns = parse_form4_xml(xml)
            except ET.ParseError:
                continue
            for tx in txns:
                if tx["code"] not in ("P", "S"):   # open-market purchases/sales only
                    continue
                ttype = "purchase" if tx["code"] == "P" else "sale"
                db.add(InsiderDisclosure(
                    ticker=ticker, date=filing_date, insider_name=(name or "Unknown")[:120],
                    relationship=rel, transaction_type=ttype, shares=tx["shares"],
                    share_price=tx["price"], total_value=tx["shares"] * tx["price"],
                ))
                rows += 1
        db.commit()
        total += rows
        print(f"  {ticker}: {len(f4)} Form 4 filings -> {rows} open-market P/S transactions.")

    db.close()
    print(f"Real SEC Form 4 insider ingest complete: {total} transactions. "
          f"(Congress/STOCK Act remains synthetic-only — not wired to a real source yet.)\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Alternative disclosures ingestion")
    parser.add_argument("--synthetic", action="store_true",
                        help="Seed SYNTHETIC random disclosures (plumbing only, no signal).")
    parser.add_argument("--lookback-days", type=int, default=None, help="Form 4 history window (days).")
    parser.add_argument("--max-filings", type=int, default=250, help="Max Form 4 filings per ticker.")
    args = parser.parse_args()
    if args.synthetic:
        seed_alternative_data()
    else:
        fetch_real_insider_data(lookback_days=args.lookback_days, max_filings_per_ticker=args.max_filings)
