import sys
import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
import requests
import urllib.parse

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    TICKER_UNIVERSE, BENCHMARK_TICKER, MASSIVE_API_KEY, MASSIVE_BASE_URL,
    DATA_TIMESPAN, DATA_MULTIPLIER, HOURLY_LOOKBACK_DAYS, DAILY_HISTORY_START
)
from app.database import init_db, SessionLocal, RecentPrice, DailyPrice, UniverseTicker

# Symbols that differ between Massive/Polygon (class-B uses a dot) and Yahoo (uses a dash).
MASSIVE_SYMBOL_OVERRIDES = {"BRK-B": "BRK.B"}

# Fictional tickers (e.g. SpaceX SPACE) that should be generated synthetically.
FICTIONAL_TICKERS = {"SPACE"}

# Safe-asset ETFs used by the Crash Radar defensive playbook. They are NOT part of the tradeable
# universe (so they stay out of the screener/sentiment pipeline), but the paper rebalancer needs
# realistic prices for them. They are stored only in daily_prices, which is never purged by
# universe membership.
DEFENSIVE_ETFS = ["TLT", "IEF", "BIL", "LQD", "TIP", "GLD", "GSG"]


def map_ticker_to_massive(ticker):
    return MASSIVE_SYMBOL_OVERRIDES.get(ticker, ticker)


def map_ticker_to_yahoo(ticker):
    if ticker.startswith("X:"):
        return ticker[2:].replace("USD", "-USD")
    elif ticker.startswith("C:"):
        return ticker[2:] + "=X"
    # Class shares use a dash on Yahoo (BRK.B -> BRK-B), but a dot in most broker exports.
    if "." in ticker:
        return ticker.replace(".", "-")
    return ticker


def timestamp_to_str(epoch_ms, daily=False):
    dt = datetime.fromtimestamp(epoch_ms / 1000.0)
    if daily:
        return dt.strftime("%Y-%m-%d")
    if DATA_TIMESPAN in ["minute", "hour"]:
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Raw source fetchers
# ---------------------------------------------------------------------------

