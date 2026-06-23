import os
import json
import sys
import requests
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, NewsLLMScore, VirtualPosition, EquityLot
from app.core.config import ALPHA_VANTAGE_API_KEY, TICKER_UNIVERSE, BASE_DIR

STATE_FILE = os.path.join(BASE_DIR, "data", "alphavantage_backfill_state.json")

PRIORITY_TICKERS = [
    # Top holdings from current portfolio (fallback)
    "NVDA", "HOOD", "META", "NFLX", "SHOP", "TSLA", "RYCEY", "BABA", "AAPL", "WMT",
    "AMZN", "AMD", "BRK.B", "GOOGL", "ROKU", "MSFT", "QCOM", "GEV", "TSM", "MU",
    "PYPL", "DELL", "ISRG", "PLTR", "HMC", "LUV", "NSANY", "FSLY", "INTC", "BYND",
    "JKS", "SMCI", "AVAV", "SNDK", "DASH", "MRNA", "CAT", "AMAT", "AVGO", "RGTI",
    "ARM", "CRWD", "UBER", "MMM", "FDX", "GE", "BAC", "NOK", "BB", "IBM", "RUN",
    "DKNG", "NIO",
    # Rest of universe
    "CSCO", "ORCL", "JPM", "LLY", "PG", "JNJ"
]

def get_dynamic_priority_tickers(db) -> list:
    try:
        held_virtual = [r[0] for r in db.query(VirtualPosition.ticker).filter(
            VirtualPosition.mode == "real",
            VirtualPosition.quantity > 0
        ).all()]
    except Exception as e:
        print(f"Error querying virtual positions: {e}")
        held_virtual = []

    try:
        held_equity = [r[0] for r in db.query(EquityLot.ticker).filter(
            EquityLot.shares > 0
        ).all()]
    except Exception as e:
        print(f"Error querying equity lots: {e}")
        held_equity = []

    held_set = {t.upper().strip() for t in held_virtual + held_equity if t}
    universe_set = {t.upper().strip() for t in TICKER_UNIVERSE if t}
    all_tickers = held_set.union(universe_set)

    # Standard ETFs that we want to de-prioritize
    etfs = {"SPY", "QQQ", "XLK", "XLF", "XLE", "XLV", "XLP", "GLD", "IAU", "VOO", "VTI", "VXUS", "VTSAX", "VFIAX", "VUG", "IWF", "VASGX", "VSMGX", "REMX", "PALL", "BND", "AGG", "VT"}

    held_stocks = sorted([t for t in held_set if t not in etfs])
    held_etfs = sorted([t for t in held_set if t in etfs])
    univ_stocks = sorted([t for t in universe_set if t not in held_set and t not in etfs])
    univ_etfs = sorted([t for t in universe_set if t not in held_set and t in etfs])
    other = sorted([t for t in all_tickers if t not in held_set and t not in universe_set])

    priority_list = held_stocks + held_etfs + univ_stocks + univ_etfs + other
    # Fallback to PRIORITY_TICKERS if list is empty
    return [t for t in priority_list if t] or PRIORITY_TICKERS


def load_ipo_dates() -> dict:
    ipo_path = os.path.join(BASE_DIR, "data", "ipo_markers.json")
    if os.path.exists(ipo_path):
        try:
            with open(ipo_path) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading ipo_markers.json: {e}")
    return {}


def get_intervals(today=None):
    if today is None:
        today = datetime.now()
    intervals = []
    for i in range(5):
        end_dt = today - timedelta(days=365.25 * i)
        start_dt = today - timedelta(days=365.25 * (i + 1))
        intervals.append({
            "name": f"Year_{i+1}",
            "start": start_dt.strftime("%Y%m%dT%H%M"),
            "end": end_dt.strftime("%Y%m%dT%H%M")
        })
    return intervals

def load_state():
    if not os.path.exists(STATE_FILE):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        return {"ticker_states": {}, "daily_request_count": 0, "last_reset_date": datetime.now().date().isoformat()}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"ticker_states": {}, "daily_request_count": 0, "last_reset_date": datetime.now().date().isoformat()}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Error saving AlphaVantage backfill state: {e}")

