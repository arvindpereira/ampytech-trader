import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import get_daily_suggestions
from app.database import SessionLocal

print("Running suggestions endpoint verification...")
db = SessionLocal()
try:
    res = get_daily_suggestions(date=None, db=db)
    print("Suggestions run completed successfully!")
    print("Result type:", type(res))
    if isinstance(res, dict):
        print("Keys in suggestions result:", list(res.keys()))
        if "suggestions" in res:
            print("Suggestions count:", len(res["suggestions"]))
            if res["suggestions"]:
                print("First suggestion:", res["suggestions"][0])
    else:
        print("Response structure:", res)
except Exception as e:
    import traceback
    print("Error occurred:")
    traceback.print_exc()
finally:
    db.close()
