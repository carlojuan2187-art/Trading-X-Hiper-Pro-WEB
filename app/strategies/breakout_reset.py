import os
import math
import time
from app.settings_utils import env_float, env_int, env_bool
from app.symbol_policy import ALLOWED_SYMBOLS, BLOCKED_MEME_KEYWORDS
from app.trading_quality import meets_min_rr
from app.pending_setup import find_pending_reset, find_m5_continuation
from typing import Dict, Any, List, Optional, Tuple

from app.strategies.base import BaseStrategy
from app.market_context import build_market_context, fetch_candles as fetch_closed_candles

TF_5M = "5m"
TF_15M = "15m"
TF_1H = "1h"
LOOKBACK_5M = 320
LOOKBACK_15M = 240
LOOKBACK_1H = 240

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
ADX_PERIOD = 14
ATR_PERIOD = 14
EMA_SLOPE_LOOKBACK = 6

MIN_CANDLES_REQUIRED = 260
MIN_NONZERO_VOLUME_RATIO = 0.92

# MTF simple pura: sesgo 1H + confirmación 15M + reset/continuación 5M.
H1_ADX_MIN = env_float("MTF_H1_ADX_MIN", 12.0)
M15_ADX_MIN = env_float("MTF_M15_ADX_MIN", 11.0)
M5_ADX_MIN = env_float("MTF_M5_ADX_MIN", 9.5)
ATR_PCT_MIN = env_float("MTF_ATR_PCT_MIN", 0.00075)
ATR_PCT_MAX = env_float("MTF_ATR_PCT_MAX", 0.018)
TREND_STACK_MIN_PCT = 0.00030
RESET_LOOKBACK_BARS = env_int("MTF_RESET_LOOKBACK_BARS", 5, 1)
MTF_PENDING_SETUP_ENABLED = env_bool("MTF_PENDING_SETUP_ENABLED", True)
# LAB isolation: identical strategy decisions to LIVE by default. These
# experimental parameters are applied only to DEMO after explicit opt-in.
DEMO_STRATEGY_EXPERIMENTAL = env_bool("DEMO_STRATEGY_EXPERIMENTAL", False)
# H1-only DEMO experiment: does not opt into M15 or M5 trial rules.
DEMO_H1_EARLY_REVERSAL_ENABLED = env_bool("DEMO_H1_EARLY_REVERSAL_ENABLED", False)
# Independent DEMO-only M15 early trend confirmation; LIVE remains strict.
DEMO_M15_EARLY_CONFIRMATION_ENABLED = env_bool("DEMO_M15_EARLY_CONFIRMATION_ENABLED", False)
# DEMO only: second 5m entry path after a failed conventional pending reset.
# Does not relax H1/M15, reuse consumed resets, or change LIVE.
DEMO_M5_EXPANDED_ENTRIES_ENABLED = env_bool("DEMO_M5_EXPANDED_ENTRIES_ENABLED", True)
DEMO_MTF_RESET_TOUCH_TOL_ATR = env_float("DEMO_MTF_RESET_TOUCH_TOL_ATR", 0.58, minimum=0.0)
DEMO_MTF_CONTINUATION_CONFIRM_TOL_ATR = env_float(
    "DEMO_MTF_CONTINUATION_CONFIRM_TOL_ATR", 0.32, minimum=0.0)
MTF_SETUP_MAX_AGE_BARS = env_int("MTF_SETUP_MAX_AGE_BARS", 8, 1)
MTF_H1_PULLBACK_ENABLED = env_bool("MTF_H1_PULLBACK_ENABLED", True)
MTF_H1_PULLBACK_MAX_ATR = env_float("MTF_H1_PULLBACK_MAX_ATR", 0.50, maximum=1.0)
MTF_H1_TRANSITION_ENABLED = env_bool("MTF_H1_TRANSITION_ENABLED", True)
MTF_H1_TRANSITION_ADX_MIN = env_float("MTF_H1_TRANSITION_ADX_MIN", 16.0)
MTF_H1_REQUIRE_EMA200 = env_bool("MTF_H1_REQUIRE_EMA200", True)
MTF_M15_PULLBACK_ENABLED = env_bool("MTF_M15_PULLBACK_ENABLED", True)
MTF_M15_PULLBACK_MAX_ATR = env_float("MTF_M15_PULLBACK_MAX_ATR", 0.75, minimum=0.0, maximum=1.0)
MTF_M15_REQUIRE_EMA200 = env_bool("MTF_M15_REQUIRE_EMA200", True)
ENTRY_RR_RESERVE = env_float("ENTRY_RR_RESERVE", 0.05, maximum=0.50)
RESET_TOUCH_TOL_ATR = 0.38
RESET_BREAK_TOL_ATR = 0.48
TRIGGER_MAX_EMA20_EXTENSION_ATR = env_float("MTF_TRIGGER_MAX_EMA20_EXTENSION_ATR", 0.95)
CONTINUATION_CONFIRM_TOL_ATR = 0.10
MTF_SL_MIN_PCT = env_float("MTF_SL_MIN_PCT", 0.0045)
MTF_SL_MAX_PCT = 0.0068  # legado/soft reference; ya no recorta la invalidación estructural
MTF_SL_HARD_MAX_PCT = max(float(os.getenv("MTF_SL_HARD_MAX_PCT", "0.0140") or 0.0140), MTF_SL_MIN_PCT)
MTF_SL_ATR_MULT = 0.88
MTF_SL_BUFFER_ATR = 0.10
MTF_TP_MIN_PCT = 0.0060
MTF_TP_MAX_PCT = 0.0180
MTF_RR_MIN = max(env_float("MTF_RR_MIN", 1.15), env_float("MIN_ENTRY_RR", 1.15))
MTF_RR_MAX = 1.34
MIN_RR_TO_SIGNAL = max(MTF_RR_MIN, env_float("MIN_ENTRY_RR", 1.15))
STRUCTURAL_TARGET_LOOKBACK = max(int(os.getenv("MTF_STRUCTURAL_TARGET_LOOKBACK", "96") or 96), 24)
STRUCTURAL_TARGET_BUFFER = max(0.50, min(float(os.getenv("MTF_STRUCTURAL_TARGET_BUFFER", "0.88") or 0.88), 0.98))
MAX_SCORE = 100.0
MIN_SCORE_TO_SIGNAL = env_float("MTF_MIN_SCORE_TO_SIGNAL", 69.0)
STRENGTH_MIN = 0.20
STRENGTH_MAX = 0.97
LOG_SIGNAL_DIAGNOSTICS = True


def _log(msg: str):
    try:
        print(f"[STRATEGY {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}] {msg}")
    except Exception:
        pass


def _interval_ms(interval: str) -> int:
    return {"5m": 300_000, "15m": 900_000, "1h": 3_600_000}.get(interval, 0)


