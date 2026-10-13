from __future__ import annotations

from fastapi import HTTPException, status

from app import database as db_module
from app.database import (
    activate_premium_plan,
    admin_set_user_trading_status,
    ensure_access_on_activate,
    get_admin_action_history,
    get_admin_trade_stats,
    get_admin_live_monitor_snapshot,
    get_admin_operator_snapshots,
    get_system_runtime_snapshot,
    clear_admin_monitor_feed as clear_admin_monitor_feed_db,
    get_admin_user_snapshot,
    get_admin_visual_stats,
    get_manual_plan_days_preview,
    get_security_overview,
    get_user_trade_stats,
    grant_manual_plan_days,
    log_admin_action,
    migrate_legacy_private_keys,
    migrate_user_private_key_to_encrypted,
    reset_user_private_key_credentials,
    reset_user_trade_stats_epoch,
    search_users_for_admin,
    log_user_activity,
)



def _strategy_overview_fallback(user_id: int | None = None, limit_recent_events: int = 25) -> dict:
    return {
        'scope_user_id': int(user_id) if user_id is not None else None,
        'counts': {},
        'breakdown': {'execution_mode': {}, 'strategy': {}, 'regime': {}, 'event_type': {}},
        'recent_events': [],
        'partial': True,
        'unavailable': True,
        'error': 'strategy_telemetry_unavailable',
    }


