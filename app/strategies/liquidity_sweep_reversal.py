from __future__ import annotations

import os
import time
from app.trading_quality import meets_min_rr
from app.settings_utils import env_float
from typing import Any, Dict, List, Optional, Tuple

from app.market_context import build_market_context
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

STRATEGY_ID = "liquidity_sweep_reversal"
STRATEGY_VERSION = "v2"
STRATEGY_MODEL = "liquidity_sweep_reversal_5m_v2_audit"

SWEEP_LOOKBACK = 34
SWEEP_MAX_AGE_BARS = 8
SWEEP_MIN_DEPTH_ATR = 0.14
SWEEP_MIN_WICK_RATIO = 0.30
SWEEP_MIN_RVOL = 0.65
SWEEP_RECOVER_TOL_ATR = 0.36
TRIGGER_MIN_RVOL = 0.62
TRIGGER_MIN_BODY_RATIO = 0.16
TRIGGER_CLOSE_POS_LONG_MIN = 0.50
TRIGGER_CLOSE_POS_SHORT_MAX = 0.50
TRIGGER_EXTENSION_MAX_ATR = 1.85
TRIGGER_EMA20_RECOVER_TOL_ATR = 0.55
TRIGGER_EMA50_RECOVER_TOL_ATR = 1.05
RETEST_INVALIDATION_ATR = 0.58
SL_BUFFER_ATR = 0.18
SL_MIN_PCT = 0.0050
SL_MAX_PCT = 0.0070  # legado/soft reference; no recorta invalidación estructural
SL_HARD_MAX_PCT = max(float(os.getenv("LIQUIDITY_SL_HARD_MAX_PCT", "0.0140") or 0.0140), SL_MIN_PCT)
TP_HARD_MAX_PCT = max(float(os.getenv("LIQUIDITY_TP_HARD_MAX_PCT", "0.0180") or 0.0180), 0.0060)
STRUCTURAL_TARGET_BUFFER = max(0.50, min(float(os.getenv("LIQUIDITY_STRUCTURAL_TARGET_BUFFER", "0.86") or 0.86), 0.98))
TARGET_LOOKBACK = 48
MIN_RR = 1.05
MIN_FINAL_RR = max(env_float("LIQUIDITY_MIN_FINAL_RR", 1.15), env_float("MIN_ENTRY_RR", 1.15))
ENTRY_RR_RESERVE = env_float("ENTRY_RR_RESERVE", 0.05, maximum=0.50)
ATR_PCT_MIN = 0.0013
ATR_PCT_MAX = 0.0280
LOG_SIGNAL_DIAGNOSTICS = os.getenv("LOG_LIQUIDITY_SIGNAL_DIAGNOSTICS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _log(msg: str) -> None:
    try:
        print(f"[STRATEGY-LIQ {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {msg}")
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
    s = str(symbol or "").upper()
    for suffix in ("-PERP", "-USDC", "-USD", "-USDT"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _is_probable_meme_symbol(symbol: str) -> bool:
    base = _base_coin(symbol)
    return bool(base) and any(key in base for key in BLOCKED_MEME_KEYWORDS)


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


def _compute_liquidity_fixed_tp_pct(
    *,
    score: float,
    atr_pct: float,
    sl_pct: float,
    rr_estimate: float,
    structural_tp_pct: float,
    bars_since_sweep: int,
    trigger_rvol: float,
) -> Tuple[float, float]:
    sl_pct = max(float(sl_pct or 0.0), SL_MIN_PCT)
    score = float(score or 0.0)
    atr_pct = float(atr_pct or 0.0)
    rr_estimate = float(rr_estimate or 0.0)
    structural_tp_pct = max(float(structural_tp_pct or 0.0), 0.0)
    bars_since_sweep = max(1, int(bars_since_sweep or 1))
    trigger_rvol = float(trigger_rvol or 1.0)

    # El objetivo nace del riesgo real y de la calidad del setup. Después se
    # limita ANTES del obstáculo estructural; nunca se fuerza detrás de él.
    rr_target = 1.24
    rr_target += _clamp((score - 82.0) * 0.006, -0.06, 0.12)
    rr_target += _clamp((trigger_rvol - 1.0) * 0.06, -0.04, 0.08)
    rr_target += _clamp((min(rr_estimate, 2.4) - 1.2) * 0.05, -0.04, 0.06)
    rr_target -= _clamp((bars_since_sweep - 1) * 0.012, 0.0, 0.06)
    rr_target = _clamp(rr_target, MIN_FINAL_RR + ENTRY_RR_RESERVE, max(1.38, MIN_FINAL_RR + ENTRY_RR_RESERVE))

    tp_pct = min(sl_pct * rr_target, TP_HARD_MAX_PCT)
    if structural_tp_pct > 0.0:
        tp_pct = min(tp_pct, structural_tp_pct * STRUCTURAL_TARGET_BUFFER)

    rr_real = tp_pct / max(sl_pct, 1e-12)
    return tp_pct, rr_real


def _compute_management_params(
    strength: float,
    score: float,
    atr_pct: Optional[float] = None,
    *,
    sl_pct: Optional[float] = None,
    rr_estimate: float = 0.0,
    structural_tp_pct: float = 0.0,
    bars_since_sweep: int = 1,
    trigger_rvol: float = 1.0,
) -> Dict[str, Any]:
    atr_pct = float(atr_pct or 0.0)
    score = float(score or 0.0)
    strength = float(strength or 0.0)
    sl_pct = float(sl_pct or SL_MIN_PCT)

    tp_fixed, tp_rr_multiple = _compute_liquidity_fixed_tp_pct(
        score=score,
        atr_pct=atr_pct,
        sl_pct=sl_pct,
        rr_estimate=rr_estimate,
        structural_tp_pct=structural_tp_pct,
        bars_since_sweep=bars_since_sweep,
        trigger_rvol=trigger_rvol,
    )

    break_even_activation = _clamp(max(sl_pct * 0.46, tp_fixed * 0.40), 0.0028, min(tp_fixed * 0.56, 0.0039))
    break_even_offset = _clamp(max(atr_pct * 0.06, 0.00050), 0.00050, 0.00095)

    if score >= 89.0:
        bucket = "liquidity_strong"
    elif score >= 82.0:
        bucket = "liquidity_base"
    else:
        bucket = "liquidity_weak"

    return {
        "bucket": bucket,
        "tp_activation_price": tp_fixed,
        "trail_retrace_price": 0.0,
        "force_min_profit_price": 999999.0,
        "force_min_strength": 0.0,
        "partial_tp_activation_price": 999999.0,
        "partial_tp_close_fraction": 0.0,
        "break_even_activation_price": 0.0,
        "break_even_offset_price": 0.0,
        "tp_rr_multiple": round(tp_rr_multiple, 4),
        "vol_regime": "volatile" if atr_pct >= 0.0095 else ("active" if atr_pct >= 0.0050 else "normal"),
    }


def get_trade_management_params(strength: float, score: float, atr_pct: Optional[float] = None, sl_pct: Optional[float] = None) -> Dict[str, Any]:
    return _compute_management_params(strength, score, atr_pct, sl_pct=sl_pct)


def _score_candidate(
    *,
    sweep_depth_atr: float,
    sweep_wick_ratio: float,
    sweep_rvol: float,
    trigger_rvol: float,
    trigger_body_ratio: float,
    trigger_close_pos: float,
    extension_atr: float,
    rr_estimate: float,
    bars_since_sweep: int,
) -> float:
    depth_q = _clamp((sweep_depth_atr - SWEEP_MIN_DEPTH_ATR) / 0.70, 0.0, 1.0)
    wick_q = _clamp((sweep_wick_ratio - SWEEP_MIN_WICK_RATIO) / 0.42, 0.0, 1.0)
    sweep_rvol_q = _clamp((sweep_rvol - 0.85) / 0.90, 0.0, 1.0)
    trigger_rvol_q = _clamp((trigger_rvol - 0.80) / 0.85, 0.0, 1.0)
    body_q = _clamp((trigger_body_ratio - TRIGGER_MIN_BODY_RATIO) / 0.45, 0.0, 1.0)
    close_q = _clamp((trigger_close_pos - 0.50) / 0.40, 0.0, 1.0)
    extension_penalty = _clamp(extension_atr / TRIGGER_EXTENSION_MAX_ATR, 0.0, 1.0)
    rr_q = _clamp((rr_estimate - MIN_RR) / 1.4, 0.0, 1.0)
    age_penalty = _clamp((bars_since_sweep - 1) / max(SWEEP_MAX_AGE_BARS - 1, 1), 0.0, 1.0)

    quality = _clamp(
        (0.19 * depth_q)
        + (0.16 * wick_q)
        + (0.12 * sweep_rvol_q)
        + (0.13 * trigger_rvol_q)
        + (0.14 * body_q)
        + (0.10 * close_q)
        + (0.18 * rr_q)
        - (0.08 * extension_penalty)
        - (0.06 * age_penalty),
        0.0,
        1.0,
    )
    return round(min(100.0, 62.0 + (38.0 * quality)), 2)


def _detect_long_sweep(
    o: List[float],
    h: List[float],
    l: List[float],
    c: List[float],
    v: List[float],
    ema20: List[float],
    ema50: List[float],
    atr_value: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    if len(c) < max(SWEEP_LOOKBACK + SWEEP_MAX_AGE_BARS + 6, 90):
        return False, "NOT_ENOUGH_BARS", {}

    trigger_idx = len(c) - 1
    best: Optional[Dict[str, Any]] = None
    failures: Dict[str, int] = {}
    examined = 0

    def reject(reason: str) -> None:
        failures[reason] = failures.get(reason, 0) + 1

    for sweep_idx in range(max(SWEEP_LOOKBACK + 2, trigger_idx - SWEEP_MAX_AGE_BARS), trigger_idx):
        examined += 1
        left_start = max(0, sweep_idx - SWEEP_LOOKBACK)
        if sweep_idx - left_start < 12:
            reject("HISTORY_TOO_SHORT")
            continue
        liquidity_level = min(l[left_start:sweep_idx])
        sweep_low = float(l[sweep_idx])
        sweep_depth_atr = (float(liquidity_level) - sweep_low) / max(atr_value, 1e-12)
        if sweep_depth_atr < SWEEP_MIN_DEPTH_ATR:
            reject("NO_SWEEP_DEPTH")
            continue

        sweep_close_pos = _close_position_in_range(o[sweep_idx], h[sweep_idx], l[sweep_idx], c[sweep_idx])
        sweep_wick_ratio = _lower_wick_ratio(o[sweep_idx], h[sweep_idx], l[sweep_idx], c[sweep_idx])
        sweep_rvol = _relative_volume(v, sweep_idx, 24)
        if sweep_wick_ratio < SWEEP_MIN_WICK_RATIO:
            reject("SWEEP_WICK_TOO_SMALL")
            continue
        if sweep_rvol < SWEEP_MIN_RVOL:
            reject("SWEEP_VOLUME_TOO_LOW")
            continue
        if float(c[sweep_idx]) < float(liquidity_level) - (atr_value * SWEEP_RECOVER_TOL_ATR):
            reject("LEVEL_NOT_RECOVERED")
            continue
        if sweep_close_pos < 0.46:
            reject("SWEEP_CLOSE_WEAK")
            continue

        bars_since_sweep = trigger_idx - sweep_idx
        if bars_since_sweep <= 0 or bars_since_sweep > SWEEP_MAX_AGE_BARS:
            reject("SWEEP_EXPIRED")
            continue

        invalidation_low = min(l[sweep_idx + 1:trigger_idx + 1]) if trigger_idx > sweep_idx else sweep_low
        if invalidation_low < sweep_low - (atr_value * RETEST_INVALIDATION_ATR):
            reject("SWEEP_INVALIDATED")
            continue

        trigger_rvol = _relative_volume(v, trigger_idx, 24)
        trigger_body_ratio = _body_ratio(o[trigger_idx], h[trigger_idx], l[trigger_idx], c[trigger_idx])
        trigger_close_pos = _close_position_in_range(o[trigger_idx], h[trigger_idx], l[trigger_idx], c[trigger_idx])
        extension_atr = (float(c[trigger_idx]) - float(liquidity_level)) / max(atr_value, 1e-12)
        trigger_checks = {
            "TRIGGER_DIRECTION": float(c[trigger_idx]) > float(o[trigger_idx]),
            "LEVEL_CONFIRMATION": float(c[trigger_idx]) > float(liquidity_level),
            "SWEEP_CLOSE_CONFIRMATION": float(c[trigger_idx]) >= float(c[sweep_idx]),
            "EMA20_RECOVERY": float(c[trigger_idx]) >= float(ema20[trigger_idx]) - (atr_value * TRIGGER_EMA20_RECOVER_TOL_ATR),
            "EMA50_RECOVERY": float(c[trigger_idx]) >= float(ema50[trigger_idx]) - (atr_value * TRIGGER_EMA50_RECOVER_TOL_ATR),
            "TRIGGER_VOLUME": trigger_rvol >= TRIGGER_MIN_RVOL,
            "TRIGGER_BODY": trigger_body_ratio >= TRIGGER_MIN_BODY_RATIO,
            "TRIGGER_CLOSE_POSITION": trigger_close_pos >= TRIGGER_CLOSE_POS_LONG_MIN,
            "TRIGGER_EXTENSION": extension_atr <= TRIGGER_EXTENSION_MAX_ATR,
        }
        trigger_ok = all(trigger_checks.values())
        if not trigger_ok:
            for name, passed in trigger_checks.items():
                if not passed:
                    reject(name)
            reject("TRIGGER_NOT_CONFIRMED")
            continue

        left_target_start = max(0, sweep_idx - TARGET_LOOKBACK)
        target_level = max(h[left_target_start:sweep_idx]) if sweep_idx > left_target_start else max(h[:sweep_idx] or [0.0])
        stop_price = min(l[sweep_idx:trigger_idx + 1]) - (atr_value * SL_BUFFER_ATR)
        risk_abs = max(float(c[trigger_idx]) - stop_price, 1e-12)
        reward_abs = max(float(target_level) - float(c[trigger_idx]), 0.0)
        rr_estimate = reward_abs / risk_abs if risk_abs > 0 else 0.0
        if rr_estimate < MIN_RR:
            reject("TARGET_RR_TOO_LOW")
            continue

        diag = {
            "liquidity_level": round(float(liquidity_level), 8),
            "sweep_idx": int(sweep_idx),
            "bars_since_sweep": int(bars_since_sweep),
            "sweep_depth_atr": round(float(sweep_depth_atr), 4),
            "sweep_wick_ratio": round(float(sweep_wick_ratio), 4),
            "sweep_rvol": round(float(sweep_rvol), 4),
            "trigger_rvol": round(float(trigger_rvol), 4),
            "trigger_body_ratio": round(float(trigger_body_ratio), 4),
            "trigger_close_pos": round(float(trigger_close_pos), 4),
            "trigger_extension_atr": round(float(extension_atr), 4),
            "rr_estimate": round(float(rr_estimate), 4),
            "target_level": round(float(target_level), 8),
            "stop_price": float(stop_price),
            "direction": "long",
        }
        score = _score_candidate(
            sweep_depth_atr=float(sweep_depth_atr),
            sweep_wick_ratio=float(sweep_wick_ratio),
            sweep_rvol=float(sweep_rvol),
            trigger_rvol=float(trigger_rvol),
            trigger_body_ratio=float(trigger_body_ratio),
            trigger_close_pos=float(trigger_close_pos),
            extension_atr=float(extension_atr),
            rr_estimate=float(rr_estimate),
            bars_since_sweep=int(bars_since_sweep),
        )
        diag["score"] = float(score)
        if best is None or float(score) > float(best.get("score", 0.0)):
            best = diag

    if not best:
        return False, "NO_VALID_LONG_SWEEP", {"examined": examined, "failures": failures}
    best["search_diagnostics"] = {"examined": examined, "failures": failures}
    return True, "OK", best


def _detect_short_sweep(
    o: List[float],
    h: List[float],
    l: List[float],
    c: List[float],
    v: List[float],
    ema20: List[float],
    ema50: List[float],
    atr_value: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    if len(c) < max(SWEEP_LOOKBACK + SWEEP_MAX_AGE_BARS + 6, 90):
        return False, "NOT_ENOUGH_BARS", {}

    trigger_idx = len(c) - 1
    best: Optional[Dict[str, Any]] = None
    failures: Dict[str, int] = {}
    examined = 0

    def reject(reason: str) -> None:
        failures[reason] = failures.get(reason, 0) + 1

    for sweep_idx in range(max(SWEEP_LOOKBACK + 2, trigger_idx - SWEEP_MAX_AGE_BARS), trigger_idx):
        examined += 1
        left_start = max(0, sweep_idx - SWEEP_LOOKBACK)
        if sweep_idx - left_start < 12:
            reject("HISTORY_TOO_SHORT")
            continue
        liquidity_level = max(h[left_start:sweep_idx])
        sweep_high = float(h[sweep_idx])
        sweep_depth_atr = (sweep_high - float(liquidity_level)) / max(atr_value, 1e-12)
        if sweep_depth_atr < SWEEP_MIN_DEPTH_ATR:
            reject("NO_SWEEP_DEPTH")
            continue

        sweep_close_pos = _close_position_in_range(o[sweep_idx], h[sweep_idx], l[sweep_idx], c[sweep_idx])
        sweep_wick_ratio = _upper_wick_ratio(o[sweep_idx], h[sweep_idx], l[sweep_idx], c[sweep_idx])
        sweep_rvol = _relative_volume(v, sweep_idx, 24)
        if sweep_wick_ratio < SWEEP_MIN_WICK_RATIO:
            reject("SWEEP_WICK_TOO_SMALL")
            continue
        if sweep_rvol < SWEEP_MIN_RVOL:
            reject("SWEEP_VOLUME_TOO_LOW")
            continue
        if float(c[sweep_idx]) > float(liquidity_level) + (atr_value * SWEEP_RECOVER_TOL_ATR):
            reject("LEVEL_NOT_RECOVERED")
            continue
        if sweep_close_pos > 0.54:
            reject("SWEEP_CLOSE_WEAK")
            continue

        bars_since_sweep = trigger_idx - sweep_idx
        if bars_since_sweep <= 0 or bars_since_sweep > SWEEP_MAX_AGE_BARS:
            reject("SWEEP_EXPIRED")
            continue

        invalidation_high = max(h[sweep_idx + 1:trigger_idx + 1]) if trigger_idx > sweep_idx else sweep_high
        if invalidation_high > sweep_high + (atr_value * RETEST_INVALIDATION_ATR):
            reject("SWEEP_INVALIDATED")
            continue

        trigger_rvol = _relative_volume(v, trigger_idx, 24)
        trigger_body_ratio = _body_ratio(o[trigger_idx], h[trigger_idx], l[trigger_idx], c[trigger_idx])
        trigger_close_pos = _close_position_in_range(o[trigger_idx], h[trigger_idx], l[trigger_idx], c[trigger_idx])
        extension_atr = (float(liquidity_level) - float(c[trigger_idx])) / max(atr_value, 1e-12)
        trigger_checks = {
            "TRIGGER_DIRECTION": float(c[trigger_idx]) < float(o[trigger_idx]),
            "LEVEL_CONFIRMATION": float(c[trigger_idx]) < float(liquidity_level),
            "SWEEP_CLOSE_CONFIRMATION": float(c[trigger_idx]) <= float(c[sweep_idx]),
            "EMA20_RECOVERY": float(c[trigger_idx]) <= float(ema20[trigger_idx]) + (atr_value * TRIGGER_EMA20_RECOVER_TOL_ATR),
            "EMA50_RECOVERY": float(c[trigger_idx]) <= float(ema50[trigger_idx]) + (atr_value * TRIGGER_EMA50_RECOVER_TOL_ATR),
            "TRIGGER_VOLUME": trigger_rvol >= TRIGGER_MIN_RVOL,
            "TRIGGER_BODY": trigger_body_ratio >= TRIGGER_MIN_BODY_RATIO,
            "TRIGGER_CLOSE_POSITION": trigger_close_pos <= TRIGGER_CLOSE_POS_SHORT_MAX,
            "TRIGGER_EXTENSION": extension_atr <= TRIGGER_EXTENSION_MAX_ATR,
        }
        trigger_ok = all(trigger_checks.values())
        if not trigger_ok:
            for name, passed in trigger_checks.items():
                if not passed:
                    reject(name)
            reject("TRIGGER_NOT_CONFIRMED")
            continue

        left_target_start = max(0, sweep_idx - TARGET_LOOKBACK)
        target_level = min(l[left_target_start:sweep_idx]) if sweep_idx > left_target_start else min(l[:sweep_idx] or [0.0])
        stop_price = max(h[sweep_idx:trigger_idx + 1]) + (atr_value * SL_BUFFER_ATR)
        risk_abs = max(stop_price - float(c[trigger_idx]), 1e-12)
        reward_abs = max(float(c[trigger_idx]) - float(target_level), 0.0)
        rr_estimate = reward_abs / risk_abs if risk_abs > 0 else 0.0
        if rr_estimate < MIN_RR:
            reject("TARGET_RR_TOO_LOW")
            continue

        diag = {
            "liquidity_level": round(float(liquidity_level), 8),
            "sweep_idx": int(sweep_idx),
            "bars_since_sweep": int(bars_since_sweep),
            "sweep_depth_atr": round(float(sweep_depth_atr), 4),
            "sweep_wick_ratio": round(float(sweep_wick_ratio), 4),
            "sweep_rvol": round(float(sweep_rvol), 4),
            "trigger_rvol": round(float(trigger_rvol), 4),
            "trigger_body_ratio": round(float(trigger_body_ratio), 4),
            "trigger_close_pos": round(float(trigger_close_pos), 4),
            "trigger_extension_atr": round(float(extension_atr), 4),
            "rr_estimate": round(float(rr_estimate), 4),
            "target_level": round(float(target_level), 8),
            "stop_price": float(stop_price),
            "direction": "short",
        }
        score = _score_candidate(
            sweep_depth_atr=float(sweep_depth_atr),
            sweep_wick_ratio=float(sweep_wick_ratio),
            sweep_rvol=float(sweep_rvol),
            trigger_rvol=float(trigger_rvol),
            trigger_body_ratio=float(trigger_body_ratio),
            trigger_close_pos=float(1.0 - trigger_close_pos),
            extension_atr=float(extension_atr),
            rr_estimate=float(rr_estimate),
            bars_since_sweep=int(bars_since_sweep),
        )
        diag["score"] = float(score)
        if best is None or float(score) > float(best.get("score", 0.0)):
            best = diag

    if not best:
        return False, "NO_VALID_SHORT_SWEEP", {"examined": examined, "failures": failures}
    best["search_diagnostics"] = {"examined": examined, "failures": failures}
    return True, "OK", best


def _evaluate_market_context(market_context: Dict[str, Any]) -> Dict[str, Any]:
    try:
        coin = str(market_context.get("coin") or "").strip()
        if not coin:
            return {"signal": False, "reason": "BAD_SYMBOL"}

        tf5 = (market_context.get("timeframes") or {}).get(TF_5M) or {}
        status = str(tf5.get("status") or "UNKNOWN")
        candles = tf5.get("candles") or []
        if status in ("API_FAIL", "BAD_SYMBOL", "BAD_INTERVAL"):
            return {"signal": False, "reason": "CANDLES_FETCH_FAIL", "detail": {TF_5M: status}, "coin": coin}
        if not candles:
            return {"signal": False, "reason": "NO_CANDLES", "coin": coin}

        quality_ok, quality_reason, quality_diag = _validate_symbol_quality(coin, candles)
        if not quality_ok:
            return {"signal": False, "reason": quality_reason, "coin": coin, "diag": quality_diag}

        stale = bool(tf5.get("stale", True))
        age_s = float(tf5.get("age_s", 0.0) or 0.0)
        last_t = int(tf5.get("last_t", 0) or 0)
        if stale:
            return {"signal": False, "reason": "STALE_CANDLES", "coin": coin, "age_s": {TF_5M: round(age_s, 1)}, "last_t": {TF_5M: last_t}}

        o = tf5.get("o") or []
        h = tf5.get("h") or []
        l = tf5.get("l") or []
        c = tf5.get("c") or []
        v = tf5.get("v") or []
        ema20 = tf5.get(f"ema{EMA_FAST}") or []
        ema50 = tf5.get(f"ema{EMA_MID}") or []
        ema200 = tf5.get(f"ema{EMA_SLOW}") or []
        if not (c and ema20 and ema50 and ema200):
            return {"signal": False, "reason": "NO_TREND_DATA", "coin": coin}

        close5 = float(tf5.get("close") or c[-1])
        atr_value = float(tf5.get("atr") or 0.0)
        atr_pct = float(tf5.get("atr_pct") or 0.0)
        adx5 = float(tf5.get("adx") or 0.0)
        if atr_pct < ATR_PCT_MIN:
            return {"signal": False, "reason": "ATR_TOO_LOW", "coin": coin, "diag": {"atr_pct": round(atr_pct, 6)}}
        if atr_pct > ATR_PCT_MAX:
            return {"signal": False, "reason": "ATR_TOO_HIGH", "coin": coin, "diag": {"atr_pct": round(atr_pct, 6)}}

        long_ok, long_reason, long_diag = _detect_long_sweep(o, h, l, c, v, ema20, ema50, atr_value)
        short_ok, short_reason, short_diag = _detect_short_sweep(o, h, l, c, v, ema20, ema50, atr_value)

        if not long_ok and not short_ok:
            if LOG_SIGNAL_DIAGNOSTICS:
                _log(f"BLOCK coin={coin} long={long_reason} short={short_reason} atr_pct={atr_pct:.6f} adx={adx5:.2f}")
            return {
                "signal": False,
                "reason": "NO_LIQUIDITY_SWEEP_SETUP",
                "coin": coin,
                "diag": {
                    "long_reason": long_reason,
                    "long_search": long_diag,
                    "short_search": short_diag,
                    "short_reason": short_reason,
                    "atr_pct": round(atr_pct, 6),
                    "adx5": round(adx5, 2),
                },
            }

        candidate = None
        if long_ok and short_ok:
            candidate = long_diag if float(long_diag.get("score", 0.0)) >= float(short_diag.get("score", 0.0)) else short_diag
        else:
            candidate = long_diag if long_ok else short_diag

        direction = str(candidate.get("direction") or "")
        if direction not in {"long", "short"}:
            return {"signal": False, "reason": "BAD_DIRECTION", "coin": coin}

        structural_stop_price = float(candidate.get("stop_price") or 0.0)
        structural_sl_pct = ((close5 - structural_stop_price) / max(close5, 1e-12)) if direction == "long" else ((structural_stop_price - close5) / max(close5, 1e-12))
        # Mantiene el stop fuera de la invalidación y le da un suelo de ruido
        # basado en ATR; nunca lo recorta hacia dentro de la estructura.
        sl_pct = max(float(structural_sl_pct), SL_MIN_PCT, atr_pct * 0.72)
        if sl_pct > SL_HARD_MAX_PCT:
            return {
                "signal": False,
                "reason": "STRUCTURAL_SL_TOO_WIDE",
                "coin": coin,
                "diag": {
                    "sl_pct": round(sl_pct, 6),
                    "structural_sl_pct": round(structural_sl_pct, 6),
                    "hard_max": round(SL_HARD_MAX_PCT, 6),
                },
            }
        stop_price = close5 * (1.0 - sl_pct) if direction == "long" else close5 * (1.0 + sl_pct)

        score = float(candidate.get("score") or 0.0)
        if score < 74.0:
            return {
                "signal": False,
                "reason": "SCORE_TOO_LOW",
                "coin": coin,
                "diag": {"score": round(score, 2), "min_score": 74.0, "candidate": dict(candidate)},
            }

        structural_tp_pct = 0.0
        target_level = float(candidate.get("target_level") or 0.0)
        if target_level > 0.0:
            structural_tp_pct = (abs(target_level - close5) / max(close5, 1e-12))

        strength = _clamp(score / 100.0, 0.22, 0.96)
        mgmt = _compute_management_params(
            strength,
            score,
            atr_pct,
            sl_pct=sl_pct,
            rr_estimate=float(candidate.get("rr_estimate") or 0.0),
            structural_tp_pct=structural_tp_pct,
            bars_since_sweep=int(candidate.get("bars_since_sweep") or 0),
            trigger_rvol=float(candidate.get("trigger_rvol") or 0.0),
        )
        final_tp_pct = float(mgmt.get("tp_activation_price") or 0.0)
        final_rr = final_tp_pct / max(sl_pct, 1e-12)
        if final_tp_pct <= 0.0 or not meets_min_rr(final_rr, MIN_FINAL_RR + ENTRY_RR_RESERVE):
            return {
                "signal": False,
                "reason": "STRUCTURAL_RR_TOO_LOW",
                "coin": coin,
                "diag": {
                    "rr": round(final_rr, 4),
                    "min_rr": MIN_FINAL_RR,
                    "sl_pct": round(sl_pct, 6),
                    "tp_pct": round(final_tp_pct, 6),
                    "structural_tp_pct": round(structural_tp_pct, 6),
                    "target_level": round(target_level, 8),
                },
            }
        target_price = close5 * (1.0 + final_tp_pct) if direction == "long" else close5 * (1.0 - final_tp_pct)
        mgmt["tp_rr_multiple"] = round(final_rr, 4)

        out = {
            "signal": True,
            "direction": direction,
            "strength": round(strength, 4),
            "score": round(score, 2),
            "sl_price_pct": sl_pct,
            "signal_reference_price": close5,
            "stop_price": float(stop_price),
            "target_price": float(target_price),
            "structural_target_price": round(float(target_level), 8),
            "tp_activation_price": float(mgmt["tp_activation_price"]),
            "trail_retrace_price": float(mgmt["trail_retrace_price"]),
            "force_min_profit_price": float(mgmt["force_min_profit_price"]),
            "force_min_strength": float(mgmt["force_min_strength"]),
            "partial_tp_activation_price": float(mgmt["partial_tp_activation_price"]),
            "partial_tp_close_fraction": float(mgmt["partial_tp_close_fraction"]),
            "break_even_activation_price": float(mgmt["break_even_activation_price"]),
            "break_even_offset_price": float(mgmt["break_even_offset_price"]),
            "mgmt_bucket": str(mgmt["bucket"]),
            "vol_regime": str(mgmt.get("vol_regime", "volatile")),
            "atr_pct": round(float(atr_pct), 6),
            "close_5": round(close5, 6),
            "last_candle_t_5m": int(tf5.get("last_close_t") or last_t),
            "ema20_5m": round(float(ema20[-1]), 6),
            "adx1": round(adx5, 2),
            "adx15": round(adx5, 2),
            "coin": coin,
            "strategy_model": STRATEGY_MODEL,
            "sweep_level": float(candidate.get("liquidity_level") or 0.0),
            "sweep_idx": int(candidate.get("sweep_idx") or 0),
            "bars_since_sweep": int(candidate.get("bars_since_sweep") or 0),
            "sweep_depth_atr": float(candidate.get("sweep_depth_atr") or 0.0),
            "sweep_wick_ratio": float(candidate.get("sweep_wick_ratio") or 0.0),
            "sweep_rvol": float(candidate.get("sweep_rvol") or 0.0),
            "trigger_rvol": float(candidate.get("trigger_rvol") or 0.0),
            "trigger_body_ratio": float(candidate.get("trigger_body_ratio") or 0.0),
            "trigger_close_pos": float(candidate.get("trigger_close_pos") or 0.0),
            "rr_estimate": float(candidate.get("rr_estimate") or 0.0),
            "structural_tp_pct": round(float(structural_tp_pct), 6),
            "tp_rr_multiple": float(mgmt.get("tp_rr_multiple", 0.0) or 0.0),
            "market_context_status": str(market_context.get("status") or status),
        }
        if LOG_SIGNAL_DIAGNOSTICS:
            _log(
                f"SIGNAL coin={coin} dir={direction} close_5={out['close_5']} adx5={adx5:.2f} atr_pct={atr_pct:.6f} "
                f"score={out['score']:.2f} strength={out['strength']:.4f} sweep_depth_atr={out['sweep_depth_atr']:.4f} "
                f"bars_since_sweep={out['bars_since_sweep']} rr={out['rr_estimate']:.4f} sl_pct={out['sl_price_pct']:.6f} "
                f"tp_fixed={out['tp_activation_price']:.6f} tp_rr={out['tp_rr_multiple']:.4f} struct_tp={out['structural_tp_pct']:.6f} "
                f"be_act={out['break_even_activation_price']:.6f} be_offset={out['break_even_offset_price']:.6f}"
            )
        return out
    except Exception as e:
        return {"signal": False, "reason": "STRATEGY_EXCEPTION", "error": str(e)[:180]}


def get_entry_signal(symbol: str, market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    market_context = market_context or build_market_context(
        symbol=symbol,
        interval=TF_5M,
        limit=LOOKBACK_5M,
        ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
        adx_period=ADX_PERIOD,
        atr_period=ATR_PERIOD,
    )
    return _evaluate_market_context(market_context)


class LiquiditySweepReversalStrategy(BaseStrategy):
    strategy_id = STRATEGY_ID
    strategy_version = STRATEGY_VERSION
    strategy_model = STRATEGY_MODEL

    def evaluate(self, symbol: str, market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = get_entry_signal(symbol, market_context=market_context)
        if isinstance(out, dict) and out.get("signal"):
            out.setdefault("strategy_id", self.strategy_id)
            out.setdefault("strategy_version", self.strategy_version)
            out.setdefault("strategy_model", self.strategy_model)
        return out

    def get_trade_management_params(self, strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
        return get_trade_management_params(strength, score, atr_pct)


DEFAULT_STRATEGY = LiquiditySweepReversalStrategy()
