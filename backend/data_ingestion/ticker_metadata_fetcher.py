"""Fetch sector, industry, and market cap into ticker_metadata."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import FINNHUB_API_KEY, RESEARCH_KB_FINNHUB_SLEEP
from app.database import SessionLocal, TickerMetadata, init_db
from data_ingestion.price_fetcher import map_ticker_to_yahoo
from ml_engine.sector_resolver import canonical_sector

_SKIP_SEED = frozenset({
    "SPY", "QQQ", "TLT", "IEF", "BIL", "LQD", "TIP", "GLD", "GSG",
    "XLK", "XLF", "XLE", "XLV", "XLP", "XLC", "XLI", "XLY", "XLB", "XLRE", "XLU",
})

_STALE_DAYS = 7
_BATCH_SIZE = 25

# Finnhub profile2 often lacks GICS sector — map industry strings heuristically
_INDUSTRY_SECTOR = (
    ("semiconductor", "Technology"),
    ("software", "Technology"),
    ("internet content", "Communication Services"),
    ("internet retail", "Consumer Cyclical"),
    ("auto manufacturers", "Consumer Cyclical"),
    ("automobile", "Consumer Cyclical"),
    ("auto component", "Consumer Cyclical"),
    ("banks", "Financial Services"),
    ("banking", "Financial Services"),
    ("insurance", "Financial Services"),
    ("drug", "Healthcare"),
    ("biotechnology", "Healthcare"),
    ("healthcare", "Healthcare"),
    ("pharmaceutical", "Healthcare"),
    ("oil & gas", "Energy"),
    ("mining", "Basic Materials"),
    ("chemical", "Basic Materials"),
    ("industrial", "Industrials"),
    ("machinery", "Industrials"),
    ("aerospace", "Industrials"),
    ("airline", "Industrials"),
    ("logistics", "Industrials"),
    ("transportation", "Industrials"),
    ("road & rail", "Industrials"),
    ("electrical equipment", "Industrials"),
    ("reit", "Real Estate"),
    ("utilities", "Utilities"),
    ("beverage", "Consumer Defensive"),
    ("food", "Consumer Defensive"),
    ("household", "Consumer Defensive"),
    ("tobacco", "Consumer Defensive"),
    ("consumer product", "Consumer Defensive"),
    ("retail", "Consumer Cyclical"),
    ("hotel", "Consumer Cyclical"),
    ("restaurant", "Consumer Cyclical"),
    ("leisure", "Consumer Cyclical"),
    ("media", "Communication Services"),
    ("communication", "Communication Services"),
)


def infer_sector_from_industry(industry: Optional[str]) -> Optional[str]:
    if not industry:
        return None
    low = industry.lower()
    for needle, sector in _INDUSTRY_SECTOR:
        if needle in low:
            return sector
    return None


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def is_equity_ticker(ticker: str) -> bool:
    tk = ticker.upper().strip()
    if tk in _SKIP_SEED:
        return False
    if tk.startswith("X:") or tk.startswith("C:"):
        return False
    return True


def _is_stale(row: Optional[TickerMetadata]) -> bool:
    if not row or not row.updated_at:
        return True
    try:
        updated = datetime.strptime(row.updated_at[:10], "%Y-%m-%d").date()
    except Exception:
        return True
    return (date.today() - updated) > timedelta(days=_STALE_DAYS)


def _finnhub_profile(ticker: str) -> Optional[dict]:
    if not FINNHUB_API_KEY:
        return None
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/stock/profile2",
            params={"symbol": ticker, "token": FINNHUB_API_KEY},
            timeout=20,
        )
        if r.status_code in (401, 403, 404, 429):
            return None
        r.raise_for_status()
        time.sleep(RESEARCH_KB_FINNHUB_SLEEP)
        data = r.json() or {}
    except Exception:
        return None
    sector = data.get("gsector") or data.get("sector")
    industry = data.get("finnhubIndustry") or data.get("industry")
    cap = data.get("marketCapitalization")
    if not sector and not industry and not cap:
        return None
    # Finnhub market cap is in millions
    mcap = float(cap) * 1_000_000 if cap else None
    sector_val = canonical_sector(sector) if sector else canonical_sector(industry)
    if not sector_val:
        sector_val = infer_sector_from_industry(industry)
    return {
        "ticker": ticker.upper().strip(),
        "sector": sector_val,
        "industry": str(industry).strip() if industry else None,
        "market_cap": mcap,
        "source": "finnhub",
        "updated_at": _now(),
    }


def _yahoo_batch(symbols: List[str]) -> dict:
    """Batch Yahoo quote — sector, industry, marketCap per symbol."""
    if not symbols:
        return {}
    yahoo_syms = ",".join(map_ticker_to_yahoo(s) for s in symbols)
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    try:
        r = requests.get(url, params={"symbols": yahoo_syms}, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 429:
            time.sleep(5.0)
            r = requests.get(url, params={"symbols": yahoo_syms}, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return {}
        results = (r.json().get("quoteResponse") or {}).get("result") or []
    except Exception:
        return {}
    out = {}
    for item in results:
        sym = (item.get("symbol") or "").upper().replace("-", ".")
        # Map back BRK-B style
        sector = item.get("sector")
        industry = item.get("industry")
        cap = item.get("marketCap")
        if sym:
            out[sym] = {
                "sector": sector,
                "industry": industry,
                "market_cap": float(cap) if cap else None,
            }
    return out


def fetch_metadata(ticker: str) -> Optional[dict]:
    tk = ticker.upper().strip()
    row = _finnhub_profile(tk)
    if row and row.get("sector"):
        return row
    batch = _yahoo_batch([tk])
    y = batch.get(tk) or batch.get(map_ticker_to_yahoo(tk).upper())
    if not y:
        return row
    merged = row or {"ticker": tk, "source": "yahoo", "updated_at": _now()}
    merged["sector"] = merged.get("sector") or y.get("sector")
    merged["industry"] = merged.get("industry") or y.get("industry")
    merged["market_cap"] = merged.get("market_cap") or y.get("market_cap")
    if not merged.get("sector") and not merged.get("industry") and not merged.get("market_cap"):
        return None
    merged["source"] = merged.get("source") or "yahoo"
    merged["updated_at"] = _now()
    return merged


def upsert_metadata(db, row: dict) -> None:
    stmt = sqlite_insert(TickerMetadata).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker"],
        set_={k: stmt.excluded[k] for k in row if k != "ticker"},
    )
    db.execute(stmt)


def refresh_tickers(
    tickers: Iterable[str],
    db=None,
    *,
    force: bool = False,
    sleep_sec: float = 0.5,
) -> dict:
    init_db()
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    stats = {"requested": 0, "updated": 0, "skipped": 0, "failed": 0, "cached": 0}
    try:
        ordered = sorted({t.upper().strip() for t in tickers if t and is_equity_ticker(t)})
        to_fetch: List[str] = []
        for tk in ordered:
            stats["requested"] += 1
            if not force:
                existing = db.query(TickerMetadata).filter(TickerMetadata.ticker == tk).first()
                if existing and existing.sector and not _is_stale(existing):
                    stats["cached"] += 1
                    continue
            to_fetch.append(tk)

        # Batch Yahoo where Finnhub misses
        for i in range(0, len(to_fetch), _BATCH_SIZE):
            batch = to_fetch[i : i + _BATCH_SIZE]
            finnhub_rows = {}
            need_yahoo = []
            for tk in batch:
                fh = _finnhub_profile(tk)
                if fh:
                    finnhub_rows[tk] = fh
                else:
                    need_yahoo.append(tk)
            yahoo_map = _yahoo_batch(need_yahoo) if need_yahoo else {}
            for tk in batch:
                row = finnhub_rows.get(tk)
                if not row or not row.get("sector"):
                    y = yahoo_map.get(tk) or yahoo_map.get(map_ticker_to_yahoo(tk).upper().replace("-", "."))
                    if y:
                        sector = canonical_sector(y.get("sector")) or canonical_sector(y.get("industry")) or infer_sector_from_industry(y.get("industry"))
                        row = {
                            "ticker": tk,
                            "sector": sector,
                            "industry": y.get("industry"),
                            "market_cap": y.get("market_cap"),
                            "source": "yahoo",
                            "updated_at": _now(),
                        }
                    elif row and not row.get("sector"):
                        row["sector"] = canonical_sector(row.get("industry")) or infer_sector_from_industry(row.get("industry"))
                if not row or (not row.get("sector") and not row.get("market_cap")):
                    stats["failed"] += 1
                    continue
                upsert_metadata(db, row)
                stats["updated"] += 1
            db.commit()
            time.sleep(sleep_sec)

        stats["skipped"] = stats["requested"] - stats["updated"] - stats["failed"] - stats["cached"]
        return stats
    finally:
        if close:
            db.close()
