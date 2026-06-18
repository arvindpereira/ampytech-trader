"""Central LLM pricing + a usage ledger so we can track and refine real $/token over time.

Pricing is USD per 1M tokens as (input, output). Seeded with published values where known and clearly
flagged ESTIMATEs otherwise (e.g. gpt-5.5). Override without code changes by editing
`backend/data/llm_pricing.json` (e.g. {"gpt-5_5": [1.25, 10.0]}).

Every OpenAI call records (model, tokens, est cost) to `backend/data/llm_usage.jsonl`. That lets us
divide your *real* dashboard spend over a period by the recorded tokens to pin the true rate, then
update the estimate. A single blended daily total can't isolate one model's unit price; the ledger can.
"""
import os
import json
import threading
from datetime import datetime

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_PRICING_FILE = os.path.join(_DATA_DIR, "llm_pricing.json")
_USAGE_FILE = os.path.join(_DATA_DIR, "llm_usage.jsonl")
_LOCK = threading.Lock()

# USD per 1M tokens: (input, output). ESTIMATE entries are refined from the usage ledger vs real spend.
_DEFAULT_PRICING = {
    "gpt-4o-mini": [0.15, 0.60],   # published
    "gpt-4o": [2.50, 10.0],        # published
    "gpt-4.1-mini": [0.40, 1.60],  # published
    "gpt-4.1-nano": [0.10, 0.40],  # published
    "gpt-4.1": [2.00, 8.00],       # published
    # gpt-5.5 (and its dated snapshot id gpt-5_5-2026-04-23) — ESTIMATE, flagship 2026 tier. Refine me.
    "gpt-5.5": [1.25, 10.0],
    "gpt-5_5": [1.25, 10.0],
    "gpt-5": [1.25, 10.0],         # ESTIMATE
}


def load_pricing():
    """Defaults merged with any local override in llm_pricing.json."""
    pricing = dict(_DEFAULT_PRICING)
    try:
        if os.path.exists(_PRICING_FILE):
            override = json.load(open(_PRICING_FILE))
            pricing.update({k: v for k, v in override.items() if isinstance(v, list)})
    except Exception:
        pass
    return pricing


def _rate(model):
    """Longest matching model-prefix wins (so 'gpt-4o-mini' beats 'gpt-4o')."""
    pricing = load_pricing()
    for k in sorted(pricing, key=len, reverse=True):
        if model.startswith(k):
            return pricing[k]
    return None


def estimate_cost(model, prompt_tokens, completion_tokens, batch=False):
    """USD estimate, or None if the model isn't priced (e.g. local Ollama). Batch API is 50% off."""
    r = _rate(model)
    if not r:
        return None
    c = prompt_tokens / 1e6 * r[0] + completion_tokens / 1e6 * r[1]
    return c * 0.5 if batch else c


def record_usage(purpose, model, prompt_tokens, completion_tokens, batch=False):
    """Append a usage row to the ledger and return the estimated cost (None if unpriced)."""
    cost = estimate_cost(model, prompt_tokens, completion_tokens, batch=batch)
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "date": datetime.now().strftime("%Y-%m-%d"), "purpose": purpose, "model": model,
           "prompt_tokens": int(prompt_tokens or 0), "completion_tokens": int(completion_tokens or 0),
           "batch": bool(batch), "est_cost": round(cost, 6) if cost is not None else None}
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with _LOCK:
            with open(_USAGE_FILE, "a") as f:
                f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return cost


def usage_summary(since=None):
    """Aggregate the ledger by model (optionally for rows with date >= `since`).
    Returns {by_model, totals}. Divide your real dashboard spend by these tokens to refine pricing."""
    by_model = {}
    tot = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0}
    try:
        for line in open(_USAGE_FILE):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if since and r.get("date", "") < since:
                continue
            m = by_model.setdefault(r["model"], {"calls": 0, "prompt_tokens": 0,
                                                 "completion_tokens": 0, "est_cost": 0.0})
            for agg in (m, tot):
                agg["calls"] += 1
                agg["prompt_tokens"] += r.get("prompt_tokens", 0)
                agg["completion_tokens"] += r.get("completion_tokens", 0)
                agg["est_cost"] += r.get("est_cost") or 0.0
    except FileNotFoundError:
        pass
    return {"by_model": by_model, "totals": tot, "pricing": load_pricing()}
