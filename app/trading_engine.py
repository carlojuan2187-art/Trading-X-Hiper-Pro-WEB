from app.trade_notifications import opening_evidence
# ============================================================
# TRADING ENGINE – Trading X Hyper Pro
# PRODUCCIÓN REAL – BANK GRADE
# SL + TP FIJOS DESDE LA APERTURA
# Cuenta trades SOLO si hubo FILL real
# FIX:
#   - 1 trade a la vez por usuario (LOCK)
#   - No abre si ya hay posición abierta en el exchange
#   - Sizing: usa el balance REAL del exchange (withdrawable) como capital operativo; interés compuesto natural
#   - ✅ NO CIERRA POR TIEMPO: la salida queda gobernada por TP/SL del exchange
#   - ✅ TP y SL se fijan al abrir y NO se mueven durante la operación
#   - ✅ Break-even/trailing dinámico desactivado en el motor
#
# FIX CLAVE (ESTE PATCH):
#   - Usa entry_price REAL del fill si viene en open_resp
#   - Cierres por %PnL (pnl_pct), NO por "precio vs precio"
# ============================================================

import time
import os
import json
import math
import threading
import traceback
from datetime import datetime, timedelta, date, timezone
from typing import Any, Optional
from collections import deque, Counter
from app.emergency_exit import close_verified
from app.exit_guard import exit_guard_reason
from app.settings_utils import env_bool
from app.hyperliquid_client import get_best_bid_ask

SOFTWARE_EXIT_GUARD_ENABLED = env_bool("SOFTWARE_EXIT_GUARD_ENABLED", True)

from app.market_scanner import get_ranked_symbols, mark_symbol_recent, mark_symbol_evaluated
from app.market_context import build_market_context
from app.strategy import (
    get_entry_signal,
    get_entry_signal_for_strategy,
    get_trade_management_params,
    get_trade_management_params_for_strategy,
)
from app.risk import validate_trade_conditions
from app.hyperliquid_client import place_market_order, place_stop_loss, place_take_profit, place_position_tpsl_pair, cancel_orders, cancel_all_orders_for_symbol, cancel_reduce_only_triggers_for_symbol_side, get_price, get_balance, has_open_position, get_position_entry_price, get_open_position_size, get_position_size_verified, make_request, get_last_closed_trade_snapshot, get_account_snapshot, get_asset_index, get_sz_decimals, get_exchange_min_order_notional_usdc

from app.database import (
    register_trade,
    add_daily_admin_fee,
    add_weekly_ref_fee,
    get_user_referrer,
    get_user_wallet,
    save_last_close,
)
import app.database as database_module

from app.config import (
    OWNER_FEE_PERCENT,
    REFERRAL_FEE_PERCENT,
    SCANNER_SHORTLIST_DEPTH_FOR_L2,
    LEVERAGE,
    MIN_CAPITAL_USDC,
    MIN_ENTRY_RR,
    EXECUTION_IOC_MAX_SLIPPAGE_BPS,
    ENTRY_MAX_DRIFT_PCT,
    ENTRY_MAX_DRIFT_ATR_MULT,
    ENTRY_MAX_DRIFT_HARD_PCT,
    ENTRY_MAX_ADVERSE_DRIFT_MULT,
    USER_RISK_MAX_CONSEC_LOSSES,
    USER_RISK_COOLDOWN_SECONDS,
    USER_RISK_LOSS_COOLDOWN_SECONDS,
    USER_RISK_MIN_PF,
    USER_RISK_PF_WINDOW,
    USER_RISK_PF_COOLDOWN_SECONDS,
    GLOBAL_RISK_MAX_CONSEC_LOSSES,
    GLOBAL_RISK_COOLDOWN_SECONDS,
    GLOBAL_RISK_MIN_PF,
    GLOBAL_RISK_PF_WINDOW,
    GLOBAL_RISK_PF_COOLDOWN_SECONDS,
    MAX_TRADES_PER_HOUR,
    MAX_TRADES_PER_DAY,
)
from app.trading_quality import (
    absolute_risk_reward,
    meets_min_rr,
    rr_entry_price_limit,
    adaptive_entry_drift_limit,
    evaluate_entry_drift,
    levels_to_percentages,
)


TRADE_PLAN_SCHEMA_VERSION = os.getenv("TRADE_PLAN_SCHEMA_VERSION", "phase0_v1").strip() or "phase0_v1"
DEFAULT_STRATEGY_ID = os.getenv("DEFAULT_STRATEGY_ID", "legacy_breakout_reset").strip() or "legacy_breakout_reset"
DEFAULT_REGIME_ID = os.getenv("DEFAULT_REGIME_ID", "legacy_single_strategy").strip() or "legacy_single_strategy"
DEFAULT_DETECTOR_VERSION = os.getenv("DEFAULT_DETECTOR_VERSION", "not_applicable").strip() or "not_applicable"


SHORTLIST_EVAL_BUDGET_SECONDS = max(float(os.getenv("SHORTLIST_EVAL_BUDGET_SECONDS", "32") or 32), 8.0)
SHORTLIST_EVAL_MIN_CANDIDATES = max(int(os.getenv("SHORTLIST_EVAL_MIN_CANDIDATES", "4") or 4), 1)
SHORTLIST_HARD_CAP = max(int(os.getenv("SHORTLIST_HARD_CAP", "8") or 8), 1)
SLOW_SYMBOL_EVAL_SECONDS = max(float(os.getenv("SLOW_SYMBOL_EVAL_SECONDS", "4.5") or 4.5), 0.5)
SLOW_CYCLE_WARN_SECONDS = max(float(os.getenv("SLOW_CYCLE_WARN_SECONDS", "25") or 25), 5.0)
SHORTLIST_FETCH_MULTIPLIER = max(int(os.getenv("SHORTLIST_FETCH_MULTIPLIER", "3") or 3), 1)
DATA_UNAVAILABLE_QUARANTINE_SECONDS = max(float(os.getenv("DATA_UNAVAILABLE_QUARANTINE_SECONDS", "180") or 180), 30.0)
ROUTER_SHADOW_ONLY_COOLDOWN_SECONDS = max(float(os.getenv("ROUTER_SHADOW_ONLY_COOLDOWN_SECONDS", "180") or 180), 30.0)
REJECT_COOLDOWN_BUFFER_SECONDS = max(float(os.getenv("REJECT_COOLDOWN_BUFFER_SECONDS", "8") or 8), 0.0)

_SHORTLIST_SKIP_LOCK = threading.Lock()
_SHORTLIST_SKIP_CACHE: dict[str, dict[str, Any]] = {}
_DETERMINISTIC_REJECTION_REASONS = {
    "NO_BREAKDOWN",
    "NO_BREAKOUT",
    "NO_RETEST_BAR",
    "RETEST_CLOSE_BAD",
    "RETEST_TOO_DEEP",
    "NO_RETEST_TOUCH",
    "TRIGGER_BODY_WEAK",
    "TRIGGER_CLOSE_LOCATION_BAD",
    "RETEST_VOLUME_WEAK",
    "TRIGGER_REJECTION_WEAK",
    "TRIGGER_CANDLE_TOO_LARGE",
    "TRIGGER_BODY_OVEREXTENDED",
    "TOO_EXTENDED_AFTER_RETEST",
}


def _engine_clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        s = str(value).strip()
        return s if s else str(default)
    except Exception:
        return str(default)


def _prune_shortlist_skip_cache(now_ts: float | None = None) -> None:
    now = float(now_ts or time.time())
    with _SHORTLIST_SKIP_LOCK:
        dead = [k for k, v in _SHORTLIST_SKIP_CACHE.items() if float(v.get("expires_at") or 0.0) <= now]
        for k in dead:
            _SHORTLIST_SKIP_CACHE.pop(k, None)


