"""Operaciones DEMO protegidas por la misma sesión Telegram que la web."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import require_session
from app.demo import ledger
from app.demo.credentials import user_ready
from app.demo.settings import DEMO_ENABLED, DEMO_TAKER_FEE_BPS, DEMO_SLIPPAGE_BPS

router = APIRouter(prefix='/api/v1/demo', tags=['demo'])


def enabled_or_503() -> None:
    if not DEMO_ENABLED:
        raise HTTPException(status_code=503, detail='La cuenta DEMO aún no está habilitada en este servicio')


@router.get('')
def demo_account(session: dict = Depends(require_session)) -> dict:
    enabled_or_503()
    return {**ledger.as_public(ledger.get_or_create(int(session['user_id']))),
            'credentials_ready': user_ready(int(session['user_id'])),
            'taker_fee_bps': DEMO_TAKER_FEE_BPS, 'slippage_bps': DEMO_SLIPPAGE_BPS}


@router.post('/start')
def demo_start(session: dict = Depends(require_session)) -> dict:
    enabled_or_503()
    if not user_ready(int(session['user_id'])):
        raise HTTPException(status_code=403, detail='Configura wallet y private key cifrada válida antes de iniciar DEMO')
    try:
        return {**ledger.as_public(ledger.change_status(int(session['user_id']), active=True)), 'credentials_ready': True}
    except ledger.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/pause')
def demo_pause(session: dict = Depends(require_session)) -> dict:
    enabled_or_503()
    try:
        return ledger.as_public(ledger.change_status(int(session['user_id']), active=False))
    except ledger.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/reset')
def demo_reset(session: dict = Depends(require_session)) -> dict:
    enabled_or_503()
    try:
        return ledger.as_public(ledger.reset(int(session['user_id'])))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ledger.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
