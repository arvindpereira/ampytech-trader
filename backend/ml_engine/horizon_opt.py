"""Holding-horizon optimization forward-walk for the swing strategy.

Since the tuned strategy exits mostly at the holding horizon, how long we hold is the key remaining knob.
This sweeps SWING_HORIZON look-ahead-free: for each candidate horizon it runs a full walk-forward
(`swing_oos_frame` — the triple-barrier labels AND the hold length both change with the horizon) and
replays the live portfolio sim with the CURRENT tuned stop/TP config, ranking by out-of-sample Sharpe
(max-drawdown shown). The current horizon is marked as the baseline.
"""
from app.core.config import (SWING_HORIZON_DAYS, SWING_ATR_STOP_MULT, SWING_TP_MULT,
                             SWING_STOP_MIN, SWING_STOP_MAX)

HORIZONS = [3, 5, 7, 10, 15]


def horizon_study(horizons=None, n_splits=5, oos_start="2022-01-01", progress_cb=None):
    from ml_engine.swing_alpha import swing_oos_frame
    from ml_engine.models import simulate_portfolio_chronological, compute_regime_series
    horizons = horizons or HORIZONS
    regime_by_date = compute_regime_series(oos_start)   # gate to match live execution (regime depends on oos_start, not horizon)
    rows = []
    for i, h in enumerate(horizons):
        if progress_cb:
            progress_cb(int(100 * i / len(horizons)), f"Walk-forward @ horizon {h}d ({i+1}/{len(horizons)})…")
        oos, prices_df, _ = swing_oos_frame(horizon=h, n_splits=n_splits, oos_start=oos_start)
        if oos is None or oos.empty:
            rows.append({"horizon": h, "n": 0})
            continue
        _, m = simulate_portfolio_chronological(oos, prices_df, horizon=h, stop_max=SWING_STOP_MAX,
                                                stop_min=SWING_STOP_MIN, atr_mult=SWING_ATR_STOP_MULT,
                                                tp_mult=SWING_TP_MULT, regime_by_date=regime_by_date)
        rows.append({"horizon": h, "n": int(len(oos)), **(m or {})})
    if progress_cb:
        progress_cb(100, "Complete")
    ranked = sorted([r for r in rows if r.get("sharpe_ratio") is not None],
                    key=lambda r: r["sharpe_ratio"], reverse=True)
    return {"oos_start": oos_start, "n_splits": n_splits, "baseline_horizon": SWING_HORIZON_DAYS,
            "rows": rows, "ranked": ranked, "best": ranked[0] if ranked else None}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Holding-horizon optimization forward-walk for the swing strategy")
    p.add_argument("--horizons", default=",".join(map(str, HORIZONS)), help="comma-separated trading-day horizons")
    p.add_argument("--splits", type=int, default=5)
    p.add_argument("--oos-start", default="2022-01-01")
    a = p.parse_args()
    hs = [int(x) for x in a.horizons.split(",") if x.strip()]
    r = horizon_study(horizons=hs, n_splits=a.splits, oos_start=a.oos_start,
                      progress_cb=lambda pct, s: print(f"[{pct:3}%] {s}"))
    print(f"\nHolding-horizon study — OOS from {r['oos_start']}, {r['n_splits']} folds, "
          f"tuned stops (≤{SWING_STOP_MAX*100:.0f}% / tp×{SWING_TP_MULT})\n")
    print(f"{'horizon':9} {'signals':>8} | {'return':>9} {'Sharpe':>7} {'maxDD':>8}")
    print("-" * 50)
    for m in sorted(r["rows"], key=lambda x: x["horizon"]):
        tag = "  ← current" if m["horizon"] == r["baseline_horizon"] else ""
        if m.get("sharpe_ratio") is None:
            print(f"{m['horizon']:>5}d   {'(no data)':>8}{tag}")
            continue
        print(f"{m['horizon']:>5}d   {m['n']:>8} | {m.get('total_return',0)*100:>+8.1f}% "
              f"{m.get('sharpe_ratio',0):>7.2f} {m.get('max_drawdown',0)*100:>7.1f}%{tag}")
    b = r["best"]
    if b:
        print(f"\nBest: {b['horizon']}d hold → Sharpe {b['sharpe_ratio']:.2f} "
              f"(current {r['baseline_horizon']}d), return {b.get('total_return',0)*100:+.1f}%, "
              f"maxDD {b.get('max_drawdown',0)*100:.1f}%.")
