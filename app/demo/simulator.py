"""Simulador determinista: usa contratos del motor LIVE, nunca su capa de órdenes.

Nota: el bid/ask L2 es real pero el fill es modelo hipotético, NO ejecución garantizada.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN
from typing import Any

from app.config import (
    LEVERAGE, EXECUTION_MAX_SPREAD_BPS, MAX_TRADES_PER_DAY, MAX_TRADES_PER_HOUR,
    USER_RISK_MAX_CONSEC_LOSSES, USER_RISK_COOLDOWN_SECONDS,
    USER_RISK_LOSS_COOLDOWN_SECONDS, USER_RISK_PF_WINDOW,
    USER_RISK_MIN_PF, USER_RISK_PF_COOLDOWN_SECONDS,
)
from app.demo.ledger import append_close, now_iso
from app.demo.settings import DEMO_SLIPPAGE_BPS, DEMO_TAKER_FEE_BPS
from app.risk import validate_trade_conditions


def _money(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal('0.00000001')))


def _quantity(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN))


def _positive(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


def valid_quote(bid: float, ask: float) -> bool:
    return _positive(bid) > 0 and _positive(ask) >= _positive(bid)


def fill_price(side: str, bid: float, ask: float) -> float:
    if not valid_quote(bid, ask):
        raise ValueError('Libro de órdenes no disponible')
    bps = DEMO_SLIPPAGE_BPS / 10000.0
    return (ask * (1 + bps)) if side == 'buy' else (bid * (1 - bps))


def fee_usdc(notional: float) -> float:
    return _money(notional * DEMO_TAKER_FEE_BPS / 10000.0)


def entry_gate_reason(account: dict, *, now: datetime | None = None) -> str | None:
    """Control DEMO persistente por usuario equivalente a cuotas/cooldown LIVE.

    No lee el estado LIVE ni toca sus gobernadores globales. Esta función se
    ejecuta antes del scanner y de nuevo justo antes de ejecutar una señal.
    """
    if not account.get('enabled') or account.get('position'):
        return 'NOT_ELIGIBLE'
    now = now or datetime.now(timezone.utc)
    try:
        until = datetime.fromisoformat(str(account.get('risk_cooldown_until')))
        until = until if until.tzinfo else until.replace(tzinfo=timezone.utc)
        if now < until:
            return 'RISK_COOLDOWN'
    except (ValueError, TypeError):
        pass
    from app import trading_engine as engine
    if float(account.get('balance_usdc') or 0) < float(engine.MIN_CAPITAL_USDC):
        return 'MIN_CAPITAL'
    try:
        last = datetime.fromisoformat(str(account.get('last_entry_at') or account.get('last_close_at')))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (now - last).total_seconds() < engine.USER_TRADE_COOLDOWN_SECONDS:
            return 'TRADE_COOLDOWN'
    except (ValueError, TypeError):
        pass
    recent = account.get('entry_timestamps') or []
    parsed = []
    for ts in recent:
        try:
            t = datetime.fromisoformat(str(ts))
            parsed.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))
        except (ValueError, TypeError):
            continue
    if MAX_TRADES_PER_HOUR is not None and sum(t >= now - timedelta(hours=1) for t in parsed) >= MAX_TRADES_PER_HOUR:
        return 'MAX_TRADES_PER_HOUR'
    if MAX_TRADES_PER_DAY is not None and sum(t.date() == now.date() for t in parsed) >= MAX_TRADES_PER_DAY:
        return 'MAX_TRADES_PER_DAY'
    return None


def open_position(account: dict, selected: dict, bid: float, ask: float) -> dict | None:
    """Evalúa riesgo, drift y SL/TP ESTRUCTURAL con las funciones originales.

    None = sin entrada apta. Las funciones de exchange LIVE no se importan ni invocan.
    """
    if entry_gate_reason(account) is not None:
        return None
    direction = str(selected.get('direction') or '').lower()
    if direction not in {'long', 'short'}:
        return None
    if not valid_quote(bid, ask):
        return None
    spread = (ask - bid) / ((ask + bid) / 2) * 10000
    if spread > float(EXECUTION_MAX_SPREAD_BPS):
        return None

    from app import trading_engine as engine  # Motor de decisiones existente
    signal = selected.get('signal') or {}
    if not signal.get('signal') or float(selected.get('strength') or 0) < engine.MIN_TRADE_STRENGTH:
        return None
    balance = float(account['balance_usdc'])
    if balance < float(engine.MIN_CAPITAL_USDC):
        return None
    # La política de cuotas y cooldown ya se comprueba en entry_gate_reason.
    risk = validate_trade_conditions(balance, float(selected.get('strength') or 0))
    if not risk.get('ok'):
        return None
    desired_margin = min(float(risk['position_size']), balance)
    # Reserva explícita para comisión de entrada: sin ella 100% de margen
    # no deja dinero para pagar la comisión. Nunca superar el disponible.
    fee_multiplier = (DEMO_TAKER_FEE_BPS / 10000.0) * LEVERAGE
    margin = _money(min(desired_margin, balance / (1 + fee_multiplier) - 0.00000002))
    notional = margin * float(LEVERAGE)
    if notional < float(engine.MIN_NOTIONAL_USDC):
        return None
    entry = fill_price('buy' if direction == 'long' else 'sell', bid, ask)
    drift_ok, _ = engine._entry_drift_check(signal=signal, current_price=entry, direction=direction)
    if not drift_ok:
        return None
    mgmt = engine._coalesce_management_params(
        signal=signal, entry_strength=float(selected.get('strength') or 0),
        best_score=float(signal.get('score') or 0),
    )
    plan_ok, plan = engine._resolve_structural_exit_plan(
        signal=signal, entry_price=entry, direction=direction,
        fallback_sl_pct=float(signal.get('sl_price_pct') or 0),
        fallback_tp_pct=float(mgmt.get('tp_activate_price') or 0),
    )
    if not plan_ok:
        return None
    qty = _quantity(notional / entry)
    if qty < float(engine.MIN_QTY_COIN) or qty * entry < float(engine.MIN_NOTIONAL_USDC):
        return None
    entry_fee = fee_usdc(qty * entry)
    if balance < margin + entry_fee:
        # No permitir saldo negativo por comisión al reservar el margen.
        return None
    updated = deepcopy(account)
    updated['balance_usdc'] = _money(balance - entry_fee)
    updated['position'] = {
        'id': uuid.uuid4().hex, 'mode': 'DEMO', 'symbol': str(selected['symbol']).upper(),
        'direction': direction, 'entry_price': entry, 'quantity': qty,
        'margin_usdc': margin, 'requested_margin_usdc': _money(desired_margin), 'leverage': int(LEVERAGE),
        'notional_usdc': _money(qty * entry),
        'stop_price': float(plan['stop_price']), 'target_price': float(plan['target_price']),
        'rr': float(plan['rr']), 'strategy_id': str(signal.get('strategy_id') or ''),
        'regime_id': str(signal.get('regime_id') or ''),
        'strategy_model': str(signal.get('strategy_model') or ''),
        'router_decision': str(signal.get('router_decision') or ''),
        'signal_strength': float(selected.get('strength') or 0),
        'signal_score': float(signal.get('score') or 0),
        'scanner_score': float((selected.get('scanner') or {}).get('score') or 0),
        'signal_reference_price': float(signal.get('signal_reference_price') or 0),
        'entry_spread_bps': round(spread, 4),
        'slippage_model_bps': float(DEMO_SLIPPAGE_BPS),
        'entry_fee_model_bps': float(DEMO_TAKER_FEE_BPS),
        'execution_model': 'paper_bid_ask',
        'decision_source': 'live_scanner_router',
        'opened_at': now_iso(), 'entry_fee_usdc': entry_fee,
        'mark_price': (bid + ask) / 2, 'unrealized_pnl_usdc': 0.0,
    }
    updated['position_open'] = True
    updated['last_signal_at'] = now_iso()
    updated['last_entry_at'] = now_iso()
    updated['entry_timestamps'] = [x for x in (account.get('entry_timestamps') or []) if isinstance(x, str) and x >= (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()] + [now_iso()]
    updated['last_error'] = None
    return updated


def update_position(account: dict, bid: float, ask: float) -> dict | None:
    """Mark-to-market y cierre por TP/SL; bajo gap se cierra a bid/ask, no al trigger.

    La posición se gestiona incluso si autoentrada está en pausa.
    """
    position = account.get('position')
    if not position or not valid_quote(bid, ask):
        return None
    d = position['direction']
    mid = (bid + ask) / 2
    exit_side = 'sell' if d == 'long' else 'buy'
    exit_px = fill_price(exit_side, bid, ask)
    qty = float(position['quantity'])
    signed = 1 if d == 'long' else -1
    gross = signed * (exit_px - float(position['entry_price'])) * qty
    exit_fee = fee_usdc(qty * exit_px)
    net_open = gross - exit_fee - float(position['entry_fee_usdc'])
    target = float(position['target_price'])
    stop = float(position['stop_price'])
    if d == 'long':
        reason = 'SL' if bid <= stop else 'TP' if bid >= target else None
    else:
        reason = 'SL' if ask >= stop else 'TP' if ask <= target else None
    updated = deepcopy(account)
    updated['last_tick_at'] = now_iso()
    if reason is None:
        updated['position']['mark_price'] = mid
        updated['position']['unrealized_pnl_usdc'] = _money(gross - exit_fee)
        return updated
    closed = {k: v for k, v in position.items() if k != 'unrealized_pnl_usdc'}
    closed.update({
        'exit_price': exit_px, 'closed_at': now_iso(), 'close_reason': reason,
        'exit_fee_usdc': exit_fee, 'pnl_gross_usdc': _money(gross),
        'pnl_net_usdc': _money(net_open), 'result': 'WIN' if net_open > 0 else 'LOSS',
    })
    updated['balance_usdc'] = _money(float(updated['balance_usdc']) + gross - exit_fee)
    updated['position'] = None
    updated['position_open'] = False
    updated['last_close_at'] = now_iso()
    append_close(updated, closed)
    # Refleja los bloqueos por pérdida/racha/PF individuales de LIVE.
    # Nunca escribe ni lee el gobernador de riesgo LIVE de otras cuentas.
    consecutive = int(updated.get('risk_consecutive_losses') or 0) + 1 if net_open < 0 else 0
    updated['risk_consecutive_losses'] = consecutive
    cooldown = USER_RISK_LOSS_COOLDOWN_SECONDS if net_open < 0 else 0
    reason = 'USER_SINGLE_LOSS_COOLDOWN' if net_open < 0 else None
    if consecutive >= USER_RISK_MAX_CONSEC_LOSSES:
        cooldown = max(cooldown, USER_RISK_COOLDOWN_SECONDS)
        reason = f'USER_CONSEC_LOSSES_{consecutive}'
    else:
        recent_pnl = [float(t['pnl_net_usdc']) for t in updated['history'][-USER_RISK_PF_WINDOW:]]
        if USER_RISK_PF_WINDOW > 0 and len(recent_pnl) >= USER_RISK_PF_WINDOW:
            gains = sum(p for p in recent_pnl if p > 0)
            losses = sum(-p for p in recent_pnl if p < 0)
            pf = gains / losses if losses > 0 else float('inf') if gains > 0 else 0.0
            if pf < USER_RISK_MIN_PF:
                cooldown = max(cooldown, USER_RISK_PF_COOLDOWN_SECONDS)
                reason = f'USER_PF_{pf:.2f}_WIN{USER_RISK_PF_WINDOW}'
    if cooldown > 0:
        until = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
        try:
            old_until = datetime.fromisoformat(str(updated.get('risk_cooldown_until')))
            until = max(until, old_until if old_until.tzinfo else old_until.replace(tzinfo=timezone.utc))
        except (ValueError, TypeError):
            pass
        updated['risk_cooldown_until'] = until.isoformat()
        updated['risk_cooldown_reason'] = reason
    return updated
