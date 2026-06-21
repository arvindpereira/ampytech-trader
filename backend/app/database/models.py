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
    # Which trading strategy manages this ticker: 'swing' (multi-day + news),
    # 'longterm' (MPT/regime rebalancing), or 'hold' (monitor only, never trade).
    strategy = Column(String, default="swing")


class AppSetting(Base):
    """Generic key/value settings store (e.g. strategy bucket capital allocations as JSON)."""
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(String)


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


class EquityLot(Base):
    __tablename__ = "equity_lots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False)
    account_label = Column(String, nullable=True)
    lot_type = Column(String, nullable=False, default="other")  # rsu | espp | other
    shares = Column(Float, nullable=False)
    cost_basis_per_share = Column(Float, nullable=False)
    acquisition_date = Column(String, nullable=False)  # YYYY-MM-DD
    notes = Column(String, nullable=True)
    created_at = Column(String, nullable=False)


class EquityVestSchedule(Base):
    """Expected future vest/purchase dates for external equity holdings (RSU/ESPP cadence)."""
    __tablename__ = "equity_vest_schedules"

    ticker = Column(String, nullable=False)
    lot_type = Column(String, nullable=False, default="rsu")  # rsu | espp
    cadence = Column(String, nullable=False, default="quarterly")  # quarterly | semi_annual | monthly | annual
    vest_day = Column(Integer, nullable=True)  # day-of-month (e.g. 20 or 23)
    vest_months = Column(String, nullable=True)  # JSON list of months 1-12 when cadence=quarterly/semi_annual
    next_vest_date = Column(String, nullable=False)  # YYYY-MM-DD
    est_shares = Column(Float, nullable=True)
    vesting_complete = Column(Boolean, nullable=False, default=False)  # no future grants expected
    notes = Column(String, nullable=True)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "lot_type", name="pk_equity_vest_schedules"),
    )


class EquityAutoTradeBlock(Base):
    """Per-ticker bot-trading block preference for externally held equity (survives in DB backups)."""
    __tablename__ = "equity_auto_trade_blocks"

    ticker = Column(String, primary_key=True)
    blocked = Column(Boolean, nullable=False, default=True)
    updated_at = Column(String, nullable=False)


class TaxProfile(Base):
    __tablename__ = "tax_profile"

    id = Column(Integer, primary_key=True, default=1)
    filing_status = Column(String, nullable=False, default="single")
    ordinary_income = Column(Float, nullable=False, default=0.0)
    magi = Column(Float, nullable=False, default=0.0)
    state_ltcg_rate = Column(Float, nullable=False, default=0.0)
    state_stcg_rate = Column(Float, nullable=False, default=0.0)
    carryover_loss = Column(Float, nullable=False, default=0.0)
    tax_year = Column(Integer, nullable=False, default=2026)


class AnalystForecast(Base):
    __tablename__ = "ticker_analyst_forecasts"

    ticker = Column(String, nullable=False)
    as_of_date = Column(String, nullable=False)
    current_price = Column(Float, nullable=True)
    target_mean = Column(Float, nullable=True)
    target_high = Column(Float, nullable=True)
    target_low = Column(Float, nullable=True)
    target_median = Column(Float, nullable=True)
    num_analysts = Column(Integer, nullable=True)
    recommendation_mean = Column(Float, nullable=True)
    recommendation_key = Column(String, nullable=True)
    strong_buy = Column(Integer, nullable=True)
    buy = Column(Integer, nullable=True)
    hold = Column(Integer, nullable=True)
    sell = Column(Integer, nullable=True)
    strong_sell = Column(Integer, nullable=True)
    upside_pct = Column(Float, nullable=True)
    source = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "as_of_date", name="pk_ticker_analyst_forecasts"),
    )


class TradingBlock(Base):
    """A guard that prevents the auto-trader from BUYING a ticker. Two kinds:

    - 'wash_sale': time-boxed. Recorded when you harvest a loss (here or in an external
      account like Schwab) so the bot can't re-buy the same name inside the 30-day IRS
      wash-sale window and disallow the loss. Auto-expires at `blocked_until`.
    - 'permanent': open-ended (blocked_until = NULL). For names you hold/manage externally
      and never want the bot to accumulate (e.g. employer RSUs).

    Releasing a block sets `active=False` rather than deleting it, so the history is auditable.
    The global auto-trading kill-switch lives separately in AppSetting ('auto_trading_paused').
    """
    __tablename__ = "trading_blocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=False, index=True)
    block_type = Column(String, nullable=False, default="wash_sale")  # 'wash_sale' | 'permanent'
    reason = Column(String, nullable=True)
    account_label = Column(String, nullable=True)
    sale_date = Column(String, nullable=True)          # YYYY-MM-DD (wash_sale only)
    realized_loss = Column(Float, nullable=True)        # signed; negative = harvested loss
    shares = Column(Float, nullable=True)
    blocked_until = Column(String, nullable=True)       # YYYY-MM-DD; NULL = permanent
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(String, nullable=False)


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