def _next_timeframe_boundary_ts(tf_seconds: int = 300, buffer_seconds: float = 0.0) -> float:
    now = time.time()
    tf = max(int(tf_seconds or 300), 60)
    current_bucket = int(now // tf)
    return float((current_bucket + 1) * tf + max(float(buffer_seconds or 0.0), 0.0))


def _cooldown_expiry_for_reason(reason: str) -> float:
    upper = str(reason or "").strip().upper()
    if upper in _DETERMINISTIC_REJECTION_REASONS:
        return _next_timeframe_boundary_ts(300, REJECT_COOLDOWN_BUFFER_SECONDS)
    if upper in {"ROUTER_REGIME_SHADOW_ONLY", "ROUTER_REGIME_NO_TRADE"}:
        # A new closed candle can change the regime; never mask it for a full TTL.
        return min(time.time() + float(ROUTER_SHADOW_ONLY_COOLDOWN_SECONDS),
                   _next_timeframe_boundary_ts(300, REJECT_COOLDOWN_BUFFER_SECONDS))
    if upper.startswith("ROUTER_DATA_UNAVAILABLE") or upper.startswith("ROUTER_BTC_DATA_UNAVAILABLE"):
        return time.time() + float(DATA_UNAVAILABLE_QUARANTINE_SECONDS)
    return 0.0


def _cache_shortlist_skip(symbol: str, reason: str, detail: str = "") -> None:
    sym = str(symbol or "").strip().upper()
    rsn = str(reason or "").strip().upper()
    if not sym or not rsn:
        return
    expires_at = _cooldown_expiry_for_reason(rsn)
    if expires_at <= time.time():
        return
    with _SHORTLIST_SKIP_LOCK:
        _SHORTLIST_SKIP_CACHE[sym] = {
            "reason": rsn,
            "detail": str(detail or "")[:220],
            "expires_at": float(expires_at),
        }


def _get_shortlist_skip(symbol: str) -> dict[str, Any] | None:
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    _prune_shortlist_skip_cache()
    with _SHORTLIST_SKIP_LOCK:
        row = _SHORTLIST_SKIP_CACHE.get(sym)
        if not row:
            return None
        if float(row.get("expires_at") or 0.0) <= time.time():
            _SHORTLIST_SKIP_CACHE.pop(sym, None)
            return None
        return dict(row)


def _build_trade_plan(
    *,
    signal: Optional[dict[str, Any]] = None,
    active_trade: Optional[dict[str, Any]] = None,
    mgmt: Optional[dict[str, Any]] = None,
    sl_price_pct: float = 0.0,
    entry_strength: float = 0.0,
    best_score: float = 0.0,
    approved_margin_usdc: float = 0.0,
    leverage: float = 0.0,
    target_notional_usdc: float = 0.0,
    requested_qty_coin: float = 0.0,
    actual_qty_coin: float = 0.0,
    actual_notional_usdc: float = 0.0,
    entry_price_preview: float = 0.0,
    entry_price: float = 0.0,
    source: str = "runtime",
) -> dict[str, Any]:
    src_signal = signal if isinstance(signal, dict) else {}
    src_active = active_trade if isinstance(active_trade, dict) else {}
    src_mgmt = mgmt if isinstance(mgmt, dict) else {}
    strategy_model = _safe_str(
        src_signal.get("strategy_model")
        or src_active.get("strategy_model")
        or src_active.get("strategy_id")
        or DEFAULT_STRATEGY_ID,
        DEFAULT_STRATEGY_ID,
    )
    strategy_id = _safe_str(
        src_signal.get("strategy_id")
        or src_active.get("strategy_id")
        or strategy_model
        or DEFAULT_STRATEGY_ID,
        DEFAULT_STRATEGY_ID,
    )
    strategy_version = _safe_str(
        src_signal.get("strategy_version")
        or src_active.get("strategy_version")
        or TRADE_PLAN_SCHEMA_VERSION,
        TRADE_PLAN_SCHEMA_VERSION,
    )
    regime_id = _safe_str(
        src_signal.get("regime_id")
        or src_active.get("regime_id")
        or DEFAULT_REGIME_ID,
        DEFAULT_REGIME_ID,
    )
    regime_version = _safe_str(
        src_signal.get("regime_version")
        or src_active.get("regime_version")
        or TRADE_PLAN_SCHEMA_VERSION,
        TRADE_PLAN_SCHEMA_VERSION,
    )
    detector_version = _safe_str(
        src_signal.get("detector_version")
        or src_active.get("detector_version")
        or DEFAULT_DETECTOR_VERSION,
        DEFAULT_DETECTOR_VERSION,
    )

    resolved_entry_price = _safe_float(entry_price, 0.0)
    resolved_actual_qty = abs(_safe_float(actual_qty_coin, 0.0))
    resolved_actual_notional = _safe_float(actual_notional_usdc, 0.0)
    if resolved_actual_notional <= 0.0 and resolved_entry_price > 0.0 and resolved_actual_qty > 0.0:
        resolved_actual_notional = resolved_entry_price * resolved_actual_qty

    return {
        "schema_version": TRADE_PLAN_SCHEMA_VERSION,
        "frozen_at": datetime.utcnow().isoformat(),
        "source": _safe_str(source, "runtime"),
        "strategy_id": strategy_id,
        "strategy_model": strategy_model,
        "strategy_version": strategy_version,
        "regime_id": regime_id,
        "regime_version": regime_version,
        "detector_version": detector_version,
        "entry_strength": _safe_float(entry_strength, _safe_float(src_active.get("entry_strength"), 0.0)),
        "best_score": _safe_float(best_score, _safe_float(src_active.get("best_score"), 0.0)),
        "atr_pct": _safe_float(src_signal.get("atr_pct"), _safe_float(src_active.get("atr_pct"), 0.0)),
        "sl_price_pct": _safe_float(sl_price_pct, _safe_float(src_active.get("sl_price_pct"), 0.0)),
        "tp_activation_price": _safe_float(src_mgmt.get("tp_activate_price", src_mgmt.get("tp_activation_price", 0.0)), 0.0),
        # Política de salida fija: no trailing, no parcial, no cierre por fuerza y no break-even.
        "trail_retrace_price": 0.0,
        "force_min_profit_price": 0.0,
        "force_min_strength": 0.0,
        "partial_tp_activation_price": 0.0,
        "partial_tp_close_fraction": 0.0,
        "break_even_activation_price": 0.0,
        "break_even_offset_price": 0.0,
        "bucket": _safe_str(src_mgmt.get("bucket"), "strategy"),
        "approved_margin_usdc": _safe_float(approved_margin_usdc, _safe_float(src_active.get("approved_margin_usdc"), 0.0)),
        "leverage": _safe_float(leverage, _safe_float(src_active.get("leverage"), 0.0)),
        "target_notional_usdc": _safe_float(target_notional_usdc, _safe_float(src_active.get("target_notional_usdc"), 0.0)),
        "requested_qty_coin": _safe_float(requested_qty_coin, _safe_float(src_active.get("requested_qty_coin"), 0.0)),
        "actual_qty_coin": resolved_actual_qty,
        "actual_notional_usdc": resolved_actual_notional,
        "entry_price_preview": _safe_float(entry_price_preview, _safe_float(src_active.get("entry_price_preview"), 0.0)),
        "entry_price": resolved_entry_price,
        "signal_reference_price": _safe_float(src_signal.get("signal_reference_price"), _safe_float(src_signal.get("close_5"), 0.0)),
        "structural_stop_price": _safe_float(src_signal.get("stop_price"), 0.0),
        "structural_target_price": _safe_float(src_signal.get("target_price"), 0.0),
        "resolved_rr": _safe_float(src_signal.get("resolved_rr"), _safe_float(src_mgmt.get("tp_rr_multiple"), 0.0)),
        "exit_policy": "FIXED_STRUCTURAL_TP_SL",
    }


def _build_entry_context(
    *,
    symbol: str,
    symbol_for_exec: str,
    scanner_meta: Optional[dict[str, Any]] = None,
    signal: Optional[dict[str, Any]] = None,
    risk: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    scanner = scanner_meta if isinstance(scanner_meta, dict) else {}
    sig = signal if isinstance(signal, dict) else {}
    risk_payload = risk if isinstance(risk, dict) else {}
    return {
        "captured_at": datetime.utcnow().isoformat(),
        "symbol": _safe_str(symbol).upper(),
        "symbol_for_exec": _safe_str(symbol_for_exec).upper(),
        "scanner": dict(scanner),
        "signal_summary": {
            "direction": _safe_str(sig.get("direction")).lower(),
            "strength": _safe_float(sig.get("strength"), 0.0),
            "score": _safe_float(sig.get("score"), 0.0),
            "strategy_id": _safe_str(sig.get("strategy_id"), DEFAULT_STRATEGY_ID),
            "strategy_version": _safe_str(sig.get("strategy_version"), TRADE_PLAN_SCHEMA_VERSION),
            "strategy_model": _safe_str(sig.get("strategy_model"), DEFAULT_STRATEGY_ID),
            "regime_id": _safe_str(sig.get("regime_id"), DEFAULT_REGIME_ID),
            "detector_version": _safe_str(sig.get("detector_version"), DEFAULT_DETECTOR_VERSION),
            "router_mode": _safe_str(sig.get("router_mode"), "legacy"),
            "router_decision": _safe_str(sig.get("router_decision"), "legacy"),
            "shadow_range": dict(sig.get("shadow_range") or {}) if isinstance(sig.get("shadow_range"), dict) else {},
            "shadow_signal": bool(sig.get("shadow_signal")),
            "shadow_strategy_id": _safe_str(sig.get("shadow_strategy_id"), ""),
            "shadow_direction": _safe_str(sig.get("shadow_direction"), "").lower(),
            "shadow_score": _safe_float(sig.get("shadow_score"), 0.0),
            "last_candle_t_5m": _safe_int(sig.get("last_candle_t_5m")),
            "signal_reference_price": _safe_float(sig.get("signal_reference_price"), _safe_float(sig.get("close_5"), 0.0)),
            "stop_price": _safe_float(sig.get("stop_price"), 0.0),
            "target_price": _safe_float(sig.get("target_price"), 0.0),
            "structural_target_price": _safe_float(sig.get("structural_target_price"), 0.0),
            "atr_pct": _safe_float(sig.get("atr_pct"), 0.0),
            "sl_price_pct": _safe_float(sig.get("sl_price_pct"), 0.0),
            "tp_price_pct": _safe_float(sig.get("tp_activation_price"), 0.0),
        },
        "risk": {
            "ok": bool(risk_payload.get("ok")),
            "reason": _safe_str(risk_payload.get("reason"), ""),
            "position_size": _safe_float(risk_payload.get("position_size"), 0.0),
        },
    }


def _build_strategy_router_event_payload(
    *,
    event_type: str,
    symbol: str,
    signal: Optional[dict[str, Any]] = None,
    scanner_meta: Optional[dict[str, Any]] = None,
    execution_mode: str = "live",
    selected: bool = False,
    trade_opened: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    sig = signal if isinstance(signal, dict) else {}
    scanner = scanner_meta if isinstance(scanner_meta, dict) else {}
    regime_context = sig.get("regime_context") if isinstance(sig.get("regime_context"), dict) else {}
    shadow = sig.get("shadow_range") if isinstance(sig.get("shadow_range"), dict) else {}

    mode = _safe_str(execution_mode, "live").lower() or "live"
    if mode == "shadow":
        strategy_id = _safe_str(shadow.get("strategy_id") or sig.get("shadow_strategy_id"), "range_mean_reversion")
        direction = _safe_str(shadow.get("direction") or sig.get("shadow_direction"), "").lower()
        score = _safe_float(shadow.get("score"), _safe_float(sig.get("shadow_score"), 0.0))
        strength = _safe_float(shadow.get("strength"), 0.0)
        signal_flag = bool(shadow.get("signal"))
    else:
        strategy_id = _safe_str(sig.get("strategy_id"), DEFAULT_STRATEGY_ID)
        direction = _safe_str(sig.get("direction"), "").lower()
        score = _safe_float(sig.get("score"), 0.0)
        strength = _safe_float(sig.get("strength"), 0.0)
        signal_flag = bool(sig.get("signal"))

    return {
        "event_type": _safe_str(event_type, "router_event").lower(),
        "symbol": _safe_str(symbol).upper(),
        "strategy_id": strategy_id,
        "regime_id": _safe_str(sig.get("regime_id"), DEFAULT_REGIME_ID),
        "execution_mode": mode,
        "direction": direction,
        "signal": bool(signal_flag),
        "selected": bool(selected),
        "trade_opened": bool(trade_opened),
        "regime_changed": bool(regime_context.get("changed")),
        "shadow_evaluated": bool(shadow.get("evaluated")),
        "shadow_signal": bool(shadow.get("signal")),
        "signal_summary": {
            "strategy_id": _safe_str(sig.get("strategy_id"), DEFAULT_STRATEGY_ID),
            "strategy_model": _safe_str(sig.get("strategy_model"), DEFAULT_STRATEGY_ID),
            "direction": _safe_str(sig.get("direction"), "").lower(),
            "strength": _safe_float(sig.get("strength"), 0.0),
            "score": _safe_float(sig.get("score"), 0.0),
            "router_mode": _safe_str(sig.get("router_mode"), "legacy"),
            "router_decision": _safe_str(sig.get("router_decision"), "legacy"),
        },
        "shadow_summary": {
            "strategy_id": _safe_str(shadow.get("strategy_id") or sig.get("shadow_strategy_id"), ""),
            "direction": _safe_str(shadow.get("direction") or sig.get("shadow_direction"), "").lower(),
            "signal": bool(shadow.get("signal")),
            "score": _safe_float(shadow.get("score"), _safe_float(sig.get("shadow_score"), 0.0)),
            "strength": _safe_float(shadow.get("strength"), 0.0),
            "reason": _safe_str(shadow.get("reason"), ""),
            "evaluated": bool(shadow.get("evaluated")),
        },
        "scanner_summary": {
            "score": _safe_float(scanner.get("score"), 0.0),
            "volume": _safe_float(scanner.get("volume"), 0.0),
            "oi": _safe_float(scanner.get("oi"), 0.0),
            "shortlist_rank": _safe_int(scanner.get("shortlist_rank"), 0),
            "shortlist_size": _safe_int(scanner.get("shortlist_size"), 0),
        },
        "regime_summary": {
            "candidate_regime": _safe_str(regime_context.get("candidate_regime"), "unknown"),
            "active_regime": _safe_str(regime_context.get("active_regime"), "unknown"),
            "candidate_confidence": _safe_float(regime_context.get("candidate_confidence"), 0.0),
            "changed": bool(regime_context.get("changed")),
            "bias": _safe_str(regime_context.get("bias"), "neutral"),
            "source": _safe_str(sig.get("router_regime_source"), "unknown"),
            "reasons": list(regime_context.get("reasons") or [])[:8],
            "feature_summary": dict(regime_context.get("feature_summary") or {}),
        },
        "extra": dict(extra or {}),
    }


def _record_strategy_router_event(
    user_id: int,
    *,
    event_type: str,
    symbol: str,
    signal: Optional[dict[str, Any]] = None,
    scanner_meta: Optional[dict[str, Any]] = None,
    execution_mode: str = "live",
    selected: bool = False,
    trade_opened: bool = False,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    if (signal or {}).get('signal_cache_hit') and event_type in {'router_blocked', 'strategy_rejected', 'shadow_opportunity'}:
        return
    try:
        payload = _build_strategy_router_event_payload(
            event_type=event_type,
            symbol=symbol,
            signal=signal,
            scanner_meta=scanner_meta,
            execution_mode=execution_mode,
            selected=selected,
            trade_opened=trade_opened,
            extra=extra,
        )
        database_module.record_strategy_router_event(int(user_id), payload)
    except Exception:
        pass


def _shadow_candidate_rank_tuple(shadow: dict, scanner_row: dict) -> tuple:
    return (
        float((shadow or {}).get("score", 0.0) or 0.0),
        float((shadow or {}).get("strength", 0.0) or 0.0),
        float((scanner_row or {}).get("score", 0.0) or 0.0),
        float((scanner_row or {}).get("volume", 0.0) or 0.0),
        float((scanner_row or {}).get("oi", 0.0) or 0.0),
    )


def _build_active_trade_snapshot(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    side: str,
    opposite: str,
    entry_price: float,
    qty_coin_for_log: float,
    qty_usdc_for_profit: float,
    best_score: float,
    entry_strength: float,
    mode: str,
    sl_price_pct: float = 0.0,
    mgmt: Optional[dict[str, Any]] = None,
    opened_at_ms: Optional[int] = None,
    trade_plan: Optional[dict[str, Any]] = None,
    entry_context: Optional[dict[str, Any]] = None,
    existing_state: Optional[dict[str, Any]] = None,
    manager_bootstrap_pending: bool = False,
) -> dict[str, Any]:
    snapshot = dict(existing_state or {})
    frozen_plan = dict(trade_plan or {})
    snapshot.update({
        "symbol": symbol,
        "symbol_for_exec": symbol_for_exec,
        "direction": direction,
        "side": side,
        "opposite": opposite,
        "entry_price": float(entry_price),
        "qty_coin_for_log": float(qty_coin_for_log),
        "qty_usdc_for_profit": float(qty_usdc_for_profit),
        "best_score": float(best_score),
        "entry_strength": float(entry_strength),
        "mode": mode,
        "sl_price_pct": float(sl_price_pct),
        "started_at": snapshot.get("started_at") or datetime.utcnow().isoformat(),
        "opened_at_ms": int(opened_at_ms) if opened_at_ms else int(time.time() * 1000),
        "tp_activation_price": float((mgmt or {}).get("tp_activate_price", (mgmt or {}).get("tp_activation_price", frozen_plan.get("tp_activation_price", 0.0))) or 0.0),
        # Compatibilidad de esquema: la operación solo conserva TP/SL fijos.
        "trail_retrace_price": 0.0,
        "force_min_profit_price": 0.0,
        "force_min_strength": 0.0,
        "partial_tp_activation_price": 0.0,
        "partial_tp_close_fraction": 0.0,
        "break_even_activation_price": 0.0,
        "break_even_offset_price": 0.0,
        "bucket": _safe_str((mgmt or {}).get("bucket", frozen_plan.get("bucket", ""))),
        "trade_plan": frozen_plan,
        "entry_context": dict(entry_context or snapshot.get("entry_context") or {}),
        "strategy_id": _safe_str(frozen_plan.get("strategy_id") or snapshot.get("strategy_id") or DEFAULT_STRATEGY_ID, DEFAULT_STRATEGY_ID),
        "strategy_model": _safe_str(frozen_plan.get("strategy_model") or snapshot.get("strategy_model") or DEFAULT_STRATEGY_ID, DEFAULT_STRATEGY_ID),
        "strategy_version": _safe_str(frozen_plan.get("strategy_version") or snapshot.get("strategy_version") or TRADE_PLAN_SCHEMA_VERSION, TRADE_PLAN_SCHEMA_VERSION),
        "regime_id": _safe_str(frozen_plan.get("regime_id") or snapshot.get("regime_id") or DEFAULT_REGIME_ID, DEFAULT_REGIME_ID),
        "regime_version": _safe_str(frozen_plan.get("regime_version") or snapshot.get("regime_version") or TRADE_PLAN_SCHEMA_VERSION, TRADE_PLAN_SCHEMA_VERSION),
        "detector_version": _safe_str(frozen_plan.get("detector_version") or snapshot.get("detector_version") or DEFAULT_DETECTOR_VERSION, DEFAULT_DETECTOR_VERSION),
        "approved_margin_usdc": _safe_float(frozen_plan.get("approved_margin_usdc"), _safe_float(snapshot.get("approved_margin_usdc"), 0.0)),
        "target_notional_usdc": _safe_float(frozen_plan.get("target_notional_usdc"), _safe_float(snapshot.get("target_notional_usdc"), 0.0)),
        "requested_qty_coin": _safe_float(frozen_plan.get("requested_qty_coin"), _safe_float(snapshot.get("requested_qty_coin"), 0.0)),
        "entry_price_preview": _safe_float(frozen_plan.get("entry_price_preview"), _safe_float(snapshot.get("entry_price_preview"), 0.0)),
        "leverage": _safe_float(frozen_plan.get("leverage"), _safe_float(snapshot.get("leverage"), 0.0)),
        "manager_bootstrap_pending": bool(manager_bootstrap_pending),
    })
    snapshot.setdefault("partial_tp_taken", False)
    snapshot.setdefault("partial_tp_blocked", False)
    snapshot.setdefault("partial_tp_blocked_reason", "")
    # Política actual: el SL nunca se mueve a break-even. Limpiamos estados viejos.
    snapshot["break_even_armed"] = False
    snapshot.setdefault("trailing_active", False)
    snapshot.setdefault("best_pnl_pct", 0.0)
    snapshot.setdefault("trailing_stop_pnl", None)
    snapshot.setdefault("peak_price", float(entry_price))
    snapshot.setdefault("last_price", float(entry_price))
    snapshot.setdefault("last_pnl_pct", 0.0)
    snapshot.setdefault("strength_check_ts", 0.0)
    snapshot.setdefault("manager_heartbeat_ts", time.time())
    snapshot.setdefault("exchange_stop_trigger", 0.0)
    snapshot.setdefault("exchange_stop_context", "")
    snapshot.setdefault("exchange_stop_mode", "initial")
    snapshot.setdefault("exchange_stop_reference_price", 0.0)
    snapshot.setdefault("exchange_stop_updated_at", 0.0)
    snapshot.setdefault("tp_in_exchange", False)
    snapshot.setdefault("exchange_tp_trigger", 0.0)
    snapshot.setdefault("exchange_tp_context", "")
    snapshot.setdefault("exchange_tp_mode", "fixed")
    snapshot.setdefault("exchange_tp_reference_price", 0.0)
    snapshot.setdefault("exchange_tp_updated_at", 0.0)
    snapshot.setdefault("close_in_progress", False)
    snapshot.setdefault("close_finalized", False)
    return snapshot


def _build_trade_audit_metadata(active_trade: Optional[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(active_trade, dict):
        return None
    payload: dict[str, Any] = {}
    for key in (
        "strategy_id",
        "strategy_model",
        "strategy_version",
        "regime_id",
        "regime_version",
        "detector_version",
        "approved_margin_usdc",
        "target_notional_usdc",
        "requested_qty_coin",
        "entry_price_preview",
        "leverage",
    ):
        if key in active_trade and active_trade.get(key) is not None:
            payload[key] = active_trade.get(key)
    if active_trade.get("opened_at_ms") is not None:
        # Visualización histórica: instante real de apertura, sin alterar órdenes o gestión LIVE.
        payload["opened_at_ms"] = active_trade.get("opened_at_ms")
    if isinstance(active_trade.get("trade_plan"), dict):
        payload["trade_plan"] = dict(active_trade.get("trade_plan") or {})
    if isinstance(active_trade.get("entry_context"), dict):
        payload["entry_context"] = dict(active_trade.get("entry_context") or {})
    return payload or None


def _publish_operational_snapshot(
    user_id: int,
    state: str,
    message: str,
    *,
    mode: str | None = None,
    live_trade: bool = False,
    active_symbol: str | None = None,
    exchange_snapshot: dict | None = None,
    metadata: dict | None = None,
) -> None:
    payload = dict(metadata or {})
    snapshot = dict(exchange_snapshot or {})
    if snapshot:
        payload.setdefault('exchange_status', snapshot.get('status'))
        payload.setdefault('exchange_available_balance', snapshot.get('available_balance'))
        payload.setdefault('exchange_account_value', snapshot.get('account_value'))
        payload.setdefault('positions_count', snapshot.get('positions_count'))
        payload.setdefault('capital_threshold', snapshot.get('capital_threshold'))
        payload.setdefault('capital_sufficient', snapshot.get('capital_sufficient'))
        payload.setdefault('exchange_message', snapshot.get('message'))
        payload.setdefault('active_symbols', snapshot.get('active_symbols'))
    payload.setdefault('last_cycle_at', datetime.utcnow())
    try:
        database_module.touch_user_operational_state(
            int(user_id),
            state,
            message,
            mode=mode,
            source='trading_engine',
            live_trade=bool(live_trade),
            active_symbol=active_symbol,
            metadata=payload,
        )
    except Exception:
        pass


def _publish_scanner_runtime(
    component_state: str,
    *,
    user_id: int | None = None,
    symbol: str | None = None,
    decision: str | None = None,
    exchange_snapshot: dict | None = None,
    extra: dict | None = None,
) -> None:
    meta = dict(extra or {})
    if user_id is not None:
        meta['user_id'] = int(user_id)
    if symbol:
        meta['symbol'] = str(symbol).upper()
    if decision:
        meta['last_decision'] = str(decision)
    if exchange_snapshot:
        meta.setdefault('exchange_status', exchange_snapshot.get('status'))
        meta.setdefault('available_balance', exchange_snapshot.get('available_balance'))
        meta.setdefault('account_value', exchange_snapshot.get('account_value'))
        meta.setdefault('positions_count', exchange_snapshot.get('positions_count'))
        meta.setdefault('exchange_message', exchange_snapshot.get('message'))
    try:
        database_module.touch_runtime_component('scanner', component_state, metadata=meta)
    except Exception:
        pass

# ============================================================
# CONFIG
# ============================================================

PRICE_CHECK_INTERVAL = 0.4

MIN_TRADE_STRENGTH = 0.18
USER_TRADE_COOLDOWN_SECONDS = 600
MAX_SIGNAL_EVAL_CANDIDATES = 12
# ============================================================
# RISK GOVERNOR (participation control)
# - No toca estrategia ni TP/SL.
# - Solo bloquea NUEVAS entradas cuando el rendimiento reciente es malo.
# ============================================================

USER_RISK_WINDOW = max(10, USER_RISK_PF_WINDOW)
GLOBAL_RISK_WINDOW = max(20, GLOBAL_RISK_PF_WINDOW)

# Risk state is loaded lazily and persisted after closes. One trading worker only.
_user_risk_state: dict[int, dict[str, Any]] = {}
_global_risk_state: dict[str, Any] = {
    "results": deque(maxlen=GLOBAL_RISK_WINDOW),  # list of (profit: float)
    "consec_losses": 0,
    "cooldown_until": 0.0,
}
_risk_lock = threading.Lock()


def _risk_pf(results: deque) -> float:
    gains = 0.0
    losses = 0.0
    for p in results:
        if p > 0:
            gains += float(p)
        elif p < 0:
            losses += abs(float(p))
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


_risk_loaded = False
_risk_dirty = False
_risk_pending_closes: list[tuple] = []
_global_seen_opportunities = deque(maxlen=2000)
_processed_risk_closes = deque(maxlen=4000)


def _risk_load_locked() -> bool:
    global _risk_loaded
    if _risk_loaded:
        return True
    try:
        doc = database_module.settings_col.find_one({'_id': 'risk_governor_v2'}) or {}
        for uid, raw in (doc.get('users') or {}).items():
            state = dict(raw)
            state['results'] = deque(state.get('results', []), maxlen=USER_RISK_WINDOW)
            _user_risk_state[int(uid)] = state
        raw_global = doc.get('global') or {}
        _global_risk_state.update(raw_global)
        _global_risk_state['results'] = deque(raw_global.get('results', []), maxlen=GLOBAL_RISK_WINDOW)
        _global_seen_opportunities.extend(doc.get('seen_opportunities', []))
        _processed_risk_closes.extend(doc.get('processed_closes', []))
        _risk_loaded = True
        return True
    except Exception as exc:
        log(f'RISK_STATE_LOAD_FAILED: {exc}', 'CRITICAL')
        return False


def _risk_save_locked() -> bool:
    global _risk_dirty
    if not _risk_dirty:
        return True
    try:
        users = {str(uid): {**st, 'results': list(st['results'])} for uid, st in _user_risk_state.items()}
        snapshot = {'users': users,
                    'global': {**_global_risk_state, 'results': list(_global_risk_state['results'])},
                    'seen_opportunities': list(_global_seen_opportunities),
                    'processed_closes': list(_processed_risk_closes), 'updated_at': datetime.utcnow()}
        database_module.settings_col.update_one({'_id': 'risk_governor_v2'}, {'$set': snapshot}, upsert=True)
        _risk_dirty = False
        return True
    except Exception as exc:
        log(f'RISK_STATE_SAVE_FAILED: {exc}', 'CRITICAL')
        return False


def _risk_flush_locked() -> bool:
    global _risk_dirty
    if not _risk_load_locked():
        return False
    while _risk_pending_closes:
        uid, profit, opportunity, normalized, close_id = _risk_pending_closes.pop(0)
        if close_id and close_id in _processed_risk_closes:
            continue
        _apply_risk_close(uid, profit, opportunity, normalized)
        if close_id:
            _processed_risk_closes.append(close_id)
        _risk_dirty = True
    return _risk_save_locked()


def _risk_record_close(user_id: int, profit: float, opportunity_key=None, global_profit=None, close_id=None) -> None:
    with _risk_lock:
        _risk_pending_closes.append((user_id, profit, opportunity_key, global_profit, close_id))
        _risk_flush_locked()


def _apply_risk_close(user_id, profit, opportunity_key=None, global_profit=None):
    now_ts = time.time()
    st = _user_risk_state.get(user_id)
    if not st:
        st = {
            "results": deque(maxlen=USER_RISK_WINDOW),
            "consec_losses": 0,
            "cooldown_until": 0.0,
            "cooldown_reason": "",
        }
        _user_risk_state[user_id] = st

    st["results"].append(float(profit))
    if profit < 0:
        st["consec_losses"] = int(st.get("consec_losses", 0)) + 1
        st["cooldown_until"] = max(
            float(st.get("cooldown_until") or 0.0),
            now_ts + USER_RISK_LOSS_COOLDOWN_SECONDS,
        )
        st["cooldown_reason"] = "USER_SINGLE_LOSS_COOLDOWN"
    else:
        st["consec_losses"] = 0

    count_global = not opportunity_key or opportunity_key not in _global_seen_opportunities
    if opportunity_key and count_global:
        _global_seen_opportunities.append(opportunity_key)
    # Each opportunity contributes once globally; every account counts individually.
    if count_global:
        _global_risk_state["results"].append(float(global_profit if global_profit is not None else profit))
        if profit < 0:
            _global_risk_state["consec_losses"] = int(_global_risk_state.get("consec_losses", 0)) + 1
        else:
            _global_risk_state["consec_losses"] = 0
    
    # user triggers
    if st["consec_losses"] >= USER_RISK_MAX_CONSEC_LOSSES:
        st["cooldown_until"] = max(float(st.get("cooldown_until") or 0.0), now_ts + USER_RISK_COOLDOWN_SECONDS)
        st["cooldown_reason"] = f"USER_CONSEC_LOSSES_{st['consec_losses']}"
    else:
        # PF trigger only when we have enough samples
        if len(st["results"]) >= USER_RISK_PF_WINDOW:
            pf = _risk_pf(list(st["results"])[-USER_RISK_PF_WINDOW:])
            if pf < USER_RISK_MIN_PF:
                st["cooldown_until"] = max(float(st.get("cooldown_until") or 0.0), now_ts + USER_RISK_PF_COOLDOWN_SECONDS)
                st["cooldown_reason"] = f"USER_PF_{pf:.2f}_WIN{USER_RISK_PF_WINDOW}"

    if count_global:
        # global triggers
        if int(_global_risk_state.get("consec_losses", 0)) >= GLOBAL_RISK_MAX_CONSEC_LOSSES:
            _global_risk_state["cooldown_until"] = max(float(_global_risk_state.get("cooldown_until") or 0.0), now_ts + GLOBAL_RISK_COOLDOWN_SECONDS)
            _global_risk_state["cooldown_reason"] = f"GLOBAL_CONSEC_LOSSES_{_global_risk_state['consec_losses']}"
        else:
            if len(_global_risk_state["results"]) >= GLOBAL_RISK_PF_WINDOW:
                pf_g = _risk_pf(list(_global_risk_state["results"])[-GLOBAL_RISK_PF_WINDOW:])
                if pf_g < GLOBAL_RISK_MIN_PF:
                    _global_risk_state["cooldown_until"] = max(float(_global_risk_state.get("cooldown_until") or 0.0), now_ts + GLOBAL_RISK_PF_COOLDOWN_SECONDS)
                    _global_risk_state["cooldown_reason"] = f"GLOBAL_PF_{pf_g:.2f}_WIN{GLOBAL_RISK_PF_WINDOW}"



def _risk_governor_allows_new_entries(user_id: int) -> tuple[bool, str]:
    now_ts = time.time()
    with _risk_lock:
        if not _risk_flush_locked():
            return False, "RISK_STATE_UNAVAILABLE"
        # global first
        g_until = float(_global_risk_state.get("cooldown_until") or 0.0)
        if now_ts < g_until:
            secs = int(g_until - now_ts)
            reason = str(_global_risk_state.get("cooldown_reason") or "GLOBAL_COOLDOWN")
            return False, f"RISK_GOV_GLOBAL_COOLDOWN ({secs}s) {reason}"

        st = _user_risk_state.get(user_id)
        if st:
            u_until = float(st.get("cooldown_until") or 0.0)
            if now_ts < u_until:
                secs = int(u_until - now_ts)
                reason = str(st.get("cooldown_reason") or "USER_COOLDOWN")
                return False, f"RISK_GOV_USER_COOLDOWN ({secs}s) {reason}"

    return True, "OK"


# Startup grace to avoid an immediate trade right after a deploy/restart
STARTUP_GRACE_SECONDS = int(os.getenv('STARTUP_GRACE_SECONDS', '30'))
PROCESS_START_TIME_UTC = datetime.utcnow()

SYMBOL_NOFILL_COOLDOWN_SECONDS = 90

# ✅ Sizing (la fuente de verdad vive en app.config)
MARGIN_USE_PCT = 1.0   # legacy; POSITION_PERCENT/risk.py gobierna el sizing real
FIXED_MARGIN_USDC = float(os.getenv("FIXED_MARGIN_USDC", os.getenv("FIXED_NOTIONAL_USDC", "3.0")))  # legacy; no gobierna el sizing de entrada

# ✅ BLINDAJE BANK GRADE (ANTI-ÓRDENES RIDÍCULAS)
# Evita operaciones de centavos / qty ~ 0 por capital bajo o redondeos.
MIN_NOTIONAL_USDC = get_exchange_min_order_notional_usdc()
MIN_QTY_COIN = 0.0001    # qty mínimo en coin (seguridad)

# ✅ Guardián de calidad de ejecución: evita abrir trades cuyo plan tenga una
# relación recompensa/riesgo demasiado baja aunque la estrategia lo permita.
# Se mantiene configurable para pruebas; no altera los cálculos internos de la estrategia.



# Gestión técnica del manager.
# La lógica de trading usa TP fijo en exchange, SL fijo en exchange y upgrade opcional a break-even protector
# la define strategy.py y el engine solo la ejecuta.
TP_FORCE_CHECK_INTERVAL = 15.0      # segundos entre re-evaluaciones de fuerza

# ============================================================
# STATE (rate limit / cooldown) — requerido por trading_loop
# ============================================================

# user_id -> datetime (cooldown entre trades)
user_next_trade_time: dict[int, datetime] = {}

# user_id -> {"hour_key": str, "hour_count": int, "day": date, "day_count": int}
user_trade_counter: dict[int, dict] = {}

STRENGTH_STRONG_THRESHOLD = 0.30  # umbral para "fuerte"



# user_id -> { "CC-PERP": expiry_dt, ... }
user_symbol_cooldowns: dict[int, dict[str, datetime]] = {}

# ✅ Lock por usuario
_user_locks: dict[int, threading.Lock] = {}

# ✅ Manager threads por usuario (para NO bloquear el ciclo durante horas)
# Mantiene un watcher por usuario mientras exista una posición abierta.
_user_manager_threads: dict[int, threading.Thread] = {}
_user_manager_meta: dict[int, dict] = {}
_user_manager_guard = threading.Lock()

# Estado en memoria de trades activos para reconciliación post-cierre.
# Esto NO reemplaza la DB; solo evita perder el registro si el manager muere
# o si el exchange cierra la posición fuera del flujo normal del bot.
_user_active_trades: dict[int, dict[str, Any]] = {}
_user_active_trade_guard = threading.Lock()

POSITION_SYNC_INTERVAL = 2.0
ADOPT_EMERGENCY_SL_PCT_RAW = float(os.getenv("ADOPT_EMERGENCY_SL_PCT", "0.012"))  # valor solicitado para ADOPT antes del cap del engine
ADOPT_EMERGENCY_SL_PCT = float(ADOPT_EMERGENCY_SL_PCT_RAW)
ADOPT_STOP_BUFFER_PCT = float(os.getenv("ADOPT_STOP_BUFFER_PCT", "0.003"))  # buffer mínimo vs precio actual para SL adoptado/recuperado
ACTIVE_TRADE_STATE_DIR = os.path.abspath(os.getenv("ACTIVE_TRADE_STATE_DIR", "runtime_state/active_trades"))
ACTIVE_TRADES_COLLECTION = os.getenv("ACTIVE_TRADES_COLLECTION", "active_trades")
ACTIVE_TRADE_STATE_FALLBACK_DIR = os.path.abspath(os.getenv("ACTIVE_TRADE_STATE_FALLBACK_DIR", ACTIVE_TRADE_STATE_DIR))
ADOPT_RELOG_SECONDS = float(os.getenv("ADOPT_RELOG_SECONDS", "300"))
ADOPT_SL_RECHECK_SECONDS = float(os.getenv("ADOPT_SL_RECHECK_SECONDS", "120"))
STOP_TRIGGER_DECIMALS_FALLBACK = int(os.getenv("STOP_TRIGGER_DECIMALS_FALLBACK", "6"))
STOP_TRIGGER_DECIMALS_MIN = int(os.getenv("STOP_TRIGGER_DECIMALS_MIN", "4"))
STOP_TRIGGER_DECIMALS_MAX = int(os.getenv("STOP_TRIGGER_DECIMALS_MAX", "8"))


# ===============================
# Auditoría de niveles de trade
# ===============================
def _pct_to_abs_price(entry_price: float, pct_value: float, direction: str, *, kind: str) -> float:
    try:
        entry = float(entry_price or 0.0)
        pct = float(pct_value or 0.0)
    except Exception:
        return 0.0
    if entry <= 0 or pct <= 0:
        return 0.0
    d = str(direction or "").lower()
    if kind == "sl":
        return entry * (1.0 - pct) if d == "long" else entry * (1.0 + pct)
    if kind in {"tp_activate", "force_min_profit"}:
        return entry * (1.0 + pct) if d == "long" else entry * (1.0 - pct)
    return 0.0


def _trail_exit_price_from_price(peak_or_trough_price: float, retrace_pct: float, direction: str) -> float:
    try:
        px = float(peak_or_trough_price or 0.0)
        retr = float(retrace_pct or 0.0)
    except Exception:
        return 0.0
    if px <= 0 or retr <= 0:
        return 0.0
    d = str(direction or "").lower()
    return (px * (1.0 - retr)) if d == "long" else (px * (1.0 + retr))


def _log_trade_plan(*, context: str, user_id: int, symbol: str, direction: str, entry_price: float, sl_price_pct: float, tp_activate_price: float, break_even_activation_price: float, break_even_offset_price: float, qty_coin: float = 0.0, notional_usdc: float = 0.0, bucket: str = "", strategy_id: str = "", strategy_model: str = "") -> None:
    # break_even_* se mantienen en la firma por compatibilidad con llamadas antiguas,
    # pero ya no forman parte de la gestión de salida.
    _ = (break_even_activation_price, break_even_offset_price)
    sl_abs = _pct_to_abs_price(entry_price, sl_price_pct, direction, kind="sl")
    tp_abs = _pct_to_abs_price(entry_price, tp_activate_price, direction, kind="tp_activate")
    log(
        f"TRADE_PLAN[{context}] user={user_id} symbol={symbol} dir={direction} "
        f"strategy_id={strategy_id or 'n/a'} strategy_model={strategy_model or 'n/a'} "
        f"entry={float(entry_price):.8f} qty_coin={float(qty_coin):.8f} notional~={float(notional_usdc):.4f} "
        f"sl_pct={float(sl_price_pct):.6f} sl_price={sl_abs:.8f} "
        f"tp_fixed_pct={float(tp_activate_price):.6f} tp_fixed_price={tp_abs:.8f} "
        f"exit_policy=FIXED_TP_SL bucket={bucket or 'n/a'}",
        "WARN",
    )



def _active_trade_state_path(user_id: int) -> str:
    safe_user_id = str(int(user_id))
    return os.path.join(ACTIVE_TRADE_STATE_FALLBACK_DIR, f"{safe_user_id}.json")


def _safe_jsonable_dict(payload: Optional[dict[str, Any]]) -> dict[str, Any]:
    base = dict(payload or {})
    try:
        return json.loads(json.dumps(base, ensure_ascii=False, default=str))
    except Exception:
        cleaned: dict[str, Any] = {}
        for k, v in base.items():
            try:
                json.dumps(v, ensure_ascii=False, default=str)
                cleaned[k] = v
            except Exception:
                cleaned[k] = str(v)
        return cleaned


def _resolve_active_trades_collection():
    db_obj = None
    for attr_name in ("db", "database", "mongo_db"):
        candidate = getattr(database_module, attr_name, None)
        if candidate is not None:
            db_obj = candidate
            break

    if db_obj is None:
        for fn_name in ("get_db", "get_database", "get_mongo_db"):
            fn = getattr(database_module, fn_name, None)
            if callable(fn):
                try:
                    candidate = fn()
                    if candidate is not None:
                        db_obj = candidate
                        break
                except Exception as e:
                    log(f"active_trades mongo resolver {fn_name} error: {e}", "WARN")

    if db_obj is None:
        return None

    try:
        return db_obj[ACTIVE_TRADES_COLLECTION]
    except Exception:
        return getattr(db_obj, ACTIVE_TRADES_COLLECTION, None)


def _persist_active_trade_mongo(user_id: int, trade_data: dict[str, Any]) -> bool:
    collection = _resolve_active_trades_collection()
    if collection is None:
        return False
    payload = _safe_jsonable_dict(trade_data)
    payload["user_id"] = int(user_id)
    payload["persisted_at"] = datetime.utcnow().isoformat()
    try:
        collection.update_one(
            {"user_id": int(user_id)},
            {"$set": payload},
            upsert=True,
        )
        return True
    except Exception as e:
        log(f"persist_active_trade_mongo error user={user_id} err={e}", "ERROR")
        return False


def _load_persisted_active_trade_mongo(user_id: int) -> Optional[dict[str, Any]]:
    collection = _resolve_active_trades_collection()
    if collection is None:
        return None
    try:
        doc = collection.find_one({"user_id": int(user_id)})
        if not isinstance(doc, dict):
            return None
        doc.pop("_id", None)
        return _safe_jsonable_dict(doc)
    except Exception as e:
        log(f"load_persisted_active_trade_mongo error user={user_id} err={e}", "WARN")
        return None


def _delete_persisted_active_trade_mongo(user_id: int) -> None:
    collection = _resolve_active_trades_collection()
    if collection is None:
        return
    try:
        collection.delete_one({"user_id": int(user_id)})
    except Exception as e:
        log(f"delete_persisted_active_trade_mongo error user={user_id} err={e}", "WARN")


def _persist_active_trade_fallback_file(user_id: int, trade_data: dict[str, Any]) -> None:
    tmp_path = None
    try:
        final_path = os.path.abspath(_active_trade_state_path(user_id))
        final_dir = os.path.dirname(final_path)
        os.makedirs(final_dir, exist_ok=True)

        payload = _safe_jsonable_dict(trade_data)
        payload["user_id"] = int(user_id)
        payload["persisted_at"] = datetime.utcnow().isoformat()

        import tempfile
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{int(user_id)}_",
            suffix=".json.tmp",
            dir=final_dir,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"), default=str)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:
                    pass

            os.replace(tmp_path, final_path)
            tmp_path = None
        finally:
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
    except Exception as e:
        log(f"persist_active_trade_fallback_file error user={user_id} err={e}", "ERROR")


def _load_persisted_active_trade_fallback_file(user_id: int) -> Optional[dict[str, Any]]:
    try:
        path = _active_trade_state_path(user_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return dict(data) if isinstance(data, dict) else None
    except Exception as e:
        log(f"load_persisted_active_trade_fallback_file error user={user_id} err={e}", "WARN")
        return None


def _delete_persisted_active_trade_fallback_file(user_id: int) -> None:
    try:
        path = _active_trade_state_path(user_id)
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log(f"delete_persisted_active_trade_fallback_file error user={user_id} err={e}", "WARN")


def _persist_active_trade_snapshot(user_id: int, trade_data: dict[str, Any]) -> None:
    persisted_in_mongo = _persist_active_trade_mongo(user_id, trade_data)
    if not persisted_in_mongo:
        log(f"active_trade persistence fallback=user={user_id} backend=file", "WARN")
    _persist_active_trade_fallback_file(user_id, trade_data)


def _load_persisted_active_trade_snapshot(user_id: int) -> Optional[dict[str, Any]]:
    mongo_state = _load_persisted_active_trade_mongo(user_id)
    if isinstance(mongo_state, dict):
        return mongo_state
    return _load_persisted_active_trade_fallback_file(user_id)


def _delete_persisted_active_trade_snapshot(user_id: int) -> None:
    _delete_persisted_active_trade_mongo(user_id)
    _delete_persisted_active_trade_fallback_file(user_id)


def _set_active_trade(user_id: int, trade_data: dict[str, Any]) -> None:
    snapshot = dict(trade_data or {})
    with _user_active_trade_guard:
        _user_active_trades[user_id] = snapshot
    _persist_active_trade_snapshot(user_id, snapshot)


def _update_active_trade_fields(user_id: int, **fields: Any) -> None:
    current = _get_active_trade(user_id) or {}
    current.update(fields)
    _set_active_trade(user_id, current)


def _get_active_trade(user_id: int) -> Optional[dict[str, Any]]:
    with _user_active_trade_guard:
        data = _user_active_trades.get(user_id)
        if isinstance(data, dict):
            return dict(data)

    persisted = _load_persisted_active_trade_snapshot(user_id)
    if isinstance(persisted, dict):
        with _user_active_trade_guard:
            _user_active_trades[user_id] = dict(persisted)
        return dict(persisted)

    return None


def _clear_active_trade(user_id: int) -> None:
    with _user_active_trade_guard:
        _user_active_trades.pop(user_id, None)
    _delete_persisted_active_trade_snapshot(user_id)

def _infer_price_decimals(*values: Any) -> int:
    decimals: list[int] = []
    for val in values:
        if val is None:
            continue
        try:
            s = f"{float(val):.12f}"
        except Exception:
            try:
                s = str(val)
            except Exception:
                continue
        if "e" in s.lower():
            try:
                s = f"{float(s):.12f}"
            except Exception:
                continue
        if "." not in s:
            continue
        frac = s.split(".", 1)[1].rstrip("0")
        if frac:
            decimals.append(len(frac))
    if not decimals:
        return max(STOP_TRIGGER_DECIMALS_MIN, min(STOP_TRIGGER_DECIMALS_MAX, STOP_TRIGGER_DECIMALS_FALLBACK))
    inferred = max(decimals)
    return max(STOP_TRIGGER_DECIMALS_MIN, min(STOP_TRIGGER_DECIMALS_MAX, inferred))


def _round_trigger_price(price: float, *, direction: str, decimals: int, exit_kind: str = "stop") -> float:
    factor = 10 ** int(decimals)
    px = float(price or 0.0)
    if px <= 0.0 or factor <= 0:
        return 0.0

    direction_l = str(direction).lower()
    kind_l = str(exit_kind or "stop").lower()
    round_up = (direction_l == "short") if kind_l == "stop" else (direction_l == "long")
    if round_up:
        return (int(px * factor + 0.999999999)) / factor
    return (int(px * factor)) / factor


def _build_stop_trigger_candidates(*, raw_trigger: float, current_px: float, direction: str) -> list[float]:
    if raw_trigger <= 0.0:
        return []
    decimals_base = _infer_price_decimals(raw_trigger, current_px)
    candidates: list[float] = []
    seen: set[float] = set()
    for dec in [decimals_base, decimals_base - 1, decimals_base - 2, STOP_TRIGGER_DECIMALS_FALLBACK, 5, 4]:
        if dec < STOP_TRIGGER_DECIMALS_MIN:
            continue
        if dec > STOP_TRIGGER_DECIMALS_MAX:
            dec = STOP_TRIGGER_DECIMALS_MAX
        candidate = _round_trigger_price(raw_trigger, direction=direction, decimals=dec, exit_kind="stop")
        if current_px > 0.0:
            step = 1.0 / (10 ** int(dec))
            buf = max(0.0005, float(ADOPT_STOP_BUFFER_PCT))
            if str(direction).lower() == "short":
                min_valid = float(current_px) * (1.0 + buf)
                if candidate <= min_valid:
                    candidate = _round_trigger_price(min_valid + step, direction=direction, decimals=dec, exit_kind="stop")
            else:
                max_valid = float(current_px) * (1.0 - buf)
                if candidate >= max_valid:
                    candidate = _round_trigger_price(max_valid - step, direction=direction, decimals=dec, exit_kind="stop")
        candidate = float(candidate)
        if candidate > 0.0 and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def _build_take_profit_trigger_candidates(*, raw_trigger: float, current_px: float, direction: str) -> list[float]:
    if raw_trigger <= 0.0:
        return []
    decimals_base = _infer_price_decimals(raw_trigger, current_px)
    candidates: list[float] = []
    seen: set[float] = set()
    for dec in [decimals_base, decimals_base - 1, decimals_base - 2, STOP_TRIGGER_DECIMALS_FALLBACK, 5, 4]:
        if dec < STOP_TRIGGER_DECIMALS_MIN:
            continue
        if dec > STOP_TRIGGER_DECIMALS_MAX:
            dec = STOP_TRIGGER_DECIMALS_MAX
        candidate = _round_trigger_price(raw_trigger, direction=direction, decimals=dec, exit_kind="take_profit")
        if current_px > 0.0:
            step = 1.0 / (10 ** int(dec))
            buf = max(0.0005, float(ADOPT_STOP_BUFFER_PCT))
            if str(direction).lower() == "short":
                max_valid = float(current_px) * (1.0 - buf)
                if candidate >= max_valid:
                    candidate = _round_trigger_price(max_valid - step, direction=direction, decimals=dec, exit_kind="take_profit")
            else:
                min_valid = float(current_px) * (1.0 + buf)
                if candidate <= min_valid:
                    candidate = _round_trigger_price(min_valid + step, direction=direction, decimals=dec, exit_kind="take_profit")
        candidate = float(candidate)
        if candidate > 0.0 and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def _is_valid_bracket_pair(*, stop_trigger: float, take_profit_trigger: float, direction: str) -> bool:
    if stop_trigger <= 0.0 or take_profit_trigger <= 0.0:
        return False
    if str(direction).lower() == "short":
        return float(take_profit_trigger) < float(stop_trigger)
    return float(stop_trigger) < float(take_profit_trigger)


def _same_live_position(active_trade: Optional[dict[str, Any]], *, symbol: str, direction: str, entry_price: float) -> bool:
    if not isinstance(active_trade, dict):
        return False
    if str(active_trade.get("symbol") or "") != str(symbol or ""):
        return False
    if str(active_trade.get("direction") or "").lower() != str(direction or "").lower():
        return False
    try:
        stored_entry = float(active_trade.get("entry_price") or 0.0)
        live_entry = float(entry_price or 0.0)
    except Exception:
        return False
    if stored_entry <= 0.0 or live_entry <= 0.0:
        return False
    diff_pct = abs(stored_entry - live_entry) / max(live_entry, 1e-12)
    return diff_pct <= 0.005


def _fetch_frontend_open_orders(user_id: int) -> list[dict[str, Any]]:
    """Lee órdenes abiertas del usuario desde Hyperliquid.
    Usamos frontendOpenOrders porque expone isTrigger/reduceOnly/triggerPx.
    """
    try:
        wallet = get_user_wallet(user_id)
        if not wallet:
            return []
        r = make_request("/info", {"type": "frontendOpenOrders", "user": wallet})
        if isinstance(r, list):
            return [x for x in r if isinstance(x, dict)]
        return []
    except Exception as e:
        log(f"frontendOpenOrders error user={user_id} err={e}", "WARN")
        return []


def _classify_reduce_only_trigger_role(*, order: dict[str, Any], current_px: float, direction: str) -> str:
    """Distingue SL vs TP al leer frontendOpenOrders.

    Hyperliquid expone ``orderType`` y ``triggerCondition`` en frontendOpenOrders,
    pero dependiendo del caso no siempre vienen uniformes. Primero intentamos
    clasificar por metadata textual y, si no alcanza, inferimos por la posición
    relativa del trigger frente al precio actual.
    """
    try:
        order_type = str(order.get("orderType") or "").strip().lower()
        trigger_condition = str(order.get("triggerCondition") or "").strip().lower()

        if "take" in order_type or "tp" in order_type or "profit" in trigger_condition:
            return "tp"
        if "stop" in order_type or "sl" in order_type or "loss" in trigger_condition:
            return "sl"

        trig = float(order.get("triggerPx") or 0.0)
        if trig <= 0.0 or current_px <= 0.0:
            return "unknown"

        tol = _stop_price_tolerance(trig, current_px)
        if str(direction).lower() == "short":
            if trig > (current_px + tol):
                return "sl"
            if trig < (current_px - tol):
                return "tp"
        else:
            if trig < (current_px - tol):
                return "sl"
            if trig > (current_px + tol):
                return "tp"
    except Exception:
        return "unknown"
    return "unknown"


def _list_live_exchange_take_profits(user_id: int, symbol_for_exec: str, direction: str) -> list[dict[str, Any]]:
    coin = _norm_coin(symbol_for_exec)
    expected_side = "A" if str(direction).lower() == "long" else "B"
    found: list[dict[str, Any]] = []
    orders = _fetch_frontend_open_orders(user_id)
    try:
        current_px = float(get_price(symbol_for_exec) or 0.0)
    except Exception:
        current_px = 0.0
    for od in orders:
        try:
            if _norm_coin(str(od.get("coin") or "")) != coin:
                continue
            if not bool(od.get("isTrigger")):
                continue
            if not bool(od.get("reduceOnly")):
                continue
            if str(od.get("side") or "").upper() != expected_side:
                continue
            trig = float(od.get("triggerPx") or 0.0)
            if trig <= 0.0:
                continue
            role = _classify_reduce_only_trigger_role(order=od, current_px=float(current_px), direction=str(direction))
            if role != "tp":
                continue
            found.append({"trigger_price": float(trig), "raw": od})
        except Exception:
            continue
    if str(direction).lower() == "short":
        found.sort(key=lambda item: float(item.get("trigger_price") or 0.0), reverse=True)
    else:
        found.sort(key=lambda item: float(item.get("trigger_price") or 0.0))
    return found


def _current_take_profit_trigger(user_id: int, symbol_for_exec: str, direction: str) -> float:
    tps = _list_live_exchange_take_profits(user_id, symbol_for_exec, direction)
    if not tps:
        return 0.0
    try:
        return float(tps[0].get("trigger_price") or 0.0)
    except Exception:
        return 0.0


def _tp_price_tolerance(*values: Any) -> float:
    decimals = _infer_price_decimals(*values)
    return max(1e-8, 1.0 / (10 ** max(2, int(decimals))))


def _is_same_take_profit(*, current_trigger: float, desired_trigger: float) -> bool:
    tol = _tp_price_tolerance(current_trigger, desired_trigger)
    return abs(float(current_trigger) - float(desired_trigger)) <= tol



def _extract_exchange_order_oid(order: dict[str, Any]) -> int | None:
    if not isinstance(order, dict):
        return None
    for key in ("oid", "orderId", "order_id", "o"):
        try:
            value = order.get(key)
            if value is None:
                continue
            return int(str(value).strip())
        except Exception:
            continue
    return None


def _live_exchange_triggers(user_id: int, symbol_for_exec: str, direction: str) -> list[dict[str, Any]]:
    coin = _norm_coin(symbol_for_exec)
    expected_side = "A" if str(direction).lower() == "long" else "B"
    try:
        current_px = float(get_price(symbol_for_exec) or 0.0)
    except Exception:
        current_px = 0.0
    found: list[dict[str, Any]] = []
    for od in _fetch_frontend_open_orders(user_id):
        try:
            if _norm_coin(str(od.get("coin") or od.get("symbol") or "")) != coin:
                continue
            if not bool(od.get("isTrigger")) or not bool(od.get("reduceOnly")):
                continue
            if str(od.get("side") or "").upper() != expected_side:
                continue
            trig = float(od.get("triggerPx") or 0.0)
            if trig <= 0.0:
                continue
            oid = _extract_exchange_order_oid(od)
            if oid is None:
                continue
            role = _classify_reduce_only_trigger_role(order=od, current_px=current_px, direction=direction)
            found.append({"oid": int(oid), "trigger_price": trig, "role": role, "raw": od})
        except Exception:
            continue
    return found


def _consolidate_exchange_trigger_role(
    *,
    user_id: int,
    symbol_for_exec: str,
    direction: str,
    role: str,
    desired_trigger: float,
    context: str,
    retries: int = 3,
) -> bool:
    """Deja exactamente una trigger reduce-only del rol indicado y al precio deseado.

    Se usa después de colocar/reemplazar una protección. Nunca elimina la única
    protección sin que exista otra con el precio deseado ya aceptada por el exchange.
    """
    desired_trigger = float(desired_trigger or 0.0)
    if desired_trigger <= 0.0:
        return False
    tolerance_fn = _stop_price_tolerance if role == "sl" else _tp_price_tolerance
    for attempt in range(1, max(1, int(retries)) + 1):
        live = [x for x in _live_exchange_triggers(user_id, symbol_for_exec, direction) if x.get("role") == role]
        tol = tolerance_fn(desired_trigger)
        matches = [x for x in live if abs(float(x.get("trigger_price") or 0.0) - desired_trigger) <= tol]
        if not matches:
            if attempt < retries:
                time.sleep(0.15 * attempt)
                continue
            log(
                f"{context}: no existe trigger {role} en exchange al precio deseado={desired_trigger:.8f} live={live}",
                "CRITICAL",
            )
            return False

        keep = matches[0]
        cancel_ids = sorted({int(x["oid"]) for x in live if int(x["oid"]) != int(keep["oid"])})
        if cancel_ids:
            resp = cancel_orders(user_id=user_id, symbol=symbol_for_exec, order_ids=cancel_ids)
            if not isinstance(resp, dict) or not resp.get("ok"):
                log(
                    f"{context}: fallo cancelando triggers duplicadas role={role} keep={keep.get('oid')} cancel_ids={cancel_ids} resp={resp}",
                    "CRITICAL",
                )
                if attempt < retries:
                    time.sleep(0.20 * attempt)
                    continue
                return False
            log(
                f"{context}: triggers {role} consolidadas keep_oid={keep.get('oid')} cancelled={cancel_ids}",
                "WARN",
            )

        verify = [x for x in _live_exchange_triggers(user_id, symbol_for_exec, direction) if x.get("role") == role]
        tol = tolerance_fn(desired_trigger)
        valid = [x for x in verify if abs(float(x.get("trigger_price") or 0.0) - desired_trigger) <= tol]
        extras = [x for x in verify if int(x.get("oid") or 0) != int(valid[0].get("oid") or -1)] if valid else verify
        if len(valid) == 1 and not extras:
            return True
        if attempt < retries:
            time.sleep(0.15 * attempt)
    return False


def _ensure_exchange_take_profit_trigger(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    qty_coin: float,
    trigger_price: float,
    context: str,
) -> bool:
    try:
        if qty_coin <= 0 or trigger_price <= 0:
            log(
                f"{context}: parámetros inválidos para asegurar TP symbol={symbol} qty={qty_coin} trigger={trigger_price}",
                "ERROR",
            )
            return False

        existing_tp = 0.0 if str(context).upper().startswith("OPEN") else _current_take_profit_trigger(user_id, symbol_for_exec, direction)
        if existing_tp > 0.0 and _is_same_take_profit(current_trigger=float(existing_tp), desired_trigger=float(trigger_price)):
            log(
                f"TP_SYNC[{context}] mantiene TP existente coin={symbol} dir={direction} existing_trigger={existing_tp:.8f}",
                "INFO",
            )
            consolidated = _consolidate_exchange_trigger_role(
                user_id=user_id,
                symbol_for_exec=symbol_for_exec,
                direction=direction,
                role="tp",
                desired_trigger=float(existing_tp),
                context=f"TP_SYNC[{context}]",
            )
            if not consolidated:
                return False
            _update_active_trade_fields(
                user_id,
                tp_in_exchange=True,
                exchange_tp_trigger=float(existing_tp),
                exchange_tp_context=str(context),
                exchange_tp_updated_at=time.time(),
            )
            return True

        tp_resp = place_take_profit(
            user_id=user_id,
            symbol=symbol_for_exec,
            position_side=direction,
            qty=float(qty_coin),
            trigger_price=float(trigger_price),
        )
        ok = bool(isinstance(tp_resp, dict) and tp_resp.get("ok"))
        if ok:
            placed_trigger = float(tp_resp.get("triggerPx") or trigger_price)
            log(
                f"TP_SYNC[{context}] coin={symbol} dir={direction} trigger={placed_trigger:.8f} qty={float(qty_coin):.8f} status={tp_resp.get('reason')}",
                "WARN",
            )
            consolidated = _consolidate_exchange_trigger_role(
                user_id=user_id,
                symbol_for_exec=symbol_for_exec,
                direction=direction,
                role="tp",
                desired_trigger=float(placed_trigger),
                context=f"TP_SYNC[{context}]",
            )
            if not consolidated:
                log(
                    f"TP_SYNC[{context}] protección duplicada/no consolidada tras colocar nuevo TP coin={symbol} dir={direction}",
                    "CRITICAL",
                )
                return False
            _update_active_trade_fields(
                user_id,
                tp_in_exchange=True,
                exchange_tp_trigger=float(placed_trigger),
                exchange_tp_context=str(context),
                exchange_tp_updated_at=time.time(),
            )
            return True

        reason = (tp_resp or {}).get("reason") if isinstance(tp_resp, dict) else "NO_RESP"
        err = (tp_resp or {}).get("error", "") if isinstance(tp_resp, dict) else ""
        log(
            f"{context}: TP NO colocado en exchange coin={symbol} dir={direction} trigger={float(trigger_price):.8f} reason={reason} err={err}",
            "CRITICAL",
        )
        _update_active_trade_fields(
            user_id,
            tp_in_exchange=False,
            exchange_tp_context=str(context),
            exchange_tp_updated_at=time.time(),
        )
        return False
    except Exception as e:
        log(f"{context}: TP ERROR inesperado coin={symbol} dir={direction} err={e}", "CRITICAL")
        _update_active_trade_fields(
            user_id,
            tp_in_exchange=False,
            exchange_tp_context=str(context),
            exchange_tp_updated_at=time.time(),
        )
        return False


def _list_live_exchange_stops(user_id: int, symbol_for_exec: str, direction: str) -> list[dict[str, Any]]:
    coin = _norm_coin(symbol_for_exec)
    expected_side = "A" if str(direction).lower() == "long" else "B"
    found: list[dict[str, Any]] = []
    orders = _fetch_frontend_open_orders(user_id)
    try:
        current_px = float(get_price(symbol_for_exec) or 0.0)
    except Exception:
        current_px = 0.0
    for od in orders:
        try:
            if _norm_coin(str(od.get("coin") or "")) != coin:
                continue
            if not bool(od.get("isTrigger")):
                continue
            if not bool(od.get("reduceOnly")):
                continue
            if str(od.get("side") or "").upper() != expected_side:
                continue
            trig = float(od.get("triggerPx") or 0.0)
            if trig <= 0.0:
                continue
            role = _classify_reduce_only_trigger_role(order=od, current_px=float(current_px), direction=str(direction))
            if role != "sl":
                continue
            found.append({"trigger_price": float(trig), "raw": od})
        except Exception:
            continue
    if str(direction).lower() == "short":
        found.sort(key=lambda item: float(item.get("trigger_price") or 0.0))
    else:
        found.sort(key=lambda item: float(item.get("trigger_price") or 0.0), reverse=True)
    return found


def _has_live_exchange_stop(user_id: int, symbol_for_exec: str, direction: str) -> bool:
    return bool(_list_live_exchange_stops(user_id, symbol_for_exec, direction))


def _current_protective_stop_trigger(user_id: int, symbol_for_exec: str, direction: str) -> float:
    stops = _list_live_exchange_stops(user_id, symbol_for_exec, direction)
    if not stops:
        return 0.0
    try:
        return float(stops[0].get("trigger_price") or 0.0)
    except Exception:
        return 0.0


def _stop_price_tolerance(*values: Any) -> float:
    decimals = _infer_price_decimals(*values)
    return max(1e-8, 1.0 / (10 ** max(2, int(decimals))))


def _is_stop_improvement(*, current_trigger: float, desired_trigger: float, direction: str) -> bool:
    tol = _stop_price_tolerance(current_trigger, desired_trigger)
    if str(direction).lower() == "short":
        return float(desired_trigger) < (float(current_trigger) - tol)
    return float(desired_trigger) > (float(current_trigger) + tol)


def _ensure_exchange_stop_trigger(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    qty_coin: float,
    trigger_price: float,
    context: str,
    replace_existing: bool = True,
) -> bool:
    try:
        if qty_coin <= 0 or trigger_price <= 0:
            log(
                f"{context}: parámetros inválidos para asegurar stop symbol={symbol} qty={qty_coin} trigger={trigger_price}",
                "ERROR",
            )
            return False

        current_px = 0.0
        try:
            current_px = float(get_price(symbol_for_exec) or 0.0)
        except Exception:
            current_px = 0.0

        trigger_candidates = _build_stop_trigger_candidates(
            raw_trigger=float(trigger_price),
            current_px=float(current_px),
            direction=str(direction),
        ) or [float(trigger_price)]
        desired_trigger = float(trigger_candidates[0])

        existing_stops = [] if str(context).upper().startswith("OPEN") else _list_live_exchange_stops(user_id, symbol_for_exec, direction)
        existing_trigger = float(existing_stops[0].get("trigger_price") or 0.0) if existing_stops else 0.0

        if existing_trigger > 0.0:
            should_improve = _is_stop_improvement(
                current_trigger=float(existing_trigger),
                desired_trigger=float(desired_trigger),
                direction=str(direction),
            )
            if not should_improve:
                log(
                    f"STOP_SYNC[{context}] conserva mejor protección coin={symbol} dir={direction} existing_trigger={existing_trigger:.8f} desired_trigger={desired_trigger:.8f}",
                    "INFO",
                )
                consolidated = _consolidate_exchange_trigger_role(
                    user_id=user_id,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    role="sl",
                    desired_trigger=float(existing_trigger),
                    context=f"STOP_SYNC[{context}]",
                )
                if not consolidated:
                    return False
                _update_active_trade_fields(
                    user_id,
                    sl_in_exchange=True,
                    exchange_stop_trigger=float(existing_trigger),
                    exchange_stop_context=str(context),
                    exchange_stop_updated_at=time.time(),
                )
                return True

            log(
                f"STOP_SYNC[{context}] reemplazo de protección existente coin={symbol} dir={direction} existing_trigger={existing_trigger:.8f} desired_trigger={desired_trigger:.8f}",
                "WARN",
            )

        attempt_errors: list[str] = []
        for idx, sl_trigger in enumerate(trigger_candidates, start=1):
            sl_resp = place_stop_loss(
                user_id=user_id,
                symbol=symbol_for_exec,
                position_side=direction,
                qty=float(qty_coin),
                trigger_price=float(sl_trigger),
            )
            ok = bool(isinstance(sl_resp, dict) and sl_resp.get("ok"))
            if ok:
                placed_trigger = float(sl_resp.get("triggerPx") or sl_trigger)
                log(
                    f"STOP_SYNC[{context}] coin={symbol} dir={direction} trigger={placed_trigger:.8f} qty={float(qty_coin):.8f} "
                    f"status={sl_resp.get('reason')} current={current_px if current_px > 0 else 'n/a'} attempts={idx}",
                    "WARN",
                )
                consolidated = _consolidate_exchange_trigger_role(
                    user_id=user_id,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    role="sl",
                    desired_trigger=float(placed_trigger),
                    context=f"STOP_SYNC[{context}]",
                )
                if not consolidated:
                    log(
                        f"STOP_SYNC[{context}] protección duplicada/no consolidada tras colocar nuevo stop coin={symbol} dir={direction}",
                        "CRITICAL",
                    )
                    return False
                _update_active_trade_fields(
                    user_id,
                    sl_in_exchange=True,
                    exchange_stop_trigger=float(placed_trigger),
                    exchange_stop_context=str(context),
                    exchange_stop_updated_at=time.time(),
                )
                return True

            reason = (sl_resp or {}).get("reason") if isinstance(sl_resp, dict) else "NO_RESP"
            err = (sl_resp or {}).get("error", "") if isinstance(sl_resp, dict) else ""
            attempt_errors.append(f"try{idx}:{float(sl_trigger):.8f}:{reason}:{err}")

        if existing_trigger > 0.0:
            log(
                f"STOP_SYNC[{context}] no pudo mejorar stop; mantiene protección previa coin={symbol} dir={direction} existing_trigger={existing_trigger:.8f} attempts={' | '.join(attempt_errors)}",
                "CRITICAL",
            )
            _update_active_trade_fields(
                user_id,
                sl_in_exchange=True,
                exchange_stop_trigger=float(existing_trigger),
                exchange_stop_context=str(context),
                exchange_stop_updated_at=time.time(),
            )
            return True

        log(
            f"{context}: STOP NO colocado en exchange coin={symbol} dir={direction} trigger_candidates={trigger_candidates} "
            f"current~{current_px:.8f} attempts={' | '.join(attempt_errors)}",
            "CRITICAL",
        )
        _update_active_trade_fields(
            user_id,
            sl_in_exchange=False,
            exchange_stop_context=str(context),
            exchange_stop_updated_at=time.time(),
        )
        return False
    except Exception as e:
        log(f"{context}: STOP ERROR inesperado coin={symbol} dir={direction} err={e}", "CRITICAL")
        existing_trigger = _current_protective_stop_trigger(user_id, symbol_for_exec, direction)
        _update_active_trade_fields(
            user_id,
            sl_in_exchange=bool(existing_trigger > 0.0),
            exchange_stop_trigger=float(existing_trigger or 0.0),
            exchange_stop_context=str(context),
            exchange_stop_updated_at=time.time(),
        )
        return bool(existing_trigger > 0.0)


def _cleanup_stale_reduce_only_triggers_before_open(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    retries: int = 4,
) -> bool:
    """Garantiza que no quede ningún trigger reduce-only antes de una entrada.

    Se considera éxito solo cuando el exchange vuelve a reportar cero triggers
    reduce-only para ese símbolo/lado. Si falla la cancelación, se reintenta.
    """
    for attempt in range(1, max(1, int(retries)) + 1):
        try:
            resp = cancel_reduce_only_triggers_for_symbol_side(
                user_id=user_id,
                symbol=symbol_for_exec,
                position_side=direction,
            )
            live = [
                x for x in _live_exchange_triggers(user_id, symbol_for_exec, direction)
                if x.get("role") in {"sl", "tp", "unknown"}
            ]
            if not live:
                reason = (resp or {}).get("reason") if isinstance(resp, dict) else "NO_RESP"
                log(
                    f"OPEN_PROTECTION_CLEANUP coin={symbol} dir={direction} verified_empty attempt={attempt} reason={reason}",
                    "INFO",
                )
                return True
            cancelled = list((resp or {}).get("cancelled") or []) if isinstance(resp, dict) else []
            log(
                f"OPEN_PROTECTION_CLEANUP coin={symbol} dir={direction} remaining={live} cancelled={cancelled} attempt={attempt}",
                "CRITICAL",
            )
        except Exception as e:
            log(f"OPEN_PROTECTION_CLEANUP coin={symbol} dir={direction} attempt={attempt} err={e}", "CRITICAL")
        if attempt < retries:
            time.sleep(0.20 * attempt)
    return False


def _ensure_exchange_protection_pair(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    qty_coin: float,
    stop_trigger_price: float,
    take_profit_trigger_price: float,
    context: str,
    stop_mode: str = "initial",
    replace_existing: bool = True,
) -> dict[str, Any]:
    """Asegura la protección TP/SL en exchange sin dejar la posición desnuda.

    Reglas de seguridad:
    - nunca cancela primero una protección viva; primero intenta colocar la nueva
    - con ``replace_existing=True`` restaura exactamente el TP/SL fijo del plan
    - si el exchange rechaza el reemplazo, conserva la protección previa
    - si falta una pata, intenta añadirla sin tocar la otra
    """
    result = {
        "ok": False,
        "sl_ok": False,
        "tp_ok": False,
        "reason": "UNKNOWN",
        "stop_trigger": 0.0,
        "tp_trigger": 0.0,
        "raw": None,
    }
    try:
        if qty_coin <= 0 or stop_trigger_price <= 0 or take_profit_trigger_price <= 0:
            log(
                f"{context}: parámetros inválidos para asegurar protección symbol={symbol} qty={qty_coin} stop={stop_trigger_price} tp={take_profit_trigger_price}",
                "ERROR",
            )
            result["reason"] = "INVALID_PARAMS"
            return result

        current_px = 0.0
        try:
            current_px = float(get_price(symbol_for_exec) or 0.0)
        except Exception:
            current_px = 0.0

        stop_candidates = _build_stop_trigger_candidates(
            raw_trigger=float(stop_trigger_price),
            current_px=float(current_px),
            direction=str(direction),
        ) or [float(stop_trigger_price)]
        tp_candidates = _build_take_profit_trigger_candidates(
            raw_trigger=float(take_profit_trigger_price),
            current_px=float(current_px),
            direction=str(direction),
        ) or [float(take_profit_trigger_price)]

        desired_stop = float(stop_candidates[0])
        desired_tp = float(tp_candidates[0])
        if str(context).upper().startswith("OPEN"):
            existing_stop = 0.0
            existing_tp = 0.0
        else:
            existing_stop = _current_protective_stop_trigger(user_id, symbol_for_exec, direction)
            existing_tp = _current_take_profit_trigger(user_id, symbol_for_exec, direction)

        stop_ok = False
        tp_ok = False
        stop_live = float(existing_stop or 0.0)
        tp_live = float(existing_tp or 0.0)

        if existing_stop > 0.0:
            stop_tol = _stop_price_tolerance(existing_stop, desired_stop)
            stop_matches_plan = abs(float(existing_stop) - float(desired_stop)) <= float(stop_tol)

            if bool(replace_existing) and (not stop_matches_plan):
                # Recovery/adopt: el plan es TP/SL fijo. Colocamos primero el SL
                # original y solo después consolidamos, para no dejar la posición desnuda.
                forced_resp = place_stop_loss(
                    user_id=user_id,
                    symbol=symbol_for_exec,
                    position_side=direction,
                    qty=float(qty_coin),
                    trigger_price=float(desired_stop),
                )
                forced_ok = bool(isinstance(forced_resp, dict) and forced_resp.get("ok"))
                if forced_ok:
                    placed_stop = float(forced_resp.get("triggerPx") or desired_stop)
                    stop_ok = _consolidate_exchange_trigger_role(
                        user_id=user_id,
                        symbol_for_exec=symbol_for_exec,
                        direction=direction,
                        role="sl",
                        desired_trigger=float(placed_stop),
                        context=f"{context}_STOP_RESTORE_FIXED",
                    )
                    stop_live = float(placed_stop) if stop_ok else float(existing_stop)
                    log(
                        f"STOP_SYNC[{context}] restaura SL fijo coin={symbol} dir={direction} "
                        f"old={float(existing_stop):.8f} fixed={float(placed_stop):.8f} ok={bool(stop_ok)}",
                        "WARN",
                    )
                else:
                    stop_ok = _consolidate_exchange_trigger_role(
                        user_id=user_id,
                        symbol_for_exec=symbol_for_exec,
                        direction=direction,
                        role="sl",
                        desired_trigger=float(existing_stop),
                        context=f"{context}_STOP_KEEP_PREVIOUS",
                    )
                    stop_live = float(existing_stop)
                    log(
                        f"STOP_SYNC[{context}] no pudo restaurar SL fijo; conserva protección previa "
                        f"coin={symbol} dir={direction} existing={float(existing_stop):.8f} desired={float(desired_stop):.8f} resp={forced_resp}",
                        "CRITICAL",
                    )
            elif _is_stop_improvement(current_trigger=float(existing_stop), desired_trigger=float(desired_stop), direction=str(direction)):
                stop_ok = _ensure_exchange_stop_trigger(
                    user_id=user_id,
                    symbol=symbol,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    qty_coin=float(qty_coin),
                    trigger_price=float(desired_stop),
                    context=f"{context}_STOP_UPGRADE",
                    replace_existing=True,
                )
                stop_live = _current_protective_stop_trigger(user_id, symbol_for_exec, direction) if stop_ok else float(existing_stop)
            else:
                stop_ok = _consolidate_exchange_trigger_role(
                    user_id=user_id,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    role="sl",
                    desired_trigger=float(existing_stop),
                    context=f"{context}_STOP_CONSOLIDATE",
                )
                stop_live = float(existing_stop)

        if existing_tp > 0.0:
            tp_matches_plan = _is_same_take_profit(current_trigger=float(existing_tp), desired_trigger=float(desired_tp))
            if bool(replace_existing) and (not tp_matches_plan):
                tp_ok = _ensure_exchange_take_profit_trigger(
                    user_id=user_id,
                    symbol=symbol,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    qty_coin=float(qty_coin),
                    trigger_price=float(desired_tp),
                    context=f"{context}_TP_RESTORE_FIXED",
                )
                tp_live = _current_take_profit_trigger(user_id, symbol_for_exec, direction) if tp_ok else float(existing_tp)
            else:
                tp_ok = _consolidate_exchange_trigger_role(
                    user_id=user_id,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    role="tp",
                    desired_trigger=float(existing_tp),
                    context=f"{context}_TP_CONSOLIDATE",
                )
                tp_live = float(existing_tp)

        if not stop_ok and existing_stop <= 0.0 and existing_tp <= 0.0:
            attempt_errors: list[str] = []
            for stop_idx, stop_candidate in enumerate(stop_candidates[:6], start=1):
                for tp_idx, tp_candidate in enumerate(tp_candidates[:6], start=1):
                    if not _is_valid_bracket_pair(
                        stop_trigger=float(stop_candidate),
                        take_profit_trigger=float(tp_candidate),
                        direction=str(direction),
                    ):
                        continue
                    pair_resp = place_position_tpsl_pair(
                        user_id=user_id,
                        symbol=symbol_for_exec,
                        position_side=direction,
                        qty=float(qty_coin),
                        stop_trigger_price=float(stop_candidate),
                        take_profit_trigger_price=float(tp_candidate),
                    )
                    if isinstance(pair_resp, dict) and pair_resp.get("ok"):
                        placed_stop = float(pair_resp.get("stopTriggerPx") or stop_candidate)
                        placed_tp = float(pair_resp.get("tpTriggerPx") or tp_candidate)
                        log(
                            f"PROTECTION_SYNC[{context}] coin={symbol} dir={direction} stop={placed_stop:.8f} tp={placed_tp:.8f} qty={float(qty_coin):.8f} status={pair_resp.get('reason')} current={current_px if current_px > 0 else 'n/a'} attempts={stop_idx}/{tp_idx}",
                            "WARN",
                        )
                        _update_active_trade_fields(
                            user_id,
                            sl_in_exchange=True,
                            tp_in_exchange=True,
                            exchange_stop_trigger=float(placed_stop),
                            exchange_stop_context=str(context),
                            exchange_stop_mode=str(stop_mode or "initial"),
                            exchange_stop_reference_price=float(placed_stop),
                            exchange_stop_updated_at=time.time(),
                            exchange_tp_trigger=float(placed_tp),
                            exchange_tp_context=str(context),
                            exchange_tp_mode="fixed",
                            exchange_tp_reference_price=float(placed_tp),
                            exchange_tp_updated_at=time.time(),
                        )
                        sl_consolidated = _consolidate_exchange_trigger_role(
                            user_id=user_id,
                            symbol_for_exec=symbol_for_exec,
                            direction=direction,
                            role="sl",
                            desired_trigger=float(placed_stop),
                            context=f"PROTECTION_SYNC[{context}]_SL",
                        )
                        tp_consolidated = _consolidate_exchange_trigger_role(
                            user_id=user_id,
                            symbol_for_exec=symbol_for_exec,
                            direction=direction,
                            role="tp",
                            desired_trigger=float(placed_tp),
                            context=f"PROTECTION_SYNC[{context}]_TP",
                        )
                        if not (sl_consolidated and tp_consolidated):
                            log(
                                f"PROTECTION_SYNC[{context}] pair aceptado pero no pudo consolidarse a 1 SL + 1 TP coin={symbol} dir={direction}",
                                "CRITICAL",
                            )
                            continue
                        result.update({
                            "ok": True,
                            "sl_ok": True,
                            "tp_ok": True,
                            "reason": str(pair_resp.get("reason") or "PAIR_ACCEPTED"),
                            "stop_trigger": float(placed_stop),
                            "tp_trigger": float(placed_tp),
                            "raw": pair_resp,
                        })
                        return result
                    reason = (pair_resp or {}).get("reason") if isinstance(pair_resp, dict) else "NO_RESP"
                    err = (pair_resp or {}).get("error", "") if isinstance(pair_resp, dict) else ""
                    attempt_errors.append(f"sl{stop_idx}/tp{tp_idx}:{float(stop_candidate):.8f}|{float(tp_candidate):.8f}:{reason}:{err}")
            log(
                f"{context}: PROTECTION PAIR NO colocada en exchange coin={symbol} dir={direction} stop_candidates={stop_candidates} tp_candidates={tp_candidates} current~{current_px:.8f} attempts={' | '.join(attempt_errors)}",
                "CRITICAL",
            )

        if not stop_ok:
            stop_ok = _ensure_exchange_stop_trigger(
                user_id=user_id,
                symbol=symbol,
                symbol_for_exec=symbol_for_exec,
                direction=direction,
                qty_coin=float(qty_coin),
                trigger_price=float(desired_stop),
                context=f"{context}_SL_FALLBACK",
                replace_existing=False,
            )
            stop_live = _current_protective_stop_trigger(user_id, symbol_for_exec, direction) if stop_ok else float(existing_stop or 0.0)

        if not tp_ok:
            tp_ok = _ensure_exchange_take_profit_trigger(
                user_id=user_id,
                symbol=symbol,
                symbol_for_exec=symbol_for_exec,
                direction=direction,
                qty_coin=float(qty_coin),
                trigger_price=float(desired_tp),
                context=f"{context}_TP_FALLBACK",
            )
            tp_live = _current_take_profit_trigger(user_id, symbol_for_exec, direction) if tp_ok else float(existing_tp or 0.0)

        _update_active_trade_fields(
            user_id,
            sl_in_exchange=bool(stop_ok),
            tp_in_exchange=bool(tp_ok),
            exchange_stop_trigger=float(stop_live or 0.0),
            exchange_stop_context=str(context),
            exchange_stop_mode=str(stop_mode or "initial"),
            exchange_stop_reference_price=float(stop_live or desired_stop or 0.0),
            exchange_stop_updated_at=time.time(),
            exchange_tp_trigger=float(tp_live or 0.0),
            exchange_tp_context=str(context),
            exchange_tp_mode="fixed",
            exchange_tp_reference_price=float(tp_live or desired_tp or 0.0),
            exchange_tp_updated_at=time.time(),
        )

        result.update({
            "ok": bool(stop_ok),
            "sl_ok": bool(stop_ok),
            "tp_ok": bool(tp_ok),
            "reason": "PAIR_OK" if (stop_ok and tp_ok) else ("SL_ONLY_FALLBACK" if stop_ok else "PAIR_AND_SL_FAILED"),
            "stop_trigger": float(stop_live or 0.0),
            "tp_trigger": float(tp_live or 0.0),
            "raw": {
                "existing_stop": float(existing_stop or 0.0),
                "existing_tp": float(existing_tp or 0.0),
                "desired_stop": float(desired_stop),
                "desired_tp": float(desired_tp),
            },
        })
        return result
    except Exception as e:
        log(f"{context}: PROTECTION ERROR inesperado coin={symbol} dir={direction} err={e}", "CRITICAL")
        if str(context).upper().startswith("OPEN"):
            existing_stop = 0.0
            existing_tp = 0.0
        else:
            existing_stop = _current_protective_stop_trigger(user_id, symbol_for_exec, direction)
            existing_tp = _current_take_profit_trigger(user_id, symbol_for_exec, direction)
        _update_active_trade_fields(
            user_id,
            sl_in_exchange=bool(existing_stop > 0.0),
            tp_in_exchange=bool(existing_tp > 0.0),
            exchange_stop_trigger=float(existing_stop or 0.0),
            exchange_stop_context=str(context),
            exchange_stop_updated_at=time.time(),
            exchange_tp_trigger=float(existing_tp or 0.0),
            exchange_tp_context=str(context),
            exchange_tp_updated_at=time.time(),
        )
        result.update({
            "reason": f"EXCEPTION:{type(e).__name__}",
            "sl_ok": bool(existing_stop > 0.0),
            "tp_ok": bool(existing_tp > 0.0),
            "ok": bool(existing_stop > 0.0),
            "stop_trigger": float(existing_stop or 0.0),
            "tp_trigger": float(existing_tp or 0.0),
        })
        return result


def _ensure_exchange_stop_loss(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    entry_price: float,
    qty_coin: float,
    sl_price_pct: float,
    context: str,
) -> bool:
    try:
        if entry_price <= 0 or qty_coin <= 0 or sl_price_pct <= 0:
            log(
                f"{context}: parámetros inválidos para asegurar SL symbol={symbol} entry={entry_price} qty={qty_coin} sl_pct={sl_price_pct}",
                "ERROR",
            )
            return False

        raw_entry_trigger = _pct_to_abs_price(float(entry_price), float(sl_price_pct), str(direction), kind="sl")
        ok = _ensure_exchange_stop_trigger(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
            qty_coin=float(qty_coin),
            trigger_price=float(raw_entry_trigger),
            context=context,
            replace_existing=True,
        )
        if ok:
            log(
                f"STOP_LOSS_HIT_PLAN[{context}] coin={symbol} dir={direction} entry={float(entry_price):.8f} sl_pct={float(sl_price_pct):.6f} "
                f"sl_price={float(raw_entry_trigger):.8f} qty={float(qty_coin):.8f}",
                "WARN",
            )
        return ok
    except Exception as e:
        log(f"{context}: STOP ERROR inesperado coin={symbol} dir={direction} err={e}", "CRITICAL")
        return False


def _manager_is_running(user_id: int) -> bool:
    with _user_manager_guard:
        th = _user_manager_threads.get(user_id)
        return bool(th and th.is_alive())

def _start_manager_thread(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    side: str,
    opposite: str,
    entry_price: float,
    qty_coin_for_log: float,
    qty_usdc_for_profit: float,
    best_score: float,
    entry_strength: float,
    mode: str,
    sl_price_pct: float = 0.0,
    mgmt: Optional[dict[str, Any]] = None,
    opened_at_ms: Optional[int] = None,
    trade_plan: Optional[dict[str, Any]] = None,
    entry_context: Optional[dict[str, Any]] = None,
) -> bool:
    """Arranca un manager en background si no existe uno vivo para el usuario.
    Retorna True si se creó, False si ya había uno corriendo.
    """
    with _user_manager_guard:
        existing = _user_manager_threads.get(user_id)
        if existing and existing.is_alive():
            return False

        def _runner():
            try:
                _user_manager_meta[user_id] = {
                    "symbol": symbol,
                    "mode": mode,
                    "started_at": datetime.utcnow().isoformat(),
                    "opened_at_ms": int(opened_at_ms) if opened_at_ms else int(time.time() * 1000),
                }
                _manage_trade_until_close(
                    user_id=user_id,
                    symbol=symbol,
                    symbol_for_exec=symbol_for_exec,
                    direction=direction,
                    side=side,
                    opposite=opposite,
                    entry_price=float(entry_price),
                    qty_coin_for_log=float(qty_coin_for_log),
                    qty_usdc_for_profit=float(qty_usdc_for_profit),
                    best_score=float(best_score),
                    entry_strength=float(entry_strength),
                    mode=mode,
                    sl_price_pct=float(sl_price_pct),
                    mgmt=mgmt,
                )
            except Exception as e:
                log(f"MANAGER THREAD error user={user_id} symbol={symbol} err={e}\n{traceback.format_exc()}", "CRITICAL")
            finally:
                with _user_manager_guard:
                    _user_manager_threads.pop(user_id, None)
                    _user_manager_meta.pop(user_id, None)

        existing_runtime = _get_active_trade(user_id) or {}
        snapshot = _build_active_trade_snapshot(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
            side=side,
            opposite=opposite,
            entry_price=float(entry_price),
            qty_coin_for_log=float(qty_coin_for_log),
            qty_usdc_for_profit=float(qty_usdc_for_profit),
            best_score=float(best_score),
            entry_strength=float(entry_strength),
            mode=mode,
            sl_price_pct=float(sl_price_pct),
            mgmt=mgmt,
            opened_at_ms=opened_at_ms,
            trade_plan=trade_plan,
            entry_context=entry_context,
            existing_state=existing_runtime,
            manager_bootstrap_pending=False,
        )
        snapshot["manager_heartbeat_ts"] = time.time()
        _set_active_trade(user_id, snapshot)

        th = threading.Thread(target=_runner, name=f"mgr-{user_id}", daemon=True)
        _user_manager_threads[user_id] = th
        th.start()
        return True


def _try_begin_trade_finalize(user_id: int, source: str) -> Optional[dict[str, Any]]:
    """Idempotency guard for trade finalization.

    Returns a snapshot of the previous flags if this caller acquired the
    finalize lock. Returns None when another path is already finalizing or has
    finalized the trade.
    """
    try:
        active = _get_active_trade(user_id) or {}
    except Exception:
        active = {}

    close_in_progress = bool(active.get("close_in_progress", False))
    close_finalized = bool(active.get("close_finalized", False))

    if close_in_progress or close_finalized:
        return None

    snapshot = {
        "close_in_progress": close_in_progress,
        "close_finalized": close_finalized,
        "source": str(source or ""),
        "started_at": time.time(),
    }

    try:
        _update_active_trade_fields(
            user_id,
            close_in_progress=True,
            close_finalized=False,
            close_started_at=time.time(),
            close_source=str(source or ""),
        )
    except Exception as e:
        log(f"finalize guard set error user={user_id} src={source} err={e}", "WARN")

    return snapshot


# ============================================================
# LOG
# ============================================================

def log(msg: str, level: str = "INFO"):
    print(f"[ENGINE {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] {level} {msg}")

# ============================================================
# NORMALIZADOR (coin para ejecución real)
# ============================================================

def _norm_coin(symbol: str) -> str:
    try:
        s = (symbol or "").strip().upper()
        s = s.replace("-PERP", "").replace("_PERP", "").replace("PERP", "")
        if "/" in s:
            s = s.split("/", 1)[0].strip()
        return s
    except Exception:
        return symbol

# ============================================================
# DETECTOR DE FILL (compatible con cliente dict: ok/filled)
# ============================================================

def _has_positive_fill(obj: Any) -> bool:
    try:
        if isinstance(obj, dict) and "filled" in obj:
            return bool(obj.get("filled"))

        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = str(k).lower()

                if lk in ("filledsz", "fillsz", "filledsize", "filled", "fill"):
                    try:
                        if float(v) > 0:
                            return True
                    except Exception:
                        pass

                if lk in ("status", "state"):
                    sv = str(v).lower()
                    if "filled" in sv:
                        return True

                if _has_positive_fill(v):
                    return True

        elif isinstance(obj, list):
            for it in obj:
                if _has_positive_fill(it):
                    return True

        return False
    except Exception:
        return False

def _is_filled_exchange_response(resp: Any) -> bool:
    if not resp:
        return False
    return _has_positive_fill(resp)

def _resp_ok(resp: Any) -> bool:
    if isinstance(resp, dict) and "ok" in resp:
        return bool(resp.get("ok"))
    return bool(resp)

def _resp_reason(resp: Any) -> str:
    if isinstance(resp, dict):
        r = resp.get("reason") or resp.get("status") or resp.get("error") or resp.get("err") or ""
        return str(r)
    return ""

# ============================================================
# EXTRAER PRECIO DE FILL REAL (si viene en la respuesta)
# ============================================================

def _extract_fill_price(obj: Any) -> Optional[float]:
    """
    Busca un precio de fill/avgPx dentro de estructuras dict/list.
    Claves típicas: avgPx, averagePrice, fillPx, filledPx, price, px, entryPx...
    Devuelve float si encuentra > 0.
    """
    try:
        if isinstance(obj, dict):
            # claves directas comunes
            for key in ("avgPx", "averagePrice", "fillPx", "filledPx", "entryPx", "avg_price", "fill_price"):
                if key in obj:
                    try:
                        v = float(obj[key])
                        if v > 0:
                            return v
                    except Exception:
                        pass

            # a veces viene en "fills" como lista o estructuras anidadas
            for key in ("fills", "fill", "orders", "data", "result", "response"):
                if key in obj:
                    v2 = _extract_fill_price(obj[key])
                    if v2 and v2 > 0:
                        return v2

            # fallback: cualquier campo "px" o "price" válido (o recursión por otros campos)
            for k, v in obj.items():
                lk = str(k).lower()
                if lk in ("px", "price"):
                    try:
                        fv = float(v)
                        if fv > 0:
                            return fv
                    except Exception:
                        pass
                nested = _extract_fill_price(v)
                if nested and nested > 0:
                    return nested

        elif isinstance(obj, list):
            for it in obj:
                v = _extract_fill_price(it)
                if v and v > 0:
                    return v

        return None
    except Exception:
        return None

# ============================================================
# COOLDOWN POR SÍMBOLO (por usuario)
# ============================================================

def _get_excluded_symbols(user_id: int) -> set[str]:
    now = datetime.utcnow()
    m = user_symbol_cooldowns.get(user_id) or {}

    alive: dict[str, datetime] = {}
    exclude: set[str] = set()

    for sym, exp in m.items():
        try:
            if exp and now < exp:
                alive[sym] = exp
                exclude.add(sym)
        except Exception:
            continue

    user_symbol_cooldowns[user_id] = alive
    return exclude

def _cooldown_symbol(user_id: int, symbol: str, seconds: int = SYMBOL_NOFILL_COOLDOWN_SECONDS):
    try:
        sym = str(symbol or "").upper()
        if not sym:
            return
        m = user_symbol_cooldowns.setdefault(user_id, {})
        m[sym] = datetime.utcnow() + timedelta(seconds=int(seconds))
    except Exception:
        pass

# ============================================================
# RATE LIMIT (por usuario)
# ============================================================

def _hour_key(now: datetime) -> str:
    return now.strftime("%Y-%m-%d-%H")

def _can_trade_now(user_id: int) -> tuple[bool, str]:
    now = datetime.utcnow()
    # Per-user startup grace: prevents an immediate entry right after a deploy/restart.
    # We only set this once per process and per user, so normal cooldown rules still apply afterwards.
    if STARTUP_GRACE_SECONDS > 0 and user_id not in user_next_trade_time:
        user_next_trade_time[user_id] = PROCESS_START_TIME_UTC + timedelta(seconds=STARTUP_GRACE_SECONDS)
        log(f"Startup grace activo ({STARTUP_GRACE_SECONDS}s) para usuario {user_id}", "INFO")


    next_time = user_next_trade_time.get(user_id)
    if next_time and now < next_time:
        secs = int((next_time - now).total_seconds())
        return False, f"Cooldown usuario activo ({secs}s)"

    state = user_trade_counter.get(user_id)
    if not state:
        user_trade_counter[user_id] = {
            "hour_key": _hour_key(now),
            "hour_count": 0,
            "day": date.today(),
            "day_count": 0,
        }
        state = user_trade_counter[user_id]

    hk = _hour_key(now)
    if state["hour_key"] != hk:
        state["hour_key"] = hk
        state["hour_count"] = 0

    today = date.today()
    if state["day"] != today:
        state["day"] = today
        state["day_count"] = 0

    if MAX_TRADES_PER_HOUR is not None and state["hour_count"] >= MAX_TRADES_PER_HOUR:
        return False, f"Límite por hora alcanzado ({MAX_TRADES_PER_HOUR})"

    if MAX_TRADES_PER_DAY is not None and state["day_count"] >= MAX_TRADES_PER_DAY:
        return False, f"Límite por día alcanzado ({MAX_TRADES_PER_DAY})"

    return True, "OK"

def _register_trade_attempt(user_id: int):
    now = datetime.utcnow()
    state = user_trade_counter.setdefault(user_id, {
        "hour_key": _hour_key(now),
        "hour_count": 0,
        "day": date.today(),
        "day_count": 0,
    })

    hk = _hour_key(now)
    if state["hour_key"] != hk:
        state["hour_key"] = hk
        state["hour_count"] = 0

    today = date.today()
    if state["day"] != today:
        state["day"] = today
        state["day_count"] = 0

    state["hour_count"] += 1
    state["day_count"] += 1

    user_next_trade_time[user_id] = now + timedelta(seconds=USER_TRADE_COOLDOWN_SECONDS)



def _register_post_close_cooldown(user_id: int):
    """Aplica cooldown DESPUÉS de cerrar un trade (evita re-entrada inmediata al finalizar)."""
    try:
        user_next_trade_time[user_id] = datetime.utcnow() + timedelta(seconds=USER_TRADE_COOLDOWN_SECONDS)
    except Exception:
        # Nunca romper el engine por un fallo de cooldown
        pass

# ============================================================
# CICLO PRINCIPAL
# ============================================================

# ============================================================
# ✅ RECUPERACIÓN DE POSICIÓN ABIERTA (ANTI-RESTART)
# - Si el proceso se reinicia y queda una posición abierta en el exchange,
#   este manager la toma y aplica SL/TRAIL hasta cerrarla.
# - NO abre nuevas operaciones.
# ============================================================

def _get_first_open_position_coin(user_id: int) -> Optional[str]:
    """Devuelve el primer coin con posición REAL abierta (szi != 0) en HL."""
    try:
        wallet = get_user_wallet(user_id)
        if not wallet:
            return None

        r = make_request("/info", {"type": "clearinghouseState", "user": wallet})
        if not isinstance(r, dict):
            return None

        aps = r.get("assetPositions") or []
        if not isinstance(aps, list):
            return None

        for ap in aps:
            if not isinstance(ap, dict):
                continue
            pos = ap.get("position")
            if not isinstance(pos, dict):
                continue

            coin = (pos.get("coin") or ap.get("coin") or "").strip().upper()
            if not coin:
                continue

            try:
                szi = float(pos.get("szi", 0) or 0.0)
            except Exception:
                szi = 0.0

            if szi == 0.0:
                continue

            # Filtro de polvo (best-effort): si no podemos estimar notional, igual lo devolvemos por seguridad.
            try:
                px = float(get_price(coin) or 0.0)
            except Exception:
                px = 0.0

            if px > 0:
                notional = abs(szi) * px
                # si es extremadamente pequeño, lo ignoramos (el cliente ya intenta limpiar dust)
                if notional < float(MIN_NOTIONAL_USDC) * 0.25:
                    continue

            return _norm_coin(coin)

        return None
    except Exception:
        return None



def _extract_strategy_management_params(src: Optional[dict[str, Any]]) -> Optional[dict[str, float]]:
    if not isinstance(src, dict):
        return None

    candidates: list[dict[str, Any]] = []
    nested_plan = src.get("trade_plan")
    if isinstance(nested_plan, dict):
        candidates.append(nested_plan)
    candidates.append(src)

    for candidate in candidates:
        try:
            tp_activate = float(candidate.get("tp_activation_price", candidate.get("tp_activate_price", 0.0)) or 0.0)
            trail_retrace = float(candidate.get("trail_retrace_price", 0.0) or 0.0)
            force_min_profit = float(candidate.get("force_min_profit_price", 0.0) or 0.0)
            force_min_strength = float(candidate.get("force_min_strength", 0.0) or 0.0)
            partial_tp_activation = float(candidate.get("partial_tp_activation_price", 0.0) or 0.0)
            partial_tp_close_fraction = float(candidate.get("partial_tp_close_fraction", 0.0) or 0.0)
            bucket = str(candidate.get("mgmt_bucket") or candidate.get("bucket") or "strategy")
        except Exception:
            continue

        if tp_activate <= 0.0:
            continue

        trail_retrace = max(0.0, trail_retrace)
        force_min_profit = max(0.0, force_min_profit)
        force_min_strength = max(0.0, force_min_strength)
        if partial_tp_activation <= 0.0:
            partial_tp_activation = 0.0
        partial_tp_close_fraction = _engine_clamp(float(partial_tp_close_fraction or 0.0), 0.0, 0.45)

        return {
            "bucket": bucket,
            "tp_activate_price": tp_activate,
            "trail_retrace_price": 0.0,
            "force_min_profit_price": 0.0,
            "force_min_strength": 0.0,
            "partial_tp_activation_price": 0.0,
            "partial_tp_close_fraction": 0.0,
            "break_even_activation_price": 0.0,
            "break_even_offset_price": 0.0,
        }

    return None


def _disabled_management_params() -> dict[str, float]:
    # Si no hay parámetros strategy-driven disponibles, el engine no inventa
    # una lógica de salida propia. Solo deja el SL del exchange como protección.
    return {
        "bucket": "exchange_only",
        "tp_activate_price": 999999.0,
        "trail_retrace_price": 0.0,
        "force_min_profit_price": 999999.0,
        "force_min_strength": 0.0,
        "partial_tp_activation_price": 999999.0,
        "partial_tp_close_fraction": 0.0,
        "break_even_activation_price": 0.0,
        "break_even_offset_price": 0.0,
    }


def _has_frozen_trade_plan(active_trade: Optional[dict[str, Any]]) -> bool:
    if not isinstance(active_trade, dict):
        return False
    nested_plan = active_trade.get("trade_plan")
    if isinstance(nested_plan, dict):
        try:
            return float(nested_plan.get("tp_activation_price", nested_plan.get("tp_activate_price", 0.0)) or 0.0) > 0.0
        except Exception:
            pass
    try:
        return float(active_trade.get("tp_activation_price", active_trade.get("tp_activate_price", 0.0)) or 0.0) > 0.0
    except Exception:
        return False


def _coalesce_management_params(
    *,
    signal: Optional[dict[str, Any]] = None,
    active_trade: Optional[dict[str, Any]] = None,
    entry_strength: float = 0.0,
    best_score: float = 0.0,
) -> dict[str, float]:
    mgmt = _extract_strategy_management_params(signal)
    if mgmt is not None:
        return mgmt

    mgmt = _extract_strategy_management_params(active_trade)
    if mgmt is not None:
        return mgmt

    strategy_id = _safe_str(
        (signal or {}).get("strategy_id")
        or (active_trade or {}).get("strategy_id")
        or (active_trade or {}).get("strategy_model")
        or DEFAULT_STRATEGY_ID,
        DEFAULT_STRATEGY_ID,
    )
    try:
        derived = get_trade_management_params_for_strategy(strategy_id, float(entry_strength or 0.0), float(best_score or 0.0))
    except Exception as e:
        log(f"coalesce_management_params derive error strategy_id={strategy_id} strength={entry_strength} score={best_score} err={e}", "WARN")
        derived = None

    mgmt = _extract_strategy_management_params(derived)
    if mgmt is not None:
        return mgmt

    return _disabled_management_params()


def _should_close_on_strength_loss(

    *,
    symbol: str,
    direction: str,
    pnl_pct: float,
    entry_strength: float,
    force_min_profit_price: float,
    force_min_strength: float,
    last_check_ts: float,
    strategy_id: str = DEFAULT_STRATEGY_ID,
) -> tuple[bool, str, float, float]:
    """Re-evalúa la estrategia abierta con umbrales definidos por estrategia."""
    _ = entry_strength
    now_ts = time.time()
    if (now_ts - float(last_check_ts)) < float(TP_FORCE_CHECK_INTERVAL):
        return False, "", 0.0, last_check_ts

    if float(pnl_pct) < float(force_min_profit_price):
        return False, "", 0.0, now_ts

    sig = get_entry_signal_for_strategy(symbol, strategy_id)
    if not isinstance(sig, dict):
        return False, "", 0.0, now_ts

    same_dir = str(sig.get("direction") or "").lower() == str(direction or "").lower()
    live_strength = float(sig.get("strength", 0.0) or 0.0)
    min_strength = max(float(force_min_strength or 0.0), 0.0)

    if (not sig.get("signal")):
        reason = str(sig.get("reason") or "WEAKNESS")
        transient_prefixes = (
            "CANDLES_FETCH_FAIL",
            "API_FAIL",
            "STALE_CANDLES",
            "NO_CANDLES",
            "BAD_SYMBOL",
            "STRATEGY_EXCEPTION",
        )
        # Si la estructura ya no existe y no es un fallo transitorio de datos,
        # al llegar al umbral de beneficio preferimos cerrar y pagar la señal.
        if not reason.startswith(transient_prefixes):
            return True, f"FORCE_LOSS_{reason}", live_strength, now_ts
        return False, "", live_strength, now_ts

    if not same_dir:
        return True, "FORCE_LOSS_DIRECTION_FLIP", live_strength, now_ts

    if live_strength <= min_strength:
        return True, f"FORCE_LOSS_STRENGTH_{live_strength:.4f}", live_strength, now_ts

    return False, "", live_strength, now_ts


def _iso_utc_to_epoch_ms(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def _active_trade_opened_since_ms(active_trade: Optional[dict[str, Any]]) -> Optional[int]:
    if not isinstance(active_trade, dict):
        return None

    for key in ("opened_at_ms", "entry_started_at_ms", "started_at_ms"):
        try:
            val = int(active_trade.get(key) or 0)
            if val > 0:
                return val
        except Exception:
            pass

    started_iso = _iso_utc_to_epoch_ms(active_trade.get("started_at"))
    if started_iso and started_iso > 0:
        return started_iso
    return None


def _extract_exchange_order_id(obj: Any) -> str:
    if isinstance(obj, dict):
        for key in ("oid", "orderId", "order_id", "order", "cloid"):
            val = obj.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        for key in ("response", "data", "result", "statuses", "status", "filled", "resting", "order", "orders"):
            if key in obj:
                found = _extract_exchange_order_id(obj.get(key))
                if found:
                    return found
        for v in obj.values():
            found = _extract_exchange_order_id(v)
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for it in obj:
            found = _extract_exchange_order_id(it)
            if found:
                return found
    return ""


def _is_exchange_close_snapshot_consistent(
    *,
    direction: str,
    entry_price: float,
    exit_price: float,
    gross_pnl: float,
    closed_qty: float,
) -> bool:
    try:
        direction_s = str(direction or "").lower().strip()
        entry = float(entry_price or 0.0)
        exit_ = float(exit_price or 0.0)
        gross = float(gross_pnl or 0.0)
        qty = float(closed_qty or 0.0)
    except Exception:
        return False

    if entry <= 0.0 or exit_ <= 0.0 or qty <= 0.0:
        return False

    eps = 1e-8
    move = exit_ - entry
    if abs(move) <= eps:
        return True

    if direction_s == "long":
        expected_positive = move > 0.0
    elif direction_s == "short":
        expected_positive = move < 0.0
    else:
        return True

    if expected_positive and gross < -eps:
        return False
    if (not expected_positive) and gross > eps:
        return False
    return True


def _has_min_trade_close_context(trade: Optional[dict[str, Any]]) -> bool:
    if not isinstance(trade, dict):
        return False
    symbol = str(trade.get("symbol") or "").strip()
    direction = str(trade.get("direction") or "").strip().lower()
    try:
        entry_price = float(trade.get("entry_price") or 0.0)
    except Exception:
        entry_price = 0.0
    return bool(symbol and direction in {"long", "short"} and entry_price > 0.0)


def _normalize_exit_reason_for_user(exit_reason: str, net_profit: float) -> str:
    reason = str(exit_reason or "").strip()
    if not reason:
        return reason
    if reason.startswith("FORCE_LOSS_") and float(net_profit) >= 0.0:
        return "FORCE_CLOSE_" + reason[len("FORCE_LOSS_"):]
    if reason == "FORCE_LOSS_DIRECTION_FLIP" and float(net_profit) >= 0.0:
        return "FORCE_CLOSE_DIRECTION_FLIP"
    return reason


def _store_pending_exchange_close_snapshot(
    *,
    user_id: int,
    symbol: str,
    direction: str,
    side: str,
    entry_price: float,
    exit_price: float,
    qty_coin: float,
    qty_usdc_for_profit: float,
    exit_reason: str,
    source: str,
    detail: str,
) -> None:
    try:
        close_snapshot = {
            "symbol": str(symbol or "") or "OPERACION",
            "side": str(side or "").upper(),
            "direction": str(direction or ""),
            "entry_price": float(entry_price or 0.0),
            "exit_price": float(exit_price or 0.0),
            "qty": float(qty_coin or 0.0),
            "notional_usdc": float(qty_usdc_for_profit or 0.0),
            "profit": None,
            "gross_pnl": None,
            "fees": None,
            "pnl_source": "exchange_reconcile_pending",
            "realized_fills": 0,
            "close_source": str(source or ""),
            "exit_reason": str(exit_reason or "EXCHANGE_SYNC_CLOSE_PENDING"),
            "pending_exact_exchange_reconcile": True,
            "message": detail,
        }
        save_last_close(user_id, close_snapshot)
    except Exception as e:
        log(f"save pending exchange close error user={user_id} symbol={symbol} err={e}", "WARN")


def _format_trade_open_user_message(*, symbol: str, direction: str, entry_price: float, qty_coin: float, notional_usdc: float, opened_at_ms: Optional[int] = None) -> str:
    asset = str(symbol or '').replace('-PERP', '') or 'ACTIVO'
    lines = [
        '🟢 Nueva operación abierta',
        f'Símbolo: {symbol}',
        f'Lado: {str(direction or "").upper()}',
        f'Entrada: {float(entry_price):.6f}',
        f'Tamaño: {float(qty_coin):.8f} {asset}',
        f'Valor aprox: {float(notional_usdc):.4f} USDC',
    ]
    if opened_at_ms:
        try:
            opened_at = datetime.utcfromtimestamp(int(opened_at_ms) / 1000.0).strftime('%Y-%m-%d %H:%M:%S UTC')
            lines.append(f'Hora: {opened_at}')
        except Exception:
            pass
    lines.append('El motor quedó gestionando la posición en la MiniApp.')
    return '\\n'.join(lines)


def _format_trade_close_user_message(*, symbol: str, direction: str, entry_price: float, exit_price: float, qty_coin: float, notional_usdc: float, net_profit: float, gross_pnl: float, fees: float, exit_reason: str, pnl_source: str) -> str:
    asset = str(symbol or '').replace('-PERP', '') or 'ACTIVO'
    header = '✅ Operación cerrada' if float(net_profit) >= 0 else '🔴 Operación cerrada'
    lines = [
        header,
        f'Símbolo: {symbol}',
        f'Lado: {str(direction or "").upper()}',
        f'Entrada: {float(entry_price):.6f}',
        f'Salida: {float(exit_price):.6f}',
        f'Tamaño: {float(qty_coin):.8f} {asset}',
        f'Valor aprox: {float(notional_usdc):.4f} USDC',
        f'PnL bruto exchange: {float(gross_pnl):.6f} USDC',
        f'Fees exchange: {float(fees):.6f} USDC',
        f'PnL neto trading (sin funding): {float(net_profit):.6f} USDC',
    ]
    if exit_reason:
        lines.append(f'Motivo: {str(exit_reason)}')
    if pnl_source:
        lines.append(f'Fuente PnL: {str(pnl_source)}')
    return '\n'.join(lines)


def _read_trade_realized_pnl(
    user_id: int,
    symbol: str,
    active_trade: Optional[dict[str, Any]],
    *,
    direction: str = "",
    entry_price: float = 0.0,
    expected_order_id: str = "",
) -> Optional[dict[str, float]]:
    since_ms = _active_trade_opened_since_ms(active_trade)

    def _try_read_once() -> Optional[dict[str, float]]:
        try:
            snap = get_last_closed_trade_snapshot(
                user_id,
                symbol,
                opened_after_ms=since_ms,
                lookback_ms=2 * 60 * 60 * 1000,
                expected_order_id=(str(expected_order_id or "").strip() or None),
                expected_qty=float((active_trade or {}).get("qty_coin_for_log") or 0.0),
                direction=direction,
            )
            fills = int(snap.get("fills", 0) or 0)
            if fills > 0:
                gross = float(snap.get("gross_pnl", 0.0) or 0.0)
                fees = float(snap.get("fees", 0.0) or 0.0)
                net = float(snap.get("net_pnl", 0.0) or 0.0)
                exit_price_real = float(snap.get("exit_price", 0.0) or 0.0)
                closed_qty_real = float(snap.get("closed_qty", 0.0) or 0.0)

                if not _is_exchange_close_snapshot_consistent(
                    direction=direction,
                    entry_price=float(entry_price or 0.0),
                    exit_price=exit_price_real,
                    gross_pnl=gross,
                    closed_qty=closed_qty_real,
                ):
                    bad_oid = str(snap.get("close_order_id") or "").strip()
                    current_active = _get_active_trade(user_id) or {}
                    last_bad_oid = str(current_active.get("last_bad_close_order_id") or "").strip()
                    last_bad_logged_at = float(current_active.get("last_bad_close_logged_at") or 0.0)
                    now_bad_ts = time.time()
                    if (bad_oid != last_bad_oid) or ((now_bad_ts - last_bad_logged_at) >= 15.0):
                        log(
                            f"Cierre exchange inconsistente para {symbol}: dir={direction} entry={float(entry_price or 0.0):.10f} exit={exit_price_real:.10f} gross={gross:.10f} oid={str(snap.get('close_order_id') or '-')}",
                            "WARN",
                        )
                        try:
                            _update_active_trade_fields(
                                user_id,
                                last_bad_close_order_id=bad_oid,
                                last_bad_close_logged_at=now_bad_ts,
                            )
                        except Exception:
                            pass
                    return None

                payload = {
                    "entry_fees": float(snap.get("entry_fees") or 0),
                    "exit_fees": float(snap.get("exit_fees") or 0),
                    "close_order_ids": list(snap.get("close_order_ids") or []),
                    "pnl": round(gross, 6),
                    "fees": round(fees, 6),
                    "net": round(net, 6),
                    "fills": fills,
                    "since_ms": int(since_ms or 0),
                    "source": str(snap.get("source") or "exchange_close_batch"),
                    "exit_price": float(round(exit_price_real, 10)),
                    "closed_qty": float(round(closed_qty_real, 10)),
                    "close_time_ms": int(snap.get("close_time_ms", 0) or 0),
                    "close_order_id": str(snap.get("close_order_id") or ""),
                }
                log(
                    f"PnL_REAL_EXCHANGE {symbol}={payload['net']} gross={payload['pnl']} fees={payload['fees']} fills={fills} oid={payload['close_order_id'] or '-'}",
                    "INFO",
                )
                return payload
        except Exception as e:
            log(f"No se pudo leer cierre REAL exchange para {symbol}: {e}", "WARN")
        return None

    # Esperamos el settlement real del exchange antes de persistir el trade.
    attempts = 20
    for idx in range(attempts):
        payload = _try_read_once()
        if payload is not None:
            return payload
        if idx < attempts - 1:
            time.sleep(1.0)

    return None


def _register_trade_safe(
    *,
    user_id: int,
    symbol: str,
    direction: str,
    side: str,
    entry_price: float,
    exit_price: float,
    qty: float,
    profit: float,
    exit_reason: str,
    best_score: float,
    fees: float = 0.0,
    gross_pnl: float = 0.0,
    pnl_source: str = "",
    realized_fills: int = 0,
    close_source: str = "",
    notional_usdc: float = 0.0,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    # The bundled database has one known signature. A DB failure is not a
    # signature mismatch and must never trigger a second, metadata-free insert.
    register_trade(
        user_id=user_id,
        symbol=symbol,
        side=side.upper(),
        entry_price=float(entry_price),
        exit_price=float(exit_price),
        qty=float(qty),
        profit=float(profit),
        best_score=float(best_score),
        fees=float(fees),
        gross_pnl=float(gross_pnl),
        pnl_source=str(pnl_source or ""),
        realized_fills=int(realized_fills or 0),
        exit_reason=str(exit_reason or ""),
        close_source=str(close_source or ""),
        direction=str(direction or ""),
        notional_usdc=float(notional_usdc or 0.0),
        metadata=(dict(metadata) if isinstance(metadata, dict) else None),
    )


def _finalize_trade_close(
    *,
    user_id: int,
    symbol: str,
    direction: str,
    side: str,
    entry_price: float,
    exit_price: float,
    qty_coin: float,
    qty_usdc_for_profit: float,
    best_score: float,
    exit_reason: str,
    exit_pnl_pct: float,
    source: str,
    close_order_id_hint: str = "",
) -> float:
    guard_snapshot = _try_begin_trade_finalize(user_id, source)
    if guard_snapshot is None:
        log(f"Skip duplicate trade close user={user_id} symbol={symbol} src={source}", "WARN")
        return 0.0

    active_trade = _get_active_trade(user_id) or {}
    pnl_diag = _read_trade_realized_pnl(
        user_id,
        symbol,
        active_trade,
        direction=str(direction or ""),
        entry_price=float(entry_price or 0.0),
        expected_order_id=str(close_order_id_hint or ""),
    )
    if pnl_diag is None:
        log(
            f"Trade cerrado ({source}) {symbol} pendiente de reconciliación exacta con exchange; no se persiste cierre sin source real.",
            "CRITICAL",
        )
        # Keep the opening timestamp/quantity until the complete ledger is available.
        # Clearing this state loses opening fees and allows later trades to overlap.
        try:
            _update_active_trade_fields(
                user_id,
                close_in_progress=False,
                close_finalized=False,
                close_started_at=None,
                close_source=str(source or ""),
                reconciliation_pending=True,
                reconciliation_pending_at=time.time(),
                last_reconciliation_error="exchange_close_snapshot_unavailable",
            )
        except Exception as e:
            log(f"reconciliation pending flag error {symbol} src={source} err={e}", "WARN")
        return 0.0
    else:
        profit = float(pnl_diag.get("net", 0.0) or 0.0)
        gross_pnl = float(pnl_diag.get("pnl", 0.0) or 0.0)
        fees = float(pnl_diag.get("fees", 0.0) or 0.0)
        realized_fills = int(pnl_diag.get("fills", 0) or 0)
        pnl_source = str(pnl_diag.get("source") or "exchange_close_batch")
        exchange_exit_price = float(pnl_diag.get("exit_price", 0.0) or 0.0)
        exchange_closed_qty = float(pnl_diag.get("closed_qty", 0.0) or 0.0)
        if exchange_exit_price > 0.0:
            exit_price = exchange_exit_price
        if exchange_closed_qty > 0.0:
            qty_coin = exchange_closed_qty
            qty_usdc_for_profit = abs(float(exchange_closed_qty) * float(exit_price))
        log(
            f"Trade cerrado ({source}) {symbol} PnL_REAL={profit} gross={gross_pnl} fees={fees} fills={realized_fills} src={pnl_source} reason={exit_reason}",
            "INFO",
        )

    try:
        trade_audit_metadata = _build_trade_audit_metadata(_get_active_trade(user_id) or {}) or {}
        trade_audit_metadata["accounting"] = dict(pnl_diag)
        trade_audit_metadata["accounting"]["funding_included"] = False
        _register_trade_safe(
            user_id=user_id,
            symbol=symbol,
            direction=direction,
            side=side,
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            qty=float(qty_coin),
            profit=float(profit),
            exit_reason=str(exit_reason),
            best_score=float(best_score),
            fees=float(fees),
            gross_pnl=float(gross_pnl),
            pnl_source=str(pnl_source),
            realized_fills=int(realized_fills),
            close_source=str(source),
            notional_usdc=float(qty_usdc_for_profit),
            metadata=trade_audit_metadata,
        )
    except Exception as e:
        log(f"register_trade error {symbol} src={source} err={e}", "ERROR")
        _update_active_trade_fields(user_id, close_in_progress=False, close_finalized=False,
            reconciliation_pending=True, last_reconciliation_error="TRADE_PERSISTENCE_FAILED")
        return 0.0

    try:
        risk_active = _get_active_trade(user_id) or {}
        risk_sig = (risk_active.get('entry_context') or {}).get('signal_summary') or {}
        signal_bar = risk_sig.get('last_candle_t_5m')
        opportunity = f"{symbol}:{direction}:{risk_sig.get('strategy_id')}:{signal_bar}" if signal_bar else None
        opened = _active_trade_opened_since_ms(risk_active)
        close_id = f"{user_id}:{symbol}:{opened}" if opened else None
        normalized_profit = profit / max(float(qty_usdc_for_profit), 1e-12)
        _risk_record_close(user_id, profit, opportunity, normalized_profit, close_id)
    except Exception as e:
        log(f"risk_record_close error {symbol} src={source} err={e}", "ERROR")

    close_snapshot = {
        "symbol": symbol,
        "side": str(side or "").upper(),
        "direction": str(direction or ""),
        "entry_price": float(entry_price),
        "exit_price": float(exit_price),
        "qty": float(qty_coin),
        "notional_usdc": float(qty_usdc_for_profit),
        "profit": float(profit),
        "gross_pnl": float(gross_pnl),
        "fees": float(fees),
        "pnl_source": str(pnl_source),
        "realized_fills": int(realized_fills),
        "close_source": str(source),
        "exit_reason": str(exit_reason),
        "message": _format_trade_close_user_message(
            symbol=symbol,
            direction=direction,
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            qty_coin=float(qty_coin),
            notional_usdc=float(qty_usdc_for_profit),
            net_profit=float(profit),
            gross_pnl=float(gross_pnl),
            fees=float(fees),
            exit_reason=str(exit_reason),
            pnl_source=str(pnl_source),
        ),
    }
    try:
        save_last_close(user_id, close_snapshot)
    except Exception as e:
        log(f"save_last_close error {symbol} src={source} err={e}", "ERROR")

    try:
        database_module.touch_user_operational_state(
            user_id,
            'cycle_completed',
            f'Último cierre {symbol} registrado. PnL neto {float(profit):.6f} USDC.',
            mode='entries_enabled',
            live_trade=False,
            active_symbol=None,
            source='engine_close',
            metadata={
                'last_result': 'trade_closed',
                'last_decision': 'trade_closed',
                'last_symbol': symbol,
                'last_cycle_at': datetime.utcnow(),
                'close_reason': str(exit_reason),
                'close_profit': float(profit),
                'close_gross_pnl': float(gross_pnl),
                'close_fees': float(fees),
                'close_pnl_source': str(pnl_source),
            },
        )
    except Exception as e:
        log(f"touch_user_operational_state close error {symbol} src={source} err={e}", "WARN")

    _register_post_close_cooldown(user_id)

    try:
        admin_fee = max(float(profit), 0.0) * float(OWNER_FEE_PERCENT or 0.0)
        referrer_id = get_user_referrer(user_id)
        if referrer_id and admin_fee > 0:
            ref_fee = admin_fee * float(REFERRAL_FEE_PERCENT or 0.0)
            add_weekly_ref_fee(referrer_id, ref_fee)
            admin_fee -= ref_fee
        if admin_fee > 0:
            add_daily_admin_fee(user_id, admin_fee)
    except Exception as e:
        log(f"fee calc error {symbol} src={source} err={e}", "ERROR")

    try:
        _update_active_trade_fields(
            user_id,
            close_in_progress=False,
            close_finalized=True,
            close_finished_at=time.time(),
            close_reason=str(exit_reason),
            close_profit=float(profit),
            reconciliation_pending=False,
            reconciliation_pending_at=None,
            last_reconciliation_error=None,
        )
    except Exception as e:
        log(f"active_trade finalize mark error {symbol} src={source} err={e}", "WARN")

    _clear_active_trade(user_id)
    return float(profit)


def _ensure_manager_watchdog(user_id: int) -> Optional[dict]:
    active = _get_active_trade(user_id)
    if not active:
        return None
    if _manager_is_running(user_id):
        return None

    try:
        still_open = bool(has_open_position(user_id))
    except Exception as e:
        log(f"WATCHDOG has_open_position error user={user_id} err={e}", "WARN")
        return None

    if not still_open:
        return None

    symbol = str(active.get("symbol") or "")
    symbol_for_exec = str(active.get("symbol_for_exec") or _norm_coin(symbol))
    direction = str(active.get("direction") or "")
    side = str(active.get("side") or "")
    opposite = str(active.get("opposite") or ("sell" if side == "buy" else "buy"))
    entry_price = float(active.get("entry_price") or 0.0)
    qty_coin_for_log = float(active.get("qty_coin_for_log") or 0.0)
    qty_usdc_for_profit = float(active.get("qty_usdc_for_profit") or 0.0)
    best_score = float(active.get("best_score") or 0.0)
    entry_strength = float(active.get("entry_strength") or 0.0)
    sl_price_pct = float(active.get("sl_price_pct") or 0.0)
    mgmt = _coalesce_management_params(active_trade=active, entry_strength=entry_strength, best_score=best_score)

    log(f"WATCHDOG: manager muerto; relanzando user={user_id} symbol={symbol}", "CRITICAL")
    started = _start_manager_thread(
        user_id=user_id,
        symbol=symbol,
        symbol_for_exec=symbol_for_exec,
        direction=direction,
        side=side,
        opposite=opposite,
        entry_price=entry_price,
        qty_coin_for_log=qty_coin_for_log,
        qty_usdc_for_profit=qty_usdc_for_profit,
        best_score=best_score,
        entry_strength=entry_strength,
        mode=str(active.get("mode") or "WATCHDOG"),
        sl_price_pct=sl_price_pct,
        mgmt=mgmt,
        opened_at_ms=int((_active_trade_opened_since_ms(active) or int(time.time() * 1000))),
    )
    return {"event": "MANAGER_WATCHDOG", "manager": {"symbol": symbol, "started": started}}


def _reconcile_orphan_closed_trade(user_id: int) -> bool:
    active = _get_active_trade(user_id)
    if not active:
        return False

    try:
        still_open = bool(has_open_position(user_id))
    except Exception as e:
        log(f"No se pudo reconciliar has_open_position user={user_id} err={e}", "WARN")
        return False

    if still_open:
        return False

    if not _has_min_trade_close_context(active):
        log(f"RECONCILE: active_trade corrupto o incompleto user={user_id}; se limpia estado fantasma sin persistir cierre exacto", "CRITICAL")
        try:
            _store_pending_exchange_close_snapshot(
                user_id=user_id,
                symbol=str(active.get('symbol') or ''),
                direction=str(active.get('direction') or ''),
                side=str(active.get('side') or ''),
                entry_price=float(active.get('entry_price') or 0.0),
                exit_price=0.0,
                qty_coin=float(active.get('qty_coin_for_log') or 0.0),
                qty_usdc_for_profit=float(active.get('qty_usdc_for_profit') or 0.0),
                exit_reason='EXCHANGE_SYNC_CLOSE_PENDING_CONTEXT_MISSING',
                source='RECONCILE',
                detail='El exchange ya no muestra posición abierta, pero el contexto local del trade estaba incompleto. Se limpia el estado para evitar posición fantasma.',
            )
        except Exception:
            pass
        _register_post_close_cooldown(user_id)
        _clear_active_trade(user_id)
        return True

    symbol = str(active.get("symbol") or "")
    entry_price = float(active.get("entry_price") or 0.0)
    qty_usdc_for_profit = float(active.get("qty_usdc_for_profit") or 0.0)
    qty_coin_for_log = float(active.get("qty_coin_for_log") or 0.0)
    direction = str(active.get("direction") or "")
    side = str(active.get("side") or "")
    best_score = float(active.get("best_score") or 0.0)
    symbol_for_exec = str(active.get("symbol_for_exec") or _norm_coin(symbol))

    exit_price = float(get_price(symbol_for_exec) or entry_price or 0.0)
    exit_pnl_pct = 0.0
    if entry_price > 0 and exit_price > 0:
        if direction == "long":
            exit_pnl_pct = (exit_price - entry_price) / entry_price
        elif direction == "short":
            exit_pnl_pct = (entry_price - exit_price) / entry_price

    log(f"RECONCILE: posición cerrada en exchange sin cierre interno previo user={user_id} symbol={symbol}", "CRITICAL")
    profit = _finalize_trade_close(
        user_id=user_id,
        symbol=symbol,
        direction=direction,
        side=side,
        entry_price=entry_price,
        exit_price=exit_price,
        qty_coin=float(qty_coin_for_log),
        qty_usdc_for_profit=float(qty_usdc_for_profit),
        best_score=best_score,
        exit_reason="EXCHANGE_SYNC_CLOSE",
        exit_pnl_pct=float(exit_pnl_pct),
        source="RECONCILE",
    )
    return profit != 0.0 or (_get_active_trade(user_id) is None)


def _floor_qty_to_step(qty: float, sz_decimals: int) -> float:
    try:
        dec = max(int(sz_decimals or 0), 0)
    except Exception:
        dec = 0
    factor = 10 ** dec
    try:
        return math.floor(max(float(qty), 0.0) * factor) / factor
    except Exception:
        return 0.0


def _attempt_partial_take_profit(*, user_id: int, symbol: str, symbol_for_exec: str, direction: str, opposite: str, close_fraction: float) -> dict[str, Any]:
    try:
        size_signed = float(get_open_position_size(user_id, symbol_for_exec) or 0.0)
    except Exception:
        size_signed = 0.0
    if size_signed == 0.0:
        return {"filled": False, "terminal_skip": False, "reason": "NO_OPEN_SIZE"}

    raw_qty = abs(size_signed) * float(close_fraction)
    asset_index = get_asset_index(symbol_for_exec)
    try:
        sz_decimals = int(get_sz_decimals(int(asset_index))) if asset_index is not None else 8
    except Exception:
        sz_decimals = 8
    qty = _floor_qty_to_step(raw_qty, sz_decimals)

    if qty < float(MIN_QTY_COIN):
        log(
            f"PARTIAL_TP_SKIPPED_MIN_QTY user={user_id} symbol={symbol} dir={direction} close_fraction={float(close_fraction):.4f} "
            f"raw_qty={float(raw_qty):.8f} qty={float(qty):.8f} min_qty={float(MIN_QTY_COIN):.8f}",
            "WARN",
        )
        return {"filled": False, "terminal_skip": True, "reason": "MIN_QTY", "qty": float(qty)}

    mark_price = float(get_price(symbol_for_exec) or 0.0)
    if mark_price <= 0.0:
        return {"filled": False, "terminal_skip": False, "reason": "NO_PRICE", "qty": float(qty)}

    notional = float(qty) * float(mark_price)
    min_notional = float(get_exchange_min_order_notional_usdc() or 0.0)
    if min_notional > 0.0 and notional < min_notional:
        log(
            f"PARTIAL_TP_SKIPPED_MIN_NOTIONAL user={user_id} symbol={symbol} dir={direction} close_fraction={float(close_fraction):.4f} "
            f"qty={float(qty):.8f} px={float(mark_price):.8f} notional={float(notional):.4f} min_notional={float(min_notional):.4f}",
            "WARN",
        )
        return {
            "filled": False,
            "terminal_skip": True,
            "reason": "MIN_NOTIONAL",
            "qty": float(qty),
            "notional": float(notional),
            "min_notional": float(min_notional),
        }

    try:
        resp = place_market_order(user_id, symbol_for_exec, opposite, qty, reduce_only=True)
    except Exception as e:
        log(f"PARTIAL_TP error user={user_id} symbol={symbol} err={e}", "ERROR")
        return {"filled": False, "terminal_skip": False, "reason": f"EXCEPTION:{type(e).__name__}", "qty": float(qty)}
    if not resp or (not _resp_ok(resp)) or (not _is_filled_exchange_response(resp)):
        reason = str((resp or {}).get("reason") or "NO_FILL") if isinstance(resp, dict) else "NO_FILL"
        log(f"PARTIAL_TP no fill user={user_id} symbol={symbol} reason={reason} resp={resp}", "WARN")
        return {"filled": False, "terminal_skip": False, "reason": reason, "qty": float(qty)}
    log(f"PARTIAL_TP_FILLED user={user_id} symbol={symbol} dir={direction} close_fraction={float(close_fraction):.4f} qty={qty:.8f}", "WARN")
    return {"filled": True, "terminal_skip": False, "reason": "FILLED", "qty": float(qty)}


def _manage_trade_until_close(
    *,
    user_id: int,
    symbol: str,
    symbol_for_exec: str,
    direction: str,
    side: str,
    opposite: str,
    entry_price: float,
    qty_coin_for_log: float,
    qty_usdc_for_profit: float,
    best_score: float,
    entry_strength: float,
    mode: str,
    sl_price_pct: float | None = None,
    mgmt: Optional[dict[str, Any]] = None,
) -> None:
    _ = sl_price_pct
    active_runtime = _get_active_trade(user_id) or {}
    if mgmt is None:
        mgmt = _coalesce_management_params(active_trade=active_runtime, entry_strength=float(entry_strength), best_score=float(best_score))
    else:
        mgmt = _coalesce_management_params(signal=mgmt, active_trade=active_runtime, entry_strength=float(entry_strength), best_score=float(best_score))

    tp_activate_price = float(mgmt["tp_activate_price"])
    best_pnl_pct = float(active_runtime.get("best_pnl_pct", 0.0) or 0.0)
    peak_price = float(active_runtime.get("peak_price", entry_price) or entry_price)
    strength_check_ts = float(active_runtime.get("strength_check_ts", 0.0) or 0.0)
    last_pos_sync_ts = 0.0
    last_runtime_flush_ts = 0.0

    log(
        f"🧠 MANAGER[{mode}] start user={user_id} {symbol} dir={direction} entry={entry_price} qty_coin~{qty_coin_for_log} notional~{qty_usdc_for_profit:.4f} "
        f"(bucket={mgmt['bucket']}, TP_fijo={tp_activate_price:.6f}, exit_policy=FIXED_TP_SL)",
        "WARN",
    )

    _update_active_trade_fields(
        user_id,
        break_even_armed=False,
        best_pnl_pct=float(best_pnl_pct),
        peak_price=float(peak_price),
        last_price=float(entry_price),
        last_pnl_pct=0.0,
        strength_check_ts=float(strength_check_ts),
        manager_heartbeat_ts=time.time(),
    )

    exit_price = entry_price
    exit_reason = "UNKNOWN"
    exit_pnl_pct = 0.0

    while True:
        now_ts = time.time()
        if (now_ts - float(last_pos_sync_ts)) >= float(POSITION_SYNC_INTERVAL):
            last_pos_sync_ts = now_ts
            try:
                live_size_signed = get_position_size_verified(user_id, symbol_for_exec)
            except Exception as e:
                live_size_signed = None
                log(f"MANAGER[{mode}] sync size error {symbol} err={e}", "WARN")

            if live_size_signed == 0.0:
                exit_reason = "EXCHANGE_POSITION_CLOSED"
                exit_price = float(get_price(symbol_for_exec) or entry_price or 0.0)
                if entry_price > 0 and exit_price > 0:
                    if direction == "long":
                        exit_pnl_pct = (exit_price - entry_price) / entry_price
                    else:
                        exit_pnl_pct = (entry_price - exit_price) / entry_price
                log(
                    f"EXCHANGE_POSITION_CLOSED[{mode}] user={user_id} symbol={symbol} dir={direction} entry={float(entry_price):.8f} observed_exit_price={float(exit_price):.8f} observed_pnl_pct={float(exit_pnl_pct):.6f}",
                    "CRITICAL",
                )
                break

        price = float(get_price(symbol_for_exec) or 0.0)
        if price <= 0:
            time.sleep(PRICE_CHECK_INTERVAL)
            continue

        if direction == "long":
            pnl_pct = (price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - price) / entry_price

        best_pnl_pct = max(best_pnl_pct, pnl_pct)
        if direction == "long":
            peak_price = max(float(peak_price), float(price))
        else:
            peak_price = min(float(peak_price), float(price)) if peak_price > 0 else float(price)

        fixed_tp_price = _pct_to_abs_price(entry_price, float(tp_activate_price), direction, kind="tp_activate")
        desired_stop_price = _pct_to_abs_price(entry_price, float((_get_active_trade(user_id) or {}).get("sl_price_pct", sl_price_pct or 0.0) or 0.0), direction, kind="sl")
        state = _get_active_trade(user_id) or {}
        sl_guard_live = bool(state.get("sl_in_exchange", False))
        tp_guard_live = bool(state.get("tp_in_exchange", False))

        if (now_ts - float(last_runtime_flush_ts)) >= float(max(POSITION_SYNC_INTERVAL, 2.0)):
            last_runtime_flush_ts = now_ts
            _update_active_trade_fields(
                user_id,
                last_price=float(price),
                last_pnl_pct=float(pnl_pct),
                break_even_armed=False,
                best_pnl_pct=float(best_pnl_pct),
                peak_price=float(peak_price),
                strength_check_ts=float(strength_check_ts),
                manager_heartbeat_ts=time.time(),
            )

        # Exchange flags are historical evidence, not proof an order still exists.
        # Use a fresh executable quote before a reduce-only backup exit.
        if SOFTWARE_EXIT_GUARD_ENABLED:
            possible_hit = exit_guard_reason(direction, price, price, desired_stop_price, fixed_tp_price)
            if possible_hit:
                bid, ask = get_best_bid_ask(symbol_for_exec)
                guard_reason = exit_guard_reason(direction, bid, ask, desired_stop_price, fixed_tp_price)
                if guard_reason:
                    exit_reason = guard_reason
                    exit_price = bid if direction == "long" else ask
                    exit_pnl_pct = ((exit_price-entry_price) / entry_price
                                    if direction == "long" else (entry_price-exit_price) / entry_price)
                    break

        if not SOFTWARE_EXIT_GUARD_ENABLED and not tp_guard_live and fixed_tp_price > 0.0:
            tp_hit = (direction == "long" and price >= fixed_tp_price) or (direction == "short" and price <= fixed_tp_price)
            if tp_hit:
                exit_reason = "FIXED_TP_FALLBACK"
                exit_price = float(price)
                exit_pnl_pct = float(pnl_pct)
                break

        if not SOFTWARE_EXIT_GUARD_ENABLED and not sl_guard_live and desired_stop_price > 0.0:
            stop_hit = (direction == "long" and price <= desired_stop_price) or (direction == "short" and price >= desired_stop_price)
            if stop_hit:
                exit_reason = "STOP_FALLBACK"
                exit_price = float(price)
                exit_pnl_pct = float(pnl_pct)
                break

        time.sleep(PRICE_CHECK_INTERVAL)

    log(f"Cerrando posición (MANAGER[{mode}]) {symbol} reason={exit_reason}", "WARN")

    size_signed_now = get_position_size_verified(user_id, symbol_for_exec)
    result = close_verified(
        read_size=lambda: get_position_size_verified(user_id, symbol_for_exec),
        close_order=lambda size: place_market_order(user_id, symbol_for_exec,
            'sell' if size > 0 else 'buy', abs(size), reduce_only=True),
        cancel_orders=lambda: cancel_all_orders_for_symbol(user_id, symbol_for_exec),
        protect=lambda size: _ensure_exchange_stop_loss(user_id=user_id, symbol=symbol,
            symbol_for_exec=symbol_for_exec, direction='long' if size > 0 else 'short',
            entry_price=entry_price, qty_coin=abs(size), sl_price_pct=sl_price_pct,
            context='MANAGER_RESIDUAL'),
    )
    if not result['closed']:
        _abort_new_position_after_fill(user_id=user_id, symbol=symbol,
            symbol_for_exec=symbol_for_exec, size_real_signed=float(size_signed_now or 0),
            reason=f'MANAGER_CLOSE_PENDING:{exit_reason}',
            stop_price=_pct_to_abs_price(entry_price, sl_price_pct, direction, kind='sl'))
        return
    _finalize_trade_close(
        user_id=user_id, symbol=symbol, direction=direction, side=side,
        entry_price=float(entry_price), exit_price=float(exit_price),
        qty_coin=float(qty_coin_for_log), qty_usdc_for_profit=float(qty_usdc_for_profit),
        best_score=float(best_score), exit_reason=str(exit_reason),
        exit_pnl_pct=float(exit_pnl_pct), source=f'MANAGER[{mode}]',
    )




def admin_force_close_position(user_id: int, *, reason: str | None = None) -> dict[str, Any]:
    """Cierra forzosamente la posición abierta del usuario desde admin.

    Reglas de seguridad:
    - cancela triggers/órdenes del símbolo antes del reduce-only close
    - intenta cierre real en exchange
    - espera confirmación breve de size=0
    - persiste el cierre con motivo ADMIN_FORCE_CLOSE si existe contexto local
    """
    uid = int(user_id)
    active = _get_active_trade(uid) or {}

    coin = _get_first_open_position_coin(uid)
    if not coin and active:
        coin = _norm_coin(str(active.get('symbol_for_exec') or active.get('symbol') or ''))

    if not coin:
        if active:
            try:
                reconciled = _reconcile_orphan_closed_trade(uid)
            except Exception:
                reconciled = False
            return {
                'ok': True,
                'result': 'no_open_position',
                'message': 'No había posición abierta en exchange. Se limpió el estado local pendiente.' if reconciled else 'No había posición abierta en exchange.',
                'symbol': str(active.get('symbol') or ''),
                'reconciled': bool(reconciled),
            }
        return {
            'ok': True,
            'result': 'no_open_position',
            'message': 'No había posición abierta en exchange.',
            'symbol': '',
            'reconciled': False,
        }

    symbol_for_exec = _norm_coin(coin)
    symbol = str(active.get('symbol') or f'{symbol_for_exec}-PERP')

    try:
        size_signed = get_position_size_verified(uid, symbol_for_exec)
        if size_signed is None:
            raise RuntimeError('Estado de posición no disponible')
    except Exception as e:
        raise RuntimeError(f'No se pudo leer el tamaño abierto en exchange: {e}')

    if size_signed == 0.0:
        if active:
            try:
                reconciled = _reconcile_orphan_closed_trade(uid)
            except Exception:
                reconciled = False
            return {
                'ok': True,
                'result': 'no_open_position',
                'message': 'La posición ya estaba cerrada en exchange. Se reconciliará el estado local.' if reconciled else 'La posición ya estaba cerrada en exchange.',
                'symbol': symbol,
                'reconciled': bool(reconciled),
            }
        return {
            'ok': True,
            'result': 'no_open_position',
            'message': 'La posición ya estaba cerrada en exchange.',
            'symbol': symbol,
            'reconciled': False,
        }

    direction = 'long' if size_signed > 0 else 'short'
    side = 'buy' if size_signed > 0 else 'sell'
    close_side = 'sell' if size_signed > 0 else 'buy'
    qty_close = abs(float(size_signed))

    entry_price = float(active.get('entry_price') or 0.0)
    if entry_price <= 0.0:
        try:
            entry_price = float(get_position_entry_price(uid, symbol_for_exec) or 0.0)
        except Exception:
            entry_price = 0.0
    mark_price = 0.0
    try:
        mark_price = float(get_price(symbol_for_exec) or 0.0)
    except Exception:
        mark_price = 0.0
    if entry_price <= 0.0:
        entry_price = float(mark_price or 0.0)

    qty_coin_for_log = float(active.get('qty_coin_for_log') or qty_close or 0.0)
    if qty_coin_for_log <= 0.0:
        qty_coin_for_log = float(qty_close)
    qty_usdc_for_profit = float(active.get('qty_usdc_for_profit') or 0.0)
    if qty_usdc_for_profit <= 0.0:
        qty_usdc_for_profit = abs(float(qty_close) * float(entry_price or mark_price or 0.0))
    best_score = float(active.get('best_score') or 0.0)

    try:
        _update_active_trade_fields(
            uid,
            close_in_progress=True,
            close_finalized=False,
            close_started_at=time.time(),
            close_source='ADMIN_FORCE_CLOSE',
        )
    except Exception as e:
        log(f'ADMIN_FORCE_CLOSE set guard error user={uid} symbol={symbol} err={e}', 'WARN')

    close_resp = place_market_order(uid, symbol_for_exec, close_side, qty_close, reduce_only=True)
    if not close_resp or (not _resp_ok(close_resp)) or (not _is_filled_exchange_response(close_resp)):
        try:
            _update_active_trade_fields(
                uid,
                close_in_progress=False,
                close_finalized=False,
                close_started_at=None,
                close_source='ADMIN_FORCE_CLOSE',
            )
        except Exception:
            pass
        raise RuntimeError(f'Cierre reduce-only no confirmado por exchange: {_resp_reason(close_resp) or close_resp}')

    close_order_id_hint = _extract_exchange_order_id(close_resp)

    closed = False
    last_size = abs(float(size_signed))
    for _ in range(20):
        time.sleep(0.30)
        try:
            size_now = get_position_size_verified(uid, symbol_for_exec)
        except Exception:
            size_now = None
        if size_now is None:
            continue
        last_size = abs(float(size_now))
        if size_now == 0.0:
            closed = True
            break

    if closed:
        try:
            cancel_all_orders_for_symbol(uid, symbol_for_exec)
        except Exception as e:
            log(f'ADMIN_FORCE_CLOSE post-cancel error user={uid} symbol={symbol} err={e}', 'WARN')
    
    if not closed and last_size > 0.0:
        try:
            _update_active_trade_fields(
                uid,
                close_in_progress=False,
                close_finalized=False,
                close_started_at=None,
                close_source='ADMIN_FORCE_CLOSE',
            )
        except Exception:
            pass
        raise RuntimeError(f'El exchange todavía reporta posición abierta tras el cierre admin. size_restante={last_size:.10f}')

    exit_price = float(_extract_fill_price(close_resp) or mark_price or entry_price or 0.0)
    exit_pnl_pct = 0.0
    if entry_price > 0.0 and exit_price > 0.0:
        if direction == 'long':
            exit_pnl_pct = (exit_price - entry_price) / entry_price
        else:
            exit_pnl_pct = (entry_price - exit_price) / entry_price

    if active and _has_min_trade_close_context(active):
        profit = _finalize_trade_close(
            user_id=uid,
            symbol=symbol,
            direction=direction,
            side=side,
            entry_price=float(entry_price),
            exit_price=float(exit_price),
            qty_coin=float(qty_coin_for_log),
            qty_usdc_for_profit=float(qty_usdc_for_profit),
            best_score=float(best_score),
            exit_reason='ADMIN_FORCE_CLOSE',
            exit_pnl_pct=float(exit_pnl_pct),
            source='ADMIN_FORCE_CLOSE',
            close_order_id_hint=str(close_order_id_hint or ''),
        )
        return {
            'ok': True,
            'result': 'position_closed',
            'message': f'Posición cerrada manualmente en exchange para {symbol}.',
            'symbol': symbol,
            'direction': direction,
            'qty_closed': float(qty_close),
            'profit': float(profit),
            'close_order_id': str(close_order_id_hint or ''),
            'reason': str(reason or ''),
            'finalized': True,
        }

    try:
        _clear_active_trade(uid)
    except Exception as e:
        log(f'ADMIN_FORCE_CLOSE clear active trade fallback error user={uid} symbol={symbol} err={e}', 'WARN')

    return {
        'ok': True,
        'result': 'position_closed',
        'message': f'Posición cerrada manualmente en exchange para {symbol}. El contexto local no tenía datos completos para persistir la operación exacta.',
        'symbol': symbol,
        'direction': direction,
        'qty_closed': float(qty_close),
        'profit': 0.0,
        'close_order_id': str(close_order_id_hint or ''),
        'reason': str(reason or ''),
        'finalized': False,
    }


def _manage_existing_open_position(user_id: int) -> Optional[dict]:
    """Adopta una posición ya abierta en el exchange y asegura que el manager esté corriendo en background.
    Evita reprocesar ADOPT completo en cada ciclo cuando la misma posición ya está adoptada.
    """
    coin = _get_first_open_position_coin(user_id)
    if not coin:
        log("has_open_position=True pero no se pudo detectar coin/posición (probable dust) — skip", "WARN")
        return None

    symbol = f"{coin}-PERP"
    symbol_for_exec = _norm_coin(coin)

    try:
        size_signed = float(get_open_position_size(user_id, symbol_for_exec) or 0.0)
    except Exception:
        size_signed = 0.0

    if size_signed == 0.0:
        log(f"Posición detectada pero size=0 ({symbol}) — skip", "WARN")
        return None

    direction = "long" if size_signed > 0 else "short"
    side = "buy" if direction == "long" else "sell"
    opposite = "sell" if side == "buy" else "buy"

    entry_price = float(get_position_entry_price(user_id, symbol_for_exec) or 0.0)
    if entry_price <= 0:
        entry_price = float(get_price(symbol_for_exec) or 0.0)

    if entry_price <= 0:
        log(f"No se pudo determinar entry_price de la posición abierta ({symbol}) — skip", "ERROR")
        return None

    qty_coin_real = abs(float(size_signed))
    qty_usdc_real = float(entry_price) * float(qty_coin_real)

    active_trade = _get_active_trade(user_id)
    manager_running = _manager_is_running(user_id)
    same_position = _same_live_position(active_trade, symbol=symbol, direction=direction, entry_price=float(entry_price))

    adopt_signal = None
    adopt_entry_strength = float((active_trade or {}).get("entry_strength", 0.0) or 0.0)
    adopt_best_score = float((active_trade or {}).get("best_score", 0.0) or 0.0)
    adopt_sl_price_pct = float((active_trade or {}).get("sl_price_pct", ADOPT_EMERGENCY_SL_PCT) or ADOPT_EMERGENCY_SL_PCT)
    frozen_plan = _has_frozen_trade_plan(active_trade)
    adopt_mgmt = _coalesce_management_params(
        active_trade=active_trade,
        entry_strength=adopt_entry_strength,
        best_score=adopt_best_score,
    )

    if (not frozen_plan) and (not same_position):
        adopt_strategy_id = _safe_str((active_trade or {}).get("strategy_id") or (active_trade or {}).get("strategy_model"), DEFAULT_STRATEGY_ID)
        adopt_signal = get_entry_signal_for_strategy(symbol, adopt_strategy_id)
    elif (not frozen_plan) and same_position and isinstance(active_trade, dict):
        log(
            f"ADOPT conserva snapshot persistido sin recalcular señal user={user_id} symbol={symbol}",
            "WARN",
        )

    if (not frozen_plan) and isinstance(adopt_signal, dict) and adopt_signal.get("signal") and str(adopt_signal.get("direction") or "").lower() == direction:
        adopt_entry_strength = float(adopt_signal.get("strength", adopt_entry_strength) or adopt_entry_strength or 0.0)
        adopt_best_score = float(adopt_signal.get("score", adopt_best_score) or adopt_best_score or 0.0)
        adopt_sl_price_pct = float(adopt_signal.get("sl_price_pct", adopt_sl_price_pct) or adopt_sl_price_pct)
        adopt_mgmt = _coalesce_management_params(
            signal=adopt_signal,
            active_trade=active_trade,
            entry_strength=adopt_entry_strength,
            best_score=adopt_best_score,
        )

    existing_opened_at_ms = _active_trade_opened_since_ms(active_trade)
    entry_context = dict((active_trade or {}).get("entry_context") or {})
    if not entry_context:
        entry_context = _build_entry_context(
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            scanner_meta=None,
            signal=adopt_signal,
            risk=None,
        )
        entry_context["captured_from"] = "adopt"

    trade_plan = _build_trade_plan(
        signal=adopt_signal,
        active_trade=active_trade,
        mgmt=adopt_mgmt,
        sl_price_pct=float(adopt_sl_price_pct),
        entry_strength=float(adopt_entry_strength),
        best_score=float(adopt_best_score),
        approved_margin_usdc=_safe_float((active_trade or {}).get("approved_margin_usdc"), 0.0),
        leverage=_safe_float((active_trade or {}).get("leverage"), float(LEVERAGE)),
        target_notional_usdc=_safe_float((active_trade or {}).get("target_notional_usdc"), float(qty_usdc_real)),
        requested_qty_coin=_safe_float((active_trade or {}).get("requested_qty_coin"), float(qty_coin_real)),
        actual_qty_coin=float(qty_coin_real),
        actual_notional_usdc=float(qty_usdc_real),
        entry_price_preview=_safe_float((active_trade or {}).get("entry_price_preview"), float(entry_price)),
        entry_price=float(entry_price),
        source=("adopt_frozen" if frozen_plan else "adopt"),
    )

    snapshot = _build_active_trade_snapshot(
        user_id=user_id,
        symbol=symbol,
        symbol_for_exec=symbol_for_exec,
        direction=direction,
        side=side,
        opposite=opposite,
        entry_price=float(entry_price),
        qty_coin_for_log=float(qty_coin_real),
        qty_usdc_for_profit=float(qty_usdc_real),
        best_score=float(adopt_best_score),
        entry_strength=float(adopt_entry_strength),
        mode="ADOPT",
        sl_price_pct=float(adopt_sl_price_pct),
        mgmt=adopt_mgmt,
        opened_at_ms=int(existing_opened_at_ms) if existing_opened_at_ms else int(time.time() * 1000),
        trade_plan=trade_plan,
        entry_context=entry_context,
        existing_state=active_trade,
        manager_bootstrap_pending=False,
    )
    snapshot["adopt_last_seen_ts"] = time.time()
    snapshot["adopt_plan_logged_at"] = float((active_trade or {}).get("adopt_plan_logged_at", 0.0) or 0.0)
    snapshot["adopt_sl_checked_at"] = float((active_trade or {}).get("adopt_sl_checked_at", 0.0) or 0.0)
    snapshot["sl_in_exchange"] = bool((active_trade or {}).get("sl_in_exchange", False))
    _set_active_trade(user_id, snapshot)

    now_ts = time.time()
    last_plan_logged_at = float(snapshot.get("adopt_plan_logged_at", 0.0) or 0.0)
    should_log_plan = (not same_position) or (not manager_running) or ((now_ts - last_plan_logged_at) >= float(ADOPT_RELOG_SECONDS))
    if should_log_plan:
        _log_trade_plan(
            context=("ADOPT_FROZEN" if frozen_plan else "ADOPT"),
            user_id=user_id,
            symbol=symbol,
            direction=direction,
            entry_price=float(entry_price),
            sl_price_pct=float(adopt_sl_price_pct),
            tp_activate_price=float(adopt_mgmt.get("tp_activate_price", 0.0) or 0.0),
            break_even_activation_price=float(adopt_mgmt.get("break_even_activation_price", 0.0) or 0.0),
            break_even_offset_price=float(adopt_mgmt.get("break_even_offset_price", 0.0) or 0.0),
            qty_coin=float(qty_coin_real),
            notional_usdc=float(qty_usdc_real),
            bucket=str(adopt_mgmt.get("bucket", "")),
            strategy_id=str((active_trade or {}).get("strategy_id") or ""),
            strategy_model=str((active_trade or {}).get("strategy_model") or ""),
        )
        _update_active_trade_fields(user_id, adopt_plan_logged_at=now_ts)

    last_sl_checked_at = float(snapshot.get("adopt_sl_checked_at", 0.0) or 0.0)
    sl_known_ok = bool(snapshot.get("sl_in_exchange", False)) and bool(snapshot.get("tp_in_exchange", False))
    should_recheck_sl = (not sl_known_ok) or (not same_position) or ((now_ts - last_sl_checked_at) >= float(ADOPT_SL_RECHECK_SECONDS))

    adopt_sl_ok = sl_known_ok
    if should_recheck_sl:
        stop_context = ("ADOPT_FROZEN" if frozen_plan else "ADOPT")
        stop_mode = "initial"
        desired_stop_price = _pct_to_abs_price(float(entry_price), float(adopt_sl_price_pct), direction, kind="sl")
        desired_tp_price = _pct_to_abs_price(float(entry_price), float(adopt_mgmt.get("tp_activate_price", 0.0) or 0.0), direction, kind="tp_activate")

        # Aunque un estado antiguo indique break-even, restauramos el SL inicial.
        snapshot["break_even_armed"] = False

        adopt_protection = _ensure_exchange_protection_pair(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
            qty_coin=float(qty_coin_real),
            stop_trigger_price=float(desired_stop_price),
            take_profit_trigger_price=float(desired_tp_price),
            context=stop_context,
            stop_mode=str(stop_mode),
            replace_existing=True,
        )
        adopt_sl_ok = bool(adopt_protection.get("sl_ok"))
        adopt_tp_ok = bool(adopt_protection.get("tp_ok"))

        _update_active_trade_fields(
            user_id,
            sl_in_exchange=bool(adopt_sl_ok),
            tp_in_exchange=bool(adopt_tp_ok),
            adopt_sl_checked_at=now_ts,
            exchange_stop_mode=str(stop_mode),
            exchange_stop_reference_price=float(desired_stop_price),
            exchange_tp_mode="fixed",
            exchange_tp_reference_price=float(desired_tp_price),
        )
        if adopt_sl_ok:
            log(
                f"🛡️ ADOPT protección validada user={user_id} symbol={symbol} dir={direction} entry={float(entry_price):.8f} mode={stop_mode} stop_price={float(desired_stop_price):.8f} tp_price={float(desired_tp_price):.8f} sl_ok={bool(adopt_sl_ok)} tp_ok={bool(adopt_tp_ok)} reason={adopt_protection.get('reason')}",
                "WARN",
            )
        else:
            log(
                f"🛡️ ADOPT NO pudo validar/crear protección user={user_id} symbol={symbol} dir={direction} entry={float(entry_price):.8f} mode={stop_mode} stop_price={float(desired_stop_price):.8f} tp_price={float(desired_tp_price):.8f} reason={adopt_protection.get('reason')}",
                "CRITICAL",
            )

    if manager_running and same_position:
        _update_active_trade_fields(user_id, manager_heartbeat_ts=now_ts, adopt_last_seen_ts=now_ts)
        return {"event": "MANAGER", "manager": {"symbol": symbol, "started": False, "already_running": True}}

    started = _start_manager_thread(
        user_id=user_id,
        symbol=symbol,
        symbol_for_exec=symbol_for_exec,
        direction=direction,
        side=side,
        opposite=opposite,
        entry_price=entry_price,
        qty_coin_for_log=qty_coin_real,
        qty_usdc_for_profit=qty_usdc_real,
        best_score=float(adopt_best_score),
        entry_strength=float(adopt_entry_strength),
        mode="ADOPT",
        sl_price_pct=float(adopt_sl_price_pct),
        mgmt=adopt_mgmt,
        opened_at_ms=int((_active_trade_opened_since_ms(snapshot) or int(time.time() * 1000))),
        trade_plan=trade_plan,
        entry_context=entry_context,
    )

    if started:
        log(f"MANAGER adoptado en background para {symbol} (user={user_id})", "WARN")
    else:
        log(f"MANAGER ya estaba corriendo para user={user_id} (skip start)", "INFO")

    return {"event": "MANAGER", "manager": {"symbol": symbol, "started": started}}


def _entry_drift_check(*, signal: dict[str, Any], current_price: float, direction: str) -> tuple[bool, dict[str, Any]]:
    ref_price = _safe_float(signal.get("signal_reference_price"), _safe_float(signal.get("close_5"), 0.0))
    current = _safe_float(current_price, 0.0)
    atr_pct = max(_safe_float(signal.get("atr_pct"), 0.0), 0.0)
    allowed = adaptive_entry_drift_limit(
        atr_pct=atr_pct,
        base_pct=float(ENTRY_MAX_DRIFT_PCT),
        atr_mult=float(ENTRY_MAX_DRIFT_ATR_MULT),
        hard_pct=float(ENTRY_MAX_DRIFT_HARD_PCT),
    )
    result = evaluate_entry_drift(
        direction=direction,
        signal_price=ref_price,
        current_price=current,
        allowed_pct=allowed,
        adverse_mult=float(ENTRY_MAX_ADVERSE_DRIFT_MULT),
    )
    return bool(result.get("ok")), result


def _resolve_structural_exit_plan(
    *,
    signal: dict[str, Any],
    entry_price: float,
    direction: str,
    fallback_sl_pct: float,
    fallback_tp_pct: float,
) -> tuple[bool, dict[str, Any]]:
    entry = _safe_float(entry_price, 0.0)
    d = str(direction or "").lower()
    if entry <= 0.0 or d not in {"long", "short"}:
        return False, {"reason": "BAD_ENTRY_PRICE"}

    stop_price = _safe_float(signal.get("stop_price"), _safe_float(signal.get("initial_stop_price"), 0.0))
    target_price = _safe_float(signal.get("target_price"), _safe_float(signal.get("initial_tp_price"), 0.0))
    structural_available = stop_price > 0.0 and target_price > 0.0

    if not structural_available:
        sl_pct = max(_safe_float(fallback_sl_pct, 0.0), 0.0)
        tp_pct = max(_safe_float(fallback_tp_pct, 0.0), 0.0)
        if sl_pct <= 0.0 or tp_pct <= 0.0:
            return False, {"reason": "NO_EXIT_PLAN"}
        stop_price = _pct_to_abs_price(entry, sl_pct, d, kind="sl")
        target_price = _pct_to_abs_price(entry, tp_pct, d, kind="tp_activate")

    rr_info = absolute_risk_reward(
        direction=d,
        entry_price=entry,
        stop_price=stop_price,
        target_price=target_price,
    )
    if not rr_info.get("ok"):
        return False, {
            "reason": str(rr_info.get("reason") or "STRUCTURE_CONSUMED_BY_ENTRY"),
            "entry_price": entry,
            "stop_price": stop_price,
            "target_price": target_price,
            "rr": float(rr_info.get("rr") or 0.0),
        }

    pct = levels_to_percentages(
        direction=d,
        entry_price=entry,
        stop_price=stop_price,
        target_price=target_price,
    )
    sl_pct = float(pct.get("sl_pct") or 0.0)
    tp_pct = float(pct.get("tp_pct") or 0.0)
    rr = float(rr_info.get("rr") or 0.0)
    if not meets_min_rr(rr, float(MIN_ENTRY_RR)):
        return False, {
            "reason": "RR_TOO_LOW_AFTER_ENTRY",
            "entry_price": entry,
            "stop_price": stop_price,
            "target_price": target_price,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "rr": rr,
        }
    return True, {
        "reason": "OK",
        "entry_price": entry,
        "stop_price": stop_price,
        "target_price": target_price,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "rr": rr,
        "structural": bool(structural_available),
    }


_emergency_pending: dict[int, dict] = {}


def _process_emergency_exit(user_id: int, intent: dict) -> bool:
    symbol = str(intent['symbol'])
    coin = str(intent['symbol_for_exec'])
    def protect(size):
        stop = float(intent.get('stop_price') or 0.0)
        if stop > 0:
            # Never remove an existing stop just because an emergency IOC failed.
            return _ensure_exchange_stop_trigger(
                user_id=user_id, symbol=symbol, symbol_for_exec=coin,
                direction='long' if size > 0 else 'short', qty_coin=abs(size),
                trigger_price=stop, context='EMERGENCY_RESIDUAL', replace_existing=False)
        return False
    result = close_verified(
        read_size=lambda: get_position_size_verified(user_id, coin),
        close_order=lambda size: place_market_order(user_id, coin,
            'sell' if size > 0 else 'buy', abs(size), reduce_only=True),
        cancel_orders=lambda: cancel_all_orders_for_symbol(user_id, coin),
        protect=protect,
    )
    if result['closed']:
        # Retain the intent if deleting it fails; a later cycle will re-confirm flat.
        database_module.settings_col.delete_one({'_id': f'emergency_exit:{user_id}'})
        _emergency_pending.pop(user_id, None)
        if _get_active_trade(user_id):
            _reconcile_orphan_closed_trade(user_id)
        log(f"EMERGENCY_EXIT_CONFIRMED_FLAT user={user_id} symbol={symbol}", "WARN")
        return True
    log(f"EMERGENCY_EXIT_PENDING user={user_id} symbol={symbol} result={result}", "CRITICAL")
    _publish_operational_snapshot(user_id, 'error',
        'Cierre de emergencia pendiente de confirmación; nuevas entradas bloqueadas.',
        mode='blocked', live_trade=True, active_symbol=symbol,
        metadata={'last_block_reason': 'EMERGENCY_EXIT_PENDING', 'emergency_exit': result})
    return False


def _resume_emergency_exit(user_id: int) -> bool:
    """True consumes this cycle, including when persistence is unavailable."""
    try:
        intent = _emergency_pending.get(user_id)
        if not intent:
            intent = database_module.settings_col.find_one({'_id': f'emergency_exit:{user_id}'})
        if not intent:
            return False
        _emergency_pending[user_id] = intent
        database_module.settings_col.update_one({'_id': f'emergency_exit:{user_id}'}, {'$set': intent}, upsert=True)
        _process_emergency_exit(user_id, intent)
    except Exception as exc:
        log(f"EMERGENCY_STATE_UNAVAILABLE user={user_id} error={exc}", "CRITICAL")
    return True


def _abort_new_position_after_fill(
    *, user_id: int, symbol: str, symbol_for_exec: str,
    size_real_signed: float, reason: str, stop_price: float = 0.0,
) -> None:
    log(f"POST_FILL_ENTRY_QUALITY_ABORT {symbol} reason={reason}", "CRITICAL")
    intent = {'symbol': symbol, 'symbol_for_exec': symbol_for_exec,
              'reason': reason, 'stop_price': float(stop_price), 'created_at': datetime.utcnow()}
    _emergency_pending[user_id] = intent
    try:
        database_module.settings_col.update_one({'_id': f'emergency_exit:{user_id}'}, {'$set': intent}, upsert=True)
    except Exception as exc:
        log(f"EMERGENCY_INTENT_PERSIST_FAILED user={user_id}: {exc}", "CRITICAL")
    try:
        _process_emergency_exit(user_id, intent)
    except Exception as exc:
        log(f"EMERGENCY_EXIT_FAILED user={user_id}: {exc}", "CRITICAL")
    _cooldown_symbol(user_id, symbol, SYMBOL_NOFILL_COOLDOWN_SECONDS)


def _signal_candidate_limit() -> int:
    try:
        raw = int(SCANNER_SHORTLIST_DEPTH_FOR_L2)
    except Exception:
        raw = 8
    if raw <= 0:
        raw = 8
    return max(1, min(int(raw), MAX_SIGNAL_EVAL_CANDIDATES, int(SHORTLIST_HARD_CAP)))


def _candidate_rank_tuple(signal: dict, scanner_row: dict) -> tuple:
    return (
        float(signal.get("score", 0.0) or 0.0),
        float(signal.get("strength", 0.0) or 0.0),
        float(scanner_row.get("score", 0.0) or 0.0),
        float(scanner_row.get("volume", 0.0) or 0.0),
        float(scanner_row.get("oi", 0.0) or 0.0),
    )


def _select_best_signal_from_scanner_shortlist(user_id: int, exclude_symbols: set[str], *, execution_mode: str = "live") -> dict | None:
    shortlist_started_ts = time.time()
    limit = _signal_candidate_limit()
    fetch_limit = max(int(limit * SHORTLIST_FETCH_MULTIPLIER), int(limit))
    shortlist = get_ranked_symbols(exclude_symbols=exclude_symbols, limit=fetch_limit,
                                    execution_mode=execution_mode)
    shortlist_fetch_elapsed_s = time.time() - shortlist_started_ts
    if not shortlist:
        log("Scanner no devolvió candidatos", "WARN")
        return None

    from app.strategies.breakout_reset import ADX_PERIOD, ATR_PERIOD, EMA_FAST, EMA_MID, EMA_SLOW, LOOKBACK_5M, TF_5M

    eval_started_ts = time.time()
    budget_deadline_ts = eval_started_ts + float(SHORTLIST_EVAL_BUDGET_SECONDS)
    btc_context = build_market_context(
        symbol="BTC",
        interval=TF_5M,
        limit=LOOKBACK_5M,
        ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
        adx_period=ADX_PERIOD,
        atr_period=ATR_PERIOD,
    )

    best_choice: dict | None = None
    best_shadow_choice: dict | None = None
    blocked_samples: list[str] = []
    rejection_counts = Counter()
    cache_hits = 0
    slow_samples: list[str] = []
    evaluated_count = 0
    budget_exhausted = False

    for idx, candidate in enumerate(shortlist, start=1):
        if evaluated_count >= int(limit):
            break
        if evaluated_count >= int(SHORTLIST_EVAL_MIN_CANDIDATES) and time.time() >= budget_deadline_ts:
            budget_exhausted = True
            blocked_samples.append("SHORTLIST_TIME_BUDGET_EXHAUSTED")
            break

        symbol = str(candidate.get("symbol") or "").upper()
        if not symbol:
            continue

        mark_symbol_evaluated(symbol)
        cached_skip = _get_shortlist_skip(symbol)
        if cached_skip:
            rejection_counts["CACHED:" + str(cached_skip.get("reason"))] += 1
            if len(blocked_samples) < 6:
                cached_reason = str(cached_skip.get("reason") or "COOLDOWN")
                cached_detail = str(cached_skip.get("detail") or "").strip()
                if cached_detail:
                    blocked_samples.append(f"{symbol}:COOLDOWN_{cached_reason}[{cached_detail[:140]}]")
                else:
                    blocked_samples.append(f"{symbol}:COOLDOWN_{cached_reason}")
            continue

        evaluated_count += 1
        try:
            symbol_ctx = build_market_context(
                symbol=symbol,
                interval=TF_5M,
                limit=LOOKBACK_5M,
                ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
                adx_period=ADX_PERIOD,
                atr_period=ATR_PERIOD,
            )
            # Modo de evaluación explícito, aislado: sólo DEMO usa la prueba H1/M15.
            # No modificar el snapshot cacheado del proveedor de mercado.
            strategy_ctx = dict(symbol_ctx)
            strategy_ctx["_execution_mode"] = "demo" if execution_mode == "demo" else "live"
            symbol_eval_started_ts = time.time()
            signal = get_entry_signal(symbol, market_context=strategy_ctx, btc_context=btc_context)
            symbol_eval_elapsed_s = time.time() - symbol_eval_started_ts
            if symbol_eval_elapsed_s >= float(SLOW_SYMBOL_EVAL_SECONDS) and len(slow_samples) < 6:
                slow_samples.append(f"{symbol}:{symbol_eval_elapsed_s:.2f}s")
        except Exception as e:
            rejection_counts["STRATEGY_EXCEPTION"] += 1
            blocked_samples.append(f"{symbol}:STRATEGY_EXCEPTION:{str(e)[:80]}")
            continue

        if not isinstance(signal, dict):
            rejection_counts["INVALID_SIGNAL"] += 1
            blocked_samples.append(f"{symbol}:INVALID_SIGNAL")
            continue

        cache_hits += int(bool(signal.get("signal_cache_hit")))
        if not signal.get("signal_cache_hit") and signal.get("liquidity_diagnostics"):
            log(f"LIQUIDITY_DIAGNOSTICS symbol={symbol} "
                f"diag={json.dumps(signal['liquidity_diagnostics'], sort_keys=True)}")
        shadow = signal.get("shadow_range") if isinstance(signal.get("shadow_range"), dict) else {}
        if bool(shadow.get("signal")):
            shadow_ranked = _shadow_candidate_rank_tuple(shadow, candidate)
            shadow_picked = {
                "symbol": symbol,
                "signal": signal,
                "shadow": shadow,
                "scanner": candidate,
                "rank_tuple": shadow_ranked,
                "shortlist_rank": idx,
                "shortlist_size": len(shortlist),
            }
            if (best_shadow_choice is None) or (shadow_picked["rank_tuple"] > best_shadow_choice["rank_tuple"]):
                best_shadow_choice = shadow_picked

        if not signal.get("signal"):
            reason = str(signal.get("reason") or "NO_SIGNAL")
            rejection_counts[reason] += 1
            router_detail = str(signal.get("router_reason_detail") or "").strip()
            if not signal.get("signal_cache_hit"):
                # Every fresh rejection must retain its measured values; summary
                # counters alone cannot explain an RR/trigger/router rejection.
                tf_diag = (symbol_ctx.get("timeframes") or {}).get(TF_5M) or {}
                rejection_diag = {
                    "candle_close_t": tf_diag.get("last_close_t"),
                    "reason": reason,
                    "regime": signal.get("regime_id"),
                    "strategy": signal.get("router_strategy_id"),
                    "diag": signal.get("diag") or {},
                    "router_detail": router_detail,
                    "attempts": signal.get("router_considered_strategies") or [],
                }
                log(f"REJECTION_DIAGNOSTICS symbol={symbol} "
                    f"diag={json.dumps(rejection_diag, sort_keys=True)}")
            if reason in {"NO_H1_BIAS", "NO_M15_BIAS"} and not signal.get("signal_cache_hit"):
                log(f"BIAS_DIAGNOSTICS symbol={symbol} reason={reason} "
                    f"diag={json.dumps(signal.get('diag') or {}, sort_keys=True)}")
            if bool(shadow.get("signal")) and len(blocked_samples) < 6:
                blocked_samples.append(
                    f"{symbol}:SHADOW_RANGE:{str(shadow.get('direction') or '').lower()}:{float(shadow.get('score') or 0.0):.2f}"
                )
            else:
                if len(blocked_samples) < 6:
                    if reason.startswith("ROUTER_") and router_detail:
                        blocked_samples.append(f"{symbol}:{reason}[{router_detail[:180]}]")
                    else:
                        blocked_samples.append(f"{symbol}:{reason}")

            reject_event_type = "router_blocked" if reason.startswith("ROUTER_") else "strategy_rejected"
            reject_scanner = dict(candidate or {})
            reject_scanner["shortlist_rank"] = idx
            reject_scanner["shortlist_size"] = len(shortlist)
            extra_payload = {
                "phase": "shortlist_rejected",
                "reject_reason": reason,
                "reject_reason_detail": router_detail,
                "router_reason": str(signal.get("router_reason") or reason),
                "router_regime_source": str(signal.get("router_regime_source") or ""),
                "router_candidate_regime": str(signal.get("router_candidate_regime") or ""),
                "router_candidate_confidence": float(signal.get("router_candidate_confidence") or 0.0),
                "router_candidate_scores": dict(signal.get("router_candidate_scores") or {}),
            }
            _record_strategy_router_event(
                user_id,
                event_type=reject_event_type,
                symbol=symbol,
                signal=signal,
                scanner_meta=reject_scanner,
                execution_mode=execution_mode,
                extra=extra_payload,
            )
            _cache_shortlist_skip(symbol, reason, router_detail)
            continue

        strength = float(signal.get("strength", 0.0) or 0.0)
        if strength < MIN_TRADE_STRENGTH:
            rejection_counts["WEAK_SIGNAL"] += 1
            if len(blocked_samples) < 6:
                blocked_samples.append(f"{symbol}:WEAK:{strength:.4f}")
            weak_scanner = dict(candidate or {})
            weak_scanner["shortlist_rank"] = idx
            weak_scanner["shortlist_size"] = len(shortlist)
            _record_strategy_router_event(
                user_id,
                event_type="signal_weak",
                symbol=symbol,
                signal=signal,
                scanner_meta=weak_scanner,
                execution_mode=execution_mode,
                extra={"phase": "shortlist_rejected", "reject_reason": "WEAK_SIGNAL", "strength_threshold": float(MIN_TRADE_STRENGTH)},
            )
            continue

        direction = str(signal.get("direction") or "").lower()
        if direction not in ("long", "short"):
            rejection_counts["BAD_DIRECTION"] += 1
            if len(blocked_samples) < 6:
                blocked_samples.append(f"{symbol}:BAD_DIRECTION:{direction}")
            bad_dir_scanner = dict(candidate or {})
            bad_dir_scanner["shortlist_rank"] = idx
            bad_dir_scanner["shortlist_size"] = len(shortlist)
            _record_strategy_router_event(
                user_id,
                event_type="strategy_rejected",
                symbol=symbol,
                signal=signal,
                scanner_meta=bad_dir_scanner,
                execution_mode=execution_mode,
                extra={"phase": "shortlist_rejected", "reject_reason": f"BAD_DIRECTION:{direction}"},
            )
            continue

        ranked = _candidate_rank_tuple(signal, candidate)
        picked = {
            "symbol": symbol,
            "symbol_for_exec": _norm_coin(symbol),
            "signal": signal,
            "strength": strength,
            "direction": direction,
            "scanner": candidate,
            "rank_tuple": ranked,
            "shortlist_rank": idx,
            "shortlist_size": len(shortlist),
        }

        if (best_choice is None) or (picked["rank_tuple"] > best_choice["rank_tuple"]):
            best_choice = picked

    if best_shadow_choice:
        shadow_live_overlap = bool(
            best_choice
            and best_choice.get("symbol") == best_shadow_choice.get("symbol")
            and str((best_choice.get("direction") or "")).lower() == str(((best_shadow_choice.get("shadow") or {}).get("direction") or "")).lower()
        )
        if not shadow_live_overlap:
            shadow_scanner = dict(best_shadow_choice.get("scanner") or {})
            shadow_scanner["shortlist_rank"] = int(best_shadow_choice.get("shortlist_rank") or 0)
            shadow_scanner["shortlist_size"] = int(best_shadow_choice.get("shortlist_size") or len(shortlist))
            _record_strategy_router_event(
                user_id,
                event_type="shadow_opportunity",
                symbol=str(best_shadow_choice.get("symbol") or ""),
                signal=best_shadow_choice.get("signal"),
                scanner_meta=shadow_scanner,
                execution_mode="demo_shadow" if execution_mode == "demo" else "shadow",
                extra={
                    "phase": "shortlist_shadow",
                    "cycle_result": "no_selected_signal" if not best_choice else "selected_shadow_divergence",
                },
            )

    log(f"SCAN_DIAGNOSTICS user={user_id} evaluated={evaluated_count} shortlist={len(shortlist)} cache_hits={cache_hits} rejected={dict(rejection_counts)} budget_exhausted={budget_exhausted}")
    eval_elapsed_s = time.time() - eval_started_ts

    if not best_choice:
        suffix = f" reasons={'; '.join(blocked_samples)}" if blocked_samples else ""
        log(f"Shortlist scanner sin setup accionable (evaluados={evaluated_count}/{len(shortlist)}){suffix}", "INFO")
        try:
            database_module.record_strategy_router_event(
                int(user_id),
                {
                    "event_type": "scanner_no_signal",
                    "symbol": "",
                    "strategy_id": "none",
                    "regime_id": "unknown",
                    "execution_mode": "demo_router" if execution_mode == "demo" else "router",
                    "signal": False,
                    "selected": False,
                    "trade_opened": False,
                    "regime_changed": False,
                    "shadow_evaluated": bool(best_shadow_choice),
                    "shadow_signal": bool(best_shadow_choice and ((best_shadow_choice.get("shadow") or {}).get("signal"))),
                    "scanner_summary": {
                        "shortlist_size": int(len(shortlist) or 0),
                    },
                    "signal_summary": {},
                    "shadow_summary": {
                        "strategy_id": str(((best_shadow_choice or {}).get("shadow") or {}).get("strategy_id") or "range_mean_reversion"),
                        "direction": str(((best_shadow_choice or {}).get("shadow") or {}).get("direction") or "").lower(),
                        "signal": bool(best_shadow_choice and ((best_shadow_choice.get("shadow") or {}).get("signal"))),
                        "score": float((((best_shadow_choice or {}).get("shadow") or {}).get("score") or 0.0)),
                        "strength": float((((best_shadow_choice or {}).get("shadow") or {}).get("strength") or 0.0)),
                        "reason": str(((best_shadow_choice or {}).get("shadow") or {}).get("reason") or ""),
                        "evaluated": bool(best_shadow_choice),
                    },
                    "regime_summary": {},
                    "extra": {
                        "phase": "shortlist_no_signal",
                        "evaluated": int(evaluated_count or 0),
                        "fetched": int(len(shortlist) or 0),
                        "blocked_samples": list(blocked_samples[:6]),
                        "slow_samples": list(slow_samples[:6]),
                        "evaluated_count": int(evaluated_count or 0),
                        "shortlist_fetch_elapsed_s": round(float(shortlist_fetch_elapsed_s), 4),
                        "eval_elapsed_s": round(float(eval_elapsed_s), 4),
                        "budget_exhausted": bool(budget_exhausted),
                    },
                },
            )
        except Exception:
            pass
        return None

    best_choice["perf"] = {
        "evaluated_count": int(evaluated_count or 0),
        "shortlist_fetch_elapsed_s": round(float(shortlist_fetch_elapsed_s), 4),
        "eval_elapsed_s": round(float(eval_elapsed_s), 4),
        "budget_exhausted": bool(budget_exhausted),
        "slow_samples": list(slow_samples[:6]),
    }

    best_signal = best_choice["signal"]
    if best_signal.get("strategy_id") == "liquidity_sweep_reversal":
        log(f"LIQUIDITY_EXECUTION user={user_id} symbol={best_choice['symbol']} stage=SHORTLIST_SELECTED")
    scanner_meta = best_choice["scanner"]
    shadow = best_signal.get('shadow_range') if isinstance(best_signal.get('shadow_range'), dict) else {}
    shadow_suffix = ''
    if shadow:
        shadow_suffix = (
            f" shadow_range={int(bool(shadow.get('signal')))}"
            f" shadow_dir={str(shadow.get('direction') or '').lower()}"
            f" shadow_score={float(shadow.get('score') or 0.0):.2f}"
        )
    log(
        f"Shortlist evaluada: candidatos={len(shortlist)} elegido={best_choice['symbol']} shortlist_rank={best_choice['shortlist_rank']}/{best_choice['shortlist_size']} "
        f"strategy_score={float(best_signal.get('score', 0.0) or 0.0):.2f} strength={float(best_signal.get('strength', 0.0) or 0.0):.4f} "
        f"scanner_score={float(scanner_meta.get('score', 0.0) or 0.0):.4f}{shadow_suffix}",
        "INFO",
    )
    return best_choice


def execute_trade_cycle(user_id: int) -> dict | None:
    lock = _user_locks.setdefault(user_id, threading.Lock())
    if not lock.acquire(blocking=False):
        log(f"Usuario {user_id} — ciclo ya en ejecución, se salta", "WARN")
        return None

    try:
        cycle_started_ts = time.time()
        log(f"Usuario {user_id} — inicio ciclo")

        policy = database_module.get_user_cycle_policy(user_id)
        if not policy.get('should_run_cycle', False):
            log(f"Usuario {user_id} sin permiso operativo: {policy.get('runtime_message')}")
            return None

        manager_only = bool(policy.get('manager_only', False))
        exchange_snapshot = get_account_snapshot(user_id)
        if bool((exchange_snapshot or {}).get('credentials_repair_required')):
            log(f"Usuario {user_id} con credencial operativa inválida — ciclo bloqueado hasta reparación", "ERROR")
            _publish_operational_snapshot(
                user_id,
                'configuration_blocked',
                'La private key almacenada no pudo validarse. Reconfigúrala en la MiniApp antes de reactivar la operativa.',
                mode='blocked',
                live_trade=bool(exchange_snapshot.get('has_open_position')),
                active_symbol=((exchange_snapshot.get('active_symbols') or [None])[0]),
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'credentials_invalid', 'last_decision': 'credentials_invalid', 'last_block_reason': 'private_key_decrypt_error', 'last_cycle_at': datetime.utcnow()},
            )
            _publish_scanner_runtime('online', user_id=user_id, decision='credentials_invalid', exchange_snapshot=exchange_snapshot, extra={'phase': 'credentials_invalid'})
            return {'event': 'CREDENTIALS_INVALID'}
        _publish_scanner_runtime(
            'online',
            user_id=user_id,
            decision='cycle_started',
            exchange_snapshot=exchange_snapshot,
            extra={'phase': 'engine_cycle_started'},
        )

        if _resume_emergency_exit(user_id):
            return {'event': 'EMERGENCY_EXIT_RECONCILIATION'}

        # Reconciliación defensiva: si el exchange ya no tiene posición pero el bot
        # conserva estado activo en memoria, registramos el cierre y activamos cooldown.
        try:
            if _reconcile_orphan_closed_trade(user_id):
                log(f"Usuario {user_id} — cierre reconciliado desde exchange", "CRITICAL")
                _publish_operational_snapshot(
                    user_id,
                    'cycle_completed',
                    'Se reconciliò un cierre detectado directamente desde el exchange.',
                    mode='entries_enabled',
                    live_trade=False,
                    exchange_snapshot=exchange_snapshot,
                    metadata={'last_result': 'reconcile_closed', 'last_decision': 'reconciled_from_exchange', 'last_cycle_at': datetime.utcnow()},
                )
                return {"event": "RECONCILE_CLOSED"}
        except Exception as e:
            log(f"Reconcile error user={user_id} err={e}\n{traceback.format_exc()}", "ERROR")

        pending_accounting = _get_active_trade(user_id) or {}
        if pending_accounting.get("reconciliation_pending"):
            _publish_operational_snapshot(user_id, 'reconciliation_pending',
                'Cierre pendiente de conciliar: se conserva el historial y se reintenta antes de abrir otra operación.',
                mode='blocked', live_trade=bool((exchange_snapshot or {}).get('has_open_position')),
                active_symbol=pending_accounting.get('symbol'), exchange_snapshot=exchange_snapshot)
            return {"event": "RECONCILIATION_PENDING"}

        watchdog_resp = _ensure_manager_watchdog(user_id)
        if watchdog_resp:
            _publish_operational_snapshot(
                user_id,
                'manager_only',
                'La cuenta mantiene una posición abierta bajo gestión activa del motor.',
                mode='manager_only',
                live_trade=True,
                active_symbol=((watchdog_resp.get('manager') or {}).get('symbol')) if isinstance(watchdog_resp, dict) else None,
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'manager_watchdog', 'last_decision': 'manager_watchdog', 'last_cycle_at': datetime.utcnow()},
            )
            return watchdog_resp

        # ✅ Capital operativo REAL (exchange). Interés compuesto natural.
        # Se usa balance withdrawable para sizing seguro
        capital = float(get_balance(user_id) or 0.0)
        log(f"Capital (Exchange/withdrawable): {capital}")
        _publish_operational_snapshot(
            user_id,
            'cycle_running',
            'El motor leyó la cuenta del exchange y está evaluando elegibilidad operativa.',
            mode='cycle_running',
            live_trade=bool(exchange_snapshot.get('has_open_position')),
            active_symbol=((exchange_snapshot.get('active_symbols') or [None])[0]),
            exchange_snapshot=exchange_snapshot,
            metadata={
                'last_result': 'balance_read',
                'last_decision': 'reading_exchange',
                'last_cycle_at': datetime.utcnow(),
                'exchange_available_balance': float(capital),
                'exchange_account_value': exchange_snapshot.get('account_value'),
                'positions_count': exchange_snapshot.get('positions_count'),
            },
        )
        _publish_scanner_runtime(
            'online',
            user_id=user_id,
            decision='balance_read',
            exchange_snapshot=exchange_snapshot,
            extra={'phase': 'balance_read', 'available_balance': float(capital), 'positions_count': exchange_snapshot.get('positions_count')},
        )

        # ✅ Si ya existe posición abierta en el exchange, SIEMPRE priorizamos modo MANAGER.
        # Importante: con posiciones abiertas el balance withdrawable puede verse bajo,
        # así que NO debemos bloquear por MIN_CAPITAL_USDC (si no, se pierde la reanudación).
        if has_open_position(user_id):
            log("Ya hay una posición abierta en el exchange — entrando en modo MANAGER (SL/TRAIL)", "WARN")
            _publish_operational_snapshot(
                user_id,
                'manager_only',
                'Hay una posición activa en el exchange. El motor pasa a modo de gestión.',
                mode='manager_only',
                live_trade=True,
                active_symbol=((exchange_snapshot.get('active_symbols') or [None])[0]),
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'open_position_detected', 'last_decision': 'manager_mode', 'last_cycle_at': datetime.utcnow(), 'last_block_reason': 'open_position_detected'},
            )
            return _manage_existing_open_position(user_id)

        if manager_only:
            log(f"Usuario {user_id} en modo MANAGER_ONLY — no se evaluarán nuevas entradas", "INFO")
            _publish_operational_snapshot(
                user_id,
                'manager_only',
                'Nuevas entradas deshabilitadas. Solo gestión de posición activa.',
                mode='manager_only',
                live_trade=bool(exchange_snapshot.get('has_open_position')),
                active_symbol=((exchange_snapshot.get('active_symbols') or [None])[0]),
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'manager_only_idle', 'last_decision': 'manager_only', 'last_cycle_at': datetime.utcnow()},
            )
            return {'event': 'MANAGER_ONLY_IDLE'}

        # ✅ Guard: capital mínimo (evita órdenes ridículas) — solo aplica cuando NO hay posición abierta
        if capital < float(MIN_CAPITAL_USDC):
            log(f"Capital insuficiente ({capital} USDC) < {MIN_CAPITAL_USDC} — no se ejecuta trading", "WARN")
            _publish_operational_snapshot(
                user_id,
                'configuration_blocked',
                f'Capital insuficiente para operar: {capital:.4f} USDC observados.',
                mode='blocked',
                live_trade=False,
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'capital_insufficient', 'last_decision': 'blocked', 'last_block_reason': 'capital_insufficient', 'last_cycle_at': datetime.utcnow()},
            )
            _publish_scanner_runtime('online', user_id=user_id, decision='capital_insufficient', exchange_snapshot=exchange_snapshot, extra={'phase': 'blocked_capital'})
            return None


        ok_trade, reason_trade = _can_trade_now(user_id)
        if not ok_trade:
            log(f"Bloqueo responsable: {reason_trade}", "INFO")
            _publish_operational_snapshot(
                user_id,
                'configuration_blocked',
                f'El motor bloqueó nuevas entradas: {reason_trade}',
                mode='blocked',
                live_trade=False,
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'trade_gate_blocked', 'last_decision': 'blocked', 'last_block_reason': str(reason_trade), 'last_cycle_at': datetime.utcnow()},
            )
            _publish_scanner_runtime('online', user_id=user_id, decision='trade_gate_blocked', exchange_snapshot=exchange_snapshot, extra={'phase': 'trade_gate_blocked', 'reason': str(reason_trade)})
            return None

        ok_risk, reason_risk = _risk_governor_allows_new_entries(user_id)
        if not ok_risk:
            log(f"Bloqueo responsable: {reason_risk}", "INFO")
            _publish_operational_snapshot(
                user_id,
                'configuration_blocked',
                f'El gobernador de riesgo bloqueó nuevas entradas: {reason_risk}',
                mode='blocked',
                live_trade=False,
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'risk_blocked', 'last_decision': 'risk_blocked', 'last_block_reason': str(reason_risk), 'last_cycle_at': datetime.utcnow()},
            )
            _publish_scanner_runtime('online', user_id=user_id, decision='risk_blocked', exchange_snapshot=exchange_snapshot, extra={'phase': 'risk_blocked', 'reason': str(reason_risk)})
            return None

        exclude = _get_excluded_symbols(user_id)
        selected = _select_best_signal_from_scanner_shortlist(user_id, exclude)
        if not selected:
            _publish_operational_snapshot(
                user_id,
                'entries_enabled',
                'Ciclo completado sin oportunidad accionable del scanner.',
                mode='entries_enabled',
                live_trade=False,
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'no_signal', 'last_decision': 'scanner_no_signal', 'last_cycle_at': datetime.utcnow()},
            )
            _publish_scanner_runtime('online', user_id=user_id, decision='no_signal', exchange_snapshot=exchange_snapshot, extra={'phase': 'scanner_no_signal'})
            total_cycle_elapsed_s = time.time() - cycle_started_ts
            if total_cycle_elapsed_s >= float(SLOW_CYCLE_WARN_SECONDS):
                log(f"Ciclo lento user={user_id} total={total_cycle_elapsed_s:.2f}s fase=scanner_no_signal", "WARN")
            return None

        symbol = str(selected["symbol"]).upper()
        symbol_for_exec = str(selected["symbol_for_exec"]).upper()
        signal = dict(selected["signal"] or {})
        strength = float(selected["strength"] or 0.0)
        direction = str(selected["direction"] or "").lower()
        scanner_meta = dict(selected.get("scanner") or {})
        perf_meta = dict(selected.get("perf") or {})

        if strength < MIN_TRADE_STRENGTH:
            log(f"Señal débil bloqueada: strength={strength:.4f} < {MIN_TRADE_STRENGTH}", "INFO")
            return None

        if direction not in ("long", "short"):
            log(f"Dirección inválida en señal: {direction}", "ERROR")
            return None

        side = "buy" if direction == "long" else "sell"
        opposite = "sell" if side == "buy" else "buy"

        shadow = signal.get('shadow_range') if isinstance(signal.get('shadow_range'), dict) else {}
        if perf_meta:
            perf_fetch_elapsed_s = float(perf_meta.get('shortlist_fetch_elapsed_s') or 0.0)
            perf_eval_elapsed_s = float(perf_meta.get('eval_elapsed_s') or 0.0)
            perf_budget_exhausted = bool(perf_meta.get('budget_exhausted'))
            perf_slow_samples = perf_meta.get('slow_samples')
            if perf_eval_elapsed_s >= float(SLOW_CYCLE_WARN_SECONDS) or perf_budget_exhausted:
                log(
                    "Shortlist profundo user={} fetch={:.2f}s eval={:.2f}s budget_exhausted={} slow={}".format(
                        user_id,
                        perf_fetch_elapsed_s,
                        perf_eval_elapsed_s,
                        perf_budget_exhausted,
                        perf_slow_samples,
                    ),
                    "WARN",
                )
        shadow_suffix = ''
        if shadow:
            shadow_suffix = (
                f" shadow_range={int(bool(shadow.get('signal')))}"
                f" shadow_dir={str(shadow.get('direction') or '').lower()}"
                f" shadow_score={float(shadow.get('score') or 0.0):.2f}"
            )
        log(
            f"SEÑAL CONFIRMADA {symbol} {direction.upper()} strength={signal.get('strength')} score={signal.get('score')} "
            f"scanner_score={scanner_meta.get('score')} model={signal.get('strategy_model', 'strategy')} "
            f"strategy_id={signal.get('strategy_id')} regime={signal.get('regime_id')} router={signal.get('router_decision')}{shadow_suffix}",
            "INFO",
        )
        _publish_operational_snapshot(
            user_id,
            'cycle_running',
            f'Scanner confirmó {symbol} {direction.upper()} como mejor oportunidad del ciclo.',
            mode='entries_enabled',
            live_trade=False,
            active_symbol=symbol,
            exchange_snapshot=exchange_snapshot,
            metadata={
                'last_result': 'signal_selected',
                'last_decision': 'signal_selected',
                'last_symbol': symbol,
                'last_cycle_at': datetime.utcnow(),
                'scanner_score': scanner_meta.get('score'),
                'strategy_model': signal.get('strategy_model'),
                'strategy_id': signal.get('strategy_id'),
                'regime_id': signal.get('regime_id'),
                'router_decision': signal.get('router_decision'),
                'shadow_signal': signal.get('shadow_signal'),
                'shadow_strategy_id': signal.get('shadow_strategy_id'),
                'shadow_direction': signal.get('shadow_direction'),
                'shadow_score': signal.get('shadow_score'),
                'shortlist_fetch_elapsed_s': perf_meta.get('shortlist_fetch_elapsed_s'),
                'signal_eval_elapsed_s': perf_meta.get('eval_elapsed_s'),
                'budget_exhausted': perf_meta.get('budget_exhausted'),
            },
        )
        _publish_scanner_runtime('online', user_id=user_id, symbol=symbol, decision='signal_selected', exchange_snapshot=exchange_snapshot, extra={'phase': 'signal_selected', 'scanner_score': scanner_meta.get('score'), 'strategy_model': signal.get('strategy_model'), 'strategy_id': signal.get('strategy_id'), 'regime_id': signal.get('regime_id'), 'router_decision': signal.get('router_decision'), 'shadow_signal': signal.get('shadow_signal'), 'shadow_strategy_id': signal.get('shadow_strategy_id'), 'shadow_direction': signal.get('shadow_direction'), 'shadow_score': signal.get('shadow_score'), 'shortlist_fetch_elapsed_s': perf_meta.get('shortlist_fetch_elapsed_s'), 'signal_eval_elapsed_s': perf_meta.get('eval_elapsed_s'), 'budget_exhausted': perf_meta.get('budget_exhausted'), 'slow_samples': perf_meta.get('slow_samples')})
        _record_strategy_router_event(
            user_id,
            event_type='signal_selected',
            symbol=symbol,
            signal=signal,
            scanner_meta={**scanner_meta, 'shortlist_rank': selected.get('shortlist_rank'), 'shortlist_size': selected.get('shortlist_size')},
            execution_mode='live',
            selected=True,
            extra={'phase': 'signal_selected'},
        )

        risk = validate_trade_conditions(capital, strength)
        if not risk.get("ok"):
            log(f"Trade cancelado: {risk.get('reason')}", "WARN")
            _publish_operational_snapshot(
                user_id,
                'configuration_blocked',
                f'La validación de riesgo canceló la entrada: {risk.get("reason")}',
                mode='blocked',
                live_trade=False,
                active_symbol=symbol,
                exchange_snapshot=exchange_snapshot,
                metadata={'last_result': 'risk_validation_blocked', 'last_decision': 'risk_validation_blocked', 'last_symbol': symbol, 'last_block_reason': risk.get('reason'), 'last_cycle_at': datetime.utcnow()},
            )
            _record_strategy_router_event(
                user_id,
                event_type='signal_blocked_risk',
                symbol=symbol,
                signal=signal,
                scanner_meta={**scanner_meta, 'shortlist_rank': selected.get('shortlist_rank'), 'shortlist_size': selected.get('shortlist_size')},
                execution_mode='live',
                extra={'phase': 'risk_validation_blocked', 'risk_reason': str(risk.get('reason') or '')},
            )
            return None
        mgmt = _coalesce_management_params(signal=signal, entry_strength=float(strength), best_score=float(signal.get("score", 0.0) or 0.0))
        try:
            mgmt["strategy_model"] = str(signal.get("strategy_model", ""))
            mgmt["atr_pct"] = float(signal.get("atr_pct", 0.0) or 0.0)
        except Exception:
            pass
        tp_activate_price = float(mgmt["tp_activate_price"])
        strategy_sl_price_pct = float(signal.get("sl_price_pct", 0.0) or 0.0)
        if strategy_sl_price_pct <= 0.0:
            log("Señal sin sl_price_pct válido", "ERROR")
            return None

        sl_price_pct = float(strategy_sl_price_pct)

        rr_multiple = 0.0
        try:
            rr_multiple = float(mgmt.get("tp_rr_multiple", 0.0) or 0.0)
        except Exception:
            rr_multiple = 0.0
        if rr_multiple <= 0.0 and sl_price_pct > 0.0 and tp_activate_price > 0.0:
            rr_multiple = float(tp_activate_price) / float(sl_price_pct)
        if not meets_min_rr(rr_multiple, float(MIN_ENTRY_RR)):
            log(
                f"Entrada bloqueada por calidad RR: {symbol} rr={rr_multiple:.3f} < {MIN_ENTRY_RR:.3f}",
                "WARN",
            )
            _publish_operational_snapshot(
                user_id,
                'entries_enabled',
                f'Entrada bloqueada por relación riesgo/beneficio insuficiente ({rr_multiple:.2f}R).',
                mode='entries_enabled',
                live_trade=False,
                active_symbol=symbol,
                exchange_snapshot=exchange_snapshot,
                metadata={
                    'last_result': 'entry_quality_blocked',
                    'last_decision': 'rr_guard_blocked',
                    'last_symbol': symbol,
                    'last_block_reason': 'RR_TOO_LOW',
                    'rr_multiple': float(rr_multiple),
                    'min_entry_rr': float(MIN_ENTRY_RR),
                    'last_cycle_at': datetime.utcnow(),
                },
            )
            return None

        log(
            f"Plan de riesgo por trade: bucket={mgmt['bucket']} TP_fijo={tp_activate_price:.6f}, "
            f"exit_policy=FIXED_TP_SL, strategy_sl={strategy_sl_price_pct:.6f}, SL(exchange)={sl_price_pct:.6f}",
            "INFO",
        )
        # ✅ Sizing real gobernado por risk.py.
        # La validación de riesgo ya decidió el tamaño permitido; el engine no debe ignorarlo.
        if float(capital) < float(MIN_CAPITAL_USDC):
            log(f"Capital demasiado bajo para operar ({capital} USDC) < {MIN_CAPITAL_USDC}", "WARN")
            return None

        approved_margin_usdc = max(0.0, min(_safe_float(risk.get("position_size"), 0.0), float(capital)))
        target_notional_usdc = float(approved_margin_usdc) * float(LEVERAGE)

        if approved_margin_usdc <= 0:
            log(f"Margen aprobado inválido ({approved_margin_usdc} USDC) — skip", "WARN")
            return None
        if target_notional_usdc < float(MIN_NOTIONAL_USDC):
            log(f"CONFIGURED_NOTIONAL_BELOW_EXCHANGE_MIN notional={target_notional_usdc:.4f} min={MIN_NOTIONAL_USDC} — no se fuerza la orden", "WARN")
            _publish_operational_snapshot(user_id, 'configuration_blocked',
                'El capital configurado produce un nominal inferior al mínimo del exchange.',
                mode='blocked', live_trade=False, active_symbol=symbol,
                metadata={'last_block_reason': 'CONFIGURED_NOTIONAL_BELOW_EXCHANGE_MIN',
                          'target_notional_usdc': target_notional_usdc, 'minimum_notional_usdc': MIN_NOTIONAL_USDC})
            return None

        entry_price_preview = float(get_price(symbol_for_exec) or 0.0)
        if entry_price_preview <= 0:
            log("No se pudo obtener precio para calcular qty_coin", "ERROR")
            return None

        drift_ok, drift_preview = _entry_drift_check(
            signal=signal,
            current_price=entry_price_preview,
            direction=direction,
        )
        if not drift_ok:
            log(
                f"Entrada bloqueada por drift {symbol}: reason={drift_preview.get('reason')} "
                f"ref={drift_preview.get('reference_price')} current={entry_price_preview} "
                f"drift={float(drift_preview.get('directional_drift_pct') or 0.0):.6f}",
                "WARN",
            )
            _cache_shortlist_skip(symbol, "ENTRY_PRICE_DRIFT", str(drift_preview))
            return None

        preview_plan_ok, preview_plan = _resolve_structural_exit_plan(
            signal=signal,
            entry_price=entry_price_preview,
            direction=direction,
            fallback_sl_pct=sl_price_pct,
            fallback_tp_pct=float(mgmt.get("tp_activate_price", 0.0) or 0.0),
        )
        if not preview_plan_ok:
            log(f"Entrada bloqueada por estructura/RR antes de ordenar {symbol}: {preview_plan}", "WARN")
            _cache_shortlist_skip(symbol, "ENTRY_STRUCTURE_INVALID", str(preview_plan))
            return None
        sl_price_pct = float(preview_plan.get("sl_pct") or sl_price_pct)
        mgmt["tp_activate_price"] = float(preview_plan.get("tp_pct") or mgmt.get("tp_activate_price") or 0.0)
        mgmt["tp_rr_multiple"] = float(preview_plan.get("rr") or 0.0)
        tp_activate_price = float(mgmt["tp_activate_price"])

        requested_qty_coin = round(target_notional_usdc / entry_price_preview, 8)
        if requested_qty_coin <= 0:
            log("qty_coin inválido tras conversión", "ERROR")
            return None

        if requested_qty_coin < float(MIN_QTY_COIN):
            log(f"qty_coin demasiado pequeño ({requested_qty_coin}) < {MIN_QTY_COIN} — skip", "WARN")
            return None

        entry_context = _build_entry_context(
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            scanner_meta=scanner_meta,
            signal=signal,
            risk=risk,
        )
        entry_context["execution_quality"] = {
            "preview_drift": dict(drift_preview),
            "preview_exit_plan": dict(preview_plan),
            "scanner_spread_bps": _safe_float(scanner_meta.get("spread_bps"), 0.0),
            "scanner_top_book_notional": _safe_float(scanner_meta.get("top_book_notional"), 0.0),
        }

        log(
            f"Ejecutando orden {symbol} {side} qty_coin={requested_qty_coin} "
            f"(approved_margin~{approved_margin_usdc} USDC -> target_notional~{target_notional_usdc} USDC, lev={LEVERAGE}x)"
        )

        # Nunca abrir una posición nueva con triggers reduce-only heredados.
        # Si el exchange no confirma que quedaron eliminados, abortamos la entrada.
        if not _cleanup_stale_reduce_only_triggers_before_open(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
        ):
            log(
                f"Entrada bloqueada: no se pudieron limpiar/certificar triggers reduce-only antes de abrir {symbol} {direction}",
                "CRITICAL",
            )
            _cooldown_symbol(user_id, symbol, SYMBOL_NOFILL_COOLDOWN_SECONDS)
            return None

        # Revalidación justo antes de enviar la IOC: el shortlist/cleanup puede
        # consumir segundos y no queremos perseguir una señal ya desarrollada.
        jit_price = float(get_price(symbol_for_exec) or 0.0)
        jit_drift_ok, jit_drift = _entry_drift_check(signal=signal, current_price=jit_price, direction=direction)
        jit_plan_ok, jit_plan = _resolve_structural_exit_plan(
            signal=signal,
            entry_price=jit_price,
            direction=direction,
            fallback_sl_pct=sl_price_pct,
            fallback_tp_pct=float(mgmt.get("tp_activate_price", 0.0) or 0.0),
        ) if jit_price > 0.0 else (False, {"reason": "NO_JIT_PRICE"})
        if (not jit_drift_ok) or (not jit_plan_ok):
            log(f"Entrada cancelada en guard JIT {symbol}: drift={jit_drift} plan={jit_plan}", "WARN")
            _cache_shortlist_skip(symbol, "ENTRY_JIT_QUALITY", f"drift={jit_drift};plan={jit_plan}")
            return None

        entry_price_preview = jit_price
        sl_price_pct = float(jit_plan.get("sl_pct") or sl_price_pct)
        mgmt["tp_activate_price"] = float(jit_plan.get("tp_pct") or mgmt.get("tp_activate_price") or 0.0)
        mgmt["tp_rr_multiple"] = float(jit_plan.get("rr") or 0.0)
        tp_activate_price = float(mgmt["tp_activate_price"])
        worst_price = entry_price_preview * (1 + float(EXECUTION_IOC_MAX_SLIPPAGE_BPS) / 10000
            if direction == 'long' else 1 - float(EXECUTION_IOC_MAX_SLIPPAGE_BPS) / 10000)
        rr_price_cap = rr_entry_price_limit(stop_price=float(jit_plan['stop_price']),
            target_price=float(jit_plan['target_price']), minimum_rr=float(MIN_ENTRY_RR))
        worst_price = min(worst_price, rr_price_cap) if direction == 'long' else max(worst_price, rr_price_cap)
        requested_qty_coin = math.floor(target_notional_usdc / entry_price_preview * 1e8) / 1e8
        entry_context["execution_quality"]["jit_drift"] = dict(jit_drift)
        entry_context["execution_quality"]["jit_exit_plan"] = dict(jit_plan)

        entry_started_at_ms = int(time.time() * 1000)
        if signal.get("strategy_id") == "liquidity_sweep_reversal":
            log(f"LIQUIDITY_EXECUTION user={user_id} symbol={symbol} stage=ORDER_SUBMITTED")
        open_resp = place_market_order(user_id, symbol_for_exec, side, requested_qty_coin, entry_price_limit=worst_price)

        if not open_resp:
            log("Orden OPEN sin respuesta/empty del exchange — abortando trade", "ERROR")
            _cooldown_symbol(user_id, symbol, SYMBOL_NOFILL_COOLDOWN_SECONDS)
            return None

        if not _resp_ok(open_resp):
            reason = _resp_reason(open_resp) or "EXCHANGE_REJECTED"
            log(f"OPEN no OK (reason={reason}) -> cooldown {SYMBOL_NOFILL_COOLDOWN_SECONDS}s para {symbol}", "ERROR")
            _cooldown_symbol(user_id, symbol, SYMBOL_NOFILL_COOLDOWN_SECONDS)
            return None

        if not _is_filled_exchange_response(open_resp):
            reason = _resp_reason(open_resp) or "NO_FILL"
            log(f"OPEN sin FIL (reason={reason}) -> cooldown {SYMBOL_NOFILL_COOLDOWN_SECONDS}s para {symbol}", "WARN")
            _cooldown_symbol(user_id, symbol, SYMBOL_NOFILL_COOLDOWN_SECONDS)
            return None

        _register_trade_attempt(user_id)
        mark_symbol_recent(symbol)

        # ✅ ENTRY PRICE: primero intento sacar el fill real del open_resp
        # ✅ ENTRY PRICE REAL (NO inventar con px/limit):
        # 1) Leer entryPx desde clearinghouseState (fuente del exchange).
        entry_state = float(get_position_entry_price(user_id, symbol_for_exec) or 0.0)
        if entry_state > 0:
            entry_price = entry_state
            log(f"Entry price (STATE REAL): {entry_price}", "INFO")
        else:
            # 2) fallback: intentar extraer avgPx/fillPx real del open_resp (si viene)
            entry_fill = _extract_fill_price(open_resp)
            if entry_fill and entry_fill > 0:
                entry_price = float(entry_fill)
                log(f"Entry price (FILL REAL): {entry_price}", "INFO")
            else:
                # 3) último recurso: mid/mark de get_price (solo para no crashear)
                entry_price = 0.0
                log("Entry price no verificable tras FILL", "CRITICAL")

        if entry_price <= 0:
            _abort_new_position_after_fill(user_id=user_id, symbol=symbol,
                symbol_for_exec=symbol_for_exec, size_real_signed=0.0,
                reason='NO_REAL_ENTRY_PRICE', stop_price=float(signal.get('stop_price') or 0.0))
            return None

        # ✅ Estado mínimo de protección para TP fijo + SL fijo
        # ✅ SANITY CHECK POST-FILL (ANTI-ÓRDENES RIDÍCULAS / DUST)
        verified_size = get_position_size_verified(user_id, symbol_for_exec)
        if verified_size is None:
            _abort_new_position_after_fill(user_id=user_id, symbol=symbol,
                symbol_for_exec=symbol_for_exec, size_real_signed=0.0,
                reason='POST_FILL_STATE_UNKNOWN', stop_price=float(signal.get('stop_price') or 0.0))
            return None
        size_real_signed = float(verified_size)
        size_real = abs(size_real_signed)

        if size_real <= 0.0:
            log("OPEN OK pero sin posición real (size=0) — treat as NO_FILL", "WARN")
            _cooldown_symbol(user_id, symbol, SYMBOL_NOFILL_COOLDOWN_SECONDS)
            return None

        notional_real = float(entry_price) * float(size_real)
        if signal.get("strategy_id") == "liquidity_sweep_reversal":
            log(f"LIQUIDITY_EXECUTION user={user_id} symbol={symbol} stage=FILL_OBSERVED "
                f"price={entry_price} qty={size_real}")
        # Persist the filled position before quality checks can initiate an emergency exit.
        _set_active_trade(user_id, _build_active_trade_snapshot(
            user_id=user_id, symbol=symbol, symbol_for_exec=symbol_for_exec,
            direction=direction, side=side, opposite=opposite, entry_price=entry_price,
            qty_coin_for_log=size_real, qty_usdc_for_profit=notional_real,
            best_score=float(signal.get('score') or 0), entry_strength=strength,
            mode='FILLED_PENDING_VALIDATION', sl_price_pct=sl_price_pct, mgmt=mgmt,
            opened_at_ms=entry_started_at_ms, entry_context=entry_context,
            manager_bootstrap_pending=True))
        if (notional_real < float(MIN_NOTIONAL_USDC)) or (size_real < float(MIN_QTY_COIN)):
            _abort_new_position_after_fill(user_id=user_id, symbol=symbol,
                symbol_for_exec=symbol_for_exec, size_real_signed=size_real_signed,
                reason='FILL_BELOW_MINIMUM', stop_price=float(signal.get('stop_price') or 0.0))
            return None

        # Segunda barrera: elimina cualquier trigger que haya quedado entre el
        # pre-clean y el fill. Si no podemos certificar cero triggers, cerramos
        # la nueva posición en vez de exponerla a un SL/TP heredado.
        if not _cleanup_stale_reduce_only_triggers_before_open(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
        ):
            log(
                f"POST_FILL_PROTECTION_CLEANUP_FAILED {symbol} {direction} — cerrando posición para evitar protección heredada/duplicada",
                "CRITICAL",
            )
            _abort_new_position_after_fill(user_id=user_id, symbol=symbol,
                symbol_for_exec=symbol_for_exec, size_real_signed=size_real_signed,
                reason='PROTECTION_CLEANUP_FAILED', stop_price=float(signal.get('stop_price') or 0.0))
            return None

        # El fill real es la última fuente de verdad. Si el precio ejecutado ya
        # consumió el target o degradó el RR, cerramos inmediatamente en vez de
        # montar TP/SL porcentuales deformados alrededor de un mal fill.
        actual_drift_ok, actual_drift = _entry_drift_check(
            signal=signal,
            current_price=entry_price,
            direction=direction,
        )
        actual_plan_ok, actual_plan = _resolve_structural_exit_plan(
            signal=signal,
            entry_price=entry_price,
            direction=direction,
            fallback_sl_pct=sl_price_pct,
            fallback_tp_pct=float(mgmt.get("tp_activate_price", 0.0) or 0.0),
        )
        if (not actual_drift_ok) or (not actual_plan_ok):
            _abort_new_position_after_fill(
                user_id=user_id,
                symbol=symbol,
                symbol_for_exec=symbol_for_exec,
                size_real_signed=size_real_signed,
                reason=f"drift={actual_drift};plan={actual_plan}",
                stop_price=float(signal.get("stop_price") or 0.0),
            )
            return None

        sl_price_pct = float(actual_plan.get("sl_pct") or sl_price_pct)
        mgmt["tp_activate_price"] = float(actual_plan.get("tp_pct") or mgmt.get("tp_activate_price") or 0.0)
        mgmt["tp_rr_multiple"] = float(actual_plan.get("rr") or 0.0)
        tp_activate_price = float(mgmt["tp_activate_price"])
        entry_context["execution_quality"]["actual_fill_drift"] = dict(actual_drift)
        entry_context["execution_quality"]["actual_fill_exit_plan"] = dict(actual_plan)
        entry_context["execution_quality"]["open_response_slippage"] = _safe_float((open_resp or {}).get("slippage"), 0.0) if isinstance(open_resp, dict) else 0.0
        signal["resolved_entry_price"] = float(entry_price)
        signal["resolved_sl_price_pct"] = float(sl_price_pct)
        signal["resolved_tp_price_pct"] = float(tp_activate_price)
        signal["resolved_rr"] = float(actual_plan.get("rr") or 0.0)

        trade_plan = _build_trade_plan(
            signal=signal,
            mgmt=mgmt,
            sl_price_pct=float(sl_price_pct),
            entry_strength=float(strength),
            best_score=float(signal.get("score", 0.0) or 0.0),
            approved_margin_usdc=float(approved_margin_usdc),
            leverage=float(LEVERAGE),
            target_notional_usdc=float(target_notional_usdc),
            requested_qty_coin=float(requested_qty_coin),
            actual_qty_coin=float(size_real),
            actual_notional_usdc=float(notional_real),
            entry_price_preview=float(entry_price_preview),
            entry_price=float(entry_price),
            source="open",
        )
        pre_manager_snapshot = _build_active_trade_snapshot(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
            side=side,
            opposite=opposite,
            entry_price=float(entry_price),
            qty_coin_for_log=float(size_real),
            qty_usdc_for_profit=float(notional_real),
            best_score=float(signal.get("score", 0.0) or 0.0),
            entry_strength=float(strength),
            mode="NEW_PENDING_MANAGER",
            sl_price_pct=float(sl_price_pct),
            mgmt=mgmt,
            opened_at_ms=int(entry_started_at_ms),
            trade_plan=trade_plan,
            entry_context=entry_context,
            existing_state=None,
            manager_bootstrap_pending=True,
        )
        _set_active_trade(user_id, pre_manager_snapshot)

        _log_trade_plan(
            context="OPEN",
            user_id=user_id,
            symbol=symbol,
            direction=direction,
            entry_price=float(entry_price),
            sl_price_pct=float(sl_price_pct),
            tp_activate_price=float(mgmt["tp_activate_price"]),
            break_even_activation_price=float(mgmt.get("break_even_activation_price", 0.0) or 0.0),
            break_even_offset_price=float(mgmt.get("break_even_offset_price", 0.0) or 0.0),
            qty_coin=float(size_real),
            notional_usdc=float(notional_real),
            bucket=str(mgmt.get("bucket", "")),
            strategy_id=str(signal.get("strategy_id") or ""),
            strategy_model=str(signal.get("strategy_model") or ""),
        )

        # ✅ PROTECCIÓN REAL EN EXCHANGE (BANK GRADE):
        # Antes de montar la protección del trade nuevo, limpiamos triggers reduce-only huérfanos
        # del mismo símbolo/lado para que un stop viejo nunca se herede al trade actual.
        # El cleanup post-fill ya fue validado justo antes de montar la protección.
        # TP fijo + SL fijo desde la apertura; estos niveles no se modifican durante la operación.
        initial_stop_price = _pct_to_abs_price(float(entry_price), float(sl_price_pct), direction, kind="sl")
        fixed_tp_price = _pct_to_abs_price(float(entry_price), float(mgmt["tp_activate_price"]), direction, kind="tp_activate")
        protection_in_exchange = _ensure_exchange_protection_pair(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
            qty_coin=float(size_real),
            stop_trigger_price=float(initial_stop_price),
            take_profit_trigger_price=float(fixed_tp_price),
            context="OPEN",
            stop_mode="initial",
            replace_existing=False,
        )
        _update_active_trade_fields(
            user_id,
            sl_in_exchange=bool(protection_in_exchange.get("sl_ok")),
            tp_in_exchange=bool(protection_in_exchange.get("tp_ok")),
            manager_bootstrap_pending=True,
            trade_plan=trade_plan,
            entry_context=entry_context,
            exchange_stop_mode="initial",
            exchange_stop_reference_price=float(initial_stop_price),
            exchange_tp_mode="fixed",
            exchange_tp_reference_price=float(fixed_tp_price),
        )

        # ✅ IMPORTANTÍSIMO:
        # No bloqueamos el ciclo gestionando el trade aquí (puede durar horas).
        # Arrancamos un MANAGER en background y devolvemos control al loop.
        started = _start_manager_thread(
            user_id=user_id,
            symbol=symbol,
            symbol_for_exec=symbol_for_exec,
            direction=direction,
            side=side,
            opposite=opposite,
            entry_price=entry_price,
            qty_coin_for_log=float(size_real),
            qty_usdc_for_profit=float(notional_real),
            best_score=float(signal.get("score", 0.0) or 0.0),
            entry_strength=float(strength),
            mode="NEW",
            sl_price_pct=float(sl_price_pct),
            mgmt=mgmt,
            opened_at_ms=int(entry_started_at_ms),
            trade_plan=trade_plan,
            entry_context=entry_context,
        )

        if started:
            log(f"MANAGER iniciado en background para {symbol} (user={user_id})", "WARN")
        else:
            log(f"MANAGER ya estaba corriendo para user={user_id} (skip start)", "INFO")

        _publish_operational_snapshot(
            user_id,
            'entries_enabled',
            f'Operación abierta en {symbol}. El motor quedó gestionando la posición.',
            mode='entries_enabled',
            live_trade=True,
            active_symbol=symbol,
            exchange_snapshot=exchange_snapshot,
            metadata={'last_result': 'trade_opened', 'last_decision': 'trade_opened', 'last_symbol': symbol, 'last_cycle_at': datetime.utcnow(), 'scanner_score': scanner_meta.get('score'), 'strategy_model': signal.get('strategy_model'), 'strategy_id': signal.get('strategy_id'), 'regime_id': signal.get('regime_id'), 'router_decision': signal.get('router_decision'), 'shadow_signal': signal.get('shadow_signal'), 'shadow_strategy_id': signal.get('shadow_strategy_id'), 'shadow_direction': signal.get('shadow_direction'), 'shadow_score': signal.get('shadow_score')},
        )
        _publish_scanner_runtime('online', user_id=user_id, symbol=symbol, decision='trade_opened', exchange_snapshot=exchange_snapshot, extra={'phase': 'trade_opened', 'scanner_score': scanner_meta.get('score'), 'strategy_model': signal.get('strategy_model'), 'strategy_id': signal.get('strategy_id'), 'regime_id': signal.get('regime_id'), 'router_decision': signal.get('router_decision'), 'shadow_signal': signal.get('shadow_signal'), 'shadow_strategy_id': signal.get('shadow_strategy_id'), 'shadow_direction': signal.get('shadow_direction'), 'shadow_score': signal.get('shadow_score')})
        _record_strategy_router_event(
            user_id,
            event_type='trade_opened',
            symbol=symbol,
            signal=signal,
            scanner_meta={**scanner_meta, 'shortlist_rank': selected.get('shortlist_rank'), 'shortlist_size': selected.get('shortlist_size')},
            execution_mode='live',
            selected=True,
            trade_opened=True,
            extra={'phase': 'trade_opened', 'manager_started': bool(started), 'entry_price': float(entry_price), 'qty_coin': float(size_real), 'notional_usdc': float(notional_real)},
        )

        open_payload = {
            "admin_evidence": opening_evidence(
                signal=signal, plan=trade_plan, stop=initial_stop_price,
                target=fixed_tp_price, protection=protection_in_exchange),
            "symbol": symbol,
            "coin": symbol,
            "side": str(direction or '').upper(),
            "direction": str(direction or '').upper(),
            "entry_price": float(entry_price),
            "qty": float(size_real),
            "notional_usdc": float(notional_real),
            "opened_at": datetime.utcnow().isoformat(),
            "opened_at_ms": int(entry_started_at_ms),
            "manager_started": bool(started),
            "message": _format_trade_open_user_message(
                symbol=symbol,
                direction=direction,
                entry_price=float(entry_price),
                qty_coin=float(size_real),
                notional_usdc=float(notional_real),
                opened_at_ms=int(entry_started_at_ms),
            ),
        }
        return {
            "event": "OPEN",
            "open": open_payload,
            "manager": {"started": started, "symbol": symbol},
        }

    finally:
        try:
            lock.release()
        except Exception:
            pass
