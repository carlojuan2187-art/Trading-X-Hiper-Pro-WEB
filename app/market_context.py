from __future__ import annotations

import os
import math
import time
import threading
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from app.hyperliquid_client import make_request, norm_coin


TF_5M = "5m"
TF_15M = "15m"
TF_1H = "1h"


_MARKET_CONTEXT_CACHE_TTL_SECONDS = max(float(os.getenv("MARKET_CONTEXT_CACHE_TTL_SECONDS", "300.0") or 8.0), 0.0)
_MARKET_CONTEXT_REQUEST_TIMEOUT_SECONDS = max(float(os.getenv("MARKET_CONTEXT_REQUEST_TIMEOUT_SECONDS", "3.5") or 3.5), 1.0)
_MARKET_CONTEXT_REQUEST_RETRIES = max(int(os.getenv("MARKET_CONTEXT_REQUEST_RETRIES", "2") or 2), 1)
_MARKET_CONTEXT_REQUEST_BACKOFF = max(float(os.getenv("MARKET_CONTEXT_REQUEST_BACKOFF", "0.35") or 0.35), 0.0)
_CLOSED_CANDLE_GRACE_MS = max(int(os.getenv("CLOSED_CANDLE_GRACE_MS", "1500") or 1500), 0)
_market_context_cache_lock = threading.Lock()
_market_context_cache: Dict[tuple, tuple[float, Dict[str, Any]]] = {}


def _cache_get(key: tuple) -> Optional[Dict[str, Any]]:
    if _MARKET_CONTEXT_CACHE_TTL_SECONDS <= 0.0:
        return None
    now = time.time()
    with _market_context_cache_lock:
        payload = _market_context_cache.get(key)
        if not payload:
            return None
        ts, value = payload
        if (now - float(ts)) > float(_MARKET_CONTEXT_CACHE_TTL_SECONDS):
            _market_context_cache.pop(key, None)
            return None
        tf = (value.get('timeframes') or {}).get(key[2]) if key[0] == 'market' else value
        if isinstance(tf, dict):
            closed = float(tf.get('last_close_t') or 0.0) / 1000.0
            next_close = closed + interval_ms(str(key[2])) / 1000.0 + _CLOSED_CANDLE_GRACE_MS / 1000.0
            if not closed or now >= next_close:
                _market_context_cache.pop(key, None)
                return None
        return value


def _cache_set(key: tuple, value: Dict[str, Any]) -> None:
    if _MARKET_CONTEXT_CACHE_TTL_SECONDS <= 0.0:
        return
    if value.get('status') != 'OK' or not value.get('context_ok', True):
        return
    now = time.time()
    with _market_context_cache_lock:
        _market_context_cache[key] = (now, value)
        if len(_market_context_cache) > 256:
            stale = [
                k
                for k, (ts, _) in _market_context_cache.items()
                if (now - float(ts)) > float(_MARKET_CONTEXT_CACHE_TTL_SECONDS)
            ]
            for stale_key in stale[:128]:
                _market_context_cache.pop(stale_key, None)


def interval_ms(interval: str) -> int:
    return {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
    }.get(str(interval or "").strip(), 0)


def parse_candle(row: dict, *, interval: str = "") -> Optional[dict]:
    """Normaliza una vela de Hyperliquid y conserva su hora de cierre.

    `T` es el cierre reportado por el exchange. Si no viniera por compatibilidad,
    se infiere como t + intervalo; esto permite filtrar de forma determinista la
    vela aún abierta antes de calcular cualquier señal.
    """
    try:
        t = int(row.get("t", 0) or 0)
        close_t = int(row.get("T", 0) or 0)
        step = interval_ms(interval)
        if close_t <= 0 and t > 0 and step > 0:
            close_t = t + step
        parsed = {
            "t": t,
            "T": close_t,
            "o": float(row.get("o", 0)),
            "h": float(row.get("h", 0)),
            "l": float(row.get("l", 0)),
            "c": float(row.get("c", 0)),
            "v": float(row.get("v", 0)),
        }
        if not all(math.isfinite(parsed[k]) for k in ("o", "h", "l", "c", "v")):
            return None
        if (min(parsed[k] for k in ("o", "h", "l", "c")) <= 0 or parsed["v"] < 0
                or parsed["l"] > min(parsed["o"], parsed["c"])
                or parsed["h"] < max(parsed["o"], parsed["c"])
                or t <= 0 or close_t <= t):
            return None
        return parsed
    except Exception:
        return None


def _is_http_error_response(resp: Any) -> bool:
    return isinstance(resp, dict) and bool(resp.get("_http_error"))


