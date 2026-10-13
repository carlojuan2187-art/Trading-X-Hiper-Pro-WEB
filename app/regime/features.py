from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

TF_5M = "5m"
BTC_REFERENCE_SYMBOL = "BTC"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default



def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))



def _window(values: List[float], size: int) -> List[float]:
    if size <= 0:
        return list(values)
    if len(values) <= size:
        return list(values)
    return list(values[-size:])



def _true_ranges(h: List[float], l: List[float], c: List[float]) -> List[float]:
    if not h or len(h) != len(l) or len(h) != len(c):
        return []
    out: List[float] = [max(float(h[0]) - float(l[0]), 0.0)]
    for i in range(1, len(c)):
        out.append(
            max(
                float(h[i]) - float(l[i]),
                abs(float(h[i]) - float(c[i - 1])),
                abs(float(l[i]) - float(c[i - 1])),
            )
        )
    return out



def _sum_abs_deltas(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    return sum(abs(float(values[i]) - float(values[i - 1])) for i in range(1, len(values)))



def compute_choppiness(h: List[float], l: List[float], c: List[float], period: int = 14) -> float:
    if len(c) < max(period + 1, 3):
        return 50.0
    hh = max(float(x) for x in h[-period:])
    ll = min(float(x) for x in l[-period:])
    price_range = max(hh - ll, 1e-12)
    tr_sum = sum(_true_ranges(h[-period:], l[-period:], c[-period:]))
    if tr_sum <= 0.0:
        return 50.0
    return _clamp(100.0 * math.log10(tr_sum / price_range) / math.log10(float(period)), 0.0, 100.0)



def compute_efficiency_ratio(c: List[float], lookback: int = 20) -> float:
    if len(c) < max(lookback + 1, 3):
        return 0.0
    window = c[-(lookback + 1):]
    net_move = abs(float(window[-1]) - float(window[0]))
    path = _sum_abs_deltas(window)
    if path <= 0.0:
        return 0.0
    return _clamp(net_move / path, 0.0, 1.0)



def compute_realized_vol(c: List[float], lookback: int = 20) -> float:
    if len(c) < max(lookback + 1, 4):
        return 0.0
    values = c[-(lookback + 1):]
    rets: List[float] = []
    for i in range(1, len(values)):
        prev = float(values[i - 1])
        now = float(values[i])
        if prev > 0.0 and now > 0.0:
            rets.append(math.log(now / prev))
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / len(rets)
    return max(0.0, math.sqrt(var))



def compute_wick_instability(o: List[float], h: List[float], l: List[float], c: List[float], lookback: int = 8) -> float:
    if len(c) < max(lookback, 3):
        return 0.0
    values: List[float] = []
    for oo, hh, ll, cc in zip(_window(o, lookback), _window(h, lookback), _window(l, lookback), _window(c, lookback)):
        rng = max(float(hh) - float(ll), 1e-12)
        body = abs(float(cc) - float(oo))
        wick_share = _clamp((rng - body) / rng, 0.0, 1.0)
        values.append(wick_share)
    return sum(values) / len(values) if values else 0.0



def compute_body_quality(o: List[float], h: List[float], l: List[float], c: List[float], lookback: int = 8) -> float:
    if len(c) < max(lookback, 3):
        return 0.0
    values: List[float] = []
    for oo, hh, ll, cc in zip(_window(o, lookback), _window(h, lookback), _window(l, lookback), _window(c, lookback)):
        rng = max(float(hh) - float(ll), 1e-12)
        body = abs(float(cc) - float(oo))
        values.append(_clamp(body / rng, 0.0, 1.0))
    return sum(values) / len(values) if values else 0.0



def compute_breakout_failure_ratio(h: List[float], l: List[float], c: List[float], lookback: int = 20, sample_size: int = 12) -> float:
    total = 0
    failures = 0
    n = len(c)
    if n < max(lookback + 3, sample_size + 3):
        return 0.0
    start = max(lookback, n - sample_size - 1)
    for i in range(start, n - 1):
        prior_high = max(float(x) for x in h[i - lookback:i])
        prior_low = min(float(x) for x in l[i - lookback:i])
        close_now = float(c[i])
        close_next = float(c[i + 1])
        if close_now > prior_high:
            total += 1
            if close_next < prior_high:
                failures += 1
        elif close_now < prior_low:
            total += 1
            if close_next > prior_low:
                failures += 1
    if total <= 0:
        return 0.0
    return _clamp(failures / total, 0.0, 1.0)



def compute_ema_stack_metrics(close: float, ema20: List[float], ema50: List[float], ema200: List[float]) -> Dict[str, Any]:
    e20 = _safe_float(ema20[-1] if ema20 else 0.0)
    e50 = _safe_float(ema50[-1] if ema50 else 0.0)
    e200 = _safe_float(ema200[-1] if ema200 else 0.0)
    slope20 = _safe_float(e20 - (ema20[-6] if len(ema20) >= 6 else e20))
    slope50 = _safe_float(e50 - (ema50[-6] if len(ema50) >= 6 else e50))

    bullish = close > e20 > e50 > e200 and slope20 > 0.0 and slope50 >= 0.0
    bearish = close < e20 < e50 < e200 and slope20 < 0.0 and slope50 <= 0.0

    distance_20 = (close - e20)
    distance_50 = (close - e50)
    distance_200 = (close - e200)

    alignment = 0.0
    if bullish or bearish:
        alignment = 1.0
    else:
        score = 0.0
        if close > e20:
            score += 0.25
        if e20 > e50:
            score += 0.25
        if e50 > e200:
            score += 0.25
        if slope20 > 0.0:
            score += 0.125
        if slope50 > 0.0:
            score += 0.125
        alignment = _clamp(score, 0.0, 1.0)

    bias = "neutral"
    if bullish:
        bias = "long"
    elif bearish:
        bias = "short"

    return {
        "ema20": e20,
        "ema50": e50,
        "ema200": e200,
        "ema20_slope": slope20,
        "ema50_slope": slope50,
        "ema_stack_alignment": alignment,
        "trend_bias": bias,
        "distance_to_ema20": distance_20,
        "distance_to_ema50": distance_50,
        "distance_to_ema200": distance_200,
        "stack_bullish": bullish,
        "stack_bearish": bearish,
    }



def compute_vwap_distance(candles: List[Dict[str, Any]], close: float, atr_value: float, lookback: int = 48) -> Dict[str, float]:
    if not candles:
        return {"rolling_vwap": 0.0, "distance_to_vwap": 0.0, "distance_to_vwap_atr": 0.0}
    window = candles[-lookback:] if len(candles) > lookback else candles
    notional = 0.0
    volume = 0.0
    for item in window:
        price = (_safe_float(item.get("h")) + _safe_float(item.get("l")) + _safe_float(item.get("c"))) / 3.0
        vol = max(_safe_float(item.get("v")), 0.0)
        notional += price * vol
        volume += vol
    if volume <= 0.0:
        vwap = close
    else:
        vwap = notional / volume
    distance = close - vwap
    return {
        "rolling_vwap": vwap,
        "distance_to_vwap": distance,
        "distance_to_vwap_atr": abs(distance) / max(atr_value, 1e-12),
    }



def compute_recent_move(c: List[float], bars: int = 3) -> float:
    if len(c) < max(bars + 1, 2):
        return 0.0
    prev = float(c[-bars - 1])
    now = float(c[-1])
    if prev == 0.0:
        return 0.0
    return (now - prev) / prev



def build_regime_features(
    symbol: str,
    market_context: Optional[Dict[str, Any]] = None,
    btc_context: Optional[Dict[str, Any]] = None,
    interval: str = TF_5M,
) -> Dict[str, Any]:
    if market_context is None or btc_context is None:
        from app.market_context import build_market_context

    ctx = market_context or build_market_context(symbol, interval=interval)
    btc_ctx = btc_context or build_market_context(BTC_REFERENCE_SYMBOL, interval=interval)

    tf = (ctx.get("timeframes") or {}).get(interval) or {}
    btc_tf = (btc_ctx.get("timeframes") or {}).get(interval) or {}

    candles = tf.get("candles") or []
    o = list(tf.get("o") or [])
    h = list(tf.get("h") or [])
    l = list(tf.get("l") or [])
    c = list(tf.get("c") or [])

    close = _safe_float(tf.get("close"), _safe_float(c[-1] if c else 0.0))
    atr_value = _safe_float(tf.get("atr"))
    atr_pct = _safe_float(tf.get("atr_pct"))
    adx = _safe_float(tf.get("adx"))

    ema_metrics = compute_ema_stack_metrics(
        close=close,
        ema20=list(tf.get("ema20") or []),
        ema50=list(tf.get("ema50") or []),
        ema200=list(tf.get("ema200") or []),
    )
    vwap_metrics = compute_vwap_distance(candles=candles, close=close, atr_value=atr_value)

    chop = compute_choppiness(h, l, c, period=14)
    efficiency_ratio = compute_efficiency_ratio(c, lookback=20)
    realized_vol = compute_realized_vol(c, lookback=20)
    wick_instability = compute_wick_instability(o, h, l, c, lookback=8)
    body_quality = compute_body_quality(o, h, l, c, lookback=8)
    breakout_failure_ratio = compute_breakout_failure_ratio(h, l, c, lookback=20, sample_size=12)
    recent_move_3 = compute_recent_move(c, bars=3)
    recent_move_6 = compute_recent_move(c, bars=6)

    btc_c = list(btc_tf.get("c") or [])
    btc_recent_move_3 = compute_recent_move(btc_c, bars=3)
    btc_recent_move_6 = compute_recent_move(btc_c, bars=6)
    btc_atr_pct = _safe_float(btc_tf.get("atr_pct"))
    btc_realized_vol = compute_realized_vol(btc_c, lookback=20)
    btc_shock_ratio = 0.0
    if btc_atr_pct > 0.0:
        btc_shock_ratio = abs(btc_recent_move_3) / btc_atr_pct

    return {
        "symbol": symbol,
        "interval": interval,
        "status": str(ctx.get("status") or tf.get("status") or "UNKNOWN"),
        "context_ok": bool(tf.get("status") == "OK" and not tf.get("stale")),
        "context_status": str(tf.get("status") or "UNKNOWN"),
        "context_stale": bool(tf.get("stale", True)),
        "close": close,
        "atr": atr_value,
        "atr_pct": atr_pct,
        "adx": adx,
        "choppiness": chop,
        "efficiency_ratio": efficiency_ratio,
        "realized_vol": realized_vol,
        "wick_instability": wick_instability,
        "body_quality": body_quality,
        "breakout_failure_ratio": breakout_failure_ratio,
        "recent_move_3": recent_move_3,
        "recent_move_6": recent_move_6,
        "rolling_vwap": vwap_metrics["rolling_vwap"],
        "distance_to_vwap": vwap_metrics["distance_to_vwap"],
        "distance_to_vwap_atr": vwap_metrics["distance_to_vwap_atr"],
        "btc_symbol": BTC_REFERENCE_SYMBOL,
        "btc_context_ok": bool(btc_tf.get("status") == "OK" and not btc_tf.get("stale")),
        "btc_context_status": str(btc_tf.get("status") or "UNKNOWN"),
        "btc_atr_pct": btc_atr_pct,
        "btc_realized_vol": btc_realized_vol,
        "btc_recent_move_3": btc_recent_move_3,
        "btc_recent_move_6": btc_recent_move_6,
        "btc_shock_ratio": btc_shock_ratio,
        **ema_metrics,
    }


__all__ = [
    "BTC_REFERENCE_SYMBOL",
    "build_regime_features",
    "compute_body_quality",
    "compute_breakout_failure_ratio",
    "compute_choppiness",
    "compute_efficiency_ratio",
    "compute_ema_stack_metrics",
    "compute_recent_move",
    "compute_realized_vol",
    "compute_vwap_distance",
    "compute_wick_instability",
]
