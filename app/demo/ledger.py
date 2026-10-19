"""Estado DEMO en una sola colección, con CAS por versión por usuario.

Contabilidad de paper trading exclusivamente; no comparte colección con LIVE.
Las mutaciones usan replace_one con version para evitar dobles cierres/entradas.
"""
from __future__ import annotations

from copy import deepcopy
import logging
from datetime import datetime, timezone
import math

from app.database import db
from app.demo.settings import DEMO_INITIAL_EQUITY_USDC, DEMO_HISTORY_LIMIT

logger = logging.getLogger(__name__)
COLLECTION = db['demo_accounts']


class Conflict(RuntimeError):
    """Otro ciclo o usuario modificó la cuenta durante la operación."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_demo_capital(amount: float) -> float:
    # Capital simulado: nunca afecta a fondos LIVE, wallet ni apalancamiento.
    if isinstance(amount, bool):
        raise ValueError('Capital DEMO inválido')
    try:
        result = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError('Capital DEMO inválido') from exc
    if not math.isfinite(result) or not 10 <= result <= 100000:
        raise ValueError('El capital DEMO debe estar entre 10 y 100000 USDC')
    if round(result, 2) != result:
        raise ValueError('Máximo dos decimales para el capital DEMO')
    return round(result, 2)


def make_account(user_id: int, *, initial_equity: float | None = None) -> dict:
    amount = DEMO_INITIAL_EQUITY_USDC if initial_equity is None else validate_demo_capital(initial_equity)
    return {
        '_id': int(user_id), 'mode': 'DEMO', 'version': 0, 'enabled': False,
        'initial_equity_usdc': amount,
        'balance_usdc': amount,
        'position_open': False, 'position': None, 'history': [], 'trades_closed': 0,
        'wins': 0, 'losses': 0, 'realized_pnl_usdc': 0.0,
        'last_tick_at': None, 'last_signal_at': None, 'last_error': None,
        'last_entry_at': None, 'last_close_at': None, 'entry_timestamps': [],
        'risk_consecutive_losses': 0, 'risk_cooldown_until': None, 'risk_cooldown_reason': None,
        'created_at': now_iso(), 'updated_at': now_iso(),
    }


def get_or_create(user_id: int) -> dict:
    user_id = int(user_id)
    COLLECTION.update_one({'_id': user_id}, {'$setOnInsert': make_account(user_id)}, upsert=True)
    return COLLECTION.find_one({'_id': user_id})


def read(user_id: int) -> dict | None:
    return COLLECTION.find_one({'_id': int(user_id)})


def save(previous: dict, updated: dict) -> dict:
    if previous['_id'] != updated['_id']:
        raise ValueError('Cambio de propietario prohibido')
    new_doc = deepcopy(updated)
    new_doc['version'] = int(previous['version']) + 1
    new_doc['updated_at'] = now_iso()
    result = COLLECTION.replace_one(
        {'_id': previous['_id'], 'version': int(previous['version'])}, new_doc,
    )
    if result.modified_count != 1:
        raise Conflict('Actualización concurrente; reintentar')
    # Se encola DESPUÉS de guardar la operación; nunca se bloquea el trade por Telegram.
    if bool(previous.get('position')) != bool(new_doc.get('position')):
        try:
            from app.demo.trade_alerts import queue_transition
            queue_transition(previous, new_doc)
        except Exception:
            logger.exception('DEMO trade guardado pero el aviso Telegram no pudo encolarse user=%s', previous['_id'])
    # Persist one sanitized, deduplicated learning sample only AFTER successful
    # CAS closure. Failures are diagnosed but NEVER roll back the paper trade.
    if previous.get('position') and new_doc.get('position') is None:
        from app.demo.ai_experience import build_closed_experience
        closed = (new_doc.get('history') or [None])[-1]
        if isinstance(closed, dict):
            experience = build_closed_experience(closed)
            if experience:
                try:
                    db['demo_ai_experiences'].update_one(
                        {'_id': experience['_id']},
                        {'$setOnInsert': experience}, upsert=True)
                    logger.info('DEMO_AI_EXPERIENCE_RECORDED trade=%s coin=%s model=%s pnl_net_usdc=%.6f',
                                closed.get('id'), experience['symbol'],
                                experience['model_version'], experience['pnl_net_usdc'])
                except Exception:
                    logger.exception('DEMO_AI_EXPERIENCE_PERSIST_FAILED trade=%s', closed.get('id'))
            elif closed.get('decision_source') == 'ai_ppo_only':
                logger.warning('DEMO_AI_EXPERIENCE_NOT_RECORDABLE trade=%s missing_entry_state', closed.get('id'))
    return new_doc


def change_status(user_id: int, *, active: bool) -> dict:
    for _ in range(4):
        previous = get_or_create(user_id)
        if bool(previous['enabled']) == active:
            return previous
        updated = deepcopy(previous)
        updated['enabled'] = active
        try:
            return save(previous, updated)
        except Conflict:
            continue
    raise Conflict('Cuenta ocupada; vuelve a intentar')


def configure_initial_capital(user_id: int, amount: float) -> dict:
    amount = validate_demo_capital(amount)
    for _ in range(4):
        previous = get_or_create(user_id)
        if previous.get('enabled') or previous.get('position') or previous.get('position_open'):
            raise ValueError('Pausa DEMO y espera el cierre de la posición antes de configurar capital')
        # Nunca reescribir un historial ni PnL. El usuario puede usar Reiniciar saldo
        # explícitamente y luego asignar un capital nuevo si quiere empezar de cero.
        if (previous.get('history') or int(previous.get('trades_closed') or 0)
                or float(previous.get('realized_pnl_usdc') or 0) != 0):
            raise ValueError('Para cambiar capital con operaciones previas, usa Reiniciar saldo (borra historial) primero')
        if float(previous.get('initial_equity_usdc') or 0) == amount and float(previous.get('balance_usdc') or 0) == amount:
            return previous
        updated = deepcopy(previous)
        updated['initial_equity_usdc'] = amount
        updated['balance_usdc'] = amount
        try:
            return save(previous, updated)
        except Conflict:
            continue
    raise Conflict('Cuenta ocupada; vuelve a intentar')


def reset(user_id: int) -> dict:
    for _ in range(4):
        previous = get_or_create(user_id)
        if previous.get('enabled') or previous.get('position'):
            raise ValueError('Pausa DEMO y espera el cierre de la posición antes de reiniciar')
        updated = make_account(user_id, initial_equity=previous.get('initial_equity_usdc', DEMO_INITIAL_EQUITY_USDC))
        updated['created_at'] = previous.get('created_at') or updated['created_at']
        try:
            return save(previous, updated)
        except Conflict:
            continue
    raise Conflict('Cuenta ocupada; vuelve a intentar')


def reset_all_demo_accounts() -> dict:
    """Pause all paper traders, then clear history and reset balances.

    This never queries or changes LIVE. Each account update is version-CAS,
    so a concurrent position entry cannot be overwritten by a stale reset.
    """
    accounts = list(COLLECTION.find({}))
    if any(row.get('position') or row.get('position_open') for row in accounts):
        raise ValueError('No se puede reiniciar DEMO: hay posiciones abiertas. Espera a que cierren.')
    for previous in accounts:
        if previous.get('enabled'):
            updated = deepcopy(previous)
            updated['enabled'] = False
            save(previous, updated)
    # Verify again after pausing: refuse to discard any concurrent open.
    accounts = list(COLLECTION.find({}))
    if any(row.get('position') or row.get('position_open') for row in accounts):
        raise Conflict('Apertura concurrente detectada; no se borró el historial.')
    reset_count = 0
    for previous in accounts:
        updated = make_account(int(previous['_id']),
                               initial_equity=previous.get('initial_equity_usdc', DEMO_INITIAL_EQUITY_USDC))
        updated['created_at'] = previous.get('created_at') or updated['created_at']
        save(previous, updated)
        reset_count += 1
    logger.warning('DEMO_ADMIN_STATS_RESET reset_accounts=%d scope=demo_accounts_only', reset_count)
    return {'ok': True, 'reset_accounts': reset_count, 'trading_paused': True, 'mode': 'DEMO'}


def recent_strategy_metrics(history: list[dict]) -> list[dict]:
    """Estadísticas netas por estrategia sobre historial DEMO retenido (max 200).

    No atribuir estas métricas a todo el historial si el límite de retención
    ya descartó operaciones anteriores. Nunca lee estadísticas LIVE.
    """
    by_strategy: dict[str, dict] = {}
    for trade in history:
        name = str(trade.get('strategy_id') or 'SIN_ESTRATEGIA')
        row = by_strategy.setdefault(name, {
            'strategy_id': name, 'trades': 0, 'wins': 0, 'losses': 0,
            'net_pnl_usdc': 0.0, 'gross_wins_usdc': 0.0, 'gross_losses_usdc': 0.0,
        })
        pnl = float(trade.get('pnl_net_usdc') or 0)
        row['trades'] += 1
        row['net_pnl_usdc'] += pnl
        if pnl > 0:
            row['wins'] += 1
            row['gross_wins_usdc'] += pnl
        elif pnl < 0:
            row['losses'] += 1
            row['gross_losses_usdc'] -= pnl
    for row in by_strategy.values():
        total = row['trades']
        row['win_rate_pct'] = round(100 * row['wins'] / total, 2) if total else 0.0
        row['profit_factor'] = (round(row['gross_wins_usdc'] / row['gross_losses_usdc'], 4)
                                if row['gross_losses_usdc'] else None)
        row['net_pnl_usdc'] = round(row['net_pnl_usdc'], 8)
        row['gross_wins_usdc'] = round(row['gross_wins_usdc'], 8)
        row['gross_losses_usdc'] = round(row['gross_losses_usdc'], 8)
    return sorted(by_strategy.values(), key=lambda row: row['net_pnl_usdc'], reverse=True)


def as_public(doc: dict) -> dict:
    result = {k: deepcopy(v) for k, v in doc.items() if k != '_id'}
    result['user_id'] = doc['_id']
    position = doc.get('position')
    unrealized = float(position.get('unrealized_pnl_usdc', 0.0)) if position else 0.0
    locked = float(position.get('margin_usdc', 0.0)) if position else 0.0
    balance = float(doc.get('balance_usdc', 0.0))
    result.update({
        'unrealized_pnl_usdc': round(unrealized, 8),
        'equity_usdc': round(balance + unrealized, 8),
        'available_usdc': round(max(0.0, balance - locked), 8),
        'locked_margin_usdc': round(locked, 8),
        'history': list(reversed(result.get('history') or [])),
        'recent_strategy_metrics': recent_strategy_metrics(doc.get('history') or []),
        'strategy_metrics_scope': 'retained_demo_history_only',
    })
    return result


def append_close(updated: dict, trade: dict) -> None:
    updated['history'] = (list(updated.get('history') or []) + [trade])[-DEMO_HISTORY_LIMIT:]
    updated['trades_closed'] = int(updated.get('trades_closed', 0)) + 1
    pnl = float(trade['pnl_net_usdc'])
    updated['wins' if pnl > 0 else 'losses'] = int(updated.get('wins' if pnl > 0 else 'losses', 0)) + 1
    updated['realized_pnl_usdc'] = round(float(updated.get('realized_pnl_usdc', 0.0)) + pnl, 8)
