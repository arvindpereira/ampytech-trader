"""Best-effort Massive-backed analyst forecast snapshots for the equity advisor.

The advisor can operate with only local price data. Analyst fields are optional because Massive account
entitlements vary; when Benzinga analyst endpoints are unavailable this module caches a price-only row.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta
from statistics import median
from typing import Dict, Iterable, Optional

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import MASSIVE_API_KEY, MASSIVE_BASE_URL
from app.database import AnalystForecast, DailyPrice, RecentPrice, SessionLocal, init_db


def _latest_local_price(db, ticker: str) -> Optional[float]:
    row = (db.query(RecentPrice).filter(RecentPrice.ticker == ticker)
           .order_by(RecentPrice.date.desc()).first())
    if row and row.close is not None:
        return float(row.close)
    row = (db.query(DailyPrice).filter(DailyPrice.ticker == ticker)
           .order_by(DailyPrice.date.desc()).first())
    if row and row.close is not None:
        return float(row.close)
    return None


def _massive_get(path: str, params: Dict[str, str]):
    if not MASSIVE_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
    url = f"{MASSIVE_BASE_URL}{path}"
    backoff = 1.5
    for attempt in range(4):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code in (401, 403, 404):
                return None
            if r.status_code == 429:
                time.sleep(backoff)
                backoff *= 2
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 3:
                return None
            time.sleep(backoff)
            backoff *= 2
    return None


def _extract_ratings(payload) -> Dict[str, Optional[float]]:
    results = (payload or {}).get("results") or []
    targets, ratings = [], []
    counts = {"strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0}
    for rec in results:
        for key in ("price_target", "target_price", "pt", "price_target_new"):
            val = rec.get(key)
            if isinstance(val, (int, float)) and val > 0:
                targets.append(float(val))
                break
        rating = str(rec.get("rating") or rec.get("action_company") or rec.get("analyst_rating") or "").lower()
        if "strong" in rating and "buy" in rating:
            counts["strong_buy"] += 1
        elif "buy" in rating or "outperform" in rating or "overweight" in rating:
            counts["buy"] += 1
        elif "hold" in rating or "neutral" in rating or "market perform" in rating:
            counts["hold"] += 1
        elif "strong" in rating and "sell" in rating:
            counts["strong_sell"] += 1
        elif "sell" in rating or "underperform" in rating or "underweight" in rating:
            counts["sell"] += 1
        if rating:
            ratings.append(rating)
    out = {k: (v if v else None) for k, v in counts.items()}
    if targets:
        out.update({
            "target_mean": sum(targets) / len(targets),
            "target_high": max(targets),
            "target_low": min(targets),
            "target_median": median(targets),
            "num_analysts": len(targets),
        })
    else:
        out.update({"target_mean": None, "target_high": None, "target_low": None, "target_median": None, "num_analysts": len(results) or None})
    score_count = sum(counts.values())
    if score_count:
        score = (counts["strong_buy"] * 1 + counts["buy"] * 2 + counts["hold"] * 3 + counts["sell"] * 4 + counts["strong_sell"] * 5) / score_count
        out["recommendation_mean"] = score
        out["recommendation_key"] = "buy" if score <= 2.0 else "hold" if score <= 3.0 else "sell"
    else:
        out["recommendation_mean"] = None
        out["recommendation_key"] = None
    return out


def _fetch_massive_analyst(ticker: str) -> Dict[str, Optional[float]]:
    # Massive/Polygon-compatible Benzinga endpoint. Entitlement-dependent; absence is expected.
    payload = _massive_get("/benzinga/v1/analyst-ratings", {"ticker": ticker, "limit": "50", "sort": "date.desc"})
    return _extract_ratings(payload) if payload else {}


def snapshot_forecast(ticker: str, db=None, refresh: bool = True) -> Optional[AnalystForecast]:
    close_db = False
    if db is None:
        init_db()
        db = SessionLocal()
        close_db = True
    try:
        ticker = ticker.upper().strip()
        today = date.today().isoformat()
        existing = db.query(AnalystForecast).filter(AnalystForecast.ticker == ticker, AnalystForecast.as_of_date == today).first()
        if existing and not refresh:
            return existing

        price = _latest_local_price(db, ticker)
        fields = _fetch_massive_analyst(ticker) if refresh else {}
        target_mean = fields.get("target_mean")
        upside = ((target_mean - price) / price) if (target_mean is not None and price) else None
        values = {
            "ticker": ticker,
            "as_of_date": today,
            "current_price": price,
            "target_mean": target_mean,
            "target_high": fields.get("target_high"),
            "target_low": fields.get("target_low"),
            "target_median": fields.get("target_median"),
            "num_analysts": fields.get("num_analysts"),
            "recommendation_mean": fields.get("recommendation_mean"),
            "recommendation_key": fields.get("recommendation_key"),
            "strong_buy": fields.get("strong_buy"),
            "buy": fields.get("buy"),
            "hold": fields.get("hold"),
            "sell": fields.get("sell"),
            "strong_sell": fields.get("strong_sell"),
            "upside_pct": upside,
            "source": "massive:benzinga" if fields else "massive/local-price",
        }
        stmt = sqlite_insert(AnalystForecast).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "as_of_date"],
            set_={k: v for k, v in values.items() if k not in ("ticker", "as_of_date")},
        )
        db.execute(stmt)
        db.commit()
        return db.query(AnalystForecast).filter(AnalystForecast.ticker == ticker, AnalystForecast.as_of_date == today).first()
    finally:
        if close_db:
            db.close()


def latest_or_refresh(ticker: str, db, stale_days: int = 1) -> Optional[AnalystForecast]:
    ticker = ticker.upper().strip()
    latest = (db.query(AnalystForecast).filter(AnalystForecast.ticker == ticker)
              .order_by(AnalystForecast.as_of_date.desc()).first())
    if latest:
        try:
            as_of = datetime.strptime(latest.as_of_date, "%Y-%m-%d").date()
            if date.today() - as_of <= timedelta(days=stale_days):
                return latest
        except Exception:
            pass
    try:
        return snapshot_forecast(ticker, db=db, refresh=True) or latest
    except Exception:
        return latest


def refresh_forecasts(tickers: Iterable[str], db=None):
    close_db = False
    if db is None:
        init_db()
        db = SessionLocal()
        close_db = True
    try:
        return [snapshot_forecast(t, db=db, refresh=True) for t in sorted({t.upper().strip() for t in tickers if t})]
    finally:
        if close_db:
            db.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Refresh advisor analyst forecast snapshots")
    p.add_argument("--tickers", default="ADBE,PINS")
    a = p.parse_args()
    rows = refresh_forecasts(a.tickers.split(","))
    for row in rows:
        if row:
            print(f"{row.ticker}: price={row.current_price} target={row.target_mean} source={row.source}")
