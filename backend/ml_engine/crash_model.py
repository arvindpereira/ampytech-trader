import sys
import os
import json
import hashlib
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.isotonic import isotonic_regression
from sklearn.metrics import roc_auc_score

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, MacroIndicator, DailyPrice, CrashRiskSnapshot
from app.core.config import DATA_STORAGE_DIR
from ml_engine.crash_radar import get_latest_date

HORIZONS = [30, 90, 180]
DRAWDOWN_LEVELS = [0.10, 0.20, 0.35]

# Macro feature series + SPY price that the drawdown-odds model depends on. A change in any of
# their newest observations is what makes a forecast refresh worthwhile (data-change trigger).
FORECAST_INPUT_INDICATORS = [
    "cape", "buffett_indicator", "term_spread_10y3m", "yield_spread",
    "excess_bond_premium", "nfci_leverage",
]
_FORECAST_STATE_PATH = os.path.join(DATA_STORAGE_DIR, "crash_forecast_state.json")

def prepare_features_df():
    """Loads and aligns daily/monthly macro and price features chronologically."""
    db = SessionLocal()

    # 1. Fetch daily SPY close prices
    spy_records = db.query(DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.ticker == "SPY"
    ).order_by(DailyPrice.date.asc()).all()
    if not spy_records:
        db.close()
        return pd.DataFrame()

    df = pd.DataFrame(spy_records, columns=["date", "spy_close"])
    df["date"] = pd.to_datetime(df["date"])

    # 2. Fetch target macro indicators
    indicators = ["cape", "buffett_indicator", "term_spread_10y3m", "yield_spread",
                  "excess_bond_premium", "nfci_leverage"]

    for ind in indicators:
        records = db.query(MacroIndicator.date, MacroIndicator.value).filter(
            MacroIndicator.indicator_name == ind
        ).order_by(MacroIndicator.date.asc()).all()
        if records:
            ind_df = pd.DataFrame(records, columns=["date", ind])
            ind_df["date"] = pd.to_datetime(ind_df["date"])
            # Merge (forward fill macro indicators since they are monthly/weekly)
            df = pd.merge_asof(df.sort_values("date"), ind_df.sort_values("date"), on="date", direction="backward")

    db.close()

    # Clean term spread
    if "term_spread_10y3m" in df.columns and "yield_spread" in df.columns:
        df["term_spread"] = df["term_spread_10y3m"].fillna(df["yield_spread"])
    elif "yield_spread" in df.columns:
        df["term_spread"] = df["yield_spread"]
    else:
        df["term_spread"] = 1.5 # default fallback

    df = df.sort_values("date").reset_index(drop=True)
    # Forward fill missing values
    df = df.ffill().dropna()
    return df

def generate_drawdown_labels(df, dd_threshold, horizon_days):
    """Generates binary labels: 1 if drawdown >= dd_threshold within forward horizon_days, else 0."""
    labels = []
    close_prices = df["spy_close"].values
    dates = df["date"].values
    n = len(df)

    for i in range(n):
        ref_price = close_prices[i]
        ref_date = dates[i]

        # Find all prices within forward horizon_days
        limit_date = ref_date + np.timedelta64(horizon_days, 'D')
        forward_window = df[(df["date"] > ref_date) & (df["date"] <= limit_date)]["spy_close"].values

        if len(forward_window) == 0:
            labels.append(np.nan) # Cannot label end of series
            continue

        min_price = np.min(forward_window)
        max_dd = (min_price - ref_price) / ref_price

        if max_dd <= -dd_threshold:
            labels.append(1)
        else:
            labels.append(0)

    return pd.Series(labels, index=df.index)

def purged_embargo_kfold_split(df, n_splits=3, purge_window=180, embargo_window=30):
    """
    Implements Purged & Embargoed Cross-Validation.
    Removes training periods that overlap with the test set or fall within the post-test embargo.
    """
    n = len(df)
    dates = df["date"].values
    indices = np.arange(n)

    # Split into K contiguous blocks
    block_size = n // n_splits
    splits = []

    for k in range(n_splits):
        test_start_idx = k * block_size
        test_end_idx = min((k + 1) * block_size, n - 1)
        test_indices = indices[test_start_idx:test_end_idx+1]

        test_start_date = dates[test_start_idx]
        test_end_date = dates[test_end_idx]

        # Determine train indices (exclude test, purged, and embargoed ranges)
        # Purge range: test_start_date - purge_window to test_end_date
        # Embargo range: test_end_date to test_end_date + embargo_window
        purge_limit = test_start_date - np.timedelta64(purge_window, 'D')
        embargo_limit = test_end_date + np.timedelta64(embargo_window, 'D')

        train_indices = [
            i for i in indices
            if (dates[i] < purge_limit) or (dates[i] > embargo_limit)
        ]

        splits.append((np.array(train_indices), np.array(test_indices)))

    return splits

