from sqlalchemy import Column, String, Float, Integer, Date, PrimaryKeyConstraint, Boolean, Text
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
    strategy_mode = Column(String, nullable=False, default="growth")
    aggression = Column(Integer, nullable=False, default=60)
    buckets_json = Column(String, nullable=True)
    # de-risk policy: 'rotate' (concentrate into quality, keep market exposure) or 'shed_beta'
    # (move high-beta weight to cash). NULL = follow the model's recommendation.
    de_risk_policy = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class ExternalStatementHolding(Base):
    """The latest month-end statement snapshot per external account — the cost-basis + share-count
    'anchor' used to reconstruct holdings from a transaction CSV. Populated automatically from an
    imported monthly statement PDF, or seeded from a data CSV (for accounts whose positions
    transferred in with no basis, e.g. the Robinhood Joint account). Replaces the formerly
    hardcoded *_PDF_HOLDINGS dicts."""
    __tablename__ = "external_statement_holdings"

    account_label = Column(String, primary_key=True)
    ticker = Column(String, primary_key=True)
    shares = Column(Float, nullable=False)
    avg_cost = Column(Float, nullable=True)            # null = basis unknown (enter manually)
    statement_date = Column(String, nullable=False)    # YYYY-MM-DD
    source = Column(String, nullable=False, default="pdf")  # 'pdf' | 'csv-seed' | 'manual'
    created_at = Column(String, nullable=False)


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


# --- Research Analyst (Tab 7) -------------------------------------------------

class TickerMetadata(Base):
    """Sector/industry/market-cap metadata for research KB."""
    __tablename__ = "ticker_metadata"

    ticker = Column(String, primary_key=True)
    sector = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    market_cap = Column(Float, nullable=True)
    company_name = Column(String, nullable=True)   # e.g. "NVIDIA Corporation"
    description = Column(String, nullable=True)    # short business description from yfinance
    ceo = Column(String, nullable=True)            # current CEO / chief executive
    website = Column(String, nullable=True)        # company homepage URL
    country = Column(String, nullable=True)        # HQ country
    employees = Column(Integer, nullable=True)     # full-time employee count
    exchange = Column(String, nullable=True)       # listing exchange (e.g. NASDAQ)
    logo_url = Column(String, nullable=True)       # company logo image URL
    source = Column(String, nullable=True)
    updated_at = Column(String, nullable=True)


class ExecutionRun(Base):
    """One row per run_execution() pass — the decision snapshot (plan) plus the orders
    actually submitted. Powers the dashboard 'last actual run' view."""
    __tablename__ = "execution_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_at = Column(String, nullable=False)        # ISO timestamp
    trigger = Column(String, nullable=True)        # 'scheduled' | 'intraday' | 'manual'
    regime = Column(String, nullable=True)
    paused = Column(Boolean, default=False)
    market_open = Column(Boolean, nullable=True)
    plan_json = Column(Text, nullable=True)        # build_execution_plan() snapshot
    orders_json = Column(Text, nullable=True)      # orders actually submitted this run


class CompanySnapshot(Base):
    """Denormalized daily company state for the research knowledge base."""
    __tablename__ = "company_snapshots"

    ticker = Column(String, nullable=False)
    as_of_date = Column(String, nullable=False)  # YYYY-MM-DD
    price = Column(Float, nullable=True)
    momentum_1w = Column(Float, nullable=True)
    momentum_1m = Column(Float, nullable=True)
    momentum_3m = Column(Float, nullable=True)
    momentum_1y = Column(Float, nullable=True)
    tier = Column(String, nullable=True)
    quality = Column(Float, nullable=True)
    volatility = Column(Float, nullable=True)
    verdict = Column(String, nullable=True)
    target_mean = Column(Float, nullable=True)
    target_high = Column(Float, nullable=True)
    target_low = Column(Float, nullable=True)
    num_analysts = Column(Integer, nullable=True)
    upside_pct = Column(Float, nullable=True)
    recommendation_key = Column(String, nullable=True)
    news_score_7d = Column(Float, nullable=True)
    news_score_30d = Column(Float, nullable=True)
    news_headline_count_30d = Column(Integer, nullable=True)
    sector = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    coverage_pct = Column(Float, nullable=True)
    facts_json = Column(String, nullable=True)  # full bundle with per-field coverage
    refreshed_at = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "as_of_date", name="pk_company_snapshots"),
    )


class ExternalAnalystItem(Base):
    """Third-party analyst/news/newsletter/search items for synthesis."""
    __tablename__ = "external_analyst_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, nullable=True)
    sector_id = Column(String, nullable=True)
    source = Column(String, nullable=False)
    source_id = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    published_at = Column(String, nullable=True)
    title = Column(String, nullable=True)
    excerpt = Column(String, nullable=True)
    analyst_firm = Column(String, nullable=True)
    rating = Column(String, nullable=True)
    target_price = Column(Float, nullable=True)
    raw_json = Column(String, nullable=True)
    created_at = Column(String, nullable=False)


