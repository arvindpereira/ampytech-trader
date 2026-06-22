"""Earnings report context: transcripts, revisions, surprises."""
from typing import Dict, List, Optional, Tuple

from data_ingestion.earnings_content_fetcher import (
    earnings_facts_for_ticker,
    latest_transcript,
    next_eps_estimate,
)


def resolve_earnings_context(db, ticker: str) -> Dict:
    """Structured earnings bundle for synthesis."""
    ticker = ticker.upper().strip()
    facts = earnings_facts_for_ticker(db, ticker)
    tx = latest_transcript(db, ticker)
    nxt = next_eps_estimate(db, ticker)
    return {
        "ticker": ticker,
        "earnings_facts": facts,
        "next_eps": nxt,
        "transcript": _transcript_dict(tx) if tx else None,
        "transcript_available": bool(tx and (tx.content or tx.summary_excerpt)),
        "revision_30d": facts.get("eps_revision_30d"),
        "last_surprise_pct": facts.get("last_earnings_surprise_pct"),
    }


def _transcript_dict(tx) -> dict:
    content = tx.summary_excerpt or (tx.content[:12000] if tx.content else "")
    return {
        "period": tx.period,
        "call_date": tx.call_date,
        "title": tx.title,
        "finnhub_id": tx.finnhub_id,
        "excerpt": content,
        "source": tx.source,
    }


def resolve_earnings_tickers(routed, db) -> Tuple[List[str], Dict]:
    tickers = list(routed.tickers or [])
    if not tickers:
        return [], {"error": "no_ticker", "message": "Mention a ticker for earnings analysis."}
    primary = tickers[0]
    meta = resolve_earnings_context(db, primary)
    return [primary], meta