def _strategy_summary_fallback(
    user_id: int | None = None,
    *,
    limit: int = 100,
    symbol: str | None = None,
    execution_mode: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> list[dict]:
    return []


def _strategy_events_fallback(
    user_id: int | None = None,
    *,
    limit: int = 100,
    event_type: str | None = None,
    execution_mode: str | None = None,
    symbol: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> list[dict]:
    return []


get_strategy_runtime_overview = getattr(db_module, 'get_strategy_runtime_overview', _strategy_overview_fallback)
get_strategy_runtime_summary = getattr(db_module, 'get_strategy_runtime_summary', _strategy_summary_fallback)
get_strategy_router_events = getattr(db_module, 'get_strategy_router_events', _strategy_events_fallback)


def _log_action(action: str, actor_user_id: int | None, actor_username: str | None, target_user: dict | None = None, *, reason: str | None = None, status: str = 'success', message: str | None = None, metadata: dict | None = None) -> None:
    if actor_user_id is None:
        return
    log_admin_action(
        int(actor_user_id),
        actor_username or '',
        action,
        target_user_id=int(target_user['user_id']) if target_user and target_user.get('user_id') is not None else None,
        target_username=(target_user or {}).get('username'),
        reason=reason,
        status=status,
        message=message,
        metadata=metadata or {},
    )

def _with_user_performance(detail: dict) -> dict:
    enriched = dict(detail)
    user_id = int(detail['user_id'])
    enriched['performance'] = {
        '24h': get_user_trade_stats(user_id, 24) or {},
        '7d': get_user_trade_stats(user_id, 24 * 7) or {},
        '30d': get_user_trade_stats(user_id, 24 * 30) or {},
    }
    return enriched




def admin_clear_monitor_feed(actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    outcome = clear_admin_monitor_feed_db()
    if not outcome.get('ok'):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=outcome.get('message') or 'No se pudo limpiar el feed operativo')
    message = f"Feed operativo limpiado: {int(outcome.get('hidden_count') or 0)} evento(s) ocultados"
    _log_action(
        'clear_admin_monitor_feed',
        actor_user_id,
        actor_username,
        None,
        reason=reason,
        message=message,
        metadata={'hidden_count': int(outcome.get('hidden_count') or 0)},
    )
    return {
        'result': 'monitor_feed_cleared',
        'message': message,
        'hidden_count': int(outcome.get('hidden_count') or 0),
    }

def get_admin_overview() -> dict:
    section_errors: list[str] = []

    def _safe(name: str, fn, default):
        try:
            value = fn()
            return value if value is not None else default
        except Exception as exc:
            section_errors.append(f"{name}: {exc}")
            return default

    visual = _safe('visual', lambda: get_admin_visual_stats() or {}, {})
    trade_stats = _safe('trade_stats_30d', lambda: get_admin_trade_stats(720) or {}, {})
    security = _safe('security', lambda: get_security_overview() or {}, {})
    recent_actions = _safe('recent_actions', lambda: get_admin_action_history(limit=12), [])
    monitor = _safe(
        'monitor',
        lambda: get_admin_live_monitor_snapshot(limit_events=30, limit_positions=20) or {},
        {'events': [], 'active_positions': [], 'counts': {'events': 0, 'active_positions': 0, 'trade_events': 0, 'payment_events': 0}},
    )
    operator_snapshots = _safe('operator_snapshots', lambda: get_admin_operator_snapshots(limit=20) or [], [])
    system_runtime = _safe('system_runtime', lambda: get_system_runtime_snapshot() or {}, {})
    strategy_runtime = _safe('strategy_runtime', lambda: get_strategy_runtime_overview(None, limit_recent_events=15) or {}, {})
    return {
        'visual': visual,
        'trade_stats_30d': trade_stats,
        'security': security,
        'recent_actions': recent_actions,
        'monitor': monitor,
        'operator_snapshots': operator_snapshots,
        'system_runtime': system_runtime,
        'strategy_runtime': strategy_runtime,
        'partial': bool(section_errors),
        'section_errors': section_errors,
    }


def admin_search_users(query: str, limit: int = 10) -> dict:
    items = search_users_for_admin(query=query, limit=limit)
    return {
        'query': (query or '').strip(),
        'count': len(items),
        'items': items,
    }


def admin_get_user_detail(user_id: int) -> dict:
    detail = get_admin_user_snapshot(int(user_id))
    if not detail:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Usuario no encontrado')
    enriched = _with_user_performance(detail)
    enriched['strategy_runtime_preview'] = get_strategy_runtime_summary(int(user_id), limit=20)
    enriched['strategy_runtime_overview'] = get_strategy_runtime_overview(int(user_id), limit_recent_events=10)
    return enriched


def admin_activate_premium(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    if not activate_premium_plan(int(user_id)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No se pudo activar premium para este usuario')
    detail = admin_get_user_detail(int(user_id))
    message = 'Premium activado manualmente'
    _log_action('activate_premium_fixed_30d', actor_user_id, actor_username, detail, reason=reason, message=message, metadata={'target_plan': 'premium'})
    return {
        'result': 'premium_activated',
        'message': message,
        'user': detail,
    }


def admin_preview_manual_plan_days(user_id: int, plan: str, days: int) -> dict:
    if int(days) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='La cantidad de días debe ser mayor que cero')
    outcome = get_manual_plan_days_preview(int(user_id), str(plan or 'premium'), int(days))
    if not outcome.get('ok'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=outcome.get('message') or 'No se pudo calcular la previsualización')
    detail = admin_get_user_detail(int(user_id))
    return {
        'result': 'manual_plan_days_preview',
        'message': 'Previsualización calculada',
        'user': detail,
        'preview': outcome,
    }


def admin_grant_manual_plan_days(user_id: int, plan: str, days: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    if int(days) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='La cantidad de días debe ser mayor que cero')
    outcome = grant_manual_plan_days(int(user_id), str(plan or 'premium'), int(days))
    if not outcome.get('ok'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=outcome.get('message') or 'No se pudo aplicar la extensión manual')
    detail = admin_get_user_detail(int(user_id))
    message = outcome.get('message') or f'Plan actualizado manualmente por {int(days)} días'
    _log_action('grant_manual_plan_days', actor_user_id, actor_username, detail, reason=reason, message=message, metadata={'plan': outcome.get('target_plan') or str(plan or 'premium'), 'days': int(days), 'new_expires_at': outcome.get('new_expires_at')})
    return {
        'result': 'manual_plan_days_granted',
        'message': message,
        'user': detail,
        'plan': outcome.get('target_plan') or str(plan or 'premium'),
        'days': int(days),
    }


def admin_preview_manual_premium_days(user_id: int, days: int) -> dict:
    return admin_preview_manual_plan_days(user_id, 'premium', days)


def admin_grant_manual_premium_days(user_id: int, days: int) -> dict:
    return admin_grant_manual_plan_days(user_id, 'premium', days)


def admin_pause_user_trading(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    if not admin_set_user_trading_status(int(user_id), 'inactive'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No se pudo pausar el trading del usuario')
    detail = admin_get_user_detail(int(user_id))
    message = 'Trading pausado por admin'
    _log_action('pause_user_trading', actor_user_id, actor_username, detail, reason=reason, message=message)
    return {
        'result': 'trading_paused',
        'message': message,
        'user': detail,
    }


def admin_close_user_trading(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    detail_before = admin_get_user_detail(int(user_id))

    if not admin_set_user_trading_status(int(user_id), 'inactive'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No se pudo pausar el trading del usuario antes del cierre manual')

    try:
        from app.trading_engine import admin_force_close_position
        outcome = admin_force_close_position(int(user_id), reason=reason)
    except Exception as e:
        _log_action(
            'close_user_trading',
            actor_user_id,
            actor_username,
            detail_before,
            reason=reason,
            status='error',
            message=f'Error al forzar cierre manual: {e}',
            metadata={'phase': 'force_close', 'runtime_live_trade': bool(detail_before.get('runtime_live_trade')), 'runtime_active_symbol': detail_before.get('runtime_active_symbol')},
        )
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f'No se pudo cerrar la operación en exchange: {e}')

    detail = admin_get_user_detail(int(user_id))
    if outcome.get('result') == 'position_closed':
        symbol = str(outcome.get('symbol') or detail_before.get('runtime_active_symbol') or '-')
        direction = str(outcome.get('direction') or '').upper() or 'UNKNOWN'
        qty_closed = float(outcome.get('qty_closed') or 0.0)
        log_user_activity(
            int(user_id),
            'Cierre administrativo ejecutado',
            f'Soporte administrativo cerró manualmente la posición activa {symbol} ({direction}) y dejó el trading pausado hasta nueva reactivación.',
            tone='warning',
            event_type='admin_force_close',
            metadata={
                'symbol': symbol,
                'direction': direction.lower(),
                'qty_closed': qty_closed,
                'close_order_id': str(outcome.get('close_order_id') or ''),
                'admin_reason': reason or '',
            },
        )
    else:
        log_user_activity(
            int(user_id),
            'Trading pausado sin posición activa',
            'Soporte administrativo pausó el trading. No se detectó posición abierta en exchange para cerrar.',
            tone='warning',
            event_type='admin_force_close_no_position',
            metadata={
                'runtime_active_symbol': detail_before.get('runtime_active_symbol'),
                'admin_reason': reason or '',
            },
        )

    message = str(outcome.get('message') or 'Trading pausado por admin')
    if outcome.get('result') == 'position_closed':
        message = f"{message} Nuevas entradas quedaron pausadas hasta reactivación manual."

    _log_action(
        'close_user_trading',
        actor_user_id,
        actor_username,
        detail,
        reason=reason,
        message=message,
        metadata={
            'close_result': outcome.get('result'),
            'symbol': outcome.get('symbol') or detail_before.get('runtime_active_symbol'),
            'direction': outcome.get('direction'),
            'qty_closed': float(outcome.get('qty_closed') or 0.0),
            'close_order_id': str(outcome.get('close_order_id') or ''),
            'finalized': bool(outcome.get('finalized', False)),
        },
    )
    return {
        'result': outcome.get('result') or 'trading_closed',
        'message': message,
        'user': detail,
        'close': outcome,
    }


def admin_activate_user_trading(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    access = ensure_access_on_activate(int(user_id))
    if not access.get('allowed', False):
        detail = get_admin_user_snapshot(int(user_id))
        _log_action('activate_user_trading', actor_user_id, actor_username, detail, reason=reason, status='rejected', message=access.get('message') or 'Sin acceso vigente')
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=access.get('message') or 'El usuario no tiene acceso vigente para activar trading')
    if not admin_set_user_trading_status(int(user_id), 'active'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No se pudo activar el trading del usuario')
    detail = admin_get_user_detail(int(user_id))
    message = access.get('plan_message') or 'Trading activado por admin'
    _log_action('activate_user_trading', actor_user_id, actor_username, detail, reason=reason, message=message)
    return {
        'result': 'trading_activated',
        'message': message,
        'user': detail,
    }


def admin_reset_user_stats(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    if not reset_user_trade_stats_epoch(int(user_id)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No se pudo resetear el rendimiento del usuario')
    detail = admin_get_user_detail(int(user_id))
    message = 'Rendimiento del usuario reseteado desde este momento'
    log_user_activity(int(user_id), 'Rendimiento reiniciado', 'El historial estadístico fue reiniciado por soporte administrativo.', tone='warning', event_type='stats_reset')
    _log_action('reset_user_stats', actor_user_id, actor_username, detail, reason=reason, message=message)
    return {
        'result': 'stats_reset',
        'message': message,
        'user': detail,
    }


def admin_reset_user_credentials(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    outcome = reset_user_private_key_credentials(int(user_id))
    result = outcome.get('result')
    if result == 'user_not_found':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Usuario no encontrado')
    if not outcome.get('ok'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=outcome.get('message') or 'No se pudo resetear la credencial del usuario')
    detail = admin_get_user_detail(int(user_id))
    message = outcome.get('message') or 'Credencial operativa reseteada'
    log_user_activity(
        int(user_id),
        'Credencial operativa reseteada',
        'Soporte administrativo eliminó la private key almacenada y pausó el trading hasta que el usuario vuelva a configurarla.',
        tone='warning',
        event_type='credential_reset',
    )
    _log_action(
        'reset_user_credentials',
        actor_user_id,
        actor_username,
        detail,
        reason=reason,
        message=message,
        metadata={
            'wallet_preserved': bool(outcome.get('wallet_preserved')),
            'trading_paused': bool(outcome.get('trading_paused')),
            'had_private_key': bool(outcome.get('had_private_key')),
        },
    )
    return {
        'result': 'credentials_reset',
        'message': message,
        'user': detail,
    }


def admin_migrate_user_private_key(user_id: int, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    outcome = migrate_user_private_key_to_encrypted(int(user_id))
    result = outcome.get('result')
    if result == 'user_not_found':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Usuario no encontrado')
    if result == 'not_configured':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='El usuario no tiene private key configurada')
    detail = admin_get_user_detail(int(user_id))
    messages = {
        'already_encrypted': 'La private key del usuario ya estaba cifrada',
        'migrated': 'Private key legacy migrada a cifrado',
    }
    message = messages.get(result, 'Operación completada')
    if result == 'migrated':
        log_user_activity(int(user_id), 'Clave operativa endurecida', 'La private key fue migrada a almacenamiento cifrado.', tone='success', event_type='private_key_hardened')
    _log_action('migrate_user_private_key', actor_user_id, actor_username, detail, reason=reason, message=message, metadata={'migration_result': result})
    return {
        'result': result,
        'message': message,
        'user': detail,
    }


def admin_bulk_migrate_legacy_keys(limit: int = 25, actor_user_id: int | None = None, actor_username: str | None = None, reason: str | None = None) -> dict:
    outcome = migrate_legacy_private_keys(limit=limit)
    outcome['result'] = 'bulk_migration_completed'
    outcome['message'] = f"Migración completada. {outcome.get('migrated_count', 0)} keys actualizadas."
    _log_action('bulk_migrate_legacy_keys', actor_user_id, actor_username, None, reason=reason, message=outcome['message'], metadata={'requested_limit': int(limit), 'migrated_count': outcome.get('migrated_count', 0), 'remaining_legacy_plaintext_keys': outcome.get('remaining_legacy_plaintext_keys', 0)})
    return outcome


def get_admin_strategy_runtime_overview(user_id: int | None = None, limit_recent_events: int = 25) -> dict:
    return get_strategy_runtime_overview(user_id, limit_recent_events=int(limit_recent_events))


def get_admin_strategy_runtime_summary(
    user_id: int | None = None,
    *,
    limit: int = 100,
    symbol: str | None = None,
    execution_mode: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> dict:
    items = get_strategy_runtime_summary(
        user_id,
        limit=int(limit),
        symbol=symbol,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        regime_id=regime_id,
    )
    return {
        'count': len(items),
        'items': items,
        'filters': {
            'user_id': int(user_id) if user_id is not None else None,
            'symbol': (symbol or '').strip().upper() or None,
            'execution_mode': (execution_mode or '').strip().lower() or None,
            'strategy_id': (strategy_id or '').strip().lower() or None,
            'regime_id': (regime_id or '').strip().lower() or None,
            'limit': int(limit),
        },
    }


def get_admin_strategy_router_events(
    user_id: int | None = None,
    *,
    limit: int = 100,
    event_type: str | None = None,
    execution_mode: str | None = None,
    symbol: str | None = None,
    strategy_id: str | None = None,
    regime_id: str | None = None,
) -> dict:
    items = get_strategy_router_events(
        user_id=user_id,
        limit=int(limit),
        event_type=event_type,
        execution_mode=execution_mode,
        symbol=symbol,
        strategy_id=strategy_id,
        regime_id=regime_id,
    )
    return {
        'count': len(items),
        'items': items,
        'filters': {
            'user_id': int(user_id) if user_id is not None else None,
            'event_type': (event_type or '').strip().lower() or None,
            'execution_mode': (execution_mode or '').strip().lower() or None,
            'symbol': (symbol or '').strip().upper() or None,
            'strategy_id': (strategy_id or '').strip().lower() or None,
            'regime_id': (regime_id or '').strip().lower() or None,
            'limit': int(limit),
        },
    }

