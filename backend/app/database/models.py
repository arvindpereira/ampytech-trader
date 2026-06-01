from sqlalchemy import Column, String, Float, Integer, Date, PrimaryKeyConstraint
from app.database.connection import Base

class RecentPrice(Base):
    __tablename__ = "recent_prices"
    
    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)  # ISO date string YYYY-MM-DD
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    
    __table_args__ = (
        PrimaryKeyConstraint("ticker", "date", name="pk_recent_prices"),
    )

class CrisisPrice(Base):
    __tablename__ = "crisis_prices"
    
    ticker = Column(String, nullable=False)
    era = Column(String, nullable=False)    # 'dotcom', 'gfc', 'covid'
    date = Column(String, nullable=False)   # ISO date string YYYY-MM-DD
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    
    __table_args__ = (
        PrimaryKeyConstraint("ticker", "era", "date", name="pk_crisis_prices"),
    )

class MacroIndicator(Base):
    __tablename__ = "macro_indicators"
    
    date = Column(String, nullable=False)   # ISO date string YYYY-MM-DD
    indicator_name = Column(String, nullable=False)  # 'fed_funds', 'yield_spread', etc.
    value = Column(Float, nullable=False)
    
    __table_args__ = (
        PrimaryKeyConstraint("date", "indicator_name", name="pk_macro_indicators"),
    )

class TickerSentiment(Base):
    __tablename__ = "ticker_sentiment"
    
    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)   # ISO date string YYYY-MM-DD
    sentiment_score = Column(Float, nullable=False)  # Average polarity (-1.0 to 1.0)
    positive_ratio = Column(Float, default=0.0)
    negative_ratio = Column(Float, default=0.0)
    mention_count = Column(Integer, default=0)
    source = Column(String, nullable=False)  # 'news' or 'reddit'
    
    __table_args__ = (
        PrimaryKeyConstraint("ticker", "date", "source", name="pk_ticker_sentiment"),
    )

class ExecutedTrade(Base):
    __tablename__ = "executed_trades"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)
    action = Column(String, nullable=False)    # 'BUY', 'SELL'
    price = Column(Float, nullable=False)
    shares = Column(Float, nullable=False)
    value = Column(Float, nullable=False)
    status = Column(String, default="filled")  # 'filled' or 'simulated'


class UniverseTicker(Base):
    __tablename__ = "universe_tickers"
    
    ticker = Column(String, primary_key=True)


class VirtualAccount(Base):
    __tablename__ = "virtual_accounts"
    
    id = Column(Integer, primary_key=True, default=1)
    cash = Column(Float, nullable=False, default=100000.0)
    buying_power = Column(Float, nullable=False, default=100000.0)
    equity = Column(Float, nullable=False, default=100000.0)


class VirtualPosition(Base):
    __tablename__ = "virtual_positions"
    
    ticker = Column(String, primary_key=True)
    quantity = Column(Float, nullable=False, default=0.0)
    entry_price = Column(Float, nullable=False, default=0.0)
    policy = Column(String, nullable=False, default="rebalance")  # 'rebalance', 'lock', 'liquidate'


class VirtualOrder(Base):
    __tablename__ = "virtual_orders"
    
    id = Column(String, primary_key=True)
    ticker = Column(String, nullable=False)
    qty = Column(Float, nullable=False)
    side = Column(String, nullable=False)  # 'buy' or 'sell'
    type = Column(String, nullable=False)  # 'market' etc.
    status = Column(String, nullable=False, default="pending")  # 'pending', 'filled', 'canceled'
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    filled_price = Column(Float, nullable=True)
    created_at = Column(String, nullable=False)
    sim_date = Column(String, nullable=True)


class BrokerPerformanceLog(Base):
    __tablename__ = "broker_performance_logs"
    
    date = Column(String, nullable=False)
    mode = Column(String, nullable=False)  # 'live' or 'replay'
    portfolio_value = Column(Float, nullable=False)
    spy_value = Column(Float, nullable=False)
    qqq_value = Column(Float, nullable=False)
    brk_value = Column(Float, nullable=False)
    
    __table_args__ = (
        PrimaryKeyConstraint("date", "mode", name="pk_broker_performance_logs"),
    )


