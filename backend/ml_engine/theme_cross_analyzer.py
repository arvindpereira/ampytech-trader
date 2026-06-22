"""Cross-theme demand / interdependency analysis (Phase 3)."""
from __future__ import annotations

from typing import Dict, List, Tuple

from ml_engine.intent_router import RoutedQuery
from ml_engine.rank_engine import rank_tickers
from ml_engine.research_dossier import get_many
from ml_engine.research_framework import METHODOLOGY_VERSION
from ml_engine.theme_resolver import list_themes, load_themes, resolve


def detect_themes_in_query(query: str) -> List[str]:
    """Match configured research themes from query text."""
    low = (query or "").lower()
    themes = load_themes()
    found: List[str] = []
    for tid, meta in themes.items():
        label = (meta.get("label") or tid).lower()
        if tid.replace("_", " ") in low or label in low:
            found.append(tid)
            continue
        for kw in (meta.get("keywords") or []):
            if kw.lower() in low:
                found.append(tid)
                break
    if "quantum" in low and "quantum_computing" not in found:
        found.append("quantum_computing")
    if any(k in low for k in ("ai infra", "semiconductor", "chip", "memory")) and "ai_infrastructure" not in found:
        found.append("ai_infrastructure")
    return found


def analyze_cross_theme(routed: RoutedQuery, db) -> Tuple[List[str], Dict]:
    """Union tickers across themes; highlight overlap as demand linkage proxy."""
    theme_ids = detect_themes_in_query(routed.raw_query)
    if not theme_ids:
        theme_ids = [t for t in (routed.theme,) if t] or list(load_themes().keys())[:2]

    by_theme: Dict[str, List[str]] = {}
    for tid in theme_ids:
        by_theme[tid] = resolve(tid, routed.tickers)

    all_tickers: List[str] = []
    for ts in by_theme.values():
        for t in ts:
            if t not in all_tickers:
                all_tickers.append(t)

    overlap = []
    if len(theme_ids) >= 2:
        sets = [set(by_theme.get(tid, [])) for tid in theme_ids]
        inter = set.intersection(*sets) if sets else set()
        overlap = sorted(inter)

    labels = {tid: (load_themes().get(tid) or {}).get("label", tid) for tid in theme_ids}
    meta = {
        "themes": theme_ids,
        "theme_labels": labels,
        "tickers_by_theme": by_theme,
        "overlap_tickers": overlap,
        "methodology_version": METHODOLOGY_VERSION,
        "framework_note": (
            "Cross-theme linkage uses configured theme membership + ticker overlap as a "
            "demand-proxy (not input-output tables). See docs/research-methodology.md."
        ),
    }
    return all_tickers, meta


def cross_theme_facts(db, tickers: List[str], meta: Dict) -> Dict:
    facts = get_many(tickers, db=db)
    return {
        "ranked": rank_tickers(facts),
        "facts_by_ticker": facts,
        "meta": meta,
    }
