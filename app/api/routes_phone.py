"""Registro y login teléfono + contraseña + verificación del contacto Telegram."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.security import create_session_token
from app.api.telegram_oidc import CSRF_COOKIE, SESSION_COOKIE
from app.auth.phone_login import (
    FLOWS, FLOW_SECONDS, PhoneAuthError, begin_flow, finish_flow,
    valid_browser_flow, authenticate_password_login,
)
from app.config import ADMIN_TELEGRAM_ID, BOT_USERNAME, WEBAPP_SESSION_TTL_SECONDS, WEB_BASE_URL

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/auth/phone', tags=['phone-auth'])
FLOW_COOKIE = 'hl_phone_verification'


class BeginRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=35)
    password: str = Field(min_length=10, max_length=128)
    mode: str
    referrer: str = ''


class CompleteRequest(BaseModel):
    flow_id: str = Field(min_length=20, max_length=48)


def _secure_origin(origin: str | None) -> bool:
    return bool(WEB_BASE_URL and WEB_BASE_URL.startswith('https://') and origin == WEB_BASE_URL)


def _raise(exc: PhoneAuthError) -> None:
    raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


def _new_session(uid: int, *, verified_login: bool = False) -> Response:
    session = create_session_token({'user_id': uid,
                                    'is_admin': uid == int(ADMIN_TELEGRAM_ID),
                                    'username': ''})
    response = JSONResponse({'authenticated': True})
    response.set_cookie(SESSION_COOKIE, session, max_age=WEBAPP_SESSION_TTL_SECONDS,
                        httponly=True, secure=True, samesite='lax', path='/')
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), max_age=WEBAPP_SESSION_TTL_SECONDS,
                        httponly=False, secure=True, samesite='lax', path='/')
    if verified_login:
        response.delete_cookie(FLOW_COOKIE, path='/api/v1/auth/phone')
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.post('/start')
def start_phone_login(data: BeginRequest, request: Request) -> Response:
    if not _secure_origin(request.headers.get('origin')):
        raise HTTPException(status_code=403, detail='Origen de solicitud inválido')
    if not os.getenv('WEBAPP_SESSION_SECRET'):
        raise HTTPException(status_code=503, detail='Autenticación telefónica sin configurar')
    if data.mode == 'login':
        try:
            uid = authenticate_password_login(
                data.phone, data.password,
                request.client.host if request.client else 'unknown',
            )
        except PhoneAuthError as exc:
            _raise(exc)
        return _new_session(uid, verified_login=True)
    if data.mode != 'register':
        raise HTTPException(status_code=400, detail='Tipo de acceso inválido')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}', (os.getenv('BOT_USERNAME') or '').strip().lstrip('@')):
        raise HTTPException(status_code=503, detail='Bot de registro no configurado')
    try:
        flow_id, browser_secret = begin_flow(
            data.phone, data.password, data.mode, data.referrer,
            request.client.host if request.client else 'unknown',
        )
    except PhoneAuthError as exc:
        _raise(exc)
    response = JSONResponse({'flow_id': flow_id, 'expires_in': FLOW_SECONDS,
                             'telegram_url': f'https://t.me/{BOT_USERNAME}?start=verify_{flow_id}'})
    response.set_cookie(FLOW_COOKIE, browser_secret, max_age=FLOW_SECONDS,
                        httponly=True, secure=True, samesite='strict', path='/api/v1/auth/phone')
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.post('/complete')
def complete_phone_login(payload: CompleteRequest, request: Request) -> Response:
    if not _secure_origin(request.headers.get('origin')):
        raise HTTPException(status_code=403, detail='Origen de solicitud inválido')
    try:
        uid = finish_flow(payload.flow_id, request.cookies.get(FLOW_COOKIE, ''))
    except PhoneAuthError as exc:
        _raise(exc)
    return _new_session(uid, verified_login=True)


class FlowChangeHub:
    """Un observador MongoDB compartido por proceso para todos los desafíos web."""
    def __init__(self):
        self.waiters: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self.task = None
        self.stream = None

    def subscribe(self, flow_id: str) -> asyncio.Queue:
        if sum(map(len, self.waiters.values())) >= 150 or len(self.waiters[flow_id]) >= 2:
            raise ValueError('Demasiadas conexiones de verificación')
        q = asyncio.Queue(maxsize=2)
        self.waiters[flow_id].add(q)
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run())
        return q

    def unsubscribe(self, flow_id: str, q: asyncio.Queue) -> None:
        self.waiters[flow_id].discard(q)
        if not self.waiters[flow_id]:
            del self.waiters[flow_id]
        if not self.waiters and self.stream:
            asyncio.create_task(asyncio.to_thread(self.stream.close))

    async def run(self):
        try:
            self.stream = await asyncio.to_thread(lambda: FLOWS.watch(
                [{'$match': {'operationType': {'$in': ['update', 'replace']}}}],
                full_document='updateLookup', max_await_time_ms=1000,
            ))
            while self.waiters:
                try:
                    event = await asyncio.to_thread(next, self.stream)
                except StopIteration:
                    break
                doc = event.get('fullDocument') or {}
                fid = event.get('documentKey', {}).get('_id')
                if fid and doc.get('state') == 'verified':
                    for q in tuple(self.waiters.get(fid, ())):
                        if not q.full():
                            q.put_nowait({'status': 'verified'})
        except Exception:
            logger.exception('Canal de verificación Telegram desconectado; disponible confirmación manual')
        finally:
            if self.stream:
                try:
                    await asyncio.to_thread(self.stream.close)
                except Exception:
                    pass
            self.stream = None
            for subscribers in tuple(self.waiters.values()):
                for q in tuple(subscribers):
                    if not q.full():
                        q.put_nowait({'status': 'ws_unavailable'})


_hub = FlowChangeHub()


@router.websocket('/updates/{flow_id}')
async def phone_updates(socket: WebSocket, flow_id: str) -> None:
    if not _secure_origin(socket.headers.get('origin')):
        await socket.close(code=1008)
        return
    try:
        flow = valid_browser_flow(flow_id, socket.cookies.get(FLOW_COOKIE, ''))
    except PhoneAuthError:
        await socket.close(code=1008)
        return
    try:
        q = _hub.subscribe(flow_id)
    except ValueError:
        await socket.close(code=1013)
        return
    await socket.accept()
    try:
        # Tras suscribir, leer estado otra vez para evitar carrera de confirmación.
        flow = valid_browser_flow(flow_id, socket.cookies.get(FLOW_COOKIE, ''))
        if flow.get('state') == 'verified':
            await socket.send_json({'status': 'verified'})
            return
        while True:
            msg_task = asyncio.create_task(q.get())
            recv_task = asyncio.create_task(socket.receive_text())
            done, pending = await asyncio.wait({msg_task, recv_task}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if recv_task in done:
                # Socket desconectado o mensaje inesperado: el cliente debe reconectar.
                break
            update = msg_task.result()
            await socket.send_json(update)
            if update['status'] in ('verified', 'ws_unavailable'):
                break
    except (WebSocketDisconnect, PhoneAuthError):
        pass
    finally:
        _hub.unsubscribe(flow_id, q)
        try:
            await socket.close()
        except Exception:
            pass
