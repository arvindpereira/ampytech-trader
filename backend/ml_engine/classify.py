"""Step 2d: classify each ticker into a routing tier on the risk × fundamental-quality grid.

Blends the quant composite (2b) and LLM overlay (2c) into a final quality, adjusts for LLM flags
(one-off / speculative / distress), measures volatility + 2022 drawdown, and assigns a tier:

  quality_growth — strong fundamentals + high volatility → accumulate dips, hold long-term (NVDA, PLTR)
  core           — solid fundamentals, lower volatility → the primary swing/long-term book
  speculative    — weak fundamentals + high volatility → small high-risk bucket (BYND, RGTI)
  value_trap     — weak fundamentals + low volatility → avoid

This is the signal the two-model training (step 3) and the high-risk bucket / routing (step 4) consume.
"""
import sys
import os
import json
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, DailyPrice, TickerClassification, UniverseTicker
from ml_engine.fundamental_quality import compute_quant_quality

QUALITY_HI = 0.55          # blended-quality threshold for "strong fundamentals"


def _vol_and_dd(db, tickers):
    """{ticker: (annualized_vol, 2022_max_drawdown)} from daily closes."""
    rows = db.query(DailyPrice.ticker, DailyPrice.date, DailyPrice.close).filter(
        DailyPrice.ticker.in_(list(tickers)), DailyPrice.date >= "2021-06-01").all()
    df = pd.DataFrame(rows, columns=["ticker", "date", "close"]).sort_values(["ticker", "date"])
    out = {}
    for tk, g in df.groupby("ticker"):
        c = g["close"].astype(float).values
        rets = pd.Series(c).pct_change().dropna()
        vol = float(rets.tail(252).std() * np.sqrt(252)) if len(rets) > 20 else None
        g22 = g[(g["date"] >= "2022-01-01") & (g["date"] < "2023-01-01")]["close"].astype(float).values
        dd22 = float((g22 / np.maximum.accumulate(g22) - 1).min()) if len(g22) > 20 else None
        out[tk] = (vol, dd22)
    return out


def _tier(quality, vol, vol_median, distressed):
    vol_high = vol is not None and vol >= vol_median
    if distressed or (quality is not None and quality < 0.30):
        return "speculative" if vol_high else "value_trap"
    if quality is not None and quality >= QUALITY_HI:
        return "quality_growth" if vol_high else "core"
    # mid quality
    return "speculative" if vol_high else "core"


def classify_universe(run_llm=False, progress_cb=None):
    """Compute/refresh tiers for the whole universe. If run_llm, (re)runs the LLM overlay first;
    otherwise it uses whatever llm_quality is already stored (falling back to quant where missing)."""
    if run_llm:
        from ml_engine.fundamental_llm import assess_all
        assess_all(progress_cb=progress_cb)

    quant = compute_quant_quality()
    db = SessionLocal()
    try:
        uni = [t.ticker for t in db.query(UniverseTicker).all()] or list(quant.keys())
        existing = {c.ticker: c for c in db.query(TickerClassification).all()}
        vdd = _vol_and_dd(db, uni)
        vols = [v for v, _ in vdd.values() if v is not None]
        vol_median = float(np.median(vols)) if vols else 0.4

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        results = []
        for tk in quant:                       # only tickers with fundamentals
            qq = quant[tk]["quality"]
            distressed = quant[tk]["distressed"]
            row = existing.get(tk)
            llm_q = row.llm_quality if row and row.llm_quality is not None else None
            flags = []
            if row and row.llm_flags:
                try:
                    flags = json.loads(row.llm_flags)
                except Exception:
                    flags = []
            blended = (0.5 * qq + 0.5 * llm_q) if llm_q is not None else qq
            # flag adjustments
            if "bank" in flags and llm_q is not None:
                blended = llm_q                       # ratios mislead for banks → trust the LLM
            if any(f in flags for f in ("one_off_gain", "speculative")):
                blended = min(blended, 0.50)          # don't let a one-off inflate a speculative name
            if distressed or "distress" in flags:
                blended = min(blended, 0.20)
            vol, dd22 = vdd.get(tk, (None, None))
            computed = _tier(blended, vol, vol_median, distressed)
            override = row.tier_override if row else None
            tier = override or computed                # a manual override wins over the computed tier
            vals = {"ticker": tk, "quant_quality": qq, "llm_quality": llm_q, "quality": round(blended, 3),
                    "volatility": round(vol, 3) if vol is not None else None,
                    "dd_2022": round(dd22, 3) if dd22 is not None else None,
                    "distressed": bool(distressed), "tier": tier,
                    "updated_at": datetime.now().isoformat(timespec="seconds")}
            # never clobber tier_override on recompute (it's preserved via the explicit set_ below)
            stmt = sqlite_insert(TickerClassification).values(**vals)
            stmt = stmt.on_conflict_do_update(index_elements=["ticker"],
                                              set_={k: v for k, v in vals.items() if k != "ticker"})
            db.execute(stmt)
            results.append({**vals, "flags": flags})
        db.commit()
        return {"vol_median": vol_median, "rows": results}
    finally:
        db.close()


