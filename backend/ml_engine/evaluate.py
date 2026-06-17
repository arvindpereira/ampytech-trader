"""Strategy evaluation orchestrator for the UI's model-investigation tab.

Runs look-ahead-free (walk-forward / expanding-window) backtests of the swing and long-term strategies,
builds matching benchmark curves (SPY, QQQ, BRK-B), optionally blends the strategies by the user's
current capital allocation, and returns aligned equity curves + metrics for plotting. All series are
normalized to start at $100k on the same date so returns are directly comparable.
"""
import os
import sys
import pandas as pd
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, DailyPrice

BENCHMARKS = [("SPY", "spy"), ("QQQ", "qqq"), ("BRK-B", "brk")]


def _curve_to_daily(curve):
    """list of {date, portfolio_value} -> pandas Series indexed by 'YYYY-MM-DD' (last value per day)."""
    if not curve:
        return pd.Series(dtype=float)
    df = pd.DataFrame(curve)
    df["day"] = df["date"].astype(str).str.slice(0, 10)
    return df.groupby("day")["portfolio_value"].last()


def _metrics(series):
    """total return, CAGR, Sharpe, maxDD, final value for a date-indexed $ series."""
    s = series.dropna()
    if len(s) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0, "final_value": float(s.iloc[-1]) if len(s) else 0.0}
    total = s.iloc[-1] / s.iloc[0] - 1.0
    days = (pd.to_datetime(s.index[-1]) - pd.to_datetime(s.index[0])).days or 1
    years = days / 365.25
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    rets = s.pct_change().dropna()
    sharpe = (rets.mean() / (rets.std() + 1e-9)) * np.sqrt(252) if len(rets) > 2 else 0.0
    dd = ((s - s.cummax()) / s.cummax()).min()
    return {"total_return": float(total), "cagr": float(cagr), "sharpe_ratio": float(sharpe),
            "max_drawdown": float(dd), "final_value": float(s.iloc[-1])}


def _benchmark_series(db, ticker, dates):
    """Daily closes for `ticker` over the date window, normalized to $100k at the first date."""
    lo, hi = dates[0], dates[-1]
    rows = db.query(DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.ticker == ticker, DailyPrice.date >= lo, DailyPrice.date <= hi).all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series({d[:10]: c for d, c in rows if c}).sort_index()
    s = s.reindex(dates).ffill().bfill()
    if s.iloc[0]:
        s = s / s.iloc[0] * 100000.0
    return s


def run_evaluation(strategies, horizon=5, splits=4, allocation=None, progress_cb=None):
    """Returns {series: [{date, swing, longterm, blended, spy, qqq, brk}], metrics: {...}, window: [...]}."""
    def report(p, note):
        if progress_cb:
            progress_cb(int(p), note)

    raw = {}   # name -> daily $ series
    metrics = {}

    if "swing" in strategies:
        report(5, "Swing walk-forward (training folds)…")
        from ml_engine.swing_alpha import backtest_swing_curve
        curve, _ = backtest_swing_curve(horizon=horizon, n_splits=splits,
                                        progress_cb=lambda f: report(5 + int(f * 45), "Swing walk-forward (training folds)…"))
        if curve:
            raw["swing"] = _curve_to_daily(curve)

    # The reference window is the swing OOS window when available (else the long-term default).
    ref_start = raw["swing"].index[0] if "swing" in raw and len(raw["swing"]) else "2023-06-16"

    if "longterm" in strategies:
        report(55, "Long-term MPT backtest…")
        from ml_engine.longterm_alpha import backtest_longterm_curve
        lt_allowed = allocation.get("longterm_tickers") if allocation else None
        curve, _ = backtest_longterm_curve(start_date=ref_start, allowed_tickers=lt_allowed,
                                           progress_cb=lambda f: report(55 + int(f * 25), "Long-term MPT backtest…"))
        if curve:
            raw["longterm"] = _curve_to_daily(curve)

    if not raw:
        report(100, "No data")
        return {"series": [], "metrics": {}, "window": []}

    # Common daily date index across all strategy curves, forward-filled.
    all_dates = sorted(set().union(*[set(s.index) for s in raw.values()]))
    for k in list(raw.keys()):
        raw[k] = raw[k].reindex(all_dates).ffill()
        # renormalize each strategy to $100k at the window start for fair comparison
        first = raw[k].dropna().iloc[0] if len(raw[k].dropna()) else 0
        if first:
            raw[k] = raw[k] / first * 100000.0

    # Blend by the current capital allocation (cash earns 0%).
    if allocation and "swing" in raw:
        sw = allocation.get("swing", 0.0)
        lt = allocation.get("longterm", 0.0)
        swing_ret = raw["swing"].pct_change().fillna(0.0)
        lt_ret = raw["longterm"].pct_change().fillna(0.0) if "longterm" in raw else pd.Series(0.0, index=all_dates)
        blended_ret = sw * swing_ret + lt * lt_ret  # remainder is cash @ 0%
        raw["blended"] = (1.0 + blended_ret).cumprod() * 100000.0

    report(85, "Loading benchmarks (SPY / QQQ / BRK-B)…")
    db = SessionLocal()
    try:
        for sym, key in BENCHMARKS:
            raw[key] = _benchmark_series(db, sym, all_dates)
    finally:
        db.close()

    for k, s in raw.items():
        metrics[k] = _metrics(s)

    report(95, "Assembling curves…")
    rows = []
    for d in all_dates:
        row = {"date": d}
        for k, s in raw.items():
            v = s.get(d)
            row[k] = round(float(v), 2) if (v is not None and pd.notna(v)) else None
        rows.append(row)

    report(100, "Complete")
    return {"series": rows, "metrics": metrics, "window": [all_dates[0], all_dates[-1]]}
