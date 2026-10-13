from __future__ import annotations

from fastapi import HTTPException, status

from app.database import (
    activate_premium_plan,
    get_admin_trade_stats,
    get_admin_user_snapshot,
    get_admin_visual_stats,
    get_security_overview,
    search_users_for_admin,
)


def get_admin_overview() -> dict:
    visual = get_admin_visual_stats() or {}
    trade_stats = get_admin_trade_stats(720) or {}
    security = get_security_overview() or {}
    return {
        'visual': visual,
        'trade_stats_30d': trade_stats,
        'security': security,
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
    return detail


def admin_activate_premium(user_id: int) -> dict:
    if not activate_premium_plan(int(user_id)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No se pudo activar premium para este usuario')
    detail = admin_get_user_detail(int(user_id))
    return {
        'result': 'premium_activated',
        'message': 'Premium activado por 30 días',
        'user': detail,
    }
