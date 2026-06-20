"""AI analyst for the Crash Radar wargame (defensive-policy scenario comparison).

Takes the structured output of `run_scenario_comparison` (equity curves + metrics for each policy
across historical bear regimes and synthetic crashes) and asks a strong OpenAI model to explain — in
plain English and honestly — what the glide-path knobs mean, how each policy behaved, and which
posture best fits an owner who expects a 2027 downturn and wants to minimize regret.

Mirrors ml_engine/expert.py: returns structured JSON sections so the UI renders without markdown.
Degrades gracefully if the key is missing or the call fails.
"""
import json
import requests

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_EXPERT_MODEL
from app.core.llm_cost import estimate_cost, record_usage


def _policy_line(pol):
    """One-line description of a policy incl. its knobs/allocation."""
    if pol.get("type") == "glide":
        return (f"{pol['label']} (dynamic glide: θ={pol.get('theta'):.2f}, k={pol.get('k'):.1f}, "
                f"γ={pol.get('gamma'):.2f})")
    if pol.get("type") == "static":
        return f"{pol['label']} (static {int((1 - pol.get('d', 0)) * 100)}% SPY / {int(pol.get('d', 0) * 100)}% defense)"
    return f"{pol['label']} (100% SPY, no de-risking)"


def _data_summary(comparison):
    """Render the comparison result into a compact text block for the model."""
    policies = comparison.get("policies", [])
    pol_by_id = {p["id"]: p for p in policies}
    lines = []

    glossary = comparison.get("knob_glossary", {})
    if glossary:
        lines.append("Glide-path knob definitions (use these exact meanings):")
        for key in ("theta", "k", "gamma"):
            g = glossary.get(key)
            if g:
                lines.append(f"- {g.get('symbol', key)} ({g.get('name', key)}): {g.get('desc', '')}")
        lines.append("")

    lines.append("Policies compared:")
    for p in policies:
        lines.append(f"- {_policy_line(p)} — {p.get('desc', '')}")

    lines.append("\nPer-scenario results (growth of $100,000; total return / max drawdown / Sharpe):")
    for sc in comparison.get("scenarios", []):
        lines.append(f"\n{sc.get('label', sc.get('id'))} — {sc.get('subtitle', '')}")
        lines.append(f"  Perfect-foresight ceiling: {sc.get('perfect_foresight_return'):+.0f}%")
        series = sc.get("series", {})
        # Order by total return descending for readability.
        ordered = sorted(series.items(), key=lambda kv: kv[1].get("total_return", 0), reverse=True)
        for pid, m in ordered:
            name = pol_by_id.get(pid, {}).get("label", pid)
            lines.append(
                f"  - {name}: return {m.get('total_return'):+.1f}%, "
                f"max DD {m.get('max_drawdown'):.1f}%, Sharpe {m.get('sharpe'):.2f}, "
                f"turnover {m.get('turnover'):.1f}x")
    return "\n".join(lines)


def interpret_wargame(comparison, goal=None, model=None):
    """Return {sections, model, tokens, cost?} or {error}.

    `sections` has: tldr / how_to_read / knobs_explained / policy_findings[] /
    regime_insights[] / best_for_you / caveats[].
    """
    if not OPENAI_API_KEY:
        return {"error": "Set OPENAI_API_KEY in backend/.env to enable the AI wargame analyst."}
    model = model or OPENAI_EXPERT_MODEL
    summary = _data_summary(comparison)
    goal = goal or ("The owner expects a recession or correction around 2027, has limited capital, "
                    "and wants to minimize the regret of either losing money in a crash or missing "
                    "the late-cycle upside. They want to gradually de-risk while still participating.")

    system = (
        "You are a skeptical, senior quantitative risk manager explaining a defensive-strategy "
        "wargame to a smart but non-expert investor. The wargame replays several portfolio policies "
        "across real historical bear markets (Dot-Com, GFC, COVID, 2022) and synthetic crashes. "
        "Each policy blends stocks (SPY) with defense (Treasuries/cash) either statically or via a "
        "'glide path' driven by a composite crash-risk score. Be HONEST and concrete: distinguish "
        "luck/regime-dependence from genuine edge, note that V-shaped recoveries punish over-derisking "
        "while grind-downs reward it, and that these are simplified SPY/TLT simulations. Never oversell."
    )
    user = (
        "Explain this wargame and return ONLY JSON with these fields:\n"
        '{"tldr": "<2-3 sentence plain-English bottom line>",\n'
        ' "how_to_read": "<explain what the equity-curve timelines and the metrics (return, max '
        'drawdown, Sharpe, turnover) mean, and how to read the comparison>",\n'
        ' "knobs_explained": "<plain explanation of the glide-path knobs theta, k, and gamma and how '
        'they change behavior>",\n'
        ' "policy_findings": [{"policy": "<name>", "finding": "<how it behaved across scenarios and '
        'its trade-off>"}, ...],\n'
        ' "regime_insights": ["<insight tying a behavior to a specific regime, e.g. COVID V-shape vs '
        'GFC grind vs 2022 bonds-fell-too>", ...],\n'
        ' "best_for_you": "<one honest paragraph recommending a posture given the owner goal, '
        'including roughly which knob settings or policy to lean toward and why>",\n'
        ' "caveats": ["<limitation of THIS study/simulation>", ...]}\n\n'
        f"Owner goal/context:\n{goal}\n\nWargame data:\n{summary}"
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
    try:
        r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=headers, timeout=120)
        r.raise_for_status()
        j = r.json()
        sections = json.loads(j["choices"][0]["message"]["content"])
        u = j.get("usage", {}) or {}
        ptok, ctok = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
        c = record_usage("wargame_interpret", model, ptok, ctok, provider="openai", requests=1)
        out = {"sections": sections, "model": model, "tokens": ptok + ctok,
               "input_tokens": ptok, "output_tokens": ctok}
        if c is not None:
            out["cost"] = c
        return out
    except Exception as e:
        return {"error": f"AI wargame analyst failed ({model}): {str(e)[:180]}", "model": model}