def _format_http_error_response(resp: Any) -> str:
    if not isinstance(resp, dict):
        return "API_FAIL"
    status = resp.get("_http_status")
    body = str(resp.get("_http_body") or "")[:160]
    if status:
        if body:
            return f"HTTP_{status}:{body}"
        return f"HTTP_{status}"
    if body:
        return f"REQUEST_FAILED:{body}"
    return "API_FAIL"


def _closed_candles_only(candles: List[dict], interval: str, *, now_ms: Optional[int] = None) -> List[dict]:
    if not candles:
        return []
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    cutoff = now_ms - int(_CLOSED_CANDLE_GRACE_MS)
    step = interval_ms(interval)
    out: List[dict] = []
    for candle in candles:
        try:
            t = int(candle.get("t", 0) or 0)
            close_t = int(candle.get("T", 0) or 0)
            if close_t <= 0 and t > 0 and step > 0:
                close_t = t + step
                candle["T"] = close_t
            if close_t > 0 and close_t <= cutoff:
                out.append(candle)
        except Exception:
            continue
    return out


_raw_candle_cache = {}
_raw_cache_guard = threading.Lock()
_raw_fetch_locks = [threading.Lock() for _ in range(64)]


def fetch_candles(coin: str, interval: str, limit: int) -> Tuple[List[dict], str]:
    key = (norm_coin(coin), interval, int(limit))
    with _raw_fetch_locks[hash(key) % len(_raw_fetch_locks)]:
        now = time.time()
        with _raw_cache_guard:
            cached = _raw_candle_cache.get(key)
        if cached and now < cached[0]:
            return deepcopy(cached[1]), 'OK'
        candles, status = _fetch_candles_uncached(coin, interval, limit)
        if status == 'OK' and candles:
            close_t = float(candles[-1].get('T') or 0) / 1000
            expiry = min(now + _MARKET_CONTEXT_CACHE_TTL_SECONDS,
                         close_t + interval_ms(interval) / 1000 + _CLOSED_CANDLE_GRACE_MS / 1000)
            if expiry > now:
                with _raw_cache_guard:
                    if len(_raw_candle_cache) >= 512:
                        expired = [k for k, value in _raw_candle_cache.items() if value[0] <= now]
                        for k in expired:
                            _raw_candle_cache.pop(k, None)
                        if len(_raw_candle_cache) >= 512:
                            _raw_candle_cache.pop(min(_raw_candle_cache, key=lambda k: _raw_candle_cache[k][0]))
                    _raw_candle_cache[key] = (expiry, deepcopy(candles))
        return candles, status


def _fetch_candles_uncached(coin: str, interval: str, limit: int) -> Tuple[List[dict], str]:
    coin = norm_coin(coin)
    if not coin:
        return [], "BAD_SYMBOL"
    step = interval_ms(interval)
    if step <= 0:
        return [], "BAD_INTERVAL"

    now = int(time.time() * 1000)
    # Pedimos varias velas extra porque la última respuesta puede incluir la vela
    # abierta y luego será descartada por `_closed_candles_only`.
    requested = max(int(limit) + 3, 53)
    start = now - step * requested
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start,
            "endTime": now,
        },
    }

    last_error = "API_FAIL"
    responses = []

    def _attempt_request(*, retries: int, backoff: float, timeout: float):
        nonlocal last_error
        try:
            responses.append(
                make_request(
                    "/info",
                    payload,
                    retries=retries,
                    backoff=backoff,
                    timeout=timeout,
                )
            )
        except Exception as e:
            last_error = f"REQUEST_EXCEPTION:{type(e).__name__}:{str(e)[:120]}"

    _attempt_request(
        retries=_MARKET_CONTEXT_REQUEST_RETRIES,
        backoff=_MARKET_CONTEXT_REQUEST_BACKOFF,
        timeout=_MARKET_CONTEXT_REQUEST_TIMEOUT_SECONDS,
    )

    first_resp = responses[0] if responses else None
    first_http_500 = (
        _is_http_error_response(first_resp)
        and int(first_resp.get("_http_status") or 0) >= 500
        if isinstance(first_resp, dict)
        else False
    )

    if not isinstance(first_resp, list) or not first_resp:
        _attempt_request(
            retries=max(_MARKET_CONTEXT_REQUEST_RETRIES, 3),
            backoff=max(_MARKET_CONTEXT_REQUEST_BACKOFF, 0.6),
            timeout=max(_MARKET_CONTEXT_REQUEST_TIMEOUT_SECONDS, 6.0),
        )

    latest_resp = responses[-1] if responses else None
    latest_http_500 = (
        _is_http_error_response(latest_resp)
        and int(latest_resp.get("_http_status") or 0) >= 500
        if isinstance(latest_resp, dict)
        else False
    )
    if first_http_500 or latest_http_500:
        time.sleep(0.35)
        _attempt_request(
            retries=max(_MARKET_CONTEXT_REQUEST_RETRIES + 1, 4),
            backoff=max(_MARKET_CONTEXT_REQUEST_BACKOFF, 0.8),
            timeout=max(_MARKET_CONTEXT_REQUEST_TIMEOUT_SECONDS, 8.0),
        )

    resp = None
    saw_http_500 = False
    for candidate in responses:
        if isinstance(candidate, list) and candidate:
            resp = candidate
            break
        if _is_http_error_response(candidate):
            status = int(candidate.get("_http_status") or 0)
            if status >= 500:
                saw_http_500 = True
            last_error = _format_http_error_response(candidate)
        elif candidate == {} or candidate is None:
            last_error = "API_FAIL"
        elif not isinstance(candidate, list):
            last_error = f"BAD_RESPONSE_SCHEMA:{type(candidate).__name__}"
        else:
            last_error = "EMPTY"

    if not isinstance(resp, list) or not resp:
        if saw_http_500 and not str(last_error).startswith("HTTP_500"):
            last_error = f"TRANSIENT_HTTP_500:{last_error}"
        return [], last_error

    candles: List[dict] = []
    for raw in resp:
        if isinstance(raw, dict):
            item = parse_candle(raw, interval=interval)
            if item is None:
                return [], "INVALID_CANDLES"
            candles.append(item)

    if not candles:
        return [], "EMPTY"

    try:
        candles.sort(key=lambda x: int(x.get("t", 0)))
    except Exception:
        pass

    candles = _closed_candles_only(candles, interval, now_ms=now)
    if not candles:
        return [], "NO_CLOSED_CANDLES"

    if len(candles) > limit:
        candles = candles[-limit:]
    return candles, "OK"


