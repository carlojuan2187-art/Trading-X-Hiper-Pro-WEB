from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.dependencies import require_session
from app.payment_service import cancel_payment_order, confirm_payment_order, create_payment_order
from app.services.billing_service import get_billing_overview, serialize_order_public
from app.services.user_service import get_user_profile

router = APIRouter(prefix='/api/v1/billing', tags=['billing'])
logger = logging.getLogger(__name__)

def _billing_reason_message(reason: str | None) -> str:
    normalized = str(reason or '').strip().lower()
    mapping = {
        'payment_not_found': 'No se localizó todavía un depósito exacto para esta orden.',
        'payment_waiting_confirmations': 'Se detectó el pago, pero sigue pendiente de confirmaciones en cadena.',
        'payment_confirmed': 'Pago confirmado correctamente.',
        'verification_in_progress': 'Ya hay una verificación en curso para esta orden.',
        'order_expired': 'La orden venció y debe generarse una nueva.',
        'order_cancelled': 'La orden fue cancelada.',
        'tx_already_used': 'La transacción ya fue utilizada en otra orden.',
        'payment_config_missing': 'La configuración de cobro aún no está completa en el backend.',
        'verification_error': 'No se pudo completar la verificación en este momento. Intenta de nuevo en unos segundos.',
        'activation_exception': 'El pago fue detectado, pero la activación no pudo completarse todavía.',
        'activation_failed': 'El pago fue detectado, pero la activación no pudo aplicarse.',
        'finalize_exception': 'El pago fue detectado, pero la orden no pudo cerrarse todavía.',
        'already_completed': 'Esta orden ya fue confirmada anteriormente.',
        'order_not_found': 'No se encontró la orden solicitada.',
        'order_not_available': 'La orden no está disponible para verificación ahora mismo.',
    }
    return mapping.get(normalized, 'Sin novedad adicional en la verificación.')


class PaymentOrderCreateRequest(BaseModel):
    days: int


class PaymentOrderActionRequest(BaseModel):
    order_id: str


@router.get('')
def billing(session: dict = Depends(require_session)) -> dict:
    user_id = int(session['user_id'])
    overview = get_billing_overview(user_id)
    overview['user'] = get_user_profile(user_id)
    return overview


@router.get('/order')
def billing_active_order(session: dict = Depends(require_session)) -> dict:
    return {'order': get_billing_overview(int(session['user_id'])).get('active_order')}


@router.post('/order')
def billing_create_order(payload: PaymentOrderCreateRequest, session: dict = Depends(require_session)) -> dict:
    try:
        order = create_payment_order(int(session['user_id']), int(payload.days))
        return {'ok': True, 'order': serialize_order_public(order)}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post('/order/confirm')
def billing_confirm_order(payload: PaymentOrderActionRequest, session: dict = Depends(require_session)) -> dict:
    try:
        result = confirm_payment_order(payload.order_id, int(session['user_id']))
    except Exception as exc:
        logger.exception('billing_confirm_order failed for user_id=%s order_id=%s', session.get('user_id'), payload.order_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='No se pudo verificar la orden en este momento') from exc

    order = result.get('order')
    verification = result.get('verification') or {}
    response = {
        'ok': bool(result.get('ok')),
        'reason': result.get('reason'),
        'message': result.get('message') or _billing_reason_message(result.get('reason')),
        'order': serialize_order_public(order),
        'verification': verification,
    }
    if response['ok']:
        response['user'] = get_user_profile(int(session['user_id']))
    return response


@router.post('/order/cancel')
def billing_cancel_order(payload: PaymentOrderActionRequest, session: dict = Depends(require_session)) -> dict:
    try:
        cancelled = cancel_payment_order(payload.order_id, int(session['user_id']))
    except Exception as exc:
        logger.exception('billing_cancel_order failed for user_id=%s order_id=%s', session.get('user_id'), payload.order_id)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='No se pudo cancelar la orden en este momento') from exc
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='No se pudo cancelar la orden')
    return {'ok': True, 'message': 'Orden cancelada correctamente'}
