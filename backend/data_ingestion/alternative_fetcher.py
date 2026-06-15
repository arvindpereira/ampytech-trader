import sys
import os
import random
from datetime import datetime, timedelta

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, CongressDisclosure, InsiderDisclosure, UniverseTicker
from app.core.config import TICKER_UNIVERSE

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

if __name__ == "__main__":
    seed_alternative_data()
