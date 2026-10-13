"""Realtime privado vía MongoDB Change Streams: UN observador por proceso API.

El transporte desde MongoDB y hacia el navegador es event-driven (sin polling
REST). Cada socket se autentica con la cookie OIDC y valida Origin. Ningún
mensaje contiene private keys ni documentos MongoDB en bruto.

MongoDB requiere un replica set o cluster sharded con Change Streams.
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.exceptions import HTTPException
from pymongo.errors import PyMongoError

from app.api.security import verify_session_token
from app.api.telegram_oidc import SESSION_COOKIE
from app.config import WEB_BASE_URL
from app.database import db
from app.market.overlays import project, project_account

logger = logging.getLogger(__name__)
router = APIRouter()
STREAM_COLLECTIONS = ('demo_accounts', 'users', 'trades', os.getenv('ACTIVE_TRADES_COLLECTION', 'active_trades'))


def authorize_ws(cookie: str | None, origin: str | None, base_url: str) -> int:
    if not cookie or not base_url or origin != base_url or not base_url.startswith('https://'):
        raise ValueError('Origen o sesión WebSocket no autorizados')
    try:
        claims = verify_session_token(cookie)
    except HTTPException as exc:
        raise ValueError('Sesión expirada o inválida') from exc
    return int(claims['user_id'])


def live_projection(user_id: int) -> dict:
    from app.database import get_user_active_trade_snapshot, get_user_trades_limited
    active = project(get_user_active_trade_snapshot(user_id) or {}, mode='LIVE', closed=False)
    history = [item for row in get_user_trades_limited(user_id, 100)
               if (item := project(row, mode='LIVE', closed=True)) is not None]
    return {'mode': 'LIVE', 'active': active, 'history': history}


def snapshots(user_id: int) -> list[dict]:
    from app.demo import ledger
    from app.demo.credentials import user_ready
    from app.demo.settings import DEMO_TAKER_FEE_BPS, DEMO_SLIPPAGE_BPS
    from app.services.user_service import get_user_profile
    demo = ledger.get_or_create(user_id)
    return [
        {'type': 'demo', 'data': {**ledger.as_public(demo), 'credentials_ready': user_ready(user_id),
                                  'taker_fee_bps': DEMO_TAKER_FEE_BPS, 'slippage_bps': DEMO_SLIPPAGE_BPS}},
        {'type': 'overlays', 'mode': 'DEMO', 'data': project_account(demo, mode='DEMO')},
        {'type': 'overlays', 'mode': 'LIVE', 'data': live_projection(user_id)},
        {'type': 'profile', 'data': get_user_profile(user_id)},
    ]


def event_owner(event: dict) -> tuple[int, str] | None:
    """Exclusivamente el ID del usuario; jamás publicar raw fullDocument."""
    collection = event.get('ns', {}).get('coll')
    if collection not in STREAM_COLLECTIONS:
        return None
    if collection == 'demo_accounts':
        uid = event.get('documentKey', {}).get('_id')
    else:
        uid = (event.get('fullDocument') or {}).get('user_id')
    if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
        return None
    return uid, collection


def relevant_messages(user_id: int, collection: str) -> list[dict]:
    if collection == 'demo_accounts':
        from app.demo import ledger
        demo = ledger.read(user_id)
        return ([{'type': 'demo', 'data': ledger.as_public(demo)},
                 {'type': 'overlays', 'mode': 'DEMO', 'data': project_account(demo, mode='DEMO')}]
                if demo else [])
    if collection == 'users':
        from app.services.user_service import get_user_profile, get_control_summary
        return [{'type': 'profile', 'data': get_user_profile(user_id)},
                {'type': 'control', 'data': get_control_summary(user_id)}]
    from app.services.user_service import get_dashboard, get_recent_operations, get_performance_summary
    return [{'type': 'overlays', 'mode': 'LIVE', 'data': live_projection(user_id)},
            {'type': 'operations', 'data': get_recent_operations(user_id, 20)},
            {'type': 'dashboard', 'data': get_dashboard(user_id, include_balance=False)},
            {'type': 'performance', 'data': get_performance_summary(user_id)}]


def _next_change(stream):
    try:
        return next(stream)
    except StopIteration as exc:
        raise RuntimeError('Change Stream finalizado') from exc


class SharedChangeHub:
    """Una suscripción Mongo por proceso en lugar de una por navegador."""

    def __init__(self) -> None:
        self.subscribers: dict[int, set[asyncio.Queue[str]]] = defaultdict(set)
        self.task: asyncio.Task | None = None
        self.stream = None
        self.backoff = 1
        self.active = False

    def subscribe(self, user_id: int) -> asyncio.Queue[str]:
        # Límite por instancia para evitar abuso de sockets por la misma cuenta.
        if len(self.subscribers.get(int(user_id), ())) >= 4:
            raise ValueError('Máximo de sesiones realtime para esta cuenta')
        if sum(len(v) for v in self.subscribers.values()) >= 250:
            raise ValueError('Máximo de conexiones realtime alcanzado')
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=64)
        self.subscribers[int(user_id)].add(queue)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue[str]) -> None:
        group = self.subscribers.get(int(user_id))
        if group:
            group.discard(queue)
            if not group:
                self.subscribers.pop(int(user_id), None)
        if not self.subscribers and self.stream is not None:
            # Despierta next() cuando se va el último navegador.
            asyncio.create_task(asyncio.to_thread(self.stream.close))

    def publish(self, user_id: int, collection: str) -> None:
        for queue in tuple(self.subscribers.get(user_id, ())):
            if queue.full():
                # Un cliente lento recupera snapshot completo, nunca usa datos
                # antiguos y nunca acumula una cola sin límite.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait('resync')
                continue
            queue.put_nowait(collection)

    def broadcast(self, message: str) -> None:
        for uid in tuple(self.subscribers):
            self.publish(uid, message)

    async def run(self):
        pipeline = [{'$match': {'ns.coll': {'$in': list(STREAM_COLLECTIONS)}}}]
        while self.subscribers:
            stream = None
            try:
                stream = await asyncio.to_thread(db.watch, pipeline, full_document='updateLookup', max_await_time_ms=15000)
                self.stream = stream
                self.active = True
                self.backoff = 1
                self.broadcast('resync')
                while self.subscribers:
                    event = await asyncio.to_thread(_next_change, stream)
                    owner = event_owner(event)
                    if owner:
                        self.publish(*owner)
            except asyncio.CancelledError:
                raise
            except (PyMongoError, RuntimeError) as exc:
                # CursorKilled al cerrar el último WebSocket es un evento normal:
                # cancelamos el cursor Mongo para no consumir recursos sin clientes.
                if not self.subscribers:
                    logger.debug('Change Stream detenido: sin suscriptores (%s)', type(exc).__name__)
                    break
                # Un cursor interrumpido también sucede con failover/reconexiones;
                # no implica necesariamente que falte un replica set.
                logger.warning('MongoDB Change Stream interrumpido (%s); reconectando', type(exc).__name__)
                self.active = False
                self.broadcast('error')
                await asyncio.sleep(self.backoff)  # reconexión del transporte, no polling de estado
                self.backoff = min(self.backoff * 2, 30)
            finally:
                self.active = False
                self.stream = None
                if stream is not None:
                    await asyncio.to_thread(stream.close)


hub = SharedChangeHub()


@router.websocket('/api/v1/stream')
async def stream(websocket: WebSocket) -> None:
    cookie = websocket.cookies.get(SESSION_COOKIE)
    try:
        user_id = authorize_ws(cookie, websocket.headers.get('origin'), WEB_BASE_URL)
        valid_until = int(verify_session_token(cookie)['exp'])
        queue = hub.subscribe(user_id)
    except (ValueError, HTTPException):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    disconnect_task = asyncio.create_task(websocket.receive_text())
    try:
        # Snapshot inicial tras registrar el watcher: evita perder cambios
        # en la ventana entre leer y suscribirse.
        for packet in await asyncio.to_thread(snapshots, user_id):
            await websocket.send_json(packet)
        await websocket.send_json({'type': 'ready', 'at': datetime.now(timezone.utc).isoformat()})
        while True:
            deadline = valid_until - datetime.now(timezone.utc).timestamp()
            if deadline <= 0:
                await websocket.close(code=1008)
                break
            next_event = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({next_event, disconnect_task},
                                         timeout=deadline, return_when=asyncio.FIRST_COMPLETED)
            if disconnect_task in done:
                next_event.cancel()
                break
            if next_event not in done:
                next_event.cancel()
                await websocket.close(code=1008)
                break
            collection = next_event.result()
            if collection == 'error':
                await websocket.send_json({'type': 'error', 'message': 'Canal de eventos temporalmente interrumpido. Reconectando…'})
                continue
            packets = await asyncio.to_thread(snapshots if collection == 'resync' else relevant_messages,
                                              user_id, *([] if collection == 'resync' else [collection]))
            for packet in packets:
                await websocket.send_json(packet)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        logger.exception('Fallo inesperado del stream privado user=%s', user_id)
    finally:
        disconnect_task.cancel()
        hub.unsubscribe(user_id, queue)
        try:
            await websocket.close()
        except Exception:
            pass
