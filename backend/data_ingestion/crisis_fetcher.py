import sys
import os
import yaml
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, CrisisPrice, DailyPrice

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crisis_universes.yaml")

# Broad fallback windows (mirror ml_engine.wargame era spans) when an era isn't in the YAML.
_FALLBACK_WINDOWS = {
    "dotcom": ("1999-01-01", "2002-12-31"),
    "gfc": ("2007-01-01", "2009-12-31"),
    "covid": ("2020-01-01", "2020-12-31"),
}


def _load_universe_config():
    try:
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _era_window(era):
    cfg = _load_universe_config().get(era, {})
    if cfg.get("start_date") and cfg.get("end_date"):
        return cfg["start_date"], cfg["end_date"]
    return _FALLBACK_WINDOWS.get(era, (None, None))


def _choose_backfill_source(era_start):
    """Pick the daily backfill provider. Honor CRISIS_BACKFILL_SOURCE, but never ask Alpaca for
    pre-2016 history (it doesn't have it) — fall back to Yahoo's deep history there."""
    from app.core.config import CRISIS_BACKFILL_SOURCE
    src = CRISIS_BACKFILL_SOURCE if CRISIS_BACKFILL_SOURCE in ("yahoo", "alpaca", "massive") else "yahoo"
    if src == "alpaca" and (era_start or "") < "2016":
        return "yahoo"
    return src


def _insert_crisis_rows(db, ticker, era, records):
    """records: iterable of (date_str, o, h, l, c, v). Inserts rows not already present. Returns count."""
    existing_dates = {d for (d,) in db.query(CrisisPrice.date).filter(
        CrisisPrice.ticker == ticker, CrisisPrice.era == era).all()}
    added = 0
    for date_str, o, h, l, c, v in records:
        if date_str in existing_dates:
            continue
        db.add(CrisisPrice(ticker=ticker, era=era, date=date_str,
                           open=float(o), high=float(h), low=float(l), close=float(c),
                           volume=float(v or 0.0)))
        existing_dates.add(date_str)
        added += 1
    if added:
        db.commit()
    return added


def _copy_from_daily(db, ticker, era, start_d, end_d):
    """Fast path: reuse the deep daily_prices history we already hold (Yahoo survivors + BRK.B)."""
    rows = db.query(DailyPrice).filter(
        DailyPrice.ticker == ticker, DailyPrice.date >= start_d, DailyPrice.date <= end_d
    ).order_by(DailyPrice.date.asc()).all()
    if not rows:
        return 0
    return _insert_crisis_rows(db, ticker, era,
                               ((r.date, r.open, r.high, r.low, r.close, r.volume) for r in rows))


def _vendor_backfill(db, ticker, era, start_d, end_d):
    """Fetch the era window from the configured daily provider (Yahoo deep history by default)."""
    from data_ingestion.price_fetcher import fetch_daily_bars
    source = _choose_backfill_source(start_d)
    start_dt = datetime.strptime(start_d, "%Y-%m-%d")
    end_dt = datetime.strptime(end_d, "%Y-%m-%d") + timedelta(days=1)
    bars = fetch_daily_bars(ticker, start_dt, end_dt, source=source) or []
    if not bars and source != "yahoo":  # last-resort: Yahoo has the deepest coverage
        bars = fetch_daily_bars(ticker, start_dt, end_dt, source="yahoo") or []
    records = ((datetime.fromtimestamp(b["t"] / 1000.0).strftime("%Y-%m-%d"),
                b["o"], b["h"], b["l"], b["c"], b.get("v", 0.0)) for b in bars)
    return _insert_crisis_rows(db, ticker, era, records)


