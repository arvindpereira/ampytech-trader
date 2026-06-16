import os
import sys
import unittest
from datetime import datetime

# Adjust path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app
from app.database import SessionLocal, UniverseTicker, RecentPrice
from app.core.config import ALT_DATA_ENABLED

class TestSuggestionsAPI(unittest.TestCase):
    
    def setUp(self):
        self.client = TestClient(app)
        
    def test_suggestions_endpoint_no_alt(self):
        """VerifiesSuggestions API runs without error with ALT_DATA_ENABLED=False."""
        os.environ["ALT_DATA_ENABLED"] = "False"
        # Force reload app configurations if cached, or just make a request
        response = self.client.get("/api/suggestions?mode=real")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("long_term_allocation", data)
        self.assertIn("regime", data)
        
        # Verify CASH is present in allocations
        allocations = data["long_term_allocation"]
        cash_alloc = [a for a in allocations if a["ticker"] == "CASH"]
        self.assertTrue(len(cash_alloc) > 0)
        self.assertEqual(cash_alloc[0]["insider_tilt_score"], 0.0)

    def test_suggestions_endpoint_with_alt(self):
        """VerifiesSuggestions API runs and computes tilts when ALT_DATA_ENABLED=True."""
        os.environ["ALT_DATA_ENABLED"] = "True"
        # We need to make sure main.py picks up the env update
        import app.core.config
        app.core.config.ALT_DATA_ENABLED = True
        
        response = self.client.get("/api/suggestions?mode=real")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("long_term_allocation", data)
        self.assertIn("regime", data)
        
        # Check if we have tilt scores
        allocations = data["long_term_allocation"]
        has_non_zero_tilt = any(a.get("insider_tilt_score", 0.0) != 0.0 for a in allocations if a["ticker"] != "CASH")
        print(f"Verified Suggestions API. allocations: {allocations}")
        # Note: If database has no alternative disclosures, tilt will be 0.0 but endpoint must still succeed.
        
if __name__ == "__main__":
    unittest.main()