def _massive_get(url, headers, ticker):
    """GETs a Massive/Polygon URL with 429 backoff. Returns (json|None)."""
    backoff_sec = 2.0
    for attempt in range(5):
        time.sleep(0.2)
        try:
            response = requests.get(url, headers=headers, timeout=60)
            if response.status_code == 429:
                print(f"Rate limited (429) for {ticker}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
                backoff_sec *= 2.0
                continue
            if response.status_code == 403:
                msg = response.json().get("message", "not authorized") if response.headers.get("content-type", "").startswith("application/json") else response.text[:120]
                print(f"Massive plan does not cover {ticker}: {msg}")
                return None
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == 4:
                print(f"Failed Massive request for {ticker}: {e}")
                return None
            time.sleep(backoff_sec)
            backoff_sec *= 2.0
    return None


def fetch_massive_hourly(ticker, start_date, end_date):
    """Fetches HOURLY bars from Massive/Polygon, following the `next_url` cursor across all
    pages (the API caps each page at ~1000 bars regardless of `limit`). No fallback to other
    sources: if the plan does not cover the timeframe (403) or no data exists, returns []."""
    symbol = map_ticker_to_massive(ticker)
    ticker_encoded = urllib.parse.quote(symbol)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"} if MASSIVE_API_KEY else {}

    url = (f"{MASSIVE_BASE_URL}/v2/aggs/ticker/{ticker_encoded}/range/"
           f"{DATA_MULTIPLIER}/{DATA_TIMESPAN}/{start_str}/{end_str}"
           f"?adjusted=true&sort=asc&limit=50000")

    bars = []
    pages = 0
    while url:
        data = _massive_get(url, headers, ticker)
        if data is None:
            break
        bars.extend(data.get("results", []) or [])
        pages += 1
        next_url = data.get("next_url")
        # next_url carries the cursor but not the api key; auth stays in the header.
        url = next_url if next_url else None
        if pages > 200:  # safety guard against runaway pagination
            print(f"Stopping {ticker} after {pages} pages.")
            break
    if pages > 1:
        print(f"{ticker}: fetched {len(bars)} hourly bars across {pages} pages.")
    return bars


def fetch_yahoo_daily(ticker, start_date, end_date):
    """Fetches DAILY bars from Yahoo Finance. Returns [] for delisted/unavailable symbols."""
    yahoo_symbol = map_ticker_to_yahoo(ticker)
    p1 = int(start_date.timestamp())
    p2 = int(end_date.timestamp())
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol}"
           f"?period1={p1}&period2={p2}&interval=1d")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}

    max_retries = 5
    backoff_sec = 2.0
    for attempt in range(max_retries):
        try:
            res = requests.get(url, headers=headers, timeout=20)
            if res.status_code == 429:
                time.sleep(backoff_sec)
                backoff_sec *= 2.0
                continue
            if res.status_code == 400:
                try:
                    res_json = res.json()
                    err_desc = res_json.get("chart", {}).get("error", {}).get("description", "")
                    if "Data doesn't exist for startDate" in err_desc:
                        return "PRE_IPO_LIMIT"
                except Exception:
                    pass
                print(f"Yahoo has no data for {ticker} ({yahoo_symbol}): HTTP {res.status_code} (likely delisted/renamed).")
                return []
            if res.status_code == 404:
                print(f"Yahoo has no data for {ticker} ({yahoo_symbol}): HTTP {res.status_code} (likely delisted/renamed).")
                return []
            res.raise_for_status()
            chart = res.json().get("chart", {}).get("result", [])
            if not chart:
                return []
            chart = chart[0]
            timestamps = chart.get("timestamp", [])
            quotes = chart.get("indicators", {}).get("quote", [])
            if not quotes:
                return []
            quotes = quotes[0]
            opens, highs = quotes.get("open", []), quotes.get("high", [])
            lows, closes, volumes = quotes.get("low", []), quotes.get("close", []), quotes.get("volume", [])

            bars = []
            for i in range(len(timestamps)):
                o, h, l, c = opens[i], highs[i], lows[i], closes[i]
                if o is None or h is None or l is None or c is None:
                    continue
                bars.append({
                    "t": timestamps[i] * 1000,
                    "o": float(o), "h": float(h), "l": float(l), "c": float(c),
                    "v": float(volumes[i]) if volumes[i] is not None else 0.0
                })
            return bars
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to fetch Yahoo daily data for {ticker}: {e}")
                return []
            time.sleep(backoff_sec)
            backoff_sec *= 2.0
    return []


# ---------------------------------------------------------------------------
# Local indicator computation
# ---------------------------------------------------------------------------