def ensure_crisis_prices(db, tickers, era):
    """Make sure crisis_prices holds each ticker's daily bars for `era`, so the crash war-game can use
    REAL prices for names that traded then. Order of preference: existing rows → local daily_prices →
    a vendor backfill. Names that return nothing (not yet listed in the era) get a 0 — the basket
    builder beta-proxies those. Returns {ticker: row_count} (0 = no real data, will be proxied)."""
    start_d, end_d = _era_window(era)
    if not start_d:
        return {}
    coverage = {}
    for raw in tickers:
        t = (raw or "").upper().strip()
        if not t or t in coverage:
            continue
        existing = db.query(CrisisPrice).filter(
            CrisisPrice.ticker == t, CrisisPrice.era == era).count()
        if existing > 0:
            coverage[t] = existing
            continue
        n = _copy_from_daily(db, t, era, start_d, end_d)
        if n == 0:
            try:
                n = _vendor_backfill(db, t, era, start_d, end_d)
            except Exception as e:
                print(f"crisis backfill failed for {t} ({era}): {e}")
                db.rollback()
                n = 0
        coverage[t] = n
    return coverage

def fetch_crisis_data():
    init_db()
    db = SessionLocal()

    # Load crisis universes yaml
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(backend_dir, "data_ingestion", "crisis_universes.yaml")

    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    print("Starting historical crisis eras price fetch...")

    for era, era_config in config.items():
        start_date_default = era_config["start_date"]
        end_date_default = era_config["end_date"]
        tickers = era_config.get("tickers", [])
        overrides = era_config.get("overrides", {})

        print(f"\n--- Processing Era: {era.upper()} ({start_date_default} to {end_date_default}) ---")

        # Add overridden tickers to the list if they are not already there
        all_tickers = list(set(tickers + list(overrides.keys())))

        for ticker in all_tickers:
            # Determine start and end date
            ticker_start = start_date_default
            ticker_end = end_date_default

            if ticker in overrides:
                ticker_start = overrides[ticker].get("start_date", start_date_default)
                ticker_end = overrides[ticker].get("end_date", end_date_default)

            # Check if we already have records for this ticker in this era
            existing_count = db.query(CrisisPrice).filter(
                CrisisPrice.ticker == ticker,
                CrisisPrice.era == era
            ).count()

            if existing_count > 0:
                print(f"Ticker {ticker} in era {era} already has {existing_count} cached records. Skipping.")
                continue

            print(f"Fetching {ticker} for {era}... ({ticker_start} to {ticker_end})")

            try:
                # Add 1 day to end_date because yfinance is exclusive of end_date
                end_dt = datetime.strptime(ticker_end, "%Y-%m-%d")
                end_dt_exclusive = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")

                df = yf.download(
                    ticker,
                    start=ticker_start,
                    end=end_dt_exclusive,
                    progress=False
                )

                if df.empty:
                    print(f"No price data returned for {ticker} in era {era}.")
                    continue

                # Flatten MultiIndex columns if present (common in newer yfinance versions)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df = df.reset_index()

                new_records = []
                for _, row in df.iterrows():
                    date_val = row['Date']
                    if hasattr(date_val, 'strftime'):
                        date_str = date_val.strftime("%Y-%m-%d")
                    else:
                        date_str = str(date_val)[:10]

                    def get_float(val):
                        if hasattr(val, 'item'):
                            return float(val.item())
                        return float(val)

                    # Final safety check against duplicates
                    existing = db.query(CrisisPrice).filter(
                        CrisisPrice.ticker == ticker,
                        CrisisPrice.era == era,
                        CrisisPrice.date == date_str
                    ).first()

                    if existing:
                        continue

                    crisis_record = CrisisPrice(
                        ticker=ticker,
                        era=era,
                        date=date_str,
                        open=get_float(row['Open']),
                        high=get_float(row['High']),
                        low=get_float(row['Low']),
                        close=get_float(row['Close']),
                        volume=get_float(row['Volume'])
                    )
                    new_records.append(crisis_record)

                if new_records:
                    db.bulk_save_objects(new_records)
                    db.commit()
                    print(f"Added {len(new_records)} records for {ticker} in era {era}.")
                else:
                    print(f"No new records added for {ticker} in era {era}.")

            except Exception as e:
                print(f"Failed to ingest {ticker} for {era}: {e}")
                db.rollback()

    db.close()
    print("\nCrisis eras data fetch completed.\n")

if __name__ == "__main__":
    fetch_crisis_data()
