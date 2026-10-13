from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from app.config import get_payment_configuration_status, get_payment_min_confirmations
from app.payment_service import get_active_payment_order_for_user, get_payment_catalog


def _serialize_dt(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def serialize_order_public(order: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not order:
        return None
    expires_at = order.get('expires_at')
    expires_in_seconds = None
    if isinstance(expires_at, datetime):
        expires_in_seconds = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
    return {
        'order_id': order.get('order_id'),
        'plan': order.get('plan') or 'premium',
        'days': int(order.get('days') or 0),
        'base_price_usdt': float(order.get('base_price_usdt') or 0.0),
        'amount_usdt': float(order.get('amount_usdt') or 0.0),
        'amount_formatted': f"{float(order.get('amount_usdt') or 0.0):.3f}",
        'network': order.get('network') or 'bep20',
        'token_symbol': order.get('token_symbol') or 'USDT',
        'deposit_address': order.get('deposit_address'),
        'status': order.get('status') or 'awaiting_payment',
        'confirmations': int(order.get('confirmations') or 0),
        'matched_tx_hash': order.get('matched_tx_hash'),
        'last_verification_reason': order.get('last_verification_reason'),
        'expires_at': _serialize_dt(expires_at),
        'expires_in_seconds': expires_in_seconds,
        'created_at': _serialize_dt(order.get('created_at')),
        'updated_at': _serialize_dt(order.get('updated_at')),
        'confirmed_at': _serialize_dt(order.get('confirmed_at')),
    }


def get_billing_overview(user_id: int) -> dict:
    config_status = get_payment_configuration_status()
    order = get_active_payment_order_for_user(int(user_id))
    return {
        'catalog': get_payment_catalog(),
        'configuration': config_status,
        'required_confirmations': int(get_payment_min_confirmations() or 3),
        'active_order': serialize_order_public(order),
    }
