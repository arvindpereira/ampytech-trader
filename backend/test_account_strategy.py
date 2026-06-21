import json
import math
import os
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATA_STORAGE_DIR", tempfile.mkdtemp(prefix="ampy_test_db_"))

from app.database import AppSetting, ExternalAccount, SessionLocal, init_db
from app.database.connection import engine
from app.main import (BucketsRequest, ExternalStrategyRequest, set_strategy_buckets,
                      update_external_account_strategy)
from app.services.account_strategy import (
    ALL_WEATHER, StrategyValidationError, build_account_target,
    generate_trade_proposals, validate_buckets,
)


class TestAccountStrategyService(unittest.TestCase):
    def test_growth_endpoint_uses_bucket_candidates_and_preserves_cash(self):
        snapshot = {
            "swing_suggestions": [{"ticker": "AAPL", "verdict": "BUY", "probability": 0.8}],
            "high_risk_suggestions": [{"ticker": "RKLB", "verdict": "BUY", "probability": 0.6}],
            "long_term_allocation": [{"ticker": "MSFT", "weight": 0.4, "suggested_action": "Hold"}],
        }
        buckets = {"swing": 0.4, "longterm": 0.5, "high_risk": 0.05}
        result = build_account_target({}, "growth", 100, buckets, snapshot)
        self.assertAlmostEqual(result["target_weights"]["AAPL"], 0.2)
        self.assertAlmostEqual(result["target_weights"]["MSFT"], 0.2)
        self.assertAlmostEqual(result["target_weights"]["RKLB"], 0.05)
        self.assertAlmostEqual(result["cash_target_weight"], 0.55)
        self.assertAlmostEqual(sum(result["target_weights"].values()) + result["cash_target_weight"], 1.0)

    def test_unsignalled_holding_is_not_liquidated_at_growth_endpoint(self):
        result = build_account_target(
            {"AAPL": 0.6}, "growth", 100,
            {"swing": 1.0, "longterm": 0.0, "high_risk": 0.0}, snapshot=None,
        )
        self.assertAlmostEqual(result["target_weights"]["AAPL"], 0.6)
        self.assertAlmostEqual(result["cash_target_weight"], 0.4)

    def test_buy_signal_does_not_reduce_existing_position(self):
        snapshot = {"swing_suggestions": [
            {"ticker": "AAPL", "verdict": "BUY", "probability": 0.9},
        ]}
        result = build_account_target(
            {"AAPL": 0.8}, "growth", 100,
            {"swing": 0.4, "longterm": 0.0, "high_risk": 0.0}, snapshot=snapshot,
        )
        self.assertAlmostEqual(result["target_weights"]["AAPL"], 0.8)
        self.assertAlmostEqual(result["cash_target_weight"], 0.2)

    def test_all_weather_endpoint_and_midpoint_are_exact(self):
        buckets = {"swing": 1.0, "longterm": 0.0, "high_risk": 0.0}
        endpoint = build_account_target({}, "all_weather", 0, buckets)
        self.assertEqual(endpoint["target_weights"], ALL_WEATHER)
        self.assertAlmostEqual(endpoint["cash_target_weight"], 0.0)
        midpoint = build_account_target({}, "all_weather", 50, buckets)
        for ticker, weight in ALL_WEATHER.items():
            self.assertAlmostEqual(midpoint["target_weights"][ticker], weight / 2)
        self.assertAlmostEqual(midpoint["cash_target_weight"], 0.5)

    def test_de_risk_keeps_quality_and_sheds_speculative(self):
        """Holdings-aware de-risk: a quality/low-vol holding (BRK.B) is kept far above a
        speculative/high-vol one (BYND); the crash coefficient sets the cash floor."""
        buckets = {"swing": 1.0, "longterm": 0.0, "high_risk": 0.0}
        classifications = {"BRK.B": {"tier": "core", "volatility": 0.14},
                           "BYND": {"tier": "speculative", "volatility": 2.0}}
        res = build_account_target({"BRK.B": 0.5, "BYND": 0.5}, "de_risk", 0, buckets,
                                   snapshot=None, classifications=classifications,
                                   de_risk_coefficient=0.2)
        tw = res["target_weights"]
        self.assertGreater(tw.get("BRK.B", 0.0), 5 * tw.get("BYND", 0.0))
        self.assertGreater(tw.get("BRK.B", 0.0), 0.5)                 # quality concentrated, not sold
        self.assertAlmostEqual(res["cash_target_weight"], 0.2, places=2)

    def test_defensive_mode_does_not_open_new_speculative(self):
        """A de-risking account must not open a fresh speculative position from a model BUY."""
        snapshot = {"high_risk_suggestions": [{"ticker": "BYND", "verdict": "BUY", "probability": 0.9}]}
        res = build_account_target({}, "de_risk", 10,
                                   {"swing": 0.0, "longterm": 0.0, "high_risk": 0.05},
                                   snapshot=snapshot, classifications={}, de_risk_coefficient=0.0)
        self.assertLess(res["target_weights"].get("BYND", 0.0), 0.01)

    def test_glide_endpoint_requires_crash_coefficient(self):
        buckets = {"swing": 1.0, "longterm": 0.0, "high_risk": 0.0}
        with self.assertRaises(StrategyValidationError):
            build_account_target({"AAPL": 1.0}, "glide_path", 0, buckets,
                                 classifications={}, de_risk_coefficient=None)

    def test_invalid_buckets_are_rejected_not_clamped(self):
        for value in (
            {"swing": math.nan, "longterm": 0, "high_risk": 0},
            {"swing": 0.8, "longterm": 0.3, "high_risk": 0},
            {"swing": 0, "longterm": 0, "high_risk": 0.5},
            {"swing": -0.1, "longterm": 0, "high_risk": 0},
        ):
            with self.assertRaises(StrategyValidationError):
                validate_buckets(value)

    def test_orders_do_not_oversell_or_overspend(self):
        suggestions, turnover, warnings = generate_trade_proposals(
            {"MSFT": 0.5}, 0.1, 1000.0, 0.0,
            {"AAPL": 10.0}, {"AAPL": 100.0, "MSFT": 100.0},
        )
        sell = next(item for item in suggestions if item["side"] == "SELL")
        buy = next(item for item in suggestions if item["side"] == "BUY")
        self.assertLessEqual(sell["qty"], 10.0)
        self.assertLessEqual(buy["qty"] * buy["limit_price"],
                             sell["qty"] * sell["limit_price"] - 100.0 + 1e-6)
        self.assertGreater(turnover, 0)
        self.assertTrue(any("sells before buys" in warning for warning in warnings))

    def test_fallback_price_never_generates_order(self):
        suggestions, _, warnings = generate_trade_proposals(
            {"TLT": 1.0}, 0.0, 1000.0, 1000.0, {}, {"TLT": 100.0}, {"TLT"},
        )
        self.assertEqual(suggestions, [])
        self.assertTrue(any("no order generated" in warning for warning in warnings))