def compute_local_indicators_for_bars(bars, daily=False):
    """Computes SMA/RSI/MACD locally over a chronological bar series.
    Returns (sma_10_map, sma_50_map, rsi_14_map, macd_map) keyed by formatted date string."""
    if not bars:
        return {}, {}, {}, {}

    df = pd.DataFrame(bars).drop_duplicates(subset=['t']).sort_values('t').reset_index(drop=True)

    df['sma_10'] = df['c'].rolling(window=10).mean()
    df['sma_50'] = df['c'].rolling(window=50).mean()

    delta = df['c'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean().values
    avg_loss = loss.rolling(window=14).mean().values
    gain_vals, loss_vals = gain.values, loss.values
    for i in range(15, len(df)):
        if not np.isnan(avg_gain[i - 1]):
            avg_gain[i] = (avg_gain[i - 1] * 13 + gain_vals[i]) / 14
        if not np.isnan(avg_loss[i - 1]):
            avg_loss[i] = (avg_loss[i - 1] * 13 + loss_vals[i]) / 14
    df['rsi_14'] = 100 - (100 / (1 + avg_gain / (avg_loss + 1e-10)))

    ema_12 = df['c'].ewm(span=12, adjust=False).mean()
    ema_26 = df['c'].ewm(span=26, adjust=False).mean()
    df['macd'] = ema_12 - ema_26
    df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

    sma_10_map, sma_50_map, rsi_14_map, macd_map = {}, {}, {}, {}
    for _, row in df.iterrows():
        date_str = timestamp_to_str(row['t'], daily=daily)
        if not np.isnan(row['sma_10']):
            sma_10_map[date_str] = float(row['sma_10'])
        if not np.isnan(row['sma_50']):
            sma_50_map[date_str] = float(row['sma_50'])
        if not np.isnan(row['rsi_14']):
            rsi_14_map[date_str] = float(row['rsi_14'])
        if not np.isnan(row['macd']) and not np.isnan(row['macd_signal']):
            macd_map[date_str] = (float(row['macd']), float(row['macd_signal']))
    return sma_10_map, sma_50_map, rsi_14_map, macd_map


def _write_bars(db, Model, ticker, bars, daily=False):
    """Upserts a bar series into the given price Model, backfilling indicators on existing rows."""
    if not bars:
        return 0, 0

    # 1. Fetch historical bars from database to ensure correct indicator computation
    history = db.query(Model).filter(Model.ticker == ticker).order_by(Model.date.desc()).limit(100).all()

    # Convert DB records to the dict format
    combined_bars_map = {}
    for h in history:
        # Reconstruct timestamp
        try:
            if daily:
                dt = datetime.strptime(h.date, "%Y-%m-%d")
            else:
                dt = datetime.strptime(h.date, "%Y-%m-%d %H:%M:%S")
            t_ms = int(dt.timestamp() * 1000)
        except Exception:
            continue
        combined_bars_map[h.date] = {
            "t": t_ms, "o": h.open, "h": h.high, "l": h.low, "c": h.close, "v": h.volume
        }

    # Add new bars (they will overwrite historical if date matches)
    for bar in bars:
        epoch_ms = bar.get("t")
        if not epoch_ms:
            continue
        date_str = timestamp_to_str(epoch_ms, daily=daily)
        combined_bars_map[date_str] = bar

    # Sort combined bars chronologically
    combined_bars = sorted(combined_bars_map.values(), key=lambda x: x["t"])

    # Compute indicators on the combined list
    sma_10_map, sma_50_map, rsi_14_map, macd_map = compute_local_indicators_for_bars(combined_bars, daily=daily)

    existing_map = {p.date: p for p in db.query(Model).filter(Model.ticker == ticker).all()}
    new_records = []
    updated = 0

    # We only want to write or update bars that were actually in the newly fetched `bars`
    new_bar_dates = {timestamp_to_str(b["t"], daily=daily) for b in bars if b.get("t")}

    for date_str in new_bar_dates:
        bar = combined_bars_map[date_str]
        macd_pair = macd_map.get(date_str)

        existing = existing_map.get(date_str)
        if existing:
            changed = False
            # Check and update standard fields
            if existing.open != float(bar["o"]):
                existing.open = float(bar["o"]); changed = True
            if existing.high != float(bar["h"]):
                existing.high = float(bar["h"]); changed = True
            if existing.low != float(bar["l"]):
                existing.low = float(bar["l"]); changed = True
            if existing.close != float(bar["c"]):
                existing.close = float(bar["c"]); changed = True
            if existing.volume != float(bar["v"]):
                existing.volume = float(bar["v"]); changed = True

            # Update indicators if they differ or are newly calculated
            if date_str in sma_10_map and existing.sma_10 != sma_10_map[date_str]:
                existing.sma_10 = sma_10_map[date_str]; changed = True
            if date_str in sma_50_map and existing.sma_50 != sma_50_map[date_str]:
                existing.sma_50 = sma_50_map[date_str]; changed = True
            if date_str in rsi_14_map and existing.rsi_14 != rsi_14_map[date_str]:
                existing.rsi_14 = rsi_14_map[date_str]; changed = True
            if macd_pair:
                if existing.macd != macd_pair[0] or existing.macd_signal != macd_pair[1]:
                    existing.macd, existing.macd_signal = macd_pair; changed = True

            if changed:
                db.add(existing); updated += 1
            continue

        new_records.append(Model(
            ticker=ticker, date=date_str,
            open=float(bar["o"]), high=float(bar["h"]), low=float(bar["l"]),
            close=float(bar["c"]), volume=float(bar["v"]),
            sma_10=sma_10_map.get(date_str), sma_50=sma_50_map.get(date_str),
            rsi_14=rsi_14_map.get(date_str),
            macd=macd_pair[0] if macd_pair else None,
            macd_signal=macd_pair[1] if macd_pair else None,
        ))
    if new_records:
        # Upsert: another writer (scheduler fetch + a manual backfill, or two backfills) may have
        # inserted the same (ticker, date) bars between our existing_map snapshot and this commit.
        # ON CONFLICT DO NOTHING makes the insert idempotent so the race can't raise IntegrityError.
        from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
        # Every row must carry the same column set (NULL for missing indicators), otherwise the
        # multi-row VALUES insert renders inconsistently. Safe: both tables use a composite
        # (ticker, date) PK with no autoincrement id.
        cols = [c.name for c in Model.__table__.columns]
        rows = [{c: getattr(r, c) for c in cols} for r in new_records]
        stmt = _sqlite_insert(Model).values(rows).on_conflict_do_nothing(
            index_elements=["ticker", "date"])
        db.execute(stmt)
    if new_records or updated:
        db.commit()
    return len(new_records), updated


def backfill_ticker(ticker, progress_cb=None):
    """Fetch + store DAILY history and HOURLY recent bars for a single newly-added ticker.

    `progress_cb(percent:int, stage:str)` is called as it advances so the UI can show a progress bar.
    Reused by the background "add ticker" job. Safe to re-run (upserts)."""
    ticker = ticker.upper().strip()
    def report(p, s):
        if progress_cb:
            progress_cb(p, s)
    report(5, f"Starting backfill for {ticker}")
    init_db()
    end_date = datetime.now()

    if ticker in FICTIONAL_TICKERS:
        report(100, "Synthetic ticker — nothing to fetch")
        return {"daily": 0, "hourly": 0}

    # 1) Daily history (Yahoo, full window)
    report(15, "Fetching daily history (Yahoo)…")
    daily_start = datetime.strptime(DAILY_HISTORY_START, "%Y-%m-%d")
    daily_bars = fetch_yahoo_daily(ticker, daily_start, end_date)
    db = SessionLocal()
    daily_added = 0
    try:
        report(40, "Storing daily bars + indicators…")
        daily_added, _ = _write_bars(db, DailyPrice, ticker, daily_bars, daily=True)
    finally:
        db.close()

    # 2) Hourly recent bars (Massive, ~5y window)
    report(30, "Fetching hourly bars (Massive)…")
    hourly_start = end_date - timedelta(days=HOURLY_LOOKBACK_DAYS)
    hourly_bars = fetch_massive_hourly(ticker, hourly_start, end_date)
    db = SessionLocal()
    hourly_added = 0
    try:
        report(45, "Storing hourly bars + indicators…")
        hourly_added, _ = _write_bars(db, RecentPrice, ticker, hourly_bars, daily=False)
    finally:
        db.close()

    # 3) LLM-score the ticker's news. Prefer OpenAI when configured (fast); else fall back to local
    #    Ollama (requires it to be up). News scoring is the slow part of a backfill.
    news_scored = 0
    news_skipped = False
    try:
        from app.core.config import SWING_ENABLED, NEWS_LLM_START, OLLAMA_URL, OPENAI_API_KEY
        provider = "openai" if OPENAI_API_KEY else "ollama"
        ollama_up = True
        if provider == "ollama":
            import requests as _rq
            try:
                ollama_up = _rq.get(f"{OLLAMA_URL}/api/tags", timeout=3).status_code == 200
            except Exception:
                ollama_up = False
        if SWING_ENABLED and (provider == "openai" or ollama_up):
            report(50, f"Scoring news headlines ({provider})…")
            from data_ingestion.news_llm import fetch_and_score
            before = _count_news(ticker)
            fetch_and_score(start=NEWS_LLM_START, tickers=[ticker], provider=provider,
                            progress_cb=lambda f, note: report(50 + int(f * 49), note))
            news_scored = _count_news(ticker) - before
        elif not ollama_up:
            news_skipped = True
            report(95, "Skipped news scoring — Ollama offline (set OPENAI_API_KEY to use OpenAI)")
    except Exception as e:
        print(f"News scoring failed for {ticker}: {e}")

    total_news, latest_news = _news_coverage(ticker)
    if news_skipped:
        news_part = f"news scoring SKIPPED (Ollama offline) — {total_news:,} on file"
    elif total_news:
        news_part = f"news +{news_scored:,} new ({total_news:,} scored, latest {latest_news})"
    else:
        news_part = "no news found"
    report(100, f"Done — prices +{daily_added} daily / +{hourly_added} hourly; {news_part}")
    return {"daily": daily_added, "hourly": hourly_added, "news": news_scored,
            "news_total": total_news, "news_latest": str(latest_news) if latest_news else None}


def _count_news(ticker):
    from app.database import NewsLLMScore
    db = SessionLocal()
    try:
        return db.query(NewsLLMScore).filter(NewsLLMScore.ticker == ticker).count()
    finally:
        db.close()


def _news_coverage(ticker):
    """(total scored headlines, latest scored date) for a ticker — for the backfill summary."""
    from app.database import NewsLLMScore
    from sqlalchemy import func
    db = SessionLocal()
    try:
        total = db.query(NewsLLMScore).filter(NewsLLMScore.ticker == ticker).count()
        latest = db.query(func.max(NewsLLMScore.date)).filter(NewsLLMScore.ticker == ticker).scalar()
        return total, latest
    finally:
        db.close()


def _get_active_universe(db):
    db_tickers = db.query(UniverseTicker).all()
    universe = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)
    # Always include the benchmark even if not tradable.
    return sorted(set(universe + [BENCHMARK_TICKER]))


