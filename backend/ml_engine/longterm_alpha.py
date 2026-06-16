"""Long-term (daily, multi-week horizon) test of whether insider-buying features add stock-selection
alpha — the horizon where SEC Form 4 buying is theoretically informative (weeks-to-months), unlike the
hourly short-term model where it was too laggy/sparse to help.

Experiment: build daily features (technicals + macro + insider) on `daily_prices`, label each (ticker, day)
by whether the stock's forward N-day return beats the cross-sectional median that day (market-neutral
selection), then run an expanding-window walk-forward comparing a model trained WITH vs WITHOUT the insider
features. Restricted to the insider-active window (2023-06→) so the comparison is meaningful.

Run with insider features populated:  ALT_DATA_ENABLED=True python ml_engine/longterm_alpha.py --horizon 21
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, DailyPrice, MacroIndicator, UniverseTicker
from app.core.config import TICKER_UNIVERSE, ALT_DATA_ENABLED

NON_EQUITY = {"SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP"}
ALT_FEATURES = ["feat_insider_net_flow", "feat_insider_buy_count", "feat_insider_net_buyers",
                "feat_insider_officer_buy", "feat_insider_cluster",
                "feat_congress_buying_ratio", "feat_congress_buying_90d"]


def load_daily_features():
    """Builds the full daily feature set (incl. insider when ALT_DATA_ENABLED). Returns (df, equities)."""
    from ml_engine.features import build_all_features
    db = SessionLocal()
    db_tickers = db.query(UniverseTicker).all()
    universe = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)
    equities = [t for t in universe if t not in NON_EQUITY and not t.startswith(("X:", "C:"))]
    # SPY/QQQ are needed for the cross-ticker (relative/beta) features even though we don't predict them.
    feat_universe = sorted(set(equities + ["SPY", "QQQ"]))

    prices = db.query(DailyPrice).filter(DailyPrice.ticker.in_(feat_universe)).all()
    macro = db.query(MacroIndicator).all()
    db.close()

    prices_df = pd.DataFrame([{
        "ticker": p.ticker, "date": p.date, "open": p.open, "high": p.high,
        "low": p.low, "close": p.close, "volume": p.volume,
        "sma_10": p.sma_10, "sma_50": p.sma_50, "rsi_14": p.rsi_14,
        "macd": p.macd, "macd_signal": p.macd_signal,
    } for p in prices])
    macro_df = pd.DataFrame([{"date": m.date, "indicator_name": m.indicator_name, "value": m.value}
                             for m in macro]) if macro else pd.DataFrame()

    full = build_all_features(prices_df, None, macro_df, feat_universe)
    return full, equities


def add_forward_target(df, equities, horizon_days):
    """Label = 1 if a stock's forward `horizon_days` return beats the equity cross-sectional median that
    day (market-neutral selection target). NaN where the forward window is censored."""
    df = df[df["ticker"].isin(equities)].copy()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    df = df.sort_values(["ticker", "dt"]).reset_index(drop=True)
    df["fwd_ret"] = df.groupby("ticker")["close"].transform(lambda c: c.shift(-horizon_days) / c - 1.0)
    df["xs_median"] = df.groupby("date")["fwd_ret"].transform("median")
    df["target_lt"] = (df["fwd_ret"] > df["xs_median"]).astype(float)
    df.loc[df["fwd_ret"].isna(), "target_lt"] = np.nan
    return df


def _fit_predict(tr, te, cols):
    m = xgb.XGBClassifier(n_estimators=120, max_depth=4, learning_rate=0.05,
                          subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
    m.fit(tr[cols], tr["target_lt"])
    return m.predict_proba(te[cols])[:, 1]


def walk_forward_longterm(horizon_days=21, n_splits=4, start_date="2023-06-16"):
    print(f"Loading daily features (ALT_DATA_ENABLED={ALT_DATA_ENABLED})...")
    if not ALT_DATA_ENABLED:
        print("WARNING: ALT_DATA_ENABLED is False — insider features will be 0. "
              "Re-run with ALT_DATA_ENABLED=True for a real with/without comparison.")
    df, equities = load_daily_features()
    df = add_forward_target(df, equities, horizon_days)

    feat_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    feat_no_alt = [c for c in feat_all if c not in ALT_FEATURES]
    has_alt = [c for c in ALT_FEATURES if c in feat_all]

    # Restrict to the insider-active window and drop censored/NaN rows.
    df = df[(df["date"] >= start_date)].dropna(subset=["target_lt"] + feat_all).copy()
    df = df.sort_values("dt").reset_index(drop=True)
    nz = {c: int((df[c].abs() > 0).sum()) for c in has_alt}
    print(f"Samples: {len(df)} | horizon: {horizon_days}d | window: {df['date'].min()}..{df['date'].max()}")
    print(f"Insider features present: {has_alt}")
    print(f"Non-zero insider feature cells: {nz}")

    edges = pd.date_range(df["dt"].min(), df["dt"].max(), periods=n_splits + 2)  # first chunk = warmup
    frames = {"all": [], "noalt": []}
    print(f"\n{'fold':>4} {'train':>7} {'test':>6} {'period':>23} | {'AUC+alt':>8} {'AUC-alt':>8} | "
          f"{'top-dec fwd ret +alt':>20} {'-alt':>9}")
    for i in range(1, n_splits + 1):
        lo, hi = edges[i], edges[i + 1]
        tr = df[df["dt"] < lo]
        te = df[(df["dt"] >= lo) & (df["dt"] < hi)]
        if len(tr) < 500 or len(te) < 100:
            continue
        p_all = _fit_predict(tr, te, feat_all)
        p_no = _fit_predict(tr, te, feat_no_alt)
        for key, p in (("all", p_all), ("noalt", p_no)):
            f = te[["dt", "ticker", "target_lt", "fwd_ret"]].copy()
            f["prob"] = p
            frames[key].append(f)
        auc_a = roc_auc_score(te["target_lt"], p_all) if te["target_lt"].nunique() > 1 else float("nan")
        auc_n = roc_auc_score(te["target_lt"], p_no) if te["target_lt"].nunique() > 1 else float("nan")
        # top-decile mean forward return (the economic selection metric)
        thr_a, thr_n = np.quantile(p_all, 0.9), np.quantile(p_no, 0.9)
        r_a = te["fwd_ret"][p_all >= thr_a].mean()
        r_n = te["fwd_ret"][p_no >= thr_n].mean()
        print(f"{i:>4} {len(tr):>7} {len(te):>6} {str(lo.date())+'..'+str(hi.date()):>23} | "
              f"{auc_a:>8.3f} {auc_n:>8.3f} | {r_a:>20.4f} {r_n:>9.4f}")

    if not frames["all"]:
        print("Not enough data for folds.")
        return

    def pooled(key):
        o = pd.concat(frames[key])
        y, p, r = o["target_lt"].values, o["prob"].values, o["fwd_ret"].values
        auc = roc_auc_score(y, p) if len(set(y)) > 1 else float("nan")
        out = {"auc": auc}
        for q in (0.05, 0.10, 0.20):
            m = p >= np.quantile(p, 1 - q)
            out[q] = (int(m.sum()), float(r[m].mean()))
        return out

    a, n = pooled("all"), pooled("noalt")
    base = pd.concat(frames["noalt"])["fwd_ret"].mean()
    print(f"\n--- Pooled OOS (horizon {horizon_days}d), top-decile mean forward return is the alpha metric ---")
    print(f"Universe-average forward {horizon_days}d return (baseline): {base:+.4f}")
    print(f"Pooled AUC:   WITH insider {a['auc']:.3f}   |   WITHOUT insider {n['auc']:.3f}")
    print(f"{'select':>8} | {'WITH insider (n, mean fwd ret)':>34} | {'WITHOUT insider':>28}")
    for q in (0.05, 0.10, 0.20):
        print(f"top {int(q*100):>3}% | {a[q][0]:>7} {a[q][1]:>+24.4f} | {n[q][0]:>7} {n[q][1]:>+18.4f}")
    print("\nInterpretation: if WITH-insider top-decile forward return > WITHOUT consistently, insider buying "
          "adds long-horizon selection alpha.\n")


def _portfolio_metrics(curve, dates):
    eq = pd.Series(curve, index=pd.to_datetime(dates))
    rets = eq.pct_change().dropna()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = eq.iloc[-1] / eq.iloc[0] - 1.0
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1.0 / yrs) - 1.0
    sharpe = (rets.mean() / (rets.std() + 1e-9)) * np.sqrt(252)
    dd = (eq - eq.cummax()) / eq.cummax()
    return total, cagr, sharpe, dd.min()


def backtest_longterm_tilt(tilt_strength=0.05, start_date="2022-01-01", window=252):
    """A/B backtest: monthly-rebalanced MPT portfolio WITH vs WITHOUT an insider-score tilt on expected
    returns, over the insider-active window. Also reports equal-weight and SPY benchmarks."""
    from ml_engine.models import PortfolioOptimizer
    print(f"Loading daily features (ALT_DATA_ENABLED={ALT_DATA_ENABLED})...")
    df, equities = load_daily_features()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    df = df.sort_values(["ticker", "dt"]).reset_index(drop=True)
    df["ret"] = df.groupby("ticker")["close"].pct_change()

    eq = df[df["ticker"].isin(equities)].copy()
    # BUY-SIDE conviction only: insider *selling* is mostly comp/diversification (insiders sell winners),
    # so net-flow penalizes exactly the momentum winners. The bullish view is insider BUYING.
    insf = [c for c in ["feat_insider_officer_buy", "feat_insider_buy_count", "feat_insider_cluster"]
            if c in eq.columns]
    for c in insf:
        eq[c + "_z"] = eq.groupby("date")[c].transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))
    eq["ins_score"] = eq[[c + "_z" for c in insf]].mean(axis=1).fillna(0.0)

    ret_p = eq.pivot_table(index="date", columns="ticker", values="ret")
    score_p = eq.pivot_table(index="date", columns="ticker", values="ins_score")
    spy_ret = df[df["ticker"] == "SPY"].set_index("date")["ret"]

    all_dates = sorted(ret_p.index)
    seen, rebal = set(), set()
    for d in all_dates:
        if d[:7] not in seen:
            seen.add(d[:7]); rebal.add(d)
    sim_dates = [d for d in all_dates if d >= start_date]
    print(f"Backtest {sim_dates[0]}..{sim_dates[-1]} | {len(equities)} equities | "
          f"tilt_strength={tilt_strength} | monthly rebalance")

    def run(arm):
        port, weights, curve = 1.0, {}, []
        for d in sim_dates:
            row = ret_p.loc[d]
            dr = sum(w * (row[t] if (t in row and pd.notna(row[t])) else 0.0) for t, w in weights.items())
            port *= (1.0 + dr)
            curve.append(port)
            if d in rebal:
                hist = ret_p[ret_p.index < d].tail(window).dropna(axis=1, how="any")
                cols = list(hist.columns)
                if len(hist) > 100 and len(cols) >= 2:
                    tilt = None
                    if arm in ("tilt", "ew") and arm == "tilt":
                        sr = score_p.loc[d] if d in score_p.index else None
                        tilt = {t: tilt_strength * (float(sr[t]) if (sr is not None and pd.notna(sr[t])) else 0.0)
                                for t in cols}
                    if arm == "ew":
                        weights = {t: 1.0 / len(cols) for t in cols}
                    else:
                        weights = PortfolioOptimizer.calculate_optimal_weights(hist, "growth", expected_return_tilt=tilt)
        return curve

    curves = {a: run(a) for a in ("tilt", "base", "ew")}
    spy_curve, sp = [], 1.0
    for d in sim_dates:
        sp *= (1.0 + (spy_ret[d] if (d in spy_ret and pd.notna(spy_ret[d])) else 0.0))
        spy_curve.append(sp)

    print(f"\n--- Long-term MPT backtest ({sim_dates[0]}..{sim_dates[-1]}) ---")
    print(f"{'strategy':<22} {'total':>9} {'CAGR':>8} {'Sharpe':>8} {'maxDD':>9}")
    for name, key in [("MPT + insider tilt", "tilt"), ("MPT (base)", "base"),
                      ("Equal-weight", "ew"), ("SPY (buy-hold)", None)]:
        c = spy_curve if key is None else curves[key]
        t, g, s, dd = _portfolio_metrics(c, sim_dates)
        print(f"{name:<22} {t*100:>8.1f}% {g*100:>7.1f}% {s:>8.2f} {dd*100:>8.1f}%")
    print("\nInterpretation: tilt helps iff it beats the base MPT on Sharpe / drawdown-adjusted return.\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Long-term insider-alpha tests")
    ap.add_argument("--horizon", type=int, default=21, help="forward return horizon in trading days")
    ap.add_argument("--splits", type=int, default=4)
    ap.add_argument("--start", type=str, default="2023-06-16", help="insider-active window start")
    ap.add_argument("--backtest-tilt", action="store_true", help="run the MPT insider-tilt A/B backtest")
    ap.add_argument("--tilt-strength", type=float, default=0.05, help="annualized return per unit insider z-score")
    a = ap.parse_args()
    if a.backtest_tilt:
        backtest_longterm_tilt(tilt_strength=a.tilt_strength, start_date=a.start)
    else:
        walk_forward_longterm(horizon_days=a.horizon, n_splits=a.splits, start_date=a.start)