class NewsLLMScore(Base):
    """Per-headline directional sentiment from a local LLM (Ollama), for the SWING model. One row per
    (ticker, article). `date` is the publication calendar date (features shift it +1 day to stay
    look-ahead free). `llm_score` in [-1,1], `llm_relevance` in [0,1]."""
    __tablename__ = "news_llm_scores"

    ticker = Column(String, nullable=False)
    article_id = Column(String, nullable=False)      # Polygon article id (natural dedupe key)
    date = Column(String, nullable=False)            # YYYY-MM-DD (publish date)
    published_utc = Column(String, nullable=True)    # full ISO timestamp
    title = Column(String, nullable=True)
    llm_score = Column(Float, nullable=False, default=0.0)
    llm_relevance = Column(Float, nullable=False, default=0.0)
    model = Column(String, nullable=True)                    # the LLM that scored it (gpt-4o-mini, gemma4:e4b)
    source = Column(String, nullable=True, default="polygon")  # 'polygon' (headlines) | 'premium:the-information' | …

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "article_id", name="pk_news_llm_scores"),
    )


class LLMUsage(Base):
    """Ledger of every LLM/model call the server makes — across providers (OpenAI gpt-5.5 / gpt-4o-mini,
    local Ollama gemma4, …). Token counts are the ground truth; cost is (re)estimated from the pricing
    table, so it stays accurate even after pricing is calibrated against the real OpenAI dashboard."""
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(String, nullable=False)              # ISO datetime of the call
    date = Column(String, nullable=False)            # YYYY-MM-DD (for daily rollups / filtering)
    provider = Column(String, nullable=True)         # openai | ollama | local
    model = Column(String, nullable=False)           # gpt-5_5-2026-04-23, gpt-4o-mini, gemma4:e4b, …
    purpose = Column(String, nullable=True)          # eval_interpret, news_scoring, …
    requests = Column(Integer, nullable=False, default=1)   # underlying model/API calls in this event
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    batch = Column(Boolean, nullable=False, default=False)  # OpenAI Batch API (50% off)
    est_cost = Column(Float, nullable=True)          # snapshot estimate at write time (USD)


class TickerFundamental(Base):
    """Per-period company financials (Polygon/Massive vX/reference/financials) + derived ratios.
    Feeds the fundamental-quality signal that separates strong-fundamentals dips (accumulate long-term)
    from weak-fundamentals volatility (speculative). One row per (ticker, period end)."""
    __tablename__ = "ticker_fundamentals"

    ticker = Column(String, nullable=False)
    end_date = Column(String, nullable=False)            # fiscal period end (YYYY-MM-DD)
    fiscal_period = Column(String, nullable=True)        # Q1/Q2/Q3/Q4/FY
    fiscal_year = Column(String, nullable=True)
    # raw key line items (USD)
    revenues = Column(Float, nullable=True)
    gross_profit = Column(Float, nullable=True)
    operating_income = Column(Float, nullable=True)
    net_income = Column(Float, nullable=True)
    op_cash_flow = Column(Float, nullable=True)
    capex = Column(Float, nullable=True)
    total_assets = Column(Float, nullable=True)
    total_liabilities = Column(Float, nullable=True)
    equity = Column(Float, nullable=True)
    current_assets = Column(Float, nullable=True)
    current_liabilities = Column(Float, nullable=True)
    shares = Column(Float, nullable=True)
    # derived ratios
    gross_margin = Column(Float, nullable=True)
    operating_margin = Column(Float, nullable=True)
    net_margin = Column(Float, nullable=True)
    fcf = Column(Float, nullable=True)                   # op_cash_flow - capex
    fcf_margin = Column(Float, nullable=True)
    roe = Column(Float, nullable=True)
    debt_to_equity = Column(Float, nullable=True)
    current_ratio = Column(Float, nullable=True)
    source = Column(String, nullable=True, default="polygon")
    fetched_at = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "end_date", name="pk_ticker_fundamentals"),
    )


