from __future__ import annotations

import os
from typing import Any, Dict, Optional

TF_5M = "5m"
from app.regime.features import build_regime_features
from app.regime.state_machine import (
    DEFAULT_CONFIRM_BARS,
    DEFAULT_COOLDOWN_BARS,
    DEFAULT_MIN_ACTIVE_BARS,
    advance_regime_state,
)

REGIME_TREND = "TREND_CONTINUATION"
REGIME_VOLATILE = "VOLATILE_SWEEP"
REGIME_RANGE = "RANGE"
REGIME_UNKNOWN = "UNKNOWN"
DETECTOR_VERSION = "v2_calibrated_router"


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 0)
    except Exception:
        return default



def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default



def _score_bool(condition: bool) -> int:
    return 1 if condition else 0



def classify_candidate_regime(features: Dict[str, Any]) -> Dict[str, Any]:
    reasons: list[str] = []
    confidence = 0.0

    if not features.get("context_ok"):
        reasons.append(f"symbol_context_not_ok:{features.get('context_status')}")
        return {
            "candidate_regime": REGIME_UNKNOWN,
            "confidence": 0.0,
            "reasons": reasons,
            "scores": {REGIME_TREND: 0, REGIME_VOLATILE: 0, REGIME_RANGE: 0},
            "regime_bias": "neutral",
        }

    adx = float(features.get("adx") or 0.0)
    chop = float(features.get("choppiness") or 50.0)
    efficiency = float(features.get("efficiency_ratio") or 0.0)
    wick_instability = float(features.get("wick_instability") or 0.0)
    body_quality = float(features.get("body_quality") or 0.0)
    breakout_failure_ratio = float(features.get("breakout_failure_ratio") or 0.0)
    atr_pct = float(features.get("atr_pct") or 0.0)
    btc_shock_ratio = float(features.get("btc_shock_ratio") or 0.0)
    distance_to_vwap_atr = float(features.get("distance_to_vwap_atr") or 0.0)
    ema_stack_alignment = float(features.get("ema_stack_alignment") or 0.0)
    trend_bias = str(features.get("trend_bias") or "neutral")
    recent_move_3 = abs(float(features.get("recent_move_3") or 0.0))
    btc_recent_move_3 = abs(float(features.get("btc_recent_move_3") or 0.0))

    trend_adx_min = _env_float("REGIME_TREND_ADX_MIN", 15.0)
    trend_chop_max = _env_float("REGIME_TREND_CHOP_MAX", 57.0)
    trend_eff_min = _env_float("REGIME_TREND_EFFICIENCY_MIN", 0.23)
    trend_ema_align_min = _env_float("REGIME_TREND_EMA_ALIGN_MIN", 0.54)
    trend_breakout_fail_max = _env_float("REGIME_TREND_BREAKOUT_FAIL_MAX", 0.26)

    volatile_btc_shock_min = _env_float("REGIME_VOLATILE_BTC_SHOCK_MIN", 1.20)
    volatile_wick_min = _env_float("REGIME_VOLATILE_WICK_MIN", 0.48)
    volatile_breakout_fail_min = _env_float("REGIME_VOLATILE_BREAKOUT_FAIL_MIN", 0.18)
    volatile_atr_pct_min = _env_float("REGIME_VOLATILE_ATR_PCT_MIN", 0.0060)

    range_adx_max = _env_float("REGIME_RANGE_ADX_MAX", 22.0)
    range_chop_min = _env_float("REGIME_RANGE_CHOP_MIN", 51.0)
    range_eff_max = _env_float("REGIME_RANGE_EFFICIENCY_MAX", 0.38)
    range_vwap_dist_max = _env_float("REGIME_RANGE_VWAP_DIST_ATR_MAX", 1.25)
    range_ema_align_max = _env_float("REGIME_RANGE_EMA_ALIGN_MAX", 0.68)

    volatile_score = 0
    volatile_score += _score_bool(btc_shock_ratio >= volatile_btc_shock_min)
    volatile_score += _score_bool(wick_instability >= volatile_wick_min)
    volatile_score += _score_bool(breakout_failure_ratio >= volatile_breakout_fail_min)
    volatile_score += _score_bool(atr_pct >= volatile_atr_pct_min)
    volatile_score += _score_bool(body_quality <= 0.45 and efficiency <= 0.48)
    volatile_score += _score_bool(recent_move_3 >= max(atr_pct * 0.55, 0.0035) or btc_recent_move_3 >= 0.0035)

    trend_score = 0
    trend_score += _score_bool(adx >= trend_adx_min)
    trend_score += _score_bool(chop <= trend_chop_max)
    trend_score += _score_bool(efficiency >= trend_eff_min)
    trend_score += _score_bool(ema_stack_alignment >= trend_ema_align_min)
    trend_score += _score_bool(breakout_failure_ratio <= trend_breakout_fail_max)
    trend_score += _score_bool(body_quality >= 0.35)

    range_score = 0
    range_score += _score_bool(adx <= range_adx_max)
    range_score += _score_bool(chop >= range_chop_min)
    range_score += _score_bool(efficiency <= range_eff_max)
    range_score += _score_bool(distance_to_vwap_atr <= range_vwap_dist_max)
    range_score += _score_bool(ema_stack_alignment <= range_ema_align_max)
    range_score += _score_bool(breakout_failure_ratio >= 0.07 or wick_instability >= 0.42)

    scores = {
        REGIME_TREND: trend_score,
        REGIME_VOLATILE: volatile_score,
        REGIME_RANGE: range_score,
    }
    best_regime = max(scores, key=scores.get)
    best_score = int(scores.get(best_regime) or 0)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    second_score = int(ordered[1][1]) if len(ordered) > 1 else 0

    trend_ready = (
        trend_score >= 4
        or (
            trend_score >= 3
            and adx >= trend_adx_min * 0.95
            and efficiency >= trend_eff_min * 0.90
            and ema_stack_alignment >= max(0.50, trend_ema_align_min * 0.90)
        )
    )
    volatile_ready = (
        volatile_score >= 4
        or (
            volatile_score >= 3
            and (
                btc_shock_ratio >= volatile_btc_shock_min * 0.92
                or wick_instability >= max(volatile_wick_min * 0.92, 0.46)
                or breakout_failure_ratio >= volatile_breakout_fail_min * 0.95
                or atr_pct >= volatile_atr_pct_min * 1.05
            )
        )
        or (
            volatile_score >= 2
            and wick_instability >= max(volatile_wick_min + 0.02, 0.50)
            and breakout_failure_ratio >= volatile_breakout_fail_min
            and atr_pct >= volatile_atr_pct_min
        )
    )
    range_ready = (
        range_score >= 4
        or (
            range_score >= 3
            and adx <= range_adx_max * 1.10
            and chop >= range_chop_min * 0.92
            and efficiency <= max(range_eff_max * 1.10, 0.42)
            and distance_to_vwap_atr <= range_vwap_dist_max * 1.15
        )
    )

    volatile_decisive = (
        btc_shock_ratio >= volatile_btc_shock_min * 1.02
        or wick_instability >= max(volatile_wick_min + 0.03, 0.56)
        or (breakout_failure_ratio >= volatile_breakout_fail_min + 0.06 and atr_pct >= volatile_atr_pct_min * 1.05)
    )
    trend_decisive = ema_stack_alignment >= max(trend_ema_align_min + 0.06, 0.64) or breakout_failure_ratio <= min(trend_breakout_fail_max * 0.80, 0.14)
    range_decisive = distance_to_vwap_atr <= min(range_vwap_dist_max * 0.82, 0.95) or chop >= max(range_chop_min + 3.0, 56.0)

    if volatile_ready and volatile_score >= max(trend_score, range_score) and (volatile_score - second_score >= 1 or volatile_decisive):
        candidate = REGIME_VOLATILE
        confidence = min(0.99, 0.42 + 0.08 * volatile_score + 0.03 * max(0, volatile_score - second_score))
        reasons.extend([
            f"volatile_score={volatile_score}",
            f"btc_shock_ratio={btc_shock_ratio:.2f}",
            f"wick_instability={wick_instability:.2f}",
            f"breakout_failure_ratio={breakout_failure_ratio:.2f}",
            f"atr_pct={atr_pct:.4f}",
        ])
    elif trend_ready and trend_score >= max(range_score, volatile_score) and (trend_score - second_score >= 2 or trend_decisive):
        candidate = REGIME_TREND
        confidence = min(0.99, 0.40 + 0.07 * trend_score + 0.03 * max(0, trend_score - second_score))
        reasons.extend([
            f"trend_score={trend_score}",
            f"adx={adx:.2f}",
            f"choppiness={chop:.2f}",
            f"efficiency_ratio={efficiency:.2f}",
            f"ema_stack_alignment={ema_stack_alignment:.2f}",
        ])
    elif range_ready and range_score >= max(trend_score, volatile_score) and (range_score - second_score >= 2 or range_decisive):
        candidate = REGIME_RANGE
        confidence = min(0.99, 0.40 + 0.07 * range_score + 0.03 * max(0, range_score - second_score))
        reasons.extend([
            f"range_score={range_score}",
            f"adx={adx:.2f}",
            f"choppiness={chop:.2f}",
            f"efficiency_ratio={efficiency:.2f}",
            f"distance_to_vwap_atr={distance_to_vwap_atr:.2f}",
        ])
    elif best_score >= 3 and (best_score - second_score) >= 1:
        candidate = best_regime
        confidence = min(0.78, 0.31 + 0.07 * best_score + 0.02 * max(0, best_score - second_score))
        reasons.extend([
            f"soft_classification={best_regime.lower()}",
            f"scores trend={trend_score} volatile={volatile_score} range={range_score}",
            f"adx={adx:.2f}",
            f"choppiness={chop:.2f}",
            f"efficiency_ratio={efficiency:.2f}",
            f"distance_to_vwap_atr={distance_to_vwap_atr:.2f}",
        ])
    else:
        candidate = REGIME_UNKNOWN
        confidence = 0.20
        reasons.extend([
            f"mixed_scores trend={trend_score} volatile={volatile_score} range={range_score}",
            f"adx={adx:.2f}",
            f"choppiness={chop:.2f}",
            f"efficiency_ratio={efficiency:.2f}",
            f"ema_stack_alignment={ema_stack_alignment:.2f}",
            f"distance_to_vwap_atr={distance_to_vwap_atr:.2f}",
        ])

    return {
        "candidate_regime": candidate,
        "confidence": confidence,
        "reasons": reasons,
        "scores": scores,
        "regime_bias": trend_bias,
    }



