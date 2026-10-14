from __future__ import annotations

from typing import Any, Dict, Optional
import time
from app.signal_cache import SignalCache
from app.settings_utils import env_float

_SIGNAL_CACHE = SignalCache(ttl=env_float("SIGNAL_CACHE_TTL_SECONDS", 300.0))

from app.strategies.breakout_reset import DEFAULT_STRATEGY, STRATEGY_ID, STRATEGY_MODEL, STRATEGY_VERSION
from app.strategies.registry import get_strategy_registry
from app.strategies.router import get_strategy_router


# Shim de compatibilidad: el engine actual sigue importando desde app.strategy.
# En Fase 4 ya delegamos al router central, pero mantenemos intacto el contrato
# legacy para no romper el engine en producción.


def _default_strategy():
    return DEFAULT_STRATEGY


def _router():
    return get_strategy_router()


def _registry():
    return get_strategy_registry()


def _resolve_strategy(strategy_id: Optional[str] = None):
    sid = str(strategy_id or "").strip()
    if not sid:
        return _default_strategy()
    try:
        return _registry().get(sid)
    except Exception:
        return _default_strategy()


def get_trade_management_params_for_strategy(strategy_id: Optional[str], strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
    return _resolve_strategy(strategy_id).get_trade_management_params(strength, score, atr_pct)


def get_entry_signal_for_strategy(symbol: str, strategy_id: Optional[str], market_context: Optional[Dict[str, Any]] = None) -> dict:
    out = _resolve_strategy(strategy_id).evaluate(symbol, market_context=market_context)
    if isinstance(out, dict) and out.get("signal"):
        strategy = _resolve_strategy(strategy_id)
        out.setdefault("strategy_id", getattr(strategy, "strategy_id", STRATEGY_ID))
        out.setdefault("strategy_version", getattr(strategy, "strategy_version", STRATEGY_VERSION))
        out.setdefault("strategy_model", getattr(strategy, "strategy_model", STRATEGY_MODEL))
    return out


def get_trade_management_params(strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
    return get_trade_management_params_for_strategy(None, strength, score, atr_pct)


def get_entry_signal(symbol: str, market_context: Optional[Dict[str, Any]] = None, btc_context: Optional[Dict[str, Any]] = None) -> dict:
    ctx = market_context or _router()._build_context(symbol)
    btc = btc_context or _router()._get_btc_context("5m")
    def stamp(context):
        tf = (context.get('timeframes') or {}).get('5m') or {}
        if tf.get('status') != 'OK' or tf.get('stale') or not tf.get('context_ok', True):
            return None
        return tf.get('last_close_t') or tf.get('last_t')
    symbol_bar, btc_bar = stamp(ctx), stamp(btc)
    # A new wall-clock bucket forces refresh even if one upstream feed lags.
    # El mismo par/barra puede tener resultados distintos entre DEMO experimental
    # y LIVE; una decisión cacheada nunca debe filtrarse de un modo al otro.
    mode = "demo" if ctx.get("_execution_mode") == "demo" else "live"
    key = (mode, symbol_bar, btc_bar, int(time.time() // 300)) if symbol_bar and btc_bar else None
    out = _SIGNAL_CACHE.evaluate(str(symbol).upper(), key,
        lambda: _router().route_symbol(symbol, market_context=ctx, btc_context=btc))
    if isinstance(out, dict) and out.get("signal"):
        out.setdefault("strategy_id", STRATEGY_ID)
        out.setdefault("strategy_version", STRATEGY_VERSION)
        out.setdefault("strategy_model", STRATEGY_MODEL)
    return out


__all__ = [
    "get_entry_signal",
    "get_entry_signal_for_strategy",
    "get_trade_management_params",
    "get_trade_management_params_for_strategy",
    "STRATEGY_ID",
    "STRATEGY_MODEL",
    "STRATEGY_VERSION",
]