def enforce_coherent_monotonicity(prob_grid, levels_asc, horizons_asc):
    """Project a (level x horizon) probability grid onto the logically-required monotone set.

    Both constraints follow from simple subset relationships and MUST hold:
      * Non-increasing in drawdown depth (fixed horizon): {DD>=20%} ⊂ {DD>=10%}, so
        P(DD>=10%) >= P(DD>=20%) >= P(DD>=35%).
      * Non-decreasing in horizon (fixed depth): the 30-day window is contained in the
        90-day window, so P(within 30d) <= P(within 90d) <= P(within 180d).

    We solve the joint 2D monotone L2 projection by alternating 1-D isotonic regressions
    (PAVA) on rows/columns until convergence (standard for matrix isotonic regression).
    `levels_asc`/`horizons_asc` must be sorted ascending and index the grid axes.
    """
    P = np.array(prob_grid, dtype=float)
    n_lev, n_hor = P.shape
    for _ in range(100):
        prev = P.copy()
        # Columns (fixed horizon): non-increasing as depth rises -> isotonic on reversed depth.
        for j in range(n_hor):
            col = P[:, j][::-1]                       # reverse so it should be non-decreasing
            P[:, j] = isotonic_regression(col, increasing=True)[::-1]
        # Rows (fixed depth): non-decreasing as horizon rises.
        for i in range(n_lev):
            P[i, :] = isotonic_regression(P[i, :], increasing=True)
        if np.max(np.abs(P - prev)) < 1e-9:
            break
    return np.clip(P, 0.0, 1.0)


def compute_forward_drawdowns(df):
    """Precompute, for every row and horizon, the forward max drawdown of SPY.

    For row i, the forward window is the set of dates in (date_i, date_i + horizon_days].
    Returns (dates_arr, {horizon: maxdd_array}) where maxdd is the most negative
    (trough - ref)/ref over that window, or NaN if no forward observations exist.
    Computed once over the full series so point-in-time slices are cheap.
    """
    dates_arr = df["date"].values.astype("datetime64[ns]")
    close = df["spy_close"].values.astype(float)
    n = len(df)
    fdd = {}
    for h in HORIZONS:
        limit = dates_arr + np.timedelta64(h, "D")
        # Last index whose date is within the forward horizon window.
        j_end = np.searchsorted(dates_arr, limit, side="right") - 1
        maxdd = np.full(n, np.nan)
        for i in range(n):
            je = int(j_end[i])
            if je > i:
                mn = close[i + 1:je + 1].min()
                maxdd[i] = (mn - close[i]) / close[i]
        fdd[h] = maxdd
    return dates_arr, fdd