def detect_regime(
    symbol: str,
    market_context: Optional[Dict[str, Any]] = None,
    btc_context: Optional[Dict[str, Any]] = None,
    previous_state: Optional[Dict[str, Any]] = None,
    *,
    interval: str = TF_5M,
) -> Dict[str, Any]:
    if market_context is None or btc_context is None:
        from app.market_context import build_market_context

    ctx = market_context or build_market_context(symbol, interval=interval)
    btc_ctx = btc_context or build_market_context("BTC", interval=interval)
    features = build_regime_features(symbol, market_context=ctx, btc_context=btc_ctx, interval=interval)
    candidate = classify_candidate_regime(features)

    tf_ctx = ((ctx.get("timeframes") or {}).get(interval) if isinstance(ctx.get("timeframes"), dict) else None) or {}
    last_close_t = int(tf_ctx.get("last_close_t") or 0)
    last_t = int(tf_ctx.get("last_t") or 0)
    bar_id = f"{interval}:{last_close_t or last_t}" if (last_close_t or last_t) else ""

    state = advance_regime_state(
        candidate_regime=str(candidate["candidate_regime"]),
        previous_state=previous_state,
        confirm_bars=_env_int("REGIME_CONFIRM_BARS", DEFAULT_CONFIRM_BARS),
        cooldown_bars=_env_int("REGIME_COOLDOWN_BARS", DEFAULT_COOLDOWN_BARS),
        min_active_bars=_env_int("REGIME_MIN_ACTIVE_BARS", DEFAULT_MIN_ACTIVE_BARS),
        bar_id=bar_id,
    )

    return {
        "symbol": symbol,
        "interval": interval,
        "detector_version": DETECTOR_VERSION,
        "features": features,
        "candidate_regime": candidate["candidate_regime"],
        "candidate_confidence": candidate["confidence"],
        "candidate_reasons": candidate["reasons"],
        "candidate_scores": candidate["scores"],
        "regime_bias": candidate["regime_bias"],
        "state": state,
        "active_regime": state.get("active_regime", REGIME_UNKNOWN),
        "changed": bool(state.get("changed")),
        "bar_id": bar_id,
        "duplicate_bar": bool(state.get("duplicate_bar")),
    }


__all__ = [
    "DETECTOR_VERSION",
    "REGIME_RANGE",
    "REGIME_TREND",
    "REGIME_UNKNOWN",
    "REGIME_VOLATILE",
    "classify_candidate_regime",
    "detect_regime",
]