def add_one_minute(ts_str):
    try:
        dt = datetime.strptime(ts_str[:13], "%Y%m%dT%H%M")
        next_dt = dt + timedelta(minutes=1)
        return next_dt.strftime("%Y%m%dT%H%M")
    except Exception:
        return ts_str[:13]

def _upsert_scores(db, rows):
    if not rows:
        return 0
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    stmt = sqlite_insert(NewsLLMScore).values(rows).on_conflict_do_nothing(
        index_elements=["ticker", "article_id"])
    res = db.execute(stmt)
    db.commit()
    return res.rowcount

def run_backfill_step() -> dict:
    if not ALPHA_VANTAGE_API_KEY:
        print("AlphaVantage API key missing. Skipping backfill.")
        return {"status": "error", "message": "API key missing"}

    state = load_state()
    today_str = datetime.now().date().isoformat()
    if state.get("last_reset_date") != today_str:
        state["daily_request_count"] = 0
        state["last_reset_date"] = today_str

    if state.get("daily_request_count", 0) >= 24:
        print("AlphaVantage daily request limit (24) reached. Skipping step.")
        return {"status": "limit_reached", "message": "Daily request limit (24) reached"}

    intervals = get_intervals()
    ticker_states = state.setdefault("ticker_states", {})

    db = SessionLocal()
    try:
        priority_tickers = get_dynamic_priority_tickers(db)
        ipo_dates = load_ipo_dates()

        # 1. Initialize states for tickers if missing
        for t in priority_tickers:
            t_state = ticker_states.setdefault(t, {})
            t_intervals = t_state.setdefault("intervals", [])

            ipo_date_str = ipo_dates.get(t)
            ipo_dt = None
            if ipo_date_str:
                try:
                    ipo_dt = datetime.strptime(ipo_date_str, "%Y-%m-%d")
                except Exception:
                    pass

            if not t_intervals:
                for inv in intervals:
                    try:
                        inv_end_dt = datetime.strptime(inv["end"][:8], "%Y%m%d")
                    except Exception:
                        inv_end_dt = None

                    completed = False
                    if ipo_dt and inv_end_dt and ipo_dt > inv_end_dt:
                        completed = True
                        print(f"Ticker {t} IPO date ({ipo_date_str}) is after interval {inv['name']} ({inv['end']}). Marking completed.")

                    t_intervals.append({
                        "name": inv["name"],
                        "start": inv["start"],
                        "end": inv["end"],
                        "cursor": None,
                        "completed": completed
                    })

        # 2. Find next task (outer loop by interval index, inner loop by priority tickers)
        target_task = None
        for inv_idx in range(5):
            inv_name = f"Year_{inv_idx+1}"
            for t in priority_tickers:
                t_state = ticker_states.get(t)
                if not t_state:
                    continue
                for t_inv in t_state.get("intervals", []):
                    if t_inv["name"] == inv_name and not t_inv["completed"]:
                        target_task = (t, t_inv)
                        break
                if target_task:
                    break
            if target_task:
                break

        if not target_task:
            print("AlphaVantage backfill fully completed for all prioritised tickers!")
            return {"status": "completed", "message": "All tickers fully backfilled"}

        ticker, t_inv = target_task
        print(f"[{datetime.now()}] Selected task: Ticker={ticker}, Interval={t_inv['name']} ({t_inv['start']}..{t_inv['end']}), Cursor={t_inv['cursor']}")

        # 3. Call AlphaVantage API
        url = "https://www.alphavantage.co/query"
        time_from = t_inv["cursor"] if t_inv["cursor"] else t_inv["start"]

        # Adjust time_from to the IPO date if the interval starts before the IPO date
        ipo_date_str = ipo_dates.get(ticker)
        if ipo_date_str:
            try:
                ipo_ts = ipo_date_str.replace("-", "") + "T0000"
                if time_from < ipo_ts:
                    print(f"Adjusting time_from for {ticker} from {time_from} to IPO date {ipo_ts}")
                    time_from = ipo_ts
            except Exception:
                pass

        if time_from >= t_inv["end"]:
            print(f"Interval {t_inv['name']} for {ticker} starts after end date due to IPO filter ({time_from} >= {t_inv['end']}). Marking completed.")
            t_inv["completed"] = True
            t_inv["cursor"] = None
            state["last_processed"] = {
                "ticker": ticker,
                "interval": t_inv["name"],
                "timestamp": datetime.now().isoformat(),
                "feed_size": 0,
                "new_inserted": 0,
                "status": "skipped_ipo"
            }
            save_state(state)
            return {
                "status": "success",
                "ticker": ticker,
                "interval": t_inv["name"],
                "feed_size": 0,
                "new_inserted": 0,
                "message": "Skipped due to IPO date filter"
            }

        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": ticker,
            "sort": "EARLIEST",
            "limit": "1000",
            "time_from": time_from,
            "time_to": t_inv["end"],
            "apikey": ALPHA_VANTAGE_API_KEY
        }

        try:
            r = requests.get(url, params=params, timeout=30)
            state["daily_request_count"] += 1
            save_state(state)

            if r.status_code != 200:
                print(f"AlphaVantage API returned HTTP {r.status_code}")
                return {"status": "error", "message": f"HTTP status {r.status_code}"}

            data = r.json()
        except Exception as e:
            print(f"AlphaVantage request failed: {e}")
            return {"status": "error", "message": str(e)}

        # Check for rate limit / error messages in response
        if "Note" in data or "Information" in data:
            print(f"AlphaVantage API response info/note (rate limited): {data}")
            return {"status": "rate_limited", "message": "API note received"}
        if "Error Message" in data:
            print(f"AlphaVantage API returned error: {data['Error Message']}")
            return {"status": "error", "message": data["Error Message"]}

        feed = data.get("feed") or []
        print(f"Retrieved {len(feed)} feed items.")

        # 4. Process and insert feed items
        inserted_rows = 0
        rows_to_insert = []
        known_tickers = set(TICKER_UNIVERSE).union(priority_tickers)

        for item in feed:
            url_id = item.get("url")
            if not url_id:
                continue
            time_pub = item.get("time_published")
            if not time_pub or len(time_pub) < 8:
                continue

            date_str = f"{time_pub[:4]}-{time_pub[4:6]}-{time_pub[6:8]}"
            pub_utc = f"{time_pub[:4]}-{time_pub[4:6]}-{time_pub[6:8]}T{time_pub[9:11]}:{time_pub[11:13]}:{time_pub[13:15]}Z"
            title = item.get("title")

            for t_sentiment in item.get("ticker_sentiment", []):
                t_symbol = t_sentiment.get("ticker")
                if t_symbol in known_tickers:
                    try:
                        score = float(t_sentiment.get("ticker_sentiment_score") or 0.0)
                        rel = float(t_sentiment.get("relevance_score") or 0.0)
                        rows_to_insert.append({
                            "ticker": t_symbol,
                            "article_id": url_id,
                            "date": date_str,
                            "published_utc": pub_utc,
                            "title": (title or "")[:300],
                            "llm_score": score,
                            "llm_relevance": rel,
                            "model": "av-sentiment",
                            "source": "alphavantage"
                        })
                    except Exception:
                        pass

        if rows_to_insert:
            inserted_rows = _upsert_scores(db, rows_to_insert)
            print(f"Upserted {len(rows_to_insert)} records into DB (inserted {inserted_rows} new ones).")

        # 5. Advance Cursor / Complete Interval
        if len(feed) < 1000:
            print(f"Interval {t_inv['name']} completed for {ticker} (got {len(feed)} < 1000 items).")
            t_inv["completed"] = True
            t_inv["cursor"] = None
        else:
            last_time = feed[-1].get("time_published")
            next_cursor = add_one_minute(last_time)
            print(f"Interval has more data. Setting next cursor to: {next_cursor}")
            t_inv["cursor"] = next_cursor

        state["last_processed"] = {
            "ticker": ticker,
            "interval": t_inv["name"],
            "timestamp": datetime.now().isoformat(),
            "feed_size": len(feed),
            "new_inserted": inserted_rows,
            "status": "success"
        }
        save_state(state)
        return {
            "status": "success",
            "ticker": ticker,
            "interval": t_inv["name"],
            "feed_size": len(feed),
            "new_inserted": inserted_rows
        }
    finally:
        db.close()


if __name__ == "__main__":
    res = run_backfill_step()
    print("Step Result:", res)
