"""Pure, dependency-free research and runtime feature contract.

Importing this module requires NO MongoDB, Telegram token, or broker credentials.
"""
from __future__ import annotations
from collections import defaultdict, deque
from threading import RLock
import math
import os
import time

def valid_quote(bid: float, ask: float) -> bool:
    try:
        bid, ask = float(bid), float(ask)
        return math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask
    except (ValueError, TypeError, OverflowError):
        return False


BAR_MS = 300_000
OBS_NAMES = (
    "return_1", "return_3", "return_12", "range_pct", "body_pct",
    "atr_pct", "volume_ratio", "realized_vol_12", "trend_12",
    "spread_pct", "position_sign", "unrealized_pct", "position_age",
)
ACTIONS = ("WAIT", "LONG", "SHORT", "CLOSE")
STOP_ATR = (0.8, 1.2, 1.8)
TAKE_ATR = (1.2, 2.0, 3.0)
MODEL_SCHEMA = "trading_x_ppo_v2"


def configured_symbols(trained_symbols=None) -> tuple[str, ...]:
    """Use every trained LIVE-eligible market unless an explicit DEMO subset is set."""
    raw = os.getenv("DEMO_AI_SYMBOLS")
    if raw is None or raw.strip().upper() == "AUTO":
        coins = (tuple(sorted(trained_symbols)) if trained_symbols is not None
                 else ("BTC", "ETH", "SOL"))
    else:
        coins = tuple(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))
    if not coins or len(coins) > 250 or any(
        len(s) > 25 or not s.replace("-", "").isalnum() for s in coins
    ):
        raise ValueError("DEMO_AI_SYMBOLS inválido (1..250 símbolos)")
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
    if any(not all(map(math.isfinite, (o, h, l, c, v))) or min(o, h, l, c) <= 0
           or l > min(o, c) or h < max(o, c) or v < 0
           for o, h, l, c, v in zip(opens, highs, lows, closes, volumes)):
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
    # Model inputs are expressed in percent units. Raw log returns around
    # 0.001 make the neural actor almost blind to M5 price movement.
    # The same immutable schema is used by both offline PPO and DEMO WS.
    values = [
        returns[-1]*100, sum(returns[-3:])*100, sum(returns[-12:])*100,
        (highs[-1]-lows[-1])/close*100, (close-opens[-1])/close*100,
        atr/close*100, volumes[-1]/max(sum(volumes[-21:-1])/20, 1e-9)-1,
        vol*100, (close/closes[-13]-1)*100, (ask-bid)/bid*100,
        sign, unrealized*100, age,
    ]
    # All values bounded to keep the on-device actor stable across coins.
    values = [max(-3.0, min(3.0, float(x))) for x in values]
    if not all(math.isfinite(x) for x in values):
        raise ValueError("invalid_observation")
    return values, atr


class CandleStore:
    """One bounded WS candle cache per symbol; open candles are NEVER features."""
    def __init__(self):
        self.rows: dict[str, deque] = defaultdict(lambda: deque(maxlen=560))
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

    def snapshot(self, coin: str, *, now_ms: int | None = None, lookback: int = 40):
        now = int(time.time()*1000) if now_ms is None else now_ms
        with self.lock:
            quote = self.books.get(coin)
            if quote is None or not 0 <= now - quote[2] <= 2500:
                return None
            # Never include an unfinished streaming candle.
            bars = [b.copy() for b in self.rows[coin] if b["t"] + BAR_MS <= now]
        if len(bars) < lookback or now - (bars[-1]["t"] + BAR_MS) > BAR_MS:
            return None
        return bars[-lookback:], quote[0], quote[1]

    def prime(self, coin: str, rows: list[dict]) -> int:
        count = 0
        for row in sorted(rows, key=lambda r: int(r["t"])):
            if str(row.get("s", "")).upper() == coin and self.push_candle(row):
                count += 1
        return count



# PPO v3 research contract: opt-in only; existing v2 paper actors keep their
# original schema, 40-bar observation, checkpoint and trading behavior.
from app.demo.ai_indicators import (
    HISTORY_BARS as ADVANCED_HISTORY_BARS,
    INDICATOR_NAMES, multi_timeframe_features,
)

ADVANCED_ACTIONS = ("WAIT", "LONG", "SHORT", "CLOSE", "PROTECT", "TRAIL")
ADVANCED_POSITION_NAMES = (
    "position_net_r", "position_best_mfe_r", "position_stop_distance_r",
    "position_original_risk_pct", "position_trailing_active",
)
ADVANCED_OBS_NAMES = OBS_NAMES + INDICATOR_NAMES + ADVANCED_POSITION_NAMES
ADVANCED_MODEL_SCHEMA = "trading_x_ppo_v3_mtf_sl"


def advanced_observation(candles: list[dict], bid: float, ask: float,
                         position: dict | None = None) -> tuple[list[float], float]:
    """M5, M15 and H1 closed bars + actual position feedback, no lookahead."""
    if len(candles) < ADVANCED_HISTORY_BARS:
        raise ValueError("advanced_candles_required")
    base, atr = observation(candles[-40:], bid, ask, position)
    indicators = multi_timeframe_features(candles[-ADVANCED_HISTORY_BARS:])
    metrics = [0.0] * len(ADVANCED_POSITION_NAMES)
    if position:
        sign = 1 if position["direction"] == "long" else -1
        entry = float(position["entry_price"])
        exit_px = bid if sign > 0 else ask
        qty = float(position.get("quantity") or 0)
        margin = float(position.get("margin_usdc") or 0)
        initial_stop = float(position.get("initial_stop_price") or 0)
        stop = float(position.get("stop_price") or initial_stop)
        risk = abs(entry-initial_stop)
        if qty > 0 and margin > 0 and risk > 0:
            fee_in = float(position.get("entry_fee_usdc") or 0)
            fee_rate = fee_in / max(qty*entry, 1e-9)
            fee_out = exit_px*qty*fee_rate
            gross = sign*(exit_px-entry)*qty
            metrics = [
                (gross-fee_in-fee_out)/max(qty*risk, 1e-9),
                float(position.get("audit_mfe_usdc") or 0)/max(qty*risk, 1e-9),
                sign*(exit_px-stop)/risk,
                risk/entry*100,
                float(bool(position.get("ai_trailing_active"))),
            ]
    metrics = [max(-3.0,min(3.0,float(v))) for v in metrics]
    values = base + indicators + metrics
    if len(values) != len(ADVANCED_OBS_NAMES) or not all(map(math.isfinite, values)):
        raise ValueError("invalid_advanced_observation")
    return values, atr
