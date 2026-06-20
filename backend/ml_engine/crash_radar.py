import sys
import os
import json
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, MacroIndicator, CrashRiskSnapshot, DailyPrice
from ml_engine.models import compute_regime_score_series

_regime_series_cache = None
_indicator_series_cache = {}
_spy_records_cache = None

def get_latest_date():
    """Finds the latest date we have macro indicators for, capped at the current date."""
    db = SessionLocal()
    today_str = datetime.now().strftime("%Y-%m-%d")
    r = db.query(MacroIndicator.date).filter(
        MacroIndicator.date <= today_str
    ).order_by(MacroIndicator.date.desc()).first()
    db.close()
    if r:
        return r[0]
    return today_str

def normalize_value(val, min_val, max_val, invert=False):
    """Clips and normalizes a value linearly to [0, 100]."""
    if val is None:
        return 50.0
    pct = (val - min_val) / (max_val - min_val) if max_val != min_val else 0.5
    pct = max(0.0, min(1.0, pct))
    if invert:
        pct = 1.0 - pct
    return pct * 100.0

def load_historical_series(db, indicator_name):
    """Loads all historical values for an indicator sorted by date, with caching."""
    global _indicator_series_cache
    if indicator_name in _indicator_series_cache:
        return _indicator_series_cache[indicator_name]
        
    records = db.query(MacroIndicator.date, MacroIndicator.value).filter(
        MacroIndicator.indicator_name == indicator_name
    ).order_by(MacroIndicator.date.asc()).all()
    if not records:
        # Empty series must still carry a DatetimeIndex, otherwise downstream
        # date comparisons (hist.index <= timestamp) raise a TypeError.
        res = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    else:
        dates = [r[0] for r in records]
        vals = [float(r[1]) for r in records]
        res = pd.Series(vals, index=pd.to_datetime(dates))
        
    _indicator_series_cache[indicator_name] = res
    return res

def get_rolling_percentile(val, history, min_len=30):
    """Computes empirical percentile of val in history, or returns 50.0 if history is too small."""
    if val is None or history.empty or len(history) < min_len:
        return None
    # Winsorize history first (1st and 99th percentiles)
    p1 = history.quantile(0.01)
    p99 = history.quantile(0.99)
    winsorized = history.clip(p1, p99)
    val_clipped = max(p1, min(p99, val))
    
    better = (winsorized <= val_clipped).sum()
    return (better / len(winsorized)) * 100.0