class EarningsTranscript(Base):
    """Full earnings call transcripts (Finnhub Professional+)."""
    __tablename__ = "earnings_transcripts"

    ticker = Column(String, nullable=False)
    finnhub_id = Column(String, nullable=False)
    quarter = Column(Integer, nullable=True)
    year = Column(Integer, nullable=True)
    period = Column(String, nullable=True)  # e.g. 2024Q1
    call_date = Column(String, nullable=True)
    title = Column(String, nullable=True)
    content = Column(Text, nullable=True)
    summary_excerpt = Column(String, nullable=True)
    source = Column(String, nullable=False, default="finnhub:transcript")
    fetched_at = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "finnhub_id", name="pk_earnings_transcripts"),
    )


class EarningsEstimateSnapshot(Base):
    """Point-in-time EPS estimate snapshots for revision tracking."""
    __tablename__ = "earnings_estimate_snapshots"

    ticker = Column(String, nullable=False)
    period = Column(String, nullable=False)  # YYYY-MM-DD fiscal period end
    freq = Column(String, nullable=False, default="quarterly")
    as_of_date = Column(String, nullable=False)
    eps_avg = Column(Float, nullable=True)
    eps_high = Column(Float, nullable=True)
    eps_low = Column(Float, nullable=True)
    num_analysts = Column(Integer, nullable=True)
    raw_json = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "period", "freq", "as_of_date", name="pk_earnings_estimate_snapshots"),
    )


class EarningsSurprise(Base):
    """Historical reported EPS vs estimate."""
    __tablename__ = "earnings_surprises"

    ticker = Column(String, nullable=False)
    period = Column(String, nullable=False)
    report_date = Column(String, nullable=True)
    eps_actual = Column(Float, nullable=True)
    eps_estimate = Column(Float, nullable=True)
    surprise_pct = Column(Float, nullable=True)
    raw_json = Column(String, nullable=True)
    fetched_at = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "period", name="pk_earnings_surprises"),
    )


class ResearchWatchlist(Base):
    __tablename__ = "research_watchlist"

    ticker = Column(String, primary_key=True)
    added_at = Column(String, nullable=False)


class WebSearchCache(Base):
    __tablename__ = "web_search_cache"

    query_hash = Column(String, primary_key=True)
    query_text = Column(String, nullable=False)
    results_json = Column(String, nullable=False)
    fetched_at = Column(String, nullable=False)


class SectorSnapshot(Base):
    """Aggregated sector-level metrics for screening."""
    __tablename__ = "sector_snapshots"

    sector_id = Column(String, nullable=False)
    as_of_date = Column(String, nullable=False)
    ticker_count = Column(Integer, nullable=True)
    median_upside_pct = Column(Float, nullable=True)
    median_momentum_3m = Column(Float, nullable=True)
    median_news_score_30d = Column(Float, nullable=True)
    median_quality = Column(Float, nullable=True)
    etf_proxy = Column(String, nullable=True)
    facts_json = Column(String, nullable=True)
    refreshed_at = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("sector_id", "as_of_date", name="pk_sector_snapshots"),
    )


class InternalPriceTarget(Base):
    """Proprietary / blended price targets separate from sell-side consensus."""
    __tablename__ = "internal_price_targets"

    ticker = Column(String, nullable=False)
    as_of_date = Column(String, nullable=False)
    horizon_date = Column(String, nullable=False)
    target_price = Column(Float, nullable=True)
    method = Column(String, nullable=True)  # consensus_blend | momentum_adjusted
    confidence = Column(Float, nullable=True)
    notes = Column(String, nullable=True)
    refreshed_at = Column(String, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("ticker", "as_of_date", "horizon_date", name="pk_internal_price_targets"),
    )


class ResearchNewsEmbedding(Base):
    """Cached document embeddings for hybrid news retrieval (Phase 2c)."""
    __tablename__ = "research_news_embeddings"

    doc_key = Column(String, primary_key=True)
    model = Column(String, nullable=False)
    embedding_json = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class ResearchThread(Base):
    __tablename__ = "research_threads"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, nullable=False, default="draft")  # draft | published | rejected
    slug = Column(String, nullable=True)
    summary = Column(String, nullable=True)
    tickers_json = Column(String, nullable=True)
    theme = Column(String, nullable=True)
    coverage_pct = Column(Float, nullable=True)
    feedback_notes = Column(String, nullable=True)
    feedback_tags = Column(String, nullable=True)
    llm_tier = Column(String, nullable=True)
    published_at = Column(String, nullable=True)
    rejected_at = Column(String, nullable=True)
    wiki_exported_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class ResearchMessage(Base):
    __tablename__ = "research_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    thread_id = Column(String, nullable=False)
    role = Column(String, nullable=False)  # user | assistant
    content = Column(String, nullable=True)
    structured_json = Column(String, nullable=True)
    snapshot_tickers_json = Column(String, nullable=True)
    model = Column(String, nullable=True)
    tokens = Column(Integer, nullable=True)
    created_at = Column(String, nullable=False)
