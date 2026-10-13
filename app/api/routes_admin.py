from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies import require_admin_session
from app.services import admin_service

router = APIRouter(prefix='/api/v1/admin', tags=['admin'])


class AdminActionPayload(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class ManualPlanDaysPayload(AdminActionPayload):
    plan: str = Field(default='premium', min_length=5, max_length=20)
    days: int = Field(..., ge=1, le=3650)


def _require_admin_attr(name: str):
    attr = getattr(admin_service, name, None)
    if attr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'Admin service no disponible: {name}',
        )
    return attr


@router.get('/overview')
def admin_overview(_: dict = Depends(require_admin_session)) -> dict:
    return _require_admin_attr('get_admin_overview')()


@router.get('/strategy/overview')
def admin_strategy_overview(
    user_id: int | None = Query(default=None),
    limit_recent_events: int = Query(default=25, ge=1, le=200),
    _: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('get_admin_strategy_runtime_overview')(user_id=user_id, limit_recent_events=limit_recent_events)


@router.get('/strategy/runtime-summary')
def admin_strategy_runtime_summary(
    user_id: int | None = Query(default=None),
    symbol: str | None = Query(default=None),
    execution_mode: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None),
    regime_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('get_admin_strategy_runtime_summary')(
        user_id=user_id,
        symbol=symbol,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        regime_id=regime_id,
        limit=limit,
    )


@router.get('/strategy/router-events')
def admin_strategy_router_events(
    user_id: int | None = Query(default=None),
    event_type: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    execution_mode: str | None = Query(default=None),
    strategy_id: str | None = Query(default=None),
    regime_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('get_admin_strategy_router_events')(
        user_id=user_id,
        event_type=event_type,
        symbol=symbol,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        regime_id=regime_id,
        limit=limit,
    )


@router.get('/users/search')
def admin_search(
    q: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=25),
    _: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_search_users')(q, limit=limit)


@router.get('/users/{user_id}')
def admin_user_detail(user_id: int, _: dict = Depends(require_admin_session)) -> dict:
    return _require_admin_attr('admin_get_user_detail')(int(user_id))


@router.get('/users/{user_id}/plan/manual-days-preview')
def admin_preview_user_manual_plan_days(
    user_id: int,
    plan: str = Query(default='premium', min_length=5, max_length=20),
    days: int = Query(..., ge=1, le=3650),
    _: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_preview_manual_plan_days')(int(user_id), plan, int(days))


@router.post('/users/{user_id}/plan/premium')
def admin_activate_user_premium(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_activate_premium')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/users/{user_id}/plan/manual-days')
def admin_grant_user_manual_plan_days(
    user_id: int,
    payload: ManualPlanDaysPayload,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_grant_manual_plan_days')(
        int(user_id),
        payload.plan,
        int(payload.days),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=payload.reason,
    )


@router.post('/users/{user_id}/trading/activate')
def admin_activate_user_trading(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_activate_user_trading')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/users/{user_id}/trading/close')
def admin_close_user_trading(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_close_user_trading')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/users/{user_id}/trading/pause')
def admin_pause_user_trading(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_pause_user_trading')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/users/{user_id}/stats/reset')
def admin_reset_stats(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_reset_user_stats')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/users/{user_id}/security/reset-credentials')
def admin_reset_user_credentials(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_reset_user_credentials')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/users/{user_id}/security/migrate-key')
def admin_migrate_user_key(
    user_id: int,
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_migrate_user_private_key')(
        int(user_id),
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/monitor/clear')
def admin_clear_monitor(
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_clear_monitor_feed')(
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )


@router.post('/security/migrate-legacy-keys')
def admin_bulk_migrate_keys(
    limit: int = Query(default=25, ge=1, le=100),
    payload: AdminActionPayload | None = None,
    session: dict = Depends(require_admin_session),
) -> dict:
    return _require_admin_attr('admin_bulk_migrate_legacy_keys')(
        limit=limit,
        actor_user_id=int(session['user_id']),
        actor_username=session.get('username') or '',
        reason=(payload.reason if payload else None),
    )
