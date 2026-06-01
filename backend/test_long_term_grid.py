import sys
import os
from datetime import datetime, timedelta

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db, SessionLocal, VirtualPosition, VirtualOrder, VirtualAccount, RecentPrice
from execution.executor import get_long_term_available_shares, execute_long_term_grid_trades

class MockAlpacaAPI:
    def __init__(self, equity=100000.0, cash=100000.0):
        self.equity = equity
        self.buying_power = cash
        self.submitted_orders = []

    def get_account(self):
        class Account:
            def __init__(self, eq, bp):
                self.equity = eq
                self.buying_power = bp
        return Account(self.equity, self.buying_power)

    def submit_order(self, symbol, qty, side, type, time_in_force):
        self.submitted_orders.append({
            "symbol": symbol,
            "qty": qty,
            "side": side,
            "type": type
        })
        print(f"[MockAlpacaAPI] Order submitted: {side} {qty} shares of {symbol}")

def run_test():
    init_db()
    db = SessionLocal()

    try:
        # Clear existing data for test ticker MSFT
        db.query(VirtualPosition).filter(VirtualPosition.ticker == "MSFT").delete()
        db.query(VirtualOrder).filter(VirtualOrder.ticker == "MSFT").delete()
        db.commit()

        print("--- Test 1: Empty state ---")
        shares = get_long_term_available_shares(db, "MSFT", "2026-06-01")
        assert shares == 0.0, f"Expected 0.0, got {shares}"
        print("Passed Test 1!")

        print("\n--- Test 2: Manual holding (should default to long-term) ---")
        pos = VirtualPosition(ticker="MSFT", quantity=50.0, entry_price=300.0, policy="rebalance")
        db.add(pos)
        db.commit()

        shares = get_long_term_available_shares(db, "MSFT", "2026-06-01")
        assert shares == 50.0, f"Expected 50.0, got {shares}"
        print("Passed Test 2!")

        print("\n--- Test 3: Virtual Buy Order (short-term lot) ---")
        # Add a virtual order bought 10 days ago (relative to 2026-06-01)
        # Note: We now have 60 total shares (50 manual + 10 bought virtually)
        pos.quantity = 60.0
        db.commit()

        order = VirtualOrder(
            id="test-buy-1",
            ticker="MSFT",
            qty=10.0,
            side="buy",
            type="market",
            status="filled",
            filled_price=310.0,
            created_at="2026-05-22T10:00:00",
            sim_date="2026-05-22"
        )
        db.add(order)
        db.commit()

        shares = get_long_term_available_shares(db, "MSFT", "2026-06-01")
        # The 10 shares are short term (10 days old), so only 50 manual shares are long term
        assert shares == 50.0, f"Expected 50.0, got {shares}"
        print("Passed Test 3!")

        print("\n--- Test 4: Virtual Buy Order (long-term lot) ---")
        # Add a virtual order bought 400 days ago
        pos.quantity = 75.0
        db.commit()

        order_old = VirtualOrder(
            id="test-buy-2",
            ticker="MSFT",
            qty=15.0,
            side="buy",
            type="market",
            status="filled",
            filled_price=280.0,
            created_at="2025-04-01T10:00:00",
            sim_date="2025-04-01"
        )
        db.add(order_old)
        db.commit()

        shares = get_long_term_available_shares(db, "MSFT", "2026-06-01")
        # 50 manual (long-term) + 15 old buy (long-term) = 65 shares long-term
        assert shares == 65.0, f"Expected 65.0, got {shares}"
        print("Passed Test 4!")

        print("\n--- Test 5: Virtual Sell Order (FIFO consumption) ---")
        # Sell 10 shares. This should consume from the oldest lot (manual lot), leaving 40 manual shares
        # Long term total should become 40 manual + 15 old = 55 shares.
        pos.quantity = 65.0
        db.commit()

        order_sell = VirtualOrder(
            id="test-sell-1",
            ticker="MSFT",
            qty=10.0,
            side="sell",
            type="market",
            status="filled",
            filled_price=320.0,
            created_at="2026-05-25T10:00:00",
            sim_date="2026-05-25"
        )
        db.add(order_sell)
        db.commit()

        shares = get_long_term_available_shares(db, "MSFT", "2026-06-01")
        assert shares == 45.0, f"Expected 45.0, got {shares}"
        print("Passed Test 5!")

        print("\n--- Test 6: Grid execution check (BUY) ---")
        # If we are underweight and price is down 3%
        # Mock suggestions: MPT suggests 20% weight on MSFT. Portfolio = 100k. Target = 20k.
        # Current value of MSFT position: 65 shares * $280 = $18,200. We are underweight!
        # Cost basis is around $295. Current price is $280 (down 5% relative to cost basis).
        suggestions = {
            "long_term_allocation": [{"ticker": "MSFT", "weight": 0.20}]
        }

        # Ensure price is in DB for simulation date
        db.query(RecentPrice).filter(RecentPrice.ticker == "MSFT", RecentPrice.date == "2026-06-01").delete()
        price_bar = RecentPrice(ticker="MSFT", date="2026-06-01", open=280.0, high=285.0, low=275.0, close=280.0, volume=100000)
        db.add(price_bar)
        db.commit()

        api = MockAlpacaAPI(equity=100000.0, cash=50000.0)
        execute_long_term_grid_trades(db, api, suggestions, "2026-06-01")

        # Tranche cap is 2% of 100k = $2,000. At $280/share, that is 7.14 shares.
        # Difference is (20k - 18.2k) / 280 = 6.43 shares.
        # Min of diff (6.43) and tranche cap (7.14) is 6.43 shares!
        assert len(api.submitted_orders) == 1, "Expected 1 order submitted"
        submitted = api.submitted_orders[0]
        assert submitted["side"] == "buy"
        assert round(submitted["qty"], 2) == 6.43 or round(submitted["qty"], 2) == 6.42, f"Got quantity: {submitted['qty']}"
        print("Passed Test 6!")

        print("\n--- Test 7: Grid execution check (SELL with FIFO cap) ---")
        # Target weight is 5% (Target value: 5k)
        # Current position is 65 shares. Price is $320 (up 8.5% relative to entry basis). We are overweight!
        # Sell difference is (20.8k - 5k) / 320 = 49.38 shares.
        # Tranche cap is 2% of 100k = $2,000 / $320 = 6.25 shares.
        # Long-term shares available is 55.0.
        # Min of diff (49.38) and tranche cap (6.25) is 6.25 shares. This is capped by long-term shares (55.0), so 6.25 should execute!
        db.query(RecentPrice).filter(RecentPrice.ticker == "MSFT", RecentPrice.date == "2026-06-01").delete()
        price_bar_high = RecentPrice(ticker="MSFT", date="2026-06-01", open=320.0, high=325.0, low=315.0, close=320.0, volume=100000)
        db.add(price_bar_high)
        db.commit()

        suggestions_low = {
            "long_term_allocation": [{"ticker": "MSFT", "weight": 0.05}]
        }
        api_sell = MockAlpacaAPI(equity=100000.0, cash=50000.0)
        execute_long_term_grid_trades(db, api_sell, suggestions_low, "2026-06-01")

        assert len(api_sell.submitted_orders) == 1, "Expected 1 order submitted"
        submitted_sell = api_sell.submitted_orders[0]
        assert submitted_sell["side"] == "sell"
        assert round(submitted_sell["qty"], 2) == 6.25, f"Expected 6.25, got {submitted_sell['qty']}"
        print("Passed Test 7!")

        print("\n--- Test 8: Grid execution check (SELL skipped due to Tax Lock) ---")
        # If we have 0 long term shares. (We clear the manual holding lot and the old lot)
        db.query(VirtualOrder).filter(VirtualOrder.ticker == "MSFT").delete()

        # Insert only a short term buy order (bought 5 days ago)
        order_st = VirtualOrder(
            id="test-buy-st",
            ticker="MSFT",
            qty=65.0,
            side="buy",
            type="market",
            status="filled",
            filled_price=295.0,
            created_at="2026-05-27T10:00:00",
            sim_date="2026-05-27"
        )
        db.add(order_st)
        db.commit()

        api_sell_skip = MockAlpacaAPI(equity=100000.0, cash=50000.0)
        execute_long_term_grid_trades(db, api_sell_skip, suggestions_low, "2026-06-01")

        assert len(api_sell_skip.submitted_orders) == 0, f"Expected 0 orders submitted (skipped due to tax lock), got {len(api_sell_skip.submitted_orders)}"
        print("Passed Test 8!")

        print("\nALL UNIT TESTS PASSED SUCCESSFULLY! ✅")

    finally:
        # Cleanup test data
        db.query(VirtualPosition).filter(VirtualPosition.ticker == "MSFT").delete()
        db.query(VirtualOrder).filter(VirtualOrder.ticker == "MSFT").delete()
        db.commit()
        db.close()

if __name__ == "__main__":
    run_test()
