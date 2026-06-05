import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import SessionLocal, RecentPrice

db = SessionLocal()
records = db.query(RecentPrice).filter(RecentPrice.ticker == "NVDA").order_by(RecentPrice.date.desc()).limit(10).all()
print("Latest NVDA prices in DB:")
for r in records:
    print(f"Date: {r.date} | Close: {r.close}")
db.close()
