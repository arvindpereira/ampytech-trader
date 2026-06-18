"""Ingest company fundamentals from Polygon/Massive (vX/reference/financials) into ticker_fundamentals.

Pulls the last N quarterly statements per ticker, extracts key income/balance/cash-flow line items, derives
margins / FCF / ROE / leverage, and upserts one row per (ticker, period end). This is the hard-metric base
for the fundamental-quality signal (a quant composite + an LLM overlay) used to tell strong-fundamentals
dips (accumulate long-term) apart from weak-fundamentals volatility (speculative).

Usage:
  python data_ingestion/fundamentals_fetcher.py                 # whole universe, last 8 quarters
  python data_ingestion/fundamentals_fetcher.py --tickers NVDA,BYND --quarters 12
"""
import sys
import os
import time
import argparse
from datetime import datetime
import requests

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, TickerFundamental, UniverseTicker
from app.core.config import TICKER_UNIVERSE, MASSIVE_API_KEY, MASSIVE_BASE_URL

NON_EQUITY_PREFIX = ("X:", "C:")
FICTIONAL = {"SPACE"}


def _v(stmt, key):
    """Safe line-item value from a Polygon financials statement dict."""
    node = (stmt or {}).get(key)
    if isinstance(node, dict):
        try:
            return float(node.get("value"))
        except (TypeError, ValueError):
            return None
    return None


def _safe_div(a, b):
    return (a / b) if (a is not None and b not in (None, 0)) else None


def _fetch_financials(ticker, headers, quarters=8):
    url = (f"{MASSIVE_BASE_URL}/vX/reference/financials?ticker={ticker}"
           f"&timeframe=quarterly&order=desc&limit={quarters}")
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return []
        return r.json().get("results") or []
    except Exception:
        return []


def _row_from(ticker, rec):
    fin = rec.get("financials", {}) or {}
    inc, bs, cf = (fin.get("income_statement", {}) or {}, fin.get("balance_sheet", {}) or {},
                   fin.get("cash_flow_statement", {}) or {})
    rev = _v(inc, "revenues")
    gp = _v(inc, "gross_profit")
    oi = _v(inc, "operating_income_loss")
    ni = _v(inc, "net_income_loss")
    ocf = _v(cf, "net_cash_flow_from_operating_activities")
    capex = _v(cf, "payments_for_property_plant_and_equipment") or _v(cf, "capital_expenditure")
    assets = _v(bs, "assets")
    liabs = _v(bs, "liabilities")
    eq = _v(bs, "equity")
    cur_a = _v(bs, "current_assets")
    cur_l = _v(bs, "current_liabilities")
    shares = _v(inc, "diluted_average_shares") or _v(inc, "basic_average_shares")
    fcf = (ocf - capex) if (ocf is not None and capex is not None) else ocf
    return {
        "ticker": ticker, "end_date": rec.get("end_date") or "", "fiscal_period": rec.get("fiscal_period"),
        "fiscal_year": str(rec.get("fiscal_year") or ""), "revenues": rev, "gross_profit": gp,
        "operating_income": oi, "net_income": ni, "op_cash_flow": ocf, "capex": capex,
        "total_assets": assets, "total_liabilities": liabs, "equity": eq, "current_assets": cur_a,
        "current_liabilities": cur_l, "shares": shares,
        "gross_margin": _safe_div(gp, rev), "operating_margin": _safe_div(oi, rev),
        "net_margin": _safe_div(ni, rev), "fcf": fcf, "fcf_margin": _safe_div(fcf, rev),
        "roe": _safe_div(ni, eq), "debt_to_equity": _safe_div(liabs, eq),
        "current_ratio": _safe_div(cur_a, cur_l), "source": "polygon",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def fetch_fundamentals(tickers=None, quarters=8, progress_cb=None):
    if not MASSIVE_API_KEY:
        print("No MASSIVE_API_KEY; cannot fetch fundamentals.")
        return
    init_db()
    db = SessionLocal()
    if tickers is None:
        rows = db.query(UniverseTicker).all()
        tickers = [t.ticker for t in rows] if rows else list(TICKER_UNIVERSE)
    tickers = [t for t in tickers if t not in FICTIONAL and not t.startswith(NON_EQUITY_PREFIX)]
    headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"}
    print(f"📊 Fetching fundamentals for {len(tickers)} tickers (last {quarters} quarters each)…")

    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    total = 0
    for i, tk in enumerate(tickers):
        recs = _fetch_financials(tk, headers, quarters)
        rows = [_row_from(tk, r) for r in recs if r.get("end_date")]
        if rows:
            db.execute(sqlite_insert(TickerFundamental).values(rows).on_conflict_do_nothing(
                index_elements=["ticker", "end_date"]))
            db.commit()
            total += len(rows)
            latest = rows[0]
            print(f"  {tk:6} {len(rows)} periods | latest {latest['end_date']} "
                  f"gm={_pct(latest['gross_margin'])} nm={_pct(latest['net_margin'])} "
                  f"roe={_pct(latest['roe'])} d/e={_num(latest['debt_to_equity'])}")
        else:
            print(f"  {tk:6} no financials")
        if progress_cb:
            progress_cb((i + 1) / len(tickers), f"{tk}: {len(rows)} periods")
        time.sleep(0.12)
    db.close()
    print(f"✅ Fundamentals ingest complete: {total} period rows.")


def _pct(x):
    return f"{x*100:.0f}%" if isinstance(x, (int, float)) else "—"


def _num(x):
    return f"{x:.2f}" if isinstance(x, (int, float)) else "—"


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Ingest company fundamentals (Polygon/Massive financials)")
    p.add_argument("--tickers", default=None, help="comma-separated; default = universe")
    p.add_argument("--quarters", type=int, default=8)
    a = p.parse_args()
    tk = a.tickers.split(",") if a.tickers else None
    fetch_fundamentals(tickers=tk, quarters=a.quarters)
