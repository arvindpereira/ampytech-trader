"""Expected-value optimizer for the crash-rebalance participation slider.

The slider sets how much of the portfolio to rotate from the aggressive sleeve into the defensive
safe-asset sleeve. This module sweeps that fraction and picks the value that maximizes expected
**log-growth** across two scenarios weighted by the current crash probability:

    growth(p) = (1 - P_crash) * ln(1 + normal_return(p)) + P_crash * ln(1 + crash_return(p))

- normal_return(p): annualized return of the blended portfolio over a recent lookback window.
- crash_return(p): total return of the blended portfolio through a historical crash era
  (GFC / COVID / dot-com), reconstructed from each name's SPY-beta.
- P_crash: probability the next horizon is a crash, mapped from the Crash Radar composite index.

We optimize log-growth rather than plain expected return on purpose: a lookback CAGR is
momentum-inflated (recent winners look like they return enormously), so arithmetic EV almost always
says "hold everything." Log-growth penalizes deep drawdowns asymmetrically — a −74% crash is
near-ruinous in log terms (it needs +290% to recover) — which is exactly the "survive to compound"
objective of crash protection. The arithmetic EV is still reported per level for transparency.

The intent the user described falls straight out of the argmax: when holding through the risk would
cripple compounding (high P_crash, deep crash drawdown), the optimum is a LARGER slider; when
de-risking costs more growth than it saves (low P_crash, strong normal trend), it's a SMALLER one.

This is a heuristic decision aid, not a forecast; the P_crash mapping is deliberately simple and is
meant to be calibrated against the Phase-3 walk-forward results.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ml_engine.wargame import _metrics_from_curve
from app.services.account_wargame import _price_frame, _simulate, _estimate_betas, _era_synthetic_piv


# Minimum crash probability per risk band — a floor so an Elevated/High posture never reads as ~0.
_BAND_FLOOR = {"Calm": 0.03, "Elevated": 0.10, "High": 0.25, "Severe": 0.45}


def crash_probability(composite_index: Optional[float], risk_band: Optional[str]) -> float:
    """Map the Crash Radar composite index (0-100) + band to a horizon crash probability in [0, 0.9].

    Heuristic: a smooth component from the index (≈0.77 at the 100 extreme) floored by the band, so a
    High/Severe posture can't read as negligible even if the index sits mid-range.
    """
    idx = max(0.0, min(100.0, float(composite_index or 0.0)))
    p = idx / 130.0
    p = max(p, _BAND_FLOOR.get(str(risk_band), 0.0))
    return round(min(0.9, p), 4)


def _annualized(total_return_pct: float, n_days: int) -> float:
    """Annualize a total % return observed over n trading days."""
    if n_days <= 1:
        return total_return_pct
    years = n_days / 252.0
    gross = 1.0 + total_return_pct / 100.0
    if gross <= 0 or years <= 0:
        return total_return_pct
    return (gross ** (1.0 / years) - 1.0) * 100.0


def recommend_participation(
    db,
    current_values: Dict[str, float],
    w_def: Dict[str, float],
    composite_index: Optional[float],
    risk_band: Optional[str],
    lookback_years: int = 3,
    crash_era: str = "gfc",
    grid: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Sweep the participation slider and return the EV-maximizing value plus the full curve.

    current_values: {TICKER: market_value} for the book's current holdings (the aggressive sleeve).
    w_def:          {TICKER: weight 0..1} defensive safe-asset mix from the playbook.
    """
    grid = grid or [round(i * 0.1, 2) for i in range(11)]  # 0.0 .. 1.0
    warnings: List[str] = []

    total = sum(v for v in current_values.values() if v > 0)
    if total > 0:
        w_agg = {t.upper(): v / total for t, v in current_values.items() if v > 0}
    else:
        w_agg = {"SPY": 1.0}
        warnings.append("No current holdings — modeled the aggressive sleeve as 100% SPY.")
    w_def = {t.upper(): float(x) for t, x in (w_def or {}).items() if float(x) > 0}
    if not w_def:
        w_def = {"BIL": 1.0}
        warnings.append("No defensive mix available — modeled the safe sleeve as 100% BIL.")

    all_tickers = sorted(set(w_agg) | set(w_def))

    end = datetime.now().date()
    start = end - timedelta(days=int(lookback_years * 365))
    piv_norm = _price_frame(db, all_tickers, start.isoformat(), end.isoformat())
    if piv_norm is None or piv_norm.empty:
        warnings.append("No lookback price data — normal-regime returns are unavailable.")

    betas = _estimate_betas(db, all_tickers)
    piv_crash, _spy = _era_synthetic_piv(db, crash_era, all_tickers, betas)
    if piv_crash is None or piv_crash.empty:
        warnings.append(f"No price history for the {crash_era} crash era — crash returns are unavailable.")

    p_crash = crash_probability(composite_index, risk_band)

    ev_curve: List[Dict[str, Any]] = []
    for p in grid:
        w_target = {t: (1.0 - p) * w_agg.get(t, 0.0) + p * w_def.get(t, 0.0) for t in all_tickers}

        if piv_norm is not None and not piv_norm.empty:
            c_norm, _ = _simulate(w_target, 0.0, piv_norm)
            m_norm = _metrics_from_curve(c_norm)
            normal_return = _annualized(m_norm["total_return"], len(c_norm))
            normal_drawdown = m_norm["max_drawdown"]
        else:
            normal_return = normal_drawdown = 0.0

        if piv_crash is not None and not piv_crash.empty:
            c_crash, _ = _simulate(w_target, 0.0, piv_crash)
            m_crash = _metrics_from_curve(c_crash)
            crash_return = m_crash["total_return"]
            crash_drawdown = m_crash["max_drawdown"]
        else:
            crash_return = crash_drawdown = 0.0

        ev = (1.0 - p_crash) * normal_return + p_crash * crash_return
        # Expected log-growth (the optimization objective). Clamp 1+r above 0 so a ≤−100% scenario
        # is treated as ruin rather than a math error.
        g_norm = math.log(max(1e-6, 1.0 + normal_return / 100.0))
        g_crash = math.log(max(1e-6, 1.0 + crash_return / 100.0))
        growth = (1.0 - p_crash) * g_norm + p_crash * g_crash
        ev_curve.append({
            "participation_pct": p,
            "normal_return": round(normal_return, 2),
            "normal_drawdown": round(normal_drawdown, 2),
            "crash_return": round(crash_return, 2),
            "crash_drawdown": round(crash_drawdown, 2),
            "ev": round(ev, 2),                              # arithmetic expected return (display)
            "growth_score": round(growth, 5),               # expected log-growth (objective)
            "expected_geom_return": round((math.exp(growth) - 1.0) * 100.0, 2),
        })

    best = max(ev_curve, key=lambda r: r["growth_score"])
    hold = ev_curve[0]   # p = 0 (do nothing)
    full = ev_curve[-1]  # p = 1 (full de-risk)

    rationale = (
        f"At a {round(p_crash * 100)}% modeled crash probability ({risk_band or 'Unknown'} band), "
        f"the growth-optimal de-risk is {round(best['participation_pct'] * 100)}%. "
        f"Holding (0%) returns {hold['normal_return']}%/yr normally but {hold['crash_return']}% "
        f"through a {crash_era.upper()}-style crash; full de-risk (100%) returns "
        f"{full['normal_return']}%/yr normally and {full['crash_return']}% in that crash. "
        f"The pick maximizes expected log-growth, which penalizes the crash drawdown asymmetrically."
    )

    return {
        "suggested_participation_pct": best["participation_pct"],
        "p_crash": p_crash,
        "crash_era": crash_era,
        "lookback_years": lookback_years,
        "ev_curve": ev_curve,
        "rationale": rationale,
        "warnings": warnings,
    }
