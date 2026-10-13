"""Normalización estricta de mercado WS para el motor DEMO.

Se usan bid y ask, no vela ni precio last; no se acepta cotización invertida.
"""
from __future__ import annotations

import json
from app.demo.simulator import valid_quote


def decode_book(message: str | dict) -> tuple[str, float, float] | None:
    try:
        event = json.loads(message) if isinstance(message, str) else message
        if not isinstance(event, dict) or event.get('channel') != 'l2Book':
            return None
        data = event.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('coin'), str):
            return None
        symbol = data['coin'].strip().upper()
        if not symbol or len(symbol) > 48:
            return None
        levels = data.get('levels')
        if not isinstance(levels, list) or len(levels) != 2 or not levels[0] or not levels[1]:
            return None
        bid = float(levels[0][0]['px'])
        ask = float(levels[1][0]['px'])
        if not valid_quote(bid, ask):
            return None
        return symbol, bid, ask
    except (ValueError, TypeError, IndexError, KeyError, OverflowError):
        return None
