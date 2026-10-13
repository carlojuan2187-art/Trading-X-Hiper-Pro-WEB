"""Controles del webhook Telegram (independientes de HTTP y de PTB)."""
from __future__ import annotations

import hashlib
import hmac
import json

MAX_UPDATE_BYTES = 256 * 1024


def derive_webhook_secret(session_secret: str) -> str:
    """Secret separado criptográficamente del que firma cookies de sesión.

    No hace falta añadir otra variable Railway, pero WEBAPP_SESSION_SECRET
    debe ser suficientemente aleatorio, privado y estable entre deploys.
    """
    if not session_secret or len(session_secret) < 32:
        raise ValueError('WEBAPP_SESSION_SECRET debe tener al menos 32 caracteres')
    return hmac.new(
        session_secret.encode('utf-8'),
        b'hyper-league-telegram-webhook-v1',
        hashlib.sha256,
    ).hexdigest()


def valid_webhook_secret(received: str | None, expected: str) -> bool:
    if not received or len(received) != len(expected):
        return False
    try:
        return hmac.compare_digest(received, expected)
    except TypeError:
        return False


def decode_update(payload: bytes) -> dict:
    if not payload or len(payload) > MAX_UPDATE_BYTES:
        raise ValueError('Tamaño de actualización inválido')
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError('Actualización JSON inválida') from exc
    if not isinstance(doc, dict) or not isinstance(doc.get('update_id'), int):
        raise ValueError('Actualización Telegram inválida')
    return doc
