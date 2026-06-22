"""Ingest Finnhub earnings data: EPS estimates, surprises, and call transcripts."""
import hashlib
import json
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Union

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.config import FINNHUB_API_KEY, RESEARCH_KB_FINNHUB_SLEEP
from app.database import (
    EarningsEstimateSnapshot,
    EarningsSurprise,
    EarningsTranscript,
    ExternalAnalystItem,
    SessionLocal,
)


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return date.today().isoformat()


def _finnhub_get(path: str, params: dict) -> Optional[Union[dict, list]]:
    if not FINNHUB_API_KEY:
        return None
    params = {**params, "token": FINNHUB_API_KEY}
    try:
        r = requests.get(f"https://finnhub.io/api/v1{path}", params=params, timeout=25)
        if r.status_code in (401, 403, 404):
            return None
        r.raise_for_status()
        time.sleep(RESEARCH_KB_FINNHUB_SLEEP)
        return r.json()
    except Exception:
        return None


def _dedup_id(source: str, ticker: str, title: str, published_at: str) -> str:
    raw = f"{source}|{ticker}|{title}|{published_at}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _upsert_item(db, **kwargs) -> bool:
    sid = kwargs.get("source_id") or _dedup_id(
        kwargs["source"], kwargs.get("ticker") or "", kwargs.get("title") or "", kwargs.get("published_at") or ""
    )
    kwargs["source_id"] = sid
    existing = (
        db.query(ExternalAnalystItem)
        .filter(ExternalAnalystItem.source == kwargs["source"], ExternalAnalystItem.source_id == sid)
        .first()
    )
    if existing:
        return False
    db.add(ExternalAnalystItem(created_at=_now(), **kwargs))
    return True


def _transcript_excerpt(content: str, max_len: int = 3500) -> str:
    if not content:
        return ""
    text = content.strip()
    if len(text) <= max_len:
        return text
    head = text[: int(max_len * 0.65)]
    tail = text[-int(max_len * 0.25) :]
    return f"{head}\n\n[... transcript truncated ...]\n\n{tail}"


def fetch_eps_estimates(db, ticker: str) -> int:
    """Snapshot quarterly EPS estimates for revision tracking."""
    data = _finnhub_get("/stock/eps-estimate", {"symbol": ticker, "freq": "quarterly"})
    if not data or not isinstance(data, list):
        return 0
    as_of = _today()
    added = 0
    for row in data[:8]:
        period = row.get("period")
        if not period:
            continue
        snap = {
            "ticker": ticker,
            "period": period,
            "freq": "quarterly",
            "as_of_date": as_of,
            "eps_avg": row.get("epsAvg"),
            "eps_high": row.get("epsHigh"),
            "eps_low": row.get("epsLow"),
            "num_analysts": row.get("numberAnalysts"),
            "raw_json": json.dumps(row)[:4000],
        }
        stmt = sqlite_insert(EarningsEstimateSnapshot).values(snap)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "period", "freq", "as_of_date"],
            set_={k: stmt.excluded[k] for k in snap if k not in ("ticker", "period", "freq", "as_of_date")},
        )
        db.execute(stmt)
        added += 1
    if added:
        db.commit()
    return added


def fetch_earnings_surprises(db, ticker: str) -> int:
    data = _finnhub_get("/stock/earnings", {"symbol": ticker})
    if not data or not isinstance(data, list):
        return 0
    added = 0
    for row in data[:12]:
        period = row.get("period")
        if not period:
            continue
        actual = row.get("actual")
        estimate = row.get("estimate")
        surprise = None
        if actual is not None and estimate not in (None, 0):
            try:
                surprise = (float(actual) - float(estimate)) / abs(float(estimate))
            except (TypeError, ValueError):
                surprise = None
        snap = {
            "ticker": ticker,
            "period": period,
            "report_date": row.get("date") or row.get("quarter"),
            "eps_actual": actual,
            "eps_estimate": estimate,
            "surprise_pct": surprise,
            "raw_json": json.dumps(row)[:4000],
            "fetched_at": _now(),
        }
        stmt = sqlite_insert(EarningsSurprise).values(snap)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "period"],
            set_={k: stmt.excluded[k] for k in snap if k not in ("ticker", "period")},
        )
        db.execute(stmt)
        added += 1
    if added:
        db.commit()
    return added


def fetch_transcripts(db, ticker: str, max_fetch: int = 2) -> int:
    """Fetch latest earnings transcripts. Requires Finnhub Professional+."""
    listing = _finnhub_get("/stock/transcripts/list", {"symbol": ticker})
    if not listing or not isinstance(listing, list):
        return 0
    added = 0
    for entry in listing[:max_fetch]:
        fid = entry.get("id") or entry.get("transcriptId")
        if not fid:
            continue
        fid = str(fid)
        existing = (
            db.query(EarningsTranscript)
            .filter(EarningsTranscript.ticker == ticker, EarningsTranscript.finnhub_id == fid)
            .first()
        )
        if existing and existing.content:
            continue
        detail = _finnhub_get("/stock/transcripts", {"id": fid})
        if not detail:
            continue
        content = detail.get("transcript") or detail.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(str(x) for x in content)
        content = str(content) if content else ""
        quarter = entry.get("quarter") or detail.get("quarter")
        year = entry.get("year") or detail.get("year")
        period = f"{year}Q{quarter}" if year and quarter else entry.get("period")
        call_date = entry.get("time") or entry.get("date") or detail.get("time")
        title = entry.get("title") or detail.get("title") or f"{ticker} earnings call {period or fid}"
        excerpt = _transcript_excerpt(content)
        row = {
            "ticker": ticker,
            "finnhub_id": fid,
            "quarter": int(quarter) if quarter is not None else None,
            "year": int(year) if year is not None else None,
            "period": period,
            "call_date": str(call_date)[:10] if call_date else None,
            "title": str(title)[:300],
            "content": content[:500_000] if content else None,
            "summary_excerpt": excerpt[:8000],
            "source": "finnhub:transcript",
            "fetched_at": _now(),
        }
        stmt = sqlite_insert(EarningsTranscript).values(row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "finnhub_id"],
            set_={k: stmt.excluded[k] for k in row if k not in ("ticker", "finnhub_id")},
        )
        db.execute(stmt)
        if _upsert_item(
            db,
            ticker=ticker,
            source="finnhub:transcript",
            source_id=f"transcript-{fid}",
            published_at=row["call_date"],
            title=row["title"],
            excerpt=excerpt[:3500],
            raw_json=json.dumps({"finnhub_id": fid, "period": period})[:4000],
        ):
            added += 1
    if added:
        db.commit()
    return added


