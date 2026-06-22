"""Central LLM pricing + a DB-backed usage ledger so the server tracks token counts and estimated
cost across every model it uses (OpenAI gpt-5.5 / gpt-4o-mini, local Ollama gemma4, …).

Token counts are recorded as ground truth in the `llm_usage` table; cost is always (re)computed from
the pricing table, so summaries stay correct even after you *calibrate* a model's rate against the real
OpenAI dashboard. Pricing is USD per 1M tokens as (input, output); override without code changes via
`backend/data/llm_pricing.json` (also what calibration writes to).
"""
import os
import json
from datetime import datetime

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_PRICING_FILE = os.path.join(_DATA_DIR, "llm_pricing.json")

# USD per 1M tokens: (input, output). ESTIMATE entries are refined via calibrate_model() vs real spend.
_DEFAULT_PRICING = {
    "gpt-4o-mini": [0.15, 0.60],   # published
    "gpt-4o": [2.50, 10.0],        # published
    "gpt-4.1-mini": [0.40, 1.60],  # published
    "gpt-4.1-nano": [0.10, 0.40],  # published
    "gpt-4.1": [2.00, 8.00],       # published
    # gpt-5.5 (and its dated snapshot id gpt-5_5-2026-04-23) — ESTIMATE, flagship 2026 tier. Calibrate me.
    "gpt-5.5": [1.25, 10.0],
    "gpt-5.4": [1.00, 8.0],        # ESTIMATE
    "gpt-5_5": [1.25, 10.0],
    "gpt-5": [1.25, 10.0],         # ESTIMATE
}


def load_pricing():
    """Defaults merged with any local override in llm_pricing.json."""
    pricing = dict(_DEFAULT_PRICING)
    try:
        if os.path.exists(_PRICING_FILE):
            override = json.load(open(_PRICING_FILE))
            pricing.update({k: v for k, v in override.items() if isinstance(v, list) and len(v) == 2})
    except Exception:
        pass
    return pricing


def _save_override(model, rates):
    """Persist a calibrated rate for an exact model id into the override file."""
    cur = {}
    try:
        if os.path.exists(_PRICING_FILE):
            cur = json.load(open(_PRICING_FILE))
    except Exception:
        cur = {}
    cur[model] = [round(rates[0], 6), round(rates[1], 6)]
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_PRICING_FILE, "w") as f:
        json.dump(cur, f, indent=2)
    return cur[model]


def _rate(model, pricing=None):
    """Longest matching model-prefix wins (so 'gpt-4o-mini' beats 'gpt-4o', and an exact id beats both)."""
    pricing = pricing or load_pricing()
    for k in sorted(pricing, key=len, reverse=True):
        if model.startswith(k):
            return pricing[k]
    return None


def estimate_cost(model, prompt_tokens, completion_tokens, batch=False, pricing=None):
    """USD estimate, or None if the model isn't priced (e.g. local Ollama). Batch API is 50% off."""
    r = _rate(model, pricing)
    if not r:
        return None
    c = prompt_tokens / 1e6 * r[0] + completion_tokens / 1e6 * r[1]
    return c * 0.5 if batch else c


def _infer_provider(model):
    m = model.lower()
    if m.startswith(("gpt", "o1", "o3", "o4", "text-", "chatgpt")):
        return "openai"
    return "ollama"


def record_usage(purpose, model, prompt_tokens, completion_tokens, provider=None, requests=1, batch=False):
    """Append a usage row to the `llm_usage` DB table and return the estimated cost (None if unpriced)."""
    from app.database import SessionLocal, LLMUsage
    cost = estimate_cost(model, prompt_tokens, completion_tokens, batch=batch)
    now = datetime.now()
    db = SessionLocal()
    try:
        db.add(LLMUsage(
            ts=now.isoformat(timespec="seconds"), date=now.strftime("%Y-%m-%d"),
            provider=provider or _infer_provider(model), model=model, purpose=purpose,
            requests=int(requests or 1), prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0), batch=bool(batch),
            est_cost=round(cost, 6) if cost is not None else None))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
    return cost


def usage_summary(since=None):
    """Aggregate the ledger by model. Cost is recomputed from current pricing (so it reflects any
    calibration). Returns {by_model, totals, pricing, since}."""
    from app.database import SessionLocal, LLMUsage
    pricing = load_pricing()
    by_model, by_purpose = {}, {}
    tot = {"calls": 0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0}
    db = SessionLocal()
    try:
        q = db.query(LLMUsage)
        if since:
            q = q.filter(LLMUsage.date >= since)
        for r in q.all():
            m = by_model.setdefault(r.model, {"provider": r.provider, "calls": 0, "requests": 0,
                                              "prompt_tokens": 0, "completion_tokens": 0,
                                              "est_cost": 0.0, "priced": _rate(r.model, pricing) is not None})
            p = by_purpose.setdefault(r.purpose or "other", {"calls": 0, "requests": 0,
                                      "prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0})
            c = estimate_cost(r.model, r.prompt_tokens, r.completion_tokens, batch=r.batch, pricing=pricing) or 0.0
            for agg in (m, p, tot):
                agg["calls"] += 1
                agg["requests"] += r.requests or 1
                agg["prompt_tokens"] += r.prompt_tokens or 0
                agg["completion_tokens"] += r.completion_tokens or 0
                agg["est_cost"] += c
    finally:
        db.close()
    return {"by_model": by_model, "by_purpose": by_purpose, "totals": tot, "pricing": pricing, "since": since}


def calibrate_model(model, actual_cost, since=None):
    """Scale a model's (input, output) rate so its estimated cost over the window matches the real
    `actual_cost` you read from the OpenAI dashboard. Writes the calibrated rate to llm_pricing.json
    keyed on the exact model id (longest-prefix → it wins). Returns the new rate + the factor applied."""
    from app.database import SessionLocal, LLMUsage
    pricing = load_pricing()
    base = _rate(model, pricing)
    if not base:
        return {"error": f"No pricing entry matches '{model}'."}
    db = SessionLocal()
    p_tok = c_tok = 0
    est = 0.0
    try:
        q = db.query(LLMUsage).filter(LLMUsage.model == model)
        if since:
            q = q.filter(LLMUsage.date >= since)
        for r in q.all():
            p_tok += r.prompt_tokens or 0
            c_tok += r.completion_tokens or 0
            est += estimate_cost(model, r.prompt_tokens, r.completion_tokens, batch=r.batch, pricing=pricing) or 0.0
    finally:
        db.close()
    if est <= 0:
        return {"error": f"No priced usage recorded for '{model}'" + (f" since {since}." if since else ".")}
    factor = actual_cost / est
    new_rate = _save_override(model, [base[0] * factor, base[1] * factor])
    return {"model": model, "old_rate": base, "new_rate": new_rate, "factor": round(factor, 4),
            "est_cost_before": round(est, 4), "actual_cost": actual_cost,
            "prompt_tokens": p_tok, "completion_tokens": c_tok}