def extract_series(candles: List[dict]) -> Tuple[List[float], List[float], List[float], List[float], List[float]]:
    o, h, l, c, v = [], [], [], [], []
    for x in candles:
        o.append(float(x["o"]))
        h.append(float(x["h"]))
        l.append(float(x["l"]))
        c.append(float(x["c"]))
        v.append(float(x["v"]))
    return o, h, l, c, v


def ema(series: List[float], period: int) -> List[float]:
    if not series:
        return []
    out = [float(series[0])]
    k = 2.0 / (float(period) + 1.0)
    for i in range(1, len(series)):
        out.append((float(series[i]) * k) + (out[-1] * (1.0 - k)))
    return out


def rma(series: List[float], period: int) -> List[float]:
    if not series:
        return []
    period = max(1, int(period))
    if len(series) < period:
        avg = sum(float(x) for x in series) / len(series)
        return [avg for _ in series]
    out = [0.0] * len(series)
    first = sum(float(x) for x in series[:period]) / period
    out[period - 1] = first
    for i in range(period, len(series)):
        out[i] = ((out[i - 1] * (period - 1)) + float(series[i])) / period
    for i in range(period - 1):
        out[i] = out[period - 1]
    return out


def adx(h: List[float], l: List[float], c: List[float], period: int) -> List[float]:
    if len(h) < period + 2 or len(l) < period + 2 or len(c) < period + 2:
        return []
    plus_dm, minus_dm, tr = [0.0], [0.0], [0.0]
    for i in range(1, len(c)):
        up = float(h[i]) - float(h[i - 1])
        down = float(l[i - 1]) - float(l[i])
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(float(h[i]) - float(l[i]), abs(float(h[i]) - float(c[i - 1])), abs(float(l[i]) - float(c[i - 1]))))
    atr_series = rma(tr, period)
    plus = [100.0 * (p / a) if a else 0.0 for p, a in zip(rma(plus_dm, period), atr_series)]
    minus = [100.0 * (m / a) if a else 0.0 for m, a in zip(rma(minus_dm, period), atr_series)]
    dx = [100.0 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(plus, minus)]
    return rma(dx, period)


def last(values: List[float]) -> Optional[float]:
    return values[-1] if values else None


def atr(h: List[float], l: List[float], c: List[float], period: int = 14) -> float:
    if len(h) < 2:
        return 0.0
    tr = [0.0]
    for i in range(1, len(c)):
        tr.append(max(float(h[i]) - float(l[i]), abs(float(h[i]) - float(c[i - 1])), abs(float(l[i]) - float(c[i - 1]))))
    return float(last(rma(tr, period)) or 0.0)


def pct_change(now: float, prev: float) -> float:
    if prev == 0:
        return 0.0
    return (now - prev) / prev


def is_stale(candles: List[dict], interval: str) -> Tuple[bool, float, int]:
    if not candles:
        return True, 9e9, 0
    last_t = int(candles[-1].get("t", 0) or 0)
    last_close_t = int(candles[-1].get("T", 0) or 0)
    if last_close_t <= 0:
        last_close_t = last_t + interval_ms(interval)
    age_s = max(0.0, (time.time() * 1000.0 - last_close_t) / 1000.0)
    interval_s = interval_ms(interval) / 1000.0
    return age_s > (interval_s * 3.0), age_s, last_t