def _earliest_latest(db, Model, ticker, keep_time=False):
    latest = db.query(Model).filter(Model.ticker == ticker).order_by(Model.date.desc()).first()
    earliest = db.query(Model).filter(Model.ticker == ticker).order_by(Model.date.asc()).first()

    def to_dt(rec):
        if not rec:
            return None
        if keep_time:
            try:
                return datetime.strptime(rec.date, "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        part = rec.date.split(" ")[0].split("T")[0]
        return datetime.strptime(part, "%Y-%m-%d")
    return to_dt(earliest), to_dt(latest)


# ---------------------------------------------------------------------------
# HOURLY: recent_prices (Massive)
# ---------------------------------------------------------------------------

def _looks_like_legacy_hourly_table(db, hourly_start):
    """Detects the old mixed daily+hourly table: any Yahoo-daily-stamped rows (09:30 ET ->
    06:30 local, or date-only) or any row older than the hourly window."""
    start_str = hourly_start.strftime("%Y-%m-%d")
    legacy = db.query(RecentPrice).filter(
        (RecentPrice.date < start_str) |
        (RecentPrice.date.like("% 06:30:00")) |
        (~RecentPrice.date.like("% %:%"))
    ).first()
    return legacy is not None


def fetch_recent_prices():
    """Fetches a clean HOURLY-only dataset (~5y window) into recent_prices."""
    init_db()
    db = SessionLocal()

    end_date = datetime.now()
    hourly_start = end_date - timedelta(days=HOURLY_LOOKBACK_DAYS)
    active_universe = _get_active_universe(db)

    print(f"Starting HOURLY price fetch for {len(active_universe)} tickers "
          f"({hourly_start.strftime('%Y-%m-%d')} -> {end_date.strftime('%Y-%m-%d')})...")

    # One-time clean rebuild if the legacy mixed table is detected.
    if _looks_like_legacy_hourly_table(db, hourly_start):
        n = db.query(RecentPrice).delete()
        db.commit()
        print(f"Detected legacy mixed-resolution rows. Purged {n} rows from recent_prices for a clean hourly rebuild.")

    # Drop rows for tickers no longer in the universe to keep the table clean.
    stale = db.query(RecentPrice).filter(~RecentPrice.ticker.in_(active_universe)).delete(synchronize_session=False)
    if stale:
        db.commit()
        print(f"Removed {stale} rows for tickers no longer in the universe.")

    fetch_tasks = []
    for ticker in active_universe:
        if ticker in FICTIONAL_TICKERS:
            continue
        _, latest = _earliest_latest(db, RecentPrice, ticker, keep_time=True)
        if latest is None:
            fetch_tasks.append((ticker, hourly_start, end_date))
        elif latest < end_date - timedelta(minutes=5):
            fetch_tasks.append((ticker, max(hourly_start, latest), end_date))
    db.close()

    if not fetch_tasks:
        print("All tickers already up to date (hourly).")
        return

    print(f"Fetching {len(fetch_tasks)} hourly tasks in parallel...", flush=True)
    results_map = {}
    total_tasks = len(fetch_tasks)
    completed = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_massive_hourly, tk, s, e): tk for tk, s, e in fetch_tasks}
        for future in as_completed(futures):
            completed += 1
            tk = futures[future]
            percent = int(completed / total_tasks * 100)
            try:
                res = future.result()
                results_map[tk] = res
                bars_fetched = len(res) if isinstance(res, list) else 0
                print(f"[Hourly Fetch Progress: {percent}%] Completed {completed}/{total_tasks} - {tk} ({bars_fetched} bars)", flush=True)
            except Exception as e:
                print(f"[Hourly Fetch Progress: {percent}%] Error fetching hourly {tk}: {e}", flush=True)

    db = SessionLocal()
    try:
        for ticker, bars in results_map.items():
            if not bars:
                print(f"No hourly bars returned for {ticker}.")
                continue
            added, updated = _write_bars(db, RecentPrice, ticker, bars, daily=False)
            print(f"{ticker}: +{added} new hourly bars, {updated} backfilled.")

        # Synthetic SPACE hourly price generation using GE as proxy
        active_universe = _get_active_universe(db)
        if "SPACE" in active_universe:
            space_count = db.query(RecentPrice).filter(RecentPrice.ticker == "SPACE").count()
            if space_count < 100:
                db.query(RecentPrice).filter(RecentPrice.ticker == "SPACE").delete()
                ge_prices = db.query(RecentPrice).filter(RecentPrice.ticker == "GE").all()
                if ge_prices:
                    ge_latest = db.query(RecentPrice).filter(RecentPrice.ticker == "GE").order_by(RecentPrice.date.desc()).first()
                    mult = 210.50 / ge_latest.close if ge_latest and ge_latest.close else 1.2
                    new_space_prices = []
                    for p in ge_prices:
                        new_space_prices.append(RecentPrice(
                            ticker="SPACE",
                            date=p.date,
                            open=p.open * mult,
                            high=p.high * mult,
                            low=p.low * mult,
                            close=p.close * mult,
                            volume=p.volume,
                            sma_10=p.sma_10 * mult if p.sma_10 else None,
                            sma_50=p.sma_50 * mult if p.sma_50 else None,
                            rsi_14=p.rsi_14,
                            macd=p.macd * mult if p.macd else None,
                            macd_signal=p.macd_signal * mult if p.macd_signal else None
                        ))
                    db.bulk_save_objects(new_space_prices)
                    db.commit()
                    print(f"Synthesized {len(new_space_prices)} hourly prices for SPACE using GE as proxy (multiplier: {mult:.4f}).")
    finally:
        db.close()
    print("Hourly price fetch completed.\n")


