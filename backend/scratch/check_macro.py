import requests
import json
import os

# Load from .env
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ[k] = v

MASSIVE_BASE_URL = "https://api.massive.com"
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "")
# Try date.gte filter and a larger limit to see if they work
url = f"{MASSIVE_BASE_URL}/fed/v1/treasury-yields?date.gte=1996-01-01&limit=10000"

headers = {"Authorization": f"Bearer {MASSIVE_API_KEY}"} if MASSIVE_API_KEY else {}


try:
    response = requests.get(url, headers=headers)
    print("Status Code:", response.status_code)
    data = response.json()
    print("Response keys:", list(data.keys()))
    results = data.get("results", [])
    print("Total results returned:", len(results))
    print("Next URL/cursor if any:", data.get("next_url") or data.get("cursor") or data.get("next"))


    if results:
        # Sort results by date asc
        results = sorted(results, key=lambda x: x["date"])
        print("First 5 results:")
        for r in results[:5]:
            print("  ", r)
        print("Last 5 results:")
        for r in results[-5:]:
            print("  ", r)
    else:
        print("No results returned.")
except Exception as e:
    print("Error:", e)
