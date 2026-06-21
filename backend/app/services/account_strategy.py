"""Pure target construction and order diffing for external accounts."""

import json
import math
import re
from app.core.config import HIGH_RISK_CAP


STRATEGY_MODES = ("growth", "glide_path", "de_risk", "all_weather", "barbell")
STRATEGY_KEYS = ("swing", "longterm", "high_risk")
DEFAULT_BUCKETS = {"swing": 1.0, "longterm": 0.0, "high_risk": 0.0}
ALL_WEATHER = {"SPY": 0.30, "TLT": 0.40, "IEF": 0.15, "GLD": 0.075, "GSG": 0.075}
BARBELL = {"BIL": 0.90, "QQQ": 0.10}
MIN_ORDER_VALUE = 50.0
MAX_MODEL_POSITION_WEIGHT = 0.20
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class StrategyValidationError(ValueError):
    pass


def canonical_ticker(value):
    """Canonical symbol so holdings/signals/templates align. Class shares use a dot in our DB
    (BRK.B); a dash form (BRK-B, as Yahoo uses) is normalized to it."""
    return str(value or "").upper().strip().replace("-", ".")


def validate_buckets(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise StrategyValidationError("buckets must be an object or null")
    unknown = set(value) - set(STRATEGY_KEYS)
    if unknown:
        raise StrategyValidationError(f"Unknown bucket keys: {', '.join(sorted(unknown))}")
    result = {}
    for key in STRATEGY_KEYS:
        raw = value.get(key, 0.0)
        if isinstance(raw, bool):
            raise StrategyValidationError(f"{key} must be a finite number")
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise StrategyValidationError(f"{key} must be a finite number")
        if not math.isfinite(number) or number < 0.0 or number > 1.0:
            raise StrategyValidationError(f"{key} must be between 0 and 1")
        result[key] = number
    if result["high_risk"] > HIGH_RISK_CAP + 1e-12:
        raise StrategyValidationError(f"high_risk cannot exceed {HIGH_RISK_CAP:.4f}")
    if sum(result.values()) > 1.0 + 1e-9:
        raise StrategyValidationError("Bucket weights cannot exceed 1")
    return result


def effective_buckets(account, global_buckets):
    if account.buckets_json:
        try:
            return validate_buckets(json.loads(account.buckets_json))
        except (json.JSONDecodeError, StrategyValidationError):
            pass
    try:
        return validate_buckets(global_buckets) or dict(DEFAULT_BUCKETS)
    except StrategyValidationError:
        return dict(DEFAULT_BUCKETS)


def _score(item, *keys):
    for key in keys:
        try:
            value = float(item.get(key))
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return 1.0


def extract_model_signals(snapshot):
    """Normalize a cached daily-suggestions response without invoking model side effects."""
    candidates = {key: {} for key in STRATEGY_KEYS}
    sells = set()
    if not snapshot:
        return candidates, sells

    for bucket, response_key in (("swing", "swing_suggestions"),
                                 ("high_risk", "high_risk_suggestions")):
        for item in snapshot.get(response_key) or []:
            ticker = canonical_ticker(item.get("ticker"))
            verdict = str(item.get("verdict", item.get("action", ""))).upper()
            if not _TICKER_RE.fullmatch(ticker):
                continue
            if verdict == "BUY":
                candidates[bucket][ticker] = _score(item, "probability", "confidence", "score")
            elif verdict == "SELL":
                sells.add(ticker)

    for item in snapshot.get("long_term_allocation") or []:
        ticker = str(item.get("ticker", "")).upper().strip()
        if ticker == "CASH" or not _TICKER_RE.fullmatch(ticker):
            continue
        action = str(item.get("suggested_action", "")).upper()
        if action.startswith("SELL"):
            sells.add(ticker)
        else:
            weight = _score(item, "weight")
            if weight > 0:
                candidates["longterm"][ticker] = weight
    return candidates, sells


def _allocate(scores, budget):
    if budget <= 0 or not scores:
        return {}
    total = sum(max(0.0, float(score)) for score in scores.values())
    if total <= 0:
        return {}
    return {ticker: min(MAX_MODEL_POSITION_WEIGHT,
                        budget * max(0.0, float(score)) / total)
            for ticker, score in scores.items()}


def build_growth_target(current_weights, buckets, snapshot):
    """Preserve unsignalled holdings; deploy remaining capacity only into model candidates."""
    candidates, explicit_sells = extract_model_signals(snapshot)
    preserved = {ticker: weight for ticker, weight in current_weights.items()
                 if ticker not in explicit_sells and weight > 0}
    available = max(0.0, 1.0 - sum(preserved.values()))
    desired = {}
    for bucket in STRATEGY_KEYS:
        for ticker, weight in _allocate(candidates[bucket], buckets[bucket]).items():
            desired[ticker] = desired.get(ticker, 0.0) + weight
    weights = dict(preserved)
    increments = {ticker: max(0.0, desired_weight - weights.get(ticker, 0.0))
                  for ticker, desired_weight in desired.items() if ticker not in explicit_sells}
    increment_total = sum(increments.values())
    scale = min(1.0, available / increment_total) if increment_total > 0 else 0.0
    for ticker, increment in increments.items():
        weights[ticker] = weights.get(ticker, 0.0) + increment * scale
    cash = max(0.0, 1.0 - sum(weights.values()))
    return weights, cash, explicit_sells


# Per-name "defensiveness": how much of a held name to keep when de-risking. Quality/low-volatility
# names (BRK.B, core) are kept; speculative/high-volatility names (BYND) are shed toward cash.
_TIER_DEFENSIVENESS = {"core": 1.0, "quality_growth": 0.85, "value_trap": 0.30, "speculative": 0.12}


def _defensiveness(ticker, classifications):
    info = (classifications or {}).get(ticker) or {}
    base = _TIER_DEFENSIVENESS.get(info.get("tier"), 0.5)
    vol = info.get("volatility")
    if vol is not None:
        try:
            v = float(vol)
            if math.isfinite(v):
                base *= max(0.25, min(1.15, 1.0 - (v - 0.25) * 0.6))   # low vol kept, high vol shed
        except (TypeError, ValueError):
            pass
    return max(0.0, min(1.0, base))


def holdings_defensive_target(current_weights, classifications, de_risk_coefficient):
    """Holdings-aware de-risk: tilt toward the account's own low-vol / high-quality holdings, trim
    speculative ones, and route the de-risked remainder to cash. `de_risk_coefficient` (0..1, from
    the crash radar) sets how much of the book moves to cash."""
    try:
        d = float(de_risk_coefficient)
    except (TypeError, ValueError):
        raise StrategyValidationError("The crash-risk coefficient is unavailable")
    if not math.isfinite(d) or not 0.0 <= d <= 1.0:
        raise StrategyValidationError("The crash-risk coefficient is invalid")
    raw = {t: w * _defensiveness(t, classifications)
           for t, w in current_weights.items() if w > 0}
    total_raw = sum(raw.values())
    if total_raw <= 0:                       # nothing to keep → all cash
        return {}, 1.0
    equity_share = 1.0 - d                    # d of the book becomes cash; the rest stays in quality names
    target = {t: equity_share * (rw / total_raw) for t, rw in raw.items()}
    cash = max(0.0, 1.0 - sum(target.values()))
    return target, cash


def defensive_endpoint(mode, current_weights=None, classifications=None, de_risk_coefficient=None):
    if mode == "growth":
        return {}, 1.0
    if mode == "all_weather":                 # explicit basket rotation (Dalio-inspired ETF mix)
        return dict(ALL_WEATHER), 0.0
    if mode == "barbell":                      # explicit basket rotation (T-bill-heavy + small growth)
        return dict(BARBELL), 0.0
    if mode in ("glide_path", "de_risk"):      # holdings-aware de-risk (keep quality, raise cash)
        return holdings_defensive_target(current_weights or {}, classifications, de_risk_coefficient)
    raise StrategyValidationError(f"Unknown strategy mode: {mode}")


def build_account_target(current_weights, mode, aggression, buckets, snapshot=None,
                         classifications=None, de_risk_coefficient=None):
    if mode not in STRATEGY_MODES:
        raise StrategyValidationError("Invalid strategy mode")
    if isinstance(aggression, bool) or not isinstance(aggression, int) or not 0 <= aggression <= 100:
        raise StrategyValidationError("aggression must be an integer from 0 through 100")
    buckets = validate_buckets(buckets)
    a = aggression / 100.0
    # No fresh speculative deployment in defensive postures: scale the high_risk bucket by aggression
    # so a de-risking account doesn't open new speculative positions (BYND). Existing speculative
    # holdings are preserved by growth but trimmed by the defensive endpoint.
    growth_buckets = dict(buckets)
    growth_buckets["high_risk"] = buckets["high_risk"] * a
    growth, growth_cash, explicit_sells = build_growth_target(current_weights, growth_buckets, snapshot)
    defensive, defensive_cash = defensive_endpoint(mode, current_weights, classifications,
                                                   de_risk_coefficient)
    tickers = set(growth) | set(defensive)
    target = {ticker: a * growth.get(ticker, 0.0) + (1.0 - a) * defensive.get(ticker, 0.0)
              for ticker in tickers}
    target = {ticker: weight for ticker, weight in target.items() if weight > 1e-12}
    cash = a * growth_cash + (1.0 - a) * defensive_cash
    total = sum(target.values()) + cash
    if not math.isfinite(total) or total <= 0:
        raise StrategyValidationError("Target allocation is empty or invalid")
    target = {ticker: weight / total for ticker, weight in target.items()}
    cash /= total
    reasons = {}
    for ticker in target:
        in_growth, in_defensive = ticker in growth, ticker in defensive
        held = current_weights.get(ticker, 0.0) > 0
        tier = (classifications or {}).get(ticker, {}).get("tier")
        if in_defensive and held and tier in ("core", "quality_growth"):
            reasons[ticker] = "keep_quality"
        elif in_growth and in_defensive:
            reasons[ticker] = "blended_growth_defensive"
        elif in_growth:
            reasons[ticker] = "model_buy" if not held else "shared_model_growth"
        else:
            reasons[ticker] = "defensive_template"
    return {"target_weights": target, "cash_target_weight": cash,
            "target_reason_codes": reasons, "explicit_sells": explicit_sells}


def generate_trade_proposals(target_weights, cash_target_weight, portfolio_value, cash,
                             quantities, prices, fallback_tickers=None):
    fallback_tickers = set(fallback_tickers or [])
    current_values = {ticker: quantities.get(ticker, 0.0) * prices[ticker]
                      for ticker in quantities if ticker in prices}
    all_tickers = set(current_values) | set(target_weights)
    sells, buys, warnings = [], [], []
    if fallback_tickers:
        warnings.append("Fallback prices used for: " + ", ".join(sorted(fallback_tickers)))

    def proposal(ticker, side, value, qty):
        safe_qty = math.floor(qty * 10000.0) / 10000.0
        return {"ticker": ticker, "side": side, "qty": safe_qty,
                "limit_price": round(prices[ticker], 2), "time_in_force": "GTC_90",
                "reason_code": "account_strategy_target",
                "reason": f"Account strategy target {target_weights.get(ticker, 0.0) * 100:.1f}%"}

    for ticker in sorted(all_tickers):
        if ticker in fallback_tickers:
            warnings.append(f"No market price for {ticker}; no order generated")
            continue
        price = prices.get(ticker, 0.0)
        if price <= 0 or not math.isfinite(price):
            warnings.append(f"No usable price for {ticker}; no order generated")
            continue
        diff = portfolio_value * target_weights.get(ticker, 0.0) - current_values.get(ticker, 0.0)
        if diff < -MIN_ORDER_VALUE:
            qty = min(quantities.get(ticker, 0.0), abs(diff) / price)
            if qty > 0:
                sells.append(proposal(ticker, "SELL", abs(diff), qty))
        elif diff > MIN_ORDER_VALUE:
            buys.append((ticker, diff))

    sell_proceeds = sum(item["qty"] * item["limit_price"] for item in sells)
    reserve = portfolio_value * cash_target_weight
    available = max(0.0, cash + sell_proceeds - reserve)
    suggestions = list(sells)
    for ticker, requested in buys:
        value = min(requested, available)
        if value <= MIN_ORDER_VALUE:
            continue
        item = proposal(ticker, "BUY", value, value / prices[ticker])
        actual_value = item["qty"] * item["limit_price"]
        if actual_value <= MIN_ORDER_VALUE:
            continue
        suggestions.append(item)
        available -= actual_value
    if sells and any(item["side"] == "BUY" for item in suggestions):
        warnings.append("Execute proposed sells before buys so sale proceeds are available")
    if sells:
        warnings.append("Sell proposals use FIFO holdings and are not tax optimized")
    turnover = sum(item["qty"] * item["limit_price"] for item in suggestions)
    return suggestions, (turnover / portfolio_value if portfolio_value > 0 else 0.0), warnings