def compute_composite_index(as_of_date=None):
    """
    Computes the bucketed Composite Crash-Risk Index (0-100) as of a target date.
    Returns: CrashRiskSnapshot object (unsaved) and break-down dict.
    """
    if as_of_date is None:
        as_of_date = get_latest_date()
        
    db = SessionLocal()
    
    # 1. Fetch latest raw macro indicators as of or prior to as_of_date
    indicators = ["cape", "buffett_indicator", "term_spread_10y3m", "fed_funds", "yield_spread",
                  "excess_bond_premium", "ebp_recession_prob", "hy_spread", "ig_spread",
                  "nfci", "nfci_leverage", "sloos_tightening", "building_permits",
                  "initial_claims_4w", "sahm_indicator", "margin_debt_quarterly"]
                  
    raw_vals = {}
    history_map = {}
    for ind in indicators:
        hist = load_historical_series(db, ind)
        # Settle look-ahead free history (OOS) up to as_of_date
        hist_prior = hist[hist.index <= pd.to_datetime(as_of_date)]
        raw_vals[ind] = hist_prior.iloc[-1] if not hist_prior.empty else None
        history_map[ind] = hist_prior
        
    # Helper to calculate normalized/percentile value with robust static fallback
    def get_score(ind, min_val, max_val, invert=False):
        val = raw_vals.get(ind)
        # Try rolling percentile
        pct = get_rolling_percentile(val, history_map.get(ind, pd.Series()), min_len=100)
        if pct is not None:
            return 100.0 - pct if invert else pct
        # Fallback to linear normalization
        return normalize_value(val, min_val, max_val, invert=invert)

    # 2. Compute individual component scores (0 to 100, where 100 is high risk/stress)
    
    # A. Valuation
    cape_score = get_score("cape", 12.0, 38.0)
    buffett_score = get_score("buffett_indicator", 0.60, 2.00)
    b_valuation = 0.60 * cape_score + 0.40 * buffett_score
    
    # B. Monetary
    # Term spread is inverted (negative) in recessions, so invert=True (lower spread is higher risk)
    term_spread = raw_vals.get("term_spread_10y3m") or raw_vals.get("yield_spread")
    term_spread_score = normalize_value(term_spread, -1.0, 2.5, invert=True)
    fed_funds_score = get_score("fed_funds", 0.0, 5.5)
    b_monetary = 0.50 * term_spread_score + 0.50 * fed_funds_score
    
    # C. Credit
    ebp_score = get_score("excess_bond_premium", -0.5, 1.8)
    hy_score = get_score("hy_spread", 2.5, 9.0)
    b_credit = 0.60 * ebp_score + 0.40 * hy_score
    
    # D. Financial Conditions
    nfci_score = get_score("nfci", -0.9, 1.2)
    leverage_score = get_score("nfci_leverage", -0.8, 1.5)
    b_fin_conditions = 0.50 * nfci_score + 0.50 * leverage_score
    
    # E. Lending Standards
    b_lending = get_score("sloos_tightening", -10.0, 60.0)
    
    # F. Labor
    sahm_score = get_score("sahm_indicator", 0.0, 1.2)
    # Claims: higher claims is higher risk
    claims_score = get_score("initial_claims_4w", 180000, 350000)
    b_labor = 0.50 * sahm_score + 0.50 * claims_score
    
    # G. Real Activity
    # Permits YoY: lower permits is higher risk (invert=True)
    permits_val = raw_vals.get("building_permits")
    permits_score = normalize_value(permits_val, 1000, 2200, invert=True)
    b_real_activity = permits_score
    
    # H. Market Internals (Calculated from price data)
    # Fetch SPY prices
    global _spy_records_cache
    if _spy_records_cache is None:
        _spy_records_cache = db.query(DailyPrice.date, DailyPrice.close).filter(
            DailyPrice.ticker == "SPY"
        ).order_by(DailyPrice.date.asc()).all()
    spy_records = _spy_records_cache
    
    drawdown_score = 0.0
    breadth_score = 50.0
    concentration_score = 50.0 # fallback
    
    if spy_records:
        spy_df = pd.DataFrame(spy_records, columns=["date", "close"])
        spy_df["date"] = pd.to_datetime(spy_df["date"])
        spy_df = spy_df[spy_df["date"] <= pd.to_datetime(as_of_date)]
        if not spy_df.empty:
            spy_close = spy_df.iloc[-1]["close"]
            # 52-week peak
            spy_52w = spy_df[spy_df["date"] >= (pd.to_datetime(as_of_date) - timedelta(days=365))]["close"].max()
            if spy_52w > 0:
                dd = (spy_close - spy_52w) / spy_52w
                # Normal drawdown score: 0% to -20% map to 0 to 100
                drawdown_score = max(0.0, min(100.0, -dd / 0.20 * 100.0))
                
            # Breadth: % of universe tickers above 200 SMA
            # (Just count SPY above/below 200 SMA for simplicity in raw data, or do it over active tickers)
            spy_df["sma200"] = spy_df["close"].rolling(200).mean()
            if not spy_df.empty and not pd.isna(spy_df.iloc[-1]["sma200"]):
                spy_above = spy_close > spy_df.iloc[-1]["sma200"]
                breadth_score = 0.0 if spy_above else 100.0 # invert: if below 200 SMA, risk is high
                
    b_internals = 0.40 * drawdown_score + 0.30 * breadth_score + 0.30 * concentration_score
    
    # I. Election Cycle Seasonality
    as_of_dt = pd.to_datetime(as_of_date)
    year = as_of_dt.year
    # Election cycle year type: 0 = election, 1 = post-election, 2 = midterm, 3 = pre-election
    # E.g. 2024 = 0 (Presidential), 2025 = 1, 2026 = 2 (Midterm), 2027 = 3
    cycle_year_type = (year - 2024) % 4
    
    # Typically, midterm years (type 2) have seasonal weakness in Q2/Q3.
    # Pre-election years (type 3) are seasonally strong.
    # Let's assign a deterministic seasonal risk score based on month and year type.
    month = as_of_dt.month
    if cycle_year_type == 2 and month in [5, 6, 7, 8, 9, 10]:
        cycle_score = 75.0 # Midterm summer weakness
    elif cycle_year_type == 0 and month in [9, 10]:
        cycle_score = 65.0 # Election uncertainty
    else:
        cycle_score = 45.0
    b_cycle = cycle_score
    
    # J. HMM Regime Overlay
    # Smoothed, standardized HMM crisis score (0-100). Standardizing the features and
    # applying a causal EMA removes the weekly 20<->60 sawtooth the hard Viterbi label
    # produced, while keeping the regime's concurrent-stress validity (see
    # compute_regime_score_series + eval_regime_smoothing.py).
    b_hmm = 50.0 # Default if regime features unavailable
    try:
        global _regime_series_cache
        if _regime_series_cache is None:
            _regime_series_cache = compute_regime_score_series()
        regime_dict = _regime_series_cache
        if regime_dict:
            # find latest date in regime_dict <= as_of_date (point-in-time lookup)
            valid_dates = [k for k in regime_dict.keys() if k <= as_of_date]
            if valid_dates:
                b_hmm = float(regime_dict[max(valid_dates)])
    except Exception as e:
        print(f"HMM regime lookup failed: {e}")
        
    # 3. Calculate final weighted index
    bucket_scores = {
        "valuation": b_valuation,
        "monetary": b_monetary,
        "credit": b_credit,
        "financial_conditions": b_fin_conditions,
        "lending": b_lending,
        "labor": b_labor,
        "real_activity": b_real_activity,
        "internals": b_internals,
        "cycle": b_cycle,
        "hmm_regime": b_hmm
    }
    
    weights = {
        "valuation": 0.15,
        "monetary": 0.15,
        "credit": 0.15,
        "financial_conditions": 0.10,
        "lending": 0.08,
        "labor": 0.08,
        "real_activity": 0.08,
        "internals": 0.08,
        "cycle": 0.05,
        "hmm_regime": 0.08
    }
    
    composite_index = sum(bucket_scores[k] * weights[k] for k in bucket_scores)
    
    # Determine risk band
    if composite_index < 40.0:
        risk_band = "Calm"
    elif composite_index < 65.0:
        risk_band = "Elevated"
    elif composite_index < 80.0:
        risk_band = "High"
    else:
        risk_band = "Extreme"
        
    # Determine posture state machine
    current_posture = "Normal"
    trigger_reasons = []
    
    # Posture transition rules
    is_trend_up = breadth_score < 70.0 # Above 200 SMA
    is_credit_wide = b_credit > 60.0
    
    if composite_index >= 80.0:
        current_posture = "Protect"
        trigger_reasons.append("Composite Risk Index in Extreme territory.")
    elif composite_index >= 65.0:
        if not is_trend_up or is_credit_wide:
            current_posture = "De-Risk"
            trigger_reasons.append("Risk High with deteriorating price trends / widening credit spreads.")
        else:
            current_posture = "Froth"
            trigger_reasons.append("High risk index, but price trend remains positive (late-cycle expansion).")
    else:
        # Check for recovery / deploy conditions
        if drawdown_score > 75.0: # Deep drawdown
            current_posture = "Deploy"
            trigger_reasons.append("Equity market capitulation reached; initiating tranche staging.")
        elif composite_index < 60.0 and current_posture == "De-Risk":
            current_posture = "Recover"
            trigger_reasons.append("Risk levels normalizing; price trend stabilizing.")
        else:
            current_posture = "Normal"
            
    if b_valuation > 85.0:
        trigger_reasons.append(f"Valuation metrics (CAPE/Buffett) reflect extreme overvaluation ({b_valuation:.1f}).")
    if term_spread is not None and term_spread < 0:
        trigger_reasons.append(f"Yield curve is inverted (10Y-3M Term Spread: {term_spread:.2f}%).")
    if b_credit > 75.0:
        trigger_reasons.append("Credit markets indicating structural stress (widening corporate spreads / elevated EBP).")
        
    # 4. Debt Cycle read
    margin_debt_yoy = 0.0
    md_series = history_map.get("margin_debt_quarterly", pd.Series())
    md_prior = md_series[md_series.index <= pd.to_datetime(as_of_date)]
    if len(md_prior) >= 5:
        # compute YoY change (4 quarters ago)
        val_cur = md_prior.iloc[-1]
        val_prev = md_prior.iloc[-5]
        if val_prev > 0:
            margin_debt_yoy = ((val_cur - val_prev) / val_prev) * 100.0
            
    debt_gdp = 122.5 # baseline proxy
    ds_ratio = 11.0 # baseline proxy
    
    # Calculate real rate: Fed funds - core inflation proxy (breakeven inflation or 2.5%)
    fed_funds_val = raw_vals.get("fed_funds") or 5.0
    real_rate = fed_funds_val - 2.5
    
    if composite_index >= 80.0:
        dc_state = "Late Cycle / Fragile"
    elif composite_index >= 65.0:
        dc_state = "Late Cycle / Froth"
    elif composite_index >= 40.0:
        dc_state = "Mid Cycle"
    else:
        dc_state = "Early Cycle"
        
    debt_cycle_read = {
        "debt_to_gdp_pct": debt_gdp,
        "debt_service_ratio": ds_ratio,
        "real_rates": real_rate,
        "private_credit_growth_yoy": 6.5 if composite_index < 65 else 9.2,
        "margin_debt_yoy_pct": margin_debt_yoy,
        "qualitative_state": dc_state
    }
    
    # Create the CrashRiskSnapshot object
    snapshot = CrashRiskSnapshot(
        as_of_date=as_of_date,
        composite_index=float(composite_index),
        risk_band=risk_band,
        current_posture=current_posture,
        trigger_reasons=json.dumps(trigger_reasons),
        valuation_subscore=float(b_valuation),
        monetary_subscore=float(b_monetary),
        credit_subscore=float(b_credit),
        financial_conditions_subscore=float(b_fin_conditions),
        lending_subscore=float(b_lending),
        labor_subscore=float(b_labor),
        real_activity_subscore=float(b_real_activity),
        internals_subscore=float(b_internals),
        cycle_subscore=float(b_cycle),
        hmm_regime_subscore=float(b_hmm),
        debt_cycle_read=json.dumps(debt_cycle_read),
        experimental_forecast_odds=json.dumps([]), # populated by crash_model.py
        created_at=datetime.now().isoformat()
    )
    
    db.close()
    return snapshot, bucket_scores

