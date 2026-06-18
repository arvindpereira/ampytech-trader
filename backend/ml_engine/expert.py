"""Expert interpretation of an evaluation run via a powerful OpenAI model.

Takes the structured output of `run_evaluation` (the Model Evaluation tab's backtest / walk-forward) and
asks a strong model to explain — in plain English, and *honestly* — what was tested, what the numbers
mean, and the strengths, weaknesses, and shortcomings of the study. Returns structured JSON sections so
the UI can render them without a markdown dependency.

The model is configurable (`OPENAI_EXPERT_MODEL`, default gpt-5.5). Degrades gracefully: if the key is
missing or the call fails, returns an `error` the UI can show instead of crashing the evaluation.
"""
import json
import requests

from app.core.config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_EXPERT_MODEL
from app.core.llm_cost import estimate_cost, record_usage

# Friendly names for the evaluation series keys.
_SERIES_NAMES = {
    "swing": "Swing + LLM-news", "longterm": "Long-term MPT", "blended": "Blended book",
    "spy": "S&P 500 (SPY)", "qqq": "Nasdaq 100 (QQQ)", "brk": "Berkshire (BRK-B)",
}


def _fmt_pct(x):
    return f"{x * 100:+.1f}%" if isinstance(x, (int, float)) else "n/a"


def _data_summary(result, params):
    """Render the structured evaluation result + run params into a compact text block for the model."""
    lines = []
    mode = result.get("mode", "walkforward")
    win = result.get("window") or []
    lines.append(f"Evaluation type: {'STRESS WINDOW backtest' if mode == 'stress' else 'WALK-FORWARD out-of-sample'}")
    if win:
        lines.append(f"Window: {win[0]} → {win[-1]}")
    if params.get("strategies"):
        lines.append(f"Strategies tested: {', '.join(params['strategies'])}")
    if params.get("allocation"):
        alloc = ", ".join(f"{k} {v}" for k, v in params["allocation"].items())
        lines.append(f"Blended capital allocation: {alloc}")
    detail = []
    if params.get("oos_start"):
        detail.append(f"OOS start = {params['oos_start']}")
    if params.get("splits"):
        detail.append(f"{params['splits']} expanding folds")
    if params.get("horizon"):
        detail.append(f"{params['horizon']}-day swing horizon")
    if detail:
        lines.append("Setup: " + ", ".join(detail))

    lines.append("\nResults (growth of a $100,000 book):")
    for key, m in (result.get("metrics") or {}).items():
        name = _SERIES_NAMES.get(key, key)
        lines.append(
            f"- {name}: total {_fmt_pct(m.get('total_return'))}, CAGR {_fmt_pct(m.get('cagr'))}, "
            f"Sharpe {m.get('sharpe_ratio', 0):.2f}, max drawdown {_fmt_pct(m.get('max_drawdown'))}, "
            f"final ${m.get('final_value', 0):,.0f}")

    caveats = result.get("caveats") or []
    if caveats:
        lines.append("\nKnown caveats already attached to this run:")
        lines.extend(f"- {c}" for c in caveats)
    return "\n".join(lines)


def interpret_evaluation(result, params=None, model=None):
    """Return {sections, model, tokens, cost?} or {error}. `sections` has tldr / what_was_tested /
    key_findings[] / strengths[] / weaknesses[] / shortcomings[] / verdict."""
    if not OPENAI_API_KEY:
        return {"error": "Set OPENAI_API_KEY in backend/.env to enable the expert interpretation."}
    model = model or OPENAI_EXPERT_MODEL
    params = params or {}
    summary = _data_summary(result, params)

    system = (
        "You are a skeptical, senior quantitative risk manager reviewing a personal trading bot's "
        "strategy evaluation. Explain the results to a smart but non-expert owner. Be HONEST and "
        "conservative: call out survivorship bias, small stress samples, regime dependence, and the "
        "difference between bull-market amplification and genuine all-weather edge. Compare the "
        "strategies against the buy-and-hold benchmarks. Never oversell. Prefer drawdown-aware framing."
    )
    user = (
        "Interpret this evaluation and return ONLY JSON with these string/array fields:\n"
        '{"tldr": "<2-3 sentence plain-English bottom line>",\n'
        ' "what_was_tested": "<plain explanation of the methodology and what the chart/metrics mean>",\n'
        ' "key_findings": ["<finding>", ...],\n'
        ' "strengths": ["<pro>", ...],\n'
        ' "weaknesses": ["<con>", ...],\n'
        ' "shortcomings": ["<limitation/shortcoming of the STUDY itself>", ...],\n'
        ' "verdict": "<one honest sentence: how much to trust this and what to do>"}\n\n'
        f"Evaluation data:\n{summary}"
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
        c = record_usage("eval_interpret", model, ptok, ctok)
        out = {"sections": sections, "model": model, "tokens": ptok + ctok,
               "input_tokens": ptok, "output_tokens": ctok}
        if c is not None:
            out["cost"] = c
        return out
    except Exception as e:
        return {"error": f"Expert interpretation failed ({model}): {str(e)[:180]}", "model": model}