def _forecast_cells_as_of(df, features, dates_arr, fdd, as_of_dt, with_cv=True):
    """Coherent forward drawdown odds for a single as-of date (no look-ahead).

    Training uses ONLY rows whose full horizon window completed on/before `as_of_dt`
    (date_i + horizon <= as_of), so every label is an outcome observable as of that date.
    The prediction is made for the latest available feature row at `as_of_dt`. The full
    (level x horizon) grid is then projected onto the logically-coherent monotone set.
    """
    A = np.datetime64(pd.to_datetime(as_of_dt), "ns")
    pred_candidates = np.where(dates_arr <= A)[0]
    if len(pred_candidates) == 0:
        return []
    pred_idx = int(pred_candidates[-1])
    X_all = df[features].values

    cells = {}
    for level in DRAWDOWN_LEVELS:
        for horizon in HORIZONS:
            horizon_end = dates_arr + np.timedelta64(horizon, "D")
            train_idx = np.where(horizon_end <= A)[0]
            # keep only rows with an observed forward window (non-NaN label)
            if len(train_idx) > 0:
                valid = ~np.isnan(fdd[horizon][train_idx])
                train_idx = train_idx[valid]
            if len(train_idx) < 100:
                cells[(level, horizon)] = {"prob": None, "base_rate": None, "n_pos": 0, "cv_auc": None}
                continue

            X = X_all[train_idx]
            y = (fdd[horizon][train_idx] <= -level).astype(int)
            base_rate = float(y.mean())
            n_pos = int(y.sum())

            cv_auc = None
            if with_cv:
                sub_df = df.iloc[train_idx].reset_index(drop=True)
                splits = purged_embargo_kfold_split(sub_df, n_splits=3, purge_window=horizon, embargo_window=30)
                aucs = []
                for tr, te in splits:
                    if len(tr) < 30 or len(te) < 10:
                        continue
                    if len(np.unique(y[tr])) < 2 or len(np.unique(y[te])) < 2:
                        continue
                    scaler = StandardScaler()
                    Xtr = scaler.fit_transform(X[tr])
                    Xte = scaler.transform(X[te])
                    clf = LogisticRegression(penalty="l2", C=1.0, random_state=42, max_iter=1000)
                    clf.fit(Xtr, y[tr])
                    aucs.append(roc_auc_score(y[te], clf.predict_proba(Xte)[:, 1]))
                cv_auc = float(np.mean(aucs)) if aucs else None

            if len(np.unique(y)) < 2:
                prob = base_rate  # degenerate (e.g. no event this deep yet observable) -> base rate
            else:
                scaler = StandardScaler()
                Xs = scaler.fit_transform(X)
                clf = LogisticRegression(penalty="l2", C=1.0, random_state=42, max_iter=1000)
                clf.fit(Xs, y)
                cx = scaler.transform(X_all[pred_idx].reshape(1, -1))
                prob = float(clf.predict_proba(cx)[0, 1])

            cells[(level, horizon)] = {"prob": prob, "base_rate": base_rate, "n_pos": n_pos, "cv_auc": cv_auc}

    # Project the grid onto the coherent (depth-decreasing, horizon-increasing) set.
    levels_asc = sorted(DRAWDOWN_LEVELS)
    horizons_asc = sorted(HORIZONS)
    grid = np.array([[cells[(lv, h)]["prob"] if cells[(lv, h)]["prob"] is not None else np.nan
                      for h in horizons_asc] for lv in levels_asc])
    if np.all(np.isnan(grid)):
        return []
    coherent = enforce_coherent_monotonicity(np.where(np.isnan(grid), 0.0, grid), levels_asc, horizons_asc)

    results = []
    for li, level in enumerate(levels_asc):
        for hi, horizon in enumerate(horizons_asc):
            c = cells[(level, horizon)]
            if c["prob"] is None:
                continue
            results.append({
                "drawdown": f">={int(level*100)}%",
                "horizon_days": horizon,
                "probability": float(coherent[li, hi]),
                "raw_probability": float(c["prob"]),
                "base_rate": c["base_rate"],
                "n_positives": c["n_pos"],
                "cv_auc": c["cv_auc"],
            })
    return results


def train_and_evaluate_forecast(as_of_date=None):
    """Computes coherent forward drawdown probabilities for a date (default: latest).

    Each (drawdown level, horizon) cell is an L2 logistic regression. We deliberately do
    NOT use class_weight="balanced": balancing reweights every model to a 50/50 prior,
    which inflates the rarer (deeper-drawdown) classes far more than common ones and
    produces incoherent odds (e.g. P(DD>=20%) > P(DD>=10%)). Plain logistic keeps the
    output calibrated to base rates; the grid is then projected onto the logically
    required monotone set so the table is always internally consistent.
    """
    print("Preparing features for drawdown-odds model...")
    df = prepare_features_df()
    if df.empty or len(df) < 200:
        print("⚠ Insufficient price or macro data to train forecasting model.")
        return []

    features = ["cape", "buffett_indicator", "term_spread", "excess_bond_premium", "nfci_leverage"]
    features = [f for f in features if f in df.columns]

    dates_arr, fdd = compute_forward_drawdowns(df)
    as_of = as_of_date or get_latest_date()
    results = _forecast_cells_as_of(df, features, dates_arr, fdd, as_of, with_cv=True)
    print(f"✓ Calculated {len(results)} coherent drawdown probability forecasts for {as_of}.")
    return results


def update_latest_forecast_odds():
    """Runs the forecasting job and updates the latest CrashRiskSnapshot."""
    results = train_and_evaluate_forecast()
    if not results:
        return

    latest_date_str = get_latest_date()
    db = SessionLocal()
    snapshot = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest_date_str).first()
    if snapshot:
        snapshot.experimental_forecast_odds = json.dumps(results)
        db.add(snapshot)
        db.commit()
        print(f"✓ Updated forecast odds for {latest_date_str} in database.")
    else:
        print(f"⚠ No snapshot found for {latest_date_str} to update forecast odds.")
    db.close()


