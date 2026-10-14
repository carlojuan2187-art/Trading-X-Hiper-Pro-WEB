"""Servicio dedicado DEMO. Ejecución: python -m app.demo.worker.

No importar/llamar execute_trade_cycle ni funciones de órdenes LIVE.
Recomendación: una réplica del worker; además lease Mongo para proteger despliegues.
"""
from __future__ import annotations

import logging
import os
import time
import asyncio
import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from pymongo.errors import DuplicateKeyError

from app.database import db, touch_runtime_component
from app.demo import ledger
from app.demo.settings import DEMO_ENABLED, DEMO_SCAN_SECONDS, DEMO_MAX_USERS_PER_SCAN
from app.demo.credentials import user_ready
from app.demo.market_events import decode_book
from app.demo.simulator import open_position, update_position, entry_gate_reason

logger = logging.getLogger(__name__)
LEASES = db['demo_worker_leases']
OWNER = f'{os.getpid()}-{uuid.uuid4().hex}'
LEASE_ID = 'global_demo_worker'


def acquire_lease() -> bool:
    """Only one worker can mutate demo accounts at a time; CAS also protects each account."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        LEASES.update_one({'_id': LEASE_ID}, {'$setOnInsert': {'owner': '', 'expires_at': now}}, upsert=True)
    except DuplicateKeyError:
        pass
    return LEASES.update_one(
        {'_id': LEASE_ID, '$or': [
            {'owner': OWNER}, {'expires_at': {'$lte': now}},
        ]}, {'$set': {'owner': OWNER, 'expires_at': now + timedelta(seconds=45)}}
    ).modified_count == 1


def tick(*, do_scan: bool = False, quote_provider=None, selector=None) -> dict:
    """Single sequential pass. Selector injected for testability; zero live execution path."""
    from app import trading_engine as engine
    if quote_provider is None:
        # Compatibilidad con pruebas históricas: el servicio real NO llama tick().
        from app.hyperliquid_client import get_best_bid_ask
        quote_provider = get_best_bid_ask

    accounts = list(ledger.COLLECTION.find({'$or': [{'enabled': True}, {'position_open': True}]}))
    result = {'accounts': len(accounts), 'closed_or_marked': 0, 'closed': 0, 'opened': 0, 'scan_attempted': False}
    cache: dict[str, tuple[float, float]] = {}

    def quote(symbol: str) -> tuple[float, float]:
        symbol = symbol.upper()
        if symbol not in cache:
            cache[symbol] = quote_provider(symbol)
        return cache[symbol]

    for account in accounts:
        position = account.get('position')
        if not position:
            continue
        try:
            updated = update_position(account, *quote(str(position['symbol'])))
            if updated:
                ledger.save(account, updated)
                result['closed_or_marked'] += 1
                if not updated.get('position'):
                    result['closed'] += 1
        except Exception:
            logger.exception('DEMO mark/close failed user=%s', account['_id'])

    if not do_scan:
        return result
    eligible = [account for account in ledger.COLLECTION.find({'enabled': True, 'position': None})
                if entry_gate_reason(account) is None and user_ready(int(account['_id']))]
    if not eligible:
        return result
    result['scan_attempted'] = True
    # LIVE selecciona una señal por usuario. Nunca copiar una decisión de un usuario a otro.
    for account in eligible:
        uid = int(account['_id'])
        try:
            selected = (selector(uid) if selector is not None else
                        engine._select_best_signal_from_scanner_shortlist(uid, set(), execution_mode='demo'))
            if not selected:
                continue
            bid, ask = quote(str(selected['symbol']))
            current = ledger.read(uid)
            if not current or entry_gate_reason(current) is not None or not user_ready(uid):
                continue
            updated = open_position(current, selected, bid, ask)
            if updated:
                ledger.save(current, updated)
                result['opened'] += 1
        except ledger.Conflict:
            logger.info('DEMO concurrent update user=%s', uid)
        except Exception:
            logger.exception('DEMO scanner/entry failed user=%s', uid)
    return result


# La función tick(...) se conserva solo para pruebas históricas. El entrypoint
# de producción siguiente nunca la llama: consume eventos WS, no REST ticks.
HL_WS_URL = os.getenv('HYPERLIQUID_WS_URL', 'wss://api.hyperliquid.xyz/ws')


class DemoMarketRunner:
    """Single leader; recibe book, procesa SL/TP antes que nuevas entradas."""

    def __init__(self):
        self.subscribed: set[str] = set()
        # user_id -> (selected_signal, monotonically-ordered queue timestamp)
        self.pending_by_user: dict[int, tuple[dict, float]] = {}
        self.scan_offset = 0
        self.next_scan = time.monotonic()  # scanner es candle-driven, no polling de precios
        self.next_lease = time.monotonic()

    def protected_positions(self, symbol: str, bid: float, ask: float) -> tuple[int, int]:
        marked = closed = 0
        for account in ledger.COLLECTION.find({'position.symbol': symbol}):
            try:
                updated = update_position(account, bid, ask)
                if not updated:
                    continue
                if not updated.get('position'):
                    # Cada evento se valida: jamás saltar un tick con posible TP/SL.
                    ledger.save(account, updated)
                    closed += 1
                else:
                    # No escribimos cada book a Mongo: un precio mueve la UI
                    # mediante WS, y un cambio material queda registrado.
                    before = account['position'].get('mark_price')
                    mark = updated['position'].get('mark_price')
                    if not before or abs(float(mark) / float(before) - 1) >= .0005:
                        ledger.save(account, updated)
                        marked += 1
            except ledger.Conflict:
                logger.info('DEMO book concurrent state symbol=%s', symbol)
            except Exception:
                logger.exception('DEMO book handling failed symbol=%s', symbol)
        return marked, closed

    def open_if_pending(self, symbol: str, bid: float, ask: float) -> int:
        opened = 0
        now = time.monotonic()
        # Consume únicamente las señales del símbolo recibido y de su propietario.
        for uid, (selected, issued_at) in list(self.pending_by_user.items()):
            if str(selected.get('symbol', '')).upper() != symbol:
                continue
            self.pending_by_user.pop(uid, None)
            # Una señal que esperó demasiado no equivale a la ejecución LIVE inmediata.
            if now - issued_at > 30:
                logger.info('DEMO señal caducada user=%s symbol=%s', uid, symbol)
                continue
            try:
                current = ledger.read(uid)
                if not current or entry_gate_reason(current) is not None or not user_ready(uid):
                    continue
                updated = open_position(current, selected, bid, ask)
                if updated:
                    ledger.save(current, updated)
                    opened += 1
            except ledger.Conflict:
                logger.info('DEMO concurrent open avoided user=%s', uid)
            except Exception:
                logger.exception('DEMO open failed user=%s', uid)
        return opened

    def collect_signals(self) -> tuple[dict[int, tuple[dict, float]], int]:
        """Blocking strategy evaluation; never executed on the WebSocket event loop."""
        from app import trading_engine as engine
        eligible = sorted(
            (a for a in ledger.COLLECTION.find({'enabled': True, 'position': None})
             if entry_gate_reason(a) is None and user_ready(int(a['_id']))),
            key=lambda a: int(a['_id']),
        )
        if not eligible:
            return {}, self.scan_offset
        start = self.scan_offset % len(eligible)
        pool = [eligible[(start + i) % len(eligible)] for i in range(min(len(eligible), DEMO_MAX_USERS_PER_SCAN))]
        next_offset = (start + len(pool)) % len(eligible)
        if len(eligible) > len(pool):
            logger.warning('DEMO scanner rotativo: %s/%s usuarios en este ciclo', len(pool), len(eligible))
        pending: dict[int, tuple[dict, float]] = {}
        for account in pool:
            uid = int(account['_id'])
            try:
                # Una decisión independiente por usuario; scanner y router LIVE.
                selected = engine._select_best_signal_from_scanner_shortlist(uid, set(), execution_mode='demo')
                if selected and selected.get('symbol'):
                    pending[uid] = (selected, time.monotonic())
            except Exception:
                logger.exception('DEMO scanner/router error user=%s', uid)
        return pending, next_offset

    def select_signal(self) -> None:
        """Adaptador síncrono para pruebas; producción usa collect_signals en thread."""
        self.pending_by_user, self.scan_offset = self.collect_signals()

    async def subscribe_new(self, ws) -> None:
        wanted = {str(a['position']['symbol']).upper()
                  for a in ledger.COLLECTION.find({'position_open': True}) if a.get('position')}
        # Solo símbolos de señales frescas. Un WS sin cotización NO puede abrir.
        now = time.monotonic()
        self.pending_by_user = {uid: p for uid, p in self.pending_by_user.items() if now - p[1] <= 30}
        wanted.update(str(selected['symbol']).upper() for selected, _ in self.pending_by_user.values())
        # Protección de límites de upstream: si se supera el máximo, abortar
        # para no fingir que el motor puede gestionar todos los SL.
        if len(wanted) > 100:
            raise RuntimeError('Demasiados símbolos DEMO para una conexión WS')
        for symbol in sorted(wanted - self.subscribed):
            await ws.send(json.dumps({'method': 'subscribe', 'subscription': {'type': 'l2Book', 'coin': symbol}}))
        for symbol in sorted(self.subscribed - wanted):
            await ws.send(json.dumps({'method': 'unsubscribe', 'subscription': {'type': 'l2Book', 'coin': symbol}}))
        self.subscribed = wanted

    async def run_socket(self):
        from websockets import connect
        if not HL_WS_URL.startswith('wss://'):
            raise RuntimeError('HYPERLIQUID_WS_URL debe utilizar wss://')
        async with connect(HL_WS_URL, ping_interval=20, ping_timeout=20, max_size=2**20) as ws:
            self.subscribed = set()
            self.pending_by_user.clear()
            self.next_scan = time.monotonic()
            self.next_lease = time.monotonic()
            self.next_ping = time.monotonic() + 25
            self.next_status = time.monotonic()
            if not await asyncio.to_thread(acquire_lease):
                raise RuntimeError('La réplica DEMO no es el líder')
            await self.subscribe_new(ws)
            scan_task = None
            while True:
                now = time.monotonic()
                if now >= self.next_lease:
                    if not await asyncio.to_thread(acquire_lease):
                        raise RuntimeError('Lease perdido: se detiene gestión DEMO')
                    self.next_lease = now + 15
                # Publicar latidos del worker DEMO; no publicar nombres LIVE antiguos.
                if now >= self.next_status:
                    for component in ('demo_worker', 'demo_scanner'):
                        await asyncio.to_thread(touch_runtime_component, component, 'online',
                            metadata={'mode': 'DEMO', 'transport': 'hyperliquid_ws', 'leader': True,
                                      'subscribed_symbols': len(self.subscribed)})
                    self.next_status = time.monotonic() + 45
                # Hyperliquid cierra las conexiones sin actividad; mantener
                # alive incluso si todavía no hay símbolos con posiciones.
                if now >= self.next_ping:
                    await ws.send(json.dumps({'method': 'ping'}))
                    self.next_ping = time.monotonic() + 25
                # El scanner puede tardar muchos segundos. Jamás bloquear el
                # procesamiento de book, SL/TP y heartbeats mientras evalúa.
                if scan_task is not None and scan_task.done():
                    try:
                        self.pending_by_user, self.scan_offset = scan_task.result()
                    except Exception:
                        logger.exception('Scanner DEMO failed; no new entries')
                        self.pending_by_user.clear()
                    scan_task = None
                    await self.subscribe_new(ws)
                if now >= self.next_scan:
                    if scan_task is None:
                        scan_task = asyncio.create_task(asyncio.to_thread(self.collect_signals))
                    else:
                        logger.warning('DEMO scanner anterior sigue en curso; se omite duplicado')
                    self.next_scan = time.monotonic() + DEMO_SCAN_SECONDS
                timeout = min(self.next_scan, self.next_lease, self.next_ping, self.next_status) - time.monotonic()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(.01, timeout))
                except asyncio.TimeoutError:
                    continue  # solo timer de scanner/lease, NO polling de mercado
                book = decode_book(raw)
                if not book:
                    continue
                symbol, bid, ask = book
                if symbol not in self.subscribed:
                    continue
                _, closed = await asyncio.to_thread(self.protected_positions, symbol, bid, ask)
                opened = await asyncio.to_thread(self.open_if_pending, symbol, bid, ask)
                if closed or opened:
                    logger.info('DEMO WS symbol=%s opened=%s closed=%s', symbol, opened, closed)
                    await self.subscribe_new(ws)


async def async_main() -> None:
    if not DEMO_ENABLED:
        raise SystemExit('DEMO_ENABLED=false')
    if os.getenv('DEMO_WORKER_MODE', '').lower() != 'true':
        raise SystemExit('DEMO_WORKER_MODE=true requerido en servicio worker dedicado')
    ledger.COLLECTION.create_index('enabled')
    ledger.COLLECTION.create_index('position.symbol')
    logger.info('DEMO WebSocket event-driven; no LIVE execution; private keys never loaded')
    delay = 1
    while True:
        try:
            await DemoMarketRunner().run_socket()
            delay = 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('DEMO WS disconnected/leader unavailable; no fills while disconnected')
            await asyncio.sleep(delay)  # tiempo de reconexión, no pricing polling
            delay = min(delay * 2, 30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
