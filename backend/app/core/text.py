"""Small shared text utilities."""
import re

_WS = re.compile(r"[^a-z0-9]+")


def normalize_headline(title):
    """Normalize a news headline for cross-source / by-day dedup.

    Lowercases, collapses every run of non-alphanumeric characters to a single space, and strips.
    This makes near-identical headlines from different sources (Polygon vs Alpaca/Benzinga) — which
    differ only in punctuation, casing, or spacing — compare equal so we never double-count the signal.
    Returns "" for empty/None input (callers should treat empty as "not dedupable").
    """
    return _WS.sub(" ", (title or "").lower()).strip()
