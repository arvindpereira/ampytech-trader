"""LLM fundamental-quality overlay (step 2c) — the qualitative layer over the quant composite.

For each ticker it feeds the recent financial metrics + recent news headlines to an LLM and asks for a
fundamental-quality read aimed at a buy-the-dip-and-hold strategy: durable moat, growth durability,
profitability QUALITY (flagging one-off/non-recurring items that distort ratios, e.g. RGTI), balance-sheet
health, competitive position, and management/product trajectory — plus sector awareness (banks' FCF/ROE
don't map normally). This corrects the quant composite's known blind spots. Returns a 0-1 quality score,
flags, and a short verdict; cost is tracked via the usage ledger.
"""
import sys
import os
import json
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, TickerFundamental, NewsLLMScore, TickerClassification
from app.core.config import (OLLAMA_URL, LLM_MODEL, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL)
from app.core.llm_cost import record_usage
from ml_engine.fundamental_quality import compute_quant_quality, _latest_metrics


def _resolve():
    if OPENAI_API_KEY:
        return "openai", OPENAI_MODEL
    return "ollama", LLM_MODEL


def _recent_news(db, ticker, limit=12):
    rows = (db.query(NewsLLMScore.title).filter(NewsLLMScore.ticker == ticker)
            .order_by(NewsLLMScore.published_utc.desc()).limit(limit).all())
    seen, out = set(), []
    for (t,) in rows:
        if t and t not in seen:
            seen.add(t); out.append(t)
    return out


def _metric_lines(periods):
    m = _latest_metrics(periods)
    def p(x):
        return f"{x*100:.0f}%" if isinstance(x, (int, float)) else "n/a"
    return (f"net margin {p(m['net_margin'])}, operating margin {p(m['operating_margin'])}, "
            f"gross margin {p(m['gross_margin'])}, ROE {p(m['roe'])}, FCF margin {p(m['fcf_margin'])}, "
            f"revenue YoY {p(m['rev_growth_yoy'])}, "
            f"debt/equity {m['debt_to_equity'] if m['debt_to_equity'] is not None else 'n/a'}"
            + (", flagged distressed (negative equity with losses / deep losses)" if m['_distressed'] else ""))


def _prompt(ticker, metric_line, news, quant_q):
    nl = "\n".join(f"- {h[:140]}" for h in news) or "(no recent headlines)"
    return (
        f"You are a fundamental equity analyst rating {ticker} for a BUY-THE-DIP-AND-HOLD-LONG-TERM "
        "strategy: would a sharp price drop be a chance to accumulate a durable compounder, or a value "
        "trap to avoid?\n"
        "Assess: durable moat, growth durability, PROFITABILITY QUALITY (flag one-off / non-recurring "
        "items distorting the ratios), balance-sheet health, competitive position, management/product "
        "trajectory. Be sector-aware (for banks/financials, FCF and ROE ratios don't map normally).\n"
        f"Recent financial metrics: {metric_line}\n"
        f"A naive ratio-only model scored its quality {quant_q:.2f} (0-1) — agree or correct it.\n"
        f"Recent news headlines:\n{nl}\n\n"
        'Return ONLY JSON: {"quality": <0-1 float>, "moat": "low|medium|high", '
        '"growth": "declining|stable|growing", "balance_sheet": "weak|ok|strong", '
        '"flags": ["one_off_gain"|"bank"|"turnaround"|"speculative"|"distress"|"secular_decline"...], '
        '"verdict": "<=25 words"}. quality: 1 = best-in-class compounder to accumulate on dips, '
        "0 = distressed/speculative value-trap.")


def _call(prompt, provider, model):
    if provider == "openai":
        body = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "temperature": 0, "response_format": {"type": "json_object"}}
        h = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        r = requests.post(f"{OPENAI_BASE_URL}/chat/completions", json=body, headers=h, timeout=120)
        r.raise_for_status()
        j = r.json()
        u = j.get("usage", {}) or {}
        return json.loads(j["choices"][0]["message"]["content"]), u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    r = requests.post(f"{OLLAMA_URL}/api/generate",
                      json={"model": model, "prompt": "/no_think\n" + prompt, "stream": False,
                            "format": "json", "options": {"temperature": 0, "num_predict": 400}}, timeout=180)
    resp = r.json()
    return json.loads(resp.get("response", "") or "{}"), resp.get("prompt_eval_count", 0) or 0, resp.get("eval_count", 0) or 0


def assess_all(tickers=None, progress_cb=None):
    """LLM-assess fundamental quality for tickers with fundamentals; store onto ticker_classification."""
    provider, model = _resolve()
    quant = compute_quant_quality()
    db = SessionLocal()
    try:
        rows = db.query(TickerFundamental).all()
        by_ticker = {}
        for r in rows:
            by_ticker.setdefault(r.ticker, []).append(r)
        targets = [t for t in (tickers or by_ticker.keys()) if t in by_ticker]
        from datetime import datetime
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        done = 0
        for tk in targets:
            metric_line = _metric_lines(by_ticker[tk])
            news = _recent_news(db, tk)
            qq = quant.get(tk, {}).get("quality", 0.4)
            try:
                res, ptok, ctok = _call(_prompt(tk, metric_line, news, qq), provider, model)
            except Exception as e:
                print(f"  {tk}: LLM assess failed: {str(e)[:90]}")
                continue
            record_usage("fundamental_quality", model, ptok, ctok, provider=provider, requests=1)
            try:
                llm_q = max(0.0, min(1.0, float(res.get("quality", qq))))
            except Exception:
                llm_q = qq
            flags = res.get("flags", [])
            vals = {"ticker": tk, "quant_quality": qq, "llm_quality": round(llm_q, 3),
                    "distressed": quant.get(tk, {}).get("distressed", False),
                    "llm_flags": json.dumps(flags)[:400], "llm_verdict": str(res.get("verdict", ""))[:300],
                    "llm_model": model, "updated_at": datetime.now().isoformat(timespec="seconds")}
            stmt = sqlite_insert(TickerClassification).values(**vals)
            stmt = stmt.on_conflict_do_update(index_elements=["ticker"], set_={k: v for k, v in vals.items() if k != "ticker"})
            db.execute(stmt); db.commit()
            done += 1
            print(f"  {tk:6} quant {qq:.2f} → LLM {llm_q:.2f}  moat={res.get('moat','?')} "
                  f"flags={flags} :: {str(res.get('verdict',''))[:70]}")
            if progress_cb:
                progress_cb(done / len(targets), f"{tk}: {llm_q:.2f}")
        print(f"✅ LLM fundamental-quality overlay complete: {done} tickers.")
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="LLM fundamental-quality overlay")
    p.add_argument("--tickers", default=None, help="comma-separated; default = all with fundamentals")
    a = p.parse_args()
    assess_all(tickers=a.tickers.split(",") if a.tickers else None)
