import sys
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, DailyPrice, CrisisPrice, MacroIndicator
from ml_engine.glide import compute_defensive_coefficient
from ml_engine.crash_radar import compute_composite_index

# Global memory cache for pre-computed risk indices to keep sweeps fast
_risk_index_cache = {}

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
    
    _risk_index_cache[cache_key] = series.to_dict()
    return _risk_index_cache[cache_key]

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

def run_simulation(prices_df, risk_indices, theta, k, gamma, fee_pct=0.0005):
    """
    Runs daily portfolio simulation for a given knob configuration.
    Blends aggressive (SPY) and defensive (TLT) portfolios.
    Returns: Ending Value, Max Drawdown, Ulcer Index, CVaR, Turnover, and daily equity curve.
    """
    V = 100000.0 # Starting capital
    V_history = [V]
    
    # Pre-calculate SPY 200 SMA for trend gate
    spy_close = prices_df["SPY"].values
    tlt_close = prices_df["TLT"].values
    dates = prices_df["date"].values
    n = len(prices_df)
    
    # 200-day rolling window for trend gate
    # In eras like Dot-Com / GFC we have daily prices leading into the era, so we can calculate SMA.
    # For synthetic, we can approximate trend score by local price direction.
    spy_sma200 = pd.Series(spy_close).rolling(200, min_periods=1).mean().values
    
    w_agg = 1.0
    w_def = 0.0
    
    turnover = 0.0
    
    for t in range(1, n):
        # 1. Compute standardized risk score z
        dt_str = dates[t].strftime("%Y-%m-%d") if hasattr(dates[t], "strftime") else str(dates[t])[:10]
        comp_idx = risk_indices.get(dt_str, 50.0)
        z = (comp_idx - 50.0) / 15.0
        
        # 2. Trend score
        denom = (spy_sma200[t] * 0.05) if spy_sma200[t] > 0 else 1.0
        trend_score = np.clip((spy_close[t] - spy_sma200[t]) / denom, -1.0, 1.0)
        
        # 3. Defensive blending weight
        d_val = compute_defensive_coefficient(z, trend_score, theta, k, L=0.0, U=0.90, gamma=gamma)
        
        target_w_agg = 1.0 - d_val
        target_w_def = d_val
        
        # Calculate daily asset returns
        ret_agg = (spy_close[t] - spy_close[t-1]) / spy_close[t-1]
        ret_def = (tlt_close[t] - tlt_close[t-1]) / tlt_close[t-1]
        
        # Prior portfolio value before rebalancing
        V_prior = V * (1 + w_agg * ret_agg + w_def * ret_def)
        
        # Drifted weights before rebalancing
        w_agg_drift = (w_agg * (1 + ret_agg)) / (1 + w_agg * ret_agg + w_def * ret_def)
        w_def_drift = (w_def * (1 + ret_def)) / (1 + w_agg * ret_agg + w_def * ret_def)
        
        # Rebalance threshold (2% deviation)
        if abs(target_w_agg - w_agg_drift) > 0.02:
            # Execute trade
            trade_size_agg = abs(target_w_agg - w_agg_drift)
            trade_size_def = abs(target_w_def - w_def_drift)
            rebalance_cost = V_prior * (trade_size_agg + trade_size_def) * fee_pct
            
            V = V_prior - rebalance_cost
            w_agg = target_w_agg
            w_def = target_w_def
            turnover += (trade_size_agg + trade_size_def)
        else:
            V = V_prior
            w_agg = w_agg_drift
            w_def = w_def_drift
            
        V_history.append(V)
        
    # Metrics calculations
    V_history = np.array(V_history)
    returns = np.diff(V_history) / V_history[:-1]
    
    # Ending Value
    ending_val = V_history[-1]
    
    # Max Drawdown
    peaks = np.maximum.accumulate(V_history)
    drawdowns = (peaks - V_history) / peaks
    max_dd = np.max(drawdowns)
    
    # Ulcer Index
    ulcer_index = np.sqrt(np.mean(drawdowns ** 2))
    
    # CVaR (5% tail)
    if len(returns) > 0:
        cvar = np.mean(np.sort(returns)[:max(1, int(len(returns) * 0.05))])
    else:
        cvar = 0.0
        
    total_return = (ending_val - 100000.0) / 100000.0 * 100.0
    
    return {
        "ending_value": float(ending_val),
        "total_return": float(total_return),
        "max_drawdown": float(max_dd * 100.0),
        "ulcer_index": float(ulcer_index * 100.0),
        "cvar": float(cvar * 100.0),
        "turnover": float(turnover),
        "equity_curve": V_history.tolist()
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
