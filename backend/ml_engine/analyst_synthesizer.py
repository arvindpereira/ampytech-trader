"""Synthesize external analyst items + snapshot into standardized sections."""
import json
import requests

from app.core.config import (
    OLLAMA_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_EXPERT_MODEL,
    RESEARCH_EXPERT_MODEL,
    RESEARCH_LOCAL_MODEL,
)
from app.core.llm_cost import record_usage
from ml_engine.citation_validator import validate
from ml_engine.research_templates import (
    EVENT_SPILLOVER_LLM_SCHEMA,
    THEME_RANK_LLM_SCHEMA,
    TICKER_OUTLOOK_LLM_SCHEMA,
)


def _items_block(items) -> str:
    lines = []
    for it in items:
        lines.append(
            f"- item:{it.id} [{it.source}] {it.published_at or 'n/a'}: {it.title or ''} — {(it.excerpt or '')[:400]}"
        )
    return "\n".join(lines) or "(no external items)"


def _snapshot_block(facts: dict) -> str:
    return json.dumps(facts, indent=2)[:6000]


def _news_block(rows) -> str:
    if not rows:
        return "(no recent scored headlines)"
    lines = []
    for r in rows:
        lines.append(
            f"- {r.published_utc or r.date}: {(r.title or '')[:200]} "
            f"(score {r.llm_score:+.2f}, rel {r.llm_relevance:.2f})"
        )
    return "\n".join(lines)


def _call_ollama(prompt: str, system: str) -> dict:
    body = {
        "model": RESEARCH_LOCAL_MODEL,
        "prompt": f"{system}\n\n{prompt}",
        "stream": False,
        "format": "json",
    }
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=180)
    r.raise_for_status()
    j = r.json()
    text = (j.get("response") or "").strip()
    record_usage(
        "research_synthesis",
        RESEARCH_LOCAL_MODEL,
        j.get("prompt_eval_count", 0),
        j.get("eval_count", 0),
        provider="ollama",
    )
    return json.loads(text)


def _call_openai(prompt: str, system: str, model: str) -> dict:
    if not OPENAI_API_KEY:
        return {"error": "OPENAI_API_KEY not set"}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=180)
    r.raise_for_status()
    j = r.json()
    content = j["choices"][0]["message"]["content"]
    u = j.get("usage") or {}
    record_usage(
        "research_synthesis",
        model,
        u.get("prompt_tokens", 0),
        u.get("completion_tokens", 0),
        provider="openai",
    )
    return json.loads(content)


def synthesize_ticker(
    ticker: str,
    facts: dict,
    items,
    tier: str = "local",
    query: str = "",
    news_rows=None,
) -> dict:
    schema = json.dumps(TICKER_OUTLOOK_LLM_SCHEMA, indent=2)
    system = (
        "You are a skeptical equity research editor. Synthesize ONLY from the provided snapshot and "
        "external items. Cite sources as item:ID or snapshot:field. Do not invent price targets or ratings. "
        f"Return JSON matching: {schema}"
    )
    prompt = (
        f"User question: {query}\nTicker: {ticker}\n\nSNAPSHOT:\n{_snapshot_block(facts)}\n\n"
        f"EXTERNAL ITEMS:\n{_items_block(items)}"
    )
    if news_rows is not None:
        prompt += f"\n\nRECENT NEWS HEADLINES:\n{_news_block(news_rows)}"
    try:
        if tier == "expert" and OPENAI_API_KEY:
            out = _call_openai(prompt, system, RESEARCH_EXPERT_MODEL or OPENAI_EXPERT_MODEL)
        else:
            out = _call_ollama(prompt, system)
        if "error" in out:
            return out
        return validate(out, [it.id for it in items])
    except Exception as e:
        return {"error": str(e)[:200], "caveats": ["Synthesis failed — showing template with snapshot only."]}


def synthesize_theme(
    theme: str,
    ranked: list,
    facts_by_ticker: dict,
    items_by_ticker: dict,
    tier: str = "expert",
    query: str = "",
    news_by_ticker: dict = None,
) -> dict:
    schema = json.dumps(THEME_RANK_LLM_SCHEMA, indent=2)
    system = (
        "You are a skeptical thematic research editor. The RANK ORDER is FIXED — do not reorder. "
        "Explain the ranking using snapshot facts and external items. Cite item:ID. "
        f"Return JSON matching: {schema}"
    )
    rank_lines = "\n".join(
        f"#{r['rank']} {r['ticker']} score={r['score']} breakdown={r.get('score_breakdown')}"
        for r in ranked
    )
    blocks = []
    news_by_ticker = news_by_ticker or {}
    for t, facts in facts_by_ticker.items():
        items = items_by_ticker.get(t, [])
        news_part = f"\nRECENT NEWS:\n{_news_block(news_by_ticker.get(t, []))}"
        blocks.append(f"=== {t} ===\n{_snapshot_block(facts)}\n{_items_block(items)}{news_part}")
    prompt = f"Theme: {theme}\nQuery: {query}\n\nRANKING (fixed):\n{rank_lines}\n\n" + "\n".join(blocks)
    all_ids = [it.id for items in items_by_ticker.values() for it in items]
    try:
        if tier == "expert" and OPENAI_API_KEY:
            out = _call_openai(prompt, system, RESEARCH_EXPERT_MODEL or OPENAI_EXPERT_MODEL)
        else:
            out = _call_ollama(prompt, system)
        if "error" in out:
            return out
        return validate(out, all_ids)
    except Exception as e:
        return {"error": str(e)[:200], "caveats": ["Theme synthesis failed — rank table still valid."]}


def synthesize_spillover(
    primary: str,
    related: list,
    facts_by_ticker: dict,
    items_by_ticker: dict,
    tier: str = "expert",
    query: str = "",
    news_by_ticker: dict = None,
) -> dict:
    schema = json.dumps(EVENT_SPILLOVER_LLM_SCHEMA, indent=2)
    system = (
        "You are a skeptical equity research editor analyzing event read-through. "
        "The PRIMARY ticker is the event subject; RELATED tickers are the user's holdings. "
        "Explain plausible spillover using ONLY provided snapshots, items, and headlines. "
        "Cite item:ID or snapshot:field. Do not invent earnings figures. "
        f"Return JSON matching: {schema}"
    )
    news_by_ticker = news_by_ticker or {}
    primary_block = (
        f"PRIMARY {primary}\n{_snapshot_block(facts_by_ticker.get(primary, {}))}\n"
        f"{_items_block(items_by_ticker.get(primary, []))}\n"
        f"RECENT NEWS:\n{_news_block(news_by_ticker.get(primary, []))}"
    )
    related_blocks = []
    for t in related:
        related_blocks.append(
            f"HOLDING {t}\n{_snapshot_block(facts_by_ticker.get(t, {}))}\n"
            f"{_items_block(items_by_ticker.get(t, []))}\n"
            f"RECENT NEWS:\n{_news_block(news_by_ticker.get(t, []))}"
        )
    prompt = (
        f"User question: {query}\n\n{primary_block}\n\n"
        + "\n\n".join(related_blocks)
    )
    all_ids = [it.id for items in items_by_ticker.values() for it in items]
    try:
        if tier == "expert" and OPENAI_API_KEY:
            out = _call_openai(prompt, system, RESEARCH_EXPERT_MODEL or OPENAI_EXPERT_MODEL)
        else:
            out = _call_ollama(prompt, system)
        if "error" in out:
            return out
        return validate(out, all_ids)
    except Exception as e:
        return {"error": str(e)[:200], "caveats": ["Spillover synthesis failed — showing snapshots only."]}
