"""Per-account forward-walk: simulate each strategy mode's target allocation for an external
account over a recent window of real daily prices, and compare risk/return. Pure given a DB session
and the already-gathered account context (no broker/model side effects)."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.services.account_strategy import build_account_target, StrategyValidationError
from ml_engine.wargame import _metrics_from_curve

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


def _simulate(weights, cash_weight, piv):
    """Buy-and-hold weighted equity curve (starts at 1.0) over piv's date index. A target ticker with
    no price at the window start is dropped and its weight folded into cash. Returns (curve, used_w)."""
    if piv.empty:
        return [1.0, 1.0], 0.0
    first = piv.iloc[0]
    used = {t: w for t, w in weights.items()
            if t in piv.columns and pd.notna(first.get(t)) and float(first.get(t)) > 0}
    used_w = sum(used.values())
    cash = cash_weight + (sum(weights.values()) - used_w)   # dropped (no-history) weight → cash
    curve = np.full(len(piv), float(cash))
    for t, w in used.items():
        curve = curve + w * (piv[t] / float(piv[t].iloc[0])).values
    return [float(x) for x in curve], used_w


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
    piv = _price_frame(db, all_tickers, start.isoformat(), end.isoformat())

    # Downsample to keep the payload light (~weekly), preserving the last point.
    if not piv.empty and len(piv) > 260:
        step = max(1, len(piv) // 250)
        piv = pd.concat([piv.iloc[::step], piv.iloc[[-1]]]).drop_duplicates()

    dates = [d.strftime("%Y-%m-%d") for d in piv.index] if not piv.empty else []
    results = []
    for mode, (w, cash) in targets.items():
        curve, used_w = _simulate(w, cash, piv)
        results.append({
            "mode": mode, "label": MODE_LABELS.get(mode, mode),
            "metrics": _metrics_from_curve(curve), "curve": curve,
            "coverage": round(used_w, 3),
            "top_weights": dict(sorted({k: round(v, 4) for k, v in w.items()}.items(),
                                       key=lambda x: -x[1])[:6]),
        })
    if not results:
        return {"error": "No tradeable strategy targets could be built for this account."}
    return {"account_label": account_label, "as_of": end.isoformat(),
            "lookback_years": lookback_years, "dates": dates, "results": results,
            "price_coverage_note": "Holdings without daily-price history for the full window are "
                                   "excluded and counted as cash (see per-strategy coverage)."}