def is_valid_snapshot(snap):
    """A genuine snapshot has all bucket sub-scores and a created_at stamp populated.
    Partial rows (e.g. seeded test fixtures) leave sub-scores/created_at as NULL and
    must never be served or cached as if they were real computations.
    """
    if snap is None:
        return False
    required = [
        snap.composite_index, snap.created_at,
        snap.valuation_subscore, snap.monetary_subscore, snap.credit_subscore,
        snap.financial_conditions_subscore, snap.lending_subscore, snap.labor_subscore,
        snap.real_activity_subscore, snap.internals_subscore, snap.cycle_subscore,
        snap.hmm_regime_subscore,
    ]
    return all(v is not None for v in required)

def persist_crash_snapshot(snapshot):
    """Saves or updates a CrashRiskSnapshot in SQLite."""
    db = SessionLocal()
    existing = db.query(CrashRiskSnapshot).filter(CrashRiskSnapshot.as_of_date == snapshot.as_of_date).first()
    if existing:
        existing.composite_index = snapshot.composite_index
        existing.risk_band = snapshot.risk_band
        existing.current_posture = snapshot.current_posture
        existing.trigger_reasons = snapshot.trigger_reasons
        existing.valuation_subscore = snapshot.valuation_subscore
        existing.monetary_subscore = snapshot.monetary_subscore
        existing.credit_subscore = snapshot.credit_subscore
        existing.financial_conditions_subscore = snapshot.financial_conditions_subscore
        existing.lending_subscore = snapshot.lending_subscore
        existing.labor_subscore = snapshot.labor_subscore
        existing.real_activity_subscore = snapshot.real_activity_subscore
        existing.internals_subscore = snapshot.internals_subscore
        existing.cycle_subscore = snapshot.cycle_subscore
        existing.hmm_regime_subscore = snapshot.hmm_regime_subscore
        existing.debt_cycle_read = snapshot.debt_cycle_read
        existing.created_at = snapshot.created_at or datetime.now().isoformat()
        db.add(existing)
    else:
        db.add(snapshot)
    db.commit()
    db.close()
    print(f"✓ Saved CrashRiskSnapshot for {snapshot.as_of_date} (Index: {snapshot.composite_index:.1f} | Stance: {snapshot.current_posture})")

