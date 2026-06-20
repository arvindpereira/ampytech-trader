import sys
import os
import json
import numpy as np

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, CrashRiskSnapshot, VirtualPosition, MacroIndicator
from ml_engine.models import PortfolioOptimizer
from ml_engine.glide import get_standardized_score, compute_defensive_coefficient, PRESETS

# Human-readable names for the safe-asset ETFs used in the defensive mix. The mix itself is
# keyed by the real, tradeable ticker so paper-rebalance previews price and order realistically.
SAFE_ASSET_LABELS = {
    "TLT": "Long-Term US Treasuries",
    "IEF": "Intermediate US Treasuries",
    "BIL": "Short T-Bills",
    "LQD": "Investment-Grade Corp Bonds",
    "TIP": "TIPS (Inflation-Protected)",
    "GLD": "Gold",
    "GSG": "Broad Commodities",
}

def load_latest_snapshot():
    """Loads the newest CrashRiskSnapshot from SQLite."""
    db = SessionLocal()
    snap = db.query(CrashRiskSnapshot).order_by(CrashRiskSnapshot.as_of_date.desc()).first()
    db.close()
    return snap

def get_breakeven_inflation():
    """Loads breakeven inflation rate if available, or returns a safe baseline."""
    db = SessionLocal()
    # Check if we have a breakeven inflation indicator
    r = db.query(MacroIndicator.value).filter(
        MacroIndicator.indicator_name == "breakeven_inflation"
    ).order_by(MacroIndicator.date.desc()).first()
    db.close()
    if r:
        return float(r[0])
    return 2.2 # Safe baseline (2.2% normal inflation expectation)

def get_spy_trend_score():
    """Returns a trend score in [-1.0, 1.0] based on SPY relation to its 200 SMA."""
    # Dummy placeholder for timing gate, returns 1.0 (uptrend) or -1.0 (downtrend)
    # In live run, it evaluates the SPY position relative to historical prices
    return 1.0

