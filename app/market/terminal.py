"""Catálogo validado, histórico OHLCV y snapshot L2 para KLineCharts.

No contiene ninguna ruta de trading ni accede a claves LIVE.
"""
from __future__ import annotations

import math
import re
import time
from threading import Lock
from typing import Any, Callable

from app.hyperliquid_client import make_request

TIMEFRAMES_MS = {
    '1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000,
    '30m': 1_800_000, '1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000,
}
_SYMBOL_RE = re.compile(r'^[A-Z0-9_:]{1,48}$')
_cache_lock = Lock()
_catalog: dict[str, Any] = {'at': 0., 'rows': []}


class MarketError(ValueError):
    pass


def canonical_coin(raw: str) -> str:
    """Convierte BTC-USDC / BTC-USDT a BTC; conserva símbolos HIP-3 nativos."""
    if not isinstance(raw, str):
        raise MarketError('Símbolo inválido')
    value = raw.strip().upper().replace('/', '-').replace('_USDC', '-USDC').replace('_USDT', '-USDT')
    if value.endswith(('-USDC', '-USDT')):
        value = value.rsplit('-', 1)[0]
    if not _SYMBOL_RE.fullmatch(value):
        raise MarketError('Símbolo inválido')
    return value


def _good_number(raw: Any, positive: bool = False) -> float:
    value = float(raw)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError('Número de mercado no válido')
    return value


def catalog(request: Callable = make_request, *, fresh: bool = False) -> list[dict]:
    now = time.monotonic()
    with _cache_lock:
        if not fresh and _catalog['rows'] and now - _catalog['at'] < 60:
            return list(_catalog['rows'])
    payload = request('/info', {'type': 'meta'}, retries=2)
    universe = payload.get('universe') if isinstance(payload, dict) else None
    if not isinstance(universe, list):
        raise MarketError('Catálogo de Hyperliquid no disponible')
    rows = []
    seen = set()
    for row in universe:
        if not isinstance(row, dict) or row.get('isDelisted'):
            continue
        symbol = str(row.get('name') or '').strip().upper()
        if not _SYMBOL_RE.fullmatch(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        rows.append({'symbol': symbol, 'display': f'{symbol}/USDC',
                     'size_decimals': max(0, min(12, int(row.get('szDecimals') or 0)))})
    if not rows:
        raise MarketError('No hay contratos perpetuos disponibles')
    with _cache_lock:
        _catalog.update({'at': now, 'rows': rows})
    return list(rows)


def validate_coin(raw: str, request: Callable = make_request) -> str:
    coin = canonical_coin(raw)
    if coin not in {row['symbol'] for row in catalog(request)}:
        raise MarketError('El contrato no existe en el catálogo público de Hyperliquid')
    return coin


def normalize_candle(row: Any) -> dict | None:
    if not isinstance(row, dict):
        return None
    try:
        stamp = int(row['t'])
        o, h, l, c = [_good_number(row[k], True) for k in ('o', 'h', 'l', 'c')]
        vol = _good_number(row.get('v', 0))
        if stamp <= 0 or vol < 0 or h < max(o, c) or l > min(o, c) or l > h:
            return None
        return {'timestamp': stamp, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': vol}
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def candles(symbol: str, timeframe: str, limit: int = 400, request: Callable = make_request) -> list[dict]:
    if timeframe not in TIMEFRAMES_MS:
        raise MarketError('Temporalidad no admitida')
    if type(limit) is not int or not 20 <= limit <= 500:
        raise MarketError('Límite fuera de rango')
    coin = validate_coin(symbol, request)
    now = int(time.time() * 1000)
    # +2 velas para no excluir la vela más reciente por redondeos del servidor.
    raw = request('/info', {'type': 'candleSnapshot', 'req': {
        'coin': coin, 'interval': timeframe,
        'startTime': max(0, now - (limit + 2) * TIMEFRAMES_MS[timeframe]), 'endTime': now,
    }}, retries=2)
    if not isinstance(raw, list):
        raise MarketError('No se pudo recuperar el histórico')
    result: dict[int, dict] = {}
    for item in raw:
        candle = normalize_candle(item)
        if candle:
            result[candle['timestamp']] = candle
    return sorted(result.values(), key=lambda item: item['timestamp'])[-limit:]


def normalize_book(raw: Any) -> dict:
    levels = raw.get('levels') if isinstance(raw, dict) else None
    if not isinstance(levels, list) or len(levels) != 2:
        raise MarketError('Libro de órdenes no disponible')
    sides = []
    for index, side in enumerate(levels):
        if not isinstance(side, list):
            raise MarketError('Libro de órdenes malformado')
        cleaned = []
        for item in side[:30]:
            try:
                price = _good_number(item['px'], True)
                size = _good_number(item['sz'])
                if size >= 0:
                    cleaned.append({'price': price, 'quantity': size})
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        cleaned.sort(key=lambda item: item['price'], reverse=index == 0)
        sides.append(cleaned[:20])
    return {'bids': sides[0], 'asks': sides[1], 'timestamp': raw.get('time')}


def snapshot(symbol: str, request: Callable = make_request) -> dict:
    coin = validate_coin(symbol, request)
    raw = request('/info', {'type': 'l2Book', 'coin': coin}, retries=2)
    return normalize_book(raw)
