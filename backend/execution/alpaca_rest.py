from datetime import datetime, time as dt_time, timedelta
from types import SimpleNamespace
from urllib.parse import urljoin

import requests

from app.core.config import ALPACA_DATA_FEED, ALPACA_DATA_URL
from app.database import RecentPrice, SessionLocal, VirtualOrder


def _obj(data):
    if isinstance(data, dict):
        return SimpleNamespace(**{k: _obj(v) for k, v in data.items()})
    if isinstance(data, list):
        return [_obj(v) for v in data]
    return data


class AlpacaRestClient:
    """Small compatibility wrapper for the subset of alpaca-trade-api used by this repo."""

    def __init__(self, key_id, secret_key, base_url, account_key="paper"):
        self.key_id = key_id
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.account_key = account_key
        self.is_virtual = "localhost" in self.base_url or "127.0.0.1" in self.base_url

    def _headers(self):
        if self.is_virtual:
            return {}
        return {
            "APCA-API-KEY-ID": self.key_id,
            "APCA-API-SECRET-KEY": self.secret_key,
        }

    def _url(self, path):
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(self, method, path, **kwargs):
        response = requests.request(
            method,
            self._url(path),
            headers=self._headers(),
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get_account(self):
        return _obj(self._request("GET", "/v2/account"))

    def get_clock(self):
        if not self.is_virtual:
            return _obj(self._request("GET", "/v2/clock"))
        now = datetime.now()
        market_open = now.weekday() < 5 and dt_time(6, 30) <= now.time() <= dt_time(13, 0)
        return SimpleNamespace(
            is_open=market_open,
            next_open=now + timedelta(days=1),
            next_close=now.replace(hour=13, minute=0, second=0, microsecond=0),
        )

    def list_positions(self):
        return _obj(self._request("GET", "/v2/positions"))

    def get_position(self, symbol):
        return _obj(self._request("GET", f"/v2/positions/{symbol}"))

    def close_position(self, symbol, qty=None):
        params = {"qty": qty} if qty is not None else None
        return _obj(self._request("DELETE", f"/v2/positions/{symbol}", params=params))

    def submit_order(self, **kwargs):
        return _obj(self._request("POST", "/v2/orders", json=kwargs))

    def list_orders(self, status="open", limit=500, side=None, symbols=None, after=None):
        if self.is_virtual:
            return self._list_virtual_orders(status=status, limit=limit, side=side, symbols=symbols, after=after)
        params = {"status": status, "limit": limit}
        if side:
            params["side"] = side
        if symbols:
            params["symbols"] = ",".join(symbols) if isinstance(symbols, (list, tuple, set)) else symbols
        if after:
            params["after"] = after
        return _obj(self._request("GET", "/v2/orders", params=params))

    def get_order(self, order_id):
        if self.is_virtual:
            db = SessionLocal()
            try:
                row = db.query(VirtualOrder).filter(VirtualOrder.id == order_id).first()
                if not row:
                    raise RuntimeError(f"Virtual order {order_id} not found")
                return self._virtual_order_obj(row)
            finally:
                db.close()
        return _obj(self._request("GET", f"/v2/orders/{order_id}"))

    def cancel_order(self, order_id):
        if self.is_virtual:
            db = SessionLocal()
            try:
                row = db.query(VirtualOrder).filter(VirtualOrder.id == order_id).first()
                if row:
                    row.status = "canceled"
                    db.commit()
                return None
            finally:
                db.close()
        return self._request("DELETE", f"/v2/orders/{order_id}")

    def get_latest_trade(self, symbol):
        trades = self.get_latest_trades([symbol])
        return trades[symbol]

    def get_latest_trades(self, symbols):
        if self.is_virtual:
            db = SessionLocal()
            try:
                out = {}
                for symbol in symbols:
                    row = (
                        db.query(RecentPrice)
                        .filter(RecentPrice.ticker == symbol)
                        .order_by(RecentPrice.date.desc())
                        .first()
                    )
                    if row:
                        out[symbol] = SimpleNamespace(price=float(row.close), symbol=symbol)
                return out
            finally:
                db.close()

        headers = self._headers()
        params = {"symbols": ",".join(symbols), "feed": ALPACA_DATA_FEED}
        response = requests.get(
            f"{ALPACA_DATA_URL.rstrip('/')}/v2/stocks/trades/latest",
            headers=headers,
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json().get("trades", {})
        return {symbol: SimpleNamespace(price=trade.get("p"), symbol=symbol) for symbol, trade in data.items()}

    def _list_virtual_orders(self, status="open", limit=500, side=None, symbols=None, after=None):
        db = SessionLocal()
        try:
            q = db.query(VirtualOrder).filter(VirtualOrder.mode == self.account_key)
            if status == "open":
                q = q.filter(VirtualOrder.status.in_(["pending", "submitted", "accepted", "partially_filled"]))
            elif status != "all":
                q = q.filter(VirtualOrder.status == status)
            if side:
                q = q.filter(VirtualOrder.side == side)
            if symbols:
                q = q.filter(VirtualOrder.ticker.in_(symbols))
            if after:
                q = q.filter(VirtualOrder.created_at >= after)
            rows = q.order_by(VirtualOrder.created_at.desc()).limit(limit).all()
            return [self._virtual_order_obj(row) for row in rows]
        finally:
            db.close()

    @staticmethod
    def _virtual_order_obj(row):
        return SimpleNamespace(
            id=row.id,
            symbol=row.ticker,
            qty=str(row.qty),
            side=row.side,
            type=row.type,
            status=row.status,
            filled_qty=str(row.qty if row.status == "filled" else 0),
            filled_avg_price=str(row.filled_price or ""),
            created_at=row.created_at,
            filled_at=row.filled_at,
        )
