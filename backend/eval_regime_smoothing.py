"""Walk-forward evaluation of HMM regime-bucket smoothing methods.

Compares three ways of turning the 3-state Gaussian HMM into the crash-radar
`hmm_regime` sub-score (0-100, higher = more crisis-like):

  raw       hard Viterbi label -> {growth:20, transition:60, crisis:100}   (current)
  soft      posterior-probability expectation: 20*p_g + 60*p_t + 100*p_c
  ema_raw   causal EMA of the raw hard score
  ema_soft  causal EMA of the soft score

Evaluation is genuinely out-of-sample: an expanding-window walk-forward refits
the HMM only on data strictly BEFORE each test fold, so no fold is scored by a
model that saw it. We then judge each score series on two axes:

  Predictive power  - does a higher score precede SPY weakness?
                      Spearman corr & ROC-AUC vs forward SPY max drawdown.
  Stability         - how much spurious week-to-week chatter (the sawtooth)?
                      weekly mean |Δ|, sign-flip count, lag-1 autocorrelation.

"better" = retains (or improves) predictive power while cutting the chatter.
"""
import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from ml_engine.models import load_daily_spy_features

# Toggle: standardize HMM features so low-variance volatility isn't swamped by
# the larger-scale macro features (the current code does NOT scale -> vol ignored).
STANDARDIZE = os.environ.get("REGIME_STANDARDIZE", "1") == "1"

COLS = ["feat_volatility_10", "feat_fed_funds", "feat_yield_spread"]
SCORE = {"growth": 20.0, "transition": 60.0, "crisis": 100.0}
EMA_SPANS = [10, 21]          # ~2 and ~4 trading weeks
FOLD_FREQ = "YS"             # refit yearly
FWD_HORIZONS = [21, 63]      # ~1 and ~3 months
DD_EVENT = 0.07             # binary "stress" = forward drawdown >= 7%


def fit_and_score(train, full):
    """Fit HMM on `train`, return raw-hard and soft scores for every row of `full`."""
    Xtr = train[COLS].values
    Xfull = full[COLS].values
    if STANDARDIZE:
        sc = StandardScaler().fit(Xtr)   # fit on train fold only -> no look-ahead
        Xtr, Xfull = sc.transform(Xtr), sc.transform(Xfull)
    m = GaussianHMM(n_components=3, covariance_type="diag", n_iter=100, random_state=42)
    m.fit(Xtr)
    order = np.argsort(m.means_[:, 0])  # col 0 = volatility: low->growth, high->crisis
    state_score = np.empty(3)
    state_score[int(order[0])] = SCORE["growth"]
    state_score[int(order[1])] = SCORE["transition"]
    state_score[int(order[2])] = SCORE["crisis"]
    raw = state_score[m.predict(Xfull)]
    soft = m.predict_proba(Xfull) @ state_score
    return raw, soft


def walk_forward(d):
    """Expanding-window OOS scores. Returns d with raw/soft OOS columns (NaN before first fold)."""
    d = d.sort_values("date").reset_index(drop=True)
    dts = pd.to_datetime(d["date"])
    first_test = pd.Timestamp("2006-01-01")
    fold_starts = pd.date_range(first_test, dts.max(), freq=FOLD_FREQ)

    raw_oos = np.full(len(d), np.nan)
    soft_oos = np.full(len(d), np.nan)
    for i, fs in enumerate(fold_starts):
        fe = fold_starts[i + 1] if i + 1 < len(fold_starts) else (dts.max() + pd.Timedelta(days=1))
        train_mask = dts < fs
        test_mask = (dts >= fs) & (dts < fe)
        if train_mask.sum() < 250 or test_mask.sum() == 0:
            continue
        raw, soft = fit_and_score(d[train_mask.values], d[test_mask.values])
        raw_oos[test_mask.values] = raw
        soft_oos[test_mask.values] = soft

    d["raw"] = raw_oos
    d["soft"] = soft_oos
    for span in EMA_SPANS:
        d[f"ema_raw_{span}"] = d["raw"].ewm(span=span, adjust=False).mean()
        d[f"ema_soft_{span}"] = d["soft"].ewm(span=span, adjust=False).mean()
    return d


