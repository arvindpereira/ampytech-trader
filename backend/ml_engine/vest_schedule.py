"""Vest/purchase schedule helpers for Equity Advisor holdings."""
from __future__ import annotations

import json
from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from app.database.models import EquityLot, EquityVestSchedule


def _parse_date(s: str) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _safe_date(y: int, m: int, d: int) -> date:
    d = min(d, monthrange(y, m)[1])
    return date(y, m, d)


def advance_vest_date(
    after: date,
    cadence: str,
    vest_day: int,
    vest_months: list[int] | None = None,
) -> date:
    """Next vest on/after `after` matching the schedule."""
    vest_day = max(1, min(28, int(vest_day or 1)))
    months = sorted(vest_months or [])

    if cadence == "monthly":
        y, m = after.year, after.month
        candidate = _safe_date(y, m, vest_day)
        if candidate <= after:
            m += 1
            if m > 12:
                y += 1
                m = 1
            candidate = _safe_date(y, m, vest_day)
        return candidate

    if cadence == "annual":
        months = months or [after.month]
        anchor = months[0]
        for year in range(after.year, after.year + 3):
            candidate = _safe_date(year, anchor, vest_day)
            if candidate > after:
                return candidate
        return _safe_date(after.year + 1, anchor, vest_day)

    if cadence == "semi_annual":
        months = months or [6, 12]
        for year in range(after.year, after.year + 3):
            for mo in months:
                candidate = _safe_date(year, mo, vest_day)
                if candidate > after:
                    return candidate
        return _safe_date(after.year + 1, months[0], vest_day)

    # quarterly (default)
    months = months or [3, 6, 9, 12]
    for year in range(after.year, after.year + 3):
        for mo in months:
            candidate = _safe_date(year, mo, vest_day)
            if candidate > after:
                return candidate
    return _safe_date(after.year + 1, months[0], vest_day)


def infer_schedule_from_lots(lots: list[EquityLot]) -> Optional[dict]:
    """Guess cadence/day/months from historical lot acquisition dates."""
    if not lots:
        return None
    dates = [_parse_date(l.acquisition_date) for l in lots]
    days = Counter(d.day for d in dates)
    months = Counter(d.month for d in dates)
    vest_day = days.most_common(1)[0][0]
    common_months = sorted(m for m, _ in months.most_common())

    lot_type = lots[0].lot_type if lots else "rsu"
    avg_shares = sum(l.shares for l in lots) / len(lots)

    cadence = "quarterly"
    vest_months = common_months
    if lot_type == "espp":
        cadence = "semi_annual"
        if len(common_months) >= 2:
            vest_months = common_months[:2]
        else:
            vest_months = [6, 12]
    elif len(common_months) >= 4:
        cadence = "quarterly"
        vest_months = common_months[:4]
    elif len(common_months) == 2:
        cadence = "semi_annual"
        vest_months = common_months
    elif len(common_months) == 1:
        cadence = "annual"
        vest_months = common_months

    today = date.today()
    next_vest = advance_vest_date(today, cadence, vest_day, vest_months)
    return {
        "lot_type": lot_type,
        "cadence": cadence,
        "vest_day": vest_day,
        "vest_months": vest_months,
        "next_vest_date": next_vest.isoformat(),
        "est_shares": round(avg_shares, 0),
        "notes": "Inferred from imported lot history",
    }


