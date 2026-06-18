"""Swing (multi-day) strategy evaluator with LLM-scored news features.

Unlike the hourly short-term model (which loses money at the portfolio level), this trades a DAILY,
multi-day horizon where news-driven *drift* is capturable by a retail bot. The key new feature is the
LLM-scored, relevance-weighted news sentiment from `news_llm_scores` (see data_ingestion/news_llm.py).

It reuses the existing honest harness — triple-barrier labels, per-fold nested threshold
(`find_optimal_threshold`), and the capital-aware portfolio simulation
(`simulate_portfolio_chronological`) — and compares a model trained WITH vs WITHOUT the LLM-news features.
Verdict = portfolio-level return/Sharpe, not per-trade expectancy.

Run (with news scored):  python ml_engine/swing_alpha.py --horizon 5 --splits 4
"""
import os
import sys
import json
import pickle
import argparse
from datetime import datetime
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, DailyPrice, MacroIndicator, UniverseTicker, NewsLLMScore
from app.core.config import (
    TICKER_UNIVERSE, SWING_HORIZON_DAYS,
    SWING_ATR_STOP_MULT, SWING_TP_MULT, SWING_STOP_MIN, SWING_STOP_MAX, SWING_TOP_N,
)
from ml_engine.features import build_all_features
from ml_engine.models import find_optimal_threshold, simulate_portfolio_chronological

NON_EQUITY = {"SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP", "SPACE"}
LLM_FEATURES = ["feat_llm_news", "feat_llm_news_intensity", "feat_llm_news_today"]

SAVED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_models")
SWING_MODEL_PATH = os.path.join(SAVED_DIR, "swing_model.json")             # CORE model (primary book)
SWING_META_PATH = os.path.join(SAVED_DIR, "swing_metadata.pkl")
SWING_AGG_MODEL_PATH = os.path.join(SAVED_DIR, "swing_aggressive_model.json")   # AGGRESSIVE (high-risk bucket)
SWING_AGG_META_PATH = os.path.join(SAVED_DIR, "swing_aggressive_metadata.pkl")

# Tiers (ticker_classification.tier) the CORE model is allowed to train/trade on. Speculative +
# value_trap are excluded from core (they go to the aggressive model's small high-risk bucket).
CORE_TIERS = ("core", "quality_growth")
HIGH_RISK_TIERS = ("speculative",)   # value_trap is avoided entirely (not even traded aggressively)


def tickers_for_tiers(tiers, include_unrated=False):
    """Universe tickers whose classification tier is in `tiers`. `include_unrated` also returns tickers
    with no classification yet (no fundamentals, e.g. foreign ADRs) — folded into the core book."""
    from app.database import TickerClassification, UniverseTicker
    db = SessionLocal()
    try:
        cls = {c.ticker: c.tier for c in db.query(TickerClassification).all()}
        uni = [t.ticker for t in db.query(UniverseTicker).all()]
    finally:
        db.close()
    out = [t for t in uni if cls.get(t) in tiers]
    if include_unrated:
        out += [t for t in uni if t not in cls]
    return sorted(set(out))


def load_llm_news_daily(exclude_premium=False):
    """Per (ticker, date) relevance-weighted news aggregates from the LLM scores.
    `exclude_premium` drops premium-newsletter rows (source like 'premium:%') for A/B comparisons."""
    db = SessionLocal()
    q = db.query(NewsLLMScore)
    if exclude_premium:
        q = q.filter((NewsLLMScore.source.is_(None)) | (~NewsLLMScore.source.like("premium:%")))
    rows = q.all()
    db.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([{"ticker": r.ticker, "date": r.date,
                        "s": r.llm_score, "rel": r.llm_relevance} for r in rows])
    df = df[(df["date"].astype(str).str.len() == 10)]
    df["wscore"] = df["s"] * df["rel"]
    g = df.groupby(["ticker", "date"]).agg(wsum=("wscore", "sum"),
                                           relsum=("rel", "sum"),
                                           n=("s", "size")).reset_index()
    return g


