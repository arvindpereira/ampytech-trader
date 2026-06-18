"""Quantitative fundamental-quality score (step 2b) — the deterministic backbone the LLM overlay refines.

From the latest ingested fundamentals it builds a cross-sectional 0-1 quality score per ticker, combining
profitability, growth, cash generation and balance-sheet health, with a distress override for
negative-equity / deeply-unprofitable names (e.g. BYND) so high-beta-but-strong names (NVDA, PLTR) score
high and high-beta-but-weak names score low. Scores are percentile-ranked across the universe (robust to
the wild outliers fundamentals produce, like a -28 debt/equity on negative book value).
"""
import numpy as np
import pandas as pd

from app.database import SessionLocal, TickerFundamental

# (metric, higher_is_better, weight) — debt/equity is inverted (lower leverage = better).
_COMPONENTS = [
    ("net_margin", True, 0.22),
    ("operating_margin", True, 0.12),
    ("gross_margin", True, 0.10),
    ("roe", True, 0.13),
    ("fcf_margin", True, 0.18),
    ("rev_growth_yoy", True, 0.15),
    ("debt_to_equity", False, 0.06),
    ("current_ratio", True, 0.04),
]


def _latest_metrics(periods):
    """periods: list of TickerFundamental for one ticker (any order). Returns a metrics dict for the
    latest period + YoY revenue growth (vs ~4 quarters earlier), plus distress flags."""
    ps = sorted(periods, key=lambda r: r.end_date, reverse=True)
    cur = ps[0]
    rev_growth = None
    if len(ps) >= 5 and ps[4].revenues:
        try:
            rev_growth = cur.revenues / ps[4].revenues - 1.0
        except (TypeError, ZeroDivisionError):
            rev_growth = None
    # Negative equity alone is NOT distress — it's often heavy buybacks on a profitable company
    # (DELL, ABBV). Flag distress only when negative equity coincides with losses, or on deep losses.
    unprofitable = (cur.net_margin is not None and cur.net_margin < 0)
    distressed = (cur.equity is not None and cur.equity <= 0 and unprofitable) or \
                 (cur.net_margin is not None and cur.net_margin < -0.5)
    # ROE / debt-to-equity are meaningless on negative equity — drop them when distressed.
    roe = None if (cur.equity is not None and cur.equity <= 0) else cur.roe
    de = None if (cur.equity is not None and cur.equity <= 0) else cur.debt_to_equity
    return {"net_margin": cur.net_margin, "operating_margin": cur.operating_margin,
            "gross_margin": cur.gross_margin, "roe": roe, "fcf_margin": cur.fcf_margin,
            "rev_growth_yoy": rev_growth, "debt_to_equity": de, "current_ratio": cur.current_ratio,
            "_distressed": distressed, "_end_date": cur.end_date}


def compute_quant_quality():
    """Returns {ticker: {quality, components{}, distressed, end_date}} — quality in [0,1], percentile-ranked."""
    db = SessionLocal()
    rows = db.query(TickerFundamental).all()
    db.close()
    if not rows:
        return {}
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r.ticker, []).append(r)
    metrics = {tk: _latest_metrics(ps) for tk, ps in by_ticker.items()}

    df = pd.DataFrame(metrics).T
    # Percentile-rank each component cross-sectionally (robust to outliers); invert where lower=better.
    ranks = pd.DataFrame(index=df.index)
    for name, higher_better, _w in _COMPONENTS:
        col = pd.to_numeric(df[name], errors="coerce")
        # clip extreme leverage / ratios so a single nonsense value doesn't dominate
        if name == "debt_to_equity":
            col = col.clip(0, 5)
        if name == "current_ratio":
            col = col.clip(0, 10)
        pr = col.rank(pct=True)
        ranks[name] = pr if higher_better else (1.0 - pr)

    wsum = sum(w for _, _, w in _COMPONENTS)
    score = sum(ranks[name].fillna(0.4) * w for name, _, w in _COMPONENTS) / wsum  # missing → mild 0.4
    out = {}
    for tk in df.index:
        q = float(score[tk])
        if df.loc[tk, "_distressed"]:
            q = min(q, 0.15)   # distress override: negative equity / deep losses can't score "quality"
        out[tk] = {"quality": round(q, 3), "distressed": bool(df.loc[tk, "_distressed"]),
                   "end_date": df.loc[tk, "_end_date"],
                   "components": {name: (None if pd.isna(df.loc[tk, name]) else round(float(df.loc[tk, name]), 4))
                                  for name, _, _ in _COMPONENTS}}
    return out


if __name__ == "__main__":
    q = compute_quant_quality()
    if not q:
        print("No fundamentals ingested yet — run `make fundamentals`."); raise SystemExit(1)
    ranked = sorted(q.items(), key=lambda kv: kv[1]["quality"], reverse=True)
    print(f"Quantitative fundamental quality — {len(ranked)} tickers\n")
    print(f"{'ticker':7} {'quality':>7} {'nm':>6} {'roe':>6} {'fcf_m':>6} {'rev_yoy':>7} {'d/e':>6} {'flag'}")
    print("-" * 60)
    def p(x):
        return f"{x*100:.0f}%" if isinstance(x, (int, float)) else "—"
    for tk, d in ranked:
        c = d["components"]
        de = c["debt_to_equity"]
        de_s = f"{de:.1f}" if isinstance(de, (int, float)) else "—"
        flag = "DISTRESS" if d["distressed"] else ""
        print(f"{tk:7} {d['quality']:>7.2f} {p(c['net_margin']):>6} {p(c['roe']):>6} "
              f"{p(c['fcf_margin']):>6} {p(c['rev_growth_yoy']):>7} {de_s:>6}  {flag}")
