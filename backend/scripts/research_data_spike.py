"""Spike script: probe analyst/search API availability (run manually)."""
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import FINNHUB_API_KEY, MASSIVE_API_KEY


def main():
    tickers = ["NVDA", "IONQ", "BRK-B"]
    out = {"finnhub": {}, "massive": {}}
    if FINNHUB_API_KEY:
        import requests
        for t in tickers:
            for path in ("/stock/price-target", "/stock/recommendation"):
                r = requests.get(
                    f"https://finnhub.io/api/v1{path}",
                    params={"symbol": t, "token": FINNHUB_API_KEY},
                    timeout=15,
                )
                out["finnhub"][f"{t}{path}"] = r.status_code
    if MASSIVE_API_KEY:
        from data_ingestion.analyst_fetcher import _massive_get
        payload = _massive_get("/benzinga/v1/analyst-ratings", {"ticker": "NVDA", "limit": "3"})
        out["massive"]["nvda_sample_keys"] = list((payload or {}).get("results", [{}])[0].keys()) if payload else None
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
