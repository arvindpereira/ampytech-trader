"""Consolidated portfolio sector exposure vs S&P 500 GICS benchmark."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.core.config import BASE_DIR
from ml_engine.sector_resolver import canonical_sector, list_sector_entries

_ALERT_THRESHOLD = 0.05
_DATA_DIR = os.path.join(BASE_DIR, "data")

_INDUSTRY_REVENUE_HINTS = {
    "semiconductor": "Semiconductor chip sales (compute, AI accelerators, networking)",
    "software": "Software licenses & recurring subscription revenue",
    "internet retail": "E-commerce marketplace & retail sales",
    "internet content": "Digital advertising & platform services",
    "banks": "Net interest income & lending fees",
    "biotechnology": "Drug development milestones & product sales",
    "pharmaceutical": "Prescription drug sales",
    "oil & gas": "Upstream production & refining margins",
    "aerospace": "Defense contracts & commercial aircraft",
    "automobile": "Vehicle sales & leasing",
    "reit": "Rental income & property management fees",
    "insurance": "Premiums & underwriting income",
    "media": "Advertising, subscriptions & content licensing",
    "retail": "Consumer retail sales",
    "machinery": "Industrial equipment sales & services",
}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def load_sp500_weights() -> dict:
    path = os.path.join(_DATA_DIR, "sp500_sector_weights.json")
    if not os.path.exists(path):
        return {"weights": {}, "benchmark": "S&P 500", "as_of": "n/a"}
    with open(path) as f:
        return json.load(f)


def _latest_price(db, ticker: str, fallback: float = 0.0) -> float:
    from app.database import DailyPrice, RecentPrice

    r = db.query(RecentPrice).filter(RecentPrice.ticker == ticker).order_by(RecentPrice.date.desc()).first()
    if r and r.close:
        return float(r.close)
    d = db.query(DailyPrice).filter(DailyPrice.ticker == ticker).order_by(DailyPrice.date.desc()).first()
    if d and d.close:
        return float(d.close)
    return float(fallback or 0.0)


def _revenue_driver(sector: Optional[str], industry: Optional[str], ticker: str) -> str:
    if industry:
        low = industry.lower()
        for needle, hint in _INDUSTRY_REVENUE_HINTS.items():
            if needle in low:
                return hint
        return f"Primary revenue from {industry} operations"
    if sector:
        return f"Core {sector} business lines (see latest 10-K segment disclosure)"
    return "Revenue mix unavailable — classify ticker metadata first"


def _metadata_for(db, ticker: str) -> Tuple[Optional[str], Optional[str]]:
    from app.database import CompanySnapshot, TickerMetadata
    from data_ingestion.sector_catalog_refresh import _normalize_sector_label

    meta = db.query(TickerMetadata).filter(TickerMetadata.ticker == ticker).first()
    industry = meta.industry if meta else None
    sector = _normalize_sector_label(meta.sector if meta else None, industry)
    if sector:
        return sector, industry
    snap = (
        db.query(CompanySnapshot)
        .filter(CompanySnapshot.ticker == ticker)
        .order_by(CompanySnapshot.as_of_date.desc())
        .first()
    )
    if snap:
        industry = industry or snap.industry
        sector = _normalize_sector_label(snap.sector, industry)
    return sector, industry


def get_sector_recommendations(db, sector: str, current_holdings: set) -> List[dict]:
    from app.database import CompanySnapshot, TickerClassification, TickerMetadata, UniverseTicker
    from sqlalchemy import func

    universe_tickers = {r.ticker for r in db.query(UniverseTicker.ticker).all()}

    # 1. Latest company snapshots (RKB)
    latest_date_row = db.query(func.max(CompanySnapshot.as_of_date)).first()
    latest_date = latest_date_row[0] if latest_date_row else None

    recs = []
    seen_tickers = set()

    if latest_date:
        snaps = (
            db.query(CompanySnapshot)
            .filter(CompanySnapshot.sector == sector, CompanySnapshot.as_of_date == latest_date)
            .order_by(CompanySnapshot.quality.desc(), CompanySnapshot.upside_pct.desc())
            .limit(6)
            .all()
        )
        for s in snaps:
            recs.append({
                "ticker": s.ticker,
                "tier": s.tier,
                "quality": round(s.quality, 2) if s.quality else None,
                "upside_pct": round(s.upside_pct, 4) if s.upside_pct else None,
                "recommendation_key": s.recommendation_key,
                "held": s.ticker in current_holdings,
                "in_universe": s.ticker in universe_tickers,
            })
            seen_tickers.add(s.ticker)

    # 2. TickerClassification + TickerMetadata fallback
    if len(recs) < 6:
        rows = (
            db.query(TickerMetadata.ticker, TickerClassification.tier, TickerClassification.quality)
            .join(TickerClassification, TickerMetadata.ticker == TickerClassification.ticker)
            .filter(TickerMetadata.sector == sector)
            .order_by(TickerClassification.quality.desc())
            .all()
        )
        for r in rows:
            if r.ticker not in seen_tickers:
                recs.append({
                    "ticker": r.ticker,
                    "tier": r.tier,
                    "quality": round(r.quality, 2) if r.quality else None,
                    "upside_pct": None,
                    "recommendation_key": None,
                    "held": r.ticker in current_holdings,
                    "in_universe": r.ticker in universe_tickers,
                })
                seen_tickers.add(r.ticker)
                if len(recs) >= 6:
                    break

    # 3. Catalog seed tickers — discovery path for sectors with no RKB/classification data
    if len(recs) < 6:
        try:
            catalog_path = os.path.join(_DATA_DIR, "research_sectors.json")
            if os.path.exists(catalog_path):
                with open(catalog_path) as f:
                    catalog = json.load(f)
                for entry in catalog.get("sectors", []):
                    if entry.get("sector") != sector:
                        continue
                    for seed in entry.get("seed_tickers", []):
                        t = seed if isinstance(seed, str) else seed.get("ticker", "")
                        if t and t not in seen_tickers:
                            recs.append({
                                "ticker": t,
                                "tier": None,
                                "quality": None,
                                "upside_pct": None,
                                "recommendation_key": None,
                                "held": t in current_holdings,
                                "in_universe": t in universe_tickers,
                                "source": "catalog",
                            })
                            seen_tickers.add(t)
                            if len(recs) >= 6:
                                break
        except Exception:
            pass

    return recs


def collect_consolidated_positions(db, mode: str = "real") -> List[dict]:
    """Internal trading account + external equity lots, merged by ticker."""
    from app.database import EquityLot, ExternalAccount, VirtualPosition

    merged: Dict[str, dict] = {}
    pos_mode = "real" if mode == "real" else "replay"

    def add(ticker: str, shares: float, price: float, source: str, account: str = ""):
        tk = ticker.upper().strip()
        if not tk or shares <= 0:
            return
        val = shares * price
        if tk not in merged:
            merged[tk] = {
                "ticker": tk,
                "shares": 0.0,
                "market_value": 0.0,
                "sources": [],
                "accounts": [],
            }
        merged[tk]["shares"] += shares
        merged[tk]["market_value"] += val
        if source not in merged[tk]["sources"]:
            merged[tk]["sources"].append(source)
        if account and account not in merged[tk]["accounts"]:
            merged[tk]["accounts"].append(account)

    # Internal trading (virtual / broker-backed)
    try:
        from execution.executor import get_alpaca_api

        api = get_alpaca_api()
        for p in api.list_positions():
            add(p.symbol, float(p.qty), float(p.current_price), "trading_account", "Alpaca")
    except Exception:
        for p in db.query(VirtualPosition).filter(
            VirtualPosition.mode == pos_mode, VirtualPosition.quantity > 0
        ).all():
            px = _latest_price(db, p.ticker, p.entry_price)
            add(p.ticker, p.quantity, px, "trading_account", pos_mode)

    # External brokerage lots
    external_labels = {a.account_label for a in db.query(ExternalAccount).all()}
    for lot in db.query(EquityLot).all():
        if lot.account_label not in external_labels:
            continue
        px = _latest_price(db, lot.ticker, lot.cost_basis_per_share)
        add(lot.ticker, lot.shares, px, "external", lot.account_label or "external")

    rows = list(merged.values())
    for r in rows:
        r["market_value"] = round(r["market_value"], 2)
        r["shares"] = round(r["shares"], 6)
    rows.sort(key=lambda x: x["market_value"], reverse=True)
    return rows


def analyze_sector_exposure(db, mode: str = "real", *, refresh_metadata: bool = True) -> dict:
    positions = collect_consolidated_positions(db, mode=mode)
    total = sum(p["market_value"] for p in positions)
    if total <= 0:
        return {
            "as_of": _now(),
            "total_equity_value": 0,
            "sectors": [],
            "alerts": [],
            "positions": [],
            "error": "no_priced_holdings",
        }

    tickers = [p["ticker"] for p in positions]
    if refresh_metadata:
        try:
            from data_ingestion.ticker_metadata_fetcher import refresh_tickers

            refresh_tickers(tickers, db=db)
            from data_ingestion.sector_catalog_refresh import _backfill_metadata_sectors

            _backfill_metadata_sectors(db)
        except Exception:
            pass

    benchmark = load_sp500_weights()
    bench_weights = benchmark.get("weights") or {}
    known_sectors = {e.get("sector") for e in list_sector_entries() if e.get("sector")}

    enriched = []
    by_sector: Dict[str, List[dict]] = defaultdict(list)
    by_industry: Dict[str, List[dict]] = defaultdict(list)
    unclassified = []

    for p in positions:
        sector, industry = _metadata_for(db, p["ticker"])
        wt = p["market_value"] / total
        row = {
            **p,
            "sector": sector,
            "industry": industry,
            "weight": round(wt, 4),
            "revenue_driver": _revenue_driver(sector, industry, p["ticker"]),
        }
        enriched.append(row)
        if sector and sector in known_sectors:
            by_sector[sector].append(row)
            if industry:
                by_industry[industry].append(row)
        else:
            unclassified.append(row)

    sector_rows = []
    alerts = []
    all_sectors = sorted(known_sectors)
    current_holdings = {p["ticker"] for p in positions}

    for sec in all_sectors:
        holdings = sorted(by_sector.get(sec, []), key=lambda x: x["market_value"], reverse=True)
        port_wt = sum(h["weight"] for h in holdings)
        bench_wt = float(bench_weights.get(sec, 0.0))
        delta = round(port_wt - bench_wt, 4)
        alert = abs(delta) >= _ALERT_THRESHOLD and (port_wt > 0 or bench_wt > 0)

        ind_map: Dict[str, float] = defaultdict(float)
        for h in holdings:
            if h.get("industry"):
                ind_map[h["industry"]] += h["weight"]

        industries = [
            {"industry": k, "portfolio_weight": round(v, 4)}
            for k, v in sorted(ind_map.items(), key=lambda x: -x[1])
        ]

        drill = [
            {
                "ticker": h["ticker"],
                "portfolio_weight": h["weight"],
                "market_value": h["market_value"],
                "industry": h.get("industry"),
                "revenue_driver": h.get("revenue_driver"),
                "accounts": h.get("accounts"),
                "sources": h.get("sources"),
            }
            for h in holdings[:12]
        ]

        recs = get_sector_recommendations(db, sec, current_holdings)

        entry = {
            "sector": sec,
            "portfolio_weight": round(port_wt, 4),
            "benchmark_weight": round(bench_wt, 4),
            "delta": delta,
            "alert": alert,
            "market_value": round(sum(h["market_value"] for h in holdings), 2),
            "industries": industries,
            "holdings": drill,
            "recommendations": recs,
        }
        sector_rows.append(entry)
        if alert:
            direction = "overweight" if delta > 0 else "underweight"
            alerts.append({
                "sector": sec,
                "direction": direction,
                "delta_pct": round(delta * 100, 1),
                "portfolio_pct": round(port_wt * 100, 1),
                "benchmark_pct": round(bench_wt * 100, 1),
                "message": (
                    f"{sec} is {abs(delta)*100:.1f}pp {direction} vs S&P 500 "
                    f"({port_wt*100:.1f}% portfolio vs {bench_wt*100:.1f}% benchmark)"
                ),
            })

    sector_rows.sort(key=lambda r: r["portfolio_weight"], reverse=True)
    alerts.sort(key=lambda a: abs(a["delta_pct"]), reverse=True)

    from ml_engine.sector_resolver import etf_map
    etfs = etf_map()

    return {
        "as_of": _now(),
        "total_equity_value": round(total, 2),
        "position_count": len(enriched),
        "classified_count": len(enriched) - len(unclassified),
        "alert_threshold_pp": _ALERT_THRESHOLD * 100,
        "benchmark": {
            "name": benchmark.get("benchmark", "S&P 500"),
            "as_of": benchmark.get("as_of"),
            "source": benchmark.get("source"),
        },
        "sectors": sector_rows,
        "alerts": alerts,
        "unclassified": [
            {"ticker": u["ticker"], "weight": u["weight"], "market_value": u["market_value"]}
            for u in unclassified
        ],
        "positions": enriched,
        "etfs": etfs,
    }
