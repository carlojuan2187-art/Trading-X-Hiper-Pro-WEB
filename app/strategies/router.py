from __future__ import annotations

from app.settings_utils import env_bool

import os
import time
import threading
from typing import Any, Dict, Optional

from app.market_context import build_market_context
from app.config import (
    STRATEGY_ROUTER_MODE,
    STRATEGY_ROUTER_USE_CONFIDENT_CANDIDATE,
    STRATEGY_ROUTER_ALLOW_RANGE_CANDIDATE,
    STRATEGY_ROUTER_ALLOW_TREND_CANDIDATE_BREAKOUT,
    STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED,
)
from app.regime.detector import (
    DETECTOR_VERSION,
    REGIME_RANGE,
    REGIME_TREND,
    REGIME_UNKNOWN,
    REGIME_VOLATILE,
    detect_regime,
)
from app.strategies.registry import DEFAULT_STRATEGY_ID, get_strategy_registry


_ROUTER_INTERVAL = os.getenv("STRATEGY_ROUTER_INTERVAL", "5m").strip() or "5m"
_ROUTER_MODE = STRATEGY_ROUTER_MODE
_ROUTER_USE_CANDIDATE_WHEN_UNKNOWN = os.getenv("STRATEGY_ROUTER_USE_CANDIDATE_WHEN_UNKNOWN", "0").strip().lower() not in {"0", "false", "no", "off"}
_ROUTER_BLOCK_UNKNOWN_NO_TRADE = os.getenv("STRATEGY_ROUTER_BLOCK_UNKNOWN_NO_TRADE", "1").strip().lower() not in {"0", "false", "no", "off"}
_ROUTER_BLOCK_UNSUPPORTED_NO_TRADE = os.getenv("STRATEGY_ROUTER_BLOCK_UNSUPPORTED_NO_TRADE", "1").strip().lower() not in {"0", "false", "no", "off"}
_ROUTER_STATE_TTL_SECONDS = max(int(os.getenv("STRATEGY_ROUTER_STATE_TTL_SECONDS", "21600") or 21600), 300)
_ROUTER_RANGE_SHADOW_ENABLED = os.getenv("STRATEGY_ROUTER_RANGE_SHADOW_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
_ROUTER_RANGE_SHADOW_ALWAYS = os.getenv("STRATEGY_ROUTER_RANGE_SHADOW_ALWAYS", "0").strip().lower() in {"1", "true", "yes", "on"}
_ROUTER_BTC_CONTEXT_TTL_SECONDS = max(float(os.getenv("STRATEGY_ROUTER_BTC_CONTEXT_TTL_SECONDS", "4.0") or 4.0), 0.0)
_ROUTER_ALLOW_DEGRADED_NO_BTC = os.getenv("STRATEGY_ROUTER_ALLOW_DEGRADED_NO_BTC", "1").strip().lower() not in {"0", "false", "no", "off"}

_ROUTER_CANDIDATE_CONFIDENCE_MIN = max(float(os.getenv("STRATEGY_ROUTER_CANDIDATE_CONFIDENCE_MIN", "0.52") or 0.52), 0.0)
_ROUTER_CANDIDATE_SCORE_MIN = max(int(os.getenv("STRATEGY_ROUTER_CANDIDATE_SCORE_MIN", "3") or 3), 1)
_ROUTER_ALLOW_CONFIDENT_CANDIDATE = STRATEGY_ROUTER_USE_CONFIDENT_CANDIDATE
_ROUTER_ALLOW_RANGE_CANDIDATE = STRATEGY_ROUTER_ALLOW_RANGE_CANDIDATE
_ROUTER_ALLOW_TREND_CANDIDATE_BREAKOUT = STRATEGY_ROUTER_ALLOW_TREND_CANDIDATE_BREAKOUT
_ROUTER_TREND_CANDIDATE_CONFIDENCE_MIN = max(float(os.getenv("STRATEGY_ROUTER_TREND_CANDIDATE_CONFIDENCE_MIN", "0.44") or 0.44), 0.0)
_ROUTER_TREND_CANDIDATE_SCORE_MIN = max(int(os.getenv("STRATEGY_ROUTER_TREND_CANDIDATE_SCORE_MIN", "2") or 2), 1)
_ROUTER_LIQUIDITY_PROBE_ENABLED = STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED
_ROUTER_LIQUIDITY_PROBE_MIN_VOL_SCORE = max(int(os.getenv("STRATEGY_ROUTER_LIQUIDITY_PROBE_MIN_VOL_SCORE", "3") or 3), 1)
_ROUTER_LIQUIDITY_PROBE_MIN_CONFIDENCE = max(float(os.getenv("STRATEGY_ROUTER_LIQUIDITY_PROBE_MIN_CONFIDENCE", "0.42") or 0.42), 0.0)
_ROUTER_LIQUIDITY_PROBE_MIN_SIGNAL_SCORE = max(float(os.getenv("STRATEGY_ROUTER_LIQUIDITY_PROBE_MIN_SIGNAL_SCORE", "74.0") or 74.0), 0.0)


_SUPPORTED_ROUTER_MODES = {"observe_only", "enforced"}
if _ROUTER_MODE not in _SUPPORTED_ROUTER_MODES:
    _ROUTER_MODE = "observe_only"


class StrategyRouter:
    """Router central de estrategias basado en régimen.

    Fase 4: integra detector + registro de estrategias sin romper el contrato legacy.
    - `observe_only`: detecta régimen y adjunta metadata, pero sigue evaluando la
      estrategia por defecto si no existe todavía una estrategia especializada.
    - `enforced`: solo permite la estrategia mapeada al régimen activo.
    """

    def __init__(self) -> None:
        self._registry = get_strategy_registry()
        self._state_lock = threading.Lock()
        self._symbol_state: Dict[str, Dict[str, Any]] = {}
        self._btc_context_cache: Optional[Dict[str, Any]] = None
        self._btc_context_cache_ts: float = 0.0
        self._regime_strategy_map: Dict[str, str] = {
            REGIME_TREND: DEFAULT_STRATEGY_ID,
            REGIME_VOLATILE: "liquidity_sweep_reversal",
            # RANGE permanece en shadow mode durante Fase 6.
        }
        if env_bool('STRATEGY_ROUTER_RANGE_LIVE_ENABLED', False):
            self._regime_strategy_map[REGIME_RANGE] = 'range_mean_reversion'
        self._range_shadow_strategy_id = "range_mean_reversion"

    @property
    def mode(self) -> str:
        return _ROUTER_MODE

    def _purge_expired_state(self, now_ts: float) -> None:
        expired = [
            symbol
            for symbol, payload in self._symbol_state.items()
            if now_ts - float((payload or {}).get("updated_at_ts", 0.0) or 0.0) > float(_ROUTER_STATE_TTL_SECONDS)
        ]
        for symbol in expired:
            self._symbol_state.pop(symbol, None)

    def _get_previous_regime_state(self, symbol: str) -> Optional[Dict[str, Any]]:
        now_ts = time.time()
        with self._state_lock:
            self._purge_expired_state(now_ts)
            payload = self._symbol_state.get(symbol)
            if not isinstance(payload, dict):
                return None
            state = payload.get("state")
            return dict(state) if isinstance(state, dict) else None

    def _remember_regime_state(self, symbol: str, state: Optional[Dict[str, Any]]) -> None:
        if not isinstance(state, dict):
            return
        now_ts = time.time()
        with self._state_lock:
            self._purge_expired_state(now_ts)
            self._symbol_state[symbol] = {
                "state": dict(state),
                "updated_at_ts": now_ts,
            }

    def _build_context(self, symbol: str) -> Dict[str, Any]:
        # Import local para mantener una sola fuente de verdad de parámetros.
        from app.strategies.breakout_reset import (
            ADX_PERIOD,
            ATR_PERIOD,
            EMA_FAST,
            EMA_MID,
            EMA_SLOW,
            LOOKBACK_5M,
            TF_5M,
        )

        interval = TF_5M or _ROUTER_INTERVAL
        return build_market_context(
            symbol=symbol,
            interval=interval,
            limit=LOOKBACK_5M,
            ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
            adx_period=ADX_PERIOD,
            atr_period=ATR_PERIOD,
        )


    def _get_btc_context(self, interval: str) -> Dict[str, Any]:
        now_ts = time.time()
        with self._state_lock:
            cached = self._btc_context_cache if isinstance(self._btc_context_cache, dict) else None
            if cached and (now_ts - float(self._btc_context_cache_ts or 0.0)) <= float(_ROUTER_BTC_CONTEXT_TTL_SECONDS):
                return cached
        btc_ctx = self._build_context("BTC")
        with self._state_lock:
            self._btc_context_cache = btc_ctx
            self._btc_context_cache_ts = now_ts
        return btc_ctx

    def _is_confident_candidate(self, regime_result: Dict[str, Any]) -> bool:
        candidate_regime = str(regime_result.get("candidate_regime") or REGIME_UNKNOWN)
        if candidate_regime == REGIME_UNKNOWN:
            return False
        if candidate_regime == REGIME_RANGE and not _ROUTER_ALLOW_RANGE_CANDIDATE:
            return False

        confidence = float(regime_result.get("candidate_confidence") or 0.0)
        scores = regime_result.get("candidate_scores") if isinstance(regime_result.get("candidate_scores"), dict) else {}
        best_score = float(scores.get(candidate_regime) or 0.0)
        if confidence < _ROUTER_CANDIDATE_CONFIDENCE_MIN or best_score < _ROUTER_CANDIDATE_SCORE_MIN:
            return False

        features = regime_result.get("features") if isinstance(regime_result.get("features"), dict) else {}
        adx = float(features.get("adx") or 0.0)
        chop = float(features.get("choppiness") or 50.0)
        efficiency = float(features.get("efficiency_ratio") or 0.0)
        ema_align = float(features.get("ema_stack_alignment") or 0.0)
        btc_shock = float(features.get("btc_shock_ratio") or 0.0)
        wick_instability = float(features.get("wick_instability") or 0.0)
        breakout_failure_ratio = float(features.get("breakout_failure_ratio") or 0.0)
        distance_to_vwap_atr = float(features.get("distance_to_vwap_atr") or 0.0)

        if candidate_regime == REGIME_TREND:
            return (
                confidence >= max(_ROUTER_CANDIDATE_CONFIDENCE_MIN, 0.50)
                and (adx >= 13.5 or ema_align >= 0.52)
                and efficiency >= 0.18
                and chop <= 60.0
            )
        if candidate_regime == REGIME_VOLATILE:
            return (
                confidence >= max(_ROUTER_CANDIDATE_CONFIDENCE_MIN + 0.04, 0.58)
                and (btc_shock >= 1.35 or wick_instability >= 0.50 or breakout_failure_ratio >= 0.24)
            )
        if candidate_regime == REGIME_RANGE:
            return (
                confidence >= max(_ROUTER_CANDIDATE_CONFIDENCE_MIN, 0.50)
                and chop >= 49.0
                and efficiency <= 0.42
                and distance_to_vwap_atr <= 1.45
            )
        return False

    def _is_breakout_trend_candidate(self, regime_result: Dict[str, Any]) -> bool:
        if not _ROUTER_ALLOW_TREND_CANDIDATE_BREAKOUT:
            return False
        candidate_regime = str(regime_result.get("candidate_regime") or REGIME_UNKNOWN)
        if candidate_regime != REGIME_TREND:
            return False

        confidence = float(regime_result.get("candidate_confidence") or 0.0)
        scores = regime_result.get("candidate_scores") if isinstance(regime_result.get("candidate_scores"), dict) else {}
        trend_score = float(scores.get(REGIME_TREND) or 0.0)
        if confidence < _ROUTER_TREND_CANDIDATE_CONFIDENCE_MIN or trend_score < _ROUTER_TREND_CANDIDATE_SCORE_MIN:
            return False

        features = regime_result.get("features") if isinstance(regime_result.get("features"), dict) else {}
        adx = float(features.get("adx") or 0.0)
        chop = float(features.get("choppiness") or 50.0)
        efficiency = float(features.get("efficiency_ratio") or 0.0)
        ema_align = float(features.get("ema_stack_alignment") or 0.0)
        breakout_failure_ratio = float(features.get("breakout_failure_ratio") or 0.0)
        body_quality = float(features.get("body_quality") or 0.0)
        # Puerta de entrada más permisiva que el régimen activo, pero sin abrir basura.
        return (
            (adx >= 11.5 or ema_align >= 0.44)
            and chop <= 63.0
            and efficiency >= 0.12
            and breakout_failure_ratio <= 0.34
            and body_quality >= 0.24
        )

    def _should_probe_liquidity(self, regime_result: Dict[str, Any], resolved_regime: str) -> bool:
        if not _ROUTER_LIQUIDITY_PROBE_ENABLED:
            return False
        if env_bool("STRATEGY_ROUTER_SETUP_FIRST_ENABLED", True) and resolved_regime in {REGIME_TREND, REGIME_RANGE, REGIME_VOLATILE}:
            return True
        if resolved_regime == REGIME_VOLATILE:
            return True
        if resolved_regime != REGIME_TREND:
            return False

        candidate_regime = str(regime_result.get("candidate_regime") or REGIME_UNKNOWN)
        confidence = float(regime_result.get("candidate_confidence") or 0.0)
        scores = regime_result.get("candidate_scores") if isinstance(regime_result.get("candidate_scores"), dict) else {}
        vol_score = float(scores.get(REGIME_VOLATILE) or 0.0)
        features = regime_result.get("features") if isinstance(regime_result.get("features"), dict) else {}
        wick_instability = float(features.get("wick_instability") or 0.0)
        breakout_failure_ratio = float(features.get("breakout_failure_ratio") or 0.0)
        btc_shock = float(features.get("btc_shock_ratio") or 0.0)
        atr_pct = float(features.get("atr_pct") or 0.0)

        if candidate_regime == REGIME_VOLATILE and confidence >= _ROUTER_LIQUIDITY_PROBE_MIN_CONFIDENCE:
            return True

        return (
            vol_score >= _ROUTER_LIQUIDITY_PROBE_MIN_VOL_SCORE
            and (
                wick_instability >= 0.46
                or breakout_failure_ratio >= 0.18
                or (btc_shock >= 1.05 and atr_pct >= 0.0055)
            )
        )

    def _evaluate_strategy(self, strategy_id: str, symbol: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        strategy = self._registry.get(strategy_id)
        out = strategy.evaluate(symbol, market_context=ctx)
        if not isinstance(out, dict):
            return {
                "signal": False,
                "reason": "ROUTER_INVALID_STRATEGY_OUTPUT",
                "coin": symbol,
                "strategy_id": strategy_id,
                "strategy_model": getattr(strategy, "strategy_model", strategy_id),
                "strategy_version": getattr(strategy, "strategy_version", "v0"),
            }
        out.setdefault("strategy_id", getattr(strategy, "strategy_id", strategy_id))
        out.setdefault("strategy_version", getattr(strategy, "strategy_version", "v0"))
        out.setdefault("strategy_model", getattr(strategy, "strategy_model", strategy_id))
        return out

    def _context_failure_reason(self, ctx: Dict[str, Any], interval: str, *, prefix: str) -> Optional[str]:
        tf = ((ctx or {}).get("timeframes") or {}).get(interval) or {}
        status = str(tf.get("status") or (ctx or {}).get("status") or "UNKNOWN")
        detail = str(tf.get("detail") or (ctx or {}).get("detail") or status)
        if status == "OK" and not bool(tf.get("stale", False)) and bool(tf.get("context_ok", True)):
            return None
        return f"{prefix}:{detail}"

    def _build_data_unavailable_signal(self, symbol: str, *, ctx: Dict[str, Any], btc_ctx: Dict[str, Any], reason: str) -> Dict[str, Any]:
        interval = _ROUTER_INTERVAL
        symbol_detail = self._context_failure_reason(ctx, interval, prefix="symbol")
        btc_detail = self._context_failure_reason(btc_ctx, interval, prefix="btc")
        detail_parts = [reason]
        if symbol_detail:
            detail_parts.append(symbol_detail)
        if btc_detail:
            detail_parts.append(btc_detail)
        return {
            "signal": False,
            "reason": reason,
            "coin": symbol,
            "strategy_id": "none",
            "strategy_model": "none",
            "strategy_version": "router",
            "regime_id": REGIME_UNKNOWN,
            "regime_version": DETECTOR_VERSION,
            "detector_version": DETECTOR_VERSION,
            "router_mode": self.mode,
            "router_decision": "data_unavailable",
            "router_reason": reason,
            "router_reason_detail": "|".join(detail_parts),
            "router_candidate_regime": REGIME_UNKNOWN,
            "router_candidate_confidence": 0.0,
            "router_candidate_scores": {},
            "router_regime_source": "data_unavailable",
            "regime_context": {
                "candidate_regime": REGIME_UNKNOWN,
                "active_regime": REGIME_UNKNOWN,
                "candidate_confidence": 0.0,
                "changed": False,
                "reasons": detail_parts,
                "scores": {},
                "bias": "neutral",
                "state": {},
                "feature_summary": {},
            },
            "market_context_status": str((ctx or {}).get("status") or "UNKNOWN"),
            "btc_context_status": str((btc_ctx or {}).get("status") or "UNKNOWN"),
        }

    def _build_router_reason_detail(self, regime_result: Dict[str, Any], *, resolved_regime: str, regime_source: str, reason: str) -> str:
        features = regime_result.get("features") if isinstance(regime_result.get("features"), dict) else {}
        scores = regime_result.get("candidate_scores") if isinstance(regime_result.get("candidate_scores"), dict) else {}
        candidate_regime = str(regime_result.get("candidate_regime") or REGIME_UNKNOWN)
        active_regime = str(regime_result.get("active_regime") or REGIME_UNKNOWN)
        confidence = float(regime_result.get("candidate_confidence") or 0.0)
        adx = float(features.get("adx") or 0.0)
        chop = float(features.get("choppiness") or 0.0)
        eff = float(features.get("efficiency_ratio") or 0.0)
        ema = float(features.get("ema_stack_alignment") or 0.0)
        vwap = float(features.get("distance_to_vwap_atr") or 0.0)
        btc = float(features.get("btc_shock_ratio") or 0.0)
        context_status = str(features.get("context_status") or "UNKNOWN")
        btc_context_status = str(features.get("btc_context_status") or "UNKNOWN")
        trend_score = float(scores.get(REGIME_TREND) or 0.0)
        vol_score = float(scores.get(REGIME_VOLATILE) or 0.0)
        range_score = float(scores.get(REGIME_RANGE) or 0.0)
        return (
            f"{reason}|src={regime_source}|active={active_regime}|candidate={candidate_regime}|conf={confidence:.2f}|"
            f"scores=t{trend_score:.0f}/v{vol_score:.0f}/r{range_score:.0f}|adx={adx:.1f}|chop={chop:.1f}|"
            f"eff={eff:.2f}|ema={ema:.2f}|vwap={vwap:.2f}|btc={btc:.2f}|ctx={context_status}|btc_ctx={btc_context_status}|resolved={resolved_regime}"
        )

    def _resolve_regime_for_routing(self, regime_result: Dict[str, Any]) -> tuple[str, str]:
        active_regime = str(regime_result.get("active_regime") or REGIME_UNKNOWN)
        candidate_regime = str(regime_result.get("candidate_regime") or REGIME_UNKNOWN)
        if active_regime != REGIME_UNKNOWN:
            return active_regime, "active"
        if self._is_breakout_trend_candidate(regime_result):
            return REGIME_TREND, "trend_candidate_breakout"
        if _ROUTER_ALLOW_CONFIDENT_CANDIDATE and self._is_confident_candidate(regime_result):
            return candidate_regime, "confident_candidate"
        if _ROUTER_USE_CANDIDATE_WHEN_UNKNOWN and candidate_regime != REGIME_UNKNOWN:
            return candidate_regime, "candidate_fallback"
        return REGIME_UNKNOWN, "unknown"

    def _regime_summary(self, regime_result: Dict[str, Any]) -> Dict[str, Any]:
        features = regime_result.get("features") if isinstance(regime_result.get("features"), dict) else {}
        state = regime_result.get("state") if isinstance(regime_result.get("state"), dict) else {}
        return {
            "candidate_regime": str(regime_result.get("candidate_regime") or REGIME_UNKNOWN),
            "active_regime": str(regime_result.get("active_regime") or REGIME_UNKNOWN),
            "candidate_confidence": float(regime_result.get("candidate_confidence") or 0.0),
            "changed": bool(regime_result.get("changed")),
            "reasons": list(regime_result.get("candidate_reasons") or []),
            "scores": dict(regime_result.get("candidate_scores") or {}),
            "bias": str(regime_result.get("regime_bias") or "neutral"),
            "state": dict(state),
            "feature_summary": {
                "adx": float(features.get("adx") or 0.0),
                "choppiness": float(features.get("choppiness") or 0.0),
                "efficiency_ratio": float(features.get("efficiency_ratio") or 0.0),
                "atr_pct": float(features.get("atr_pct") or 0.0),
                "wick_instability": float(features.get("wick_instability") or 0.0),
                "breakout_failure_ratio": float(features.get("breakout_failure_ratio") or 0.0),
                "btc_shock_ratio": float(features.get("btc_shock_ratio") or 0.0),
                "distance_to_vwap_atr": float(features.get("distance_to_vwap_atr") or 0.0),
                "ema_stack_alignment": float(features.get("ema_stack_alignment") or 0.0),
                "body_quality": float(features.get("body_quality") or 0.0),
            },
        }


    def _shadow_should_run(self, regime_result: Dict[str, Any], resolved_regime: str) -> bool:
        if not _ROUTER_RANGE_SHADOW_ENABLED:
            return False
        if _ROUTER_RANGE_SHADOW_ALWAYS:
            return True
        candidate_regime = str(regime_result.get("candidate_regime") or REGIME_UNKNOWN)
        active_regime = str(regime_result.get("active_regime") or REGIME_UNKNOWN)
        if resolved_regime == REGIME_RANGE or candidate_regime == REGIME_RANGE or active_regime == REGIME_RANGE:
            return True
        scores = regime_result.get("candidate_scores") if isinstance(regime_result.get("candidate_scores"), dict) else {}
        try:
            return float(scores.get(REGIME_RANGE) or 0.0) >= 3.0
        except Exception:
            return False

    def _shadow_summary(
        self,
        *,
        symbol: str,
        ctx: Dict[str, Any],
        regime_result: Dict[str, Any],
        resolved_regime: str,
        regime_source: str,
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "enabled": bool(_ROUTER_RANGE_SHADOW_ENABLED),
            "strategy_id": self._range_shadow_strategy_id,
            "strategy_mode": "shadow_only",
            "evaluated": False,
            "signal": False,
            "reason": "SHADOW_DISABLED" if not _ROUTER_RANGE_SHADOW_ENABLED else "REGIME_GATE_NOT_MET",
            "regime_gate": resolved_regime,
            "regime_source": regime_source,
        }
        if not self._shadow_should_run(regime_result, resolved_regime):
            return summary
        summary["evaluated"] = True
        try:
            strategy = self._registry.get(self._range_shadow_strategy_id)
            out = strategy.evaluate(symbol, market_context=ctx)
        except Exception as e:
            summary["reason"] = f"SHADOW_EXCEPTION:{type(e).__name__}"
            return summary

        if not isinstance(out, dict):
            summary["reason"] = "SHADOW_INVALID_OUTPUT"
            return summary

        summary.update({
            "signal": bool(out.get("signal")),
            "reason": str(out.get("reason") or ("SHADOW_SIGNAL" if out.get("signal") else "SHADOW_NO_SIGNAL")),
            "direction": str(out.get("direction") or "").lower(),
            "strength": float(out.get("strength") or 0.0),
            "score": float(out.get("score") or 0.0),
            "strategy_version": str(out.get("strategy_version") or getattr(strategy, "strategy_version", "v0")),
            "strategy_model": str(out.get("strategy_model") or getattr(strategy, "strategy_model", self._range_shadow_strategy_id)),
        })
        meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
        diag = meta.get("diagnostics") if isinstance(meta.get("diagnostics"), dict) else {}
        if meta:
            summary["meta"] = dict(meta)
        if diag:
            summary["diagnostics"] = dict(diag)
        return summary

    def _attach_shadow_metadata(
        self,
        *,
        out: Dict[str, Any],
        symbol: str,
        ctx: Dict[str, Any],
        regime_result: Dict[str, Any],
        resolved_regime: str,
        regime_source: str,
    ) -> Dict[str, Any]:
        shadow_range = self._shadow_summary(
            symbol=symbol,
            ctx=ctx,
            regime_result=regime_result,
            resolved_regime=resolved_regime,
            regime_source=regime_source,
        )
        out["shadow_range"] = shadow_range
        out["shadow_strategy_id"] = str(shadow_range.get("strategy_id") or self._range_shadow_strategy_id)
        out["shadow_signal"] = bool(shadow_range.get("signal"))
        out["shadow_score"] = float(shadow_range.get("score") or 0.0)
        out["shadow_direction"] = str(shadow_range.get("direction") or "").lower()
        out.setdefault("market_context_status", str(ctx.get("status") or "UNKNOWN"))
        return out

    def _blocked_signal(self, symbol: str, *, resolved_regime: str, regime_source: str, regime_result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        detail = self._build_router_reason_detail(
            regime_result,
            resolved_regime=resolved_regime,
            regime_source=regime_source,
            reason=reason,
        )
        summary = self._regime_summary(regime_result)
        return {
            "signal": False,
            "reason": reason,
            "coin": symbol,
            "strategy_id": "none",
            "strategy_model": "none",
            "strategy_version": "router",
            "regime_id": resolved_regime,
            "regime_version": DETECTOR_VERSION,
            "detector_version": DETECTOR_VERSION,
            "router_mode": self.mode,
            "router_decision": "regime_blocked",
            "router_reason": reason,
            "router_reason_detail": detail,
            "router_candidate_regime": str(regime_result.get("candidate_regime") or REGIME_UNKNOWN),
            "router_candidate_confidence": float(regime_result.get("candidate_confidence") or 0.0),
            "router_candidate_scores": dict(regime_result.get("candidate_scores") or {}),
            "router_regime_source": regime_source,
            "btc_context_missing": bool(regime_result.get("btc_context_missing")),
            "regime_context": summary,
        }

    def route_symbol(self, symbol: str, market_context: Optional[Dict[str, Any]] = None, btc_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = self._route_symbol_impl(symbol, market_context, btc_context)
        attempts = out.get("router_considered_strategies") or []
        sweep = next((row for row in attempts if row.get("strategy_id") == "liquidity_sweep_reversal"), None)
        selected = out.get("router_strategy_id") == "liquidity_sweep_reversal"
        if sweep is None:
            status = "PRIMARY_SIGNAL_PRIORITY" if out.get("signal") else "NOT_ROUTED"
        elif not sweep.get("signal"):
            status = "EVALUATED_REJECTED"
        else:
            status = "SELECTED" if selected else "PROBE_SCORE_REJECTED"
        out["liquidity_diagnostics"] = {
            "status": status, "evaluated": sweep is not None,
            "regime": out.get("regime_id"), "router_reason": out.get("reason"),
            "probe_enabled": _ROUTER_LIQUIDITY_PROBE_ENABLED,
            "probe_min_score": _ROUTER_LIQUIDITY_PROBE_MIN_SIGNAL_SCORE,
            "attempt": sweep,
        }
        return out

    def _route_symbol_impl(self, symbol: str, market_context: Optional[Dict[str, Any]] = None, btc_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return {"signal": False, "reason": "BAD_SYMBOL", "router_mode": self.mode}

        ctx = market_context if isinstance(market_context, dict) else self._build_context(symbol)
        previous_state = self._get_previous_regime_state(symbol)
        btc_ctx = btc_context if isinstance(btc_context, dict) else self._get_btc_context(_ROUTER_INTERVAL)

        symbol_data_issue = self._context_failure_reason(ctx, _ROUTER_INTERVAL, prefix="symbol")
        btc_data_issue = self._context_failure_reason(btc_ctx, _ROUTER_INTERVAL, prefix="btc")
        degraded_no_btc = False
        if symbol_data_issue:
            return self._attach_shadow_metadata(
                out=self._build_data_unavailable_signal(symbol, ctx=ctx, btc_ctx=btc_ctx, reason="ROUTER_DATA_UNAVAILABLE"),
                symbol=symbol,
                ctx=ctx,
                regime_result={"candidate_regime": REGIME_UNKNOWN, "active_regime": REGIME_UNKNOWN, "candidate_confidence": 0.0, "candidate_scores": {}, "features": {}, "state": {}, "candidate_reasons": [symbol_data_issue, btc_data_issue] if btc_data_issue else [symbol_data_issue], "regime_bias": "neutral", "changed": False},
                resolved_regime=REGIME_UNKNOWN,
                regime_source="data_unavailable",
            )
        if btc_data_issue and _ROUTER_ALLOW_DEGRADED_NO_BTC:
            degraded_no_btc = True
            btc_ctx = {
                "symbol": "BTC",
                "coin": "BTC",
                "status": "BTC_CONTEXT_MISSING_DEGRADED",
                "detail": btc_data_issue,
                "context_ok": False,
                "timeframes": {},
            }
        elif btc_data_issue:
            return self._attach_shadow_metadata(
                out=self._build_data_unavailable_signal(symbol, ctx=ctx, btc_ctx=btc_ctx, reason="ROUTER_BTC_DATA_UNAVAILABLE"),
                symbol=symbol,
                ctx=ctx,
                regime_result={"candidate_regime": REGIME_UNKNOWN, "active_regime": REGIME_UNKNOWN, "candidate_confidence": 0.0, "candidate_scores": {}, "features": {}, "state": {}, "candidate_reasons": [btc_data_issue], "regime_bias": "neutral", "changed": False},
                resolved_regime=REGIME_UNKNOWN,
                regime_source="data_unavailable",
            )

        regime_result = detect_regime(symbol, market_context=ctx, btc_context=btc_ctx, previous_state=previous_state)
        if degraded_no_btc:
            regime_result["btc_context_missing"] = True
            features = regime_result.get("features") if isinstance(regime_result.get("features"), dict) else {}
            features["btc_context_missing"] = True
            features["btc_context_status"] = str((btc_ctx.get("status") if isinstance(btc_ctx, dict) else "UNKNOWN") or "UNKNOWN")
            features["btc_context_ok"] = False
            reasons = list(regime_result.get("candidate_reasons") or [])
            reasons.append(str(btc_data_issue))
            regime_result["candidate_reasons"] = reasons
        self._remember_regime_state(symbol, regime_result.get("state"))

        resolved_regime, regime_source = self._resolve_regime_for_routing(regime_result)
        mapped_strategy_id = self._regime_strategy_map.get(resolved_regime)
        observe_only = self.mode != "enforced"

        if resolved_regime == REGIME_UNKNOWN and _ROUTER_BLOCK_UNKNOWN_NO_TRADE:
            blocked = self._blocked_signal(
                symbol,
                resolved_regime=resolved_regime,
                regime_source=regime_source,
                regime_result=regime_result,
                reason="ROUTER_UNKNOWN_NO_TRADE",
            )
            return self._attach_shadow_metadata(
                out=blocked,
                symbol=symbol,
                ctx=ctx,
                regime_result=regime_result,
                resolved_regime=resolved_regime,
                regime_source=regime_source,
            )

        range_continuation = (
            resolved_regime == REGIME_RANGE and not mapped_strategy_id
            and env_bool("STRATEGY_ROUTER_RANGE_CONTINUATION_ENABLED", True)
        )
        if range_continuation:
            # RANGE is not sufficient to reject a complete directional setup.
            # MTF still enforces H1/M15, reset, extension, score and structural RR.
            mapped_strategy_id = DEFAULT_STRATEGY_ID

        if not mapped_strategy_id:
            if _ROUTER_BLOCK_UNSUPPORTED_NO_TRADE or (not observe_only):
                reason = "ROUTER_REGIME_NO_TRADE" if resolved_regime == REGIME_RANGE else "ROUTER_REGIME_UNSUPPORTED"
                blocked = self._blocked_signal(
                    symbol,
                    resolved_regime=resolved_regime,
                    regime_source=regime_source,
                    regime_result=regime_result,
                    reason=reason,
                )
                return self._attach_shadow_metadata(
                    out=blocked,
                    symbol=symbol,
                    ctx=ctx,
                    regime_result=regime_result,
                    resolved_regime=resolved_regime,
                    regime_source=regime_source,
                )
            mapped_strategy_id = DEFAULT_STRATEGY_ID
            router_decision = "observe_only_default_strategy"
        else:
            router_decision = "mapped_strategy_selected"

        if range_continuation:
            router_decision = "range_continuation_full_validation"

        primary_strategy_id = mapped_strategy_id
        strategy_ids_to_consider = [primary_strategy_id]
        if primary_strategy_id == DEFAULT_STRATEGY_ID and self._should_probe_liquidity(regime_result, resolved_regime):
            strategy_ids_to_consider.append("liquidity_sweep_reversal")
        if (env_bool("STRATEGY_ROUTER_SETUP_FIRST_ENABLED", True)
                and primary_strategy_id == "liquidity_sweep_reversal"):
            strategy_ids_to_consider.append(DEFAULT_STRATEGY_ID)

        attempted: list[Dict[str, Any]] = []
        primary_out: Optional[Dict[str, Any]] = None
        out: Optional[Dict[str, Any]] = None
        selected_strategy_id = primary_strategy_id
        selected_router_decision = router_decision

        for idx, strategy_id in enumerate(strategy_ids_to_consider):
            current_out = self._evaluate_strategy(strategy_id, symbol, ctx)
            attempted.append({
                "strategy_id": strategy_id,
                "signal": bool(current_out.get("signal")),
                "reason": str(current_out.get("reason") or ""),
                "score": float(current_out.get("score") or 0.0),
                "diag": current_out.get("diag") or {},
            })
            if idx == 0:
                primary_out = current_out
                if bool(current_out.get("signal")):
                    out = current_out
                    break
                continue

            required_score = (_ROUTER_LIQUIDITY_PROBE_MIN_SIGNAL_SCORE
                              if strategy_id == "liquidity_sweep_reversal" else 0.0)
            if bool(current_out.get("signal")) and float(current_out.get("score") or 0.0) >= required_score:
                out = current_out
                selected_strategy_id = strategy_id
                selected_router_decision = ("trend_liquidity_probe_selected"
                    if strategy_id == "liquidity_sweep_reversal" else "volatile_continuation_full_validation")
                break

        if out is None:
            out = primary_out or {"signal": False, "reason": "ROUTER_INVALID_STRATEGY_OUTPUT", "coin": symbol, "strategy_id": primary_strategy_id}

        out["router_mode"] = self.mode
        out["router_decision"] = selected_router_decision
        out["router_regime_source"] = regime_source
        out["router_strategy_id"] = selected_strategy_id
        out["router_considered_strategies"] = attempted
        out["regime_id"] = resolved_regime
        out["regime_version"] = DETECTOR_VERSION
        out["detector_version"] = DETECTOR_VERSION
        out["regime_context"] = self._regime_summary(regime_result)
        out["btc_context_missing"] = bool(regime_result.get("btc_context_missing"))
        if out["btc_context_missing"]:
            out["router_decision"] = f"{selected_router_decision}_degraded_no_btc"
        return self._attach_shadow_metadata(
            out=out,
            symbol=symbol,
            ctx=ctx,
            regime_result=regime_result,
            resolved_regime=resolved_regime,
            regime_source=regime_source,
        )


_ROUTER = StrategyRouter()


def get_strategy_router() -> StrategyRouter:
    return _ROUTER


__all__ = [
    "StrategyRouter",
    "get_strategy_router",
]