def add_llm_features(feat_df, llm_daily, decay_days=3):
    """Adds point-in-time LLM-news features: a `decay_days`-day relevance-weighted mean score, an
    intensity (how much material news), and today's score. Shifted +1 day to stay look-ahead free."""
    for c in LLM_FEATURES:
        feat_df[c] = 0.0
    if llm_daily is None or llm_daily.empty:
        return feat_df, None

    df = feat_df.merge(llm_daily.rename(columns={"date": "cal_date"}), on=["ticker", "cal_date"], how="left")
    for c in ["wsum", "relsum", "n"]:
        df[c] = df[c].fillna(0.0)
    df = df.sort_values(["ticker", "cal_date"])

    def per_ticker(grp):
        w = grp["wsum"].rolling(decay_days, min_periods=1).sum()
        r = grp["relsum"].rolling(decay_days, min_periods=1).sum()
        grp["feat_llm_news"] = (w / (r + 1e-9)).shift(1).fillna(0.0)                  # decayed weighted mean
        grp["feat_llm_news_intensity"] = r.shift(1).fillna(0.0)                        # material-news volume
        grp["feat_llm_news_today"] = (grp["wsum"] / (grp["relsum"] + 1e-9)).shift(1).fillna(0.0)
        return grp

    df = df.groupby("ticker", group_keys=False).apply(per_ticker)
    first_llm_date = llm_daily["date"].min()
    return df, first_llm_date


def load_swing_data(horizon=5, exclude_premium=False):
    """Daily features (technicals + macro) + LLM-news features + a `horizon`-day triple-barrier target."""
    db = SessionLocal()
    db_tickers = db.query(UniverseTicker).all()
    universe = [t.ticker for t in db_tickers] if db_tickers else list(TICKER_UNIVERSE)
    equities = [t for t in universe if t not in NON_EQUITY and not t.startswith(("X:", "C:"))]
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

    full = build_all_features(prices_df, None, macro_df, feat_universe, target_horizon_bars=horizon)
    full, first_llm_date = add_llm_features(full, load_llm_news_daily(exclude_premium=exclude_premium))
    return full, equities, prices_df, first_llm_date


