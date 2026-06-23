import pandas as pd
import numpy as np
import os
import sys

from app.database import SessionLocal, CongressDisclosure, InsiderDisclosure
from app.core.config import ALT_DATA_ENABLED, INSIDER_LOOKBACK_DAYS, CONGRESS_LOOKBACK_DAYS

def load_alternative_data_from_db():
    if not ALT_DATA_ENABLED:
        return pd.DataFrame(), pd.DataFrame()
    db = SessionLocal()
    try:
        congress = db.query(CongressDisclosure).all()
        insider = db.query(InsiderDisclosure).all()
        congress_df = pd.DataFrame([{
            "ticker": c.ticker, "date": c.date, "estimated_value": c.estimated_value, "transaction_type": c.transaction_type
        } for c in congress]) if congress else pd.DataFrame()
        insider_df = pd.DataFrame([{
            "ticker": i.ticker, "date": i.date, "total_value": i.total_value,
            "transaction_type": i.transaction_type, "relationship": i.relationship,
            "insider_name": i.insider_name,
        } for i in insider]) if insider else pd.DataFrame()
        return congress_df, insider_df
    except Exception as e:
        print(f"Error loading alternative disclosures from DB: {e}")
        return pd.DataFrame(), pd.DataFrame()
    finally:
        db.close()

