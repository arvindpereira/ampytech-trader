"""Unified portfolio holdings — internal (equity lots, virtual) and external (statement)."""
from __future__ import annotations

from typing import Dict, List


def all_holdings(db) -> List[dict]:
    """All positions with provenance (deduped per ticker+source+account)."""
    from app.database import (
        EquityLot,
        ExternalStatementHolding,
        ResearchWatchlist,
        VirtualPosition,
    )

    rows: List[dict] = []
    seen = set()

    def add(ticker: str, source: str, **extra):
        tk = (ticker or "").upper().strip()
        if not tk:
            return
        key = (tk, source, extra.get("account") or "")
        if key in seen:
            return
        seen.add(key)
        rows.append({"ticker": tk, "source": source, **extra})

    for lot in db.query(EquityLot).all():
        add(lot.ticker, "equity_lot", account=lot.account_label, shares=lot.shares, lot_type=lot.lot_type)
    for pos in db.query(VirtualPosition).filter(VirtualPosition.quantity > 0).all():
        add(pos.ticker, "virtual", account=getattr(pos, "mode", None), shares=pos.quantity)
    for h in db.query(ExternalStatementHolding).all():
        add(
            h.ticker,
            f"external:{h.account_label}",
            account=h.account_label,
            shares=h.shares,
            statement_date=h.statement_date,
        )
    for w in db.query(ResearchWatchlist).all():
        add(w.ticker, "watchlist")

    return rows


def portfolio_tickers(db) -> List[str]:
    """Unique tickers across all holding sources."""
    out: List[str] = []
    for h in all_holdings(db):
        tk = h["ticker"]
        if tk not in out:
            out.append(tk)
    return out


def holdings_by_ticker(db) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for h in all_holdings(db):
        grouped.setdefault(h["ticker"], []).append(h)
    return grouped
