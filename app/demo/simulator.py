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
    LEVERAGE, EXECUTION_MAX_SPREAD_BPS, EXECUTION_MIN_BOOK_COVERAGE,
    EXECUTION_BOOK_IMPACT_BPS,
)
from app.demo.ledger import append_close, now_iso
from app.demo.settings import (
    DEMO_SLIPPAGE_BPS, DEMO_TAKER_FEE_BPS,
    DEMO_BREAK_EVEN_ENABLED, DEMO_BREAK_EVEN_TRIGGER_R, DEMO_BREAK_EVEN_BUFFER_BPS,
)
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


def _demo_fee_safe_stop(position: dict) -> float:
    """Bid (LONG) or ask (SHORT) stop trigger covering modeled round-trip costs.

    This is a dynamic paper stop, not an exchange-guaranteed fill: a gap or
    WebSocket outage may still cause an actual net loss.
    """
    entry = float(position['entry_price'])
    qty = float(position['quantity'])
    fee_in = float(position['entry_fee_usdc'])
    if min(entry, qty) <= 0:
        return 0.0
    rate = DEMO_TAKER_FEE_BPS / 10000.0
    slip = DEMO_SLIPPAGE_BPS / 10000.0
    buffer = DEMO_BREAK_EVEN_BUFFER_BPS / 10000.0
    if position['direction'] == 'long':
        close_fill = (entry + fee_in / qty) / (1.0 - rate) * (1.0 + buffer)
        return close_fill / (1.0 - slip)
    close_fill = (entry - fee_in / qty) / (1.0 + rate) * (1.0 - buffer)
    return close_fill / (1.0 + slip)


def entry_gate_reason(account: dict, *, now: datetime | None = None) -> str | None:
    """Solo restricciones operativas esenciales para experimentos DEMO.

    No aplica límites por hora/día, cooldown temporal tras cierres ni bloqueos
    por pérdidas, rachas o profit factor. LIVE conserva sus propios gobernadores.
    Se valida antes del scanner y otra vez antes de cada entrada simulada.
    """
    if not account.get('enabled') or account.get('position'):
        return 'NOT_ELIGIBLE'
    from app import trading_engine as engine
    if float(account.get('balance_usdc') or 0) < float(engine.MIN_CAPITAL_USDC):
        return 'MIN_CAPITAL'
    return None