# ---------------------------------------------------------------------------
# DAILY: daily_prices (Yahoo, full history)
# ---------------------------------------------------------------------------

def fetch_defensive_etf_prices(force=False):
    """Fetches DAILY history (Yahoo) for the Crash Radar safe-asset ETFs into daily_prices.

    Stored only in daily_prices (not recent_prices) so the universe-purge in fetch_recent_prices()
    can't delete them. Skips tickers already fresh (<=2 days old) unless force=True.
    """
    init_db()
    end_date = datetime.now()
    default_start = datetime.strptime(DAILY_HISTORY_START, "%Y-%m-%d")
    db = SessionLocal()
    summary = {}
    try:
        for ticker in DEFENSIVE_ETFS:
            earliest, latest = _earliest_latest(db, DailyPrice, ticker)
            if latest is not None and not force and latest >= end_date - timedelta(days=2):
                summary[ticker] = "fresh"
                continue
            start = default_start if latest is None else latest
            bars = fetch_yahoo_daily(ticker, start, end_date)
            if isinstance(bars, str) or not bars:
                summary[ticker] = "no-data"
                continue
            added, updated = _write_bars(db, DailyPrice, ticker, bars, daily=True)
            summary[ticker] = f"+{added}/{updated}"
    finally:
        db.close()
    print(f"Defensive ETF daily price fetch: {summary}")
    return summary


