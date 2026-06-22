"""Route natural-language research queries to intent + entities."""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.core.config import TICKER_UNIVERSE

_TICKER_RE = re.compile(r"\b([A-Z]{1,5}(?:-[A-Z])?)\b")

_NAME_ALIASES = {
    "MICRON": "MU",
    "NVIDIA": "NVDA",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "BERKSHIRE": "BRK-B",
    "BROADCOM": "AVGO",
    "MICRON TECHNOLOGY": "MU",
    "ADVANCED MICRO": "AMD",
    "LAM RESEARCH": "LRCX",
    "APPLIED MATERIALS": "AMAT",
}

_SPILLOVER_KEYWORDS = (
    "earnings",
    "impact",
    "impacted",
    "affect",
    "affected",
    "spillover",
    "read-through",
    "read through",
    "ripple",
    "my portfolio",
    "my holdings",
    "holdings",
    "other stocks",
    "other names",
)

_INTENT_KEYWORDS = {
    "theme_rank": ["rank", "ranking", "rank-ordered", "most likely", "least successful", "outlook for", "companies in"],
    "ticker_outlook": ["outlook", "price target", "targets", "next year", "forecast", "consensus"],
    "sector_screen": ["sector", "under-valued", "undervalued", "over-valued", "overvalued", "sectors are"],
    "cross_theme": ["interdependent", "demand", "under-invested", "not been invested", "booming market"],
    "crowding_risk": ["overinvested", "drawdown", "crowded", "bubble"],
}

_STUB_INTENTS = {"sector_screen", "cross_theme", "crowding_risk"}


@dataclass
class RoutedQuery:
    intent: str
    tickers: List[str] = field(default_factory=list)
    theme: Optional[str] = None
    horizons: List[str] = field(default_factory=list)
    deep_research: bool = False
    raw_query: str = ""


def _extract_tickers(text: str) -> List[str]:
    known = {t.upper() for t in TICKER_UNIVERSE}
    found = []
    upper = text.upper()
    for name, sym in sorted(_NAME_ALIASES.items(), key=lambda x: len(x[0]), reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", upper) and sym not in found:
            found.append(sym)
    for m in _TICKER_RE.finditer(upper):
        tk = m.group(1)
        if tk in known or len(tk) >= 2:
            if tk not in found:
                found.append(tk)
    return found


def _is_spillover_intent(low: str) -> bool:
    return any(k in low for k in _SPILLOVER_KEYWORDS)


def _detect_intent(text: str) -> str:
    low = text.lower()
    if _is_spillover_intent(low) and _extract_tickers(text):
        return "event_spillover"
    if any(k in low for k in ("quantum", "theme", "companies who")):
        return "theme_rank"
    scores = {}
    for intent, kws in _INTENT_KEYWORDS.items():
        scores[intent] = sum(1 for k in kws if k in low)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "ticker_outlook" if _extract_tickers(text) else "theme_rank"
    return best


def _detect_theme(text: str) -> Optional[str]:
    low = text.lower()
    if "quantum" in low:
        return "quantum_computing"
    if "ai infra" in low or "ai infrastructure" in low:
        return "ai_infrastructure"
    if any(k in low for k in ("semiconductor", "semi ", "memory", "chip")):
        return "ai_infrastructure"
    return None


def route(query: str, deep_research: bool = False, extra_tickers: Optional[List[str]] = None) -> RoutedQuery:
    q = (query or "").strip()
    intent = _detect_intent(q)
    tickers = _extract_tickers(q)
    if extra_tickers:
        for t in extra_tickers:
            tk = t.upper().strip()
            if tk and tk not in tickers:
                tickers.append(tk)
    theme = _detect_theme(q)
    horizons = []
    if "q3" in q.lower() or "end of q3" in q.lower():
        horizons.append("q3_end")
    if "next year" in q.lower() or "12 month" in q.lower() or "1 year" in q.lower():
        horizons.append("12m")
    if "h2" in q.lower():
        horizons.append("h2")
    return RoutedQuery(
        intent=intent,
        tickers=tickers,
        theme=theme,
        horizons=horizons or ["12m"],
        deep_research=deep_research,
        raw_query=q,
    )


def is_stub_intent(intent: str) -> bool:
    return intent in _STUB_INTENTS
