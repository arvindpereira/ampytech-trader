"""Read-only Robinhood live sync for the external accounts.

Uses the user's locally-checked-out, security-hardened ``robin_stocks`` fork (v4.0.0+) at
``config.ROBIN_STOCKS_PATH``. The fork is read-only by default: trading requires a root-owned
policy file we never create, so this module can only *observe* — it never mutates the account.

Design constraints (do not weaken):
  * One login exposes every account under it; we pull positions per ``account_number``.
  * The session is **memory-only** (``store_session=False``) — no keyring, no pickle, no disk.
  * Credentials are never persisted. We ``logout()`` in a ``finally``.
  * The per-lot tax basis (from the cost-basis CSV) is **preserved**. The API only exposes an
    average cost, so we reconcile *current share counts* against the live snapshot without
    clobbering the CSV's per-lot detail — backfilling/trimming only the delta.
  * Must run under Python >=3.10 (the hardened fork's floor). The repo's ``venv-py314`` satisfies this.
"""
import builtins
import sys
from datetime import datetime

from app.core import config
from app.database.models import EquityLot, ExternalAccount
from data_ingestion.import_external_csv import set_statement_anchor

# Share tolerance — below this we treat the local lots as already matching the live snapshot.
_EPS = 1e-4


def _import_robin_stocks():
    """Prepend the hardened fork's path and import it. Read-only needs no policy file."""
    path = config.ROBIN_STOCKS_PATH
    if path and path not in sys.path:
        sys.path.insert(0, path)
    try:
        import robin_stocks.robinhood as r  # noqa: WPS433 (local import is intentional)
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            f"Could not import the hardened robin_stocks fork from {path!r}. "
            "Set ROBIN_STOCKS_PATH and run the backend under venv-py314 (Python >=3.10)."
        ) from e
    return r


def _mfa_code(mfa_code=None, mfa_secret=None):
    """Resolve the 2FA code: a TOTP secret (preferred, auto-rotates) or a one-time code."""
    if mfa_secret and str(mfa_secret).strip():
        import pyotp
        return pyotp.TOTP(str(mfa_secret).strip().replace(" ", "")).now()
    return mfa_code


