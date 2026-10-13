from __future__ import annotations

import os
import time
from app.trading_quality import absolute_risk_reward, meets_min_rr
from app.settings_utils import env_float
from typing import Any, Dict, List, Optional, Tuple

from app.market_context import build_market_context
from app.regime.features import compute_choppiness, compute_efficiency_ratio, compute_vwap_distance
from app.strategies.base import BaseStrategy
from app.strategies.breakout_reset import (
    ADX_PERIOD,
    ALLOWED_SYMBOLS,
    ATR_PERIOD,
    BLOCKED_MEME_KEYWORDS,
    EMA_FAST,
    EMA_MID,
    EMA_SLOW,
    LOOKBACK_5M,
    MIN_CANDLES_REQUIRED,
    MIN_NONZERO_VOLUME_RATIO,
    TF_5M,
)

STRATEGY_ID = "range_mean_reversion"
STRATEGY_VERSION = "v1"
STRATEGY_MODEL = "range_mean_reversion_vwap_5m_v1"

RANGE_LOOKBACK = 42
VWAP_LOOKBACK = 36
RANGE_MIN_WIDTH_ATR = 2.6
RANGE_MAX_WIDTH_ATR = 11.5
ATR_PCT_MIN = 0.0010
ATR_PCT_MAX = 0.0200
ADX_MAX = 18.8
CHOP_MIN = 56.0
EFFICIENCY_MAX = 0.31
EDGE_TOL_ATR = 0.42
RECLAIM_ATR = 0.14
WICK_MIN_RATIO = 0.28
TRIGGER_MIN_RVOL = 0.82
TRIGGER_MIN_BODY_RATIO = 0.18
TRIGGER_CLOSE_POS_LONG_MIN = 0.52
TRIGGER_CLOSE_POS_SHORT_MAX = 0.48
VWAP_DIST_MIN_ATR = 0.55
VWAP_DIST_MAX_ATR = 2.20
SL_BUFFER_ATR = 0.16
SL_MIN_PCT = 0.0049
SL_MAX_PCT = 0.0138
MIN_RR = env_float("RANGE_MIN_RR", 1.18)
TARGET_BUFFER = env_float("RANGE_TARGET_BUFFER", 0.90, minimum=0.5, maximum=1.0)
ENTRY_RR_RESERVE = env_float("ENTRY_RR_RESERVE", 0.05, maximum=0.5)
LOG_SIGNAL_DIAGNOSTICS = os.getenv("LOG_RANGE_SIGNAL_DIAGNOSTICS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _log(msg: str) -> None:
    try:
        print(f"[STRATEGY-RANGE {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {msg}")
    except Exception:
        pass


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _median(values: List[float]) -> float:
    vals = sorted(float(x) for x in values if x is not None)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def _base_coin(symbol: str) -> str:
    symbol = str(symbol or "").upper().strip()
    if "-" in symbol:
        return symbol.split("-", 1)[0]
    return symbol.replace("USDC", "").replace("USD", "").replace("USDT", "")


def _is_probable_meme_symbol(symbol: str) -> bool:
    base = _base_coin(symbol)
    return any(token in base for token in BLOCKED_MEME_KEYWORDS)


def _relative_volume(volumes: List[float], idx: int, lookback: int = 20) -> float:
    if idx < 0 or idx >= len(volumes):
        return 1.0
    start = max(0, idx - max(lookback, 8))
    window = [max(float(x), 0.0) for x in volumes[start:idx]]
    nonzero = [x for x in window if x > 0.0]
    baseline = _median(nonzero or window)
    if baseline <= 0.0:
        return 1.0
    return max(0.0, float(volumes[idx]) / baseline)


def _body_ratio(o: float, h: float, l: float, c: float) -> float:
    rng = max(h - l, 1e-12)
    return abs(c - o) / rng


def _close_position_in_range(o: float, h: float, l: float, c: float) -> float:
    rng = max(h - l, 1e-12)
    return _clamp((c - l) / rng, 0.0, 1.0)


def _lower_wick_ratio(o: float, h: float, l: float, c: float) -> float:
    rng = max(h - l, 1e-12)
    return _clamp((min(o, c) - l) / rng, 0.0, 1.0)


def _upper_wick_ratio(o: float, h: float, l: float, c: float) -> float:
    rng = max(h - l, 1e-12)
    return _clamp((h - max(o, c)) / rng, 0.0, 1.0)


def _validate_symbol_quality(coin: str, candles: List[dict]) -> Tuple[bool, str, Dict[str, Any]]:
    if ALLOWED_SYMBOLS and _base_coin(coin) not in ALLOWED_SYMBOLS:
        return False, "SYMBOL_NOT_ALLOWED", {"coin": coin}
    if _is_probable_meme_symbol(coin):
        return False, "MEME_SYMBOL_BLOCKED", {"coin": coin, "base": _base_coin(coin)}
    if not candles or len(candles) < MIN_CANDLES_REQUIRED:
        return False, "NO_CANDLES", {"bars": len(candles) if candles else 0, "min_bars": MIN_CANDLES_REQUIRED}
    valid = [x for x in candles if float(x.get("c", 0.0) or 0.0) > 0.0 and float(x.get("h", 0.0) or 0.0) >= float(x.get("l", 0.0) or 0.0)]
    if len(valid) < MIN_CANDLES_REQUIRED:
        return False, "BAD_CANDLES_PARSE", {"valid_bars": len(valid), "min_bars": MIN_CANDLES_REQUIRED}
    recent = valid[-MIN_CANDLES_REQUIRED:]
    nonzero_vol_ratio = sum(1 for x in recent if float(x.get("v", 0.0) or 0.0) > 0.0) / max(len(recent), 1)
    if nonzero_vol_ratio < MIN_NONZERO_VOLUME_RATIO:
        return False, "LOW_ACTIVITY_SYMBOL", {"nonzero_vol_ratio": round(nonzero_vol_ratio, 4)}
    return True, "OK", {"base": _base_coin(coin), "bars": len(valid), "nonzero_vol_ratio": round(nonzero_vol_ratio, 4)}


def _compute_management_params(strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
    atr_pct = float(atr_pct or 0.0)
    score = float(score or 0.0)
    strength = float(strength or 0.0)

    if score >= 88.0:
        bucket = "range_strong"
        break_even_activation = 0.0044
        partial_tp = 0.0065
        partial_frac = 0.34
        tp_act = 0.0094
        retrace = 0.0031
        force = 0.0067
    elif score >= 81.0:
        bucket = "range_base"
        break_even_activation = 0.0040
        partial_tp = 0.0060
        partial_frac = 0.38
        tp_act = 0.0087
        retrace = 0.0033
        force = 0.0062
    else:
        bucket = "range_weak"
        break_even_activation = 0.0036
        partial_tp = 0.0055
        partial_frac = 0.42
        tp_act = 0.0080
        retrace = 0.0035
        force = 0.0057

    vol_add = _clamp((atr_pct - 0.0060) * 0.12, -0.0005, 0.0010)
    break_even_activation = _clamp(break_even_activation + (vol_add * 0.25), 0.0032, 0.0060)
    partial_tp = _clamp(partial_tp + (vol_add * 0.55), break_even_activation + 0.0013, 0.0088)
    tp_act = _clamp(tp_act + vol_add, partial_tp + 0.0012, 0.0120)
    retrace = _clamp(retrace + (vol_add * 0.50), 0.0028, 0.0042)
    force = _clamp(force + (vol_add * 0.55), partial_tp, tp_act - 0.0008)
    break_even_offset = _clamp(max(atr_pct * 0.06, 0.00045), 0.00045, 0.00110)
    force_strength = _clamp(max(0.11, strength * 0.52), 0.11, 0.80)

    return {
        "bucket": bucket,
        "tp_activation_price": round(tp_act, 6),
        "trail_retrace_price": round(retrace, 6),
        "force_min_profit_price": round(force, 6),
        "force_min_strength": round(force_strength, 4),
        "partial_tp_activation_price": round(partial_tp, 6),
        "partial_tp_close_fraction": round(partial_frac, 4),
        "break_even_activation_price": round(break_even_activation, 6),
        "break_even_offset_price": round(break_even_offset, 6),
        "vol_regime": "volatile" if atr_pct >= 0.0095 else ("active" if atr_pct >= 0.0050 else "normal"),
    }


def get_trade_management_params(strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
    return _compute_management_params(strength, score, atr_pct)


def _score_candidate(
    *,
    adx: float,
    chop: float,
    efficiency: float,
    edge_quality: float,
    wick_ratio: float,
    trigger_rvol: float,
    body_ratio: float,
    vwap_distance_atr: float,
    rr_estimate: float,
    reclaim_quality: float,
) -> float:
    adx_q = _clamp((ADX_MAX - adx) / max(ADX_MAX, 1e-12), 0.0, 1.0)
    chop_q = _clamp((chop - CHOP_MIN) / 20.0, 0.0, 1.0)
    eff_q = _clamp((EFFICIENCY_MAX - efficiency) / max(EFFICIENCY_MAX, 1e-12), 0.0, 1.0)
    edge_q = _clamp(edge_quality, 0.0, 1.0)
    wick_q = _clamp((wick_ratio - WICK_MIN_RATIO) / 0.40, 0.0, 1.0)
    rvol_q = _clamp((trigger_rvol - 0.80) / 0.80, 0.0, 1.0)
    body_q = _clamp((body_ratio - TRIGGER_MIN_BODY_RATIO) / 0.35, 0.0, 1.0)
    vwap_q = _clamp((vwap_distance_atr - VWAP_DIST_MIN_ATR) / max(VWAP_DIST_MAX_ATR - VWAP_DIST_MIN_ATR, 1e-12), 0.0, 1.0)
    rr_q = _clamp((rr_estimate - MIN_RR) / 1.20, 0.0, 1.0)
    reclaim_q = _clamp(reclaim_quality, 0.0, 1.0)
    quality = _clamp(
        (0.10 * adx_q)
        + (0.12 * chop_q)
        + (0.10 * eff_q)
        + (0.13 * edge_q)
        + (0.12 * wick_q)
        + (0.10 * rvol_q)
        + (0.09 * body_q)
        + (0.10 * vwap_q)
        + (0.10 * rr_q)
        + (0.04 * reclaim_q),
        0.0,
        1.0,
    )
    return round(min(100.0, 67.0 + (33.0 * quality)), 2)


def _detect_long_reversion(
    o: List[float],
    h: List[float],
    l: List[float],
    c: List[float],
    v: List[float],
    ema20: List[float],
    ema50: List[float],
    ema200: List[float],
    atr_value: float,
    adx_value: float,
    chop: float,
    efficiency: float,
    rolling_vwap: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    if len(c) < max(RANGE_LOOKBACK + 4, 90):
        return False, "NOT_ENOUGH_BARS", {}
    idx = len(c) - 1
    left_highs = h[-(RANGE_LOOKBACK + 1):-1]
    left_lows = l[-(RANGE_LOOKBACK + 1):-1]
    if not left_highs or not left_lows:
        return False, "NO_RANGE_WINDOW", {}

    range_low = float(min(left_lows))
    range_high = float(max(left_highs))
    range_width = max(range_high - range_low, 1e-12)
    range_width_atr = range_width / max(atr_value, 1e-12)
    if range_width_atr < RANGE_MIN_WIDTH_ATR or range_width_atr > RANGE_MAX_WIDTH_ATR:
        return False, "BAD_RANGE_WIDTH", {"range_width_atr": round(range_width_atr, 4)}

    close_now = float(c[idx])
    low_now = float(l[idx])
    edge_tol_abs = atr_value * EDGE_TOL_ATR
    reclaim_abs = atr_value * RECLAIM_ATR
    edge_distance_atr = (close_now - range_low) / max(atr_value, 1e-12)
    if low_now > range_low + edge_tol_abs:
        return False, "LOWER_EDGE_NOT_TAGGED", {"edge_distance_atr": round(edge_distance_atr, 4)}
    if close_now < range_low + reclaim_abs:
        return False, "NO_LOWER_RECLAIM", {"edge_distance_atr": round(edge_distance_atr, 4)}

    trigger_rvol = _relative_volume(v, idx, 24)
    body_ratio = _body_ratio(o[idx], h[idx], l[idx], c[idx])
    close_pos = _close_position_in_range(o[idx], h[idx], l[idx], c[idx])
    lower_wick_ratio = _lower_wick_ratio(o[idx], h[idx], l[idx], c[idx])
    if trigger_rvol < TRIGGER_MIN_RVOL:
        return False, "LOW_TRIGGER_RVOL", {"trigger_rvol": round(trigger_rvol, 4)}
    if body_ratio < TRIGGER_MIN_BODY_RATIO:
        return False, "WEAK_TRIGGER_BODY", {"body_ratio": round(body_ratio, 4)}
    if close_pos < TRIGGER_CLOSE_POS_LONG_MIN:
        return False, "WEAK_LOWER_CLOSE", {"close_pos": round(close_pos, 4)}
    if lower_wick_ratio < WICK_MIN_RATIO:
        return False, "LOWER_WICK_TOO_SMALL", {"lower_wick_ratio": round(lower_wick_ratio, 4)}

    distance_to_vwap = float(rolling_vwap) - close_now
    distance_to_vwap_atr = distance_to_vwap / max(atr_value, 1e-12)
    if distance_to_vwap_atr < VWAP_DIST_MIN_ATR or distance_to_vwap_atr > VWAP_DIST_MAX_ATR:
        return False, "VWAP_DISTANCE_BAD", {"distance_to_vwap_atr": round(distance_to_vwap_atr, 4)}

    stop_price = min(low_now, range_low) - (atr_value * SL_BUFFER_ATR)
    risk_abs = max(close_now - stop_price, 1e-12)
    reward_abs = max(float(rolling_vwap) - close_now, 0.0)
    rr_estimate = reward_abs / risk_abs if risk_abs > 0 else 0.0
    if rr_estimate < MIN_RR:
        return False, "RR_TOO_LOW", {"rr_estimate": round(rr_estimate, 4)}

    e20 = float(ema20[idx]) if ema20 else close_now
    e50 = float(ema50[idx]) if ema50 else close_now
    e200 = float(ema200[idx]) if ema200 else close_now
    if close_now < e200 - (atr_value * 1.60):
        return False, "TOO_FAR_BELOW_EMA200", {"ema200_distance_atr": round((e200 - close_now) / max(atr_value, 1e-12), 4)}

    reclaim_quality = _clamp((close_now - (range_low + reclaim_abs)) / max(atr_value * 0.80, 1e-12), 0.0, 1.0)
    edge_quality = _clamp((edge_tol_abs - max(low_now - range_low, 0.0)) / max(edge_tol_abs, 1e-12), 0.0, 1.0)
    score = _score_candidate(
        adx=adx_value,
        chop=chop,
        efficiency=efficiency,
        edge_quality=edge_quality,
        wick_ratio=lower_wick_ratio,
        trigger_rvol=trigger_rvol,
        body_ratio=body_ratio,
        vwap_distance_atr=distance_to_vwap_atr,
        rr_estimate=rr_estimate,
        reclaim_quality=reclaim_quality,
    )
    diag = {
        "range_low": round(range_low, 8),
        "range_high": round(range_high, 8),
        "range_width_atr": round(range_width_atr, 4),
        "edge_distance_atr": round(edge_distance_atr, 4),
        "trigger_rvol": round(trigger_rvol, 4),
        "trigger_body_ratio": round(body_ratio, 4),
        "trigger_close_pos": round(close_pos, 4),
        "wick_ratio": round(lower_wick_ratio, 4),
        "rolling_vwap": round(float(rolling_vwap), 8),
        "distance_to_vwap_atr": round(distance_to_vwap_atr, 4),
        "rr_estimate": round(rr_estimate, 4),
        "stop_price": round(stop_price, 8),
        "ema20": round(e20, 8),
        "ema50": round(e50, 8),
        "ema200": round(e200, 8),
        "direction": "long",
    }
    return True, "OK", {"score": score, "diag": diag, "stop_price": stop_price}


def _detect_short_reversion(
    o: List[float],
    h: List[float],
    l: List[float],
    c: List[float],
    v: List[float],
    ema20: List[float],
    ema50: List[float],
    ema200: List[float],
    atr_value: float,
    adx_value: float,
    chop: float,
    efficiency: float,
    rolling_vwap: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    if len(c) < max(RANGE_LOOKBACK + 4, 90):
        return False, "NOT_ENOUGH_BARS", {}
    idx = len(c) - 1
    left_highs = h[-(RANGE_LOOKBACK + 1):-1]
    left_lows = l[-(RANGE_LOOKBACK + 1):-1]
    if not left_highs or not left_lows:
        return False, "NO_RANGE_WINDOW", {}

    range_low = float(min(left_lows))
    range_high = float(max(left_highs))
    range_width = max(range_high - range_low, 1e-12)
    range_width_atr = range_width / max(atr_value, 1e-12)
    if range_width_atr < RANGE_MIN_WIDTH_ATR or range_width_atr > RANGE_MAX_WIDTH_ATR:
        return False, "BAD_RANGE_WIDTH", {"range_width_atr": round(range_width_atr, 4)}

    close_now = float(c[idx])
    high_now = float(h[idx])
    edge_tol_abs = atr_value * EDGE_TOL_ATR
    reclaim_abs = atr_value * RECLAIM_ATR
    edge_distance_atr = (range_high - close_now) / max(atr_value, 1e-12)
    if high_now < range_high - edge_tol_abs:
        return False, "UPPER_EDGE_NOT_TAGGED", {"edge_distance_atr": round(edge_distance_atr, 4)}
    if close_now > range_high - reclaim_abs:
        return False, "NO_UPPER_RECLAIM", {"edge_distance_atr": round(edge_distance_atr, 4)}

    trigger_rvol = _relative_volume(v, idx, 24)
    body_ratio = _body_ratio(o[idx], h[idx], l[idx], c[idx])
    close_pos = _close_position_in_range(o[idx], h[idx], l[idx], c[idx])
    upper_wick_ratio = _upper_wick_ratio(o[idx], h[idx], l[idx], c[idx])
    if trigger_rvol < TRIGGER_MIN_RVOL:
        return False, "LOW_TRIGGER_RVOL", {"trigger_rvol": round(trigger_rvol, 4)}
    if body_ratio < TRIGGER_MIN_BODY_RATIO:
        return False, "WEAK_TRIGGER_BODY", {"body_ratio": round(body_ratio, 4)}
    if close_pos > TRIGGER_CLOSE_POS_SHORT_MAX:
        return False, "WEAK_UPPER_CLOSE", {"close_pos": round(close_pos, 4)}
    if upper_wick_ratio < WICK_MIN_RATIO:
        return False, "UPPER_WICK_TOO_SMALL", {"upper_wick_ratio": round(upper_wick_ratio, 4)}

    distance_to_vwap = close_now - float(rolling_vwap)
    distance_to_vwap_atr = distance_to_vwap / max(atr_value, 1e-12)
    if distance_to_vwap_atr < VWAP_DIST_MIN_ATR or distance_to_vwap_atr > VWAP_DIST_MAX_ATR:
        return False, "VWAP_DISTANCE_BAD", {"distance_to_vwap_atr": round(distance_to_vwap_atr, 4)}

    stop_price = max(high_now, range_high) + (atr_value * SL_BUFFER_ATR)
    risk_abs = max(stop_price - close_now, 1e-12)
    reward_abs = max(close_now - float(rolling_vwap), 0.0)
    rr_estimate = reward_abs / risk_abs if risk_abs > 0 else 0.0
    if rr_estimate < MIN_RR:
        return False, "RR_TOO_LOW", {"rr_estimate": round(rr_estimate, 4)}

    e20 = float(ema20[idx]) if ema20 else close_now
    e50 = float(ema50[idx]) if ema50 else close_now
    e200 = float(ema200[idx]) if ema200 else close_now
    if close_now > e200 + (atr_value * 1.60):
        return False, "TOO_FAR_ABOVE_EMA200", {"ema200_distance_atr": round((close_now - e200) / max(atr_value, 1e-12), 4)}

    reclaim_quality = _clamp(((range_high - reclaim_abs) - close_now) / max(atr_value * 0.80, 1e-12), 0.0, 1.0)
    edge_quality = _clamp((edge_tol_abs - max(range_high - high_now, 0.0)) / max(edge_tol_abs, 1e-12), 0.0, 1.0)
    score = _score_candidate(
        adx=adx_value,
        chop=chop,
        efficiency=efficiency,
        edge_quality=edge_quality,
        wick_ratio=upper_wick_ratio,
        trigger_rvol=trigger_rvol,
        body_ratio=body_ratio,
        vwap_distance_atr=distance_to_vwap_atr,
        rr_estimate=rr_estimate,
        reclaim_quality=reclaim_quality,
    )
    diag = {
        "range_low": round(range_low, 8),
        "range_high": round(range_high, 8),
        "range_width_atr": round(range_width_atr, 4),
        "edge_distance_atr": round(edge_distance_atr, 4),
        "trigger_rvol": round(trigger_rvol, 4),
        "trigger_body_ratio": round(body_ratio, 4),
        "trigger_close_pos": round(close_pos, 4),
        "wick_ratio": round(upper_wick_ratio, 4),
        "rolling_vwap": round(float(rolling_vwap), 8),
        "distance_to_vwap_atr": round(distance_to_vwap_atr, 4),
        "rr_estimate": round(rr_estimate, 4),
        "stop_price": round(stop_price, 8),
        "ema20": round(e20, 8),
        "ema50": round(e50, 8),
        "ema200": round(e200, 8),
        "direction": "short",
    }
    return True, "OK", {"score": score, "diag": diag, "stop_price": stop_price}


class RangeMeanReversionStrategy(BaseStrategy):
    strategy_id = STRATEGY_ID
    strategy_version = STRATEGY_VERSION
    strategy_model = STRATEGY_MODEL

    def get_trade_management_params(self, strength: float, score: float, atr_pct: float | None = None) -> Dict[str, Any]:
        return _compute_management_params(strength, score, atr_pct)

    def evaluate(self, symbol: str, market_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        ctx = market_context if isinstance(market_context, dict) else build_market_context(
            symbol=symbol,
            interval=TF_5M,
            limit=LOOKBACK_5M,
            ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
            adx_period=ADX_PERIOD,
            atr_period=ATR_PERIOD,
        )
        tf = (ctx.get("timeframes") or {}).get(TF_5M) or {}
        candles = list(tf.get("candles") or [])
        ok_symbol, symbol_reason, symbol_meta = _validate_symbol_quality(str(symbol or "").upper(), candles)
        if not ok_symbol:
            return {
                "signal": False,
                "reason": symbol_reason,
                "coin": symbol,
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_model": STRATEGY_MODEL,
                "meta": symbol_meta,
            }
        if str(tf.get("status") or "") != "OK":
            return {
                "signal": False,
                "reason": f"CTX_{tf.get('status', 'UNKNOWN')}",
                "coin": symbol,
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_model": STRATEGY_MODEL,
            }
        if bool(tf.get("stale")):
            return {
                "signal": False,
                "reason": "STALE_CANDLES",
                "coin": symbol,
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_model": STRATEGY_MODEL,
                "meta": {"age_s": round(float(tf.get('age_s') or 0.0), 2)},
            }

        o = list(tf.get("o") or [])
        h = list(tf.get("h") or [])
        l = list(tf.get("l") or [])
        c = list(tf.get("c") or [])
        v = list(tf.get("v") or [])
        ema20 = list(tf.get(f"ema{EMA_FAST}") or [])
        ema50 = list(tf.get(f"ema{EMA_MID}") or [])
        ema200 = list(tf.get(f"ema{EMA_SLOW}") or [])
        close = float(tf.get("close") or (c[-1] if c else 0.0) or 0.0)
        atr_value = float(tf.get("atr") or 0.0)
        atr_pct = float(tf.get("atr_pct") or 0.0)
        adx_value = float(tf.get("adx") or 0.0)

        if len(c) < max(RANGE_LOOKBACK + 6, 90):
            return {"signal": False, "reason": "NOT_ENOUGH_BARS", "coin": symbol, "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "strategy_model": STRATEGY_MODEL}
        if close <= 0.0 or atr_value <= 0.0:
            return {"signal": False, "reason": "BAD_PRICE_CONTEXT", "coin": symbol, "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "strategy_model": STRATEGY_MODEL}
        if atr_pct < ATR_PCT_MIN or atr_pct > ATR_PCT_MAX:
            return {"signal": False, "reason": "ATR_PCT_FILTER", "coin": symbol, "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "strategy_model": STRATEGY_MODEL, "meta": {"atr_pct": round(atr_pct, 6)}}

        chop = float(compute_choppiness(h, l, c, period=14) or 0.0)
        efficiency = float(compute_efficiency_ratio(c, lookback=20) or 0.0)
        if adx_value > ADX_MAX:
            return {"signal": False, "reason": "ADX_TOO_HIGH", "coin": symbol, "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "strategy_model": STRATEGY_MODEL, "meta": {"adx": round(adx_value, 4)}}
        if chop < CHOP_MIN:
            return {"signal": False, "reason": "CHOP_TOO_LOW", "coin": symbol, "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "strategy_model": STRATEGY_MODEL, "meta": {"choppiness": round(chop, 4)}}
        if efficiency > EFFICIENCY_MAX:
            return {"signal": False, "reason": "EFFICIENCY_TOO_HIGH", "coin": symbol, "strategy_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION, "strategy_model": STRATEGY_MODEL, "meta": {"efficiency_ratio": round(efficiency, 4)}}

        vwap_payload = compute_vwap_distance(candles, close, atr_value, lookback=VWAP_LOOKBACK)
        rolling_vwap = float(vwap_payload.get("rolling_vwap") or close)

        long_ok, long_reason, long_payload = _detect_long_reversion(
            o, h, l, c, v, ema20, ema50, ema200, atr_value, adx_value, chop, efficiency, rolling_vwap
        )
        short_ok, short_reason, short_payload = _detect_short_reversion(
            o, h, l, c, v, ema20, ema50, ema200, atr_value, adx_value, chop, efficiency, rolling_vwap
        )

        selected_direction: Optional[str] = None
        selected_reason = "NO_SETUP"
        selected_payload: Dict[str, Any] = {}
        if long_ok and short_ok:
            if float(long_payload.get("score") or 0.0) >= float(short_payload.get("score") or 0.0):
                selected_direction, selected_reason, selected_payload = "long", long_reason, long_payload
            else:
                selected_direction, selected_reason, selected_payload = "short", short_reason, short_payload
        elif long_ok:
            selected_direction, selected_reason, selected_payload = "long", long_reason, long_payload
        elif short_ok:
            selected_direction, selected_reason, selected_payload = "short", short_reason, short_payload
        else:
            return {
                "signal": False,
                "reason": f"LONG={long_reason}|SHORT={short_reason}",
                "coin": symbol,
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_model": STRATEGY_MODEL,
                "meta": {
                    "atr_pct": round(atr_pct, 6),
                    "adx": round(adx_value, 4),
                    "choppiness": round(chop, 4),
                    "efficiency_ratio": round(efficiency, 4),
                    "rolling_vwap": round(rolling_vwap, 8),
                },
            }

        diag = dict(selected_payload.get("diag") or {})
        score = float(selected_payload.get("score") or 0.0)
        direction = str(selected_direction or "")
        stop_price = float(selected_payload.get("stop_price") or 0.0)
        risk_pct = abs(close - stop_price) / close if close > 0 and stop_price > 0 else 0.0
        sl_price_pct = max(risk_pct, SL_MIN_PCT)
        if sl_price_pct > SL_MAX_PCT:
            return {'signal': False, 'reason': 'STRUCTURAL_SL_TOO_WIDE', 'coin': symbol}
        stop_price = close * (1 - sl_price_pct if direction == 'long' else 1 + sl_price_pct)
        target_price = close + (rolling_vwap - close) * TARGET_BUFFER
        plan = absolute_risk_reward(direction=direction, entry_price=close,
                                    stop_price=stop_price, target_price=target_price)
        if not plan.get('ok') or not meets_min_rr(float(plan.get('rr', 0)), MIN_RR + ENTRY_RR_RESERVE):
            return {'signal': False, 'reason': 'STRUCTURAL_RR_TOO_LOW', 'coin': symbol,
                    'diag': plan}
        strength = _clamp(
            0.16
            + (max(score - 76.0, 0.0) / 58.0)
            + (_clamp((chop - CHOP_MIN) / 18.0, 0.0, 1.0) * 0.10)
            + (_clamp((VWAP_DIST_MAX_ATR - abs(diag.get("distance_to_vwap_atr", 0.0))) / VWAP_DIST_MAX_ATR, 0.0, 1.0) * 0.06),
            0.18,
            0.95,
        )
        mgmt = _compute_management_params(strength=strength, score=score, atr_pct=atr_pct)
        mgmt.update(tp_activation_price=plan['reward_pct'], tp_rr_multiple=plan['rr'],
                    trail_retrace_price=0.0, break_even_activation_price=0.0,
                    break_even_offset_price=0.0, partial_tp_close_fraction=0.0,
                    partial_tp_activation_price=999999.0, force_min_profit_price=999999.0)

        if LOG_SIGNAL_DIAGNOSTICS:
            _log(
                f"{symbol} {direction.upper()} score={score:.2f} strength={strength:.4f} "
                f"atr_pct={atr_pct:.4f} adx={adx_value:.2f} chop={chop:.2f} eff={efficiency:.2f} "
                f"vwap_dist_atr={diag.get('distance_to_vwap_atr')} rr={diag.get('rr_estimate')}"
            )

        return {
            "signal": True,
            "coin": symbol,
            "direction": direction,
            "strength": round(strength, 4),
            "score": round(score, 2),
            "sl_price_pct": sl_price_pct,
            "signal_reference_price": close,
            "close_5": close,
            "stop_price": stop_price,
            "target_price": target_price,
            "tp_rr_multiple": plan['rr'],
            "last_candle_t_5m": int(tf.get('last_close_t') or tf.get('last_t') or 0),
            "atr_pct": round(atr_pct, 6),
            "strategy_id": STRATEGY_ID,
            "strategy_version": STRATEGY_VERSION,
            "strategy_model": STRATEGY_MODEL,
            "tp_activation_price": mgmt["tp_activation_price"],
            "trail_retrace_price": mgmt["trail_retrace_price"],
            "force_min_profit_price": mgmt["force_min_profit_price"],
            "force_min_strength": mgmt["force_min_strength"],
            "partial_tp_activation_price": mgmt["partial_tp_activation_price"],
            "partial_tp_close_fraction": mgmt["partial_tp_close_fraction"],
            "break_even_activation_price": mgmt["break_even_activation_price"],
            "break_even_offset_price": mgmt["break_even_offset_price"],
            "mgmt_bucket": mgmt["bucket"],
            "bucket": mgmt["bucket"],
            "meta": {
                "atr_pct": round(atr_pct, 6),
                "adx": round(adx_value, 4),
                "choppiness": round(chop, 4),
                "efficiency_ratio": round(efficiency, 4),
                "rolling_vwap": round(rolling_vwap, 8),
                "diagnostics": diag,
                "selection_reason": selected_reason,
            },
        }


DEFAULT_STRATEGY = RangeMeanReversionStrategy()


__all__ = [
    "RangeMeanReversionStrategy",
    "DEFAULT_STRATEGY",
    "STRATEGY_ID",
    "STRATEGY_MODEL",
    "STRATEGY_VERSION",
    "get_trade_management_params",
]