def _minimum_candle_count(ema_periods: tuple[int, ...], adx_period: int, atr_period: int) -> int:
    values = [50, int(adx_period) + 2, int(atr_period) + 2]
    values.extend(int(x) + 5 for x in ema_periods)
    return max(values)


def build_timeframe_context(
    coin: str,
    interval: str,
    limit: int,
    ema_periods: tuple[int, ...] = (20, 50, 200),
    adx_period: int = 14,
    atr_period: int = 14,
) -> Dict[str, Any]:
    cache_key = (
        "tf",
        str(coin or "").upper(),
        str(interval or ""),
        int(limit),
        tuple(int(x) for x in ema_periods),
        int(adx_period),
        int(atr_period),
    )
    cached = _cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    candles, status = fetch_candles(coin, interval, limit)
    snapshot: Dict[str, Any] = {
        "interval": interval,
        "limit": int(limit),
        "status": status,
        "detail": status,
        "candles": candles,
        "stale": True,
        "age_s": 9e9,
        "last_t": 0,
        "last_close_t": 0,
        "closed_candles_only": True,
        "o": [],
        "h": [],
        "l": [],
        "c": [],
        "v": [],
        "close": 0.0,
        "atr": 0.0,
        "atr_pct": 0.0,
        "adx": 0.0,
        "context_ok": False,
    }
    if status != "OK" or not candles:
        _cache_set(cache_key, snapshot)
        return snapshot

    min_required = _minimum_candle_count(ema_periods, adx_period, atr_period)
    if len(candles) < min_required:
        snapshot["status"] = "INSUFFICIENT_CANDLES"
        snapshot["detail"] = f"INSUFFICIENT_CANDLES:{len(candles)}/{min_required}"
        snapshot["context_ok"] = False
        snapshot["candles"] = candles
        _cache_set(cache_key, snapshot)
        return snapshot

    stale, age_s, last_t = is_stale(candles, interval)
    o, h, l, c, v = extract_series(candles)
    close = float(c[-1]) if c else 0.0
    atr_value = float(atr(h, l, c, atr_period) or 0.0)
    adx_series = adx(h, l, c, adx_period)

    snapshot.update(
        {
            "stale": bool(stale),
            "age_s": float(age_s),
            "last_t": int(last_t),
            "last_close_t": int(candles[-1].get("T", 0) or 0),
            "detail": "STALE_CANDLES" if stale else "OK",
            "o": o,
            "h": h,
            "l": l,
            "c": c,
            "v": v,
            "close": close,
            "atr": atr_value,
            "atr_pct": (atr_value / close) if close > 0 else 0.0,
            "adx": float(last(adx_series) or 0.0),
            "adx_series": adx_series,
            "context_ok": not bool(stale),
        }
    )

    for period in ema_periods:
        snapshot[f"ema{int(period)}"] = ema(c, int(period))

    _cache_set(cache_key, snapshot)
    return snapshot


def build_market_context(
    symbol: str,
    interval: str = TF_5M,
    limit: int = 320,
    ema_periods: tuple[int, ...] = (20, 50, 200),
    adx_period: int = 14,
    atr_period: int = 14,
) -> Dict[str, Any]:
    cache_key = (
        "market",
        str(symbol or "").upper(),
        str(interval or ""),
        int(limit),
        tuple(int(x) for x in ema_periods),
        int(adx_period),
        int(atr_period),
    )
    cached = _cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    coin = norm_coin(symbol)
    if not coin:
        return {
            "symbol": symbol,
            "coin": "",
            "status": "BAD_SYMBOL",
            "detail": "BAD_SYMBOL",
            "context_ok": False,
            "timeframes": {},
        }

    tf_ctx = build_timeframe_context(
        coin=coin,
        interval=interval,
        limit=limit,
        ema_periods=ema_periods,
        adx_period=adx_period,
        atr_period=atr_period,
    )
    out = {
        "symbol": symbol,
        "coin": coin,
        "status": tf_ctx.get("status", "UNKNOWN"),
        "detail": tf_ctx.get("detail", tf_ctx.get("status", "UNKNOWN")),
        "context_ok": bool(tf_ctx.get("context_ok", False)),
        "closed_candles_only": True,
        "timeframes": {interval: tf_ctx},
    }
    _cache_set(cache_key, out)
    return out


__all__ = [
    "TF_5M",
    "TF_15M",
    "TF_1H",
    "adx",
    "atr",
    "build_market_context",
    "build_timeframe_context",
    "ema",
    "extract_series",
    "fetch_candles",
    "interval_ms",
    "is_stale",
    "last",
    "parse_candle",
    "pct_change",
    "rma",
]
