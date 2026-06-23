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
    # Long-term MPT grid verdicts
    "would_open": "Would open (new)",
    "would_add_dip": "Would add (3%+ dip)",
    "wait_for_dip": "Waiting for a dip",
    "would_trim": "Would trim (overweight)",
    "at_target": "At target weight",
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


def _replay_longterm(allocations, longterm_set, positions, equity, budget_fraction):
    """Replay the MPT grid's real entry rule: open a new position, add a tranche only on a
    dip below cost, otherwise wait. Mirrors executor.execute_long_term_grid_trades. Also surfaces
    the concrete buy (dip-below-cost) and take-profit (gain-above-cost) target prices."""
    from app.core.config import GRID_BUY_DIP, GRID_TP_GAIN
    weights = {a["ticker"]: a.get("weight", 0.0) for a in allocations}
    price_hint = {a["ticker"]: a.get("current_price") for a in allocations}
    out = []
    for tk in sorted(longterm_set):
        if tk not in weights:
            continue
        pos = positions.get(tk)
        current = pos["qty"] if pos else 0.0
        price = (pos["price"] if pos else None) or price_hint.get(tk)
        if not price or price <= 0:
            continue
        entry = pos["avg_entry"] if pos else 0.0
        target = (equity * weights[tk] * budget_fraction) / price
        diff = target - current
        dev = ((price - entry) / entry) if entry > 0 else 0.0
        cand = {"ticker": tk, "sleeve": "longterm", "weight": weights[tk],
                "price_dev": round(dev * 100, 1),
                # Concrete grid targets relative to cost basis (new names buy at market).
                "buy_target": round(entry * (1 - GRID_BUY_DIP), 2) if entry > 0 else round(price, 2),
                "take_profit": round(entry * (1 + GRID_TP_GAIN), 2) if entry > 0 else None}
        if diff > 0.01:
            if current == 0.0:
                cand["verdict"] = "would_open"
            elif dev <= -GRID_BUY_DIP:
                cand["verdict"] = "would_add_dip"
            else:
                cand["verdict"] = "wait_for_dip"
            cand["detail"] = f"{current:.1f}→{target:.1f} sh · {dev*100:+.1f}% vs cost"
        elif diff < -0.01 and current > 0.0 and dev >= GRID_TP_GAIN:
            cand["verdict"] = "would_trim"
            cand["detail"] = f"overweight {current:.1f}→{target:.1f} sh · {dev*100:+.1f}% (if tax-eligible)"
        else:
            cand["verdict"] = "at_target"
            cand["detail"] = f"{current:.1f}/{target:.1f} sh"
        out.append(cand)
    return out


def _build_warnings(sleeves, candidates, lt_candidates, regime, swing_factor,
                    sw_buy_sig, hr_buy_sig, paused, market_open):
    """Flag conditions where a sleeve can't actually deploy its allocation."""
    w = []
    if paused:
        w.append({"sleeve": "all", "level": "warn", "message": "Auto-trading is PAUSED — nothing will trade until you resume it."})
        return w
    by_key = {s["key"]: s for s in sleeves}

    def over(key, label):
        s = by_key.get(key)
        if s and s["cap"] > 0 and s["deployed"] > s["cap"] * 1.001:
            w.append({"sleeve": key, "level": "warn",
                      "message": f"{label} is over its cap (${s['deployed']:,.0f} deployed vs ${s['cap']:,.0f}). "
                                 f"No new {label.lower()} buys until positions exit and free up budget."})

    over("swing", "Swing"); over("high_risk", "High-risk")

    if swing_factor < 1.0:
        sw = by_key.get("swing", {})
        w.append({"sleeve": "swing", "level": "info",
                  "message": f"Regime '{regime}' is shrinking swing capital ×{swing_factor} "
                             f"(cap {sw.get('weight',0)*100:.0f}% → {sw.get('effective_weight',0)*100:.0f}% of equity)."})

    sw_exec = sum(1 for c in candidates if c["sleeve"] == "swing" and c["verdict"] == "buy")
    if sw_buy_sig > 0 and sw_exec == 0:
        w.append({"sleeve": "swing", "level": "warn",
                  "message": f"{sw_buy_sig} swing BUY signal(s) today but none can execute "
                             f"(already held / not assigned to swing / budget exhausted)."})
    hr_exec = sum(1 for c in candidates if c["sleeve"] == "high_risk" and c["verdict"] == "buy")
    if hr_buy_sig > 0 and hr_exec == 0:
        w.append({"sleeve": "high_risk", "level": "warn",
                  "message": f"{hr_buy_sig} high-risk BUY signal(s) today but none can execute."})

    lt = by_key.get("longterm", {})
    lt_will = sum(1 for c in lt_candidates if c["verdict"] in ("would_open", "would_add_dip"))
    if lt.get("available", 0) > 0.03 * (lt.get("cap", 0) or 1) and lt_will == 0 and lt_candidates:
        w.append({"sleeve": "longterm", "level": "info",
                  "message": f"MPT has ${lt.get('available',0):,.0f} of its allocation free, but every target is at weight "
                             f"or waiting for a 3%+ dip — the grid only adds on dips or new names, so it's holding."})
    return w