def _parse_candle(x: dict) -> Optional[dict]:
    try:
        return {
            "t": int(x.get("t", 0)),
            "o": float(x.get("o", 0)),
            "h": float(x.get("h", 0)),
            "l": float(x.get("l", 0)),
            "c": float(x.get("c", 0)),
            "v": float(x.get("v", 0)),
        }
    except Exception:
        return None


def _fetch_candles(coin: str, interval: str, limit: int):
    # Fuente única de velas: market_context descarta la vela aún abierta y
    # conserva `T` (timestamp de cierre). Esto aplica también a 15m/1h.
    return fetch_closed_candles(coin, interval, limit)


def _extract(candles):
    o, h, l, c, v = [], [], [], [], []
    for x in candles:
        o.append(float(x["o"]))
        h.append(float(x["h"]))
        l.append(float(x["l"]))
        c.append(float(x["c"]))
        v.append(float(x["v"]))
    return o, h, l, c, v


def _ema(series, period):
    if not series:
        return []
    out = [float(series[0])]
    k = 2.0 / (float(period) + 1.0)
    for i in range(1, len(series)):
        out.append((float(series[i]) * k) + (out[-1] * (1.0 - k)))
    return out


def _rma(series, period):
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


def _adx(h, l, c, period):
    if len(h) < period + 2 or len(l) < period + 2 or len(c) < period + 2:
        return []
    plus_dm, minus_dm, tr = [0.0], [0.0], [0.0]
    for i in range(1, len(c)):
        up = float(h[i]) - float(h[i - 1])
        down = float(l[i - 1]) - float(l[i])
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(float(h[i]) - float(l[i]), abs(float(h[i]) - float(c[i - 1])), abs(float(l[i]) - float(c[i - 1]))))
    atr = _rma(tr, period)
    plus = [100.0 * (p / a) if a else 0.0 for p, a in zip(_rma(plus_dm, period), atr)]
    minus = [100.0 * (m / a) if a else 0.0 for m, a in zip(_rma(minus_dm, period), atr)]
    dx = [100.0 * abs(p - m) / (p + m) if (p + m) else 0.0 for p, m in zip(plus, minus)]
    return _rma(dx, period)


def _last(x):
    return x[-1] if x else None


def _atr(h, l, c, period=14):
    if len(h) < 2:
        return 0.0
    tr = [0.0]
    for i in range(1, len(c)):
        tr.append(max(float(h[i]) - float(l[i]), abs(float(h[i]) - float(c[i - 1])), abs(float(l[i]) - float(c[i - 1]))))
    return float(_last(_rma(tr, period)) or 0.0)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _pct_change(now: float, prev: float) -> float:
    if prev == 0:
        return 0.0
    return (now - prev) / prev


def _median(values: List[float]) -> float:
    vals = sorted(float(x) for x in values if x is not None)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


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


def _close_position_in_range(o: float, h: float, l: float, c: float) -> float:
    rng = max(h - l, 1e-12)
    return _clamp((c - l) / rng, 0.0, 1.0)


def _body_ratio(o: float, h: float, l: float, c: float) -> float:
    rng = max(h - l, 1e-12)
    return abs(c - o) / rng


def _is_stale(candles: List[dict], interval: str) -> Tuple[bool, float, int]:
    if not candles:
        return True, 9e9, 0
    last_t = int(candles[-1].get("t", 0) or 0)
    close_t = int(candles[-1].get("T", 0) or 0)
    if close_t <= 0:
        close_t = last_t + _interval_ms(interval)
    age_s = max(0.0, (time.time() * 1000.0 - close_t) / 1000.0)
    interval_s = _interval_ms(interval) / 1000.0
    return age_s > (interval_s * 3.0), age_s, last_t


def _base_coin(symbol: str) -> str:
    s = str(symbol or "").upper()
    for suffix in ("-PERP", "-USDC", "-USD", "-USDT"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _is_probable_meme_symbol(symbol: str) -> bool:
    base = _base_coin(symbol)
    if not base:
        return False
    return any(key in base for key in BLOCKED_MEME_KEYWORDS)


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


def _volatility_regime_from_atr_pct(atr_pct: float) -> str:
    if atr_pct <= 0.0030:
        return "low"
    if atr_pct <= 0.0075:
        return "normal"
    if atr_pct <= 0.0120:
        return "high"
    return "extreme"


def _tf_snapshot_from_candles(candles: List[dict], interval: str) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "interval": interval,
        "status": "EMPTY",
        "detail": "EMPTY",
        "context_ok": False,
        "candles": candles,
        "stale": True,
        "age_s": 9e9,
        "last_t": 0,
        "o": [], "h": [], "l": [], "c": [], "v": [],
        "close": 0.0,
        "atr": 0.0,
        "atr_pct": 0.0,
        "adx": 0.0,
    }
    if not candles:
        return snapshot
    stale, age_s, last_t = _is_stale(candles, interval)
    o, h, l, c, v = _extract(candles)
    close = float(c[-1]) if c else 0.0
    atr_value = float(_atr(h, l, c, ATR_PERIOD) or 0.0)
    adx_series = _adx(h, l, c, ADX_PERIOD)
    snapshot.update({
        "status": "STALE_CANDLES" if stale else "OK",
        "detail": "STALE_CANDLES" if stale else "OK",
        "context_ok": not bool(stale),
        "stale": bool(stale),
        "age_s": float(age_s),
        "last_t": int(last_t),
        "o": o, "h": h, "l": l, "c": c, "v": v,
        "close": close,
        "atr": atr_value,
        "atr_pct": (atr_value / close) if close > 0 else 0.0,
        "adx": float(_last(adx_series) or 0.0),
        "adx_series": adx_series,
        f"ema{EMA_FAST}": _ema(c, EMA_FAST),
        f"ema{EMA_MID}": _ema(c, EMA_MID),
        f"ema{EMA_SLOW}": _ema(c, EMA_SLOW),
    })
    return snapshot


