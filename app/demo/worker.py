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

from app.database import db
from app.demo import ledger
from app.demo.settings import DEMO_ENABLED, DEMO_SCAN_SECONDS
from app.demo.credentials import user_ready
from app.demo.market_events import decode_book
from app.demo.simulator import open_position, update_position

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
                if user_ready(int(account['_id']))]
    if not eligible:
        return result
    result['scan_attempted'] = True
    try:
        if selector is None:
            # MISMA rutina scanner/router LIVE, etiquetada DEMO, sin ordenes.
            selected = engine._select_best_signal_from_scanner_shortlist(
                int(eligible[0]['_id']), set(), execution_mode='demo',
            )
        else:
            selected = selector(int(eligible[0]['_id']))
    except Exception:
        logger.exception('DEMO scanner/router failed')
        return result
    if not selected:
        return result
    try:
        bid, ask = quote(str(selected['symbol']))
    except Exception:
        logger.exception('DEMO market quote unavailable symbol=%s', selected['symbol'])
        return result
    for account in eligible:
        try:
            # La CAS fallará sin consecuencias si alguien pausó/reseteó mientras se escaneaba.
            current = ledger.read(account['_id'])
            if not current or not current['enabled'] or current.get('position'):
                continue
            updated = open_position(current, selected, bid, ask)
            if updated:
                ledger.save(current, updated)
                result['opened'] += 1
        except ledger.Conflict:
            logger.info('DEMO update conflict user=%s', account['_id'])
        except Exception:
            logger.exception('DEMO open failed user=%s', account['_id'])
    return result


# La función tick(...) se conserva solo para pruebas históricas. El entrypoint
# de producción siguiente nunca la llama: consume eventos WS, no REST ticks.
HL_WS_URL = os.getenv('HYPERLIQUID_WS_URL', 'wss://api.hyperliquid.xyz/ws')


class DemoMarketRunner:
    """Single leader; recibe book, procesa SL/TP antes que nuevas entradas."""

    def __init__(self):
        self.subscribed: set[str] = set()
        self.pending: dict | None = None
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
        if not self.pending or str(self.pending.get('symbol', '')).upper() != symbol:
            return 0
        selected, self.pending = self.pending, None
        opened = 0
        for account in ledger.COLLECTION.find({'enabled': True, 'position': None}):
            try:
                uid = int(account['_id'])
                if not user_ready(uid):
                    continue
                current = ledger.read(uid)
                if not current or not current.get('enabled') or current.get('position'):
                    continue
                updated = open_position(current, selected, bid, ask)
                if updated:
                    ledger.save(current, updated)
                    opened += 1
            except ledger.Conflict:
                logger.info('DEMO concurrent open avoided')
            except Exception:
                logger.exception('DEMO open failed')
        return opened

    def select_signal(self) -> None:
        from app import trading_engine as engine
        eligible = [a for a in ledger.COLLECTION.find({'enabled': True, 'position': None})
                    if user_ready(int(a['_id']))]
        self.pending = None
        if not eligible:
            return
        # MISMO selector y router LIVE; sin ejecutar órdenes reales.
        self.pending = engine._select_best_signal_from_scanner_shortlist(
            int(eligible[0]['_id']), set(), execution_mode='demo',
        )

    async def subscribe_new(self, ws) -> None:
        wanted = {str(a['position']['symbol']).upper()
                  for a in ledger.COLLECTION.find({'position_open': True}) if a.get('position')}
        if self.pending and self.pending.get('symbol'):
            wanted.add(str(self.pending['symbol']).upper())
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
            self.pending = None
            self.next_scan = time.monotonic()
            self.next_lease = time.monotonic()
            if not await asyncio.to_thread(acquire_lease):
                raise RuntimeError('La réplica DEMO no es el líder')
            await self.subscribe_new(ws)
            while True:
                now = time.monotonic()
                if now >= self.next_lease:
                    if not await asyncio.to_thread(acquire_lease):
                        raise RuntimeError('Lease perdido: se detiene gestión DEMO')
                    self.next_lease = now + 15
                if now >= self.next_scan:
                    try:
                        await asyncio.to_thread(self.select_signal)
                    except Exception:
                        logger.exception('Scanner DEMO failed; without new entries')
                    self.next_scan = time.monotonic() + DEMO_SCAN_SECONDS
                    await self.subscribe_new(ws)
                timeout = min(self.next_scan, self.next_lease) - time.monotonic()
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
    asyncio.run(async_main())


if __name__ == '__main__':
    main()