class TestAccountStrategyPersistence(unittest.TestCase):
    def setUp(self):
        init_db()
        self.db = SessionLocal()
        self.db.query(ExternalAccount).delete()
        self.db.add(ExternalAccount(account_label="Joint Account", cash=1000.0,
                                    risk_profile="balanced", created_at="2026-06-21",
                                    updated_at="2026-06-21"))
        self.db.commit()

    def tearDown(self):
        self.db.query(ExternalAccount).delete()
        self.db.query(AppSetting).filter(AppSetting.key == "bucket_allocations").delete()
        self.db.commit()
        self.db.close()

    def test_strategy_update_persists_and_reset_inherits(self):
        req = ExternalStrategyRequest(strategy_mode="all_weather", aggression=25,
                                      buckets={"swing": 0.2, "longterm": 0.7, "high_risk": 0.05})
        response = update_external_account_strategy("Joint Account", req, db=self.db)
        self.assertEqual(response["strategy_mode"], "all_weather")
        self.assertFalse(response["inherits_global_buckets"])
        account = self.db.query(ExternalAccount).first()
        self.assertEqual(json.loads(account.buckets_json)["longterm"], 0.7)

        reset = ExternalStrategyRequest(strategy_mode="growth", aggression=100, buckets=None)
        response = update_external_account_strategy("Joint Account", reset, db=self.db)
        self.assertTrue(response["inherits_global_buckets"])
        self.assertIsNone(account.buckets_json)

    def test_strategy_columns_exist_and_init_is_idempotent(self):
        from sqlalchemy import inspect
        init_db()
        init_db()
        columns = {column["name"] for column in inspect(engine).get_columns("external_accounts")}
        self.assertTrue({"strategy_mode", "aggression", "buckets_json"}.issubset(columns))

    def test_global_bucket_endpoint_rejects_instead_of_clamping(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            set_strategy_buckets(BucketsRequest(swing=0.0, longterm=0.0, high_risk=0.5), db=self.db)
        response = set_strategy_buckets(
            BucketsRequest(swing=0.4, longterm=0.5, high_risk=0.05), db=self.db,
        )
        self.assertEqual(response["buckets"]["high_risk"], 0.05)


if __name__ == "__main__":
    unittest.main()