def _resolve_tf_snapshot(symbol: str, interval: str, limit: int, provided_market_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if interval == TF_5M and isinstance(provided_market_context, dict):
        tf_ctx = ((provided_market_context.get("timeframes") or {}).get(TF_5M) if isinstance(provided_market_context.get("timeframes"), dict) else None)
        if isinstance(tf_ctx, dict) and tf_ctx.get("candles"):
            return tf_ctx
    if interval == TF_5M:
        ctx = build_market_context(
            symbol=symbol,
            interval=TF_5M,
            limit=LOOKBACK_5M,
            ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
            adx_period=ADX_PERIOD,
            atr_period=ATR_PERIOD,
        )
        tf_ctx = ((ctx.get("timeframes") or {}).get(TF_5M) if isinstance(ctx.get("timeframes"), dict) else None)
        if isinstance(tf_ctx, dict):
            return tf_ctx
    candles, status = _fetch_candles(symbol, interval, limit)
    out = _tf_snapshot_from_candles(candles, interval)
    if status == "OK" and len(candles) < EMA_SLOW:
        status = "INSUFFICIENT_HIGHER_TF_HISTORY"
    if status != "OK":
        out["status"] = status
        out["detail"] = status
        out["context_ok"] = False
    return out


def _bias_from_tf(tf_ctx: Dict[str, Any], *, adx_min: float, require_close_on_fast: bool = True, require_slow_stack: bool = True) -> Tuple[str, Dict[str, Any]]:
    c = tf_ctx.get("c") or []
    ema20 = tf_ctx.get(f"ema{EMA_FAST}") or []
    ema50 = tf_ctx.get(f"ema{EMA_MID}") or []
    ema200 = tf_ctx.get(f"ema{EMA_SLOW}") or []
    if not c or not ema20 or not ema50 or not ema200:
        return "none", {"reason": "NO_TF_DATA"}
    close = float(c[-1])
    adx_val = float(tf_ctx.get("adx") or 0.0)
    atr_pct = float(tf_ctx.get("atr_pct") or 0.0)
    slope20 = _pct_change(float(ema20[-1]), float(ema20[max(0, len(ema20) - 1 - EMA_SLOPE_LOOKBACK)] or ema20[-1]))
    slope50 = _pct_change(float(ema50[-1]), float(ema50[max(0, len(ema50) - 1 - EMA_SLOPE_LOOKBACK)] or ema50[-1]))
    stack_spread = abs(float(ema20[-1]) - float(ema50[-1])) / max(close, 1e-12)
    # Same entry predicates as before, named to expose the exact failed gates.
    common_checks = {"ADX": adx_val >= adx_min,
                     "EMA_SEPARATION": stack_spread >= TREND_STACK_MIN_PCT}
    long_checks = {
        **common_checks,
        "PRICE_ABOVE_EMA20": close > ema20[-1],
        "EMA20_ABOVE_EMA50": ema20[-1] > ema50[-1],
        "EMA50_ABOVE_EMA200": not require_slow_stack or ema50[-1] > ema200[-1],
        "EMA20_SLOPE_UP": slope20 > 0.0,
        "EMA50_SLOPE_UP": slope50 >= -0.00010,
    }
    short_checks = {
        **common_checks,
        "PRICE_BELOW_EMA20": close < ema20[-1],
        "EMA20_BELOW_EMA50": ema20[-1] < ema50[-1],
        "EMA50_BELOW_EMA200": not require_slow_stack or ema50[-1] < ema200[-1],
        "EMA20_SLOPE_DOWN": slope20 < 0.0,
        "EMA50_SLOPE_DOWN": slope50 <= 0.00010,
    }
    # require_close_on_fast was already implied by the strict price comparisons.
    long_bias = all(long_checks.values())
    short_bias = all(short_checks.values())
    direction = "long" if long_bias else "short" if short_bias else "none"
    return direction, {
        "failed_long": [key for key, passed in long_checks.items() if not passed],
        "failed_short": [key for key, passed in short_checks.items() if not passed],
        "adx_min": adx_min,
        "stack_min": TREND_STACK_MIN_PCT,
        "require_slow_stack": require_slow_stack,
        "adx": round(adx_val, 2),
        "atr_pct": round(atr_pct, 6),
        "slope20": round(slope20, 6),
        "slope50": round(slope50, 6),
        "stack_spread": round(stack_spread, 6),
        "close": round(close, 8),
        "ema20": round(float(ema20[-1]), 8),
        "ema50": round(float(ema50[-1]), 8),
        "ema200": round(float(ema200[-1]), 8),
    }


def _h1_bias(tf_ctx: Dict[str, Any], *, demo_trial: bool = False) -> Tuple[str, Dict[str, Any]]:
    direction, diag = _bias_from_tf(tf_ctx, adx_min=H1_ADX_MIN,
                                   require_slow_stack=MTF_H1_REQUIRE_EMA200)
    diag["h1_mode"] = "strict" if MTF_H1_REQUIRE_EMA200 else "without_ema200"
    if direction == "none" and MTF_H1_PULLBACK_ENABLED:
        for candidate, sign in (("long", 1), ("short", -1)):
            permitted = ({"PRICE_ABOVE_EMA20", "EMA20_SLOPE_UP"} if sign == 1
                         else {"PRICE_BELOW_EMA20", "EMA20_SLOPE_DOWN"})
            failed = set(diag.get("failed_" + candidate) or [])
            atr = float(tf_ctx.get("atr") or 0)
            close = float((tf_ctx.get("c") or [0])[-1])
            fast = float((tf_ctx.get("ema20") or [0])[-1])
            mid = float((tf_ctx.get("ema50") or [0])[-1])
            if (failed and failed.issubset(permitted)
                    and all(math.isfinite(x) for x in (atr, close, fast, mid)) and atr > 0
                    and sign * float(diag.get("slope50") or 0) > 0
                    and sign * (close-mid) > 0
                    and max(0.0, -sign * (close-fast)) <= atr * MTF_H1_PULLBACK_MAX_ATR):
                diag["h1_mode"] = "bounded_pullback"
                diag["pullback_distance_atr"] = max(0.0, -sign * (close-fast)) / atr
                return candidate, diag
    if direction != "none" or not MTF_H1_TRANSITION_ENABLED or not MTF_H1_REQUIRE_EMA200:
        return direction, diag
    # Only the slow EMA veto may be bypassed. Every other original gate remains.
    candidate = ("long" if diag.get("failed_long") == ["EMA50_ABOVE_EMA200"] else
                 "short" if diag.get("failed_short") == ["EMA50_BELOW_EMA200"] else "none")
    if candidate == "none":
        return direction, diag
    c, e20, e50 = (tf_ctx.get(key) or [] for key in ("c", "ema20", "ema50"))
    if min(len(c), len(e20), len(e50)) < 2:
        diag["transition_rejected"] = "INSUFFICIENT_CONFIRMATION"
        return direction, diag
    sign = 1 if candidate == "long" else -1
    checks = {
        "ADX": float(tf_ctx.get("adx") or 0) >= max(H1_ADX_MIN, MTF_H1_TRANSITION_ADX_MIN),
        "SLOPES": sign * float(diag["slope20"]) > 0 and sign * float(diag["slope50"]) > 0,
        "TWO_CLOSED_BARS": all(sign * (float(c[i])-float(e20[i])) > 0 and
                               sign * (float(e20[i])-float(e50[i])) > 0 for i in (-2, -1)),
        "RECENT_SLOPES": sign * (float(e20[-1])-float(e20[-2])) > 0 and
                         sign * (float(e50[-1])-float(e50[-2])) > 0,
    }
    diag["transition_failed"] = [key for key, value in checks.items() if not value]
    if all(checks.values()):
        diag["h1_mode"] = "confirmed_transition"
        return candidate, diag
    # Sólo para DEMO: anticipar UN cierre de vela cuando exactamente EMA200
    # impide entrar. Mantener ADX, las pendientes en dirección de la señal,
    # el alineamiento EMA20/EMA50 y la pendiente reciente de ambos indicadores.
    # No aceptar giros sin fuerza ni saltarse el cierre de la vela de 1h.
    if (demo_trial and checks["ADX"] and checks["SLOPES"]
            and checks["RECENT_SLOPES"]
            and sign * (float(c[-1]) - float(e20[-1])) > 0
            and sign * (float(e20[-1]) - float(e50[-1])) > 0):
        diag["h1_mode"] = "demo_early_transition"
        diag["transition_confirmation_bars"] = 1
        return candidate, diag
    return direction, diag


def _demo_h1_early_reversal(tf_ctx: Dict[str, Any], diag: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """DEMO only: accept an early 1H price recovery, never modify LIVE or M15.

    Required: two closed H1 closes beyond EMA20, current close also beyond
    EMA50, a rising/falling EMA20 over both 1 and 6 closed H1 candles,
    and ADX >= 14. EMA50/EMA200 stacking is deliberately observational.
    """
    out = dict(diag)
    c, e20, e50 = ((tf_ctx.get(key) or []) for key in ("c", "ema20", "ema50"))
    if min(len(c), len(e20), len(e50)) < EMA_SLOPE_LOOKBACK + 1:
        out["demo_h1_reject"] = "INSUFFICIENT_CLOSED_BARS"
        return "none", out
    try:
        closes = [float(c[i]) for i in (-2, -1)]
        fast = [float(e20[i]) for i in (-2, -1, -1 - EMA_SLOPE_LOOKBACK)]
        mid = float(e50[-1])
        adx = float(tf_ctx.get("adx") or 0.0)
    except (ValueError, TypeError, OverflowError, IndexError):
        out["demo_h1_reject"] = "INVALID_INDICATORS"
        return "none", out
    if not all(math.isfinite(x) and x > 0 for x in closes + fast + [mid, adx]):
        out["demo_h1_reject"] = "INVALID_INDICATORS"
        return "none", out
    if adx < max(H1_ADX_MIN, 14.0):
        out["demo_h1_reject"] = "ADX_TOO_LOW"
        return "none", out

    previous_close, latest_close = closes
    previous_fast, latest_fast, six_bar_fast = fast
    long_ok = (previous_close > previous_fast and latest_close > max(latest_fast, mid)
               and latest_fast > previous_fast and latest_fast > six_bar_fast)
    short_ok = (previous_close < previous_fast and latest_close < min(latest_fast, mid)
                and latest_fast < previous_fast and latest_fast < six_bar_fast)
    if long_ok == short_ok:
        out["demo_h1_reject"] = "NO_CONFIRMED_EARLY_REVERSAL"
        return "none", out
    out["h1_mode"] = "demo_early_reversal"
    out["demo_h1_early_reversal_bars"] = 2
    out["demo_h1_ema200_bypassed"] = True
    return ("long" if long_ok else "short"), out


def _m15_confirmation(tf_ctx: Dict[str, Any], direction: str, *, demo_trial: bool = False) -> Tuple[str, Dict[str, Any]]:
    bias, diag = _bias_from_tf(tf_ctx, adx_min=M15_ADX_MIN,
                             require_slow_stack=MTF_M15_REQUIRE_EMA200)
    diag["m15_mode"] = "strict"
    if bias == "none" and demo_trial and direction in ("long", "short"):
        # Sólo EMA200 puede fallar. El resto (incluidos ADX, pendientes,
        # precio y alineación de medias rápidas) debe haber pasado ya.
        slow_check = "EMA50_ABOVE_EMA200" if direction == "long" else "EMA50_BELOW_EMA200"
        if diag.get("failed_" + direction) == [slow_check]:
            sign = 1 if direction == "long" else -1
            c = tf_ctx.get("c") or []
            e20 = tf_ctx.get("ema20") or []
            e50 = tf_ctx.get("ema50") or []
            # Dos velas M15 cerradas: evita aprobar cruces fugaces.
            if (float(tf_ctx.get("adx") or 0) >= max(M15_ADX_MIN, 14.0)
                    and min(len(c), len(e20), len(e50)) >= 2
                    and all(sign * (float(c[i]) - float(e20[i])) > 0 and
                            sign * (float(e20[i]) - float(e50[i])) > 0
                            for i in (-2, -1))):
                diag["m15_mode"] = "demo_fast_trend_confirmed"
                return direction, diag
            diag["demo_transition_rejected"] = "WEAK_OR_UNCONFIRMED_M15"
    if bias != "none" or direction not in ("long", "short") or not MTF_M15_PULLBACK_ENABLED:
        return bias, diag
    # A pullback can turn the fast EMA before it breaks the medium-term structure.
    # Only fast-price/fast-slope failures may be deferred to the 5m entry trigger.
    permitted = ({"PRICE_ABOVE_EMA20", "EMA20_SLOPE_UP"} if direction == "long"
                 else {"PRICE_BELOW_EMA20", "EMA20_SLOPE_DOWN"})
    failed = set(diag.get("failed_" + direction) or [])
    if not failed or not failed.issubset(permitted):
        return bias, diag
    close = float((tf_ctx.get("c") or [0])[-1])
    fast = float((tf_ctx.get("ema20") or [0])[-1])
    mid = float((tf_ctx.get("ema50") or [0])[-1])
    atr = float(tf_ctx.get("atr") or 0)
    if not all(math.isfinite(value) for value in (close, fast, mid, atr)) or atr <= 0:
        diag["pullback_rejected"] = "INVALID_ATR_OR_PRICE"
        return bias, diag
    sign = 1 if direction == "long" else -1
    distance = max(0.0, -sign * (close-fast)) / atr
    diag["pullback_distance_atr"] = distance
    if sign * (close-mid) <= 0:
        diag["pullback_rejected"] = "EMA50_STRUCTURE_BROKEN"
    elif distance > MTF_M15_PULLBACK_MAX_ATR:
        diag["pullback_rejected"] = "PULLBACK_TOO_DEEP"
    else:
        diag["m15_mode"] = "bounded_pullback"
        return direction, diag
    return bias, diag


def _detect_simple_mtf_trigger(
    *,
    demo_trial: bool = False,
    demo_m5_expanded: bool = False,
    direction: str,
    o: List[float],
    h: List[float],
    l: List[float],
    c: List[float],
    v: List[float],
    ema20: List[float],
    ema50: List[float],
    ema200: List[float],
    atr: float,
) -> Tuple[bool, str, Dict[str, Any]]:
    if MTF_PENDING_SETUP_ENABLED:
        primary_ok, primary_reason, primary_diag = find_pending_reset(
            direction=direction, o=o, h=h, l=l, c=c,
            ema20=ema20, ema50=ema50, atr=atr, max_age=MTF_SETUP_MAX_AGE_BARS,
            # Original LIVE/DEMO pending-reset lifecycle is preserved.
            touch_atr=(DEMO_MTF_RESET_TOUCH_TOL_ATR if demo_trial else RESET_TOUCH_TOL_ATR),
            break_atr=RESET_BREAK_TOL_ATR,
            confirm_atr=(DEMO_MTF_CONTINUATION_CONFIRM_TOL_ATR if demo_trial else CONTINUATION_CONFIRM_TOL_ATR),
            max_extension=TRIGGER_MAX_EMA20_EXTENSION_ATR)
        # Only fresh alternative patterns; NEVER rescue an already-consumed
        # or overextended setup, or a LIVE rejection.
        if (not primary_ok and demo_m5_expanded
                and primary_reason in {'NO_VALID_PENDING_RESET', 'PENDING_CONTINUATION_CONFIRM'}):
            alt_ok, alt_reason, alt_diag = find_m5_continuation(
                direction=direction, o=o, h=h, l=l, c=c, ema20=ema20,
                atr=atr, max_extension=TRIGGER_MAX_EMA20_EXTENSION_ATR)
            if alt_ok:
                alt_diag['primary_m5_reason'] = primary_reason
                return True, alt_reason, alt_diag
            primary_diag = dict(primary_diag)
            primary_diag['demo_m5_alt_reason'] = alt_reason
            primary_diag['demo_m5_alt_diag'] = alt_diag
        return primary_ok, primary_reason, primary_diag
    if len(c) < max(EMA_SLOW + 5, 80):
        return False, "NOT_ENOUGH_BARS", {}

    i = len(c) - 1
    recent_idx = list(range(max(0, i - RESET_LOOKBACK_BARS), i))
    if not recent_idx:
        return False, "NO_RESET_WINDOW", {}

    reset_low = min(l[j] for j in recent_idx)
    reset_high = max(h[j] for j in recent_idx)
    min_ema20 = min(float(ema20[j]) for j in recent_idx)
    max_ema20 = max(float(ema20[j]) for j in recent_idx)
    min_ema50 = min(float(ema50[j]) for j in recent_idx)
    max_ema50 = max(float(ema50[j]) for j in recent_idx)
    extension_atr = abs(float(c[i]) - float(ema20[i])) / max(atr, 1e-12)
    prev_high = float(h[i - 1]) if i >= 1 else float(h[i])
    prev_low = float(l[i - 1]) if i >= 1 else float(l[i])
    touches = [j for j in recent_idx if
        (l[j] <= ema20[j] + atr * RESET_TOUCH_TOL_ATR if direction == 'long'
         else h[j] >= ema20[j] - atr * RESET_TOUCH_TOL_ATR)]
    bars_since_reset = int(i - touches[-1]) if touches else 0

    confirm_tol = atr * CONTINUATION_CONFIRM_TOL_ATR

    if direction == "long":
        reset_touched = reset_low <= (max_ema20 + atr * RESET_TOUCH_TOL_ATR)
        reset_not_broken = reset_low >= (min_ema50 - atr * RESET_BREAK_TOL_ATR)
        reclaim_ok = (
            float(c[i]) > float(ema20[i])
            and float(c[i]) > float(o[i])
            and (
                float(c[i]) >= (prev_high - confirm_tol)
                or float(h[i]) >= prev_high
            )
        )
        diag = {
            "reset_low": round(float(reset_low), 8),
            "ema20_ref": round(float(max_ema20), 8),
            "ema50_ref": round(float(min_ema50), 8),
            "extension_atr": round(float(extension_atr), 4),
            "bars_since_reset": bars_since_reset,
            "confirm_close": round(float(c[i]), 8),
            "confirm_open": float(o[i]),
            "confirm_high": float(h[i]),
            "confirm_low": float(l[i]),
            "confirm_ema20": float(ema20[i]),
            "atr": float(atr),
            "reset_touched": bool(reset_touched),
            "reset_not_broken": bool(reset_not_broken),
            "reclaim_ok": bool(reclaim_ok),
            "max_extension_atr": TRIGGER_MAX_EMA20_EXTENSION_ATR,
            "prev_high": round(float(prev_high), 8),
            "confirm_tol_atr": round(float(CONTINUATION_CONFIRM_TOL_ATR), 4),
            "confirm_level": round(float(prev_high - confirm_tol), 8),
        }
        if not reset_touched:
            return False, "NO_5M_RESET_TOUCH", diag
        if not reset_not_broken:
            return False, "RESET_TOO_DEEP", diag
        if not reclaim_ok:
            return False, "NO_5M_CONTINUATION_CONFIRM", diag
        if extension_atr > TRIGGER_MAX_EMA20_EXTENSION_ATR:
            return False, "TOO_EXTENDED_AFTER_CONFIRM", diag
        return True, "OK", diag

    reset_touched = reset_high >= (min_ema20 - atr * RESET_TOUCH_TOL_ATR)
    reset_not_broken = reset_high <= (max_ema50 + atr * RESET_BREAK_TOL_ATR)
    reclaim_ok = (
        float(c[i]) < float(ema20[i])
        and float(c[i]) < float(o[i])
        and (
            float(c[i]) <= (prev_low + confirm_tol)
            or float(l[i]) <= prev_low
        )
    )
    diag = {
        "reset_high": round(float(reset_high), 8),
        "ema20_ref": round(float(min_ema20), 8),
        "ema50_ref": round(float(max_ema50), 8),
        "extension_atr": round(float(extension_atr), 4),
        "bars_since_reset": bars_since_reset,
        "confirm_close": round(float(c[i]), 8),
        "confirm_open": float(o[i]),
        "confirm_high": float(h[i]),
        "confirm_low": float(l[i]),
        "confirm_ema20": float(ema20[i]),
        "atr": float(atr),
        "reset_touched": bool(reset_touched),
        "reset_not_broken": bool(reset_not_broken),
        "reclaim_ok": bool(reclaim_ok),
        "max_extension_atr": TRIGGER_MAX_EMA20_EXTENSION_ATR,
        "prev_low": round(float(prev_low), 8),
        "confirm_tol_atr": round(float(CONTINUATION_CONFIRM_TOL_ATR), 4),
        "confirm_level": round(float(prev_low + confirm_tol), 8),
    }
    if not reset_touched:
        return False, "NO_5M_RESET_TOUCH", diag
    if not reset_not_broken:
        return False, "RESET_TOO_DEEP", diag
    if not reclaim_ok:
        return False, "NO_5M_CONTINUATION_CONFIRM", diag
    if extension_atr > TRIGGER_MAX_EMA20_EXTENSION_ATR:
        return False, "TOO_EXTENDED_AFTER_CONFIRM", diag
    return True, "OK", diag


def _nearest_structural_target(
    *,
    direction: str,
    highs: List[float],
    lows: List[float],
    closes: List[float],
    atr: float,
) -> float:
    if not closes or not highs or not lows:
        return 0.0
    close = float(closes[-1])
    n = min(len(closes), len(highs), len(lows))
    if n < 12 or close <= 0:
        return 0.0
    start = max(2, n - int(STRUCTURAL_TARGET_LOOKBACK))
    end = n - 2
    min_distance = max(float(atr or 0.0) * 0.35, close * 0.0010)
    levels: List[float] = []
    for j in range(start, end):
        if direction == "long":
            value = float(highs[j])
            if value <= close + min_distance:
                continue
            if value >= max(float(x) for x in highs[j - 2:j + 3]):
                levels.append(value)
        else:
            value = float(lows[j])
            if value >= close - min_distance:
                continue
            if value <= min(float(x) for x in lows[j - 2:j + 3]):
                levels.append(value)
    if not levels:
        return 0.0
    return min(levels) if direction == "long" else max(levels)


def _compute_simple_mtf_fixed_tp_pct(*, score: float, atr_pct: float, sl_pct: float) -> Tuple[float, float]:
    score = float(score or 0.0)
    atr_pct = float(atr_pct or 0.0)
    sl_pct = float(sl_pct or MTF_SL_MIN_PCT)

    rr_target = 1.20
    rr_target += _clamp((score - 82.0) * 0.0048, -0.08, 0.10)
    rr_target += _clamp((atr_pct - 0.0060) * 6.0, -0.04, 0.04)
    rr_target = _clamp(rr_target, MTF_RR_MIN + ENTRY_RR_RESERVE, max(MTF_RR_MAX, MTF_RR_MIN + ENTRY_RR_RESERVE))

    tp_pct = sl_pct * rr_target
    tp_pct = _clamp(tp_pct, MTF_TP_MIN_PCT, MTF_TP_MAX_PCT)
    rr_real = tp_pct / max(sl_pct, 1e-12)
    return tp_pct, rr_real


def _dynamic_trade_management_params(
    strength: float,
    score: float,
    atr_pct: Optional[float] = None,
    *,
    sl_pct: Optional[float] = None,
) -> Dict[str, Any]:
    atr_pct = float(atr_pct or 0.0)
    score = float(score or 0.0)
    strength = float(strength or 0.0)
    sl_pct = float(sl_pct or MTF_SL_MIN_PCT)

    tp_fixed, rr_target = _compute_simple_mtf_fixed_tp_pct(score=score, atr_pct=atr_pct, sl_pct=sl_pct)

    if score >= 88.0 or strength >= 0.88:
        bucket = "strong"
        be_ratio = 0.56
        be_offset = 0.00070
    elif score >= 79.0 or strength >= 0.76:
        bucket = "base"
        be_ratio = 0.50
        be_offset = 0.00060
    else:
        bucket = "weak"
        be_ratio = 0.45
        be_offset = 0.00055

    be_activation = _clamp(min(tp_fixed * be_ratio, sl_pct * 0.95), 0.0028, 0.0049)
    return {
        "tp_activation_price": tp_fixed,
        "trail_retrace_price": 0.0,
        "force_min_profit_price": 999999.0,
        "force_min_strength": 0.0,
        "partial_tp_activation_price": 999999.0,
        "partial_tp_close_fraction": 0.0,
        "break_even_activation_price": 0.0,
        "break_even_offset_price": 0.0,
        "bucket": bucket,
        "vol_regime": _volatility_regime_from_atr_pct(atr_pct),
        "tp_rr_multiple": rr_target,
    }


def get_trade_management_params(strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
    return _dynamic_trade_management_params(strength, score, atr_pct)


def _evaluate_market_context(market_context: Dict[str, Any]) -> dict:
    coin = str(market_context.get("coin") or market_context.get("symbol") or "").upper()
    try:
        tf5 = _resolve_tf_snapshot(coin, TF_5M, LOOKBACK_5M, provided_market_context=market_context)
        candles5 = tf5.get("candles") or []
        ok_symbol, reason_symbol, symbol_diag = _validate_symbol_quality(coin, candles5)
        if not ok_symbol:
            return {"signal": False, "reason": reason_symbol, "coin": coin, "diag": symbol_diag}
        if str(tf5.get("status") or "") != "OK" or bool(tf5.get("stale", True)):
            return {"signal": False, "reason": "STALE_OR_BAD_5M", "coin": coin, "diag": {"status": tf5.get("status"), "age_s": round(float(tf5.get("age_s", 0.0) or 0.0), 1)}}

        tf15 = _resolve_tf_snapshot(coin, TF_15M, LOOKBACK_15M)
        tf1h = _resolve_tf_snapshot(coin, TF_1H, LOOKBACK_1H)
        if str(tf15.get("status") or "") != "OK" or str(tf1h.get("status") or "") != "OK":
            return {
                "signal": False,
                "reason": "BAD_HIGHER_TF_DATA",
                "coin": coin,
                "diag": {"tf15": tf15.get("status"), "tf1h": tf1h.get("status")},
            }

        o5 = tf5.get("o") or []
        h5 = tf5.get("h") or []
        l5 = tf5.get("l") or []
        c5 = tf5.get("c") or []
        v5 = tf5.get("v") or []
        ema20_5 = tf5.get(f"ema{EMA_FAST}") or []
        ema50_5 = tf5.get(f"ema{EMA_MID}") or []
        ema200_5 = tf5.get(f"ema{EMA_SLOW}") or []
        if not c5 or not ema20_5 or not ema50_5 or not ema200_5:
            return {"signal": False, "reason": "NO_5M_TREND_DATA", "coin": coin}

        close5 = float(tf5.get("close") or c5[-1])
        adx5 = float(tf5.get("adx") or 0.0)
        atr5 = float(tf5.get("atr") or 0.0)
        atr_pct = float(tf5.get("atr_pct") or 0.0)
        if atr_pct < ATR_PCT_MIN:
            return {"signal": False, "reason": "ATR_TOO_LOW", "coin": coin, "diag": {"atr_pct": round(atr_pct, 6)}}
        if atr_pct > ATR_PCT_MAX:
            return {"signal": False, "reason": "ATR_TOO_HIGH", "coin": coin, "diag": {"atr_pct": round(atr_pct, 6)}}
        if adx5 < M5_ADX_MIN:
            return {"signal": False, "reason": "ADX_5M_TOO_LOW", "coin": coin, "diag": {"adx5": round(adx5, 2)}}

        demo_trial = (market_context.get("_execution_mode") == "demo" and DEMO_STRATEGY_EXPERIMENTAL)
        bias1h, diag1h = _h1_bias(tf1h, demo_trial=demo_trial)
        # Isolated H1-only comparison: DEMO can recover early H1 direction
        # without weakening M15 confirmation or M5 setup/SL/TP rules.
        if (bias1h == "none" and market_context.get("_execution_mode") == "demo"
                and DEMO_H1_EARLY_REVERSAL_ENABLED):
            bias1h, diag1h = _demo_h1_early_reversal(tf1h, diag1h)
        bias15, diag15 = _m15_confirmation(
            tf15, bias1h,
            demo_trial=(demo_trial or (market_context.get("_execution_mode") == "demo"
                                      and DEMO_M15_EARLY_CONFIRMATION_ENABLED)),
        )
        if bias1h == "none":
            return {"signal": False, "reason": "NO_H1_BIAS", "coin": coin, "diag": diag1h}
        if bias15 == "none":
            return {"signal": False, "reason": "NO_M15_BIAS", "coin": coin, "diag": diag15}
        if bias1h != bias15:
            return {"signal": False, "reason": "MTF_BIAS_MISMATCH", "coin": coin, "diag": {"h1": bias1h, "m15": bias15, "diag1h": diag1h, "diag15": diag15}}

        direction = bias1h
        ok_trigger, reason5, diag5 = _detect_simple_mtf_trigger(
            demo_trial=demo_trial,
            demo_m5_expanded=(market_context.get("_execution_mode") == "demo"
                              and DEMO_M5_EXPANDED_ENTRIES_ENABLED),
            direction=direction,
            o=o5,
            h=h5,
            l=l5,
            c=c5,
            v=v5,
            ema20=ema20_5,
            ema50=ema50_5,
            ema200=ema200_5,
            atr=atr5,
        )
        if not ok_trigger:
            if LOG_SIGNAL_DIAGNOSTICS:
                _log(f"BLOCK coin={coin} dir={direction} reason={reason5} diag={diag5}")
            return {"signal": False, "reason": reason5, "coin": coin, "diag": diag5}

        if direction == "long":
            reset_extreme = float(diag5.get("structural_extreme") or min(l5[max(0, len(l5) - 1 - RESET_LOOKBACK_BARS): len(l5) - 1]))
            structural_pct = max(0.0, (close5 - reset_extreme) / max(close5, 1e-12))
        else:
            reset_extreme = float(diag5.get("structural_extreme") or max(h5[max(0, len(h5) - 1 - RESET_LOOKBACK_BARS): len(h5) - 1]))
            structural_pct = max(0.0, (reset_extreme - close5) / max(close5, 1e-12))

        sl_from_atr = atr_pct * MTF_SL_ATR_MULT
        sl_from_structure = structural_pct + ((atr5 / max(close5, 1e-12)) * MTF_SL_BUFFER_ATR)
        # El stop nunca se mete dentro de la invalidación solo para cumplir un
        # porcentaje arbitrario. Se amplía si ATR/min lo exige y se rechaza si
        # la estructura necesita un riesgo excesivo.
        sl_pct = max(sl_from_atr, sl_from_structure, MTF_SL_MIN_PCT)
        if sl_pct > MTF_SL_HARD_MAX_PCT:
            return {
                "signal": False,
                "reason": "STRUCTURAL_SL_TOO_WIDE",
                "coin": coin,
                "diag": {
                    "sl_pct": round(sl_pct, 6),
                    "hard_max": round(MTF_SL_HARD_MAX_PCT, 6),
                    "sl_from_atr": round(sl_from_atr, 6),
                    "sl_from_structure": round(sl_from_structure, 6),
                },
            }

        stop_price = close5 * (1.0 - sl_pct) if direction == "long" else close5 * (1.0 + sl_pct)
        extension_atr = float(diag5.get("extension_atr", 0.0) or 0.0)

        h1_strength = _clamp((float(diag1h.get("adx", 0.0)) - H1_ADX_MIN) / 15.0, 0.0, 1.0)
        m15_strength = _clamp((float(diag15.get("adx", 0.0)) - M15_ADX_MIN) / 14.0, 0.0, 1.0)
        m5_strength = _clamp((adx5 - M5_ADX_MIN) / 13.0, 0.0, 1.0)
        trend_alignment_quality = _clamp((float(diag1h.get("stack_spread", 0.0)) + float(diag15.get("stack_spread", 0.0))) / max(TREND_STACK_MIN_PCT * 6.0, 1e-12), 0.0, 1.0)
        reset_quality = _clamp(1.0 - _clamp(extension_atr / max(TRIGGER_MAX_EMA20_EXTENSION_ATR, 1e-12), 0.0, 1.0), 0.0, 1.0)

        quality = _clamp(
            (0.30 * h1_strength)
            + (0.25 * m15_strength)
            + (0.20 * m5_strength)
            + (0.15 * trend_alignment_quality)
            + (0.10 * reset_quality),
            0.0,
            1.0,
        )
        # Score con rango real: un setup mediocre ya no nace automáticamente
        # exactamente en el umbral mínimo.
        score = round(min(MAX_SCORE, 58.0 + (42.0 * quality)), 2)
        if score < MIN_SCORE_TO_SIGNAL:
            return {
                "signal": False,
                "reason": "SCORE_TOO_LOW",
                "coin": coin,
                "diag": {
                    "score": score,
                    "min_score": MIN_SCORE_TO_SIGNAL,
                    "h1_adx": diag1h.get("adx"),
                    "m15_adx": diag15.get("adx"),
                    "extension_atr": round(extension_atr, 4),
                },
            }

        strength = _clamp(score / 100.0, STRENGTH_MIN, STRENGTH_MAX)
        mgmt = _dynamic_trade_management_params(
            strength,
            score,
            atr_pct,
            sl_pct=sl_pct,
        )

        desired_tp_pct = float(mgmt.get("tp_activation_price", 0.0) or 0.0)
        structural_target = _nearest_structural_target(
            direction=direction,
            highs=h5,
            lows=l5,
            closes=c5,
            atr=atr5,
        )
        structural_tp_pct = 0.0
        if structural_target > 0.0:
            if direction == "long" and structural_target > close5:
                safe_target = close5 + ((structural_target - close5) * STRUCTURAL_TARGET_BUFFER)
                structural_tp_pct = (safe_target - close5) / max(close5, 1e-12)
            elif direction == "short" and structural_target < close5:
                safe_target = close5 - ((close5 - structural_target) * STRUCTURAL_TARGET_BUFFER)
                structural_tp_pct = (close5 - safe_target) / max(close5, 1e-12)
            else:
                safe_target = 0.0
        else:
            safe_target = 0.0

        final_tp_pct = desired_tp_pct
        if structural_tp_pct > 0.0:
            final_tp_pct = min(final_tp_pct, structural_tp_pct)
        rr_real = final_tp_pct / max(sl_pct, 1e-12)
        if final_tp_pct <= 0.0 or not meets_min_rr(rr_real, MIN_RR_TO_SIGNAL + ENTRY_RR_RESERVE):
            return {
                "signal": False,
                "reason": "STRUCTURAL_RR_TOO_LOW",
                "coin": coin,
                "diag": {
                    "tp_rr": round(rr_real, 4),
                    "sl_pct": round(sl_pct, 6),
                    "desired_tp_pct": round(desired_tp_pct, 6),
                    "structural_tp_pct": round(structural_tp_pct, 6),
                    "structural_target": round(structural_target, 8),
                    "min_rr": MIN_RR_TO_SIGNAL + ENTRY_RR_RESERVE,
                    "execution_min_rr": MIN_RR_TO_SIGNAL,
                    "entry_rr_reserve": ENTRY_RR_RESERVE,
                    "reference_price": close5,
                    "stop_price": stop_price,
                    "required_tp_pct": sl_pct * (MIN_RR_TO_SIGNAL + ENTRY_RR_RESERVE),
                    "structural_target_binding": 0.0 < structural_tp_pct < desired_tp_pct,
                    "tp_rr_full_precision": rr_real,
                },
            }

        mgmt["tp_activation_price"] = final_tp_pct
        mgmt["tp_rr_multiple"] = round(rr_real, 4)
        target_price = close5 * (1.0 + final_tp_pct) if direction == "long" else close5 * (1.0 - final_tp_pct)

        t5_open = int(tf5.get("last_t") or candles5[-1].get("t") or 0)
        t5_close = int(tf5.get("last_close_t") or candles5[-1].get("T") or 0)
        t5 = t5_close or t5_open
        age5 = max(0.0, (time.time() * 1000.0 - t5) / 1000.0) if t5 else 0.0
        out = {
            "signal": True,
            "setup_state": diag5.get("setup_state", "LEGACY"),
            "m5_entry_path": diag5.get("m5_entry_path", "pending_reset"),
            "m5_atr": float(atr5),
            "bars_since_reset": diag5.get("bars_since_reset"),
            "reset_index": diag5.get("reset_index"),
            "extension_atr": diag5.get("extension_atr"),
            "structural_extreme": diag5.get("structural_extreme"),
            "direction": direction,
            "strength": round(strength, 4),
            "score": float(score),
            "sl_price_pct": sl_pct,
            "signal_reference_price": close5,
            "stop_price": float(stop_price),
            "target_price": float(target_price),
            "structural_target_price": round(float(structural_target), 8),
            "structural_tp_pct": round(float(structural_tp_pct), 6),
            "tp_activation_price": float(mgmt["tp_activation_price"]),
            "trail_retrace_price": float(mgmt["trail_retrace_price"]),
            "force_min_profit_price": float(mgmt["force_min_profit_price"]),
            "force_min_strength": float(mgmt["force_min_strength"]),
            "partial_tp_activation_price": float(mgmt["partial_tp_activation_price"]),
            "partial_tp_close_fraction": float(mgmt["partial_tp_close_fraction"]),
            "break_even_activation_price": float(mgmt["break_even_activation_price"]),
            "break_even_offset_price": float(mgmt["break_even_offset_price"]),
            "mgmt_bucket": str(mgmt["bucket"]),
            "vol_regime": str(mgmt.get("vol_regime", _volatility_regime_from_atr_pct(atr_pct))),
            "atr_pct": round(float(atr_pct), 6),
            "ema_stack_pct": round(float(diag1h.get("stack_spread", 0.0)), 6),
            "coin": coin,
            "close_5": round(close5, 6),
            "last_candle_t_5m": int(t5),
            "ema20_5m": round(float(ema20_5[-1]), 6),
            "adx1": round(float(diag1h.get("adx", 0.0)), 2),
            "adx15": round(float(diag15.get("adx", 0.0)), 2),
            "strategy_model": "mtf_simple_continuation_5m_v4_audit",
            "market_context_status": str(market_context.get("status") or tf5.get("status") or "OK"),
            "tp_rr_multiple": float(mgmt.get("tp_rr_multiple", 0.0) or 0.0),
            "h1_bias": direction,
            "h1_mode": diag1h.get("h1_mode", "strict"),
            "m15_bias": bias15,
            "m15_mode": diag15.get("m15_mode", "strict"),
        }
        if LOG_SIGNAL_DIAGNOSTICS:
            _log(
                f"SIGNAL coin={coin} dir={out['direction']} close_5={out['close_5']} t5={out['last_candle_t_5m']} age5s={round(age5,1)} "
                f"adx5={round(adx5,2)} adx15={out['adx15']} adx1={out['adx1']} atr_pct={out['atr_pct']} score={out['score']} "
                f"h1_bias={out['h1_bias']} m15_bias={out['m15_bias']} m5_path={out['m5_entry_path']} sl_pct={out['sl_price_pct']} "
                f"tp_fixed={out['tp_activation_price']} tp_rr={out['tp_rr_multiple']:.4f} be_act={out['break_even_activation_price']} "
                f"be_offset={out['break_even_offset_price']} bucket={out['mgmt_bucket']}"
            )
        return out
    except Exception as e:
        return {"signal": False, "reason": "STRATEGY_EXCEPTION", "error": str(e)[:180]}


def get_entry_signal(symbol: str, market_context: Optional[Dict[str, Any]] = None) -> dict:
    market_context = market_context or build_market_context(
        symbol=symbol,
        interval=TF_5M,
        limit=LOOKBACK_5M,
        ema_periods=(EMA_FAST, EMA_MID, EMA_SLOW),
        adx_period=ADX_PERIOD,
        atr_period=ATR_PERIOD,
    )
    return _evaluate_market_context(market_context)


STRATEGY_ID = "mtf_simple"
STRATEGY_VERSION = "v2"
STRATEGY_MODEL = "mtf_simple_continuation_5m_v4_audit"


class MtfSimpleStrategy(BaseStrategy):
    strategy_id = STRATEGY_ID
    strategy_version = STRATEGY_VERSION
    strategy_model = STRATEGY_MODEL

    def evaluate(self, symbol: str, market_context: Optional[Dict[str, Any]] = None) -> dict:
        out = get_entry_signal(symbol, market_context=market_context)
        if isinstance(out, dict) and out.get("signal"):
            out.setdefault("strategy_id", self.strategy_id)
            out.setdefault("strategy_version", self.strategy_version)
            out.setdefault("strategy_model", self.strategy_model)
        return out

    def get_trade_management_params(self, strength: float, score: float, atr_pct: Optional[float] = None) -> Dict[str, Any]:
        return get_trade_management_params(strength, score, atr_pct)


DEFAULT_STRATEGY = MtfSimpleStrategy()


def evaluate_symbol(symbol: str) -> dict:
    return DEFAULT_STRATEGY.evaluate(symbol)