def build_execution_plan(db, api=None, suggestions_data=None) -> dict:
    """Compute the full execution plan (status, sleeves, candidate verdicts) read-only."""
    from app.main import (get_daily_suggestions, get_strategy_buckets, get_strategy_assignments)
    from execution.executor import get_alpaca_api, auto_trading_paused
    from app.core.config import (SWING_POSITION_PCT, SWING_VOL_TARGET, HIGH_RISK_CAP,
                                 REGIME_SWING_FACTORS, REGIME_OVERLAY_ENABLED, HEDGE_MODE,
                                 SWING_AUTOSIZE, SWING_TOP_N, HIGH_RISK_TOP_N)

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
    positions = {}  # symbol -> {qty, mv, avg_entry, price}
    deployed = {"swing": 0.0, "longterm": 0.0, "hold": 0.0, "short_term": 0.0}
    deployed_spec = 0.0
    if api:
        try:
            acct = api.get_account()
            equity = float(acct.equity); buying_power = float(acct.buying_power)
            for p in api.list_positions():
                held.add(p.symbol)
                positions[p.symbol] = {"qty": float(p.qty), "mv": float(p.market_value),
                                       "avg_entry": float(p.avg_entry_price), "price": float(p.current_price)}
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

    # Position size auto-scales with the chosen allocation (nominal weight ÷ top-N), matching the executor.
    swing_pos_pct = (swing_w / max(1, SWING_TOP_N)) if SWING_AUTOSIZE else SWING_POSITION_PCT
    hr_pos_pct = (hr_w / max(1, HIGH_RISK_TOP_N)) if SWING_AUTOSIZE else SWING_POSITION_PCT

    # Replay swing + high-risk entry gates.
    candidates = []
    if not plan["paused"]:
        swing_buys = [s for s in suggestions_data.get("swing_suggestions", []) if s.get("action") == "BUY"]
        sw_cands, _ = _replay_sleeve(swing_buys, core_swing_set, swing_budget, held, db,
                                     equity, swing_pos_pct, vol_map, "swing", SWING_VOL_TARGET)
        candidates += sw_cands
        if hr_w > 0 and spec_set:
            hr_buys = [s for s in suggestions_data.get("high_risk_suggestions", []) if s.get("action") == "BUY"]
            hr_cands, _ = _replay_sleeve(hr_buys, spec_set, hr_budget, held, db,
                                         equity, hr_pos_pct, vol_map, "high_risk", SWING_VOL_TARGET)
            candidates += hr_cands

    # Long-term: replay the MPT grid's REAL rules (open new / add on 3%+ dip / wait / trim).
    lt_candidates = _replay_longterm(suggestions_data.get("long_term_allocation", []),
                                     longterm_set, positions, equity, lt_w) if not plan["paused"] else []

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
    lt_candidates.sort(key=lambda c: (c["verdict"] not in ("would_open", "would_add_dip"), c["ticker"]))
    plan["longterm_candidates"] = lt_candidates

    sw_buy_sig = sum(1 for s in suggestions_data.get("swing_suggestions", []) if s.get("action") == "BUY")
    hr_buy_sig = sum(1 for s in suggestions_data.get("high_risk_suggestions", []) if s.get("action") == "BUY")
    lt_will = sum(1 for c in lt_candidates if c["verdict"] in ("would_open", "would_add_dip"))
    plan["summary"] = {
        "swing_buy_signals": sw_buy_sig,
        "high_risk_buy_signals": hr_buy_sig,
        "would_execute": sum(1 for c in candidates if c["verdict"] == "buy"),
        "longterm_will_buy": lt_will,
        "longterm_waiting": sum(1 for c in lt_candidates if c["verdict"] == "wait_for_dip"),
    }
    plan["warnings"] = _build_warnings(plan["sleeves"], candidates, lt_candidates, regime,
                                       swing_factor, sw_buy_sig, hr_buy_sig, plan["paused"], plan["market_open"])
    return plan
