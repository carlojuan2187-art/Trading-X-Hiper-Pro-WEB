from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.dependencies import require_session
from app.services.user_service import (
    accept_user_terms,
    activate_user_trading,
    get_control_summary,
    get_dashboard,
    get_performance_summary,
    get_recent_operations,
    get_referrals_summary,
    get_system_runtime_summary,
    get_user_profile,
    pause_user_trading,
    update_user_configuration,
)

router = APIRouter(prefix='/api/v1', tags=['user'])


class UserConfigurationUpdateRequest(BaseModel):
    wallet: str | None = None
    private_key: str | None = None


@router.get('/me')
def me(session: dict = Depends(require_session)) -> dict:
    return get_user_profile(int(session['user_id']))


@router.get('/dashboard')
def dashboard(
    include_balance: bool = Query(default=False),
    session: dict = Depends(require_session),
) -> dict:
    return get_dashboard(int(session['user_id']), include_balance=include_balance)


@router.get('/control')
def control(session: dict = Depends(require_session)) -> dict:
    return get_control_summary(int(session['user_id']))


@router.put('/control/configuration')
def update_configuration(
    payload: UserConfigurationUpdateRequest,
    session: dict = Depends(require_session),
) -> dict:
    return update_user_configuration(
        int(session['user_id']),
        wallet=payload.wallet,
        private_key=payload.private_key,
    )


@router.post('/control/terms/accept')
def accept_control_terms(session: dict = Depends(require_session)) -> dict:
    return accept_user_terms(int(session['user_id']))


@router.post('/control/trading/activate')
def activate_control_trading(session: dict = Depends(require_session)) -> dict:
    return activate_user_trading(int(session['user_id']))


@router.post('/control/trading/pause')
def pause_control_trading(session: dict = Depends(require_session)) -> dict:
    return pause_user_trading(int(session['user_id']))


@router.get('/performance')
def performance(session: dict = Depends(require_session)) -> dict:
    return get_performance_summary(int(session['user_id']))


@router.get('/operations')
def operations(
    limit: int = Query(default=20, ge=1, le=100),
    session: dict = Depends(require_session),
) -> dict:
    return get_recent_operations(int(session['user_id']), limit=limit)


@router.get('/referrals')
def referrals(session: dict = Depends(require_session)) -> dict:
    return get_referrals_summary(int(session['user_id']))


@router.get('/system/runtime')
def system_runtime(session: dict = Depends(require_session)) -> dict:
    return get_system_runtime_summary(
        int(session['user_id']),
        include_private_details=bool(session.get('is_admin')),
    )
