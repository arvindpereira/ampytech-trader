"""Resolve LLM citation tokens (item:ID, snapshot:field) to human labels and URLs."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set

from ml_engine.citation_validator import _collect_source_ids

_ITEM_RE = re.compile(r"item:(\d+)")
_SNAPSHOT_RE = re.compile(r"snapshot:([\w_]+)")

_SNAPSHOT_LABELS = {
    "price": "Share price",
    "momentum_1w": "1-week momentum",
    "momentum_1m": "1-month momentum",
    "momentum_3m": "3-month momentum",
    "momentum_1y": "1-year momentum",
    "target_mean": "Consensus price target",
    "target_high": "High price target",
    "target_low": "Low price target",
    "upside_pct": "Implied upside vs target",
    "num_analysts": "Analyst count",
    "recommendation_key": "Consensus recommendation",
    "tier": "Classification tier",
    "quality": "Quality score",
    "news_score_7d": "7-day news sentiment",
    "news_score_30d": "30-day news sentiment",
    "sector": "Sector",
    "industry": "Industry",
    "verdict": "Fundamental verdict",
    "volatility": "Volatility",
}


def _lookup_news_url(db, ticker: str, title: str) -> Optional[str]:
    """Match promoted headlines to sentiment source logs that store publisher URLs."""
    if not db or not title:
        return None
    try:
        from app.core.text import normalize_headline
        from app.database import SentimentSourceLog

        norm = normalize_headline(title)
        rows = (
            db.query(SentimentSourceLog)
            .filter(SentimentSourceLog.ticker == ticker.upper(), SentimentSourceLog.source == "news")
            .order_by(SentimentSourceLog.id.desc())
            .limit(300)
            .all()
        )
        for row in rows:
            if row.url and normalize_headline(row.title or "") == norm:
                return row.url
    except Exception:
        pass
    return None


def _item_url(db, item) -> Optional[str]:
    if getattr(item, "source_url", None):
        return item.source_url
    if getattr(item, "source", None) == "news_llm":
        url = _lookup_news_url(db, item.ticker or "", item.title or "")
        if url:
            return url
    return None


def _item_dict(db, item) -> Dict[str, Any]:
    return {
        "ref": f"item:{item.id}",
        "kind": "item",
        "id": item.id,
        "ticker": item.ticker,
        "source": item.source,
        "title": item.title,
        "excerpt": (item.excerpt or "")[:300] if item.excerpt else None,
        "published_at": item.published_at,
        "url": _item_url(db, item),
        "analyst_firm": item.analyst_firm,
        "rating": item.rating,
    }


def _snapshot_dict(ticker: str, field: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    blob = facts.get(field) if isinstance(facts.get(field), dict) else {}
    val = blob.get("value") if isinstance(blob, dict) else facts.get(field)
    return {
        "ref": f"snapshot:{field}",
        "kind": "snapshot",
        "ticker": ticker,
        "field": field,
        "label": _SNAPSHOT_LABELS.get(field, field.replace("_", " ")),
        "value": val,
        "as_of": blob.get("as_of") if isinstance(blob, dict) else None,
        "source_table": blob.get("source") if isinstance(blob, dict) else "company_snapshots",
    }


def _collect_snapshot_refs(obj: Any, found: Set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "sources" and isinstance(v, list):
                for s in v:
                    if isinstance(s, str):
                        for m in _SNAPSHOT_RE.finditer(s):
                            found.add(m.group(1))
            else:
                _collect_snapshot_refs(v, found)
    elif isinstance(obj, list):
        for x in obj:
            _collect_snapshot_refs(x, found)


def _default_ticker(report: dict, facts_by_ticker: Dict[str, Dict]) -> Optional[str]:
    if report.get("primary_ticker"):
        return report["primary_ticker"]
    if report.get("ticker"):
        return report["ticker"]
    tickers = list(facts_by_ticker.keys())
    return tickers[0] if len(tickers) == 1 else None


def attach_citations(
    report: dict,
    items_by_ticker: Dict[str, list],
    facts_by_ticker: Dict[str, Dict],
    db=None,
) -> dict:
    """Add resolved `citations` list and `citations_by_ref` map for UI/wiki."""
    item_index = {str(it.id): it for items in items_by_ticker.values() for it in items}

    cited_items: Set[str] = set()
    _collect_source_ids(report, cited_items)

    snapshot_fields: Set[str] = set()
    _collect_snapshot_refs(report, snapshot_fields)

    citations: List[Dict[str, Any]] = []
    by_ref: Dict[str, Dict[str, Any]] = {}

    for iid in sorted(cited_items, key=lambda x: int(x) if x.isdigit() else 0):
        item = item_index.get(iid)
        if item:
            row = _item_dict(db, item)
        else:
            row = {
                "ref": f"item:{iid}",
                "kind": "item",
                "id": int(iid) if iid.isdigit() else iid,
                "title": None,
                "url": None,
                "missing": True,
                "note": "Referenced by the model but not in the source bundle for this query.",
            }
        citations.append(row)
        by_ref[row["ref"]] = row

    default_tk = _default_ticker(report, facts_by_ticker)
    for field in sorted(snapshot_fields):
        tk = default_tk
        if report.get("holdings_impact"):
            for h in report["holdings_impact"]:
                srcs = h.get("sources") or []
                if any(f"snapshot:{field}" in s for s in srcs):
                    tk = h.get("ticker") or tk
                    break
        facts = facts_by_ticker.get(tk or "", {}) if tk else {}
        row = _snapshot_dict(tk or "—", field, facts)
        if row["ref"] not in by_ref:
            citations.append(row)
            by_ref[row["ref"]] = row

    # Also expose every item fed to the model (vetted source bundle), not only cited IDs.
    source_bundle: List[Dict[str, Any]] = []
    for items in items_by_ticker.values():
        for it in items:
            row = _item_dict(db, it)
            source_bundle.append(row)
    source_bundle.sort(key=lambda r: (r.get("ticker") or "", r.get("id") or 0))

    out = dict(report)
    out["citations"] = citations
    out["citations_by_ref"] = by_ref
    out["source_bundle"] = source_bundle
    return out


_ITEM_INLINE_RE = re.compile(r"item:(\d+)|snapshot:([\w_]+)")


def render_inline_citations(text: str, by_ref: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """Replace item:N / snapshot:field tokens with [N] markdown links or [N]* markers."""
    if not text or not by_ref:
        return text or ""

    def _repl(m: re.Match) -> str:
        if m.group(1):
            ref, label = f"item:{m.group(1)}", f"[{m.group(1)}]"
        else:
            ref, label = f"snapshot:{m.group(2)}", f"[{m.group(2)}]"
        meta = by_ref.get(ref) or {}
        url = meta.get("url")
        if url:
            title = (meta.get("title") or label).replace("]", "\\]")
            return f"[{label[1:-1]}]({url} \"{title}\")"
        return f"{label}*"

    return _ITEM_INLINE_RE.sub(_repl, text)


def render_inline_citations_html(text: str, by_ref: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """HTML-safe inline citations for static wiki pages."""
    if not text or not by_ref:
        from html import escape
        return escape(text or "")

    from html import escape

    parts: List[str] = []
    last = 0
    for m in _ITEM_INLINE_RE.finditer(text):
        parts.append(escape(text[last:m.start()]))
        if m.group(1):
            ref, label = f"item:{m.group(1)}", f"[{m.group(1)}]"
        else:
            ref, label = f"snapshot:{m.group(2)}", f"[{m.group(2)}]"
        meta = by_ref.get(ref) or {}
        url = meta.get("url")
        if url:
            parts.append(
                f"<a href='{escape(url)}' title='{escape(meta.get('title') or ref)}'>"
                f"<strong>{escape(label)}</strong></a>"
            )
        else:
            tip = escape(meta.get("title") or "no url available")
            parts.append(f"<strong title='{tip}'>{escape(label)}*</strong>")
        last = m.end()
    parts.append(escape(text[last:]))
    return "".join(parts)