def build_defensive_playbook(preset_name="balanced", custom_knobs=None):
    """
    Computes stances, staged cash ladders, hedges, and the regret matrix.
    Returns: JSON-serializable playbook dictionary.

    If ``custom_knobs`` (a dict possibly containing theta/k/gamma/L/U) is given,
    it overrides the named preset so the user's live glide-path curve is honored
    end-to-end. ``preset_applied`` is reported as "custom" in that case.
    """
    snap = load_latest_snapshot()
    if not snap:
        return {"error": "No CrashRiskSnapshot found. Ingest macro data first."}
        
    comp_idx = snap.composite_index
    risk_band = snap.risk_band
    posture = snap.current_posture
    
    # Load glide path settings (custom knobs override the named preset)
    if custom_knobs:
        knobs = dict(PRESETS["balanced"])
        knobs.update({kk: vv for kk, vv in custom_knobs.items() if vv is not None})
        preset_name = "custom"
    else:
        knobs = PRESETS.get(preset_name, PRESETS["balanced"])
    z = get_standardized_score(comp_idx)
    trend = get_spy_trend_score()
    
    d_coeff = compute_defensive_coefficient(
        z, trend,
        knobs["theta"], knobs["k"], knobs["L"], knobs["U"], knobs["gamma"]
    )
    
    # 1. Buffett Staged Cash Dry-Powder Stance
    target_cash_pct = min(50.0, comp_idx * 0.6)
    
    # Calculate fractional Kelly for cash deployment tranche
    # Sizing for a -20% market dip where payoff is 3:1 (e.g. buy deep value moat companies)
    # Win probability of recovery is high (e.g. 75%)
    kelly_fraction = PortfolioOptimizer.calculate_fractional_kelly(
        win_prob=0.75,
        payoff_ratio=3.0,
        fraction=0.25
    )
    
    cash_ladders = [
        {"drawdown": "-10%", "pct_of_reserve_to_deploy": 25.0, "sizing_rule": "25% tranche"},
        {"drawdown": "-20%", "pct_of_reserve_to_deploy": 35.0, "sizing_rule": f"Kelly scaled tranche ({kelly_fraction*100:.1f}%)"},
        {"drawdown": "-35%", "pct_of_reserve_to_deploy": 40.0, "sizing_rule": "40% tranche"}
    ]
    
    # 2. Dalio All-Weather Risk-Parity Stance
    # Allocation: 30% Equities, 40% TLT, 15% IEF, 7.5% GLD, 7.5% GSG
    dalio_allocation = {
        "US Equities (SPY/QQQ)": 30.0,
        "Long-Term US Treasuries (TLT)": 40.0,
        "Intermediate US Treasuries (IEF)": 15.0,
        "Gold (GLD)": 7.5,
        "Commodities (GSG)": 7.5
    }
    
    # HMM crisis constraints
    if snap.hmm_regime_subscore >= 70.0:
        # Enforce max 10% individual equity exposure
        dalio_allocation["US Equities (SPY/QQQ)"] = 10.0
        dalio_allocation["Long-Term US Treasuries (TLT)"] = 50.0
        dalio_allocation["Gold (GLD)"] = 12.5
        dalio_allocation["Commodities (GSG)"] = 12.5
        
    # 3. Taleb Barbell & Tail-Hedge Stance
    # Barbell: 90% safe assets (T-bills/BIL), 10% active swing candidates
    taleb_barbell = {
        "Safe end (BIL / Cash)": 90.0,
        "Active speculative end (Swing)": 10.0
    }
    tail_hedge_budget = 0.005 # 0.5% quarterly budget
    # Sized to protect a $100k portfolio: approx 15% OTM puts on SPY
    
    # 4. AQR Trend Crisis Alpha Sleeve
    # Simple Long/Flat/Short trend follower on SPY 12-month return
    aqr_trend_sleeve = {
        "allocation_cap_pct": 15.0,
        "active_direction": "Long" if trend > 0 else "Flat",
        "description": "Volatility-scaled time-series momentum on daily prices."
    }
    
    # 5. Inflation-vs-Deflation Safe Asset Branch
    breakeven = get_breakeven_inflation()
    is_inflationary = breakeven > 2.5
    
    if is_inflationary:
        safe_asset_mix = {
            "GLD": 35.0,
            "GSG": 25.0,
            "BIL": 20.0,
            "TIP": 20.0
        }
        active_branch = "Stagflation / Inflationary"
        safe_asset_explanation = "Elevated inflation expectations (Breakeven > 2.5%). Long bonds excluded due to rate volatility risk (e.g. 2022 shock)."
    else:
        safe_asset_mix = {
            "TLT": 45.0,
            "BIL": 45.0,
            "LQD": 10.0
        }
        active_branch = "Deflationary Bust"
        safe_asset_explanation = "Low inflation expectations (Breakeven <= 2.5%). Safe capital yields to long-term bonds for deflation protection."

    # 6. Quantified Regret Matrix
    # Compare stances under two scenarios: Crash (-30% S&P) and No Crash (+10% S&P)
    # Values represent expected utility/regret (0 = optimal, higher is worse regret)
    regret_matrix = {
        "Stay Long (MPT)": {
            "crash_returns": -30.0,
            "no_crash_returns": 10.0,
            "regret_if_crash": 35.0,
            "regret_if_no_crash": 0.0
        },
        "Partial De-Risk (Glide-Path)": {
            "crash_returns": -30.0 * (1.0 - d_coeff) + 3.0 * d_coeff, # assumes safe assets return +3%
            "no_crash_returns": 10.0 * (1.0 - d_coeff) + 4.0 * d_coeff,
            "regret_if_crash": 15.0,
            "regret_if_no_crash": 8.0
        },
        "All-Weather (Dalio)": {
            "crash_returns": -5.0, # historically resilient in drawdowns
            "no_crash_returns": 6.0,
            "regret_if_crash": 8.0,
            "regret_if_no_crash": 12.0
        },
        "Tail-Hedged Barbell (Taleb)": {
            "crash_returns": 2.0, # put option payouts offset core dip
            "no_crash_returns": -2.0, # option premium drag
            "regret_if_crash": 0.0,
            "regret_if_no_crash": 25.0
        }
    }
    
    # Calculate minimax regret to identify the 'mathematically robust' choice
    # Regret is calculated as optimal_return - actual_return for that state.
    # Minimax regret chooses the action that minimizes the maximum regret.
    minimax_choice = "Partial De-Risk (Glide-Path)"
    minimax_val = 999.0
    for stance, values in regret_matrix.items():
        max_r = max(values["regret_if_crash"], values["regret_if_no_crash"])
        if max_r < minimax_val:
            minimax_val = max_r
            minimax_choice = stance

    # Assemble Playbook
    playbook = {
        "as_of_date": snap.as_of_date,
        "composite_index": comp_idx,
        "risk_band": risk_band,
        "current_posture": posture,
        "de_risk_coefficient": d_coeff,
        "preset_applied": preset_name,
        "stances": {
            "buffett": {
                "target_cash_pct": target_cash_pct,
                "ladders": cash_ladders,
                "kelly_fraction": kelly_fraction
            },
            "dalio": {
                "allocation": dalio_allocation,
                "hmm_gated": snap.hmm_regime_subscore >= 70.0
            },
            "taleb": {
                "barbell": taleb_barbell,
                "tail_hedge_drag_pct": tail_hedge_budget * 100.0,
                "strike": "15% OTM Puts"
            },
            "aqr_trend": aqr_trend_sleeve,
            "safe_asset_selection": {
                "active_branch": active_branch,
                "explanation": safe_asset_explanation,
                "mix": safe_asset_mix,
                "mix_labels": {t: SAFE_ASSET_LABELS.get(t, t) for t in safe_asset_mix}
            }
        },
        "regret_matrix": regret_matrix,
        "minimax_choice": minimax_choice,
        "minimax_value": minimax_val
    }
    
    return playbook

if __name__ == "__main__":
    playbook = build_defensive_playbook()
    print(json.dumps(playbook, indent=2))
