from __future__ import annotations

import math
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def adaptive_entry_drift_limit(*, atr_pct: float, base_pct: float, atr_mult: float, hard_pct: float) -> float:
    """Return a volatility-aware entry drift ceiling.

    The floor prevents an unrealistically tiny tolerance in quiet markets while
    the hard cap prevents the scanner/engine from chasing a signal just because
    current volatility is high.
    """
    base = max(_safe_float(base_pct), 0.0)
    atr_component = max(_safe_float(atr_pct), 0.0) * max(_safe_float(atr_mult), 0.0)
    hard = max(_safe_float(hard_pct), 0.0)
    limit = max(base, atr_component)
    return min(limit, hard) if hard > 0.0 else limit


def evaluate_entry_drift(
    *,
    direction: str,
    signal_price: float,
    current_price: float,
    allowed_pct: float,
    adverse_mult: float = 1.5,
) -> dict[str, Any]:
    """Validate that a live entry has not moved too far away from the signal.

    A move in the trade direction is *chase* (LONG higher / SHORT lower), so it
    uses the strict limit. A pullback provides a potentially better price and is
    allowed a little more room, bounded by ``adverse_mult`` for backwards
    compatibility with the existing env name.
    """
    d = str(direction or "").strip().lower()
    signal = _safe_float(signal_price)
    current = _safe_float(current_price)
    allowed = max(_safe_float(allowed_pct), 0.0)
    pullback_mult = max(_safe_float(adverse_mult, 1.5), 1.0)

    if d not in {"long", "short"}:
        return {"ok": False, "reason": "BAD_DIRECTION", "drift_pct": 0.0, "allowed_pct": allowed}
    if not all(math.isfinite(x) for x in (signal, current, allowed, pullback_mult)) or signal <= 0.0 or current <= 0.0:
        return {"ok": False, "reason": "BAD_REFERENCE_PRICE", "drift_pct": 0.0, "allowed_pct": allowed}

    raw_drift = (current - signal) / signal
    chase_drift = raw_drift if d == "long" else -raw_drift
    is_chase = chase_drift > 0.0
    magnitude = abs(raw_drift)
    limit = allowed if is_chase else allowed * pullback_mult

    if magnitude > limit:
        return {
            "ok": False,
            "reason": "ENTRY_DRIFT_CHASE" if is_chase else "ENTRY_DRIFT_PULLBACK_TOO_DEEP",
            "direction": d,
            "signal_price": signal,
            "reference_price": signal,
            "current_price": current,
            "drift_pct": raw_drift,
            "directional_drift_pct": chase_drift,
            "drift_abs_pct": magnitude,
            "allowed_pct": allowed,
            "effective_limit_pct": limit,
            "is_chase": is_chase,
        }

    return {
        "ok": True,
        "reason": "OK",
        "direction": d,
        "signal_price": signal,
        "reference_price": signal,
        "current_price": current,
        "drift_pct": raw_drift,
        "directional_drift_pct": chase_drift,
        "drift_abs_pct": magnitude,
        "allowed_pct": allowed,
        "effective_limit_pct": limit,
        "is_chase": is_chase,
    }


def absolute_risk_reward(*, direction: str, entry_price: float, stop_price: float, target_price: float) -> dict[str, Any]:
    """Compute R:R from absolute structural prices and validate their geometry."""
    d = str(direction or "").strip().lower()
    entry = _safe_float(entry_price)
    stop = _safe_float(stop_price)
    target = _safe_float(target_price)

    if d not in {"long", "short"}:
        return {"ok": False, "reason": "BAD_DIRECTION", "rr": 0.0}
    if not all(math.isfinite(x) for x in (entry, stop, target)) or min(entry, stop, target) <= 0.0:
        return {"ok": False, "reason": "BAD_PRICE", "rr": 0.0}

    if d == "long":
        if stop >= entry:
            return {"ok": False, "reason": "STOP_INVALID_FOR_ENTRY", "rr": 0.0}
        if target <= entry:
            return {"ok": False, "reason": "TARGET_INVALID_FOR_ENTRY", "rr": 0.0}
        risk = entry - stop
        reward = target - entry
    else:
        if stop <= entry:
            return {"ok": False, "reason": "STOP_INVALID_FOR_ENTRY", "rr": 0.0}
        if target >= entry:
            return {"ok": False, "reason": "TARGET_INVALID_FOR_ENTRY", "rr": 0.0}
        risk = stop - entry
        reward = entry - target

    if risk <= 0.0 or reward <= 0.0:
        return {"ok": False, "reason": "INVALID_RISK_REWARD_GEOMETRY", "rr": 0.0}

    return {
        "ok": True,
        "reason": "OK",
        "risk": risk,
        "reward": reward,
        "risk_pct": risk / entry,
        "reward_pct": reward / entry,
        "rr": reward / risk,
    }


def levels_to_percentages(*, direction: str, entry_price: float, stop_price: float, target_price: float) -> dict[str, float]:
    """Convert fixed absolute SL/TP levels to percentages from the actual fill."""
    info = absolute_risk_reward(
        direction=direction,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
    )
    if not info.get("ok"):
        return {"sl_pct": 0.0, "tp_pct": 0.0, "rr": 0.0}
    return {
        "sl_pct": float(info["risk_pct"]),
        "tp_pct": float(info["reward_pct"]),
        "rr": float(info["rr"]),
    }


def meets_min_rr(rr: float, minimum: float) -> bool:
    """Numerical tolerance only, never a material relaxation of the RR gate."""
    return math.isfinite(rr) and math.isfinite(minimum) and rr + 1e-10 >= minimum


def rr_entry_price_limit(*, stop_price: float, target_price: float, minimum_rr: float) -> float:
    """Maximum LONG entry / minimum SHORT entry preserving the specified RR."""
    values = (stop_price, target_price, minimum_rr)
    if not all(math.isfinite(x) and x > 0 for x in values):
        raise ValueError('Invalid structural entry limit')
    return (target_price + minimum_rr * stop_price) / (1 + minimum_rr)
