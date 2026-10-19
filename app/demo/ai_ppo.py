"""Trading X DEMO: independent, fail-closed PPO inference. Never executes LIVE orders.

Training and inference use the same observation function. The model chooses
WAIT/LONG/SHORT/CLOSE and ATR-based SL/TP multipliers (MultiDiscrete 4/3/3).
Risk limits are deterministic safety controls, not trading signals.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from threading import RLock
import time
import uuid

from app.config import (
    LEVERAGE, MIN_CAPITAL_USDC, POSITION_PERCENT, MAX_TRADE_MARGIN_USDC,
    EXECUTION_MAX_SPREAD_BPS,
)
from app.demo.simulator import valid_quote, fill_price, fee_usdc, _money, _quantity
from app.demo.ledger import now_iso
from app.demo.settings import DEMO_TAKER_FEE_BPS, DEMO_SLIPPAGE_BPS

from app.demo.ai_features import (
    BAR_MS, OBS_NAMES, ACTIONS, STOP_ATR, TAKE_ATR, MODEL_SCHEMA,
    CandleStore, configured_symbols, observation,
)


def _linear(x: list[float], layer: dict) -> list[float]:
    weights = layer["weight"]
    bias = layer["bias"]
    if len(weights) != len(bias) or any(len(row) != len(x) for row in weights):
        raise ValueError("actor_layer_shape_mismatch")
    return [sum(a*b for a, b in zip(x, row)) + float(b) for row, b in zip(weights, bias)]


def _softmax(xs: list[float]) -> list[float]:
    m = max(xs)
    e = [math.exp(v-m) for v in xs]
    return [v/sum(e) for v in e]


@dataclass(frozen=True)
class Decision:
    symbol: str
    action: str
    stop_atr: float
    take_atr: float
    probability: float
    model_version: str


class PPOPolicy:
    """Portable inference: no torch in Railway; SHA256-checked JSON actor."""
    def __init__(self, manifest_path: str):
        path = Path(manifest_path)
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta.get("schema") != MODEL_SCHEMA or meta.get("observation_names") != list(OBS_NAMES):
            raise ValueError("PPO model/observation schema mismatch")
        if meta.get("approved_for_demo") is not True:
            raise ValueError("PPO model not approved for demo")
        if meta.get("action_dims") != [4, 3, 3]:
            raise ValueError("PPO action schema mismatch")
        cap = float(os.getenv("DEMO_AI_MAX_MARGIN_USDC", "25"))
        effective_cap = min(cap, MAX_TRADE_MARGIN_USDC) if MAX_TRADE_MARGIN_USDC > 0 else cap
        profile = meta.get("runtime_profile")
        if (not isinstance(profile, dict) or
            profile.get("leverage") != LEVERAGE or
            float(profile.get("position_percent", -1)) != POSITION_PERCENT or
            float(profile.get("margin_cap_usdc", -1)) != effective_cap or
            DEMO_TAKER_FEE_BPS != 5.0 or DEMO_SLIPPAGE_BPS != 2.0):
            raise ValueError("AI training/runtime risk profile mismatch: retrain before deploy")
        filename = meta.get("actor_file")
        if filename != "actor.json":
            raise ValueError("actor filename forbidden")
        actor_path = path.parent / filename
        raw = actor_path.read_bytes()
        if sha256(raw).hexdigest() != meta.get("actor_sha256"):
            raise ValueError("PPO actor integrity mismatch")
        actor = json.loads(raw)
        if actor.get("activation") != "tanh" or len(actor.get("hidden", [])) != 2:
            raise ValueError("unsupported PPO actor architecture")
        if len(actor["hidden"][0]["weight"][0]) != len(OBS_NAMES):
            raise ValueError("actor input shape")
        if len(actor["head"]["weight"]) != 10:
            raise ValueError("actor output shape")
        self.layers = actor["hidden"]
        self.head = actor["head"]
        self.version = str(meta["version"])
        symbols = meta.get("trained_symbols", [])
        if not symbols or not isinstance(symbols, list):
            raise ValueError("missing training symbols")
        self.symbols = set(symbols)

    def predict(self, symbol: str, obs: list[float]) -> Decision:
        if symbol not in self.symbols or len(obs) != len(OBS_NAMES) or not all(map(math.isfinite, obs)):
            raise ValueError("symbol_not_trained_or_bad_features")
        x = obs
        for layer in self.layers:
            x = [math.tanh(y) for y in _linear(x, layer)]
        logits = _linear(x, self.head)
        action_p = _softmax(logits[:4])
        # Deterministic argmax = no random exploration in DEMO.
        action = max(range(4), key=lambda i: action_p[i])
        stop = max(range(3), key=lambda i: logits[4+i])
        take = max(range(3), key=lambda i: logits[7+i])
        return Decision(symbol, ACTIONS[action], STOP_ATR[stop], TAKE_ATR[take],
                        action_p[action], self.version)


def ai_gate(account: dict) -> str | None:
    if not account.get("enabled") or account.get("position"):
        return "NOT_ELIGIBLE"
    if float(account.get("balance_usdc") or 0) < MIN_CAPITAL_USDC:
        return "MIN_CAPITAL"
    return None


def open_ai_position(account: dict, decision: Decision, bid: float, ask: float,
                     atr: float, *, rejection: dict | None = None) -> dict | None:
    """AI supplies all trade choices. Safety envelope cannot choose trades."""
    def reject(code):
        if rejection is not None:
            rejection["reason"] = code
        return None

    if ai_gate(account):
        return reject("ENTRY_GATE")
    if decision.action not in ("LONG", "SHORT") or not valid_quote(bid, ask):
        return reject("INVALID_AI_ACTION_OR_QUOTE")
    if (ask-bid)/bid*10000 > EXECUTION_MAX_SPREAD_BPS:
        return reject("SPREAD_TOO_WIDE")
    if not math.isfinite(atr) or atr <= 0:
        return reject("BAD_AI_ATR")
    direction = decision.action.lower()
    entry = fill_price("buy" if direction == "long" else "sell", bid, ask)
    stop = entry - decision.stop_atr*atr if direction == "long" else entry + decision.stop_atr*atr
    target = entry + decision.take_atr*atr if direction == "long" else entry - decision.take_atr*atr
    if stop <= 0 or target <= 0 or not all(map(math.isfinite, (stop, target))):
        return reject("INVALID_AI_EXITS")
    balance = float(account["balance_usdc"])
    # Explicit AI research cap even when the legacy account's margin cap is disabled.
    ai_margin_cap = float(os.getenv("DEMO_AI_MAX_MARGIN_USDC", "25"))
    if not math.isfinite(ai_margin_cap) or not 1 <= ai_margin_cap <= 1000:
        raise ValueError("DEMO_AI_MAX_MARGIN_USDC must be between 1 and 1000")
    margin = min(balance*POSITION_PERCENT, ai_margin_cap)
    if MAX_TRADE_MARGIN_USDC > 0:
        margin = min(margin, MAX_TRADE_MARGIN_USDC)
    margin = _money(min(margin, balance/(1 + LEVERAGE*DEMO_TAKER_FEE_BPS/10000)-1e-8))
    if margin <= 0:
        return reject("NO_MARGIN")
    notional = margin*LEVERAGE
    if notional < 10.2:
        return reject("MIN_NOTIONAL")
    qty = _quantity(notional/entry)
    fee = fee_usdc(qty*entry)
    if qty <= 0 or qty*entry < 10.2 or margin+fee > balance:
        return reject("MIN_QUANTITY_OR_FEE")
    new = deepcopy(account)
    new["balance_usdc"] = _money(balance-fee)
    new["position"] = {
        "id": uuid.uuid4().hex, "mode": "DEMO", "symbol": f"{decision.symbol}-PERP",
        "direction": direction, "entry_price": entry, "quantity": qty,
        "margin_usdc": margin, "requested_margin_usdc": margin,
        "leverage": int(LEVERAGE), "notional_usdc": _money(qty*entry),
        "stop_price": stop, "target_price": target, "initial_stop_price": stop,
        "rr": round(abs(target-entry)/abs(entry-stop), 4),
        "strategy_id": "AI_PPO_AUTONOMOUS", "strategy_model": "ppo",
        "decision_source": "ai_ppo_only", "ai_model_version": decision.model_version,
        "ai_action_probability": decision.probability, "ai_bars_held": 0,
        "ai_last_decision_bar": None, "ai_stop_atr": decision.stop_atr,
        "ai_take_atr": decision.take_atr,
        "opened_at": now_iso(), "entry_fee_usdc": fee,
        "demo_be_enabled": False, "demo_be_armed": False,
        "mark_price": (bid+ask)/2, "unrealized_pnl_usdc": 0.0,
        "audit_quotes": 0, "audit_mfe_pct": 0.0, "audit_mae_pct": 0.0,
        "audit_mfe_usdc": 0.0, "audit_mae_usdc": 0.0,
        "audit_last_quote_at": now_iso(), "audit_max_quote_gap_seconds": 0.0,
        "audit_quote_gaps_over_10s": 0,
    }
    new["position_open"] = True
    new["last_signal_at"] = now_iso()
    new["last_entry_at"] = now_iso()
    new["last_error"] = None
    return new