def eps_revision_30d(db, ticker: str) -> Optional[float]:
    """Change in next-quarter consensus EPS estimate over ~30 days."""
    cutoff = (date.today() - timedelta(days=35)).isoformat()
    rows = (
        db.query(EarningsEstimateSnapshot)
        .filter(
            EarningsEstimateSnapshot.ticker == ticker.upper(),
            EarningsEstimateSnapshot.freq == "quarterly",
        )
        .order_by(EarningsEstimateSnapshot.period.asc(), EarningsEstimateSnapshot.as_of_date.asc())
        .all()
    )
    if not rows:
        return None
    by_period: Dict[str, list] = {}
    for r in rows:
        by_period.setdefault(r.period, []).append(r)
    next_period = sorted(by_period.keys())[0]
    snaps = by_period[next_period]
    if len(snaps) < 2:
        return None
    old = [s for s in snaps if s.as_of_date <= cutoff]
    if not old:
        old = [snaps[0]]
    latest = snaps[-1]
    base = old[-1].eps_avg
    if base in (None, 0) or latest.eps_avg is None:
        return None
    return (float(latest.eps_avg) - float(base)) / abs(float(base))


def latest_transcript(db, ticker: str) -> Optional[EarningsTranscript]:
    return (
        db.query(EarningsTranscript)
        .filter(EarningsTranscript.ticker == ticker.upper())
        .order_by(EarningsTranscript.call_date.desc(), EarningsTranscript.fetched_at.desc())
        .first()
    )


def latest_surprise(db, ticker: str) -> Optional[EarningsSurprise]:
    return (
        db.query(EarningsSurprise)
        .filter(EarningsSurprise.ticker == ticker.upper())
        .order_by(EarningsSurprise.period.desc())
        .first()
    )


def next_eps_estimate(db, ticker: str) -> Optional[dict]:
    row = (
        db.query(EarningsEstimateSnapshot)
        .filter(
            EarningsEstimateSnapshot.ticker == ticker.upper(),
            EarningsEstimateSnapshot.freq == "quarterly",
            EarningsEstimateSnapshot.as_of_date == _today(),
        )
        .order_by(EarningsEstimateSnapshot.period.asc())
        .first()
    )
    if not row:
        row = (
            db.query(EarningsEstimateSnapshot)
            .filter(EarningsEstimateSnapshot.ticker == ticker.upper(), EarningsEstimateSnapshot.freq == "quarterly")
            .order_by(EarningsEstimateSnapshot.as_of_date.desc(), EarningsEstimateSnapshot.period.asc())
            .first()
        )
    if not row:
        return None
    return {
        "period": row.period,
        "eps_avg": row.eps_avg,
        "eps_high": row.eps_high,
        "eps_low": row.eps_low,
        "num_analysts": row.num_analysts,
        "as_of_date": row.as_of_date,
    }


def earnings_facts_for_ticker(db, ticker: str) -> dict:
    """Bundle earnings metrics for company_snapshots materialization."""
    rev = eps_revision_30d(db, ticker)
    nxt = next_eps_estimate(db, ticker)
    surp = latest_surprise(db, ticker)
    tx = latest_transcript(db, ticker)
    return {
        "eps_revision_30d": rev,
        "next_eps_estimate": nxt,
        "last_earnings_surprise_pct": surp.surprise_pct if surp else None,
        "last_earnings_period": surp.period if surp else None,
        "latest_transcript_period": tx.period if tx else None,
        "latest_transcript_date": tx.call_date if tx else None,
        "has_transcript": bool(tx and tx.content),
    }


def refresh_ticker(db, ticker: str) -> dict:
    ticker = ticker.upper().strip()
    stats = {"eps_estimates": 0, "earnings": 0, "transcripts": 0}
    stats["eps_estimates"] = fetch_eps_estimates(db, ticker)
    stats["earnings"] = fetch_earnings_surprises(db, ticker)
    stats["transcripts"] = fetch_transcripts(db, ticker)
    return stats


def refresh(tickers, db=None) -> dict:
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    total = {"tickers": 0, **{k: 0 for k in ("eps_estimates", "earnings", "transcripts")}}
    try:
        for t in sorted({x.upper().strip() for x in tickers if x}):
            s = refresh_ticker(db, t)
            total["tickers"] += 1
            for k in ("eps_estimates", "earnings", "transcripts"):
                total[k] += s.get(k, 0)
        return total
    finally:
        if close:
            db.close()
