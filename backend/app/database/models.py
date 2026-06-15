from sqlalchemy import Column, String, Float, Integer, Date, PrimaryKeyConstraint, Boolean
from app.database.connection import Base

class RecentPrice(Base):
    __tablename__ = "recent_prices"

    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)  # ISO date string YYYY-MM-DD or datetime
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    # Pre-calculated Technical Indicators from Massive.com
    sma_10 = Column(Float, nullable=True)
    sma_50 = Column(Float, nullable=True)
    rsi_14 = Column(Float, nullable=True)
    macd = Column(Float, nullable=True)
    macd_signal = Column(Float, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "date", name="pk_recent_prices"),
    )

class DailyPrice(Base):
    """Full multi-decade DAILY history (Yahoo Finance). Kept strictly separate from the
    hourly recent_prices table so the two resolutions are never mixed in features.
    Feeds long-term / regime models and the long-horizon benchmark comparison."""
    __tablename__ = "daily_prices"

    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)  # ISO date string YYYY-MM-DD
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    sma_10 = Column(Float, nullable=True)
    sma_50 = Column(Float, nullable=True)
    rsi_14 = Column(Float, nullable=True)
    macd = Column(Float, nullable=True)
    macd_signal = Column(Float, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "date", name="pk_daily_prices"),
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
    __tablename__ = "ticker_sentiments"

    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)   # YYYY-MM-DD
    sentiment_score = Column(Float, default=0.0)
    positive_ratio = Column(Float, default=0.0)
    negative_ratio = Column(Float, default=0.0)
    mention_count = Column(Integer, default=0)
    source = Column(String, nullable=False)  # 'news' or 'reddit'
    is_mock = Column(Boolean, nullable=True, default=False)

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

    ticker = Column(String, nullable=False)
    mode = Column(String, nullable=False, default="real")
    quantity = Column(Float, nullable=False, default=0.0)
    entry_price = Column(Float, nullable=False, default=0.0)
    policy = Column(String, nullable=False, default="rebalance")  # 'rebalance', 'lock', 'liquidate'
    purchase_date = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "mode", name="pk_virtual_positions"),
    )


class VirtualOrder(Base):
    __tablename__ = "virtual_orders"

    id = Column(String, primary_key=True)
    mode = Column(String, nullable=False, default="real")
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


class SentimentSourceLog(Base):
    __tablename__ = "sentiment_source_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)   # YYYY-MM-DD
    source = Column(String, nullable=False)  # 'news', 'reddit', 'premium'
    title = Column(String, nullable=False)
    text = Column(String, nullable=True)
    url = Column(String, nullable=True)
    score = Column(Float, nullable=False)
    is_mock = Column(Boolean, nullable=True, default=False)

class CongressDisclosure(Base):
    __tablename__ = "congress_disclosures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)   # YYYY-MM-DD (disclosure date)
    politician_name = Column(String, nullable=False)
    chamber = Column(String, nullable=True) # 'house' or 'senate'
    transaction_type = Column(String, nullable=False) # 'purchase' or 'sale'
    amount_range = Column(String, nullable=True)
    estimated_value = Column(Float, nullable=False) # midpoint estimate of transaction value

class InsiderDisclosure(Base):
    __tablename__ = "insider_disclosures"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    date = Column(String, nullable=False)   # YYYY-MM-DD (disclosure date)
    insider_name = Column(String, nullable=False)
    relationship = Column(String, nullable=True) # 'CEO', 'CFO', 'Director', etc.
    transaction_type = Column(String, nullable=False) # 'purchase' or 'sale'
    shares = Column(Float, nullable=False)
    share_price = Column(Float, nullable=False)
    total_value = Column(Float, nullable=False)

