"""Read-only 'why' for the trader: replays the exact gates run_execution() applies,
without placing any orders. Powers the dashboard Execution-plan panel and the
per-run decision snapshot persisted by run_execution().

Keeping the gate sequence here identical to executor.execute_swing_paper_trades is
deliberate — this is the single place that explains *why* a name will or won't trade.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


# Verdict codes → human label shown in the UI.
VERDICT_LABELS = {
    "buy": "Would buy",
    "already_held": "Already held",
    "not_assigned": "Not in this sleeve",
    "blocked": "Trade-blocked",
    "locked": "Position locked",
    "no_brackets": "Missing stop/target",
    "budget_exhausted": "Sleeve budget exhausted",
    "position_too_small": "Position too small (<$100)",
}


def _replay_sleeve(buys, allowed, budget, held, db, equity, position_pct, vol_map, sleeve, vol_target):
    """Replay the swing/high-risk entry gates in conviction order, decrementing the
    sleeve budget exactly as the executor does, and tag each candidate with a verdict."""
    from execution.executor import buy_block_reason
    from app.database import VirtualPosition

    locked = {p.ticker for p in db.query(VirtualPosition).filter(VirtualPosition.policy == "lock").all()}
    remaining = budget
    out = []
    for sug in buys:
        tk = sug["ticker"]
        price = sug.get("close") or 0.0
        cand = {"ticker": tk, "sleeve": sleeve, "confidence": sug.get("confidence"),
                "shares_est": None, "value_est": None}
        if allowed is not None and tk not in allowed:
            cand["verdict"] = "not_assigned"
        elif buy_block_reason(tk, db):
            cand["verdict"] = "blocked"; cand["detail"] = buy_block_reason(tk, db)
        elif tk in locked:
            cand["verdict"] = "locked"
        elif tk in held:
            cand["verdict"] = "already_held"
        elif not sug.get("stop_loss") or not sug.get("take_profit") or price <= 0:
            cand["verdict"] = "no_brackets"
        elif remaining < 100.0:
            cand["verdict"] = "budget_exhausted"
        else:
            vol_scale = 1.0
            if vol_target > 0 and vol_map.get(tk):
                vol_scale = min(1.0, vol_target / vol_map[tk])
            trade_value = min(equity * position_pct * vol_scale, remaining)
            shares = int(trade_value / price) if price else 0
            if shares < 1 or trade_value < 100.0:
                cand["verdict"] = "position_too_small"
            else:
                cand["verdict"] = "buy"
                cand["shares_est"] = shares
                cand["value_est"] = round(shares * price, 2)
                remaining -= shares * price
        out.append(cand)
    return out, remaining


def build_execution_plan(db, api=None, suggestions_data=None) -> dict:
    """Compute the full execution plan (status, sleeves, candidate verdicts) read-only."""
    from app.main import (get_daily_suggestions, get_strategy_buckets, get_strategy_assignments)
    from execution.executor import get_alpaca_api, auto_trading_paused
    from app.core.config import (SWING_POSITION_PCT, SWING_VOL_TARGET, HIGH_RISK_CAP,
                                 REGIME_SWING_FACTORS, REGIME_OVERLAY_ENABLED, HEDGE_MODE)

    now = datetime.now()
    plan: dict = {"as_of": now.isoformat(), "paused": auto_trading_paused(db)}

    if api is None:
        api = get_alpaca_api()

    # Market clock
    plan["market_open"], plan["market_detail"], plan["next_open"] = None, "unknown", None
    if api:
        try:
            clock = api.get_clock()
            plan["market_open"] = bool(clock.is_open)
            plan["market_detail"] = "open" if clock.is_open else "closed"
            plan["next_open"] = str(clock.next_open) if not clock.is_open else None
        except Exception as e:
            plan["market_detail"] = f"clock unavailable: {e}"

    if suggestions_data is None:
        suggestions_data = get_daily_suggestions(date=None, hedge_mode=HEDGE_MODE, db=db)

    regime = suggestions_data.get("regime", "growth")
    swing_factor = REGIME_SWING_FACTORS.get(regime, 1.0) if REGIME_OVERLAY_ENABLED else 1.0
    plan["regime"] = regime
    plan["regime_overlay_enabled"] = REGIME_OVERLAY_ENABLED
    plan["swing_factor"] = swing_factor

    buckets = get_strategy_buckets(db)
    assignments = get_strategy_assignments(db)
    swing_set = {t for t, s in assignments.items() if s == "swing"}
    longterm_set = {t for t, s in assignments.items() if s == "longterm"}
    try:
        from ml_engine.swing_alpha import tickers_for_tiers, HIGH_RISK_TIERS
        spec_set = set(tickers_for_tiers(HIGH_RISK_TIERS))
    except Exception:
        spec_set = set()
    core_swing_set = swing_set - spec_set

    equity, buying_power, held = 0.0, 0.0, set()
    deployed = {"swing": 0.0, "longterm": 0.0, "hold": 0.0, "short_term": 0.0}
    deployed_spec = 0.0
    if api:
        try:
            acct = api.get_account()
            equity = float(acct.equity); buying_power = float(acct.buying_power)
            for p in api.list_positions():
                held.add(p.symbol)
                strat = assignments.get(p.symbol, "swing")
                deployed[strat] = deployed.get(strat, 0.0) + float(p.market_value)
                if p.symbol in spec_set:
                    deployed_spec += float(p.market_value)
        except Exception as e:
            plan["account_error"] = str(e)
    plan["equity"] = round(equity, 2)
    plan["buying_power"] = round(buying_power, 2)

    # Per-name volatility caps (mirrors the executor sizing).
    vol_map = {}
    if SWING_VOL_TARGET > 0:
        try:
            from app.database import TickerClassification
            vol_map = {c.ticker: c.volatility for c in db.query(TickerClassification).all() if c.volatility}
        except Exception:
            vol_map = {}

    # Sleeve budgets (same formulas as run_execution).
    swing_w = buckets.get("swing", 0.0)
    swing_w_eff = swing_w * swing_factor
    swing_cap = swing_w_eff * equity
    swing_budget = max(0.0, swing_cap - deployed.get("swing", 0.0))

    hr_w = min(HIGH_RISK_CAP, buckets.get("high_risk", 0.0))
    hr_cap = hr_w * equity
    hr_budget = max(0.0, hr_cap - deployed_spec)

    lt_w = buckets.get("longterm", 0.0)
    lt_cap = lt_w * equity
    lt_budget = max(0.0, lt_cap - deployed.get("longterm", 0.0))

    # Replay swing + high-risk entry gates.
    candidates = []
    if not plan["paused"]:
        swing_buys = [s for s in suggestions_data.get("swing_suggestions", []) if s.get("action") == "BUY"]
        sw_cands, _ = _replay_sleeve(swing_buys, core_swing_set, swing_budget, held, db,
                                     equity, SWING_POSITION_PCT, vol_map, "swing", SWING_VOL_TARGET)
        candidates += sw_cands
        if hr_w > 0 and spec_set:
            hr_buys = [s for s in suggestions_data.get("high_risk_suggestions", []) if s.get("action") == "BUY"]
            hr_cands, _ = _replay_sleeve(hr_buys, spec_set, hr_budget, held, db,
                                         equity, SWING_POSITION_PCT, vol_map, "high_risk", SWING_VOL_TARGET)
            candidates += hr_cands

    # Long-term: surface the grid's actionable targets (it buys on dips, so many are "wait").
    lt_actions = []
    for a in suggestions_data.get("long_term_allocation", []):
        act = (a.get("suggested_action") or "")
        if act.startswith("BUY"):
            lt_actions.append({"ticker": a["ticker"], "sleeve": "longterm",
                               "weight": a.get("weight"), "detail": act})

    plan["sleeves"] = [
        {"key": "swing", "label": "Swing", "weight": swing_w, "effective_weight": swing_w_eff,
         "cap": round(swing_cap, 2), "deployed": round(deployed.get("swing", 0.0), 2),
         "available": round(swing_budget, 2), "assigned": len(core_swing_set)},
        {"key": "high_risk", "label": "High-risk", "weight": hr_w, "effective_weight": hr_w,
         "cap": round(hr_cap, 2), "deployed": round(deployed_spec, 2),
         "available": round(hr_budget, 2), "assigned": len(spec_set)},
        {"key": "longterm", "label": "Long-term", "weight": lt_w, "effective_weight": lt_w,
         "cap": round(lt_cap, 2), "deployed": round(deployed.get("longterm", 0.0), 2),
         "available": round(lt_budget, 2), "assigned": len(longterm_set)},
    ]
    candidates.sort(key=lambda c: (c["verdict"] != "buy", -(c.get("confidence") or 0)))
    plan["candidates"] = candidates
    plan["longterm_actions"] = lt_actions
    plan["summary"] = {
        "swing_buy_signals": sum(1 for s in suggestions_data.get("swing_suggestions", []) if s.get("action") == "BUY"),
        "high_risk_buy_signals": sum(1 for s in suggestions_data.get("high_risk_suggestions", []) if s.get("action") == "BUY"),
        "would_execute": sum(1 for c in candidates if c["verdict"] == "buy"),
        "longterm_buys": len(lt_actions),
    }
    return plan
