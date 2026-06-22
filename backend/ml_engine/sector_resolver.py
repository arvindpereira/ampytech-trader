"""GICS sector handbook — structured RAG seed from research_sectors.json."""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from app.core.config import BASE_DIR, RESEARCH_MAX_TICKERS

_catalog_cache: Optional[dict] = None
_alias_cache: Optional[Dict[str, str]] = None


def _catalog_path() -> str:
    return os.path.join(BASE_DIR, "data", "research_sectors.json")


def load_catalog() -> dict:
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    path = _catalog_path()
    if not os.path.exists(path):
        _catalog_cache = {"sectors": []}
        return _catalog_cache
    with open(path) as f:
        _catalog_cache = json.load(f)
    return _catalog_cache


def invalidate_cache() -> None:
    global _catalog_cache, _alias_cache
    _catalog_cache = None
    _alias_cache = None


def canonical_sector(sector_raw: Optional[str]) -> Optional[str]:
    """Map Yahoo/GICS/Finnhub label to handbook sector key."""
    if not sector_raw:
        return None
    s = sector_raw.strip()
    for entry in list_sector_entries():
        if s.lower() == (entry.get("sector") or "").lower():
            return entry["sector"]
        if s.lower() == (entry.get("gics_name") or "").lower():
            return entry["sector"]
        if s.lower() == (entry.get("label") or "").lower():
            return entry["sector"]
    aliases = {
        "consumer staples": "Consumer Defensive",
        "consumer discretionary": "Consumer Cyclical",
        "health care": "Healthcare",
        "financials": "Financial Services",
        "information technology": "Technology",
        "materials": "Basic Materials",
        "technology": "Technology",
        "retail": "Consumer Cyclical",
        "healthcare": "Healthcare",
        "financial services": "Financial Services",
        "communication services": "Communication Services",
        "consumer cyclical": "Consumer Cyclical",
        "consumer defensive": "Consumer Defensive",
        "basic materials": "Basic Materials",
        "real estate": "Real Estate",
        "utilities": "Utilities",
        "industrials": "Industrials",
        "energy": "Energy",
        "pharmaceuticals": "Healthcare",
        "pharmaceutical": "Healthcare",
        "consumer products": "Consumer Defensive",
        "consumer product": "Consumer Defensive",
    }
    return aliases.get(s.lower(), s)


def portfolio_classification() -> List[dict]:
    return list(load_catalog().get("portfolio_classification") or [])


def portfolio_by_sector() -> Dict[str, List[dict]]:
    return dict(load_catalog().get("portfolio_by_sector") or {})


def portfolio_tickers_in_sector(sector_name: str) -> List[str]:
    entry = find_by_sector(sector_name)
    if entry:
        holds = entry.get("portfolio_holdings") or []
        return [h["ticker"] for h in holds if h.get("ticker")]
    return [h["ticker"] for h in portfolio_by_sector().get(sector_name, []) if h.get("ticker")]


def list_sector_entries() -> List[dict]:
    return list(load_catalog().get("sectors") or [])


def list_sectors() -> List[dict]:
    """Catalog entries for API (id, label, sector, etf, seed count)."""
    out = []
    for s in list_sector_entries():
        out.append({
            "id": s.get("id"),
            "label": s.get("label"),
            "gics_name": s.get("gics_name"),
            "sector": s.get("sector"),
            "etf_spdr": s.get("etf_spdr"),
            "subsectors": s.get("subsectors") or [],
            "seed_ticker_count": len(s.get("seed_tickers") or []),
        })
    return out


def _build_aliases() -> Dict[str, str]:
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache
    aliases: Dict[str, str] = {}
    for entry in list_sector_entries():
        sector = (entry.get("sector") or "").strip()
        if not sector:
            continue
        for phrase in entry.get("keywords") or []:
            aliases[phrase.lower()] = sector
        for name in (entry.get("gics_name"), entry.get("label"), entry.get("sector")):
            if name:
                aliases[str(name).lower()] = sector
    _alias_cache = aliases
    return aliases


def match_sectors_in_query(query: str) -> List[str]:
    """Return canonical sector names (ticker_metadata.sector) mentioned in query."""
    low = (query or "").lower()
    found: List[str] = []
    for phrase, sector in sorted(_build_aliases().items(), key=lambda x: len(x[0]), reverse=True):
        if phrase in low and sector not in found:
            found.append(sector)
    return found


def find_by_sector(sector_name: str) -> Optional[dict]:
    target = (sector_name or "").strip().lower()
    for entry in list_sector_entries():
        if (entry.get("sector") or "").lower() == target:
            return entry
        if (entry.get("gics_name") or "").lower() == target:
            return entry
        if (entry.get("label") or "").lower() == target:
            return entry
    return None


def seed_tickers(sector_name: str, limit: int = RESEARCH_MAX_TICKERS) -> List[str]:
    entry = find_by_sector(sector_name)
    if not entry:
        return []
    out: List[str] = []
    for row in sorted(entry.get("seed_tickers") or [], key=lambda r: r.get("rank", 99)):
        tk = (row.get("ticker") or "").upper().strip()
        if tk and tk not in out:
            out.append(tk)
    return out[:limit]


def etf_map() -> Dict[str, str]:
    """Canonical sector name → SPDR ETF proxy."""
    m: Dict[str, str] = {}
    for entry in list_sector_entries():
        sector = entry.get("sector")
        etf = entry.get("etf_spdr")
        if sector and etf:
            m[sector] = etf
    return m


def sector_brief(sector_name: str) -> Optional[dict]:
    """Structured handbook slice for LLM synthesis context."""
    entry = find_by_sector(sector_name)
    if not entry:
        return None
    seeds = entry.get("seed_tickers") or []
    port = entry.get("portfolio_holdings") or portfolio_by_sector().get(entry.get("sector") or "", [])
    return {
        "sector": entry.get("sector"),
        "gics_name": entry.get("gics_name"),
        "label": entry.get("label"),
        "subsectors": entry.get("subsectors") or [],
        "etf_spdr": entry.get("etf_spdr"),
        "etf_alt": entry.get("etf_alt") or [],
        "index": entry.get("index"),
        "portfolio_holdings": port,
        "representative_names": [
            f"#{r.get('rank')} {r.get('ticker')} ({r.get('company')}) — {r.get('subsector')}: {r.get('notes', '')}"
            for r in sorted(seeds, key=lambda x: x.get("rank", 99))
        ],
        "source": load_catalog().get("source_doc"),
        "refreshed_at": load_catalog().get("last_refreshed_at"),
    }


def sector_handbook_for(sectors: List[str]) -> List[dict]:
    return [b for s in sectors if (b := sector_brief(s))]


def merge_constituents(
    kb_tickers: List[str],
    sector_name: str,
    *,
    limit: int = RESEARCH_MAX_TICKERS,
) -> List[str]:
    """KB snapshots first, portfolio holdings, then cap-ranked seeds."""
    out = list(kb_tickers)
    for tk in portfolio_tickers_in_sector(sector_name):
        if tk not in out:
            out.append(tk)
    for tk in seed_tickers(sector_name, limit=limit):
        if tk not in out:
            out.append(tk)
        if len(out) >= limit:
            break
    return out[:limit]