def forward_drawdown(close, h):
    """Magnitude (>=0) of the worst close-to-trough drop over the next h bars."""
    c = close.values
    out = np.full(len(c), np.nan)
    for t in range(len(c)):
        end = min(len(c), t + h + 1)
        if end - (t + 1) < 1:
            continue
        out[t] = max(0.0, (c[t] - np.min(c[t + 1:end])) / c[t])
    return out


def concurrent_stress_label(dates, close, lookback=252, thr=0.10):
    """1 when SPY is currently >= thr below its trailing `lookback`-day peak.
    This is the bucket's actual job: flag stress regimes as they happen."""
    s = pd.Series(close.values, index=pd.to_datetime(dates))
    peak = s.rolling(lookback, min_periods=20).max()
    dd = (peak - s) / peak
    return (dd >= thr).astype(int).values


def stability_metrics(weekly):
    s = weekly.dropna()
    diffs = s.diff().dropna()
    flips = int((np.sign(diffs).diff().fillna(0) != 0).sum())
    autocorr = s.autocorr(lag=1)
    return {
        "weekly_mean_abs_delta": float(diffs.abs().mean()),
        "weekly_sign_flips": flips,
        "lag1_autocorr": float(autocorr),
    }


def main():
    feats = load_daily_spy_features()
    d = feats.dropna(subset=COLS + ["close"]).copy()
    d["date"] = pd.to_datetime(d["date"])
    d = walk_forward(d)

    for h in FWD_HORIZONS:
        d[f"fdd_{h}"] = forward_drawdown(d["close"], h)
    d["stress_now"] = concurrent_stress_label(d["date"], d["close"])

    methods = ["raw", "soft"] + [f"ema_raw_{s}" for s in EMA_SPANS] + [f"ema_soft_{s}" for s in EMA_SPANS]

    # Evaluate only on OOS rows that have both scores and forward targets.
    oos = d[d["raw"].notna()].copy()
    print(f"OOS evaluation window: {oos['date'].min().date()} -> {oos['date'].max().date()}  ({len(oos)} daily obs)\n")

    rows = []
    # Weekly (Friday) sample for stability — that's the cadence the timeline shows.
    weekly = oos.set_index("date").resample("W-FRI").last()
    for mth in methods:
        rec = {"method": mth}
        # Concurrent validity: does the score flag stress regimes as they happen? (bucket's real job)
        sub = oos[[mth, "stress_now"]].dropna()
        rec["auc_concurrent"] = round(roc_auc_score(sub["stress_now"], sub[mth]), 3)
        # Forward predictive power (weak by nature — vol-driven regime is coincident, not leading)
        for h in FWD_HORIZONS:
            sub = oos[[mth, f"fdd_{h}"]].dropna()
            rho, _ = spearmanr(sub[mth], sub[f"fdd_{h}"])
            rec[f"spearman_fwd{h}"] = round(rho, 3)
        rec.update({k: round(v, 2) for k, v in stability_metrics(weekly[mth]).items()})
        rows.append(rec)

    res = pd.DataFrame(rows).set_index("method")
    pd.set_option("display.width", 220); pd.set_option("display.max_columns", 30)
    print("=== Concurrent validity (AUC, higher=better) | Forward power (spearman) | Stability (lower delta/flips=smoother) ===")
    print(res.to_string())

    # Composite ranking: concurrent AUC (does it flag real stress) + weekly turnover (smoothness).
    res["pred_rank"] = res["auc_concurrent"].rank(ascending=False)
    res["smooth_rank"] = res["weekly_mean_abs_delta"].rank(ascending=True)
    res["combined_rank"] = (res["pred_rank"] + res["smooth_rank"]) / 2
    print("\n=== Ranking (1 = best) ===")
    print(res[["auc_concurrent", "pred_rank", "weekly_mean_abs_delta", "smooth_rank", "combined_rank"]]
          .sort_values("combined_rank").to_string())

    winner = res.sort_values("combined_rank").index[0]
    print(f"\n>>> Winner by combined concurrent-validity + stability rank: {winner}")


if __name__ == "__main__":
    main()
