"""Tests for deterministic theme ranking."""
from ml_engine.rank_engine import rank_tickers


def _facts(quality=0.7, upside=0.1, news=0.2, mom=0.05):
    def f(v):
        return {"value": v, "coverage": "full", "as_of": "2026-01-01", "source": "test"}
    return {
        "quality": f(quality),
        "upside_pct": f(upside),
        "news_score_30d": f(news),
        "momentum_3m": f(mom),
        "tier": f("core"),
    }


def test_rank_order_stable():
    facts = {
        "AAA": _facts(quality=0.9, upside=0.3),
        "BBB": _facts(quality=0.4, upside=0.05),
    }
    ranked = rank_tickers(facts)
    assert ranked[0]["ticker"] == "AAA"
    assert ranked[0]["rank"] == 1
    assert ranked[1]["ticker"] == "BBB"


def test_rank_has_breakdown():
    ranked = rank_tickers({"X": _facts()})
    assert ranked[0]["score_breakdown"]["quality"] is not None
