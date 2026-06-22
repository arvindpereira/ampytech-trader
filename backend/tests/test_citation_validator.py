"""Tests for citation validator."""
from ml_engine.citation_validator import validate


def test_flags_missing_item_ids():
    syn = {
        "consensus_view": {"text": "foo", "sources": ["item:99"]},
        "caveats": [],
    }
    out = validate(syn, [1, 2])
    assert any("99" in c for c in out["caveats"])


def test_passes_valid_ids():
    syn = {"consensus_view": {"text": "ok", "sources": ["item:1"]}, "caveats": []}
    out = validate(syn, [1])
    assert not out.get("_citation_warnings")
