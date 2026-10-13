"""Telegram web OIDC primitives. No database or exchange dependency.

The login code flow is kept separate from Telegram Mini App initData.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from functools import lru_cache
import time
from typing import Any

import jwt
from jwt import PyJWKClient

from app.config import WEBAPP_SESSION_SECRET

TELEGRAM_ISSUER = "https://oauth.telegram.org"
TELEGRAM_JWKS_URL = f"{TELEGRAM_ISSUER}/.well-known/jwks.json"
OIDC_AUTH_URL = f"{TELEGRAM_ISSUER}/auth"
OIDC_TOKEN_URL = f"{TELEGRAM_ISSUER}/token"
OIDC_FLOW_MAX_AGE_SECONDS = 300
SESSION_COOKIE = "__Host-txh_session"
CSRF_COOKIE = "__Host-txh_csrf"
FLOW_COOKIE = "__Host-txh_oidc"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_login_flow(now: int | None = None, *, referrer_id: int | None = None) -> tuple[dict[str, Any], str]:
    """Return public OAuth values + HMAC-signed, HttpOnly cookie data."""
    issued_at = int(now if now is not None else time.time())
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    private_data = {"state": state, "nonce": nonce, "verifier": verifier, "iat": issued_at}
    if referrer_id is not None and 0 < int(referrer_id) < 10**16:
        private_data["referrer_id"] = int(referrer_id)
    payload = _b64url(json.dumps(private_data, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(WEBAPP_SESSION_SECRET.encode(), f"oidc.{payload}".encode(), hashlib.sha256).digest()
    cookie_value = f"{payload}.{_b64url(signature)}"
    public = {"state": state, "nonce": nonce, "code_challenge": challenge}
    return public, cookie_value


def verify_login_flow(cookie_value: str, returned_state: str, now: int | None = None) -> dict[str, Any]:
    """Validate state, signature and lifetime before exchanging an OAuth code."""
    try:
        payload, signature = cookie_value.split(".")
        expected = hmac.new(WEBAPP_SESSION_SECRET.encode(), f"oidc.{payload}".encode(), hashlib.sha256).digest()
        decoded_sig = _unb64url(signature)
        decoded_payload = _unb64url(payload)
        if (_b64url(decoded_sig) != signature or _b64url(decoded_payload) != payload
                or not hmac.compare_digest(expected, decoded_sig)):
            raise ValueError("firma inválida")
        data = json.loads(decoded_payload)
        current = int(now if now is not None else time.time())
        issued_at = int(data["iat"])
        if current < issued_at - 30 or current - issued_at > OIDC_FLOW_MAX_AGE_SECONDS:
            raise ValueError("flujo expirado")
        if not returned_state or not hmac.compare_digest(data["state"], returned_state):
            raise ValueError("state no coincide")
        if not all(isinstance(data.get(k), str) and len(data[k]) >= 20 for k in ("state", "verifier", "nonce")):
            raise ValueError("flujo incompleto")
        return data
    except (TypeError, KeyError, ValueError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("La sesión de autenticación de Telegram es inválida o expiró") from exc


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    # Reuse Telegram's cached JWKS across logins to avoid needless fetches.
    return PyJWKClient(TELEGRAM_JWKS_URL, cache_jwk_set=True, lifespan=300, timeout=10)


def verify_telegram_id_token(id_token: str, *, client_id: str, nonce: str) -> dict[str, Any]:
    """Validate Telegram's signed OIDC ID token; never trust browser-decoded claims."""
    jwks = _get_jwks_client()
    key = jwks.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256", "ES256"],
        issuer=TELEGRAM_ISSUER,
        audience=client_id,
        options={"require": ["sub", "iss", "aud", "iat", "exp"]},
        leeway=30,
    )
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
        raise ValueError("Nonce del login no coincide")
    user_id = claims.get("id")
    # Telegram profile 'id' must correspond to the bot's historical Telegram user IDs.
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise ValueError("ID de usuario de Telegram inválido")
    return {
        "user_id": user_id,
        "username": str(claims.get("preferred_username") or ""),
        "first_name": str(claims.get("given_name") or ""),
        "last_name": str(claims.get("family_name") or ""),
        "auth_date": int(time.time()),
    }
