"""Tests for portfolio-aware research context expansion."""
from unittest.mock import MagicMock

from ml_engine.context_expander import expand_spillover_tickers, is_spillover_query
from ml_engine.intent_router import route


def test_micron_alias_resolves_to_mu():
    routed = route("How might Micron earnings impact my semiconductor holdings?")
    assert "MU" in routed.tickers
    assert routed.intent == "event_spillover"


def test_spillover_query_detection():
    assert is_spillover_query("MU earnings impact on my holdings")
    assert not is_spillover_query("What is NVDA price target?")


def test_expand_spillover_includes_sector_peers():
    db = MagicMock()

    def query_side(model):
        m = MagicMock()
        if model.__name__ == "CompanySnapshot":
            def filter_side(*args, **kwargs):
                f = MagicMock()
                f.order_by.return_value.first.side_effect = [
                    MagicMock(sector="Technology", industry="Semiconductors"),
                    MagicMock(sector="Technology", industry="Semiconductors"),
                    MagicMock(sector="Technology", industry="Semiconductors"),
                ]
                return f
            m.filter.side_effect = filter_side
        elif model.__name__ == "TickerMetadata":
            m.filter.return_value.first.return_value = None
        return m

    db.query.side_effect = query_side
    routed = route("How might MU earnings impact my semiconductor holdings?")
    tickers, meta = expand_spillover_tickers(
        "MU", routed, db, portfolio=["MU", "NVDA", "AMD", "JPM"]
    )
    assert tickers[0] == "MU"
    assert "NVDA" in tickers
    assert "AMD" in tickers
    assert "JPM" not in tickers
    assert meta["primary"] == "MU"
