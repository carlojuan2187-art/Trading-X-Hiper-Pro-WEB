from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl

from fastapi import HTTPException, status

from app.config import (
    ADMIN_TELEGRAM_ID,
    TELEGRAM_BOT_TOKEN,
    WEBAPP_INITDATA_MAX_AGE_SECONDS,
    WEBAPP_SESSION_SECRET,
    WEBAPP_SESSION_TTL_SECONDS,
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_telegram_init_data(init_data: str) -> dict[str, Any]:
    if not init_data or not isinstance(init_data, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="init_data ausente")

    items = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    provided_hash = items.pop("hash", None)
    if not provided_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="hash ausente en init_data")

    auth_date_raw = items.get("auth_date")
    try:
        auth_date = int(auth_date_raw)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_date inválido")

    now = int(time.time())
    if auth_date > now + 30:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_date futuro")
    if WEBAPP_INITDATA_MAX_AGE_SECONDS > 0 and (now - auth_date) > WEBAPP_INITDATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="init_data expirado")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(items.items()))
    secret_key = hmac.new(b"WebAppData", TELEGRAM_BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(calculated_hash, provided_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="hash inválido")

    user_raw = items.get("user")
    if not user_raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="usuario ausente en init_data")

    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inválido en init_data") from exc

    if not isinstance(user, dict):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user inválido")
    user_id = user.get("id")
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="id de usuario ausente")

    start_param = items.get("start_param")

    return {
        "user_id": int(user_id),
        "username": user.get("username") or "",
        "first_name": user.get("first_name") or "",
        "last_name": user.get("last_name") or "",
        "language_code": user.get("language_code") or "",
        "auth_date": auth_date,
        "start_param": start_param,
        "is_admin": int(user_id) == int(ADMIN_TELEGRAM_ID),
    }


def create_session_token(user_payload: dict[str, Any]) -> str:
    now = int(time.time())
    payload = {
        "sub": int(user_payload["user_id"]),
        "username": user_payload.get("username") or "",
        "is_admin": bool(user_payload.get("is_admin")),
        "iat": now,
        "exp": now + int(WEBAPP_SESSION_TTL_SECONDS),
    }
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded_payload = _b64url_encode(raw_payload)
    signature = hmac.new(
        WEBAPP_SESSION_SECRET.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str) -> dict[str, Any]:
    if not token or "." not in token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token inválido")

    encoded_payload, provided_signature = token.rsplit(".", 1)
    expected_signature = hmac.new(
        WEBAPP_SESSION_SECRET.encode("utf-8"),
        encoded_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, provided_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="firma inválida")

    try:
        payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="payload inválido") from exc

    try:
        if not isinstance(payload, dict):
            raise ValueError()
        exp = int(payload.get("exp") or 0)
        user_id = int(payload["sub"])
        issued_at = int(payload.get("iat") or 0)
        if user_id <= 0 or issued_at > int(time.time()) + 30:
            raise ValueError()
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="payload inválido") from exc
    if exp <= int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token expirado")

    return {
        "user_id": user_id,
        "username": payload.get("username") or "",
        "is_admin": bool(payload.get("is_admin")) and user_id == int(ADMIN_TELEGRAM_ID),
        "iat": issued_at,
        "exp": exp,
    }
