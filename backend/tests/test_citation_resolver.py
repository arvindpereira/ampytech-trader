"""Tests for citation resolver."""
from ml_engine.citation_resolver import attach_citations


def test_attach_citations_resolves_item_and_snapshot():
    class Item:
        id = 13
        ticker = "AMD"
        source = "news_llm"
        title = "AMD AI chip headline"
        excerpt = "test"
        published_at = "2026-06-01"
        source_url = "https://example.com/amd-ai"
        analyst_firm = None
        rating = None

    facts = {
        "AMD": {
            "momentum_3m": {"value": 0.12, "as_of": "2026-06-01", "source": "daily_prices", "coverage": "full"},
        }
    }
    report = {
        "template": "event_spillover",
        "primary_ticker": "MU",
        "holdings_impact": [{"ticker": "AMD", "sources": ["item:13", "snapshot:momentum_3m"]}],
    }
    out = attach_citations(report, {"AMD": [Item()]}, facts, db=None)
    assert out["citations_by_ref"]["item:13"]["url"] == "https://example.com/amd-ai"
    assert out["citations_by_ref"]["snapshot:momentum_3m"]["value"] == 0.12
    assert len(out["source_bundle"]) == 1


def test_render_inline_citations():
    from ml_engine.citation_resolver import render_inline_citations
    by = {"item:13": {"url": "https://ex.com", "title": "Headline"}}
    out = render_inline_citations("see item:13 and item:99", {**by, "item:99": {"title": "x"}})
    assert "[13](https://ex.com" in out
    assert "[99]*" in out
