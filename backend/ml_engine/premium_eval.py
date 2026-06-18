"""Measure the value of premium-newsletter signals (e.g. The Information).

Two lenses, both for understanding whether the subscription pays for itself:
- Coverage: how much premium news we have, over what span, which tickers.
- Forward predictive study: for premium scores whose forward window has CLOSED, did the ticker actually
  move the way the score predicted? Reports the hit-rate (direction matched) and the directional edge
  (the return from going long on bullish calls / short on bearish ones) per horizon. This is the
  practical meter — it populates as premium news accumulates and forward windows close.

The honest catch: this needs weeks of premium news plus elapsed time, so early on most windows are still
open (counted as `pending`) and the sample is too small for confidence.
"""
import bisect
from app.database import SessionLocal, NewsLLMScore, DailyPrice

HIGH_REL = 0.6   # split a higher-conviction subset (≈ directly-discussed names)


def _close_series(db, tickers):
    """{ticker: sorted [(date, close)]} from daily_prices."""
    by = {}
    for r in db.query(DailyPrice).filter(DailyPrice.ticker.in_(tickers)).all():
        if r.close:
            by.setdefault(r.ticker, []).append((r.date, r.close))
    for t in by:
        by[t].sort()
    return by


def _fwd_return(series, date, horizon):
    """Return over `horizon` trading days from the first close on/after `date`. None if window not closed."""
    dates = [d for d, _ in series]
    i = bisect.bisect_left(dates, date)
    j = i + horizon
    if i >= len(series) or j >= len(series):
        return None
    entry = series[i][1]
    if not entry:
        return None
    return series[j][1] / entry - 1.0


def _agg(samples):
    """samples: list of (pred_sign, fwd_return, rel). Returns hit-rate + directional edge stats."""
    n = len(samples)
    if not n:
        return {"n": 0}
    hits = sum(1 for ps, fwd, _ in samples if ps * fwd > 0)
    edges = [ps * fwd for ps, fwd, _ in samples]                       # long-bullish / short-bearish return
    wsum = sum(r for _, _, r in samples) or 1e-9
    edge_relwt = sum((ps * fwd) * r for ps, fwd, r in samples) / wsum
    return {"n": n, "hit_rate": round(hits / n, 3), "avg_edge": round(sum(edges) / n, 4),
            "avg_edge_relwt": round(edge_relwt, 4)}


def premium_value_report(horizons=(3, 5, 10), min_n=20):
    db = SessionLocal()
    prem = db.query(NewsLLMScore).filter(NewsLLMScore.source.like("premium:%")).all()
    if not prem:
        db.close()
        return {"coverage": {"scores": 0}, "horizons": {}, "enough_data": False,
                "note": "No premium news ingested yet — run `make premium-ingest`."}
    tickers = sorted({r.ticker for r in prem})
    closes = _close_series(db, tickers)
    db.close()

    dates = [r.date for r in prem]
    from collections import Counter
    coverage = {
        "scores": len(prem),
        "articles": len({r.article_id for r in prem}),
        "tickers": len(tickers),
        "date_min": min(dates), "date_max": max(dates),
        "high_conviction": sum(1 for r in prem if r.llm_relevance >= HIGH_REL),
        "top_tickers": Counter(r.ticker for r in prem).most_common(8),
    }

    horizon_out = {}
    enough = False
    for h in horizons:
        closed, pending, high = [], 0, []
        for r in prem:
            if abs(r.llm_score) < 1e-6:
                continue
            series = closes.get(r.ticker)
            if not series:
                continue
            fwd = _fwd_return(series, r.date, h)
            if fwd is None:
                pending += 1
                continue
            ps = 1.0 if r.llm_score > 0 else -1.0
            closed.append((ps, fwd, r.llm_relevance))
            if r.llm_relevance >= HIGH_REL:
                high.append((ps, fwd, r.llm_relevance))
        stats = _agg(closed)
        stats["pending"] = pending
        stats["high_conviction"] = _agg(high)
        horizon_out[str(h)] = stats
        if stats.get("n", 0) >= min_n:
            enough = True

    return {"coverage": coverage, "horizons": horizon_out, "enough_data": enough, "min_n": min_n}
