"""Walk-forward calibration of research factor weights vs forward returns.

Optimizes STOCK_FACTOR_WEIGHTS to maximize Spearman rank correlation between
composite score and 63-trading-day forward return on historical company_snapshots.

Usage:
    python -m ml_engine.factor_calibrator
    python -m ml_engine.factor_calibrator --folds 4 --horizon 63
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from itertools import product
from typing import Dict, List, Optional, Tuple

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import CompanySnapshot, DailyPrice, SessionLocal, init_db
from ml_engine.research_framework import (
    METHODOLOGY_VERSION,
    STOCK_FACTOR_WEIGHTS,
    composite_stock_score,
    stock_component_scores,
)

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_WEIGHTS_FILE = os.path.join(_DATA_DIR, "research_factor_weights.json")

# Coarse grid on simplex (quality, upside, news); momentum = 1 - sum
_WEIGHT_GRID = [0.15, 0.20, 0.25, 0.30, 0.35]


def _weights_path() -> str:
    return _WEIGHTS_FILE


def load_calibrated_weights() -> Dict[str, float]:
    """Load calibrated weights if present, else defaults."""
    if not os.path.exists(_WEIGHTS_FILE):
        return dict(STOCK_FACTOR_WEIGHTS)
    try:
        data = json.load(open(_WEIGHTS_FILE))
        w = data.get("weights")
        if isinstance(w, dict) and abs(sum(w.values()) - 1.0) < 0.02:
            return {k: float(w[k]) for k in STOCK_FACTOR_WEIGHTS if k in w}
    except Exception:
        pass
    return dict(STOCK_FACTOR_WEIGHTS)


def _forward_return(db, ticker: str, as_of: str, horizon: int) -> Optional[float]:
    rows = (
        db.query(DailyPrice.date, DailyPrice.close)
        .filter(DailyPrice.ticker == ticker.upper(), DailyPrice.date >= as_of)
        .order_by(DailyPrice.date.asc())
        .limit(horizon + 5)
        .all()
    )
    if len(rows) < 2:
        return None
    start = float(rows[0][1])
    idx = min(len(rows) - 1, horizon)
    end = float(rows[idx][1])
    if not start:
        return None
    return (end - start) / start


def _spearman(x: pd.Series, y: pd.Series) -> float:
    if len(x) < 5:
        return 0.0
    return float(x.rank().corr(y.rank()))


def build_panel(db, horizon: int = 63, min_tickers: int = 8) -> pd.DataFrame:
    """Panel of (as_of_date, ticker, factors..., forward_return)."""
    rows = db.query(CompanySnapshot).order_by(CompanySnapshot.as_of_date.asc()).all()
    if not rows:
        return pd.DataFrame()

    by_date: Dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r.as_of_date, []).append(r)

    records = []
    for as_of, snaps in sorted(by_date.items()):
        if len(snaps) < min_tickers:
            continue
        for s in snaps:
            if not s.facts_json:
                continue
            try:
                facts = json.loads(s.facts_json)
            except Exception:
                continue
            fwd = _forward_return(db, s.ticker, as_of, horizon)
            if fwd is None:
                continue
            comp = stock_component_scores(facts)
            records.append({
                "as_of_date": as_of,
                "ticker": s.ticker,
                "forward_return": fwd,
                **comp,
            })
    return pd.DataFrame(records)


def _score_panel(df: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    return (
        weights["quality"] * df["quality"]
        + weights["upside"] * df["upside"]
        + weights["news"] * df["news"]
        + weights["momentum"] * df["momentum"]
    )


def walk_forward_calibrate(
    df: pd.DataFrame,
    folds: int = 4,
) -> Tuple[Dict[str, float], Dict]:
    """Expanding-window walk-forward grid search on weight simplex."""
    dates = sorted(df["as_of_date"].unique())
    if len(dates) < folds + 2:
        folds = max(1, len(dates) - 2)
    fold_size = max(1, len(dates) // (folds + 1))

    best_weights = dict(STOCK_FACTOR_WEIGHTS)
    best_mean_ic = -1.0
    fold_results = []

    candidates = []
    for q, u, n in product(_WEIGHT_GRID, _WEIGHT_GRID, _WEIGHT_GRID):
        m = round(1.0 - q - u - n, 2)
        if m < 0.10 or m > 0.40:
            continue
        candidates.append({"quality": q, "upside": u, "news": n, "momentum": m})

    for f in range(folds):
        test_start = (f + 1) * fold_size
        test_end = (f + 2) * fold_size if f < folds - 1 else len(dates)
        if test_start >= len(dates):
            break
        train_dates = set(dates[:test_start])
        test_dates = set(dates[test_start:test_end])
        if not test_dates:
            continue

        train_df = df[df["as_of_date"].isin(train_dates)]
        fold_best = dict(STOCK_FACTOR_WEIGHTS)
        fold_best_ic = -1.0
        for w in candidates:
            scores = _score_panel(train_df, w)
            ic = _spearman(scores, train_df["forward_return"])
            if ic > fold_best_ic:
                fold_best_ic = ic
                fold_best = w

        test_df = df[df["as_of_date"].isin(test_dates)]
        if test_df.empty:
            continue
        oos_ic = _spearman(_score_panel(test_df, fold_best), test_df["forward_return"])
        fold_results.append({"fold": f, "train_ic": round(fold_best_ic, 4), "oos_ic": round(oos_ic, 4), "weights": fold_best})
        if oos_ic > best_mean_ic:
            best_mean_ic = oos_ic
            best_weights = fold_best

    meta = {
        "folds": fold_results,
        "best_oos_spearman": round(best_mean_ic, 4),
        "panel_rows": len(df),
        "panel_dates": len(dates),
        "horizon_days": int(df.attrs.get("horizon", 63)) if hasattr(df, "attrs") else 63,
    }
    return best_weights, meta


def run_calibration(horizon: int = 63, folds: int = 4, write: bool = True) -> Dict:
    init_db()
    db = SessionLocal()
    try:
        df = build_panel(db, horizon=horizon)
        if len(df) < 30:
            return {
                "status": "insufficient_data",
                "message": f"Need more snapshot history (got {len(df)} rows). Run make research-kb-refresh daily.",
                "weights": dict(STOCK_FACTOR_WEIGHTS),
            }
        df.attrs["horizon"] = horizon
        weights, meta = walk_forward_calibrate(df, folds=folds)
        payload = {
            "status": "ok",
            "calibrated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "methodology_version": METHODOLOGY_VERSION,
            "horizon_days": horizon,
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "default_weights": dict(STOCK_FACTOR_WEIGHTS),
            **meta,
        }
        if write:
            os.makedirs(_DATA_DIR, exist_ok=True)
            with open(_WEIGHTS_FILE, "w") as f:
                json.dump(payload, f, indent=2)
        return payload
    finally:
        db.close()


def main():
    p = argparse.ArgumentParser(description="Calibrate research factor weights")
    p.add_argument("--horizon", type=int, default=63, help="Forward return horizon in trading days")
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args()
    result = run_calibration(horizon=args.horizon, folds=args.folds, write=not args.no_write)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
