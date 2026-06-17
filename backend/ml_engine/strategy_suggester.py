"""Strategy suggester (v1): per-ticker recommendation of Swing+News vs Long-term MPT vs Hold.

Evidence-driven and transparent (see docs/strategy-suggester-plan.md). For each equity it measures:
  1. Swing OOS edge — expectancy/win-rate of the swing model's above-threshold signals on that ticker,
     out-of-sample (walk-forward, default oos_start so the 2022 bear is in the test set).
  2. News responsiveness — news volume + correlation of the daily relevance-weighted LLM news score
     with the next-`horizon`-day return.
  3. Long-term quality — trailing Sharpe, max drawdown, 12-month momentum.
  4. Bear behavior — the ticker's 2022 return / drawdown.
Then a conservative rubric maps each ticker to swing | longterm | hold with a confidence + rationale.
Defaults bias AWAY from swing under weak evidence (swing is the documented downside risk).
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_SWING_TRADES = 12          # minimum OOS above-threshold signals to consider swing for a ticker
SWING_MARGIN = 0.40            # swing must beat longterm by this many std to be recommended
LT_BAR = -0.40                 # longterm score floor (in std) below which we'd rather Hold


def _zscore(s):
    s = pd.Series(s, dtype=float)
    sd = s.std()
    return (s - s.mean()) / (sd + 1e-9) if sd > 0 else s * 0.0


def suggest_strategies(horizon=5, n_splits=5, oos_start="2022-01-01", progress_cb=None):
    def report(p, note):
        if progress_cb:
            progress_cb(int(p), note)

    from ml_engine.swing_alpha import swing_oos_frame, load_llm_news_daily, NON_EQUITY

    report(8, "Swing walk-forward (per-ticker OOS signals)…")
    oos, prices_df, equities = swing_oos_frame(
        horizon=horizon, n_splits=n_splits, oos_start=oos_start,
        progress_cb=lambda f: report(8 + int(f * 52), "Swing walk-forward (per-ticker OOS signals)…"))
    if oos is None or oos.empty or prices_df is None or prices_df.empty:
        report(100, "No data")
        return {"suggestions": [], "oos_start": oos_start, "note": "Not enough scored data to evaluate."}

    # --- 1. Swing OOS edge per ticker (above-threshold signals) ---
    sig = oos[oos["prob"] >= oos["selected_threshold"]].copy()
    sig["dt"] = pd.to_datetime(sig["date"], format="mixed")
    swing = {}
    for tk, g in sig.groupby("ticker"):
        n = len(g)
        win = float((g["trade_ret"] > 0).mean()) if n else 0.0
        mean_ret = float(g["trade_ret"].mean()) if n else 0.0
        g22 = g[(g["dt"] >= "2022-01-01") & (g["dt"] <= "2022-12-31")]
        ret22 = float(g22["trade_ret"].mean()) if len(g22) else None
        swing[tk] = {"n_trades": int(n), "win_rate": round(win, 3),
                     "mean_ret": round(mean_ret, 4), "ret_2022": round(ret22, 4) if ret22 is not None else None}

    report(66, "News responsiveness…")
    # --- 2. News responsiveness per ticker ---
    llm = load_llm_news_daily()  # ticker,date,wsum,relsum,n
    news = {}
    px = prices_df.copy()
    px["dt"] = pd.to_datetime(px["date"], format="mixed")
    px = px.sort_values(["ticker", "dt"])
    for tk, g in px.groupby("ticker"):
        g = g.reset_index(drop=True)
        g["fwd"] = g["close"].shift(-horizon) / g["close"] - 1.0
        vol, corr = 0, None
        if llm is not None and not llm.empty:
            lt = llm[llm["ticker"] == tk]
            if not lt.empty:
                lt = lt.assign(score=lt["wsum"] / (lt["relsum"] + 1e-9))
                m = g.merge(lt[["date", "score"]], on="date", how="inner")
                vol = int(lt["n"].sum())
                m = m.dropna(subset=["fwd", "score"])
                if len(m) >= 30 and m["score"].std() > 0 and m["fwd"].std() > 0:
                    corr = float(np.corrcoef(m["score"], m["fwd"])[0, 1])
        news[tk] = {"volume": vol, "corr": round(corr, 3) if corr is not None else None}

    report(82, "Long-term quality + bear behavior…")
    # --- 3 & 4. Long-term quality + 2022 bear behavior per ticker ---
    lt_q, bear = {}, {}
    oos_dt = pd.to_datetime(oos_start)
    for tk, g in px.groupby("ticker"):
        g = g.sort_values("dt")
        w = g[g["dt"] >= oos_dt]
        r = w["close"].pct_change().dropna()
        sharpe = float((r.mean() / (r.std() + 1e-9)) * np.sqrt(252)) if len(r) > 5 else 0.0
        eq = w["close"]
        maxdd = float(((eq - eq.cummax()) / eq.cummax()).min()) if len(eq) > 1 else 0.0
        mom = float(eq.iloc[-1] / eq.iloc[0] - 1.0) if len(eq) > 1 else 0.0
        lt_q[tk] = {"sharpe": round(sharpe, 2), "max_dd": round(maxdd, 3), "momentum": round(mom, 3)}
        b = g[(g["dt"] >= "2022-01-01") & (g["dt"] <= "2022-12-31")]["close"]
        if len(b) > 1:
            bear[tk] = {"ret": round(float(b.iloc[-1] / b.iloc[0] - 1.0), 3),
                        "dd": round(float(((b - b.cummax()) / b.cummax()).min()), 3)}
        else:
            bear[tk] = {"ret": None, "dd": None}

    report(90, "Scoring + recommendations…")
    eq_list = [t for t in equities if t not in NON_EQUITY]
    # cross-sectional z-scores
    swing_exp = _zscore([swing.get(t, {}).get("mean_ret", 0.0) for t in eq_list])
    swing_win = _zscore([swing.get(t, {}).get("win_rate", 0.0) for t in eq_list])
    news_corr = _zscore([(news.get(t, {}).get("corr") or 0.0) for t in eq_list])
    lt_sharpe = _zscore([lt_q.get(t, {}).get("sharpe", 0.0) for t in eq_list])
    lt_dd = _zscore([-(lt_q.get(t, {}).get("max_dd", 0.0)) for t in eq_list])   # less negative DD = better
    lt_mom = _zscore([lt_q.get(t, {}).get("momentum", 0.0) for t in eq_list])

    swing_score = (0.5 * swing_exp + 0.3 * swing_win + 0.2 * news_corr)
    longterm_score = (0.45 * lt_sharpe + 0.35 * lt_dd + 0.20 * lt_mom)

    out = []
    for i, tk in enumerate(eq_list):
        sw, ns, lq, br = swing.get(tk, {}), news.get(tk, {}), lt_q.get(tk, {}), bear.get(tk, {})
        ss, ls = float(swing_score.iloc[i]), float(longterm_score.iloc[i])
        n_tr = sw.get("n_trades", 0)
        # eligibility: enough OOS signals AND positive expectancy AND didn't badly amplify the 2022 bear
        bad_2022 = (sw.get("ret_2022") is not None and sw["ret_2022"] < -0.03)
        swing_eligible = (n_tr >= MIN_SWING_TRADES) and (sw.get("mean_ret", 0.0) > 0) and not bad_2022
        if swing_eligible and ss > ls + SWING_MARGIN:
            rec = "swing"
        elif ls > LT_BAR:
            rec = "longterm"
        else:
            rec = "hold"

        margin = abs(ss - ls)
        conf = "high" if (n_tr >= 30 and margin > 1.0) else ("low" if (n_tr < MIN_SWING_TRADES or margin < 0.4) else "medium")

        bits = []
        if n_tr:
            bits.append(f"{n_tr} swing OOS signals, {sw['win_rate']*100:.0f}% win, {sw['mean_ret']*100:+.1f}%/trade")
        else:
            bits.append("no swing OOS signals")
        if ns.get("corr") is not None:
            bits.append(f"news corr {ns['corr']:+.2f} ({ns.get('volume',0)} articles)")
        if br.get("ret") is not None:
            bits.append(f"2022 {br['ret']*100:+.0f}%")
        bits.append(f"trailing Sharpe {lq.get('sharpe',0):.2f}, maxDD {lq.get('max_dd',0)*100:.0f}%")
        rationale = "; ".join(bits) + f" → {rec}"

        out.append({
            "ticker": tk, "recommended": rec, "confidence": conf,
            "swing_score": round(ss, 2), "longterm_score": round(ls, 2),
            "swing": sw, "news": ns, "longterm": lq, "bear_2022": br, "rationale": rationale,
        })

    order = {"swing": 0, "longterm": 1, "hold": 2}
    out.sort(key=lambda x: (order.get(x["recommended"], 3), -x["swing_score"]))
    report(100, "Complete")
    return {"suggestions": out, "oos_start": oos_start,
            "counts": {k: sum(1 for o in out if o["recommended"] == k) for k in ("swing", "longterm", "hold")}}