def equity_lot_tickers(db):
    """Distinct tickers held in the Equity Advisor lot table (may be outside the trade universe)."""
    from app.database.models import EquityLot
    rows = db.query(EquityLot.ticker).distinct().all()
    return sorted({r[0].upper().strip() for r in rows if r[0]})


def _equity_lot_history_start(db, ticker):
    """Earliest date we need daily_prices for grant-timeline charts (first lot minus buffer)."""
    from app.database.models import EquityLot
    from sqlalchemy import func
    d = db.query(func.min(EquityLot.acquisition_date)).filter(EquityLot.ticker == ticker).scalar()
    default_start = datetime.strptime(DAILY_HISTORY_START, "%Y-%m-%d")
    if not d:
        return default_start
    lot_start = datetime.strptime(str(d)[:10], "%Y-%m-%d") - timedelta(days=45)
    return max(lot_start, default_start)


def ensure_equity_daily_prices(db, ticker, force=False):
    """Ensure daily_prices covers an equity-advisor ticker from first grant through today.

    These names (e.g. ADBE, PINS) are often held externally and not in the trade universe, so the
    main fetch_daily_history() pipeline never pulls them. Returns True if rows exist afterward."""
    ticker = ticker.upper().strip()
    if ticker in FICTIONAL_TICKERS:
        return False
    end_date = datetime.now()
    lot_start = _equity_lot_history_start(db, ticker)
    earliest, latest = _earliest_latest(db, DailyPrice, ticker)

    tasks = []
    if force or latest is None:
        tasks.append((lot_start, end_date))
    else:
        if latest < end_date - timedelta(days=1):
            tasks.append((latest, end_date))
        if earliest is None or earliest > lot_start + timedelta(days=5):
            tasks.append((lot_start, earliest or end_date))

    if not tasks:
        return db.query(DailyPrice).filter(DailyPrice.ticker == ticker).count() > 0

    for start, end in tasks:
        if start >= end - timedelta(days=1):
            continue
        bars = fetch_yahoo_daily(ticker, start, end)
        if bars == "PRE_IPO_LIMIT":
            continue
        if not bars:
            continue
        _write_bars(db, DailyPrice, ticker, bars, daily=True)

    return db.query(DailyPrice).filter(DailyPrice.ticker == ticker).count() > 0


