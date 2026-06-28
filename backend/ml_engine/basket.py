"""Shared sleeve builders for the crash war-games.

The war-game engine blends a RISK sleeve against a DEFENSE sleeve. Historically those were hardcoded
to SPY and TLT. These builders let the RISK sleeve be *our own weighted portfolio* and the DEFENSE
sleeve be TLT / cash / BRK.B / a blend — reconstructed over an arbitrary date index using REAL prices
where we have them and a single-factor beta×SPY proxy only where a name didn't trade yet (e.g. a
2020 IPO during the 2008 GFC). Every path is normalized to start at 100 so it drops straight into the
existing SPY/TLT simulation machinery.
"""
import numpy as np
import pandas as pd

from app.database import DailyPrice, CrisisPrice

# A name counts as "real" only if it has prices for most of the window; otherwise we beta-proxy it
# (a name that lists halfway through an era would otherwise inject a flat, fake pre-listing stub).
_REAL_COVERAGE_MIN = 0.8


def _real_close_series(db, ticker, dates, era=None):
    """Close prices for `ticker` aligned to `dates` (NaN where we have nothing).

    daily_prices is the primary source — it holds deep history (back to 1998 for survivors + BRK.B),
    so it covers every era window we align to, including the 2022 bear (which isn't in crisis_prices
    at all). crisis_prices is a fallback for the rare name that lives only there (an era-specific
    universe symbol we don't otherwise keep daily bars for)."""
    idx = pd.DatetimeIndex(dates)
    lo = idx.min().strftime("%Y-%m-%d")
    hi = idx.max().strftime("%Y-%m-%d")
    rows = db.query(DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.ticker == ticker, DailyPrice.date >= lo, DailyPrice.date <= hi).all()
    if not rows and era:
        rows = db.query(CrisisPrice.date, CrisisPrice.close).filter(
            CrisisPrice.ticker == ticker, CrisisPrice.era == era).all()
    if not rows:
        return pd.Series(np.nan, index=idx)
    s = pd.Series({pd.to_datetime(d): float(c) for d, c in rows}).sort_index()
    return s.reindex(idx)


def _proxy_path(beta, spy_path):
    """A name's synthetic path = its market beta × the window's actual SPY return path."""
    spy = np.asarray(spy_path, dtype=float)
    ret = np.concatenate([[0.0], np.diff(spy) / spy[:-1]])
    return 100.0 * np.cumprod(1.0 + beta * ret)


def _name_path(db, ticker, dates, spy_path, betas, era=None):
    """(path_normalized_to_100, source) for one name — real if well-covered, else beta-proxy."""
    s = _real_close_series(db, ticker, dates, era)
    coverage = float(s.notna().mean()) if len(s) else 0.0
    if coverage >= _REAL_COVERAGE_MIN:
        filled = s.ffill().bfill().to_numpy(dtype=float)
        if filled[0] > 0:
            return 100.0 * filled / filled[0], "real"
    return _proxy_path(betas.get(ticker, 1.0), spy_path), "proxy"


def _combine_paths(paths_and_weights):
    """Weighted daily-return composite of normalized paths → a single path starting at 100."""
    total_w = sum(w for _, w in paths_and_weights) or 1.0
    n = len(paths_and_weights[0][0])
    blended_ret = np.zeros(n)
    for path, w in paths_and_weights:
        path = np.asarray(path, dtype=float)
        ret = np.concatenate([[0.0], np.diff(path) / path[:-1]])
        blended_ret += (w / total_w) * ret
    return 100.0 * np.cumprod(1.0 + blended_ret)


def build_basket_path(db, weights, dates, spy_path, era=None):
    """Composite RISK-sleeve path for a weighted portfolio over `dates`.

    Returns (path, coverage_report) where path is normalized to 100 and coverage_report is::

        {"names": {TICKER: {"weight": w, "source": "real"|"proxy"}, ...},
         "real_weight": 0.72, "proxy_weight": 0.28}

    Names with real history use real prices; the rest are beta×SPY proxied (clearly flagged).
    """
    weights = {str(t).upper(): float(w) for t, w in (weights or {}).items() if w and float(w) > 0}
    if not weights:
        # Empty book → fall back to plain SPY so callers still get a sane curve.
        return 100.0 * np.asarray(spy_path, float) / float(spy_path[0]), {
            "names": {}, "real_weight": 0.0, "proxy_weight": 0.0}

    from app.services.account_wargame import _estimate_betas   # lazy: avoids an import cycle
    betas = _estimate_betas(db, list(weights))

    paths_and_weights, names = [], {}
    real_w = proxy_w = 0.0
    for ticker, w in weights.items():
        path, source = _name_path(db, ticker, dates, spy_path, betas, era)
        paths_and_weights.append((path, w))
        names[ticker] = {"weight": w, "source": source}
        if source == "real":
            real_w += w
        else:
            proxy_w += w

    composite = _combine_paths(paths_and_weights)
    total = real_w + proxy_w or 1.0
    return composite, {
        "names": dict(sorted(names.items(), key=lambda kv: -kv[1]["weight"])),
        "real_weight": round(real_w / total, 4),
        "proxy_weight": round(proxy_w / total, 4),
    }


def _cash_path(dates):
    """Flat ~2% annualized 'cash/T-bill' path (matches the engine's TLT-absent proxy)."""
    n = len(dates)
    return 100.0 * np.exp(0.02 * np.arange(n) / 252.0)


def build_defense_path(db, defense_spec, dates, spy_path, era=None):
    """DEFENSE-sleeve path (normalized to 100) — this is also the de-risk reinvest target.

    defense_spec: "tlt" | "brkb" | "cash" | any real ticker (e.g. "bil", "lqd") |
    a blend dict like {"tlt": 0.4, "brkb": 0.4, "bil": 0.2}.
    """
    idx = pd.DatetimeIndex(dates)

    def _asset_path(name):
        name = str(name).lower()
        if name == "cash":
            return _cash_path(idx)
        # Resolve to a real ticker: brk aliases → BRK.B, otherwise the literal ticker (TLT, BIL,
        # LQD, …). Unknown/empty falls back to TLT for backward compatibility.
        ticker = "BRK.B" if name in ("brkb", "brk.b", "brk-b") else (name.upper() or "TLT")
        s = _real_close_series(db, ticker, idx, era).ffill().bfill()
        arr = s.to_numpy(dtype=float)
        if len(arr) == 0 or not np.isfinite(arr).any() or arr[0] <= 0:
            return _cash_path(idx)   # no data → safe cash-like proxy
        return 100.0 * arr / arr[0]

    if isinstance(defense_spec, dict) and defense_spec:
        return _combine_paths([(_asset_path(k), float(v)) for k, v in defense_spec.items() if v])
    return _asset_path(str(defense_spec or "tlt"))
