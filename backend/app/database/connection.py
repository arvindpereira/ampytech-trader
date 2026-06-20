import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Import unified DB_PATH
from app.core.config import DB_PATH
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # Safe for SQLite multithreaded usage in FastAPI
)

from sqlalchemy.event import listens_for
@listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

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

    # Auto-migration for purchase_date, is_mock and mode columns if not present
    from sqlalchemy import inspect, text
    inspector = inspect(engine)

    # 1. Migrate virtual_positions
    try:
        columns = [c["name"] for c in inspector.get_columns("virtual_positions")]
        if columns and "mode" not in columns:
            with engine.connect() as conn:
                conn.execute(text("CREATE TABLE virtual_positions_new (ticker VARCHAR, mode VARCHAR DEFAULT 'real', quantity FLOAT DEFAULT 0.0, entry_price FLOAT DEFAULT 0.0, policy VARCHAR DEFAULT 'rebalance', purchase_date VARCHAR, PRIMARY KEY (ticker, mode))"))
                if "purchase_date" in columns:
                    conn.execute(text("INSERT INTO virtual_positions_new (ticker, quantity, entry_price, policy, purchase_date) SELECT ticker, quantity, entry_price, policy, purchase_date FROM virtual_positions"))
                else:
                    conn.execute(text("INSERT INTO virtual_positions_new (ticker, quantity, entry_price, policy) SELECT ticker, quantity, entry_price, policy FROM virtual_positions"))
                conn.execute(text("DROP TABLE virtual_positions"))
                conn.execute(text("ALTER TABLE virtual_positions_new RENAME TO virtual_positions"))
                conn.commit()
            print("Successfully migrated virtual_positions table to include mode column.")
        elif columns and "purchase_date" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE virtual_positions ADD COLUMN purchase_date VARCHAR"))
                conn.commit()
            print("Successfully added purchase_date column to virtual_positions table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed or table virtual_positions does not exist yet: {em}")

    # 2. Migrate sentiment_source_logs
    try:
        columns_logs = [c["name"] for c in inspector.get_columns("sentiment_source_logs")]
        if columns_logs and "is_mock" not in columns_logs:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE sentiment_source_logs ADD COLUMN is_mock BOOLEAN DEFAULT 0"))
                conn.commit()
            print("Successfully added is_mock column to sentiment_source_logs table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for is_mock on sentiment_source_logs: {em}")

    # 3. Migrate ticker_sentiments
    try:
        columns_sent = [c["name"] for c in inspector.get_columns("ticker_sentiments")]
        if columns_sent and "is_mock" not in columns_sent:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE ticker_sentiments ADD COLUMN is_mock BOOLEAN DEFAULT 0"))
                conn.commit()
            print("Successfully added is_mock column to ticker_sentiments table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for is_mock on ticker_sentiments: {em}")

    # 4. Migrate virtual_orders
    try:
        columns_orders = [c["name"] for c in inspector.get_columns("virtual_orders")]
        if columns_orders and "mode" not in columns_orders:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE virtual_orders ADD COLUMN mode VARCHAR DEFAULT 'real'"))
                conn.commit()
            print("Successfully added mode column to virtual_orders table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for mode on virtual_orders: {em}")

    try:
        columns_prices = [c["name"] for c in inspector.get_columns("recent_prices")]
        for col_name in ["sma_10", "sma_50", "rsi_14", "macd", "macd_signal"]:
            if col_name not in columns_prices:
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE recent_prices ADD COLUMN {col_name} FLOAT"))
                    conn.commit()
                print(f"Successfully added {col_name} column to recent_prices table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for technical indicators on recent_prices: {em}")

    # 5. Migrate universe_tickers: add per-ticker strategy column
    try:
        columns_uni = [c["name"] for c in inspector.get_columns("universe_tickers")]
        if columns_uni and "strategy" not in columns_uni:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE universe_tickers ADD COLUMN strategy VARCHAR DEFAULT 'swing'"))
                conn.commit()
            print("Successfully added strategy column to universe_tickers table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for strategy on universe_tickers: {em}")

    # 6. Migrate news_llm_scores: add source column (headlines vs premium newsletters)
    try:
        columns_news = [c["name"] for c in inspector.get_columns("news_llm_scores")]
        if columns_news and "source" not in columns_news:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE news_llm_scores ADD COLUMN source VARCHAR DEFAULT 'polygon'"))
                conn.commit()
            print("Successfully added source column to news_llm_scores table via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for source on news_llm_scores: {em}")

    # 7. Migrate ticker_classification: add tier_override column (manual tier that wins over computed)
    try:
        cols_cls = [c["name"] for c in inspector.get_columns("ticker_classification")]
        if cols_cls and "tier_override" not in cols_cls:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE ticker_classification ADD COLUMN tier_override VARCHAR"))
                conn.commit()
            print("Successfully added tier_override column to ticker_classification via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for tier_override on ticker_classification: {em}")

    # 8. Migrate equity_vest_schedules: vesting_complete flag
    try:
        cols_vs = [c["name"] for c in inspector.get_columns("equity_vest_schedules")]
        if cols_vs and "vesting_complete" not in cols_vs:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE equity_vest_schedules ADD COLUMN vesting_complete BOOLEAN DEFAULT 0"))
                conn.commit()
            print("Successfully added vesting_complete column to equity_vest_schedules via auto-migration.")
    except Exception as em:
        print(f"Auto-migration check failed for vesting_complete on equity_vest_schedules: {em}")


    # Seeding Universe and Account
    from app.core.config import TICKER_UNIVERSE
    db = SessionLocal()
    try:
        # 1. Seed/Merge Universe tickers
        seeded_new = 0
        for ticker in TICKER_UNIVERSE:
            existing = db.query(models.UniverseTicker).filter(models.UniverseTicker.ticker == ticker).first()
            if not existing:
                db.add(models.UniverseTicker(ticker=ticker))
                seeded_new += 1
        if seeded_new > 0:
            db.commit()
            print(f"Seeded {seeded_new} new tickers into the database universe.")

        # 2. Seed VirtualAccount if empty
        if db.query(models.VirtualAccount).filter(models.VirtualAccount.id == 1).first() is None:
            account = models.VirtualAccount(id=1, cash=100000.0, buying_power=100000.0, equity=100000.0)
            db.add(account)
            db.commit()
            print("Seeded default virtual account ($100k, ID 1).")

        if db.query(models.VirtualAccount).filter(models.VirtualAccount.id == 2).first() is None:
            account = models.VirtualAccount(id=2, cash=100000.0, buying_power=100000.0, equity=100000.0)
            db.add(account)
            db.commit()
            print("Seeded default real/live account ($100k, ID 2).")
    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
    finally:
        db.close()