def schedule_dict(row: EquityVestSchedule) -> dict:
    months = []
    if row.vest_months:
        try:
            months = json.loads(row.vest_months)
        except Exception:
            months = []
    complete = bool(getattr(row, "vesting_complete", False))
    today = date.today()
    days_until = None
    upcoming = []
    if not complete:
        next_d = _parse_date(row.next_vest_date)
        days_until = (next_d - today).days
        cursor = today
        for _ in range(4):
            nxt = advance_vest_date(cursor, row.cadence, row.vest_day or 1, months or None)
            if nxt.isoformat() in {u["date"] for u in upcoming}:
                break
            upcoming.append({"date": nxt.isoformat(), "est_shares": row.est_shares})
            cursor = nxt
    return {
        "ticker": row.ticker,
        "lot_type": row.lot_type,
        "cadence": row.cadence,
        "vest_day": row.vest_day,
        "vest_months": months,
        "next_vest_date": row.next_vest_date,
        "est_shares": row.est_shares,
        "vesting_complete": complete,
        "notes": row.notes,
        "updated_at": row.updated_at,
        "days_until_next": days_until,
        "upcoming": upcoming,
    }


def ensure_vest_schedules(db) -> list[dict]:
    """Create inferred schedules for tickers/lot-types that lack one."""
    from app.database import ExternalAccount
    all_lots = db.query(EquityLot).all()
    external_labels = {acct.account_label for acct in db.query(ExternalAccount).all()}
    lots = [l for l in all_lots if l.account_label not in external_labels]
    by_key: dict[tuple[str, str], list[EquityLot]] = {}
    for lot in lots:
        lt = lot.lot_type if lot.lot_type in ("rsu", "espp") else "rsu"
        by_key.setdefault((lot.ticker.upper(), lt), []).append(lot)

    now = datetime.now().isoformat(timespec="seconds")
    out = []
    for (ticker, lot_type), group in sorted(by_key.items()):
        row = db.query(EquityVestSchedule).filter(
            EquityVestSchedule.ticker == ticker,
            EquityVestSchedule.lot_type == lot_type,
        ).first()
        if not row:
            inferred = infer_schedule_from_lots(group)
            if not inferred:
                continue
            row = EquityVestSchedule(
                ticker=ticker,
                lot_type=lot_type,
                cadence=inferred["cadence"],
                vest_day=inferred["vest_day"],
                vest_months=json.dumps(inferred["vest_months"]),
                next_vest_date=inferred["next_vest_date"],
                est_shares=inferred.get("est_shares"),
                notes=inferred.get("notes"),
                updated_at=now,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
        out.append(schedule_dict(row))
    return out


def upsert_vest_schedule(db, payload: dict) -> dict:
    ticker = payload["ticker"].upper().strip()
    lot_type = payload.get("lot_type", "rsu")
    if lot_type not in ("rsu", "espp"):
        lot_type = "rsu"
    cadence = payload.get("cadence", "quarterly")
    if cadence not in ("quarterly", "semi_annual", "monthly", "annual"):
        cadence = "quarterly"
    vest_day = int(payload.get("vest_day") or 1)
    months = payload.get("vest_months") or []
    if isinstance(months, str):
        months = json.loads(months)
    vesting_complete = bool(payload.get("vesting_complete", False))

    now = datetime.now().isoformat(timespec="seconds")
    row = db.query(EquityVestSchedule).filter(
        EquityVestSchedule.ticker == ticker,
        EquityVestSchedule.lot_type == lot_type,
    ).first()
    if not row:
        row = EquityVestSchedule(ticker=ticker, lot_type=lot_type, updated_at=now,
                                 next_vest_date=date.today().isoformat())
        db.add(row)

    if vesting_complete:
        next_vest = str(payload.get("next_vest_date") or row.next_vest_date or date.today().isoformat())[:10]
    else:
        if not payload.get("next_vest_date"):
            raise ValueError("next_vest_date is required when vesting is active")
        next_vest = str(payload["next_vest_date"])[:10]
        _parse_date(next_vest)

    row.cadence = cadence
    row.vest_day = vest_day
    row.vest_months = json.dumps(sorted(int(m) for m in months)) if months else None
    row.next_vest_date = next_vest
    row.est_shares = float(payload["est_shares"]) if payload.get("est_shares") not in (None, "") else None
    row.vesting_complete = vesting_complete
    row.notes = payload.get("notes")
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return schedule_dict(row)
