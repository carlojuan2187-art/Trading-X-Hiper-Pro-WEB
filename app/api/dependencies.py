from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.security import verify_session_token
from app.api.telegram_oidc import CSRF_COOKIE, SESSION_COOKIE
from app.config import WEB_BASE_URL

bearer_scheme = HTTPBearer(auto_error=False)


def require_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    # Keep existing Mini App Bearer authentication compatible during migration.
    if credentials is not None:
        if credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer inválido")
        return verify_session_token(credentials.credentials)

    # Independent browser login: HttpOnly session cookie; require CSRF for writes.
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inicia sesión con Telegram")
    payload = verify_session_token(token)
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin", "")
        csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
        csrf_header = request.headers.get("x-csrf-token", "")
        if (
            not WEB_BASE_URL or origin != WEB_BASE_URL
            or len(csrf_cookie) < 20 or not hmac.compare_digest(csrf_cookie, csrf_header)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Validación CSRF fallida")
    return payload


def require_admin_session(session: dict = Depends(require_session)) -> dict:
    if not session.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso de administrador requerido")
    return session
