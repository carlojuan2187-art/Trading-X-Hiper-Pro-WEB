from __future__ import annotations

from fastapi import HTTPException, status

from app.database import (
    activate_premium_plan,
    admin_set_user_trading_status,
    ensure_access_on_activate,
    get_admin_action_history,
    get_admin_trade_stats,
    get_admin_user_snapshot,
    get_admin_visual_stats,
    get_manual_plan_days_preview,
    get_security_overview,
    get_user_trade_stats,
    grant_manual_plan_days,
    log_admin_action,
    migrate_legacy_private_keys,
    migrate_user_private_key_to_encrypted,
    reset_user_trade_stats_epoch,
    search_users_for_admin,
)




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


def get_admin_overview() -> dict:
    visual = get_admin_visual_stats() or {}
    trade_stats = get_admin_trade_stats(720) or {}
    security = get_security_overview() or {}
    recent_actions = get_admin_action_history(limit=12)
    return {
        'visual': visual,
        'trade_stats_30d': trade_stats,
        'security': security,
        'recent_actions': recent_actions,
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
    return _with_user_performance(detail)


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
    _log_action('reset_user_stats', actor_user_id, actor_username, detail, reason=reason, message=message)
    return {
        'result': 'stats_reset',
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