def fetch_equity_advisor_prices(db=None, tickers=None, force=False):
    """Fetch/update daily_prices for all Equity Advisor holdings (external RSU/ESPP tickers)."""
    close_db = False
    if db is None:
        init_db()
        db = SessionLocal()
        close_db = True
    summary = {}
    try:
        targets = sorted({t.upper().strip() for t in (tickers or equity_lot_tickers(db)) if t})
        for ticker in targets:
            ok = ensure_equity_daily_prices(db, ticker, force=force)
            summary[ticker] = "ok" if ok else "no-data"
        if summary:
            print(f"Equity Advisor daily price fetch: {summary}")
        return summary
    finally:
        if close_db:
            db.close()


def fetch_daily_history():
    """Fetches full multi-decade DAILY history (Yahoo) into daily_prices."""
    init_db()
    db = SessionLocal()

    end_date = datetime.now()
    default_start = datetime.strptime(DAILY_HISTORY_START, "%Y-%m-%d")
    active_universe = _get_active_universe(db)

    print(f"Starting DAILY history fetch for {len(active_universe)} tickers "
          f"(since {DAILY_HISTORY_START})...")

    # Load IPO markers to skip pre-IPO queries
    import json
    from app.core.config import DATA_STORAGE_DIR
    ipo_markers_path = os.path.join(DATA_STORAGE_DIR, "ipo_markers.json")
    ipo_markers = {}
    if os.path.exists(ipo_markers_path):
        try:
            with open(ipo_markers_path, "r") as f:
                ipo_markers = json.load(f)
        except Exception:
            pass

    fetch_tasks = []
    for ticker in active_universe:
        if ticker in FICTIONAL_TICKERS:
            continue
        earliest, latest = _earliest_latest(db, DailyPrice, ticker)
        if latest is None:
            fetch_tasks.append((ticker, default_start, end_date))
            continue
        if latest < end_date - timedelta(days=1):
            fetch_tasks.append((ticker, latest, end_date))
        if earliest and default_start < earliest - timedelta(days=5):
            if ticker in ipo_markers:
                continue
            fetch_tasks.append((ticker, default_start, earliest))
    db.close()

    if not fetch_tasks:
        print("All tickers already up to date (daily).")
    else:
        print(f"Fetching {len(fetch_tasks)} daily tasks in parallel...", flush=True)
        results_map = {}
        total_tasks = len(fetch_tasks)
        completed = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_yahoo_daily, tk, s, e): (tk, s, e) for tk, s, e in fetch_tasks}
            for future in as_completed(futures):
                completed += 1
                tk = futures[future][0]
                percent = int(completed / total_tasks * 100)
                try:
                    bars = future.result()
                    results_map.setdefault(tk, [])
                    if bars == "PRE_IPO_LIMIT":
                        results_map[tk] = "PRE_IPO_LIMIT"
                        print(f"[Daily Fetch Progress: {percent}%] Completed {completed}/{total_tasks} - {tk} (PRE_IPO_LIMIT)", flush=True)
                    else:
                        if isinstance(results_map[tk], list):
                            results_map[tk].extend(bars)
                        bars_fetched = len(bars) if isinstance(bars, list) else 0
                        print(f"[Daily Fetch Progress: {percent}%] Completed {completed}/{total_tasks} - {tk} ({bars_fetched} bars)", flush=True)
                except Exception as e:
                    print(f"[Daily Fetch Progress: {percent}%] Error fetching daily {tk}: {e}", flush=True)

        db = SessionLocal()
        ipo_markers_updated = False
        try:
            for ticker, bars in results_map.items():
                if bars == "PRE_IPO_LIMIT":
                    earliest, _ = _earliest_latest(db, DailyPrice, ticker)
                    if earliest:
                        ipo_markers[ticker] = earliest.strftime("%Y-%m-%d")
                        ipo_markers_updated = True
                    continue
                if not bars:
                    print(f"No daily history available for {ticker} (likely delisted/renamed).")
                    continue
                added, updated = _write_bars(db, DailyPrice, ticker, bars, daily=True)
                print(f"{ticker}: +{added} new daily bars, {updated} backfilled.")
        finally:
            db.close()

        if ipo_markers_updated:
            try:
                with open(ipo_markers_path, "w") as f:
                    json.dump(ipo_markers, f)
                print(f"Saved IPO start markers to {ipo_markers_path}")
            except Exception as e:
                print(f"Error saving ipo_markers: {e}")

    # Synthetic SPACE daily generation
    db = SessionLocal()
    try:
        active_universe = _get_active_universe(db)
        if "SPACE" in active_universe:
            space_count = db.query(DailyPrice).filter(DailyPrice.ticker == "SPACE").count()
            if space_count < 100:
                db.query(DailyPrice).filter(DailyPrice.ticker == "SPACE").delete()
                ge_prices = db.query(DailyPrice).filter(DailyPrice.ticker == "GE").all()
                if ge_prices:
                    ge_latest = db.query(DailyPrice).filter(DailyPrice.ticker == "GE").order_by(DailyPrice.date.desc()).first()
                    mult = 210.50 / ge_latest.close if ge_latest and ge_latest.close else 1.2
                    new_space_prices = []
                    for p in ge_prices:
                        new_space_prices.append(DailyPrice(
                            ticker="SPACE",
                            date=p.date,
                            open=p.open * mult,
                            high=p.high * mult,
                            low=p.low * mult,
                            close=p.close * mult,
                            volume=p.volume,
                            sma_10=p.sma_10 * mult if p.sma_10 else None,
                            sma_50=p.sma_50 * mult if p.sma_50 else None,
                            rsi_14=p.rsi_14,
                            macd=p.macd * mult if p.macd else None,
                            macd_signal=p.macd_signal * mult if p.macd_signal else None
                        ))
                    db.bulk_save_objects(new_space_prices)
                    db.commit()
                    print(f"Synthesized {len(new_space_prices)} daily prices for SPACE using GE as proxy (multiplier: {mult:.4f}).")
    finally:
        db.close()

    # Keep the defensive safe-asset ETFs fresh alongside the regular daily refresh.
    try:
        fetch_defensive_etf_prices()
    except Exception as e:
        print(f"Defensive ETF price fetch failed (non-fatal): {e}")

    # External RSU/ESPP holdings → universe (strategy=hold) so the main pipeline ingests them too.
    try:
        from data_ingestion.equity_universe_sync import sync_equity_advisor_universe
        _sync_db = SessionLocal()
        try:
            sync_equity_advisor_universe(_sync_db)
        finally:
            _sync_db.close()
    except Exception as e:
        print(f"Equity Advisor universe sync failed (non-fatal): {e}")

    print("Daily history fetch completed.\n")


if __name__ == "__main__":
    fetch_recent_prices()
    fetch_daily_history()