def walk_forward_swing(horizon=5, n_splits=4, warmup_frac=0.4):
    print("Loading daily swing features + LLM news...")
    full, equities, prices_df, first_llm_date = load_swing_data(horizon)
    df = full[full["ticker"].isin(equities)].dropna(subset=["target_win", "trade_ret"]).copy()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")

    feat_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    feat_no_llm = [c for c in feat_all if c not in LLM_FEATURES]
    has_llm = [c for c in LLM_FEATURES if c in feat_all]
    df = df.dropna(subset=feat_all)

    # Restrict to the LLM-active window so WITH vs WITHOUT is a real comparison.
    if first_llm_date:
        df = df[df["date"] >= first_llm_date]
    df = df.sort_values("dt").reset_index(drop=True)
    nz = int((df[has_llm].abs().sum(axis=1) > 0).sum()) if has_llm else 0
    print(f"Samples: {len(df)} | horizon: {horizon}d | window: {df['date'].min()}..{df['date'].max()}")
    print(f"LLM features: {has_llm} | rows with any LLM signal: {nz} ({100*nz/max(len(df),1):.1f}%)")
    if len(df) < 1000:
        print("Not enough scored data yet — re-run after the news-llm backfill has more coverage.")
        return

    edges = pd.date_range(df["dt"].min() + (df["dt"].max() - df["dt"].min()) * warmup_frac,
                          df["dt"].max(), periods=n_splits + 1)
    frames = {"llm": [], "base": []}
    print(f"\n{'fold':>4} {'train':>7} {'test':>6} {'period':>23} | {'AUC+LLM':>8} {'AUC base':>8}")
    for i in range(n_splits):
        lo, hi = edges[i], edges[i + 1]
        tr = df[df["dt"] < lo]
        te = df[(df["dt"] >= lo) & (df["dt"] < hi)]
        if len(tr) < 500 or len(te) < 100:
            continue
        w = np.exp(-((tr["dt"].max() - tr["dt"]).dt.days) / (5.0 * 365.25))
        for key, cols in (("llm", feat_all), ("base", feat_no_llm)):
            m = xgb.XGBClassifier(n_estimators=120, max_depth=4, learning_rate=0.05,
                                  subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
            m.fit(tr[cols], tr["target_win"], sample_weight=w)
            p = m.predict_proba(te[cols])[:, 1]
            thr = find_optimal_threshold(tr, cols, target_col="target_win", fallback_default=0.2)
            f = te[["date", "ticker", "close", "atr_14", "target_win", "trade_ret"]].copy()
            f["prob"] = p
            f["selected_threshold"] = thr
            frames[key].append(f)
        auc_l = roc_auc_score(te["target_win"], frames["llm"][-1]["prob"]) if te["target_win"].nunique() > 1 else float("nan")
        auc_b = roc_auc_score(te["target_win"], frames["base"][-1]["prob"]) if te["target_win"].nunique() > 1 else float("nan")
        print(f"{i:>4} {len(tr):>7} {len(te):>6} {str(lo.date())+'..'+str(hi.date()):>23} | {auc_l:>8.3f} {auc_b:>8.3f}")

    if not frames["llm"]:
        print("Not enough data for folds.")
        return

    print("\nRunning capital-aware portfolio simulations (the honest verdict)...")
    print(f"{'Metric':<16} | {'WITH LLM news':>15} | {'WITHOUT (base)':>15}")
    print("-" * 54)
    out = {}
    for key, label in (("llm", "WITH LLM news"), ("base", "WITHOUT (base)")):
        oos = pd.concat(frames[key]).sort_values("date")
        try:
            auc = roc_auc_score(oos["target_win"], oos["prob"])
        except ValueError:
            auc = float("nan")
        _, metrics = simulate_portfolio_chronological(oos, prices_df, initial_capital=100000.0,
                                                      max_allocation=0.10, fee_pct=0.0005, horizon=horizon,
                                                      stop_max=SWING_STOP_MAX, stop_min=SWING_STOP_MIN,
                                                      atr_mult=SWING_ATR_STOP_MULT, tp_mult=SWING_TP_MULT)
        out[key] = (auc, metrics)
    for name, idx in (("Pooled AUC", None), ("Total Return", "total_return"),
                      ("Sharpe", "sharpe_ratio"), ("Max Drawdown", "max_drawdown"),
                      ("Final Value", "final_value")):
        if idx is None:
            print(f"{name:<16} | {out['llm'][0]:>15.3f} | {out['base'][0]:>15.3f}")
        elif idx == "final_value":
            print(f"{name:<16} | ${out['llm'][1].get(idx,0):>14,.0f} | ${out['base'][1].get(idx,0):>14,.0f}")
        else:
            mult = 100 if idx != "sharpe_ratio" else 1
            unit = "%" if idx != "sharpe_ratio" else ""
            print(f"{name:<16} | {out['llm'][1].get(idx,0)*mult:>14.2f}{unit} | {out['base'][1].get(idx,0)*mult:>14.2f}{unit}")
    print("\nVerdict: LLM news helps iff WITH beats WITHOUT on portfolio Sharpe / return.\n")


def swing_oos_frame(horizon=5, n_splits=4, warmup_frac=0.4, oos_start=None, progress_cb=None,
                    exclude_premium=False, allowed_tickers=None):
    """Walk-forward → the pooled out-of-sample signal frame, shared by the backtest and the suggester.

    Returns (oos_df, prices_df, equities) where oos_df has [date, ticker, close, atr_14, target_win,
    trade_ret, prob, selected_threshold] — every held-out prediction. `oos_start` (YYYY-MM-DD) fixes
    where OOS testing begins (first fold trains only before it); else it starts after `warmup_frac`."""
    full, equities, prices_df, first_llm_date = load_swing_data(horizon, exclude_premium=exclude_premium)
    if allowed_tickers is not None:
        equities = [t for t in equities if t in set(allowed_tickers)]
    df = full[full["ticker"].isin(equities)].dropna(subset=["target_win", "trade_ret"]).copy()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    feat_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    df = df.dropna(subset=feat_all)
    if first_llm_date:
        df = df[df["date"] >= first_llm_date]
    df = df.sort_values("dt").reset_index(drop=True)
    if len(df) < 1000:
        return None, prices_df, equities

    first_edge = (pd.to_datetime(oos_start) if oos_start
                  else df["dt"].min() + (df["dt"].max() - df["dt"].min()) * warmup_frac)
    edges = pd.date_range(first_edge, df["dt"].max(), periods=n_splits + 1)
    frames = []
    for i in range(n_splits):
        lo, hi = edges[i], edges[i + 1]
        tr = df[df["dt"] < lo]
        te = df[(df["dt"] >= lo) & (df["dt"] < hi)]
        if len(tr) < 500 or len(te) < 100:
            continue
        w = np.exp(-((tr["dt"].max() - tr["dt"]).dt.days) / (5.0 * 365.25))
        m = xgb.XGBClassifier(n_estimators=120, max_depth=4, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
        m.fit(tr[feat_all], tr["target_win"], sample_weight=w)
        p = m.predict_proba(te[feat_all])[:, 1]
        thr = find_optimal_threshold(tr, feat_all, target_col="target_win", fallback_default=0.2)
        f = te[["date", "ticker", "close", "atr_14", "target_win", "trade_ret"]].copy()
        f["prob"] = p
        f["selected_threshold"] = thr
        frames.append(f)
        if progress_cb:
            progress_cb((i + 1) / n_splits)
    if not frames:
        return None, prices_df, equities
    return pd.concat(frames).sort_values("date"), prices_df, equities


def backtest_swing_curve(horizon=5, n_splits=4, warmup_frac=0.4, oos_start=None, progress_cb=None,
                         exclude_premium=False, regime_gated=True, allowed_tickers=None):
    """Walk-forward, look-ahead-free swing backtest → (dated equity_curve, metrics).
    `regime_gated` applies the live regime overlay (crisis-shrink) so the backtest matches execution.
    `allowed_tickers` restricts the tradable set (e.g. speculative names for the aggressive sleeve)."""
    oos, prices_df, _ = swing_oos_frame(horizon, n_splits, warmup_frac, oos_start, progress_cb,
                                        exclude_premium=exclude_premium, allowed_tickers=allowed_tickers)
    if oos is None or oos.empty:
        return [], {}
    from ml_engine.models import compute_regime_series
    regime_by_date = compute_regime_series(oos_start) if regime_gated else None
    curve, metrics = simulate_portfolio_chronological(oos, prices_df, horizon=horizon,
                                                      stop_max=SWING_STOP_MAX, stop_min=SWING_STOP_MIN,
                                                      atr_mult=SWING_ATR_STOP_MULT, tp_mult=SWING_TP_MULT,
                                                      regime_by_date=regime_by_date)
    return curve, metrics


def train_and_save(horizon=None, allowed_tickers=None, model_path=SWING_MODEL_PATH,
                   meta_path=SWING_META_PATH, label="core"):
    """Train a swing model on the LLM-active window and persist it. `allowed_tickers` restricts the
    TRAINING universe (None = all equities) — used to train the CORE model on core+quality_growth names
    only, vs the AGGRESSIVE model on the full universe. Metadata records the columns / threshold / horizon
    / allowed tickers so inference reproduces training exactly."""
    horizon = horizon or SWING_HORIZON_DAYS
    full, equities, prices_df, first_llm_date = load_swing_data(horizon)
    universe = [t for t in equities if (allowed_tickers is None or t in set(allowed_tickers))]
    print(f"Training {label} swing model (horizon={horizon}d) on {len(universe)} tickers...")
    df = full[full["ticker"].isin(universe)].dropna(subset=["target_win", "trade_ret"]).copy()
    df["dt"] = pd.to_datetime(df["date"], format="mixed")
    feat_all = sorted([c for c in df.columns if c.startswith("feat_") and c != "feat_atr_14"])
    df = df.dropna(subset=feat_all)
    if first_llm_date:
        df = df[df["date"] >= first_llm_date]
    df = df.sort_values("dt").reset_index(drop=True)
    if len(df) < 1000:
        print(f"Only {len(df)} samples for {label} — score more news first (make news-llm).")
        return False

    w = np.exp(-((df["dt"].max() - df["dt"]).dt.days) / (5.0 * 365.25))
    model = xgb.XGBClassifier(n_estimators=120, max_depth=4, learning_rate=0.05,
                              subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", random_state=42)
    model.fit(df[feat_all], df["target_win"], sample_weight=w)
    thr = find_optimal_threshold(df, feat_all, target_col="target_win", fallback_default=0.2)

    os.makedirs(SAVED_DIR, exist_ok=True)
    model.save_model(model_path)
    meta = {"feature_cols": feat_all, "llm_features": [c for c in LLM_FEATURES if c in feat_all],
            "threshold": float(thr), "horizon": int(horizon), "n_samples": int(len(df)),
            "label": label, "allowed_tickers": sorted(universe),
            "window": [str(df["date"].min()), str(df["date"].max())],
            "trained_at": datetime.now().isoformat()}
    with open(meta_path, "wb") as f:
        pickle.dump(meta, f)
    print(f"  saved {label} → {os.path.basename(model_path)} | {len(universe)} tickers | "
          f"threshold={thr:.3f} | samples={len(df)} | window={meta['window'][0]}..{meta['window'][1]}")
    return True


def train_both(horizon=None):
    """Train the CORE model (core+quality_growth+unrated, the primary book) and the AGGRESSIVE model
    (full universe incl. speculative, drives the small high-risk bucket)."""
    core_tickers = tickers_for_tiers(CORE_TIERS, include_unrated=True)
    if not core_tickers:
        print("No classification yet — training core on the full universe (run `make classify` first to split tiers).")
        core_tickers = None
    train_and_save(horizon, allowed_tickers=core_tickers, model_path=SWING_MODEL_PATH,
                   meta_path=SWING_META_PATH, label="core")
    train_and_save(horizon, allowed_tickers=None, model_path=SWING_AGG_MODEL_PATH,
                   meta_path=SWING_AGG_META_PATH, label="aggressive")


def load_swing_model(aggressive=False):
    mp, xp = (SWING_AGG_MODEL_PATH, SWING_AGG_META_PATH) if aggressive else (SWING_MODEL_PATH, SWING_META_PATH)
    if not (os.path.exists(mp) and os.path.exists(xp)):
        return None, None
    m = xgb.XGBClassifier()
    m.load_model(mp)
    with open(xp, "rb") as f:
        meta = pickle.load(f)
    return m, meta


def build_swing_signals(daily_prices_df, daily_macro_df, active_universe, model=None, meta=None, top_n=None):
    """Inference: per-equity swing signal for the latest date, from daily prices + LLM news.

    Returns a list of dicts (ticker, close, prob, action, stop/take-profit, llm_news, intensity).
    Reuses the SAME feature pipeline (build_all_features + add_llm_features) as training, so columns
    align with the persisted model. Returns [] if the model isn't trained yet or data is missing.

    Only the `top_n` highest-conviction above-threshold names keep action=BUY (matching the ≤N open
    positions the portfolio sim validated); lower-ranked candidates are demoted to HOLD."""
    top_n = SWING_TOP_N if top_n is None else top_n
    if model is None or meta is None:
        model, meta = load_swing_model()
    if model is None or daily_prices_df is None or daily_prices_df.empty:
        return []

    equities = [t for t in active_universe if t not in NON_EQUITY and not str(t).startswith(("X:", "C:"))]
    feat_universe = sorted(set(equities + ["SPY", "QQQ"]))
    prices = daily_prices_df[daily_prices_df["ticker"].isin(feat_universe)].copy()
    if prices.empty:
        return []

    full = build_all_features(prices, None, daily_macro_df, feat_universe, target_horizon_bars=meta["horizon"])
    if full.empty:
        return []
    full, _ = add_llm_features(full, load_llm_news_daily())
    full = full[full["ticker"].isin(equities)].copy()

    cols = meta["feature_cols"]
    for c in cols:                       # guard against any column drift (e.g. insider feats off)
        if c not in full.columns:
            full[c] = 0.0

    out = []
    thr = meta["threshold"]
    for tk, g in full.groupby("ticker"):
        g = g.sort_values("cal_date").dropna(subset=cols)
        if g.empty:
            continue
        row = g.iloc[[-1]]
        prob = float(model.predict_proba(row[cols])[:, 1][0])
        close = float(row["close"].values[0])
        atr = float(row["atr_14"].values[0]) if "atr_14" in row else 0.0
        if close <= 0:
            continue
        sl_pct = min(SWING_STOP_MAX, max(SWING_STOP_MIN, (SWING_ATR_STOP_MULT * atr) / close))
        tp_pct = sl_pct * SWING_TP_MULT
        llm_news = float(row["feat_llm_news"].values[0]) if "feat_llm_news" in row else 0.0
        llm_intensity = float(row["feat_llm_news_intensity"].values[0]) if "feat_llm_news_intensity" in row else 0.0
        out.append({
            "ticker": tk,
            "close": round(close, 2),
            "confidence": prob,
            "_sl_pct": sl_pct, "_tp_pct": tp_pct,
            "horizon_days": meta["horizon"],
            "llm_news": round(llm_news, 3),
            "llm_news_intensity": round(llm_intensity, 2),
        })

    # Rank by conviction, then keep BUY only for the top-N above-threshold names (≤ open-position cap).
    out.sort(key=lambda x: x["confidence"], reverse=True)
    buys = 0
    for s in out:
        prob = s["confidence"]
        is_buy = prob >= thr and buys < top_n
        if is_buy:
            buys += 1
            cap_note = ""
        elif prob >= thr:
            cap_note = f" Above threshold but outside the top {top_n} by conviction — hold for now."
        else:
            cap_note = ""
        news_bit = (f" Recent news skews {'bullish' if s['llm_news'] > 0 else 'bearish'} "
                    f"(LLM news score {s['llm_news']:+.2f}).") if abs(s["llm_news"]) > 0.02 else ""
        s["action"] = "BUY" if is_buy else "HOLD"
        s["stop_loss"] = round(s["close"] * (1 - s["_sl_pct"]), 2) if is_buy else None
        s["take_profit"] = round(s["close"] * (1 + s["_tp_pct"]), 2) if is_buy else None
        s["reasoning"] = (f"{meta['horizon']}-day win probability {prob*100:.0f}% vs entry threshold "
                          f"{thr*100:.0f}%.{news_bit}{cap_note}")
        del s["_sl_pct"], s["_tp_pct"]
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Swing model with LLM-news features")
    ap.add_argument("--horizon", type=int, default=5, help="swing holding horizon in trading days")
    ap.add_argument("--splits", type=int, default=4)
    ap.add_argument("--train", action="store_true", help="train + save the core + aggressive swing models")
    ap.add_argument("--core-only", action="store_true", help="train only the core model")
    a = ap.parse_args()
    if a.train:
        if a.core_only:
            from ml_engine.swing_alpha import tickers_for_tiers, CORE_TIERS
            train_and_save(horizon=a.horizon,
                           allowed_tickers=tickers_for_tiers(CORE_TIERS, include_unrated=True) or None)
        else:
            train_both(horizon=a.horizon)
    else:
        walk_forward_swing(horizon=a.horizon, n_splits=a.splits)
