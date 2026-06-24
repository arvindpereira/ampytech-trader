"""Alpaca account registry — the single source of truth for which brokerage accounts exist.

The bot supports two Alpaca accounts: a PAPER account (the existing ALPACA_* creds) and a LIVE
real-money account (ALPACA_LIVE_* creds). Credentials live in env vars only (never the DB, which is
backed up off-box). Each account's `key` ("paper"/"live") doubles as the `mode` string stored on
VirtualOrder/VirtualPosition rows, so no new columns are needed to partition local bookkeeping.

`default_gate` is the approval-gate default when no DB override exists: live is gated ON (real money
needs per-trade human approval), paper is OFF (auto-execute).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.core.config import (
    ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL,
    ALPACA_LIVE_API_KEY, ALPACA_LIVE_SECRET_KEY, ALPACA_LIVE_BASE_URL,
)


@dataclass(frozen=True)
class AccountDef:
    key: str            # "paper" | "live" — also the VirtualOrder/VirtualPosition `mode` value
    label: str          # human label for the UI
    api_key: str
    secret_key: str
    base_url: str
    is_live: bool
    default_gate: bool   # approval-gate default when no DB row exists


ACCOUNTS: Dict[str, AccountDef] = {
    "paper": AccountDef(
        key="paper", label="Alpaca Paper",
        api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY,
        base_url=ALPACA_BASE_URL or "https://paper-api.alpaca.markets",
        is_live=False, default_gate=False,
    ),
    "live": AccountDef(
        key="live", label="Alpaca Live",
        api_key=ALPACA_LIVE_API_KEY, secret_key=ALPACA_LIVE_SECRET_KEY,
        base_url=ALPACA_LIVE_BASE_URL or "https://api.alpaca.markets",
        is_live=True, default_gate=True,
    ),
}


def get_account(key: str) -> Optional[AccountDef]:
    return ACCOUNTS.get(key)


def is_configured(acc: Optional[AccountDef]) -> bool:
    """True when the account has both API credentials present."""
    return bool(acc and acc.api_key and acc.secret_key)


def enabled_account_keys() -> List[str]:
    """Accounts the executor should run. Configured accounts are enabled; the paper account is also
    enabled when creds are absent because it falls back to the in-process virtual broker mock. A LIVE
    account with no creds is NOT enabled — it must never be silently routed to the mock or to paper."""
    out: List[str] = []
    for key, acc in ACCOUNTS.items():
        if is_configured(acc) or key == "paper":
            out.append(key)
    return out