class TickerClassification(Base):
    """Consolidated risk × fundamental-quality classification per ticker. quant_quality (ratios) +
    llm_quality (qualitative overlay) blend into `quality`; combined with `volatility`/`dd_2022` it yields
    a `tier` that routes the ticker: core swing/long-term, quality-growth (accumulate dips long-term),
    speculative (small high-risk bucket), or value-trap (avoid)."""
    __tablename__ = "ticker_classification"

    ticker = Column(String, primary_key=True)
    quant_quality = Column(Float, nullable=True)     # 0-1 from financial ratios (step 2b)
    llm_quality = Column(Float, nullable=True)       # 0-1 qualitative overlay (step 2c)
    quality = Column(Float, nullable=True)           # blended 0-1
    volatility = Column(Float, nullable=True)        # trailing annualized daily-return vol
    dd_2022 = Column(Float, nullable=True)           # 2022 max drawdown (bear stress)
    distressed = Column(Boolean, nullable=True)
    tier = Column(String, nullable=True)             # effective tier (computed, or the manual override)
    tier_override = Column(String, nullable=True)    # user-set tier that wins over the computed one
    llm_flags = Column(String, nullable=True)        # JSON list (one_off_gain, bank, turnaround, …)
    llm_verdict = Column(String, nullable=True)
    llm_model = Column(String, nullable=True)
    updated_at = Column(String, nullable=True)


class CrashRiskSnapshot(Base):
    """Stores historical and current Composite Crash-Risk Index outputs, subscores,
    debt-cycle states, and experimental forecasting odds for Tab 5 (Crash Radar)."""
    __tablename__ = "crash_risk_snapshots"

    as_of_date = Column(String, primary_key=True)   # YYYY-MM-DD
    composite_index = Column(Float, nullable=False) # 0 to 100
    risk_band = Column(String, nullable=False)      # Calm / Elevated / High / Extreme
    current_posture = Column(String, nullable=False)# Normal / Froth / De-risk / Protect / Deploy / Recover
    trigger_reasons = Column(String, nullable=True) # JSON list of strings (trigger events/reasons)

    # Subscores for the 10 buckets
    valuation_subscore = Column(Float, nullable=True)
    monetary_subscore = Column(Float, nullable=True)
    credit_subscore = Column(Float, nullable=True)
    financial_conditions_subscore = Column(Float, nullable=True)
    lending_subscore = Column(Float, nullable=True)
    labor_subscore = Column(Float, nullable=True)
    real_activity_subscore = Column(Float, nullable=True)
    internals_subscore = Column(Float, nullable=True)
    cycle_subscore = Column(Float, nullable=True)
    hmm_regime_subscore = Column(Float, nullable=True)

    # Debt cycle qualitative and quantitative metrics
    debt_cycle_read = Column(String, nullable=True)          # JSON string mapping cycle variables
    experimental_forecast_odds = Column(String, nullable=True) # JSON string of P(DD >= X% in N days)
    created_at = Column(String, nullable=True)


class ExternalAccount(Base):
    __tablename__ = "external_accounts"

    account_label = Column(String, primary_key=True)
    cash = Column(Float, nullable=False, default=0.0)
    risk_profile = Column(String, nullable=False, default="balanced")  # 'conservative', 'balanced', 'aggressive'
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class ExternalOrder(Base):
    __tablename__ = "external_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_label = Column(String, nullable=False)
    ticker = Column(String, nullable=False)
    side = Column(String, nullable=False)  # 'BUY' | 'SELL'
    qty = Column(Float, nullable=False)
    limit_price = Column(Float, nullable=False)
    time_in_force = Column(String, nullable=False)  # 'DAY' | 'GTC_90'
    status = Column(String, nullable=False, default="proposed")  # 'proposed', 'confirmed_filled', 'cancelled'
    filled_price = Column(Float, nullable=True)
    filled_qty = Column(Float, nullable=True)
    execution_date = Column(String, nullable=True)  # YYYY-MM-DD
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class ExternalTransaction(Base):
    __tablename__ = "external_transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_label = Column(String, nullable=False)
    ticker = Column(String, nullable=False)
    side = Column(String, nullable=False)  # 'BUY' | 'SELL'
    qty = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    execution_date = Column(String, nullable=False)  # YYYY-MM-DD
    raw_details = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
