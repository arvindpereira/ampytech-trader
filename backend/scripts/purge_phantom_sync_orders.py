"""One-off cleanup: remove phantom sync orders that make the local order log look like churn.

A `sync-sell` is phantom when Alpaca shows no real filled sell for that ticker — it came from a
stale/eventually-consistent positions read during reconciliation. Removing it restores the FIFO
lots that the phantom sale wrongly consumed. If, after removing a phantom sell, the recorded buy
lots exceed the actual holding, the most-recent synthetic buys are dropped so the lot history
matches the real position (oldest lots kept for correct tax aging).

VirtualPosition quantities are NOT touched (they already match the broker) — this only cleans the
VirtualOrder ledger / FIFO lots.

Usage:
  python scripts/purge_phantom_sync_orders.py            # dry-run (shows what it would remove)
  python scripts/purge_phantom_sync_orders.py --apply    # actually delete
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, VirtualOrder, VirtualPosition
from execution.executor import get_alpaca_api, _has_real_broker_sell


def purge(apply: bool = False):
    db = SessionLocal()
    api = get_alpaca_api()
    if not api:
        print("No Alpaca connection — cannot verify real sells. Aborting.")
        return

    broker_qty = {p.symbol: float(p.qty) for p in api.list_positions()}
    orders = db.query(VirtualOrder).filter(VirtualOrder.mode == "real").all()
    by_ticker: dict = {}
    for o in orders:
        by_ticker.setdefault(o.ticker, []).append(o)

    to_delete = []
    for ticker, olist in sorted(by_ticker.items()):
        sync_sells = [o for o in olist if str(o.id).startswith("sync-sell-")]
        if not sync_sells:
            continue
        if _has_real_broker_sell(api, ticker):
            print(f"{ticker}: has a real broker sell — leaving its sync-sells alone.")
            continue

        # All sync-sells here are phantom → remove them.
        for o in sync_sells:
            to_delete.append(o)

        # Reconcile remaining buy lots to the actual holding.
        remaining = [o for o in olist if o not in sync_sells]
        filled_buys = sorted([o for o in remaining if o.side == "buy" and o.status == "filled"],
                             key=lambda o: o.created_at or "")
        filled_sells_qty = sum(float(o.qty) for o in remaining if o.side == "sell" and o.status == "filled")
        lots_net = sum(float(o.qty) for o in filled_buys) - filled_sells_qty
        current = broker_qty.get(ticker, 0.0)
        excess = lots_net - current

        dropped_buys = []
        # Drop most-recent SYNTHETIC buys first until lots match the real holding.
        for o in reversed(filled_buys):
            if excess <= 0.0001:
                break
            if str(o.id).startswith("sync-buy-"):
                dropped_buys.append(o)
                to_delete.append(o)
                excess -= float(o.qty)

        print(f"{ticker}: holding={current:.4f}  phantom sells removed={len(sync_sells)} "
              f"({sum(float(o.qty) for o in sync_sells):.4f} sh)  excess sync-buys dropped={len(dropped_buys)}")

    print(f"\n{'APPLYING' if apply else 'DRY-RUN'}: {len(to_delete)} VirtualOrder rows to delete.")
    if apply and to_delete:
        for o in to_delete:
            db.delete(o)
        db.commit()
        print("Done. Deleted.")
    elif not apply:
        print("Re-run with --apply to delete.")
    db.close()


if __name__ == "__main__":
    purge(apply="--apply" in sys.argv)
