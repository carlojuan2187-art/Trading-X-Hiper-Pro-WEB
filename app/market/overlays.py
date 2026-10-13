"""Proyección solo lectura de entradas, TP/SL y marcadores cerrados.

Los datos incompletos NO se inventan; el cliente omite marcadores sin
precio o timestamp. Las colecciones LIVE y DEMO se mantienen aisladas.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any


def number(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def when(value: Any) -> int | None:
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        timestamp = value.timestamp() * 1000
    elif isinstance(value, (int, float)):
        timestamp = float(value) * (1000 if value < 100_000_000_000 else 1)
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            timestamp = parsed.timestamp() * 1000
        except ValueError:
            return None
    else:
        return None
    return int(timestamp) if math.isfinite(timestamp) and timestamp > 0 else None


def _first(item: dict, *keys: str) -> Any:
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    return None


def project(item: dict, *, mode: str, closed: bool) -> dict | None:
    if not isinstance(item, dict):
        return None
    raw_symbol = _first(item, 'symbol', 'coin', 'asset')
    if not raw_symbol or not isinstance(raw_symbol, str):
        return None
    side = str(_first(item, 'direction', 'side') or '').upper()
    if side in ('BUY', 'B'):
        side = 'LONG'
    elif side in ('SELL', 'A'):
        side = 'SHORT'
    if side not in ('LONG', 'SHORT'):
        side = 'UNKNOWN'
    metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
    plan = item.get('trade_plan') if isinstance(item.get('trade_plan'), dict) else metadata.get('trade_plan')
    if not isinstance(plan, dict):
        plan = {}
    result = {
        'id': str(_first(item, 'id', 'trade_id', 'position_id') or ''),
        'mode': mode,
        'symbol': raw_symbol.upper(), 'direction': side,
        'entry_price': number(_first(item, 'entry_price', 'avg_entry_price')),
        'stop_price': number(_first(item, 'stop_price', 'sl_price', 'stop_loss') or _first(plan, 'structural_stop_price', 'stop_price', 'initial_stop_price')),
        'target_price': number(_first(item, 'target_price', 'tp_price', 'take_profit') or _first(plan, 'structural_target_price', 'target_price', 'initial_tp_price')),
        'opened_at': when(_first(item, 'opened_at', 'opened_at_ms', 'entry_time', 'created_at') or metadata.get('opened_at_ms')),
        'strategy': str(_first(item, 'strategy_id', 'strategy') or metadata.get('strategy_id') or ''),
        'leverage': number(item.get('leverage') or metadata.get('leverage')),
        'quantity': number(_first(item, 'quantity', 'qty', 'size')),
        'margin_usdc': number(_first(item, 'margin_usdc', 'margin', 'initial_margin')),
        'notional_usdc': number(_first(item, 'notional_usdc', 'notional', 'position_value')),
        'entry_fee_usdc': item.get('entry_fee_usdc'),
    }
    if closed:
        pnl = _first(item, 'pnl_net_usdc', 'profit', 'net_pnl')
        try:
            pnl_number = float(pnl) if pnl is not None else None
            if pnl_number is not None and not math.isfinite(pnl_number):
                pnl_number = None
        except (TypeError, ValueError):
            pnl_number = None
        result.update({
            'exit_price': number(_first(item, 'exit_price', 'close_price')),
            'closed_at': when(_first(item, 'closed_at', 'exit_time', 'timestamp')),
            'pnl_net_usdc': pnl_number,
            'exit_fee_usdc': item.get('exit_fee_usdc'),
            'close_reason': str(_first(item, 'close_reason', 'exit_reason', 'reason') or '').upper(),
        })
    else:
        result.update({
            'mark_price': number(_first(item, 'mark_price', 'current_price')),
            'unrealized_pnl_usdc': item.get('unrealized_pnl_usdc'),
        })
    return result


def project_account(account: dict | None, *, mode: str) -> dict:
    if not isinstance(account, dict):
        return {'mode': mode, 'active': None, 'history': []}
    active = project(account['position'], mode=mode, closed=False) if account.get('position') else None
    history = [result for item in account.get('history', [])[-100:]
               if (result := project(item, mode=mode, closed=True)) is not None]
    return {'mode': mode, 'active': active, 'history': list(reversed(history))}