def compute_rsi(prices, window=14):
    """Computes the Relative Strength Index (RSI) using native pandas."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()

    rs = gain / (loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_atr(df, window=14):
    """Computes the Average True Range (ATR) using native pandas."""
    high = df['high']
    low = df['low']
    close_shift = df['close'].shift(1)

    tr1 = high - low
    tr2 = (high - close_shift).abs()
    tr3 = (low - close_shift).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=window).mean()
    return atr

def compute_macd(prices, fast=12, slow=26, signal=9):
    """Computes MACD and Signal Line."""
    fast_ema = prices.ewm(span=fast, adjust=False).mean()
    slow_ema = prices.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def triple_barrier_outcomes(high, low, close, atr, horizon,
                            atr_stop_mult=2.0, tp_mult=2.5, stop_min=0.015, stop_max=0.05):
    """Path-dependent triple-barrier outcomes matching the executed trade.

    For an entry at `close[i]`, place an ATR-based stop (`stop_min..stop_max` clipped
    `atr_stop_mult*ATR/close`) and a take-profit at `tp_mult * stop`. Scanning forward up to
    `horizon` bars using intrabar highs/lows, returns two aligned arrays:

      * `label`  — 1 if the take-profit is touched BEFORE the stop; 0 if the stop hits first
                   or neither hits within the horizon (timeout); same-bar ambiguity counts as a
                   loss (conservative); NaN where ATR is undefined / window censored.
      * `ret`    — the realised fractional return of that bracketed trade (gross, no fees):
                   +tp% on a win, -sl% on a stop/ambiguous, and the close-to-close return at the
                   vertical barrier on a timeout. NaN where the label is NaN.

    `label` is the training target ("would this trade have won?"); `ret` lets walk-forward
    evaluation compute the actual P&L of selected entries (it is never used as a feature).
    """
    n = len(close)
    label = np.zeros(n, dtype=float)
    ret = np.zeros(n, dtype=float)
    resolved = np.zeros(n, dtype=bool)

    sl_pct = np.clip(atr_stop_mult * atr / close, stop_min, stop_max)
    tp_pct = sl_pct * tp_mult
    tp_price = close * (1.0 + tp_pct)
    sl_price = close * (1.0 - sl_pct)

    for k in range(1, horizon + 1):
        fut_high = np.full(n, np.nan)
        fut_low = np.full(n, np.nan)
        if k < n:
            fut_high[:n - k] = high[k:]
            fut_low[:n - k] = low[k:]
        valid = ~np.isnan(fut_high)
        tp_hit = valid & (~resolved) & (fut_high >= tp_price)
        sl_hit = valid & (~resolved) & (fut_low <= sl_price)
        only_tp = tp_hit & ~sl_hit             # take-profit alone this bar -> win
        sl_first = sl_hit                       # incl. same-bar both (conservative loss)
        label[only_tp] = 1.0
        ret[only_tp] = tp_pct[only_tp]
        loss_mask = sl_first & ~only_tp
        ret[loss_mask] = -sl_pct[loss_mask]
        resolved[tp_hit | sl_hit] = True

    idx = np.arange(n)
    # Timeouts (no barrier hit but full window available): exit at the horizon bar's close.
    exit_close = np.full(n, np.nan)
    if horizon < n:
        exit_close[:n - horizon] = close[horizon:]
    timeout = (~resolved) & (idx + horizon < n)
    ret[timeout] = exit_close[timeout] / close[timeout] - 1.0

    censored = (~resolved) & (idx + horizon >= n)   # window runs off the end -> outcome unknown
    bad = censored | np.isnan(atr) | np.isnan(tp_price) | np.isnan(sl_price)
    label[bad] = np.nan
    ret[bad] = np.nan
    return label, ret


def triple_barrier_labels(high, low, close, atr, horizon,
                          atr_stop_mult=2.0, tp_mult=2.5, stop_min=0.015, stop_max=0.05):
    """Convenience wrapper returning only the win label (see `triple_barrier_outcomes`)."""
    label, _ = triple_barrier_outcomes(high, low, close, atr, horizon,
                                       atr_stop_mult, tp_mult, stop_min, stop_max)
    return label


def build_features_for_df(df, sentiment_df=None, macro_df=None,
                          target_horizon_bars=14, target_atr_stop_mult=2.0,
                          target_tp_mult=2.5, target_stop_min=0.015, target_stop_max=0.05,
                          congress_df=None, insider_df=None):
    """
    Computes all features for a single ticker's DataFrame.
    Assumes df contains columns: ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume'].
    Assumes df is sorted chronologically by date.

    `df['date']` may be an hourly timestamp ('YYYY-MM-DD HH:MM:SS') or a daily date
    ('YYYY-MM-DD'). Daily-grained sentiment and macro series are joined on the calendar
    date so they correctly broadcast across all intraday bars of a day.

    The target is a triple-barrier WIN label over `target_horizon_bars` bars using ATR-based
    stop/take-profit brackets (see `triple_barrier_labels`).
    """
    df = df.copy()

    # Calendar-date key for joining daily-grained series onto (possibly hourly) bars.
    df['cal_date'] = df['date'].astype(str).str.slice(0, 10)

    # --- Technical Indicators ---
    df['returns'] = df['close'].pct_change()
    df['volatility_10'] = df['returns'].rolling(window=10).std()

    # Parkinson Volatility (10-bar window) - extreme value range volatility
    ln_hl_sq = np.log(df['high'] / (df['low'] + 1e-9)) ** 2
    df['parkinson_vol_10'] = np.sqrt(ln_hl_sq.rolling(window=10).sum() / (4 * np.log(2) * 10))

    # Moving Averages
    if 'sma_10' in df.columns and df['sma_10'].notna().sum() > 10:
        df['sma_10'] = df['sma_10'].ffill().bfill()
    else:
        df['sma_10'] = df['close'].rolling(window=10).mean()

    if 'sma_50' in df.columns and df['sma_50'].notna().sum() > 10:
        df['sma_50'] = df['sma_50'].ffill().bfill()
    else:
        df['sma_50'] = df['close'].rolling(window=50).mean()

    df['ma_ratio'] = df['sma_10'] / (df['sma_50'] + 1e-9)

    # RSI & MACD
    if 'rsi_14' in df.columns and df['rsi_14'].notna().sum() > 10:
        df['rsi_14'] = df['rsi_14'].ffill().bfill()
    else:
        df['rsi_14'] = compute_rsi(df['close'], window=14)

    if 'macd' in df.columns and 'macd_signal' in df.columns and df['macd'].notna().sum() > 10:
        df['macd'] = df['macd'].ffill().bfill()
        df['macd_signal'] = df['macd_signal'].ffill().bfill()
    else:
        macd, macd_sig = compute_macd(df['close'])
        df['macd'] = macd
        df['macd_signal'] = macd_sig

    # Bollinger Bands
    df['bb_mid'] = df['close'].rolling(window=20).mean()
    df['bb_std'] = df['close'].rolling(window=20).std()
    df['bb_width'] = (2 * df['bb_std']) / (df['bb_mid'] + 1e-9)

    # ATR for volatility sizing
    df['atr_14'] = compute_atr(df, window=14)

    # --- Stationarity & Price-Level Normalization ---
    df['volume_ratio'] = df['volume'] / (df['volume'].rolling(window=20).mean() + 1e-9)
    df['atr_ratio'] = df['atr_14'] / (df['close'] + 1e-9)
    df['close_to_sma10'] = df['close'] / (df['sma_10'] + 1e-9) - 1.0
    df['close_to_sma50'] = df['close'] / (df['sma_50'] + 1e-9) - 1.0
    df['high_low_ratio'] = (df['high'] - df['low']) / (df['close'] + 1e-9)
    df['close_to_bb_mid'] = df['close'] / (df['bb_mid'] + 1e-9) - 1.0
    df['returns_vol_adj'] = df['returns'] / (df['volatility_10'] + 1e-9)
    df['macd_ratio'] = df['macd'] / (df['close'] + 1e-9)
    df['macd_signal_ratio'] = df['macd_signal'] / (df['close'] + 1e-9)

    # --- Merge Sentiment ---
    if sentiment_df is not None and not sentiment_df.empty:
        # Pivot sentiment to get separate columns for 'news' and 'reddit' scores
        sent_pivot = sentiment_df.pivot_table(
            index='date',
            columns='source',
            values=['sentiment_score', 'mention_count'],
            fill_value=0.0
        )
        # Flatten multi-index columns
        sent_pivot.columns = [f"{col[1]}_{col[0]}" for col in sent_pivot.columns]
        sent_pivot = sent_pivot.reset_index().rename(columns={'date': 'cal_date'}).sort_values('cal_date')

        # CRITICAL: a day's news aggregate (count/score) summarises the WHOLE day, so using it
        # for that day's intraday bars would leak future (rest-of-day) information. Shift the
        # daily series by one day so each trading day only sees the PREVIOUS day's sentiment.
        sent_val_cols = [c for c in sent_pivot.columns if c != 'cal_date']
        sent_pivot[sent_val_cols] = sent_pivot[sent_val_cols].shift(1)

        # Merge by calendar date so prior-day sentiment broadcasts across all of a day's bars.
        df = pd.merge(df, sent_pivot, on='cal_date', how='left')

        # Fill missing sentiment values with neutral (0) or 0 count
        for col in ['news_sentiment_score', 'reddit_sentiment_score']:
            if col in df.columns:
                df[col] = df[col].fillna(0.0)
            else:
                df[col] = 0.0
        for col in ['news_mention_count', 'reddit_mention_count']:
            if col in df.columns:
                df[col] = df[col].fillna(0)
            else:
                df[col] = 0
    else:
        # Fallbacks if sentiment is omitted
        df['news_sentiment_score'] = 0.0
        df['reddit_sentiment_score'] = 0.0
        df['news_mention_count'] = 0
        df['reddit_mention_count'] = 0

    # Sentiment engineered features with exponential decay (half-life of 7 bars, ~1 trading day)
    df['combined_sentiment'] = 0.6 * df['news_sentiment_score'] + 0.4 * df['reddit_sentiment_score']
    half_life = 7.0
    alpha = 1.0 - np.exp(-np.log(2.0) / half_life)
    df['combined_sentiment_decayed'] = df['combined_sentiment'].ewm(alpha=alpha, adjust=False).mean()
    df['sent_sma_3'] = df['combined_sentiment_decayed'].rolling(window=3).mean()
    df['sent_sma_7'] = df['combined_sentiment_decayed'].rolling(window=7).mean()
    df['sent_momentum'] = df['combined_sentiment_decayed'] - df['combined_sentiment_decayed'].rolling(window=10).mean().fillna(0.0)

    # --- Merge Macro Indicators ---
    if macro_df is not None and not macro_df.empty:
        # Pivot macro indicators to get separate columns
        macro_pivot = macro_df.pivot(index='date', columns='indicator_name', values='value').reset_index()
        macro_pivot = macro_pivot.rename(columns={'date': 'cal_date'}).sort_values('cal_date')

        # Shift macro by one day too (use the prior day's macro state on each trading day).
        macro_val_cols = [c for c in macro_pivot.columns if c != 'cal_date']
        macro_pivot[macro_val_cols] = macro_pivot[macro_val_cols].shift(1)
        df = pd.merge(df, macro_pivot, on='cal_date', how='left')

        # Forward fill macro indicators since they represent steady states
        for col in ['fed_funds', 'yield_spread']:
            if col in df.columns:
                df[col] = df[col].ffill().bfill()
            else:
                df[col] = 0.05 if col == 'fed_funds' else 0.01
    else:
        # Fallbacks if macro is omitted
        df['fed_funds'] = 0.05  # Sensible historical baseline
        df['yield_spread'] = 0.01

    # --- Alternative Data Features ---
    if congress_df is None or insider_df is None:
        c_global, i_global = load_alternative_data_from_db()
        # Filter for the ticker of df
        ticker = df['ticker'].iloc[0] if not df.empty and 'ticker' in df.columns else None
        if ticker:
            congress_df = c_global[c_global['ticker'] == ticker] if not c_global.empty else pd.DataFrame()
            insider_df = i_global[i_global['ticker'] == ticker] if not i_global.empty else pd.DataFrame()
        else:
            congress_df = pd.DataFrame()
            insider_df = pd.DataFrame()

    insider_feats = ['insider_net_flow', 'insider_buy_count', 'insider_net_buyers',
                     'insider_officer_buy', 'insider_cluster']
    if not df.empty:
        min_date = pd.to_datetime(df['cal_date'].min())
        max_date = pd.to_datetime(df['cal_date'].max())
        officer_window = 90
        buffer_start = min_date - pd.Timedelta(days=max(INSIDER_LOOKBACK_DAYS, officer_window, CONGRESS_LOOKBACK_DAYS) + 10)
        daily_dates = pd.date_range(start=buffer_start, end=max_date, freq='D')
        daily_df = pd.DataFrame(index=daily_dates.strftime('%Y-%m-%d'))
        daily_df.index.name = 'date'

        # --- Insider CONVICTION features (use ALL transactions, not just the rare purchases) ---
        for c in ['buy_val', 'sell_val', 'buy_cnt', 'dbuyers', 'dsellers', 'officer_buy_val', 'congress_val']:
            daily_df[c] = 0.0

        if insider_df is not None and not insider_df.empty:
            ii = insider_df.copy()
            ii['date'] = ii['date'].astype(str).str.slice(0, 10)
            tt = ii['transaction_type'].astype(str).str.lower()
            ii['is_buy'] = tt.eq('purchase')
            ii['is_sell'] = tt.eq('sale')
            rel = ii.get('relationship', pd.Series([''] * len(ii))).astype(str).str.lower()
            ii['officer'] = rel.str.contains('chief|ceo|cfo|coo|president', regex=True, na=False)
            name_col = 'insider_name' if 'insider_name' in ii.columns else None
            buys = ii[ii['is_buy']]
            sells = ii[ii['is_sell']]
            daily_df['buy_val'] = daily_df.index.map(buys.groupby('date')['total_value'].sum()).fillna(0.0)
            daily_df['sell_val'] = daily_df.index.map(sells.groupby('date')['total_value'].sum()).fillna(0.0)
            daily_df['buy_cnt'] = daily_df.index.map(buys.groupby('date').size()).fillna(0.0)
            if name_col:
                daily_df['dbuyers'] = daily_df.index.map(buys.groupby('date')[name_col].nunique()).fillna(0.0)
                daily_df['dsellers'] = daily_df.index.map(sells.groupby('date')[name_col].nunique()).fillna(0.0)
            else:
                daily_df['dbuyers'] = (daily_df['buy_cnt'] > 0).astype(float)
            daily_df['officer_buy_val'] = daily_df.index.map(
                buys[buys['officer']].groupby('date')['total_value'].sum()).fillna(0.0)

        if congress_df is not None and not congress_df.empty:
            cong_p = congress_df[congress_df['transaction_type'].astype(str).str.lower() == 'purchase']
            if not cong_p.empty:
                daily_df['congress_val'] = daily_df.index.map(cong_p.groupby('date')['estimated_value'].sum()).fillna(0.0)

        W = INSIDER_LOOKBACK_DAYS
        roll = lambda s, w: s.rolling(window=w, min_periods=1).sum()
        daily_df['insider_net_flow'] = roll(daily_df['buy_val'] - daily_df['sell_val'], W)
        daily_df['insider_buy_count'] = roll(daily_df['buy_cnt'], W)
        daily_df['insider_net_buyers'] = roll(daily_df['dbuyers'] - daily_df['dsellers'], W)
        daily_df['insider_cluster'] = roll(daily_df['dbuyers'], W)
        daily_df['insider_officer_buy'] = roll(daily_df['officer_buy_val'], officer_window)
        daily_df['congress_sum'] = roll(daily_df['congress_val'], CONGRESS_LOOKBACK_DAYS)

        # Look-ahead protection: shift the daily series by one day before broadcasting onto bars.
        out_cols = insider_feats + ['congress_sum']
        for c in out_cols:
            daily_df[c] = daily_df[c].shift(1).fillna(0.0)

        daily_df = daily_df.reset_index().rename(columns={'date': 'cal_date'})
        df = pd.merge(df, daily_df[['cal_date'] + out_cols], on='cal_date', how='left')
        for c in out_cols:
            df[c] = df[c].fillna(0.0)

        # Normalize dollar-value features by price (stationarity); counts left as-is.
        df['insider_net_flow'] = df['insider_net_flow'] / (df['close'] + 1e-9)
        df['insider_officer_buy'] = df['insider_officer_buy'] / (df['close'] + 1e-9)
        df['congress_buying_ratio'] = df['congress_sum'] / (df['close'] + 1e-9)
        df['congress_buying_90d'] = df['congress_buying_ratio']
    else:
        for c in insider_feats:
            df[c] = 0.0
        df['congress_buying_ratio'] = 0.0
        df['congress_buying_90d'] = 0.0

    # --- Target Labels Generation (triple-barrier; matches the executed trade brackets) ---
    # target_win = training label; trade_ret = realised P&L of the bracketed trade (eval only).
    df['target_win'], df['trade_ret'] = triple_barrier_outcomes(
        df['high'].values, df['low'].values, df['close'].values, df['atr_14'].values,
        horizon=target_horizon_bars, atr_stop_mult=target_atr_stop_mult,
        tp_mult=target_tp_mult, stop_min=target_stop_min, stop_max=target_stop_max,
    )

    # --- Strict Look-Ahead Bias Mitigation ---
    # Shift ALL feature columns by 1 to represent data available at the market CLOSE of day T-1
    # Note: absolute prices (open, high, low, close) are completely dropped from the training set
    # by only shifting and prefixing the normalized stationary features.
    feature_cols = [
        'volume_ratio', 'returns', 'volatility_10', 'parkinson_vol_10',
        'ma_ratio', 'rsi_14', 'bb_width', 'atr_ratio', 'atr_14',
        'close_to_sma10', 'close_to_sma50', 'high_low_ratio', 'close_to_bb_mid',
        'returns_vol_adj', 'macd_ratio', 'macd_signal_ratio',
        'news_sentiment_score', 'reddit_sentiment_score', 'news_mention_count', 'reddit_mention_count',
        'combined_sentiment_decayed', 'sent_sma_3', 'sent_sma_7', 'sent_momentum',
        'fed_funds', 'yield_spread',
        'insider_net_flow', 'insider_buy_count', 'insider_net_buyers',
        'insider_officer_buy', 'insider_cluster',
        'congress_buying_ratio', 'congress_buying_90d'
    ]

    # Keep original unshifted close & date for reference/labeling, but prefix feature names
    for col in feature_cols:
        if col in df.columns:
            df[f"feat_{col}"] = df[col].shift(1)

    # Drop rows that don't have enough history to compute indicators
    df = df.dropna(subset=[f"feat_close_to_sma50"])

    return df

def add_cross_ticker_features(df):
    """
    Computes cross-ticker features on a concatenated DataFrame of multiple tickers.
    Contains columns: ['ticker', 'date', 'close', 'returns', 'volatility_10'].
    Returns a DataFrame with new feature columns.
    """
    df = df.copy()
    # Sort chronologically
    df['date_dt'] = pd.to_datetime(df['date'], format='mixed')
    df = df.sort_values(['date_dt', 'ticker']).reset_index(drop=True)

    # 1. Extract Benchmark Index Series
    spy_data = df[df['ticker'] == 'SPY'][['date', 'returns', 'volatility_10', 'close']].rename(columns={
        'returns': 'spy_returns',
        'volatility_10': 'spy_volatility_10',
        'close': 'spy_close'
    })
    qqq_data = df[df['ticker'] == 'QQQ'][['date', 'returns', 'volatility_10', 'close']].rename(columns={
        'returns': 'qqq_returns',
        'volatility_10': 'qqq_volatility_10',
        'close': 'qqq_close'
    })

    # Merge benchmarks
    df = pd.merge(df, spy_data, on='date', how='left')
    df = pd.merge(df, qqq_data, on='date', how='left')

    # Fill benchmark references for days index is not computed (or missing)
    df['spy_returns'] = df['spy_returns'].fillna(0.0)
    df['spy_volatility_10'] = df['spy_volatility_10'].fillna(0.0)
    df['spy_close'] = df['spy_close'].ffill().bfill()

    df['qqq_returns'] = df['qqq_returns'].fillna(0.0)
    df['qqq_volatility_10'] = df['qqq_volatility_10'].fillna(0.0)
    df['qqq_close'] = df['qqq_close'].ffill().bfill()

    # 2. Compute Relative Features (Winner / Riskier indicators)
    df['relative_return_spy'] = df['returns'] - df['spy_returns']
    df['relative_return_qqq'] = df['returns'] - df['qqq_returns']

    df['relative_vol_spy'] = df['volatility_10'] / (df['spy_volatility_10'] + 1e-9)
    df['relative_vol_qqq'] = df['volatility_10'] / (df['qqq_volatility_10'] + 1e-9)

    # 50-day cumulative relative performance
    df['close_shift_50'] = df.groupby('ticker')['close'].shift(50)
    df['spy_close_shift_50'] = df.groupby('ticker')['spy_close'].shift(50)

    df['cum_rel_ret_spy_50'] = (df['close'] / (df['close_shift_50'] + 1e-9)) - (df['spy_close'] / (df['spy_close_shift_50'] + 1e-9))

    # 3. Cross-sectional Ranks (Winner / Riskier rankings per day)
    non_benchmark_mask = ~df['ticker'].isin(['SPY', 'QQQ'])
    df.loc[non_benchmark_mask, 'rank_return'] = df[non_benchmark_mask].groupby('date')['returns'].rank(pct=True)
    df.loc[non_benchmark_mask, 'rank_volatility'] = df[non_benchmark_mask].groupby('date')['volatility_10'].rank(pct=True)
    df.loc[non_benchmark_mask, 'rank_volume_ratio'] = df[non_benchmark_mask].groupby('date')['volume_ratio'].rank(pct=True)
    df.loc[non_benchmark_mask, 'rank_sentiment'] = df[non_benchmark_mask].groupby('date')['combined_sentiment_decayed'].rank(pct=True)

    # Fill benchmarks or missing rankings with neutral (0.5)
    df['rank_return'] = df['rank_return'].fillna(0.5)
    df['rank_volatility'] = df['rank_volatility'].fillna(0.5)
    df['rank_volume_ratio'] = df['rank_volume_ratio'].fillna(0.5)
    df['rank_sentiment'] = df['rank_sentiment'].fillna(0.5)

    # 4. Rolling correlation of returns vs SPY and QQQ (20 days)
    def get_rolling_corr(group):
        group = group.sort_values('date_dt')
        group['corr_spy_20'] = group['returns'].rolling(20).corr(group['spy_returns'])
        group['corr_qqq_20'] = group['returns'].rolling(20).corr(group['qqq_returns'])
        return group

    df = df.groupby('ticker', group_keys=False).apply(get_rolling_corr)

    # Clean up intermediate columns
    df = df.drop(columns=['date_dt', 'close_shift_50', 'spy_close_shift_50'])

    # Fill NAs
    fill_cols = [
        'relative_return_spy', 'relative_return_qqq', 'relative_vol_spy', 'relative_vol_qqq',
        'cum_rel_ret_spy_50', 'rank_return', 'rank_volatility', 'rank_volume_ratio', 'rank_sentiment',
        'corr_spy_20', 'corr_qqq_20'
    ]
    for col in fill_cols:
        df[col] = df[col].fillna(0.0)

    # --- Look-ahead Bias Mitigation / Shift for features ---
    for col in fill_cols:
        df[f"feat_{col}"] = df.groupby('ticker')[col].shift(1)
        # Fill leading NAs for features
        df[f"feat_{col}"] = df[f"feat_{col}"].fillna(0.0)

    return df

def build_all_features(prices_df, sent_df, macro_df, active_universe,
                       target_horizon_bars=14, target_atr_stop_mult=2.0,
                       target_tp_mult=2.5, target_stop_min=0.015, target_stop_max=0.05):
    """
    Computes individual and cross-ticker features for all active tickers.
    Returns a concatenated DataFrame containing all features.
    """
    processed_dfs = []
    congress_df, insider_df = load_alternative_data_from_db()
    for ticker in active_universe:
        ticker_prices = prices_df[prices_df['ticker'] == ticker].sort_values('date')
        if len(ticker_prices) < 50:
            continue
        ticker_sent = sent_df[sent_df['ticker'] == ticker] if (sent_df is not None and not sent_df.empty) else pd.DataFrame()
        ticker_congress = congress_df[congress_df['ticker'] == ticker] if not congress_df.empty else pd.DataFrame()
        ticker_insider = insider_df[insider_df['ticker'] == ticker] if not insider_df.empty else pd.DataFrame()
        t_feat = build_features_for_df(ticker_prices, ticker_sent, macro_df,
                                       target_horizon_bars=target_horizon_bars,
                                       target_atr_stop_mult=target_atr_stop_mult,
                                       target_tp_mult=target_tp_mult,
                                       target_stop_min=target_stop_min,
                                       target_stop_max=target_stop_max,
                                       congress_df=ticker_congress,
                                       insider_df=ticker_insider)
        processed_dfs.append(t_feat)

    if not processed_dfs:
        return pd.DataFrame()

    full_df = pd.concat(processed_dfs, ignore_index=True)
    full_df = add_cross_ticker_features(full_df)
    return full_df