def _risk_tier_from_vol(vol, vol_median):
    """Risk-only tier for names without fundamentals (ETFs, ADRs, class shares): low realized
    volatility reads as defensive ('core'), high as 'speculative'. Unknown vol stays neutral."""
    if vol is None:
        return "core"
    return "speculative" if vol >= vol_median else "core"


def classify_tickers(tickers, run_llm=False, progress_cb=None):
    """Classify an explicit ticker set (e.g. external holdings outside the trade universe).

    Tickers that have fundamentals get the same blended quant+LLM quality tier as the universe;
    tickers without (ETFs/ADRs/BRK.B) fall back to a volatility-only risk tier so every held name
    still receives a usable tier + volatility. Existing tier_override is preserved."""
    if run_llm:
        from ml_engine.fundamental_llm import assess_all
        assess_all(progress_cb=progress_cb)

    quant = compute_quant_quality()
    db = SessionLocal()
    try:
        targets = {str(t).upper().strip() for t in tickers if t}
        if not targets:
            return {"rows": []}
        vdd = _vol_and_dd(db, targets)
        vols = [v for v, _ in vdd.values() if v is not None]
        # Anchor the vol split on the broad universe median (stable) rather than just this subset.
        uni_vdd = _vol_and_dd(db, [t.ticker for t in db.query(UniverseTicker).all()])
        uni_vols = [v for v, _ in uni_vdd.values() if v is not None] or vols
        vol_median = float(np.median(uni_vols)) if uni_vols else 0.4

        existing = {c.ticker: c for c in db.query(TickerClassification).all()}
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        results = []
        for tk in sorted(targets):
            vol, dd22 = vdd.get(tk, (None, None))
            row = existing.get(tk)
            flags = []
            if row and row.llm_flags:
                try:
                    flags = json.loads(row.llm_flags)
                except Exception:
                    flags = []
            if tk in quant:
                qq = quant[tk]["quality"]
                distressed = quant[tk]["distressed"]
                llm_q = row.llm_quality if row and row.llm_quality is not None else None
                blended = (0.5 * qq + 0.5 * llm_q) if llm_q is not None else qq
                if "bank" in flags and llm_q is not None:
                    blended = llm_q
                if any(f in flags for f in ("one_off_gain", "speculative")):
                    blended = min(blended, 0.50)
                if distressed or "distress" in flags:
                    blended = min(blended, 0.20)
                computed = _tier(blended, vol, vol_median, distressed)
                quality_val = round(blended, 3)
            elif row and row.quality is not None:
                # No fundamentals this run but a prior quality exists (e.g. PINS) — preserve it,
                # only refresh volatility/tier rather than clobbering quality to None.
                qq, llm_q = row.quant_quality, row.llm_quality
                distressed = bool(row.distressed)
                quality_val = row.quality
                computed = _tier(quality_val, vol, vol_median, distressed)
            else:
                qq, llm_q, distressed = None, None, False
                computed = _risk_tier_from_vol(vol, vol_median)
                quality_val = None
            override = row.tier_override if row else None
            tier = override or computed
            vals = {"ticker": tk, "quant_quality": qq, "llm_quality": llm_q, "quality": quality_val,
                    "volatility": round(vol, 3) if vol is not None else None,
                    "dd_2022": round(dd22, 3) if dd22 is not None else None,
                    "distressed": bool(distressed), "tier": tier,
                    "updated_at": datetime.now().isoformat(timespec="seconds")}
            stmt = sqlite_insert(TickerClassification).values(**vals)
            stmt = stmt.on_conflict_do_update(index_elements=["ticker"],
                                              set_={k: v for k, v in vals.items() if k != "ticker"})
            db.execute(stmt)
            results.append({**vals, "flags": flags})
        db.commit()
        return {"vol_median": vol_median, "rows": results}
    finally:
        db.close()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Classify universe into risk × quality tiers")
    p.add_argument("--run-llm", action="store_true", help="(re)run the LLM overlay first")
    a = p.parse_args()
    res = classify_universe(run_llm=a.run_llm)
    rows = sorted(res["rows"], key=lambda r: (r["tier"], -(r["quality"] or 0)))
    from collections import Counter
    counts = Counter(r["tier"] for r in rows)
    print(f"\nUniverse classification — vol median {res['vol_median']:.2f}  | tiers: {dict(counts)}\n")
    print(f"{'ticker':7} {'tier':14} {'qual':>5} {'vol':>5} {'dd22':>6}  flags")
    print("-" * 64)
    for r in rows:
        v = f"{r['volatility']:.2f}" if r["volatility"] is not None else "—"
        d = f"{r['dd_2022']*100:.0f}%" if r["dd_2022"] is not None else "—"
        print(f"{r['ticker']:7} {r['tier']:14} {r['quality']:>5.2f} {v:>5} {d:>6}  {','.join(r['flags'])}")
