"""Per-account forward-walk: simulate each strategy mode's target allocation for an external
account over a recent window of real daily prices, and compare risk/return. Pure given a DB session
and the already-gathered account context (no broker/model side effects)."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.services.account_strategy import build_account_target, StrategyValidationError
from ml_engine.wargame import _metrics_from_curve

REBALANCE_DAYS = 21      # rebalance back to target ~monthly
MODES = ("growth", "de_risk", "all_weather", "barbell")
MODE_LABELS = {
    "growth": "Model growth",
    "de_risk": "De-risk (keep quality)",
    "all_weather": "All-Weather basket",
    "barbell": "Barbell basket",
}


def _price_frame(db, tickers, start, end):
    from app.database import DailyPrice
    tickers = [t for t in tickers if t]
    if not tickers:
        return pd.DataFrame()
    rows = db.query(DailyPrice.date, DailyPrice.ticker, DailyPrice.close).filter(
        DailyPrice.ticker.in_(tickers), DailyPrice.date >= start, DailyPrice.date <= end).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "ticker", "close"])
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index="date", columns="ticker", values="close").sort_index().ffill()


def _simulate(weights, cash_weight, piv, rebalance_days=REBALANCE_DAYS):
    """Equity curve (starts at 1.0) for a target mix with **partial-window entry** and **monthly
    rebalancing**:

    - Each month the book is rebalanced to the target weights, but only into names that are already
      trading on that date; a name's weight sits in cash until its first trading day, then joins at
      the next rebalance (no more whole-window cash-drag for late-listing names).
    - Between rebalances each holding drifts with its daily return; cash is held flat.

    Returns (curve, coverage) where coverage is the target weight that becomes investable at some
    point in the window."""
    if piv.empty:
        return [1.0, 1.0], 0.0
    arr = piv.values.astype(float)                       # rows = dates, cols = tickers
    col = {t: j for j, t in enumerate(piv.columns)}
    n = len(piv)
    total_w = sum(weights.values())
    wcols = {col[t]: w for t, w in weights.items() if t in col}   # target weights by column index
    holdings = {}                                        # col index -> dollar value
    cash = 0.0
    curve = []
    for i in range(n):
        if i > 0 and holdings:                           # drift holdings by today's return
            for j in list(holdings):
                p_now, p_prev = arr[i, j], arr[i - 1, j]
                if not np.isnan(p_now) and not np.isnan(p_prev) and p_prev > 0:
                    holdings[j] *= p_now / p_prev
        if i % rebalance_days == 0:                       # rebalance (also seeds the book at i=0)
            value = sum(holdings.values()) + cash
            if value <= 0:
                value = 1.0
            tradable = {j: w for j, w in wcols.items() if not np.isnan(arr[i, j]) and arr[i, j] > 0}
            invested = sum(tradable.values())
            holdings = {j: value * w for j, w in tradable.items()}
            cash = value * (cash_weight + (total_w - invested))   # explicit cash + not-yet-listed weight
        curve.append(sum(holdings.values()) + cash)
    coverage = sum(w for t, w in weights.items()
                   if t in col and not np.isnan(arr[:, col[t]]).all())
    return [float(x) for x in curve], coverage


def run_account_wargame(db, account_label, current_weights, classifications, snapshot, buckets,
                        de_risk_coefficient, aggression, lookback_years=3):
    end = datetime.now().date()
    start = end - timedelta(days=int(lookback_years * 365))

    targets = {}
    for mode in MODES:
        coef = de_risk_coefficient if mode == "de_risk" else None
        if mode == "de_risk" and coef is None:
            continue                       # no crash snapshot → skip the de-risk comparison cleanly
        try:
            t = build_account_target(current_weights, mode, aggression, buckets,
                                     snapshot=snapshot, classifications=classifications,
                                     de_risk_coefficient=coef)
            targets[mode] = (t["target_weights"], t["cash_target_weight"])
        except StrategyValidationError:
            continue

    all_tickers = set().union(*[set(w) for w, _ in targets.values()]) if targets else set()
    piv = _price_frame(db, all_tickers, start.isoformat(), end.isoformat())   # full daily

    # Simulate on full daily data (rebalancing + metrics use daily resolution), then downsample the
    # resulting curves + dates consistently across modes for a light payload.
    full_dates = list(piv.index)
    if len(full_dates) > 260:
        step = max(1, len(full_dates) // 250)
        keep = list(range(0, len(full_dates), step))
        if keep[-1] != len(full_dates) - 1:
            keep.append(len(full_dates) - 1)
    else:
        keep = list(range(len(full_dates)))
    dates = [full_dates[i].strftime("%Y-%m-%d") for i in keep]

    results = []
    for mode, (w, cash) in targets.items():
        curve, coverage = _simulate(w, cash, piv)
        results.append({
            "mode": mode, "label": MODE_LABELS.get(mode, mode),
            "metrics": _metrics_from_curve(curve),                 # daily-resolution metrics
            "curve": [round(curve[i], 5) for i in keep] if curve and keep else curve,
            "coverage": round(coverage, 3),
            "top_weights": dict(sorted({k: round(v, 4) for k, v in w.items()}.items(),
                                       key=lambda x: -x[1])[:6]),
        })
    if not results:
        return {"error": "No tradeable strategy targets could be built for this account."}
    return {"account_label": account_label, "as_of": end.isoformat(),
            "lookback_years": lookback_years, "dates": dates, "results": results,
            "rebalance_days": REBALANCE_DAYS,
            "price_coverage_note": "Monthly-rebalanced; a holding enters when it begins trading "
                                   "(weight waits in cash until then). Coverage is the target weight "
                                   "that became investable in the window."}
