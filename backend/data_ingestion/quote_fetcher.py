"""On-demand live quote stats for the stock-details drawer.

Layered, best-effort with graceful degradation:
  1. Stored DailyPrice OHLCV  → today's open/high/low/volume, 52-week high/low,
     average volume.  (Always works offline.)
  2. Alpaca snapshot          → live intraday price + today's bar overrides.
  3. Finnhub /stock/metric    → P/E, dividend yield, market cap.
  4. yfinance .info           → short interest + fill any missing fundamentals.

Borrow rate (cost-to-borrow) is intentionally left None — it isn't available from
any free source and needs a securities-lending feed (IBKR / Ortex / iBorrowDesk).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import requests

from app.core.config import FINNHUB_API_KEY
from app.database import DailyPrice
from data_ingestion.price_fetcher import map_ticker_to_yahoo

_TRADING_DAYS_1Y = 252
_AVG_VOL_WINDOW = 30


def _from_daily_prices(db, ticker: str) -> dict:
    """Today's bar, 52-week high/low and average volume from stored OHLCV."""
    start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    rows = (db.query(DailyPrice.date, DailyPrice.open, DailyPrice.high,
                     DailyPrice.low, DailyPrice.close, DailyPrice.volume)
              .filter(DailyPrice.ticker == ticker, DailyPrice.date >= start)
              .order_by(DailyPrice.date.asc()).all())
    if not rows:
        return {}
    window = rows[-_TRADING_DAYS_1Y:]
    last = rows[-1]
    vols = [r.volume for r in rows[-_AVG_VOL_WINDOW:] if r.volume]
    return {
        "price": round(last.close, 2),
        "open": round(last.open, 2),
        "day_high": round(last.high, 2),
        "day_low": round(last.low, 2),
        "volume": int(last.volume) if last.volume else None,
        "avg_volume": int(sum(vols) / len(vols)) if vols else None,
        "week52_high": round(max(r.high for r in window), 2),
        "week52_low": round(min(r.low for r in window), 2),
    }


def _from_alpaca(ticker: str) -> dict:
    """Live intraday overrides from the Alpaca snapshot (today's bar + last trade)."""
    try:
        from execution.executor import get_alpaca_api
        snap = get_alpaca_api().get_snapshot(ticker)
    except Exception:
        return {}
    out: dict = {}
    bar = getattr(snap, "daily_bar", None)
    if bar is not None:
        out.update({
            "open": round(float(bar.o), 2), "day_high": round(float(bar.h), 2),
            "day_low": round(float(bar.l), 2), "volume": int(bar.v),
        })
    trade = getattr(snap, "latest_trade", None)
    if trade is not None and getattr(trade, "price", None):
        out["price"] = round(float(trade.price), 2)
    return out


def _from_finnhub(ticker: str) -> dict:
    """P/E, dividend yield, market cap (+ 52-week fallback) from Finnhub metrics."""
    if not FINNHUB_API_KEY:
        return {}
    try:
        r = requests.get("https://finnhub.io/api/v1/stock/metric",
                         params={"symbol": ticker, "metric": "all", "token": FINNHUB_API_KEY},
                         timeout=15)
        if r.status_code != 200:
            return {}
        m = (r.json() or {}).get("metric") or {}
    except Exception:
        return {}
    cap = m.get("marketCapitalization")
    out = {
        "pe_ratio": m.get("peTTM") or m.get("peNormalizedAnnual"),
        "dividend_yield": m.get("dividendYieldIndicatedAnnual") or m.get("currentDividendYieldTTM"),
        "market_cap": float(cap) * 1_000_000 if cap else None,
        "week52_high": m.get("52WeekHigh"),
        "week52_low": m.get("52WeekLow"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _from_yfinance(ticker: str) -> dict:
    """Short interest (+ fundamentals backfill) from yfinance .info."""
    try:
        import yfinance as yf
        info = yf.Ticker(map_ticker_to_yahoo(ticker)).info or {}
    except Exception:
        return {}
    dy = info.get("dividendYield")
    spf = info.get("shortPercentOfFloat")
    out = {
        "pe_ratio": info.get("trailingPE"),
        "dividend_yield": dy * 100 if isinstance(dy, (int, float)) else None,
        "market_cap": info.get("marketCap"),
        "short_shares": info.get("sharesShort"),
        "short_pct_float": spf * 100 if isinstance(spf, (int, float)) else None,
        "short_ratio": info.get("shortRatio"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _round_money(v):
    return round(v, 2) if isinstance(v, (int, float)) else v


def fetch_quote_stats(db, ticker: str) -> dict:
    """Merge all sources (DB first, then live/external overrides) into one quote dict."""
    tk = ticker.upper().strip()
    out: dict = {
        "ticker": tk, "price": None, "open": None, "day_high": None, "day_low": None,
        "volume": None, "avg_volume": None, "week52_high": None, "week52_low": None,
        "market_cap": None, "pe_ratio": None, "dividend_yield": None,
        "short_shares": None, "short_pct_float": None, "short_ratio": None,
        "borrow_rate": None,  # no free source — placeholder until a feed is wired
    }
    # DB baseline → Finnhub fundamentals → Alpaca live bar → yfinance short data.
    # Later layers only override when they actually return a value.
    for layer in (_from_daily_prices(db, tk), _from_finnhub(tk), _from_alpaca(tk), _from_yfinance(tk)):
        for k, v in layer.items():
            if v is not None:
                out[k] = v
    for k in ("week52_high", "week52_low", "pe_ratio", "market_cap"):
        out[k] = _round_money(out[k])
    if isinstance(out.get("dividend_yield"), (int, float)):
        out["dividend_yield"] = round(out["dividend_yield"], 2)
    if isinstance(out.get("short_pct_float"), (int, float)):
        out["short_pct_float"] = round(out["short_pct_float"], 2)
    return out
