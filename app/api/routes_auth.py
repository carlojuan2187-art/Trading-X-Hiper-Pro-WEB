from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.api.dependencies import require_session
from app.api.security import create_session_token, verify_telegram_init_data
from app.api.telegram_oidc import (
    CSRF_COOKIE, FLOW_COOKIE, OIDC_AUTH_URL, OIDC_TOKEN_URL,
    SESSION_COOKIE, create_login_flow, verify_login_flow, verify_telegram_id_token,
)
from app.config import (
    ADMIN_TELEGRAM_ID,
    TELEGRAM_OIDC_CLIENT_ID, TELEGRAM_OIDC_CLIENT_SECRET, TELEGRAM_OIDC_REDIRECT_URI,
    WEBAPP_SESSION_TTL_SECONDS, WEB_BASE_URL, WEB_LOGIN_ENABLED,
)
from app.database import set_referrer
from app.services.user_service import ensure_user_exists, get_user_profile

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/auth', tags=['auth'])


class TelegramAuthRequest(BaseModel):
    init_data: str


@router.post('/telegram')
def auth_with_telegram(payload: TelegramAuthRequest) -> dict:
    """Legacy Mini App login remains available while users migrate to the web."""
    verified = verify_telegram_init_data(payload.init_data)
    ensure_user_exists(verified['user_id'], verified.get('username'))

    start_param = (verified.get('start_param') or '').strip()
    if start_param.isdigit() and int(start_param) != int(verified['user_id']):
        set_referrer(int(verified['user_id']), int(start_param))

    token = create_session_token(verified)
    return {
        'access_token': token,
        'token_type': 'bearer',
        'expires_in': int(WEBAPP_SESSION_TTL_SECONDS),
        'is_admin': bool(verified.get('is_admin')),
        'user': get_user_profile(int(verified['user_id'])),
    }


def _require_web_login_config() -> None:
    if not WEB_LOGIN_ENABLED:
        raise HTTPException(status_code=503, detail="El acceso web está pendiente de activación")
    if not all([WEB_BASE_URL, TELEGRAM_OIDC_CLIENT_ID, TELEGRAM_OIDC_CLIENT_SECRET, TELEGRAM_OIDC_REDIRECT_URI]):
        raise HTTPException(status_code=503, detail="Autenticación web no configurada")
    base = urlsplit(WEB_BASE_URL)
    callback = urlsplit(TELEGRAM_OIDC_REDIRECT_URI)
    if (base.scheme != "https" or base.path not in {"", "/"} or base.query or base.fragment
            or callback.scheme != "https" or
            (base.scheme, base.netloc) != (callback.scheme, callback.netloc) or
            callback.path != "/api/v1/auth/telegram/oidc/callback" or callback.query or callback.fragment):
        raise HTTPException(status_code=503, detail="Origen web o callback OIDC inválidos")


@router.get('/telegram/oidc/start', include_in_schema=False)
def start_web_login(ref: str = '') -> Response:
    _require_web_login_config()
    # Referrer is cryptographically bound to this login flow; no browser-side trust.
    referrer_id = int(ref) if ref.isdigit() and 0 < len(ref) <= 15 and int(ref) > 0 else None
    public, cookie = create_login_flow(referrer_id=referrer_id)
    query = urlencode({
        'client_id': TELEGRAM_OIDC_CLIENT_ID,
        'redirect_uri': TELEGRAM_OIDC_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid profile',
        'state': public['state'],
        'nonce': public['nonce'],
        'code_challenge': public['code_challenge'],
        'code_challenge_method': 'S256',
    })
    response = RedirectResponse(f'{OIDC_AUTH_URL}?{query}', status_code=302)
    response.set_cookie(FLOW_COOKIE, cookie, max_age=300, path='/', httponly=True, secure=True, samesite='lax')
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/telegram/oidc/callback', include_in_schema=False)
async def telegram_oidc_callback(request: Request, code: str = '', state: str = '', error: str = '') -> Response:
    _require_web_login_config()
    if error:
        raise HTTPException(status_code=401, detail="Autorización de Telegram cancelada o rechazada")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Datos incompletos de Telegram")
    try:
        flow = verify_login_flow(request.cookies.get(FLOW_COOKIE, ''), state)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            token_response = await client.post(
                OIDC_TOKEN_URL,
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': TELEGRAM_OIDC_REDIRECT_URI,
                    'client_id': TELEGRAM_OIDC_CLIENT_ID,
                    'code_verifier': flow['verifier'],
                },
                auth=(TELEGRAM_OIDC_CLIENT_ID, TELEGRAM_OIDC_CLIENT_SECRET),
            )
            token_response.raise_for_status()
            id_token = token_response.json()['id_token']
        verified = verify_telegram_id_token(id_token, client_id=TELEGRAM_OIDC_CLIENT_ID, nonce=flow['nonce'])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning('Telegram OIDC callback failed (%s)', type(exc).__name__)
        raise HTTPException(status_code=401, detail="No se pudo verificar el acceso con Telegram") from exc
    except Exception as exc:
        # PyJWT validation errors must also fail closed, without leaking tokens.
        logger.warning('Telegram OIDC signature/claims validation failed (%s)', type(exc).__name__)
        raise HTTPException(status_code=401, detail="Identidad de Telegram no verificada") from exc

    user_id = int(verified['user_id'])
    ensure_user_exists(user_id, verified.get('username'))
    referrer_id = flow.get('referrer_id')
    if isinstance(referrer_id, int) and referrer_id != user_id:
        set_referrer(user_id, referrer_id)
    verified['is_admin'] = user_id == int(ADMIN_TELEGRAM_ID)
    session = create_session_token(verified)
    response = RedirectResponse(url='/', status_code=303)
    response.delete_cookie(FLOW_COOKIE, path='/')
    response.set_cookie(SESSION_COOKIE, session, max_age=WEBAPP_SESSION_TTL_SECONDS, httponly=True, secure=True, samesite='lax', path='/')
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), max_age=WEBAPP_SESSION_TTL_SECONDS, httponly=False, secure=True, samesite='lax', path='/')
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/session')
def web_session(session: dict = Depends(require_session)) -> dict:
    return {'authenticated': True, 'is_admin': bool(session['is_admin']),
            'user': get_user_profile(int(session['user_id']))}


@router.post('/logout')
def logout(_: dict = Depends(require_session)) -> Response:
    # Browser logout invalidates cookies; signed bearer sessions live until expiry.
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path='/')
    response.delete_cookie(CSRF_COOKIE, path='/')
    response.delete_cookie(FLOW_COOKIE, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return response
