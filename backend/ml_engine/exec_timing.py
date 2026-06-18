"""Execution-timing forward-walk: WHEN in the day should the swing strategy enter?

Reuses the look-ahead-free out-of-sample BUY signals from the swing walk-forward (`swing_oos_frame`), then
re-simulates entering each at different times of day (open … close, auto-detected from the intraday volume
profile so it's robust to the box's timezone) using hourly bars. For every entry time it reports two exits:
  • time-exit  — exit at the same time-of-day `horizon` trading days later (isolates the entry-timing effect)
  • bracket-exit — replay the live ATR stop/take-profit intraday on hourly highs/lows (what you'd actually get)
…as win-rate / average return / annualized Sharpe, and recommends a schedule slot (with the ET equivalent,
since the scheduler runs on Eastern Time).
"""
import numpy as np
from datetime import datetime
from sqlalchemy import func

from app.database import SessionLocal, RecentPrice
from app.core.config import (SWING_ATR_STOP_MULT, SWING_TP_MULT, SWING_STOP_MIN, SWING_STOP_MAX)

ENTRY_NAMES = ["open", "mid-morning", "midday", "afternoon", "close"]


def _detect_session(db, equities, oos_start):
    """Find the regular-session hour labels from the volume profile (open = first high-volume hour,
    close = last). Returns (open_hour:int, close_hour:int) in the data's local label hours."""
    q = db.query(func.substr(RecentPrice.date, 12, 2), func.avg(RecentPrice.volume))
    if equities:
        q = q.filter(RecentPrice.ticker.in_(equities))
    if oos_start:
        q = q.filter(RecentPrice.date >= oos_start)
    rows = [(int(h), float(v or 0)) for h, v in q.group_by(func.substr(RecentPrice.date, 12, 2)).all() if h]
    if not rows:
        return 6, 12
    peak = max(v for _, v in rows)
    session = sorted(h for h, v in rows if v >= 0.25 * peak)
    return (session[0], session[-1]) if session else (6, 12)


def _entry_hours(open_h, close_h):
    """Up to 5 entry-time buckets across the session, labeled open … close + their ET equivalents."""
    span = max(1, close_h - open_h)
    fracs = [0.0, 0.25, 0.5, 0.75, 1.0]
    out = []
    seen = set()
    for name, fr in zip(ENTRY_NAMES, fracs):
        h = int(round(open_h + fr * span))
        if h in seen:
            continue
        seen.add(h)
        # the open-hour bar ≈ 9:30 ET open; offset the rest by whole hours from there.
        et_min = 9 * 60 + 30 + (h - open_h) * 60
        et = f"{(et_min // 60) % 24:02d}:{et_min % 60:02d} ET"
        out.append({"name": name, "hour": h, "label": f"{h:02d}:00", "et": et})
    return out


def _load_bars(db, tickers):
    """{ticker: (dates_sorted, {date: {hour: (high, low, close)}})} from hourly recent_prices."""
    out = {}
    for r in db.query(RecentPrice).filter(RecentPrice.ticker.in_(list(tickers))).all():
        if len(r.date) <= 10 or r.close is None:
            continue
        d, hh = r.date[:10], int(r.date[11:13])
        t = out.setdefault(r.ticker, {})
        t.setdefault(d, {})[hh] = (r.high, r.low, r.close)
    return {tk: (sorted(days), days) for tk, days in out.items()}


def _stats(rets, horizon):
    if not rets:
        return {"n": 0, "win_rate": None, "avg_ret": None, "sharpe": None}
    a = np.array(rets, dtype=float)
    sharpe = float(a.mean() / a.std() * np.sqrt(252.0 / horizon)) if len(a) > 1 and a.std() > 0 else 0.0
    return {"n": len(a), "win_rate": round(float((a > 0).mean()), 3),
            "avg_ret": round(float(a.mean()), 4), "med_ret": round(float(np.median(a)), 4),
            "sharpe": round(sharpe, 2)}


