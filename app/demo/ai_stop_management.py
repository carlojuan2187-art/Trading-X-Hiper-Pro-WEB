"""PPO-v3-only dynamic stop actions, guarded by executable bid/ask.

STOP never moves away from entry after being tightened. No trailing or BE
without an explicit AI PROTECT/TRAIL choice. Existing v2 positions unchanged.
"""
from __future__ import annotations
from copy import deepcopy
import math


def tighten_stop(position: dict, bid: float, ask: float, *, action: str) -> dict | None:
    if action not in ("PROTECT", "TRAIL") or position.get("decision_source") != "ai_ppo_only":
        return None
    if not all(math.isfinite(float(x)) and float(x)>0
               for x in (bid, ask, position["entry_price"], position["quantity"],
                         position["initial_stop_price"])):
        return None
    if bid > ask:
        return None
    side = str(position["direction"]).lower()
    if side not in ("long", "short"):
        return None
    sign = 1 if side == "long" else -1
    entry = float(position["entry_price"])
    qty = float(position["quantity"])
    old_stop = float(position["stop_price"])
    original_risk = abs(entry-float(position["initial_stop_price"]))
    if original_risk <= 0:
        return None
    executable = float(bid if sign>0 else ask)
    entry_fee = float(position.get("entry_fee_usdc") or 0.0)
    if entry_fee < 0:
        return None
    fee_rate = entry_fee/max(qty*entry,1e-9)
    exit_fee = fee_rate*qty*executable
    net = sign*(executable-entry)*qty-entry_fee-exit_fee
    # No break-even or trailing promise while trade is still net-negative.
    if net <= 0:
        return None
    # Explicit fee + slippage allowance and tiny positive locked gain.
    buffer = max(entry*0.0002, original_risk*0.10)
    fee_cover = (entry_fee+exit_fee)/qty + buffer
    breakeven = entry + sign*fee_cover
    candidate = breakeven
    if action == "TRAIL":
        # Distance controlled by volatility AND the original risk. Never
        # closer than 0.6 ATR to avoid an artificial near-price stop.
        atr = float(position.get("ai_entry_atr") or 0)
        gap = max(0.7*atr, 0.55*original_risk)
        candidate = (max(candidate, executable-gap) if sign>0
                     else min(candidate, executable+gap))
    # Do not arm a stop beyond the current executable quote or worsen risk.
    strictly_better = candidate > old_stop if sign>0 else candidate < old_stop
    safely_inside = candidate < bid-entry*0.00005 if sign>0 else candidate > ask+entry*0.00005
    if not strictly_better or not safely_inside or not math.isfinite(candidate):
        return None
    changed = deepcopy(position)
    changed["stop_price"] = candidate
    changed["ai_stop_moves"] = int(position.get("ai_stop_moves") or 0)+1
    changed["ai_last_stop_action"] = action
    changed["ai_trailing_active"] = action == "TRAIL" or bool(position.get("ai_trailing_active"))
    changed["demo_be_armed"] = True
    changed["demo_be_stop_price"] = candidate
    return changed


def maybe_follow_trailing(position: dict, bid: float, ask: float) -> dict | None:
    if not position.get("ai_trailing_active"):
        return None
    return tighten_stop(position, bid, ask, action="TRAIL")