def open_position(account: dict, selected: dict, bid: float, ask: float,
                  *, rejection: dict | None = None) -> dict | None:
    """Evalúa riesgo, drift y SL/TP ESTRUCTURAL con las funciones originales.

    None = sin entrada apta. Las funciones de exchange LIVE no se importan ni invocan.
    """
    def reject(reason: str, **details):
        if rejection is not None:
            rejection.update({'reason': reason, **details})
        return None

    gate = entry_gate_reason(account)
    if gate is not None:
        return reject('ENTRY_GATE', gate=gate)
    direction = str(selected.get('direction') or '').lower()
    if direction not in {'long', 'short'}:
        return reject('INVALID_DIRECTION')
    if not valid_quote(bid, ask):
        return reject('INVALID_QUOTE', bid=bid, ask=ask)
    spread = (ask - bid) / ((ask + bid) / 2) * 10000
    if spread > float(EXECUTION_MAX_SPREAD_BPS):
        return reject('SPREAD_TOO_WIDE', spread_bps=round(spread, 3),
                      allowed_bps=float(EXECUTION_MAX_SPREAD_BPS))

    from app import trading_engine as engine  # Motor de decisiones existente
    signal = selected.get('signal') or {}
    if not signal.get('signal') or float(selected.get('strength') or 0) < engine.MIN_TRADE_STRENGTH:
        return reject('INVALID_OR_WEAK_SIGNAL')
    balance = float(account['balance_usdc'])
    if balance < float(engine.MIN_CAPITAL_USDC):
        return reject('MIN_CAPITAL')
    # Validación de capital, posición y señal, sin cuotas ni pausas temporales DEMO.
    risk = validate_trade_conditions(balance, float(selected.get('strength') or 0))
    if not risk.get('ok'):
        return reject('RISK_REJECTED', risk_reason=str(risk.get('reason') or 'UNKNOWN'))
    desired_margin = min(float(risk['position_size']), balance)
    # Reserva explícita para comisión de entrada: sin ella 100% de margen
    # no deja dinero para pagar la comisión. Nunca superar el disponible.
    fee_multiplier = (DEMO_TAKER_FEE_BPS / 10000.0) * LEVERAGE
    margin = _money(min(desired_margin, balance / (1 + fee_multiplier) - 0.00000002))
    notional = margin * float(LEVERAGE)
    if notional < float(engine.MIN_NOTIONAL_USDC):
        return reject('MIN_NOTIONAL', notional=round(notional, 2))
    # Parity with LIVE execution: inspect the order's own side within the
    # configured impact band, with the SAME EXECUTION_MIN_BOOK_COVERAGE ratio.
    # A legacy scanner snapshot with only top-book data is conservatively
    # checked using that top level; do not turn missing data into fake depth.
    scanner = selected.get('scanner') or {}
    side_key = 'buy_depth_notional' if direction == 'long' else 'sell_depth_notional'
    book_value = scanner.get(side_key)
    book_model = 'impact_band_side'
    if book_value is None:
        book_value = scanner.get('top_book_notional')
        book_model = 'top_level_fallback'
    if book_value is not None:
        try:
            available_book = float(book_value)
        except (TypeError, ValueError, OverflowError):
            return reject('INVALID_SCANNER_BOOK', side=direction)
        required_book = float(EXECUTION_MIN_BOOK_COVERAGE) * notional
        if not math.isfinite(available_book) or available_book < required_book:
            return reject('SCANNER_BOOK_BELOW_ORDER', available_usdc=round(available_book, 2),
                          required_usdc=round(required_book, 2), notional_usdc=round(notional, 2),
                          side=direction, book_model=book_model, impact_bps=float(EXECUTION_BOOK_IMPACT_BPS))
    entry = fill_price('buy' if direction == 'long' else 'sell', bid, ask)
    drift_ok, drift_diag = engine._entry_drift_check(signal=signal, current_price=entry, direction=direction)
    if not drift_ok:
        return reject('PRICE_DRIFT', entry_price=entry,
                      reference_price=signal.get('signal_reference_price'),
                      drift=dict(drift_diag or {}))
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
        return reject('EXIT_PLAN_REJECTED', plan=dict(plan or {}))
    qty = _quantity(notional / entry)
    if qty < float(engine.MIN_QTY_COIN) or qty * entry < float(engine.MIN_NOTIONAL_USDC):
        return reject('MIN_QUANTITY', quantity=qty, notional=round(qty * entry, 2))
    entry_fee = fee_usdc(qty * entry)
    if balance < margin + entry_fee:
        # No permitir saldo negativo por comisión al reservar el margen.
        return reject('INSUFFICIENT_BALANCE_FOR_FEE', balance=balance,
                      total_reserved=round(margin + entry_fee, 6))
    updated = deepcopy(account)
    updated['balance_usdc'] = _money(balance - entry_fee)
    updated['position'] = {
        'id': uuid.uuid4().hex, 'mode': 'DEMO', 'symbol': str(selected['symbol']).upper(),
        'direction': direction, 'entry_price': entry, 'quantity': qty,
        'margin_usdc': margin, 'requested_margin_usdc': _money(desired_margin), 'leverage': int(LEVERAGE),
        'notional_usdc': _money(qty * entry),
        'stop_price': float(plan['stop_price']), 'target_price': float(plan['target_price']),
        # Frozen on entry. Older positions lacking these keys remain unchanged.
        'initial_stop_price': float(plan['stop_price']),
        'demo_be_enabled': bool(DEMO_BREAK_EVEN_ENABLED),
        'demo_be_trigger_r': float(DEMO_BREAK_EVEN_TRIGGER_R),
        'demo_be_armed': False, 'demo_be_armed_at': None, 'demo_be_stop_price': None,
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
        'audit_schema': 'bid_ask_observed_v1',
        'audit_mfe_pct': 0.0, 'audit_mae_pct': 0.0,
        'audit_mfe_usdc': 0.0, 'audit_mae_usdc': 0.0,
        'audit_quotes': 0, 'audit_last_quote_at': now_iso(),
        'audit_max_quote_gap_seconds': 0.0, 'audit_quote_gaps_over_10s': 0,
    }
    updated['position_open'] = True
    updated['last_signal_at'] = now_iso()
    updated['last_entry_at'] = now_iso()
    updated['entry_timestamps'] = [x for x in (account.get('entry_timestamps') or []) if isinstance(x, str) and x >= (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()] + [now_iso()]
    # Eliminar advertencias de pausas heredadas: la nueva política DEMO no las usa.
    updated['risk_cooldown_until'] = None
    updated['risk_cooldown_reason'] = None
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
    quote_at = datetime.now(timezone.utc)
    updated['last_tick_at'] = quote_at.isoformat()
    # Original SL/TP always take precedence. BE may arm only on an executable
    # bid/ask quote with >=1R *gross* move and positive fee-adjusted net.
    if reason is None and position.get('demo_be_enabled') and not position.get('demo_be_armed'):
        original_stop = float(position.get('initial_stop_price') or 0)
        original_risk = abs(float(position['entry_price']) - original_stop)
        trigger_r = float(position.get('demo_be_trigger_r') or 0)
        if original_stop > 0 and original_risk > 0 and trigger_r > 0:
            move = signed * (exit_px - float(position['entry_price']))
            if move >= original_risk * trigger_r and net_open > 0:
                candidate = _demo_fee_safe_stop(position)
                old_stop = float(position['stop_price'])
                improved = ((old_stop < candidate < bid) if d == 'long'
                            else (ask < candidate < old_stop))
                if math.isfinite(candidate) and candidate > 0 and improved:
                    updated['position']['stop_price'] = candidate
                    updated['position']['demo_be_armed'] = True
                    updated['position']['demo_be_stop_price'] = candidate
                    updated['position']['demo_be_armed_at'] = quote_at.isoformat()
    # Read-only audit measures: use the executable opposite bid/ask after
    # configured slippage, NOT the mid/mark price. Both extremes are gross
    # price excursions; fee-adjusted net PnL is accounted separately.
    metrics = updated['position']
    gross_pct = gross / max(qty * float(position['entry_price']), 1e-12) * 100.0
    metrics['audit_mfe_pct'] = round(max(0.0, float(position.get('audit_mfe_pct') or 0.0), gross_pct), 6)
    metrics['audit_mae_pct'] = round(min(0.0, float(position.get('audit_mae_pct') or 0.0), gross_pct), 6)
    metrics['audit_mfe_usdc'] = _money(max(0.0, float(position.get('audit_mfe_usdc') or 0.0), gross))
    metrics['audit_mae_usdc'] = _money(min(0.0, float(position.get('audit_mae_usdc') or 0.0), gross))
    metrics['audit_quotes'] = int(position.get('audit_quotes') or 0) + 1
    old_quote = position.get('audit_last_quote_at') or position.get('opened_at')
    try:
        previous_quote_at = datetime.fromisoformat(str(old_quote))
        if previous_quote_at.tzinfo is None:
            previous_quote_at = previous_quote_at.replace(tzinfo=timezone.utc)
        gap = max(0.0, (quote_at - previous_quote_at).total_seconds())
    except (TypeError, ValueError, OverflowError):
        gap = 0.0
    metrics['audit_max_quote_gap_seconds'] = round(max(
        float(position.get('audit_max_quote_gap_seconds') or 0.0), gap), 3)
    metrics['audit_quote_gaps_over_10s'] = (int(position.get('audit_quote_gaps_over_10s') or 0)
                                           + int(gap > 10.0))
    metrics['audit_last_quote_at'] = quote_at.isoformat()
    if reason is None:
        updated['position']['mark_price'] = mid
        updated['position']['unrealized_pnl_usdc'] = _money(gross - exit_fee)
        return updated
    closed = {k: v for k, v in updated['position'].items() if k != 'unrealized_pnl_usdc'}
    close_reason = 'BE' if reason == 'SL' and bool(position.get('demo_be_armed')) else reason
    closed.update({
        'exit_price': exit_px, 'closed_at': now_iso(), 'close_reason': close_reason,
        'exit_fee_usdc': exit_fee, 'pnl_gross_usdc': _money(gross),
        'pnl_net_usdc': _money(net_open), 'result': 'WIN' if net_open > 0 else 'LOSS',
        'audit_total_fees_usdc': _money(float(position['entry_fee_usdc']) + exit_fee),
        'audit_exit_vs_stop_pct': round(
            signed * (exit_px / stop - 1) * 100.0, 6) if stop else None,
    })
    updated['balance_usdc'] = _money(float(updated['balance_usdc']) + gross - exit_fee)
    updated['position'] = None
    updated['position_open'] = False
    updated['last_close_at'] = now_iso()
    append_close(updated, closed)
    # La racha sigue siendo una métrica para la auditoría, no un bloqueo DEMO.
    consecutive = int(updated.get('risk_consecutive_losses') or 0) + 1 if net_open < 0 else 0
    updated['risk_consecutive_losses'] = consecutive
    # Limpiar bloqueos antiguos persistidos de versiones anteriores.
    updated['risk_cooldown_until'] = None
    updated['risk_cooldown_reason'] = None
    return updated
