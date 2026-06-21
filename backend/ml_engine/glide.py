import numpy as np

# Presets mapping (theta, k, L, U, gamma)
PRESETS = {
    "conservative": {
        "theta": 0.60,
        "k": 3.0,
        "L": 0.10,
        "U": 0.95,
        "gamma": 0.15,
        "theta_r": -0.10,
        "k_r": 2.5
    },
    "balanced": {
        "theta": 0.85,
        "k": 2.0,
        "L": 0.00,
        "U": 0.90,
        "gamma": 0.25,
        "theta_r": -0.20,
        "k_r": 2.0
    },
    "aggressive": {
        "theta": 1.10,
        "k": 1.5,
        "L": 0.00,
        "U": 0.80,
        "gamma": 0.40,
        "theta_r": -0.30,
        "k_r": 1.5
    }
}

def get_standardized_score(composite_index):
    """Converts 0-100 index to standardized z units."""
    return (composite_index - 50.0) / 15.0

def compute_defensive_coefficient(z, trend_score, theta, k, L, U, gamma):
    """
    Computes the de-risking blending weight d(z) using a logistic sigmoid and a trend gate.
    - trend_score in [-1.0, 1.0] (where +1.0 is a strong positive uptrend).
    """
    # Trend gate shifts the threshold rightward during an uptrend to avoid exiting early.
    theta_eff = theta + gamma * trend_score

    # Sigmoid function
    exp_val = np.clip(-k * (z - theta_eff), -50, 50) # Prevent overflow
    sigmoid = 1.0 / (1.0 + np.exp(exp_val))

    # Scale between L and U
    d_val = L + (U - L) * sigmoid
    return float(max(0.0, min(1.0, d_val)))

def blend_portfolios(agg_weights, def_weights, d_val):
    """
    Blends aggressive and defensive portfolios on the simplex.
    Guarantees weights sum to 1.0.
    """
    # Ensure they are dictionaries matching tickers
    tickers = set(agg_weights.keys()).union(def_weights.keys())

    blended = {}
    for t in tickers:
        w_agg = agg_weights.get(t, 0.0)
        w_def = def_weights.get(t, 0.0)
        blended[t] = (1.0 - d_val) * w_agg + d_val * w_def

    # Enforce sum to 1.0 to absorb any floating point rounding drift
    total = sum(blended.values())
    if total > 0:
        blended = {k: v / total for k, v in blended.items()}

    return blended
