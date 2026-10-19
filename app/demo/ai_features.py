"""Pure, dependency-free research and runtime feature contract.

Importing this module requires NO MongoDB, Telegram token, or broker credentials.
"""
from __future__ import annotations
from collections import defaultdict, deque
from threading import RLock
import math
import os
import time

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

