import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Define database file path inside backend/data/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "trading_system.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # Safe for SQLite multithreaded usage in FastAPI
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Dependency helper to yield database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Helper function to create all tables in the database and seed default values."""
    from app.database import models
    Base.metadata.create_all(bind=engine)

    # Seeding Universe and Account
    from app.core.config import TICKER_UNIVERSE
    db = SessionLocal()
    try:
        # 1. Seed Universe tickers if empty
        if db.query(models.UniverseTicker).count() == 0:
            for ticker in TICKER_UNIVERSE:
                db.add(models.UniverseTicker(ticker=ticker))
            db.commit()
            print("Seeded default ticker universe.")

        # 2. Seed VirtualAccount if empty
        if db.query(models.VirtualAccount).filter(models.VirtualAccount.id == 1).first() is None:
            account = models.VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
            db.add(account)
            db.commit()
            print("Seeded default virtual account ($100k).")
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
    finally:
        db.close()
