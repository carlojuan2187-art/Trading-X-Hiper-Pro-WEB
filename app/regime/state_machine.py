from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_CONFIRM_BARS = 3
DEFAULT_COOLDOWN_BARS = 2
DEFAULT_MIN_ACTIVE_BARS = 3
UNKNOWN_REGIME = "UNKNOWN"


def _normalize_previous(previous_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    prev = dict(previous_state or {})
    return {
        "active_regime": str(prev.get("active_regime") or UNKNOWN_REGIME),
        "candidate_regime": str(prev.get("candidate_regime") or UNKNOWN_REGIME),
        "pending_regime": str(prev.get("pending_regime") or ""),
        "pending_count": max(int(prev.get("pending_count") or 0), 0),
        "bars_in_active": max(int(prev.get("bars_in_active") or 0), 0),
        "cooldown_remaining": max(int(prev.get("cooldown_remaining") or 0), 0),
        "transitions": max(int(prev.get("transitions") or 0), 0),
        "last_bar_id": str(prev.get("last_bar_id") or ""),
        "duplicate_bar": bool(prev.get("duplicate_bar", False)),
    }



def advance_regime_state(
    candidate_regime: str,
    previous_state: Optional[Dict[str, Any]] = None,
    *,
    confirm_bars: int = DEFAULT_CONFIRM_BARS,
    cooldown_bars: int = DEFAULT_COOLDOWN_BARS,
    min_active_bars: int = DEFAULT_MIN_ACTIVE_BARS,
    bar_id: Optional[Any] = None,
) -> Dict[str, Any]:
    candidate = str(candidate_regime or UNKNOWN_REGIME).strip().upper() or UNKNOWN_REGIME
    confirm_bars = max(int(confirm_bars), 1)
    cooldown_bars = max(int(cooldown_bars), 0)
    min_active_bars = max(int(min_active_bars), 0)

    prev = _normalize_previous(previous_state)
    normalized_bar_id = str(bar_id or "").strip()

    # El router puede ejecutarse varias veces durante la misma vela (por usuario o
    # por ciclos del scanner). Una confirmación de régimen debe avanzar UNA sola
    # vez por vela cerrada; repetir la misma bar no reduce cooldown ni incrementa
    # pending_count/bars_in_active.
    if normalized_bar_id and normalized_bar_id == str(prev.get("last_bar_id") or ""):
        duplicate = dict(prev)
        duplicate["candidate_regime"] = candidate
        duplicate["changed"] = False
        duplicate["duplicate_bar"] = True
        return duplicate

    active = prev["active_regime"]
    pending = prev["pending_regime"]
    pending_count = prev["pending_count"]
    bars_in_active = prev["bars_in_active"]
    cooldown_remaining = max(prev["cooldown_remaining"] - 1, 0)
    transitions = prev["transitions"]
    changed = False

    if active == UNKNOWN_REGIME and bars_in_active <= 0:
        if candidate != UNKNOWN_REGIME:
            active = candidate
        bars_in_active = 1
        pending = ""
        pending_count = 0
    elif candidate == active:
        bars_in_active += 1
        pending = ""
        pending_count = 0
    else:
        if candidate == pending:
            pending_count += 1
        else:
            pending = candidate
            pending_count = 1

        required = 1 if active == UNKNOWN_REGIME and candidate != UNKNOWN_REGIME else confirm_bars
        can_switch = cooldown_remaining <= 0 and bars_in_active >= min_active_bars
        if active == UNKNOWN_REGIME:
            can_switch = True

        if candidate == UNKNOWN_REGIME:
            can_switch = can_switch and pending_count >= confirm_bars

        if can_switch and pending_count >= required:
            active = candidate
            bars_in_active = 1
            cooldown_remaining = cooldown_bars
            transitions += 1
            changed = True
            pending = ""
            pending_count = 0
        else:
            bars_in_active += 1

    return {
        "active_regime": active,
        "candidate_regime": candidate,
        "pending_regime": pending,
        "pending_count": pending_count,
        "bars_in_active": bars_in_active,
        "cooldown_remaining": cooldown_remaining,
        "transitions": transitions,
        "changed": changed,
        "last_bar_id": normalized_bar_id or str(prev.get("last_bar_id") or ""),
        "duplicate_bar": False,
    }


class RegimeStateMachine:
    def __init__(
        self,
        *,
        confirm_bars: int = DEFAULT_CONFIRM_BARS,
        cooldown_bars: int = DEFAULT_COOLDOWN_BARS,
        min_active_bars: int = DEFAULT_MIN_ACTIVE_BARS,
        initial_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.confirm_bars = max(int(confirm_bars), 1)
        self.cooldown_bars = max(int(cooldown_bars), 0)
        self.min_active_bars = max(int(min_active_bars), 0)
        self.state = _normalize_previous(initial_state)

    def update(self, candidate_regime: str, *, bar_id: Optional[Any] = None) -> Dict[str, Any]:
        self.state = advance_regime_state(
            candidate_regime,
            self.state,
            confirm_bars=self.confirm_bars,
            cooldown_bars=self.cooldown_bars,
            min_active_bars=self.min_active_bars,
            bar_id=bar_id,
        )
        return dict(self.state)

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.state)


__all__ = [
    "DEFAULT_CONFIRM_BARS",
    "DEFAULT_COOLDOWN_BARS",
    "DEFAULT_MIN_ACTIVE_BARS",
    "UNKNOWN_REGIME",
    "RegimeStateMachine",
    "advance_regime_state",
]
