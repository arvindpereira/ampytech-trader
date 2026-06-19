"""Tax-aware lot planning helpers for personally-held vested shares.

This module is deterministic advisory logic, not an optimizer and not tax advice. Federal/state rates
are approximate constants for planning only; callers should surface that limitation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional


LTCG_THRESHOLDS = {
    2026: {
        "single": [(0, 0.0), (49230, 0.15), (544500, 0.20)],
        "married_joint": [(0, 0.0), (98460, 0.15), (612350, 0.20)],
        "head_of_household": [(0, 0.0), (65950, 0.15), (579300, 0.20)],
        "married_separate": [(0, 0.0), (49230, 0.15), (306175, 0.20)],
    },
    2025: {
        "single": [(0, 0.0), (48350, 0.15), (533400, 0.20)],
        "married_joint": [(0, 0.0), (96700, 0.15), (600050, 0.20)],
        "head_of_household": [(0, 0.0), (64750, 0.15), (566700, 0.20)],
        "married_separate": [(0, 0.0), (48350, 0.15), (300000, 0.20)],
    },
}

ORDINARY_BRACKETS = {
    2026: {
        "single": [(0, 0.10), (12000, 0.12), (49000, 0.22), (103000, 0.24), (198000, 0.32), (252000, 0.35), (640000, 0.37)],
        "married_joint": [(0, 0.10), (24000, 0.12), (98000, 0.22), (206000, 0.24), (396000, 0.32), (504000, 0.35), (768000, 0.37)],
        "head_of_household": [(0, 0.10), (17100, 0.12), (65000, 0.22), (103000, 0.24), (198000, 0.32), (252000, 0.35), (640000, 0.37)],
        "married_separate": [(0, 0.10), (12000, 0.12), (49000, 0.22), (103000, 0.24), (198000, 0.32), (252000, 0.35), (384000, 0.37)],
    },
    2025: {
        "single": [(0, 0.10), (11925, 0.12), (48475, 0.22), (103350, 0.24), (197300, 0.32), (250525, 0.35), (626350, 0.37)],
        "married_joint": [(0, 0.10), (23850, 0.12), (96950, 0.22), (206700, 0.24), (394600, 0.32), (501050, 0.35), (751600, 0.37)],
        "head_of_household": [(0, 0.10), (17000, 0.12), (64850, 0.22), (103350, 0.24), (197300, 0.32), (250500, 0.35), (626350, 0.37)],
        "married_separate": [(0, 0.10), (11925, 0.12), (48475, 0.22), (103350, 0.24), (197300, 0.32), (250525, 0.35), (375800, 0.37)],
    },
}

NIIT_THRESHOLDS = {
    "single": 200000,
    "head_of_household": 200000,
    "married_joint": 250000,
    "married_separate": 125000,
}


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _profile_value(profile: Any, name: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(name, default)
    return getattr(profile, name, default)


def _lot_value(lot: Any, name: str, default: Any = None) -> Any:
    if isinstance(lot, dict):
        return lot.get(name, default)
    return getattr(lot, name, default)


def marginal_rate(amount: float, brackets: List[tuple]) -> float:
    rate = brackets[0][1]
    for floor, bracket_rate in brackets:
        if amount >= floor:
            rate = bracket_rate
        else:
            break
    return rate


def _ltcg_rate(profile: Any, extra_gain: float = 0.0) -> float:
    year = int(_profile_value(profile, "tax_year", 2026) or 2026)
    status = _profile_value(profile, "filing_status", "single") or "single"
    tables = LTCG_THRESHOLDS.get(year) or LTCG_THRESHOLDS[2026]
    brackets = tables.get(status) or tables["single"]
    income = float(_profile_value(profile, "ordinary_income", 0.0) or 0.0) + max(0.0, extra_gain)
    return marginal_rate(income, brackets)


def _ordinary_rate(profile: Any, extra_gain: float = 0.0) -> float:
    year = int(_profile_value(profile, "tax_year", 2026) or 2026)
    status = _profile_value(profile, "filing_status", "single") or "single"
    tables = ORDINARY_BRACKETS.get(year) or ORDINARY_BRACKETS[2026]
    brackets = tables.get(status) or tables["single"]
    income = float(_profile_value(profile, "ordinary_income", 0.0) or 0.0) + max(0.0, extra_gain)
    return marginal_rate(income, brackets)


def _niit_rate(profile: Any, gain: float) -> float:
    if gain <= 0:
        return 0.0
    status = _profile_value(profile, "filing_status", "single") or "single"
    magi = float(_profile_value(profile, "magi", 0.0) or 0.0)
    return 0.038 if magi > NIIT_THRESHOLDS.get(status, 200000) else 0.0


def classify_lots(lots: Iterable[Any], as_of: Optional[date] = None, prices: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    as_of = as_of or date.today()
    prices = prices or {}
    out = []
    for lot in lots:
        acquisition_date = _as_date(_lot_value(lot, "acquisition_date"))
        shares = float(_lot_value(lot, "shares", 0.0) or 0.0)
        basis = float(_lot_value(lot, "cost_basis_per_share", 0.0) or 0.0)
        ticker = str(_lot_value(lot, "ticker", "") or "").upper()
        price = _lot_value(lot, "current_price", None)
        if price is None:
            price = prices.get(ticker)
        price = float(price) if price is not None else basis
        held_days = (as_of - acquisition_date).days
        gain_per_share = price - basis
        row = {
            "id": _lot_value(lot, "id"),
            "ticker": ticker,
            "account_label": _lot_value(lot, "account_label"),
            "lot_type": _lot_value(lot, "lot_type", "other"),
            "shares": shares,
            "cost_basis_per_share": basis,
            "acquisition_date": acquisition_date.isoformat(),
            "notes": _lot_value(lot, "notes"),
            "current_price": price,
            "market_value": shares * price,
            "unrealized_gain": shares * gain_per_share,
            "unrealized_gain_pct": (gain_per_share / basis) if basis else None,
            "holding_period_days": held_days,
            "is_long_term": held_days > 365,
            "days_to_long_term": max(0, 366 - held_days),
        }
        out.append(row)
    return out


def estimated_tax_on_sale(lot: Dict[str, Any], profile: Any, shares: Optional[float] = None) -> Dict[str, float]:
    sale_shares = min(float(shares if shares is not None else lot["shares"]), float(lot["shares"]))
    gain = (float(lot["current_price"]) - float(lot["cost_basis_per_share"])) * sale_shares
    state_rate = float(_profile_value(profile, "state_ltcg_rate" if lot.get("is_long_term") else "state_stcg_rate", 0.0) or 0.0)
    fed_rate = _ltcg_rate(profile, gain) if lot.get("is_long_term") else _ordinary_rate(profile, gain)
    niit = _niit_rate(profile, gain)
    rate = fed_rate + state_rate + niit
    tax = gain * rate
    return {"gain": gain, "federal_rate": fed_rate, "state_rate": state_rate, "niit_rate": niit, "combined_rate": rate, "estimated_tax": tax}


def recommend_sale(
    lots: Iterable[Any],
    profile: Any,
    objective: str = "raise_cash",
    target_amount: float = 0.0,
    long_term_grace_days: int = 45,
    target_ticker: Optional[str] = None,
    as_of: Optional[date] = None,
    prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    classified = classify_lots(lots, as_of=as_of, prices=prices)
    objective = objective or "raise_cash"
    target_amount = float(target_amount or 0.0)
    if objective == "exit_ticker" and target_ticker:
        classified = [l for l in classified if l["ticker"] == target_ticker.upper().strip()]
    selected = []
    proceeds = est_tax = gains = 0.0

    if objective == "harvest_loss":
        candidates = sorted([l for l in classified if l["unrealized_gain"] < 0], key=lambda l: l["unrealized_gain"] / max(l["shares"], 1e-9))
        stop_when = lambda: abs(gains) >= target_amount if target_amount > 0 else False
    else:
        losses = sorted([l for l in classified if l["unrealized_gain"] < 0], key=lambda l: l["unrealized_gain"] / max(l["shares"], 1e-9))
        gains_l = sorted([l for l in classified if l["unrealized_gain"] >= 0], key=lambda l: (not l["is_long_term"], -l["cost_basis_per_share"]))
        candidates = losses + gains_l
        stop_when = lambda: proceeds >= target_amount if target_amount > 0 else False

    for lot in candidates:
        if stop_when():
            break
        if lot["shares"] <= 0 or lot["current_price"] <= 0:
            continue
        if lot["unrealized_gain"] > 0 and lot["days_to_long_term"] and lot["days_to_long_term"] < long_term_grace_days:
            wait_flag = f"Consider waiting {lot['days_to_long_term']} days for long-term treatment."
        else:
            wait_flag = None
        remaining_target = target_amount - (abs(gains) if objective == "harvest_loss" else proceeds)
        shares = lot["shares"] if target_amount <= 0 else min(lot["shares"], max(0.0, remaining_target) / lot["current_price"])
        if shares <= 1e-9:
            continue
        tax = estimated_tax_on_sale(lot, profile, shares=shares)
        sale_proceeds = shares * lot["current_price"]
        proceeds += sale_proceeds
        est_tax += tax["estimated_tax"]
        gains += tax["gain"]
        selected.append({**lot, "sell_shares": shares, "sale_proceeds": sale_proceeds, **tax, "wait_flag": wait_flag})

    net_cash = proceeds - est_tax
    return {
        "objective": objective,
        "target_amount": target_amount,
        "picks": selected,
        "gross_proceeds": proceeds,
        "estimated_tax": est_tax,
        "net_cash": net_cash,
        "realized_gain": gains,
        "method": "Deterministic heuristic: losses first, then HIFO among gains with a preference for long-term lots.",
        "tax_note": "Approximate planning estimate only; verify current tax-year brackets and personal tax treatment.",
    }


def wash_sale_flags(lots: Iterable[Any], sales: Iterable[Dict[str, Any]], as_of: Optional[date] = None) -> List[Dict[str, Any]]:
    as_of = as_of or date.today()
    classified = classify_lots(lots, as_of=as_of)
    flags = []
    for sale in sales:
        if sale.get("gain", 0) >= 0:
            continue
        ticker = sale.get("ticker")
        for lot in classified:
            if lot["ticker"] != ticker or lot.get("id") == sale.get("id"):
                continue
            acquired = _as_date(lot["acquisition_date"])
            if as_of - timedelta(days=30) <= acquired <= as_of + timedelta(days=30):
                flags.append({
                    "ticker": ticker,
                    "sale_lot_id": sale.get("id"),
                    "nearby_lot_id": lot.get("id"),
                    "acquisition_date": lot["acquisition_date"],
                    "message": f"Loss sale may overlap the 61-day wash-sale window for {ticker}.",
                })
    return flags


def annual_plan(lots: Iterable[Any], profile: Any, years: int = 5, prices: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    rows = []
    carry = float(_profile_value(profile, "carryover_loss", 0.0) or 0.0)
    base_year = int(_profile_value(profile, "tax_year", date.today().year) or date.today().year)
    remaining = classify_lots(lots, as_of=date.today(), prices=prices)
    for offset in range(years):
        year = base_year + offset
        as_of = date(year, 12, 31)
        classified = classify_lots(remaining, as_of=as_of, prices=prices)
        losses = sum(l["unrealized_gain"] for l in classified if l["unrealized_gain"] < 0)
        matured_value = sum(l["market_value"] for l in classified if l["is_long_term"])
        ordinary_offset = min(3000.0, max(0.0, carry + abs(min(0.0, losses))))
        carry = max(0.0, carry + abs(min(0.0, losses)) - ordinary_offset)
        rows.append({
            "year": year,
            "long_term_market_value": matured_value,
            "harvestable_losses": losses,
            "ordinary_income_loss_offset": ordinary_offset,
            "estimated_loss_carryforward": carry,
            "note": "Approximate tranche view; does not optimize against full tax return.",
        })
    return rows
