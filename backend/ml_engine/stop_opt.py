"""Stop-loss / take-profit optimization forward-walk for the swing strategy.

The execution-timing study showed the live ATR brackets cut winners (bracket-exit Sharpe ~0.70 vs a clean
time-exit ~0.85). This sweeps the stop/TP parameters look-ahead-free: it reuses the OOS BUY signals from
`swing_oos_frame` once, then replays the EXACT live portfolio sim (`simulate_portfolio_chronological`) for
each (stop_max, atr_mult, tp_mult) combo and ranks by out-of-sample Sharpe (with max-drawdown shown).
Includes the current config as a baseline and a near-no-stop row (time/horizon exit) for reference.
"""
import itertools
import numpy as np

from app.core.config import (SWING_ATR_STOP_MULT, SWING_TP_MULT, SWING_STOP_MIN, SWING_STOP_MAX)

# Sweep grid — wider stops than today's 5% cap are explicitly included (the timing study suggested the
# current stops are too tight). Each combo is a fast portfolio sim over the pre-computed OOS signals.
ATR_MULTS = [1.5, 2.0, 2.5, 3.0]
TP_MULTS = [1.5, 2.0, 2.5, 3.0]
STOP_MAXES = [0.05, 0.08, 0.12]


def stop_tp_study(horizon=5, n_splits=5, oos_start="2022-01-01", progress_cb=None):
    from ml_engine.swing_alpha import swing_oos_frame
    from ml_engine.models import simulate_portfolio_chronological
    if progress_cb:
        progress_cb(5, "Swing walk-forward (look-ahead-free signals)…")
    oos, prices_df, _ = swing_oos_frame(horizon=horizon, n_splits=n_splits, oos_start=oos_start,
                                        progress_cb=lambda f: progress_cb(5 + int(f * 60), "Swing walk-forward…") if progress_cb else None)
    if oos is None or oos.empty:
        return {"error": "Not enough swing OOS data for the stop/TP study."}

    from ml_engine.models import compute_regime_series
    regime_by_date = compute_regime_series(oos_start)   # gate to match live execution

    def _sim(stop_max, stop_min, atr_mult, tp_mult):
        _, m = simulate_portfolio_chronological(oos, prices_df, horizon=horizon, stop_max=stop_max,
                                                stop_min=stop_min, atr_mult=atr_mult, tp_mult=tp_mult,
                                                regime_by_date=regime_by_date)
        return m or {}

    baseline_m = _sim(SWING_STOP_MAX, SWING_STOP_MIN, SWING_ATR_STOP_MULT, SWING_TP_MULT)
    baseline = {"stop_max": SWING_STOP_MAX, "stop_min": SWING_STOP_MIN, "atr_mult": SWING_ATR_STOP_MULT,
                "tp_mult": SWING_TP_MULT, **baseline_m, "is_baseline": True}

    combos = list(itertools.product(STOP_MAXES, ATR_MULTS, TP_MULTS))
    rows = []
    for i, (smax, am, tm) in enumerate(combos):
        m = _sim(smax, SWING_STOP_MIN, am, tm)
        rows.append({"stop_max": smax, "stop_min": SWING_STOP_MIN, "atr_mult": am, "tp_mult": tm, **m})
        if progress_cb and i % 5 == 0:
            progress_cb(70 + int(30 * i / len(combos)), f"Sweeping stop/TP ({i+1}/{len(combos)})…")

    # Near-no-stop reference: very wide stop so brackets ~never trigger → horizon/time exit behavior.
    noStop = {"stop_max": 0.50, "stop_min": 0.50, "atr_mult": 10.0, "tp_mult": 4.0,
              **_sim(0.50, 0.50, 10.0, 4.0), "label": "near-no-stop (time exit)"}

    ranked = sorted([r for r in rows if r.get("sharpe_ratio") is not None],
                    key=lambda r: r["sharpe_ratio"], reverse=True)
    best = ranked[0] if ranked else None
    if progress_cb:
        progress_cb(100, "Complete")
    return {"horizon": horizon, "oos_start": oos_start, "n_signals": int(len(oos)),
            "baseline": baseline, "no_stop": noStop, "ranked": ranked, "best": best}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Stop/TP optimization forward-walk for the swing strategy")
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--splits", type=int, default=5)
    p.add_argument("--oos-start", default="2022-01-01")
    p.add_argument("--top", type=int, default=10)
    a = p.parse_args()
    r = stop_tp_study(horizon=a.horizon, n_splits=a.splits, oos_start=a.oos_start,
                      progress_cb=lambda pct, s: print(f"[{pct:3}%] {s}"))
    if "error" in r:
        print("ERROR:", r["error"]); raise SystemExit(1)

    def line(tag, m):
        return (f"{tag:26} stop≤{m['stop_max']*100:>4.0f}% atr×{m['atr_mult']:<3} tp×{m['tp_mult']:<3} | "
                f"ret {m.get('total_return',0)*100:+6.1f}%  Sharpe {m.get('sharpe_ratio',0):>5.2f}  "
                f"maxDD {m.get('max_drawdown',0)*100:>6.1f}%")
    print(f"\nStop/TP study — {r['n_signals']} OOS signals, horizon {r['horizon']}d, OOS from {r['oos_start']}\n")
    print(line("CURRENT (baseline)", r["baseline"]))
    print(line("near-no-stop (ref)", r["no_stop"]))
    print("\nTop combos by OOS Sharpe:")
    print("-" * 92)
    for m in r["ranked"][:a.top]:
        print(line("", m))
    b = r["best"]
    print(f"\nBest: stop≤{b['stop_max']*100:.0f}% / atr×{b['atr_mult']} / tp×{b['tp_mult']} "
          f"→ Sharpe {b['sharpe_ratio']:.2f} (baseline {r['baseline'].get('sharpe_ratio',0):.2f}), "
          f"maxDD {b['max_drawdown']*100:.1f}%.")