def execution_timing_study(horizon=5, n_splits=4, oos_start="2022-01-01", progress_cb=None):
    from ml_engine.swing_alpha import swing_oos_frame
    if progress_cb:
        progress_cb(5, "Swing walk-forward (look-ahead-free signals)…")
    oos, _, equities = swing_oos_frame(horizon=horizon, n_splits=n_splits, oos_start=oos_start,
                                       progress_cb=lambda f: progress_cb(5 + int(f * 55), "Swing walk-forward…") if progress_cb else None)
    if oos is None or oos.empty:
        return {"error": "Not enough swing OOS data to study execution timing."}
    buys = oos[oos["prob"] >= oos["selected_threshold"]].copy()
    if buys.empty:
        return {"error": "No BUY signals in the OOS window."}

    if progress_cb:
        progress_cb(65, "Loading hourly bars + detecting session…")
    db = SessionLocal()
    try:
        open_h, close_h = _detect_session(db, equities, oos_start)
        entries = _entry_hours(open_h, close_h)
        bars = _load_bars(db, set(buys["ticker"]))
    finally:
        db.close()

    # per entry-time, per exit-method, collect realized trade returns
    acc = {e["name"]: {"time": [], "bracket": []} for e in entries}
    if progress_cb:
        progress_cb(75, "Simulating entries at each time of day…")
    for _, row in buys.iterrows():
        tk, d0 = row["ticker"], str(row["date"])[:10]
        atr, close = float(row["atr_14"] or 0), float(row["close"] or 0)
        if tk not in bars or close <= 0:
            continue
        dates_sorted, days = bars[tk]
        if d0 not in days:
            continue
        try:
            di = dates_sorted.index(d0)
        except ValueError:
            continue
        exit_di = di + horizon
        if exit_di >= len(dates_sorted):
            continue                       # forward window not closed
        d_exit = dates_sorted[exit_di]
        sl_pct = min(SWING_STOP_MAX, max(SWING_STOP_MIN, (SWING_ATR_STOP_MULT * atr) / close)) if atr else SWING_STOP_MIN
        tp_pct = sl_pct * SWING_TP_MULT

        for e in entries:
            hh = e["hour"]
            entry_bar = days[d0].get(hh)
            if not entry_bar:
                continue
            entry_px = entry_bar[2]                       # enter at that hour's close
            if entry_px <= 0:
                continue
            # time-based exit: same hour, `horizon` trading days later (fallback to last bar of exit day)
            ex = days[d_exit].get(hh) or days[d_exit].get(max(days[d_exit]))
            if ex:
                acc[e["name"]]["time"].append(ex[2] / entry_px - 1.0)
            # bracket exit: walk hourly bars from entry to the time-exit, first stop/TP hit wins
            tp, sl = entry_px * (1 + tp_pct), entry_px * (1 - sl_pct)
            ret = None
            for dd in dates_sorted[di:exit_di + 1]:
                for h2 in sorted(days[dd]):
                    if dd == d0 and h2 <= hh:
                        continue
                    if dd == d_exit and h2 > hh:
                        break
                    hi, lo, cl = days[dd][h2]
                    if hi is not None and hi >= tp:
                        ret = tp_pct; break
                    if lo is not None and lo <= sl:
                        ret = -sl_pct; break
                if ret is not None:
                    break
            if ret is None and ex:
                ret = ex[2] / entry_px - 1.0              # no barrier hit → exit at horizon
            if ret is not None:
                acc[e["name"]]["bracket"].append(ret)

    results = []
    for e in entries:
        results.append({**e,
                        "time_exit": _stats(acc[e["name"]]["time"], horizon),
                        "bracket_exit": _stats(acc[e["name"]]["bracket"], horizon)})

    def _best(method):
        ranked = [r for r in results if r[method]["n"] >= 30 and r[method]["sharpe"] is not None]
        return max(ranked, key=lambda r: r[method]["sharpe"]) if ranked else None
    rec_b, rec_t = _best("bracket_exit"), _best("time_exit")
    if progress_cb:
        progress_cb(100, "Complete")
    return {"horizon": horizon, "oos_start": oos_start, "n_signals": int(len(buys)),
            "session": {"open_hour": open_h, "close_hour": close_h},
            "entries": results,
            "recommended": {"bracket": rec_b["name"] if rec_b else None, "bracket_at": rec_b["et"] if rec_b else None,
                            "time": rec_t["name"] if rec_t else None, "time_at": rec_t["et"] if rec_t else None}}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Execution-timing forward-walk for the swing strategy")
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--splits", type=int, default=4)
    p.add_argument("--oos-start", default="2022-01-01")
    a = p.parse_args()
    r = execution_timing_study(horizon=a.horizon, n_splits=a.splits, oos_start=a.oos_start,
                               progress_cb=lambda pct, s: print(f"[{pct:3}%] {s}"))
    if "error" in r:
        print("ERROR:", r["error"]); raise SystemExit(1)
    print(f"\nExecution-timing study — {r['n_signals']} OOS BUY signals, horizon {r['horizon']}d, "
          f"OOS from {r['oos_start']} (session hours {r['session']['open_hour']:02d}:00–{r['session']['close_hour']:02d}:00 local)\n")
    hdr = f"{'entry':12} {'local':6} {'ET':9} | {'time-exit  n/win/avgret/Sharpe':32} | {'bracket-exit  n/win/avgret/Sharpe':34}"
    print(hdr); print("-" * len(hdr))
    for e in r["entries"]:
        t, b = e["time_exit"], e["bracket_exit"]
        def fmt(s):
            return f"{s['n']:4} {('%.0f%%'%(s['win_rate']*100)) if s['win_rate'] is not None else '  -':>4} {('%+.2f%%'%(s['avg_ret']*100)) if s['avg_ret'] is not None else '   -':>7} {('%.2f'%s['sharpe']) if s['sharpe'] is not None else '  -':>6}"
        print(f"{e['name']:12} {e['label']:6} {e['et']:9} | {fmt(t):32} | {fmt(b):34}")
    rec = r["recommended"]
    print(f"\nRecommended schedule slot — by realistic bracket-exit Sharpe: {rec['bracket']} (~{rec['bracket_at']}); "
          f"by clean time-exit Sharpe: {rec['time']} (~{rec['time_at']}).")