def get_crash_index_timeline():
    """
    Computes/retrieves weekly composite crash index values for the past 5 years.
    Utilizes CrashRiskSnapshot database table as a persistent cache.
    """
    db = SessionLocal()
    try:
        latest_date_str = get_latest_date()
        end_date = pd.to_datetime(latest_date_str)
        start_date = end_date - pd.DateOffset(years=5)
        
        # Generate weekly dates (every Friday)
        dates_range = pd.date_range(start=start_date, end=end_date, freq="W-FRI")
        dates = [d.strftime("%Y-%m-%d") for d in dates_range]
        
        # Ensure the latest date is also included if it's not a Friday
        if latest_date_str not in dates:
            dates.append(latest_date_str)
            dates = sorted(dates)
            
        # Bulk query existing snapshots
        existing_snaps = db.query(CrashRiskSnapshot).filter(
            CrashRiskSnapshot.as_of_date.in_(dates)
        ).all()
        existing_map = {s.as_of_date: s for s in existing_snaps}
        
        timeline = []
        new_snaps = []
        
        # First check macro indicators to prevent out-of-bounds dates
        min_indicator_date = None
        for ind in ["cape", "buffett_indicator", "term_spread_10y3m", "fed_funds"]:
            hist = load_historical_series(db, ind)
            if not hist.empty:
                d_min = hist.index.min()
                if min_indicator_date is None or d_min > min_indicator_date:
                    min_indicator_date = d_min
                    
        for date_str in dates:
            # Skip dates earlier than our oldest macro data
            if min_indicator_date and pd.to_datetime(date_str) < min_indicator_date:
                continue
                
            cached = existing_map.get(date_str)
            if is_valid_snapshot(cached):
                snap = cached
            else:
                try:
                    snap, _ = compute_composite_index(date_str)
                    if cached is not None:
                        # Overwrite a corrupt/partial cached row in place.
                        persist_crash_snapshot(snap)
                    else:
                        new_snaps.append(snap)
                except Exception as e:
                    # Gracefully skip if calculation fails for some early date with insufficient history
                    print(f"Skipping timeline calculation for {date_str}: {e}")
                    continue
                    
            timeline.append({
                "date": date_str,
                "composite_index": float(snap.composite_index),
                "risk_band": snap.risk_band,
                "current_posture": snap.current_posture
            })
            
        # Bulk save any newly computed snapshots
        if new_snaps:
            db.bulk_save_objects(new_snaps)
            db.commit()
            print(f"✓ Calculated and cached {len(new_snaps)} historical crash snapshots in database.")
            
        return timeline
    finally:
        db.close()

if __name__ == "__main__":
    snap, scores = compute_composite_index()
    persist_crash_snapshot(snap)
