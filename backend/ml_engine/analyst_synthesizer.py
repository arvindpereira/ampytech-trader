"""Synthesize external analyst items + snapshot into standardized sections."""
import json
import requests

from app.core.config import (
    OLLAMA_URL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    RESEARCH_LOCAL_MODEL,
)
from app.core.llm_cost import record_usage
from ml_engine.citation_validator import validate
from ml_engine.research_llm_router import model_for_tier
from ml_engine.research_templates import (
    EVENT_SPILLOVER_LLM_SCHEMA,
    SECTOR_SCREEN_LLM_SCHEMA,
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


def _resolve_synthesis_model(tier: str) -> tuple:
    """Return (model_name, provider) for synthesis."""
    if tier in ("standard", "premium", "expert") and OPENAI_API_KEY:
        resolved = model_for_tier("premium" if tier == "expert" else tier)
        return resolved, "openai"
    return RESEARCH_LOCAL_MODEL, "ollama"


def _editor_system(role: str, schema: str) -> str:
    return (
        f"You are a {role}. Write like a sharp buy-side analyst briefing a portfolio manager: "
        "lead with a clear thesis, cite specific numbers from the snapshot, connect news to price action, "
        "and flag what could change your view. Synthesize ONLY from provided data — never invent targets, "
        "ratings, or earnings. Cite sources as item:ID or snapshot:field. "
        f"Return JSON matching: {schema}"
    )


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


def _synthesize_llm(prompt: str, system: str, tier: str) -> dict:
    model, provider = _resolve_synthesis_model(tier)
    if provider == "openai":
        return _call_openai(prompt, system, model)
    return _call_ollama(prompt, system)


def synthesize_ticker(
    ticker: str,
    facts: dict,
    items,
    tier: str = "standard",
    query: str = "",
    news_rows=None,
) -> dict:
    schema = json.dumps(TICKER_OUTLOOK_LLM_SCHEMA, indent=2)
    system = _editor_system("skeptical equity research editor", schema)
    prompt = (
        f"User question: {query}\nTicker: {ticker}\n\nSNAPSHOT:\n{_snapshot_block(facts)}\n\n"
        f"EXTERNAL ITEMS:\n{_items_block(items)}"
    )
    if news_rows is not None:
        prompt += f"\n\nRECENT NEWS HEADLINES:\n{_news_block(news_rows)}"
    try:
        out = _synthesize_llm(prompt, system, tier)
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
    tier: str = "standard",
    query: str = "",
    news_by_ticker: dict = None,
) -> dict:
    schema = json.dumps(THEME_RANK_LLM_SCHEMA, indent=2)
    system = _editor_system(
        "thematic research editor. The RANK ORDER is FIXED — do not reorder. Explain why leaders lead and laggards lag",
        schema,
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
        out = _synthesize_llm(prompt, system, tier)
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
    tier: str = "standard",
    query: str = "",
    news_by_ticker: dict = None,
) -> dict:
    schema = json.dumps(EVENT_SPILLOVER_LLM_SCHEMA, indent=2)
    system = _editor_system(
        "equity research editor analyzing event read-through. "
        "The PRIMARY ticker is the event subject; RELATED tickers are the user's holdings. "
        "For each holding, explain directional read-through and relative sensitivity",
        schema,
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
        out = _synthesize_llm(prompt, system, tier)
        if "error" in out:
            return out
        return validate(out, all_ids)
    except Exception as e:
        return {"error": str(e)[:200], "caveats": ["Spillover synthesis failed — showing snapshots only."]}


def synthesize_sector(
    sectors: list,
    sector_rankings: list,
    ranked: list,
    facts_by_ticker: dict,
    items_by_ticker: dict,
    tier: str = "standard",
    query: str = "",
    news_by_ticker: dict = None,
    web_items=None,
) -> dict:
    schema = json.dumps(SECTOR_SCREEN_LLM_SCHEMA, indent=2)
    system = _editor_system(
        "sector strategist screening equities. Compare sector aggregates to constituent ranks. "
        "Highlight standouts and laggards with specific metrics",
        schema,
    )
    rank_lines = "\n".join(
        f"#{r['rank']} {r['sector']} score={r.get('screen_score')} upside={r.get('median_upside_pct')}"
        for r in (sector_rankings or [])[:8]
    )
    ticker_lines = "\n".join(
        f"#{r['rank']} {r['ticker']} score={r['score']}" for r in (ranked or [])
    )
    blocks = []
    news_by_ticker = news_by_ticker or {}
    for t, facts in facts_by_ticker.items():
        items = items_by_ticker.get(t, [])
        news_part = f"\nRECENT NEWS:\n{_news_block(news_by_ticker.get(t, []))}"
        blocks.append(f"=== {t} ===\n{_snapshot_block(facts)}\n{_items_block(items)}{news_part}")
    web_block = ""
    if web_items:
        web_block = f"\n\nWEB SEARCH SNIPPETS:\n{_items_block(web_items)}"
    prompt = (
        f"Sectors: {', '.join(sectors) or 'multi-sector'}\nQuery: {query}\n\n"
        f"SECTOR RANKINGS:\n{rank_lines}\n\nTICKER RANKINGS:\n{ticker_lines}\n\n"
        + "\n".join(blocks)
        + web_block
    )
    all_ids = [it.id for items in items_by_ticker.values() for it in items]
    if web_items:
        all_ids.extend([it.id for it in web_items])
    try:
        out = _synthesize_llm(prompt, system, tier)
        if "error" in out:
            return out
        return validate(out, all_ids)
    except Exception as e:
        return {"error": str(e)[:200], "caveats": ["Sector synthesis failed — rank tables still valid."]}
