import sys
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, DailyPrice, CrisisPrice, MacroIndicator, CrashRiskSnapshot
from app.core.config import DATA_STORAGE_DIR
from ml_engine.glide import compute_defensive_coefficient, PRESETS
from ml_engine.crash_radar import compute_composite_index

# Global memory cache for pre-computed risk indices to keep sweeps fast
_risk_index_cache = {}

# On-disk cache for the (expensive) scenario comparison + AI analyst interpretation so the last
# run is shown by default and the OpenAI analyst is not re-billed on every page load.
WARGAME_CACHE_PATH = os.path.join(DATA_STORAGE_DIR, "wargame_cache.json")


def load_wargame_cache():
    """Returns the persisted wargame cache dict (comparison, analyst, timestamps, fingerprints)."""
    try:
        with open(WARGAME_CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_wargame_cache(data):
    try:
        with open(WARGAME_CACHE_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"⚠ Could not persist wargame cache: {e}")


def save_wargame_comparison(comparison, fingerprint=None):
    """Persist the latest scenario comparison so it renders by default on the next page load."""
    cache = load_wargame_cache()
    cache["comparison"] = comparison
    cache["comparison_generated_at"] = datetime.now().isoformat()
    cache["fingerprint"] = fingerprint
    _save_wargame_cache(cache)


def save_wargame_analyst(analyst, fingerprint=None):
    """Persist the AI analyst interpretation alongside the comparison it was generated for."""
    cache = load_wargame_cache()
    cache["analyst"] = analyst
    cache["analyst_generated_at"] = datetime.now().isoformat()
    cache["analyst_fingerprint"] = fingerprint
    _save_wargame_cache(cache)

def get_historical_era_dates(era):
    """Returns the start and end dates for historical eras."""
    eras = {
        "dotcom": ("1999-03-10", "2002-10-31"), # QQQ start to bottom
        "gfc": ("2007-10-09", "2009-03-09"),    # SPY peak to bottom
        "covid": ("2020-02-19", "2020-04-30"),  # peak to recovery stabilization
        "2022": ("2022-01-03", "2022-10-12")    # peak to bottom
    }
    return eras.get(era, ("2020-01-01", "2020-12-31"))

def load_era_prices(era):
    """
    Loads price dataframe for SPY and representative safe asset (TLT) for an era.
    If TLT is not available, falls back to BIL or cash proxy.
    """
    db = SessionLocal()
    start_d, end_d = get_historical_era_dates(era)
    
    # We query CrisisPrice for historical eras, or DailyPrice for 2022
    if era in ["dotcom", "gfc", "covid"]:
        prices = db.query(CrisisPrice.date, CrisisPrice.ticker, CrisisPrice.close).filter(
            CrisisPrice.era == era,
            CrisisPrice.ticker.in_(["SPY", "QQQ", "TLT", "XLK", "XLF"])
        ).order_by(CrisisPrice.date.asc()).all()
    else: # 2022 and recent
        prices = db.query(DailyPrice.date, DailyPrice.ticker, DailyPrice.close).filter(
            DailyPrice.date >= start_d,
            DailyPrice.date <= end_d,
            DailyPrice.ticker.in_(["SPY", "QQQ", "TLT", "XLK", "XLF"])
        ).order_by(DailyPrice.date.asc()).all()
        
    db.close()
    
    if not prices:
        # Generate dummy prices if database is empty
        dates = pd.date_range(start=start_d, end=end_d, freq="B")
        df = pd.DataFrame({
            "SPY": [100.0 * (0.999**i) for i in range(len(dates))], # slow bleed
            "TLT": [100.0 * (1.0005**i) for i in range(len(dates))]
        }, index=dates)
        df.index.name = "date"
        return df.reset_index()
        
    # Pivot to have tickers as columns
    df = pd.DataFrame(prices, columns=["date", "ticker", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df_pivot = df.pivot(index="date", columns="ticker", values="close")
    
    # Handle missing safe assets
    if "SPY" not in df_pivot.columns:
        if "QQQ" in df_pivot.columns:
            df_pivot["SPY"] = df_pivot["QQQ"]
        else:
            df_pivot["SPY"] = 100.0
            
    if "TLT" not in df_pivot.columns:
        # Default proxy for bond returns: 2% annualized steady yield
        df_pivot["TLT"] = 100.0 * np.exp(0.02 * np.arange(len(df_pivot)) / 252)
        
    df_pivot = df_pivot.ffill().bfill()
    return df_pivot.reset_index()

def precompute_risk_indices(era, dates_list):
    """Computes and caches daily composite risk indices for the given dates."""
    global _risk_index_cache
    
    cache_key = f"{era}_{dates_list[0]}_{dates_list[-1]}"
    if cache_key in _risk_index_cache:
        return _risk_index_cache[cache_key]
        
    print(f"Pre-computing Composite Crash-Risk Index for {era} ({len(dates_list)} dates)...")
    indices = []
    
    # Load all indicators once in memory to make it faster
    db = SessionLocal()
    # We can fetch daily/weekly values of key indicator "nfci_leverage" to proxy day-to-day index movements
    # rather than running the full heavy compute_composite_index which queries 16 indicators per day.
    # To keep it extremely fast and correct, we sample every 5th day and interpolate, or compute once.
    # Let's do a fast approximation: we fetch the full index for a few anchor dates and interpolate,
    # or compute directly with carry-forward.
    
    # For wargaming accuracy, we compute the real index at weekly intervals and forward fill,
    # which is how the macro portfolio rebalances in real life anyway!
    weekly_dates = dates_list[::5] # Every 5th trading day
    if dates_list[-1] not in weekly_dates:
        weekly_dates.append(dates_list[-1])
        
    weekly_vals = {}
    for dt in weekly_dates:
        try:
            # Format datetime to string
            dt_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
            snap, _ = compute_composite_index(dt_str)
            weekly_vals[dt_str] = snap.composite_index
        except Exception as e:
            weekly_vals[str(dt)[:10]] = 50.0 # fallback
            
    db.close()
    
    # Interpolate daily values
    series = pd.Series(index=pd.to_datetime(dates_list), dtype=float)
    for k, v in weekly_vals.items():
        series[pd.to_datetime(k)] = v
    series = series.interpolate().ffill().bfill()

    # Key by "YYYY-MM-DD" strings so the per-day lookups in the simulator (which format dates to
    # strings) actually hit. A Timestamp-keyed dict here silently fell back to 50.0 for every day,
    # which made glide policies look inert over historical eras.
    result = {(k.strftime("%Y-%m-%d") if hasattr(k, "strftime") else str(k)[:10]): float(v)
              for k, v in series.items()}
    _risk_index_cache[cache_key] = result
    return result

def generate_bootstrap_scenario(spy_returns, target_dd=0.30, horizon=252, recovery="V", inflation="deflation"):
    """
    Generates a synthetic drawdown scenario using block bootstrapping.
    - target_dd: target maximum drawdown (e.g. 0.30 for -30%)
    - recovery: 'V' (rapid), 'U' (delayed), 'L' (no recovery)
    - inflation: 'deflation' (bonds hedge stocks), 'stagflation' (bonds fall with stocks)
    """
    block_size = 10
    n_blocks = horizon // block_size + 1
    
    # Sample random blocks from spy_returns
    sampled_returns = []
    for _ in range(n_blocks):
        idx = np.random.randint(0, len(spy_returns) - block_size)
        sampled_returns.extend(spy_returns[idx:idx+block_size])
        
    sampled_returns = np.array(sampled_returns[:horizon])
    
    # We construct a synthetic crash centered around the first half of the horizon
    crash_duration = horizon // 3
    bottom_duration = horizon // 6
    recovery_duration = horizon - crash_duration - bottom_duration
    
    # 1. Adjust returns to match target drawdown during crash window
    # To achieve drawdown D, daily log return during crash should average:
    crash_drift = np.log(1.0 - target_dd) / crash_duration
    sampled_returns[:crash_duration] += (crash_drift - np.mean(sampled_returns[:crash_duration]))
    
    # 2. Handle recovery
    if recovery == "V":
        # Rapid recovery back to baseline
        rec_drift = -np.log(1.0 - target_dd) / recovery_duration
        sampled_returns[crash_duration + bottom_duration:] += (rec_drift - np.mean(sampled_returns[crash_duration + bottom_duration:]))
    elif recovery == "U":
        # Flat bottom, then slower recovery
        sampled_returns[crash_duration:crash_duration+bottom_duration] = np.random.normal(0.0, 0.005, bottom_duration)
        rec_drift = -np.log(1.0 - target_dd) / recovery_duration
        sampled_returns[crash_duration + bottom_duration:] += (rec_drift - np.mean(sampled_returns[crash_duration + bottom_duration:]))
    else: # 'L'
        # No recovery
        sampled_returns[crash_duration:] = np.random.normal(-0.001, 0.005, horizon - crash_duration)
        
    # Reconstruct SPY price path
    spy_path = 100.0 * np.exp(np.cumsum(sampled_returns))
    
    # 3. Handle bond (TLT) returns based on inflation regime
    bond_returns = np.random.normal(0.0001, 0.004, horizon) # base bond path
    if inflation == "deflation":
        # Bonds are negatively correlated with stocks during crash (flight-to-safety)
        bond_returns[:crash_duration] -= 0.5 * sampled_returns[:crash_duration] # TLT rises as SPY falls
    else:
        # Stagflation: Bonds fall with stocks (positive correlation)
        bond_returns[:crash_duration] += 0.3 * sampled_returns[:crash_duration] # TLT falls too
        
    bond_path = 100.0 * np.exp(np.cumsum(bond_returns))
    
    df = pd.DataFrame({
        "date": pd.date_range(start="2026-01-01", periods=horizon, freq="B"),
        "SPY": spy_path,
        "TLT": bond_path
    })
    return df

def _defensive_weight_series(prices_df, risk_indices, policy):
    """Per-day target defensive weight d_t in [0,1] for a policy.

    policy["type"] is one of:
      - "buyhold": always 0 (100% SPY).
      - "static":  constant policy["d"] (e.g. Dalio All-Weather, Taleb Barbell).
      - "glide":   dynamic logistic glide on the standardized crash-risk score with a trend gate,
                   using policy["theta"]/["k"]/["gamma"]/["L"]/["U"].
    """
    n = len(prices_df)
    ptype = policy.get("type", "glide")
    if ptype == "buyhold":
        return np.zeros(n)
    if ptype == "static":
        return np.full(n, float(policy.get("d", 0.0)))

    spy_close = prices_df["SPY"].values
    dates = prices_df["date"].values
    spy_sma200 = pd.Series(spy_close).rolling(200, min_periods=1).mean().values
    theta, k, gamma = policy["theta"], policy["k"], policy["gamma"]
    L, U = policy.get("L", 0.0), policy.get("U", 0.90)

    d = np.zeros(n)
    for t in range(n):
        dt_str = dates[t].strftime("%Y-%m-%d") if hasattr(dates[t], "strftime") else str(dates[t])[:10]
        comp_idx = risk_indices.get(dt_str, 50.0)
        z = (comp_idx - 50.0) / 15.0
        denom = (spy_sma200[t] * 0.05) if spy_sma200[t] > 0 else 1.0
        trend_score = np.clip((spy_close[t] - spy_sma200[t]) / denom, -1.0, 1.0)
        d[t] = compute_defensive_coefficient(z, trend_score, theta, k, L=L, U=U, gamma=gamma)
    return d


def _simulate_curve(prices_df, d_series, fee_pct=0.0005):
    """Daily SPY/TLT blend simulation given a target defensive-weight series. Starts fully in SPY
    and rebalances toward d_t when drift exceeds 2%. Returns (equity_curve ndarray, turnover)."""
    spy_close = prices_df["SPY"].values
    tlt_close = prices_df["TLT"].values
    n = len(prices_df)

    V = 100000.0
    V_history = [V]
    w_agg, w_def = 1.0, 0.0
    turnover = 0.0

    for t in range(1, n):
        target_w_agg = 1.0 - d_series[t]
        target_w_def = d_series[t]

        ret_agg = (spy_close[t] - spy_close[t-1]) / spy_close[t-1]
        ret_def = (tlt_close[t] - tlt_close[t-1]) / tlt_close[t-1]

        gross = 1 + w_agg * ret_agg + w_def * ret_def
        V_prior = V * gross
        w_agg_drift = (w_agg * (1 + ret_agg)) / gross
        w_def_drift = (w_def * (1 + ret_def)) / gross

        if abs(target_w_agg - w_agg_drift) > 0.02:
            trade_size = abs(target_w_agg - w_agg_drift) + abs(target_w_def - w_def_drift)
            V = V_prior - V_prior * trade_size * fee_pct
            w_agg, w_def = target_w_agg, target_w_def
            turnover += trade_size
        else:
            V = V_prior
            w_agg, w_def = w_agg_drift, w_def_drift

        V_history.append(V)

    return np.array(V_history), turnover


def run_simulation(prices_df, risk_indices, theta, k, gamma, L=0.0, U=0.90, fee_pct=0.0005):
    """
    Runs daily portfolio simulation for a given GLIDE knob configuration (kept for the existing
    sweep/comparison callers). Blends aggressive (SPY) and defensive (TLT) portfolios.
    Returns: Ending Value, Max Drawdown, Ulcer Index, CVaR, Turnover, and daily equity curve.
    """
    policy = {"type": "glide", "theta": theta, "k": k, "gamma": gamma, "L": L, "U": U}
    d_series = _defensive_weight_series(prices_df, risk_indices, policy)
    V_history, turnover = _simulate_curve(prices_df, d_series, fee_pct=fee_pct)

    returns = np.diff(V_history) / V_history[:-1]
    ending_val = V_history[-1]
    peaks = np.maximum.accumulate(V_history)
    drawdowns = (peaks - V_history) / peaks
    cvar = np.mean(np.sort(returns)[:max(1, int(len(returns) * 0.05))]) if len(returns) > 0 else 0.0

    return {
        "ending_value": float(ending_val),
        "total_return": float((ending_val - 100000.0) / 100000.0 * 100.0),
        "max_drawdown": float(np.max(drawdowns) * 100.0),
        "ulcer_index": float(np.sqrt(np.mean(drawdowns ** 2)) * 100.0),
        "cvar": float(cvar * 100.0),
        "turnover": float(turnover),
        "equity_curve": V_history.tolist(),
    }

def _metrics_from_curve(V_history):
    """Compute summary risk/return metrics from an equity curve (numpy array)."""
    V_history = np.asarray(V_history, dtype=float)
    if len(V_history) < 2:
        return {"total_return": 0.0, "max_drawdown": 0.0, "ulcer_index": 0.0,
                "cvar": 0.0, "sharpe": 0.0}
    returns = np.diff(V_history) / V_history[:-1]
    peaks = np.maximum.accumulate(V_history)
    drawdowns = (peaks - V_history) / peaks
    cvar = float(np.mean(np.sort(returns)[:max(1, int(len(returns) * 0.05))])) if len(returns) else 0.0
    sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0.0
    return {
        "total_return": float((V_history[-1] - V_history[0]) / V_history[0] * 100.0),
        "max_drawdown": float(np.max(drawdowns) * 100.0),
        "ulcer_index": float(np.sqrt(np.mean(drawdowns ** 2)) * 100.0),
        "cvar": float(cvar * 100.0),
        "sharpe": sharpe,
    }


def run_preset_comparison(lookback_years=5, custom_knobs=None):
    """Read-only walk-forward backtest comparing the glide-path presets (and an optional custom
    knob set) against a passive Buy & Hold benchmark, using the REAL historical Composite
    Crash-Risk Index cached in crash_risk_snapshots.

    This NEVER touches the live/paper portfolio — it only simulates how each policy WOULD have
    steered an SPY/TLT blend over the available history. Returns aligned (downsampled) equity
    curves plus full-resolution metrics for each policy.
    """
    db = SessionLocal()
    try:
        snaps = db.query(CrashRiskSnapshot.as_of_date, CrashRiskSnapshot.composite_index).order_by(
            CrashRiskSnapshot.as_of_date.asc()
        ).all()
        if not snaps or len(snaps) < 10:
            return {"error": "Not enough cached crash-risk history to run a comparison."}

        risk_series = pd.Series(
            {pd.to_datetime(d): float(v) for d, v in snaps if v is not None}
        ).sort_index()
        end_date = risk_series.index.max()
        start_date = end_date - pd.DateOffset(years=lookback_years)
        risk_series = risk_series[risk_series.index >= start_date]

        # Daily SPY (aggressive) + TLT (defensive) prices over the window.
        rows = db.query(DailyPrice.date, DailyPrice.ticker, DailyPrice.close).filter(
            DailyPrice.ticker.in_(["SPY", "TLT"]),
            DailyPrice.date >= start_date.strftime("%Y-%m-%d"),
            DailyPrice.date <= end_date.strftime("%Y-%m-%d"),
        ).order_by(DailyPrice.date.asc()).all()
    finally:
        db.close()

    if not rows:
        return {"error": "No SPY price history available for the comparison window."}

    px = pd.DataFrame(rows, columns=["date", "ticker", "close"])
    px["date"] = pd.to_datetime(px["date"])
    px = px.pivot(index="date", columns="ticker", values="close")
    if "SPY" not in px.columns:
        return {"error": "SPY price history missing for the comparison window."}
    if "TLT" not in px.columns:
        # Defensive proxy: steady ~2% annualized T-bill-like cash if TLT not ingested.
        px["TLT"] = 100.0 * np.exp(0.02 * np.arange(len(px)) / 252)
    px = px.ffill().bfill().dropna()
    if len(px) < 30:
        return {"error": "Insufficient overlapping price data for the comparison window."}

    prices_df = px.reset_index()
    dates = prices_df["date"]

    # Daily risk index aligned to price dates (weekly snapshots forward-filled / interpolated).
    risk_daily = risk_series.reindex(dates).interpolate().ffill().bfill()
    risk_indices = {d.strftime("%Y-%m-%d"): float(v) for d, v in risk_daily.items()}

    # Policies to compare: the three presets + optional current custom knobs.
    policies = []
    for name in ["conservative", "balanced", "aggressive"]:
        p = PRESETS[name]
        policies.append({"label": name, "theta": p["theta"], "k": p["k"],
                         "gamma": p["gamma"], "L": p["L"], "U": p["U"]})
    if custom_knobs:
        policies.append({
            "label": "custom",
            "theta": float(custom_knobs.get("theta", 0.85)),
            "k": float(custom_knobs.get("k", 2.0)),
            "gamma": float(custom_knobs.get("gamma", 0.25)),
            "L": 0.0, "U": 0.90,
        })

    # Downsample the x-axis/equity curves to weekly to keep the payload light.
    n = len(prices_df)
    step = max(1, n // 260)
    idx_sample = list(range(0, n, step))
    if idx_sample[-1] != n - 1:
        idx_sample.append(n - 1)
    sampled_dates = [dates.iloc[i].strftime("%Y-%m-%d") for i in idx_sample]

    series = []
    for pol in policies:
        sim = run_simulation(prices_df, risk_indices, pol["theta"], pol["k"], pol["gamma"],
                             L=pol["L"], U=pol["U"])
        curve = np.asarray(sim["equity_curve"], dtype=float)
        metrics = _metrics_from_curve(curve)
        series.append({
            "label": pol["label"],
            "theta": pol["theta"], "k": pol["k"], "gamma": pol["gamma"],
            "equity_curve": [round(float(curve[i]), 2) for i in idx_sample],
            "turnover": float(sim["turnover"]),
            **metrics,
        })

    # Buy & Hold (SPY only) benchmark on the same capital base.
    spy = prices_df["SPY"].values
    bh_curve = 100000.0 * spy / spy[0]
    bh_metrics = _metrics_from_curve(bh_curve)
    benchmark = {
        "label": "Buy & Hold (SPY)",
        "equity_curve": [round(float(bh_curve[i]), 2) for i in idx_sample],
        "turnover": 0.0,
        **bh_metrics,
    }

    return {
        "start_date": sampled_dates[0],
        "end_date": sampled_dates[-1],
        "dates": sampled_dates,
        "benchmark": benchmark,
        "series": series,
    }


KNOB_GLOSSARY = {
    "theta": {
        "symbol": "θ",
        "name": "De-risking threshold",
        "desc": "How high the standardized crash-risk score must climb before you start shifting "
                "from stocks into defense. Higher θ = wait longer before de-risking (more aggressive); "
                "lower θ = bail to safety sooner (more defensive).",
    },
    "k": {
        "symbol": "k",
        "name": "Curve steepness",
        "desc": "How sharply the switch from offense to defense happens around θ. Higher k = a fast, "
                "almost all-or-nothing flip; lower k = a gradual glide that scales in over a range.",
    },
    "gamma": {
        "symbol": "γ",
        "name": "Trend gate",
        "desc": "Raises the threshold while the market is in an uptrend so you don't bail out early "
                "during a melt-up. Higher γ = keep participating longer in strong uptrends.",
    },
}

# Scenario display metadata.
SCENARIO_META = {
    "dotcom": {"label": "Dot-Com Bust (2000–02)",
               "subtitle": "Nasdaq −78% over ~2.5y; slow grind down. (Defense = cash-like; TLT not available pre-2002.)"},
    "gfc": {"label": "Global Financial Crisis (2008)",
            "subtitle": "S&P −57% peak-to-trough; credit seizure, flight to Treasuries."},
    "covid": {"label": "COVID Crash (2020)",
              "subtitle": "−34% in 5 weeks, then a V-shaped rebound — punishes over-reaction."},
    "2022": {"label": "2022 Rate-Shock Bear",
             "subtitle": "−25% grind; bonds fell WITH stocks (defense hurt too)."},
    "synth_deflation": {"label": "Synthetic −35% (deflationary, U-shaped)",
                        "subtitle": "Bonds hedge stocks; delayed recovery off the bottom."},
    "synth_stagflation": {"label": "Synthetic −30% (stagflation, V-shaped)",
                          "subtitle": "Bonds fall with stocks; fast snap-back recovery."},
}


def _policy_roster(custom_knobs=None):
    """The fixed set of strategies compared head-to-head, from 'do nothing' to fully defensive."""
    P = PRESETS
    roster = [
        {"id": "buyhold", "label": "Buy & Hold", "type": "buyhold", "color": "#9CA3AF",
         "desc": "100% SPY, never de-risk — the 'do nothing' baseline."},
        {"id": "aggressive", "label": "Aggressive Glide", "type": "glide", "color": "#F59E0B",
         "theta": P["aggressive"]["theta"], "k": P["aggressive"]["k"], "gamma": P["aggressive"]["gamma"],
         "L": P["aggressive"]["L"], "U": P["aggressive"]["U"],
         "desc": "Stays in stocks and only de-risks when risk is extreme (late, shallow)."},
        {"id": "balanced", "label": "Balanced Glide", "type": "glide", "color": "#3B82F6",
         "theta": P["balanced"]["theta"], "k": P["balanced"]["k"], "gamma": P["balanced"]["gamma"],
         "L": P["balanced"]["L"], "U": P["balanced"]["U"],
         "desc": "Moderate, symmetric de-risking around average risk."},
        {"id": "conservative", "label": "Conservative Glide", "type": "glide", "color": "#10B981",
         "theta": P["conservative"]["theta"], "k": P["conservative"]["k"], "gamma": P["conservative"]["gamma"],
         "L": P["conservative"]["L"], "U": P["conservative"]["U"],
         "desc": "De-risks early and holds more defense (cautious)."},
        {"id": "allweather", "label": "All-Weather (Dalio)", "type": "static", "d": 0.60, "color": "#A78BFA",
         "desc": "Fixed ~40% SPY / 60% defense at all times, ignoring the signal (Ray Dalio archetype)."},
        {"id": "taleb", "label": "Taleb Barbell", "type": "static", "d": 0.90, "color": "#EF4444",
         "desc": "Fixed 90% safe / 10% risk — extreme caution (Nassim Taleb archetype)."},
    ]
    if custom_knobs:
        roster.append({
            "id": "custom", "label": "Your Custom Knobs", "type": "glide", "color": "#EC4899",
            "theta": float(custom_knobs.get("theta", 0.85)),
            "k": float(custom_knobs.get("k", 2.0)),
            "gamma": float(custom_knobs.get("gamma", 0.25)),
            "L": 0.0, "U": 0.90,
            "desc": "The glide-path curve currently set on your knobs.",
        })
    return roster


def run_scenario_comparison(custom_knobs=None, progress_cb=None):
    """Replays every policy in the roster across historical bear regimes (Dot-Com, GFC, COVID, 2022)
    and two synthetic crash shapes, returning downsampled equity curves + risk/return metrics per
    (scenario, policy). Read-only — never touches the live/paper portfolio.
    """
    def report(p, s):
        if progress_cb:
            progress_cb(int(p), s)

    roster = _policy_roster(custom_knobs)
    scenarios_def = [
        ("dotcom", "historical"), ("gfc", "historical"), ("covid", "historical"), ("2022", "historical"),
        ("synth_deflation", "synthetic"), ("synth_stagflation", "synthetic"),
    ]

    # Daily SPY log-returns feed the synthetic bootstrap. Seed for reproducible scenarios.
    np.random.seed(42)
    db = SessionLocal()
    spy_rows = db.query(DailyPrice.close).filter(DailyPrice.ticker == "SPY").order_by(DailyPrice.date.asc()).all()
    db.close()
    if spy_rows:
        spy_close_all = np.array([float(r[0]) for r in spy_rows])
        spy_returns = np.diff(np.log(spy_close_all))
    else:
        spy_returns = np.random.normal(0.0003, 0.01, 1000)

    out_scenarios = []
    total = len(scenarios_def)
    for i, (sid, kind) in enumerate(scenarios_def):
        report(8 + i / total * 84, f"Simulating {SCENARIO_META.get(sid, {}).get('label', sid)}")
        if kind == "historical":
            prices = load_era_prices(sid)
            dates_list = prices["date"].tolist()
            risk = precompute_risk_indices(sid, dates_list)
        else:
            if sid == "synth_deflation":
                prices = generate_bootstrap_scenario(spy_returns, target_dd=0.35, horizon=252,
                                                     recovery="U", inflation="deflation")
            else:
                prices = generate_bootstrap_scenario(spy_returns, target_dd=0.30, horizon=252,
                                                     recovery="V", inflation="stagflation")
            n_days = len(prices)
            synth_risk = 40.0 + 45.0 * np.sin(np.pi * np.arange(n_days) / n_days)
            dl = prices["date"].tolist()
            risk = {dl[j].strftime("%Y-%m-%d"): float(synth_risk[j]) for j in range(n_days)}

        n = len(prices)
        step = max(1, n // 120)
        idx = list(range(0, n, step))
        if idx[-1] != n - 1:
            idx.append(n - 1)
        date_col = prices["date"]
        dates_fmt = []
        for j in idx:
            dv = date_col.iloc[j]
            dates_fmt.append(dv.strftime("%Y-%m-%d") if hasattr(dv, "strftime") else str(dv)[:10])

        series = {}
        for pol in roster:
            d_series = _defensive_weight_series(prices, risk, pol)
            curve, turnover = _simulate_curve(prices, d_series)
            m = _metrics_from_curve(curve)
            series[pol["id"]] = {
                "equity_curve": [round(float(curve[j]), 2) for j in idx],
                "turnover": round(float(turnover), 2),
                "total_return": round(m["total_return"], 2),
                "max_drawdown": round(m["max_drawdown"], 2),
                "ulcer_index": round(m["ulcer_index"], 2),
                "cvar": round(m["cvar"], 3),
                "sharpe": round(m["sharpe"], 2),
                "final_value": round(float(curve[-1]), 0),
            }

        # Perfect-foresight reference: hold SPY to the peak, TLT through the trough, SPY after.
        spy = prices["SPY"].values
        tlt = prices["TLT"].values
        peak = int(np.argmax(spy))
        trough = peak + int(np.argmin(spy[peak:]))
        pf_val = 100000.0 * (spy[peak] / spy[0]) * (tlt[trough] / tlt[peak]) * (spy[-1] / spy[trough])
        pf_ret = (pf_val - 100000.0) / 100000.0 * 100.0

        meta = SCENARIO_META.get(sid, {})
        out_scenarios.append({
            "id": sid,
            "kind": kind,
            "label": meta.get("label", sid),
            "subtitle": meta.get("subtitle", ""),
            "dates": dates_fmt,
            "series": series,
            "perfect_foresight_return": round(float(pf_ret), 1),
        })

    report(96, "Finalizing")
    return {
        "policies": roster,
        "knob_glossary": KNOB_GLOSSARY,
        "scenarios": out_scenarios,
        "has_custom": custom_knobs is not None,
        "generated_at": datetime.now().isoformat(),
    }


def run_wargame_sweep(theta_range=None, k_range=None, gamma_range=None):
    """
    Runs a parameter sweep grid search over historical eras and synthetic paths.
    Computes minimax regret and returns optimization heatmap.
    """
    if theta_range is None:
        theta_range = {"min": 0.5, "max": 1.5, "steps": 5}
    if k_range is None:
        k_range = {"min": 1.0, "max": 4.0, "steps": 5}
    if gamma_range is None:
        gamma_range = {"min": 0.0, "max": 0.5, "steps": 3}
        
    thetas = np.linspace(theta_range["min"], theta_range["max"], theta_range["steps"])
    ks = np.linspace(k_range["min"], k_range["max"], k_range["steps"])
    gammas = np.linspace(gamma_range["min"], gamma_range["max"], gamma_range["steps"])
    
    # 1. Load Era Data & Precompute Risk Indices
    eras = ["dotcom", "gfc", "covid"]
    era_data = {}
    for era in eras:
        prices = load_era_prices(era)
        dates_list = prices["date"].tolist()
        risk_indices = precompute_risk_indices(era, dates_list)
        era_data[era] = {"prices": prices, "risk_indices": risk_indices}
        
    # 2. Build Synthetic Scenario (Stagflationary + Deflationary crash)
    # Grab daily SPY returns to feed bootstrap
    db = SessionLocal()
    spy_prices = db.query(DailyPrice.close).filter(DailyPrice.ticker == "SPY").order_by(DailyPrice.date.asc()).all()
    db.close()
    if spy_prices:
        spy_close = np.array([r[0] for r in spy_prices])
        spy_returns = np.diff(np.log(spy_close))
    else:
        spy_returns = np.random.normal(0.0003, 0.01, 1000)
        
    synth_deflation = generate_bootstrap_scenario(spy_returns, target_dd=0.35, horizon=252, recovery="U", inflation="deflation")
    synth_stagflation = generate_bootstrap_scenario(spy_returns, target_dd=0.30, horizon=252, recovery="V", inflation="stagflation")
    
    # Risk indices for synthetic: proxy using a synthetic risk wave
    synth_dates_def = synth_deflation["date"].tolist()
    synth_dates_stag = synth_stagflation["date"].tolist()
    
    # Risk rises to peak of 85 during crash, then subsides
    n_days = len(synth_deflation)
    synth_risk = 40.0 + 45.0 * np.sin(np.pi * np.arange(n_days) / n_days)
    
    risk_indices_def = {synth_dates_def[i].strftime("%Y-%m-%d"): float(synth_risk[i]) for i in range(n_days)}
    risk_indices_stag = {synth_dates_stag[i].strftime("%Y-%m-%d"): float(synth_risk[i]) for i in range(n_days)}
    
    era_data["synth_deflation"] = {"prices": synth_deflation, "risk_indices": risk_indices_def}
    era_data["synth_stagflation"] = {"prices": synth_stagflation, "risk_indices": risk_indices_stag}
    
    # 3. Precompute Perfect Foresight & Buy-and-Hold Benchmarks for each scenario
    benchmarks = {}
    for scenario_name, data in era_data.items():
        prices = data["prices"]
        spy = prices["SPY"].values
        tlt = prices["TLT"].values
        
        # Buy-and-Hold Return
        bh_ret = (spy[-1] - spy[0]) / spy[0] * 100.0
        
        # Perfect Foresight (exits to TLT/Cash at peak of SPY, enters at trough)
        peak_idx = np.argmax(spy)
        trough_idx = peak_idx + np.argmin(spy[peak_idx:])
        
        pf_val = 100000.0
        # Phase 1: Hold SPY
        pf_val *= (spy[peak_idx] / spy[0])
        # Phase 2: Hold TLT during crash
        pf_val *= (tlt[trough_idx] / tlt[peak_idx])
        # Phase 3: Hold SPY after trough
        pf_val *= (spy[-1] / spy[trough_idx])
        
        pf_ret = (pf_val - 100000.0) / 100000.0 * 100.0
        benchmarks[scenario_name] = {"bh": bh_ret, "pf": pf_ret}
        
    # 4. Sweep Parameters Grid
    heatmap = []
    optimal_knobs = {"theta": 0.85, "k": 2.0, "gamma": 0.25}
    min_worst_case_regret = 999.0
    
    for theta in thetas:
        for k in ks:
            for gamma in gammas:
                scenario_results = {}
                regrets_pf = []
                regrets_bh = []
                total_drawdown = 0.0
                total_return = 0.0
                
                for name, data in era_data.items():
                    sim = run_simulation(data["prices"], data["risk_indices"], theta, k, gamma)
                    sim_ret = sim["total_return"]
                    
                    # Regret calculation
                    regret_pf = benchmarks[name]["pf"] - sim_ret
                    regret_bh = benchmarks[name]["bh"] - sim_ret
                    
                    regrets_pf.append(regret_pf)
                    regrets_bh.append(regret_bh)
                    total_drawdown += sim["max_drawdown"]
                    total_return += sim_ret
                    
                avg_drawdown = total_drawdown / len(era_data)
                avg_return = total_return / len(era_data)
                max_regret_pf = float(np.max(regrets_pf))
                
                heatmap.append({
                    "theta": float(theta),
                    "k": float(k),
                    "gamma": float(gamma),
                    "max_drawdown": float(-avg_drawdown),
                    "return": float(avg_return),
                    "max_regret": float(max_regret_pf)
                })
                
                if max_regret_pf < min_worst_case_regret:
                    min_worst_case_regret = max_regret_pf
                    optimal_knobs = {
                        "theta": float(theta),
                        "k": float(k),
                        "gamma": float(gamma)
                    }
                    
    # Generate a Pareto Frontier curve (Max drawdown vs. Return)
    pareto_frontier = []
    # Sort by return descending
    sorted_heatmap = sorted(heatmap, key=lambda x: x["return"], reverse=True)
    current_best_dd = 999.0 # We want smaller (less negative) drawdown
    for h in sorted_heatmap:
        # absolute drawdown to positive number
        dd_abs = abs(h["max_drawdown"])
        if dd_abs < current_best_dd:
            current_best_dd = dd_abs
            pareto_frontier.append({
                "return": h["return"],
                "drawdown": -dd_abs
            })
            
    return {
        "optimal_knobs": optimal_knobs,
        "heatmap": heatmap,
        "pareto_frontier": pareto_frontier
    }

if __name__ == "__main__":
    print("Running test parameter sweep wargame...")
    res = run_wargame_sweep(
        theta_range={"min": 0.7, "max": 1.1, "steps": 3},
        k_range={"min": 1.5, "max": 2.5, "steps": 3},
        gamma_range={"min": 0.1, "max": 0.3, "steps": 2}
    )
    print("Optimal Knobs:", res["optimal_knobs"])
    print("Heatmap size:", len(res["heatmap"]))
    print("Pareto points:", len(res["pareto_frontier"]))