def backfill_historical_forecast_odds(with_cv=True, only_missing=False):
    """Recompute coherent, point-in-time drawdown odds for cached snapshots.

    Each snapshot's odds are trained only on outcomes observable as of that snapshot's
    date, so the whole timeline is internally consistent and free of look-ahead. Snapshots
    too early to have >=100 fully-observed training rows are left with empty odds.

    With only_missing=True, snapshots that already carry non-empty odds are skipped — cheap
    enough to run routinely so newly-created weekly snapshots get populated without
    recomputing the entire history.
    """
    df = prepare_features_df()
    if df.empty or len(df) < 200:
        print("⚠ Insufficient price or macro data to backfill forecast odds.")
        return

    features = ["cape", "buffett_indicator", "term_spread", "excess_bond_premium", "nfci_leverage"]
    features = [f for f in features if f in df.columns]
    dates_arr, fdd = compute_forward_drawdowns(df)

    db = SessionLocal()
    try:
        snaps = db.query(CrashRiskSnapshot).order_by(CrashRiskSnapshot.as_of_date.asc()).all()
        updated = 0
        empty = 0
        skipped = 0
        for snap in snaps:
            if only_missing:
                try:
                    if json.loads(snap.experimental_forecast_odds or "[]"):
                        skipped += 1
                        continue
                except Exception:
                    pass  # treat unparseable as missing -> recompute
            results = _forecast_cells_as_of(df, features, dates_arr, fdd, snap.as_of_date, with_cv=with_cv)
            snap.experimental_forecast_odds = json.dumps(results)
            db.add(snap)
            if results:
                updated += 1
            else:
                empty += 1
        db.commit()
        suffix = f", {skipped} already populated" if only_missing else ""
        print(f"✓ Backfilled forecast odds: {updated} populated, {empty} left empty (insufficient history){suffix}.")
    finally:
        db.close()


def crash_data_fingerprint(db=None):
    """Fingerprint the newest observation of every forecast input (macro features + SPY).

    Returns a short hash that changes only when fresh data actually arrives, so the
    scheduler can trigger a forecast refresh on data updates rather than blindly on a clock.
    """
    own = db is None
    if own:
        db = SessionLocal()
    try:
        parts = []
        for name in FORECAST_INPUT_INDICATORS:
            row = db.query(MacroIndicator.date, MacroIndicator.value).filter(
                MacroIndicator.indicator_name == name
            ).order_by(MacroIndicator.date.desc()).first()
            if row:
                parts.append(f"{name}:{row[0]}:{round(float(row[1]), 6)}")
        spy = db.query(DailyPrice.date).filter(DailyPrice.ticker == "SPY").order_by(DailyPrice.date.desc()).first()
        if spy:
            parts.append(f"SPY:{spy[0]}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()
    finally:
        if own:
            db.close()


def _read_forecast_state():
    try:
        with open(_FORECAST_STATE_PATH) as f:
            return json.load(f).get("fingerprint")
    except Exception:
        return None


def _write_forecast_state(fingerprint):
    try:
        with open(_FORECAST_STATE_PATH, "w") as f:
            json.dump({"fingerprint": fingerprint, "updated_at": datetime.now().isoformat()}, f)
    except Exception as e:
        print(f"⚠ Could not persist crash forecast state: {e}")


def refresh_crash_forecast(force=False):
    """Data-change-triggered refresh of the crash radar's latest snapshot + coherent odds.

    Skips entirely when no forecast input has changed since the last run (unless force=True).
    Ensures the latest composite snapshot exists, recomputes its coherent drawdown odds, and
    fills odds for any other snapshots still missing them. Returns True if it ran.
    """
    from ml_engine.crash_radar import (
        compute_composite_index, persist_crash_snapshot, is_valid_snapshot,
    )

    db = SessionLocal()
    try:
        fp = crash_data_fingerprint(db)
        latest = get_latest_date()
        snap = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == latest).first()
        snap_ok = is_valid_snapshot(snap)
    finally:
        db.close()

    if not force and snap_ok and fp == _read_forecast_state():
        print("Crash forecast inputs unchanged since last run — skipping refresh.")
        return False

    # 1. Make sure the latest composite snapshot exists / is valid before attaching odds.
    if not snap_ok:
        fresh, _ = compute_composite_index(latest)
        persist_crash_snapshot(fresh)

    # 2. Recompute the latest snapshot's coherent odds + fill any other missing snapshots.
    update_latest_forecast_odds()
    backfill_historical_forecast_odds(with_cv=True, only_missing=True)

    _write_forecast_state(fp)
    print("✓ Crash forecast refresh complete.")
    return True


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Drawdown-odds forecasting")
    parser.add_argument("--backfill", action="store_true", help="Backfill coherent odds for all cached snapshots")
    parser.add_argument("--missing-only", action="store_true", help="With --backfill, only fill snapshots lacking odds")
    parser.add_argument("--refresh", action="store_true", help="Data-change-triggered refresh (skips if inputs unchanged)")
    parser.add_argument("--force", action="store_true", help="With --refresh, run even if inputs are unchanged")
    parser.add_argument("--no-cv", action="store_true", help="Skip cross-validated AUC (faster backfill)")
    args = parser.parse_args()
    if args.refresh:
        refresh_crash_forecast(force=args.force)
    elif args.backfill:
        backfill_historical_forecast_odds(with_cv=not args.no_cv, only_missing=args.missing_only)
    else:
        update_latest_forecast_odds()
