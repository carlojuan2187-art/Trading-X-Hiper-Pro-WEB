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
from app.demo.settings import DEMO_TAKER_FEE_BPS

BAR_MS = 300_000
OBS_NAMES = (
    "return_1", "return_3", "return_12", "range_pct", "body_pct",
    "atr_pct", "volume_ratio", "realized_vol_12", "trend_12",
    "spread_pct", "position_sign", "unrealized_pct", "position_age",
)
ACTIONS = ("WAIT", "LONG", "SHORT", "CLOSE")
STOP_ATR = (0.8, 1.2, 1.8)
TAKE_ATR = (1.2, 2.0, 3.0)
MODEL_SCHEMA = "trading_x_ppo_v1"


def configured_symbols() -> tuple[str, ...]:
    raw = os.getenv("DEMO_AI_SYMBOLS", "BTC,ETH,SOL")
    coins = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    if not coins or len(coins) > 12 or any(
        len(s) > 25 or not s.replace("-", "").isalnum() for s in coins
    ):
        raise ValueError("DEMO_AI_SYMBOLS inválido (máximo 12 símbolos)")
    return coins


def observation(candles: list[dict], bid: float, ask: float,
                position: dict | None = None) -> tuple[list[float], float]:
    """Exactly 40 continuous CLOSED 5-minute bars, chronological."""
    if len(candles) < 40 or not valid_quote(bid, ask):
        raise ValueError("incomplete_history_or_quote")
    bars = candles[-40:]
    ts = [int(c["t"]) for c in bars]
    if any(b - a != BAR_MS for a, b in zip(ts, ts[1:])):
        raise ValueError("candle_gap")
    closes = [float(c["c"]) for c in bars]
    opens = [float(c["o"]) for c in bars]
    highs = [float(c["h"]) for c in bars]
    lows = [float(c["l"]) for c in bars]
    volumes = [float(c["v"]) for c in bars]
    if any(not all(map(math.isfinite, row)) or min(o, h, l, c) <= 0
           or l > min(o, c) or h < max(o, c) or v < 0
           for row in zip(opens, highs, lows, closes, volumes)):
        raise ValueError("invalid_candles")
    close = closes[-1]
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
              abs(lows[i]-closes[i-1])) for i in range(1, 40)]
    atr = sum(tr[-14:]) / 14.0
    if not math.isfinite(atr) or atr <= 0:
        raise ValueError("zero_atr")
    returns = [math.log(closes[i]/closes[i-1]) for i in range(1, 40)]
    mean = sum(returns[-12:]) / 12
    vol = math.sqrt(sum((r-mean)**2 for r in returns[-12:]) / 12)
    sign = 0.0
    unrealized = 0.0
    age = 0.0
    if position:
        sign = 1.0 if position["direction"] == "long" else -1.0
        entry = float(position["entry_price"])
        unrealized = sign * ((bid if sign > 0 else ask) / entry - 1)
        age = min(1.0, max(0.0, float(position.get("ai_bars_held", 0)) / 100))
    values = [
        returns[-1], sum(returns[-3:]), sum(returns[-12:]),
        (highs[-1]-lows[-1])/close, (close-opens[-1])/close,
        atr/close, volumes[-1]/max(sum(volumes[-21:-1])/20, 1e-9)-1,
        vol, close/closes[-13]-1, (ask-bid)/bid, sign, unrealized, age,
    ]
    # All values bounded to keep the on-device actor stable across coins.
    values = [max(-3.0, min(3.0, float(x))) for x in values]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("invalid_observation")
    return values, atr


class CandleStore:
    """One bounded WS candle cache per symbol; open candles are NEVER features."""
    def __init__(self):
        self.rows: dict[str, deque] = defaultdict(lambda: deque(maxlen=80))
        self.books: dict[str, tuple[float, float, int]] = {}
        self.lock = RLock()

    def push_candle(self, data: dict) -> bool:
        try:
            coin = str(data["s"]).upper()
            if data["i"] != "5m":
                return False
            row = {k: data[k] for k in ("t", "o", "h", "l", "c", "v")}
            row["t"] = int(row["t"])
            if row["t"] % BAR_MS:
                return False
            values = [float(row[k]) for k in ("o", "h", "l", "c", "v")]
            if not all(map(math.isfinite, values)) or min(values[:4]) <= 0:
                return False
            with self.lock:
                existing = self.rows[coin]
                if existing and row["t"] < existing[-1]["t"]:
                    return False
                if existing and row["t"] == existing[-1]["t"]:
                    existing[-1] = row
                else:
                    existing.append(row)
                return True
        except (TypeError, KeyError, ValueError, OverflowError):
            return False

    def push_book(self, coin: str, bid: float, ask: float) -> None:
        if valid_quote(bid, ask):
            with self.lock:
                self.books[coin.upper()] = (bid, ask, int(time.time()*1000))

    def snapshot(self, coin: str, *, now_ms: int | None = None):
        now = int(time.time()*1000) if now_ms is None else now_ms
        with self.lock:
            quote = self.books.get(coin)
            if quote is None or not 0 <= now - quote[2] <= 2500:
                return None
            # Never include an unfinished streaming candle.
            bars = [b.copy() for b in self.rows[coin] if b["t"] + BAR_MS <= now]
        if len(bars) < 40 or now - (bars[-1]["t"] + BAR_MS) > BAR_MS:
            return None
        return bars[-40:], quote[0], quote[1]

    def prime(self, coin: str, rows: list[dict]) -> int:
        count = 0
        for row in sorted(rows, key=lambda r: int(r["t"])):
            if str(row.get("s", "")).upper() == coin and self.push_candle(row):
                count += 1
        return count


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