def _account_cash(acct_json):
    """Pull a spendable cash figure from an account profile, most-conservative first."""
    for key in ("portfolio_cash", "cash", "cash_available_for_withdrawal", "buying_power"):
        val = acct_json.get(key)
        try:
            if val not in (None, ""):
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def reconcile_positions_to_lots(db, account_label, positions, cash, statement_date,
                                source="robinhood-api"):
    """Reconcile a live Robinhood snapshot into ``EquityLot`` rows **without clobbering basis**.

    ``positions`` is an iterable of ``{ticker, shares, avg_cost}``. For each ticker we compare the
    live share count to the sum of existing local lots:
      * surplus (live > local) -> add ONE delta lot at the live average cost,
      * shortfall (live < local) -> trim existing lots FIFO (oldest acquisition_date first),
      * match -> leave the CSV lots (and their per-lot basis) untouched.
    Tickers held locally but absent from the live snapshot are trimmed to zero.

    Also records the raw snapshot to the statement anchor and updates the account's cash.
    Returns a summary dict.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().isoformat(timespec="seconds")

    rh = {}
    for p in positions:
        ticker = str(p.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        try:
            shares = float(p.get("shares") or 0.0)
        except (TypeError, ValueError):
            continue
        if shares <= 0:
            continue
        try:
            avg_cost = float(p.get("avg_cost")) if p.get("avg_cost") not in (None, "") else None
        except (TypeError, ValueError):
            avg_cost = None
        rh[ticker] = {"shares": shares, "avg_cost": avg_cost}

    # Persist the live snapshot as the statement anchor (its own sqlite connection commits + closes
    # before we touch the ORM lots, so the two writers don't contend for the file lock).
    set_statement_anchor(
        account_label,
        [{"ticker": t, "shares": v["shares"], "avg_cost": v["avg_cost"]} for t, v in rh.items()],
        statement_date,
        source=source,
    )

    existing = db.query(EquityLot).filter(EquityLot.account_label == account_label).all()
    by_ticker = {}
    for lot in existing:
        by_ticker.setdefault(lot.ticker.upper(), []).append(lot)

    backfilled = trimmed = zeroed = unchanged = 0
    for ticker in set(rh) | set(by_ticker):
        lots = sorted(by_ticker.get(ticker, []), key=lambda l: (l.acquisition_date or "", l.id))
        local_shares = sum(l.shares for l in lots)
        live = rh.get(ticker)
        live_shares = live["shares"] if live else 0.0
        diff = round(live_shares - local_shares, 6)

        if diff > _EPS:
            # Backfill one delta lot. Prefer the live average cost; fall back to the existing
            # weighted basis, then to 0 (user can correct it in the lot editor).
            cost = live["avg_cost"] if live and live["avg_cost"] and live["avg_cost"] > 0 else None
            if cost is None and local_shares > 0:
                cost = sum(l.shares * l.cost_basis_per_share for l in lots) / local_shares
            db.add(EquityLot(
                ticker=ticker, account_label=account_label, lot_type="other",
                shares=diff, cost_basis_per_share=float(cost or 0.0), acquisition_date=today,
                notes="Robinhood API backfill", created_at=now_str,
            ))
            backfilled += 1
        elif diff < -_EPS:
            to_trim = -diff
            for lot in lots:  # FIFO: oldest first
                if to_trim <= _EPS:
                    break
                if lot.shares <= to_trim + 1e-9:
                    to_trim -= lot.shares
                    db.delete(lot)
                else:
                    lot.shares = round(lot.shares - to_trim, 6)
                    to_trim = 0.0
            if live:
                trimmed += 1
            else:
                zeroed += 1
        else:
            unchanged += 1

    acct = db.query(ExternalAccount).filter(ExternalAccount.account_label == account_label).first()
    if acct is not None and cash is not None:
        acct.cash = round(float(cash), 2)
        acct.updated_at = now_str

    db.commit()
    return {
        "account_label": account_label,
        "positions": len(rh),
        "backfilled": backfilled,
        "trimmed": trimmed,
        "zeroed": zeroed,
        "unchanged": unchanged,
        "cash": round(float(cash), 2) if cash is not None else None,
    }


def sync_robinhood(db, username, password, mfa_code=None, mfa_secret=None):
    """Log in (memory-only), discover every account, and reconcile each one's live holdings.

    Returns ``{"status": "success", "accounts": [<per-account summary>...]}`` or
    ``{"status": "mfa_required", ...}`` when 2FA is needed. Strictly read-only; logs out afterward.
    """
    r = _import_robin_stocks()
    from robin_stocks.robinhood import urls as rh_urls, helper as rh_helper, account as rh_account

    mfa = _mfa_code(mfa_code, mfa_secret)

    # Block stdin so a missing-MFA prompt can't hang uvicorn waiting on a terminal that isn't there.
    original_input = builtins.input

    def _blocked_input(prompt=""):
        raise ValueError("MFA code required but stdin is blocked")

    builtins.input = _blocked_input
    try:
        try:
            r.login(username=username.strip(), password=password, mfa_code=mfa, store_session=False)
        except Exception as e:  # noqa: BLE001 - classify the failure for the UI
            msg = str(e).lower()
            if any(k in msg for k in ("challenge", "mfa", "passcode", "input", "two-factor")):
                return {"status": "mfa_required",
                        "message": "Two-factor code required. Provide mfa_code or mfa_secret."}
            raise RuntimeError(f"Robinhood login failed: {e}") from e
    finally:
        builtins.input = original_input

    statement_date = datetime.now().strftime("%Y-%m-%d")
    instrument_cache = {}

    def _symbol(instrument_url):
        if not instrument_url:
            return None
        if instrument_url not in instrument_cache:
            try:
                sym = r.get_symbol_by_url(instrument_url)
                instrument_cache[instrument_url] = sym.upper() if sym else None
            except Exception:  # noqa: BLE001
                instrument_cache[instrument_url] = None
        return instrument_cache[instrument_url]

    try:
        # Discover all accounts under this login (joint + individual share one credential set).
        accounts = rh_helper.request_get(rh_urls.account_profile_url(), "results") or []
        if isinstance(accounts, dict):  # single-account responses come back unwrapped
            accounts = [accounts]

        existing_labels = [a.account_label for a in db.query(ExternalAccount).all()]
        now_str = datetime.now().isoformat(timespec="seconds")
        results = []

        for acct_json in accounts:
            account_number = str(acct_json.get("account_number") or "").strip()
            if not account_number:
                continue

            # Match an existing label that contains the account number (e.g. the CSV-imported
            # "Robinhood Joint (116424851826)"); create a generic one only if none exists.
            label = next((lbl for lbl in existing_labels if account_number in lbl), None)
            if label is None:
                label = f"Robinhood ({account_number})"
                db.add(ExternalAccount(account_label=label, cash=0.0, risk_profile="balanced",
                                       created_at=now_str, updated_at=now_str))
                db.commit()
                existing_labels.append(label)

            raw_positions = rh_account.get_open_stock_positions(account_number=account_number) or []
            positions = []
            for pos in raw_positions:
                try:
                    qty = float(pos.get("quantity") or 0.0)
                except (TypeError, ValueError):
                    qty = 0.0
                if qty <= 0:
                    continue
                ticker = _symbol(pos.get("instrument"))
                if not ticker:
                    continue
                try:
                    avg_cost = float(pos.get("average_buy_price") or 0.0)
                except (TypeError, ValueError):
                    avg_cost = None
                positions.append({"ticker": ticker, "shares": qty, "avg_cost": avg_cost})

            cash = _account_cash(acct_json)
            summary = reconcile_positions_to_lots(db, label, positions, cash, statement_date)
            results.append(summary)

        return {"status": "success", "accounts": results}
    finally:
        try:
            r.logout()
        except Exception:  # noqa: BLE001
            pass
