"""Keep Equity Advisor holdings in the data pipeline without letting the bot trade them.

External RSU/ESPP names (e.g. PINS, ADBE) are synced into `universe_tickers` with strategy='hold'
so prices, news, fundamentals, and models all see them. Tickers flagged for auto-trade blocking
(default: any name with RSU lots — harvest/wash-sale protection) get a permanent `TradingBlock`.

All preferences below live in SQLite (backed up by `make backup` / `make db-backup`):
  - equity_lots, equity_vest_schedules, equity_auto_trade_blocks, tax_profile, trading_blocks
"""
from __future__ import annotations

import json
from datetime import datetime

from app.database.models import (
    AppSetting, EquityAutoTradeBlock, EquityLot, TradingBlock, UniverseTicker,
)
from data_ingestion.price_fetcher import equity_lot_tickers

EQUITY_BLOCKS_INITIALIZED_KEY = "equity_auto_trade_blocks_initialized"
# Legacy AppSetting keys — migrated once into equity_auto_trade_blocks table.
_LEGACY_BLOCKS_KEY = "equity_auto_trade_blocks"


def _migrate_legacy_auto_trade_blocks(db) -> None:
    """One-time import from AppSetting JSON into equity_auto_trade_blocks rows."""
    if db.query(EquityAutoTradeBlock).count() > 0:
        return
    row = db.query(AppSetting).filter(AppSetting.key == _LEGACY_BLOCKS_KEY).first()
    if not row or not row.value:
        return
    try:
        tickers = json.loads(row.value)
    except Exception:
        return
    now = datetime.now().isoformat(timespec="seconds")
    for t in tickers:
        if t:
            db.add(EquityAutoTradeBlock(ticker=str(t).upper().strip(), blocked=True, updated_at=now))
    db.commit()


def _rsu_harvest_tickers(db) -> list[str]:
    """Tickers with RSU lots held externally — default candidates for auto-trade blocks."""
    rows = db.query(EquityLot.ticker).filter(EquityLot.lot_type == "rsu").distinct().all()
    return sorted({r[0].upper().strip() for r in rows if r[0]})


def get_equity_auto_trade_blocks(db, init_defaults: bool = True) -> list[str]:
    _migrate_legacy_auto_trade_blocks(db)
    blocked = sorted(
        r.ticker for r in db.query(EquityAutoTradeBlock).filter(
            EquityAutoTradeBlock.blocked == True  # noqa: E712
        ).all()
    )
    if blocked or not init_defaults:
        return blocked

    init_flag = db.query(AppSetting).filter(AppSetting.key == EQUITY_BLOCKS_INITIALIZED_KEY).first()
    if init_flag:
        return []

    defaults = _rsu_harvest_tickers(db)
    if defaults:
        set_equity_auto_trade_blocks(db, defaults, mark_initialized=True)
        return defaults
    return []


def set_equity_auto_trade_blocks(db, tickers: list[str], mark_initialized: bool = False) -> list[str]:
    clean = sorted({t.upper().strip() for t in tickers if t})
    now = datetime.now().isoformat(timespec="seconds")
    existing = {r.ticker: r for r in db.query(EquityAutoTradeBlock).all()}
    for ticker in clean:
        row = existing.get(ticker)
        if row:
            row.blocked = True
            row.updated_at = now
        else:
            db.add(EquityAutoTradeBlock(ticker=ticker, blocked=True, updated_at=now))
    for ticker, row in existing.items():
        if ticker not in clean:
            row.blocked = False
            row.updated_at = now
    if mark_initialized:
        flag = db.query(AppSetting).filter(AppSetting.key == EQUITY_BLOCKS_INITIALIZED_KEY).first()
        if not flag:
            db.add(AppSetting(key=EQUITY_BLOCKS_INITIALIZED_KEY, value="1"))
    db.commit()
    sync_equity_trading_blocks(db, clean)
    return clean


def set_equity_auto_trade_block(db, ticker: str, blocked: bool) -> list[str]:
    ticker = ticker.upper().strip()
    now = datetime.now().isoformat(timespec="seconds")
    row = db.query(EquityAutoTradeBlock).filter(EquityAutoTradeBlock.ticker == ticker).first()
    if row:
        row.blocked = blocked
        row.updated_at = now
    else:
        db.add(EquityAutoTradeBlock(ticker=ticker, blocked=blocked, updated_at=now))
    flag = db.query(AppSetting).filter(AppSetting.key == EQUITY_BLOCKS_INITIALIZED_KEY).first()
    if not flag:
        db.add(AppSetting(key=EQUITY_BLOCKS_INITIALIZED_KEY, value="1"))
    db.commit()
    current = get_equity_auto_trade_blocks(db, init_defaults=False)
    sync_equity_trading_blocks(db, current)
    return current


def sync_equity_trading_blocks(db, blocked_tickers: list[str] | None = None):
    """Ensure permanent buy-blocks exist for flagged equity tickers; release blocks user cleared."""
    blocked = set(blocked_tickers if blocked_tickers is not None else get_equity_auto_trade_blocks(db))
    held = set(equity_lot_tickers(db))
    now = datetime.now().isoformat(timespec="seconds")

    active = db.query(TradingBlock).filter(
        TradingBlock.active == True,  # noqa: E712
        TradingBlock.block_type == "permanent",
        TradingBlock.ticker.in_(held) if held else False,
    ).all() if held else []

    active_by_ticker = {b.ticker.upper(): b for b in active}
    for ticker in blocked & held:
        if ticker in active_by_ticker:
            continue
        db.add(TradingBlock(
            ticker=ticker,
            block_type="permanent",
            active=True,
            reason="Equity Advisor: block auto-trading for external/harvest holding",
            account_label="equity_advisor",
            created_at=now,
        ))

    for ticker, block in active_by_ticker.items():
        if ticker not in blocked and (block.account_label == "equity_advisor" or
                                      (block.reason or "").startswith("Equity Advisor:")):
            block.active = False

    db.commit()


def sync_equity_advisor_universe(db) -> dict:
    """Add equity holdings to universe_tickers (strategy=hold) and sync trade blocks."""
    tickers = equity_lot_tickers(db)
    added = []
    for ticker in tickers:
        row = db.query(UniverseTicker).filter(UniverseTicker.ticker == ticker).first()
        if not row:
            db.add(UniverseTicker(ticker=ticker, strategy="hold"))
            added.append(ticker)
    db.commit()

    blocked = get_equity_auto_trade_blocks(db)
    sync_equity_trading_blocks(db, blocked)

    if added:
        print(f"Equity Advisor universe sync: added {added} as strategy=hold (data pipeline only)")
    return {"added": added, "held": tickers, "auto_trade_blocked": blocked}


def equity_advisor_table_counts(db) -> dict:
    """Row counts for Equity Advisor tables — used by backup verification."""
    from app.database.models import EquityVestSchedule, TaxProfile
    return {
        "equity_lots": db.query(EquityLot).count(),
        "equity_vest_schedules": db.query(EquityVestSchedule).count(),
        "equity_auto_trade_blocks": db.query(EquityAutoTradeBlock).filter(
            EquityAutoTradeBlock.blocked == True  # noqa: E712
        ).count(),
        "tax_profile": db.query(TaxProfile).count(),
        "equity_trading_blocks": db.query(TradingBlock).filter(
            TradingBlock.active == True,  # noqa: E712
            TradingBlock.account_label == "equity_advisor",
        ).count(),
    }
