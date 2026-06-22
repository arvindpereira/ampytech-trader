"""Resolve research themes to ticker lists."""
import json
import os
from typing import Dict, List, Optional

from app.core.config import BASE_DIR, RESEARCH_MAX_TICKERS


def _themes_path() -> str:
    return os.path.join(BASE_DIR, "data", "research_themes.json")


def load_themes() -> Dict[str, dict]:
    path = _themes_path()
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def list_themes() -> List[dict]:
    themes = load_themes()
    return [{"id": k, **v} for k, v in themes.items()]


def resolve(theme_id: Optional[str], extra_tickers: Optional[List[str]] = None) -> List[str]:
    themes = load_themes()
    tickers: List[str] = []
    if theme_id and theme_id in themes:
        tickers.extend(themes[theme_id].get("tickers") or [])
    if extra_tickers:
        tickers.extend(extra_tickers)
    out = []
    for t in tickers:
        tk = t.upper().strip()
        if tk and tk not in out:
            out.append(tk)
    return out[:RESEARCH_MAX_TICKERS]
