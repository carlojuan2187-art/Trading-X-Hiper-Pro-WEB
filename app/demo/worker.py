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
from app.hyperliquid_client import norm_coin


def _ws_coin(symbol: str) -> str:
    """Hyperliquid l2Book uses the exchange coin, not our -PERP display symbol."""
    return norm_coin(symbol)



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
        # In-memory full-resolution extrema; flush at most every 30s or on
        # material mark movement. Always merge into the final close snapshot.
        self.audit_pending: dict[tuple[int, str], dict] = {}
        self.audit_flush_at: dict[tuple[int, str], float] = {}
        self.next_scan = time.monotonic()  # scanner es candle-driven, no polling de precios
        self.next_lease = time.monotonic()

    @staticmethod
    def trace_trade(event: str, uid: int, position: dict | None, **extra) -> None:
        """Structured, per-trade Railway trace. Never log credentials or user secrets."""
        p = position or {}
        fields = (
            'id', 'symbol', 'direction', 'strategy_id', 'regime_id',
            'router_decision', 'signal_score', 'scanner_score',
            'signal_reference_price', 'entry_price', 'quantity',
            'stop_price', 'initial_stop_price', 'target_price', 'rr',
            'margin_usdc', 'notional_usdc', 'leverage', 'opened_at',
            'mark_price', 'unrealized_pnl_usdc', 'demo_be_enabled',
            'demo_be_armed', 'demo_be_stop_price', 'demo_be_trigger_r',
            'audit_quotes', 'audit_mfe_pct', 'audit_mae_pct',
            'audit_max_quote_gap_seconds', 'audit_quote_gaps_over_10s',
            'close_reason', 'closed_at', 'exit_price', 'pnl_gross_usdc',
            'pnl_net_usdc', 'entry_fee_usdc', 'exit_fee_usdc',
            'audit_exit_vs_stop_pct',
        )
        payload = {k: p.get(k) for k in fields if k in p}
        payload.update(extra)
        payload['event_at'] = datetime.now(timezone.utc).isoformat()
        logger.info('DEMO_TRADE_TRACE event=%s user=%s trade_id=%s symbol=%s data=%s',
                    event, uid, p.get('id', ''), p.get('symbol', ''),
                    json.dumps(payload, sort_keys=True, default=str))

    def protected_positions(self, symbol: str, bid: float, ask: float) -> tuple[int, int]:
        marked = closed = 0
        coin = _ws_coin(symbol)
        if not coin:
            return marked, closed
        # Existing DEMO positions retain their original symbol for UI/history.
        # Match both current -PERP and legacy plain-coin records without a collection scan.
        symbol_query = {'$or': [
            {'position.symbol': coin},
            {'position.symbol': f'{coin}-PERP'},
            {'position.symbol': f'{coin}_PERP'},
        ]}
        for account in ledger.COLLECTION.find(symbol_query):
            key = (int(account['_id']), str(account['position'].get('id') or ''))
            audit_fields = (
                'audit_mfe_pct', 'audit_mae_pct', 'audit_mfe_usdc',
                'audit_mae_usdc', 'audit_quotes', 'audit_last_quote_at',
                'audit_max_quote_gap_seconds', 'audit_quote_gaps_over_10s',
            )
            try:
                snapshot = deepcopy(account)
                if key in self.audit_pending:
                    snapshot['position'].update(self.audit_pending[key])
                updated = update_position(snapshot, bid, ask)
                if not updated:
                    continue
                measured_position = updated.get('position')
                # Every received, valid WS quote is traceable to a trade id.
                # Log BEFORE persistence, including the quote that triggers SL/TP.
                traced = measured_position or updated['history'][-1]
                self.trace_trade('QUOTE' if measured_position else 'TRIGGER',
                                 int(account['_id']), traced,
                                 ws_coin=coin, bid=bid, ask=ask,
                                 spread_bps=round((ask / bid - 1) * 10000, 4),
                                 executable_side='bid' if traced.get('direction') == 'long' else 'ask',
                                 quote_sequence=int(traced.get('audit_quotes') or 0),
                                 quote_gap_seconds=round(
                                     max(0.0, (datetime.now(timezone.utc) -
                                         datetime.fromisoformat(str(
                                             (account['position'].get('audit_last_quote_at')
                                              or account['position'].get('opened_at'))))).total_seconds()), 3)
                                     if (account['position'].get('audit_last_quote_at')
                                         or account['position'].get('opened_at')) else None)
                if not measured_position:
                    # Closed history includes unsaved in-memory extremes and
                    # latest gap counters from EVERY observed WS quote.
                    saved = ledger.save(account, updated)
                    self.audit_pending.pop(key, None)
                    self.audit_flush_at.pop(key, None)
                    trade = saved['history'][-1]
                    logger.info('DEMO_TRADE_AUDIT_CLOSE user=%s data=%s',
                                account['_id'], json.dumps({
                                    k: trade.get(k) for k in (
                                        'id', 'symbol', 'direction', 'strategy_id',
                                        'entry_price', 'exit_price', 'stop_price',
                                        'target_price', 'rr', 'signal_score',
                                        'opened_at', 'closed_at', 'close_reason',
                                        'pnl_gross_usdc', 'pnl_net_usdc',
                                        'entry_fee_usdc', 'exit_fee_usdc',
                                        'audit_mfe_pct', 'audit_mae_pct',
                                        'audit_mfe_usdc', 'audit_mae_usdc',
                                        'audit_quotes', 'audit_max_quote_gap_seconds',
                                        'audit_quote_gaps_over_10s',
                                        'initial_stop_price', 'demo_be_enabled',
                                        'demo_be_armed', 'demo_be_trigger_r',
                                        'demo_be_stop_price', 'demo_be_armed_at',
                                    )
                                }, sort_keys=True, default=str))
                    self.trace_trade('CLOSED', int(account['_id']), trade, bid=bid, ask=ask)
                    closed += 1
                else:
                    # Keep exact WS extrema between Mongo writes. Periodic
                    # persistence bounds loss of telemetry on worker restart.
                    self.audit_pending[key] = {
                        field: measured_position.get(field) for field in audit_fields
                        if field in measured_position
                    }
                    before = account['position'].get('mark_price')
                    mark = measured_position.get('mark_price')
                    due = time.monotonic() - self.audit_flush_at.get(key, 0.0) >= 30.0
                    changed = not before or abs(float(mark) / float(before) - 1) >= .0005
                    be_new = bool(measured_position.get('demo_be_armed')) and not bool(
                        account['position'].get('demo_be_armed'))
                    # Save new protective stop immediately even with a tiny
                    # price movement; do not wait for 30s audit throttle.
                    if due or changed or be_new:
                        ledger.save(account, updated)
                        self.audit_flush_at[key] = time.monotonic()
                        marked += 1
                        if be_new:
                            logger.info('DEMO_BE_ARMED user=%s symbol=%s entry=%.8f '
                                        'original_stop=%.8f be_stop=%.8f trigger_r=%.2f',
                                        account['_id'], symbol,
                                        float(measured_position['entry_price']),
                                        float(measured_position['initial_stop_price']),
                                        float(measured_position['stop_price']),
                                        float(measured_position['demo_be_trigger_r']))
            except ledger.Conflict:
                # Don't retain stale, unsaved telemetry after CAS failure.
                self.audit_pending.pop(key, None)
                logger.info('DEMO book concurrent state symbol=%s', symbol)
            except Exception:
                logger.exception('DEMO book handling failed symbol=%s', symbol)
        return marked, closed

    def open_if_pending(self, symbol: str, bid: float, ask: float) -> int:
        opened = 0
        now = time.monotonic()
        # Compare exchange coins; the strategy keeps its -PERP symbol in Mongo.
        coin = _ws_coin(symbol)
        if not coin:
            return 0
        for uid, (selected, issued_at) in list(self.pending_by_user.items()):
            if _ws_coin(str(selected.get('symbol') or '')) != coin:
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
                reason: dict = {}
                updated = open_position(current, selected, bid, ask, rejection=reason)
                if updated:
                    saved = ledger.save(current, updated)
                    opened += 1
                    pos = saved['position']
                    self.trace_trade('OPENED', uid, pos, bid=bid, ask=ask,
                                     selected_signal={
                                         k: selected.get('signal', {}).get(k)
                                         for k in ('direction', 'score', 'strength', 'strategy_id',
                                                   'signal_reference_price', 'stop_price', 'target_price',
                                                   'sweep_depth_atr', 'bars_since_sweep', 'rr_estimate',
                                                   'adx5', 'atr_pct')
                                     },
                                     shortlist_rank=selected.get('shortlist_rank'),
                                     shortlist_size=selected.get('shortlist_size'))
                    logger.info('DEMO_TRADE_AUDIT_OPEN user=%s data=%s', uid, json.dumps({
                        k: pos.get(k) for k in (
                            'id', 'symbol', 'direction', 'strategy_id',
                            'regime_id', 'router_decision', 'signal_score',
                            'scanner_score', 'signal_reference_price',
                            'entry_price', 'stop_price', 'target_price', 'rr',
                            'margin_usdc', 'leverage', 'notional_usdc',
                            'entry_spread_bps', 'slippage_model_bps',
                            'entry_fee_model_bps', 'entry_fee_usdc',
                            'opened_at', 'initial_stop_price',
                            'demo_be_enabled', 'demo_be_trigger_r',
                        )
                    }, sort_keys=True, default=str))
                else:
                    # Shadow RR evaluation: informational only, never retries or
                    # bypasses the real entry gate. Log only measured entry RR.
                    plan = reason.get('plan') if isinstance(reason, dict) else None
                    if isinstance(plan, dict) and plan.get('reason') == 'RR_TOO_LOW_AFTER_ENTRY':
                        try:
                            measured_rr = float(plan.get('rr') or 0.0)
                            logger.info('DEMO_SHADOW_RR user=%s symbol=%s data=%s',
                                        uid, coin, json.dumps({
                                            'mode': 'OBSERVE_ONLY',
                                            'original': 'REJECTED',
                                            'entry_rr': measured_rr,
                                            'shadow_min_rr_1_0': measured_rr >= 1.0,
                                            'shadow_min_rr_1_1': measured_rr >= 1.1,
                                            'entry_price': plan.get('entry_price'),
                                            'stop_price': plan.get('stop_price'),
                                            'target_price': plan.get('target_price'),
                                            'reason': plan.get('reason'),
                                        }, sort_keys=True))
                        except (TypeError, ValueError, OverflowError):
                            logger.exception('DEMO_SHADOW_RR_INVALID user=%s symbol=%s', uid, coin)
                    logger.info('DEMO_ENTRY_TRACE event=REJECTED user=%s symbol=%s bid=%.10f ask=%.10f signal=%s diag=%s',
                                uid, coin, bid, ask,
                                json.dumps(selected.get('signal') or {}, sort_keys=True, default=str),
                                json.dumps(reason, sort_keys=True, default=str))
                    logger.info('DEMO final entry rejected user=%s symbol=%s diag=%s',
                                uid, coin, json.dumps(reason, sort_keys=True, default=str))
                    # Only a rejected trade-specific execution check can fall
                    # back. Global account/risk gates are never circumvented.
                    fallback_causes = {
                        'SPREAD_TOO_WIDE', 'INVALID_QUOTE', 'SCANNER_BOOK_BELOW_ORDER',
                        'INVALID_SCANNER_BOOK', 'PRICE_DRIFT', 'EXIT_PLAN_REJECTED',
                        'MIN_QUANTITY',
                    }
                    alternatives = list(selected.get('_demo_alternatives') or [])
                    if reason.get('reason') in fallback_causes and alternatives and now - issued_at <= 30:
                        next_choice = dict(alternatives[0])
                        next_choice['_demo_alternatives'] = alternatives[1:]
                        self.pending_by_user[uid] = (next_choice, issued_at)
                        logger.info('DEMO_FALLBACK_ARMED user=%s from=%s to=%s '
                                    'cause=%s remaining=%d',
                                    uid, coin, _ws_coin(next_choice['symbol']),
                                    reason.get('reason'), len(alternatives) - 1)
                    elif alternatives:
                        logger.info('DEMO_FALLBACK_STOPPED user=%s reason=%s',
                                    uid, reason.get('reason'))
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
                # Identical discovery and strategy-ranking gates to LIVE.
                # Only DEMO execution and the isolated fallback queue differ.
                # The real order-side depth is checked at simulated entry.
                selected = engine._select_best_signal_from_scanner_shortlist(
                    uid, set(), execution_mode='demo', demo_fallback_limit=1)
                if selected and selected.get('symbol'):
                    logger.info('DEMO_SIGNAL_TRACE user=%s symbol=%s data=%s', uid,
                                selected['symbol'], json.dumps({
                                    'event_at': datetime.now(timezone.utc).isoformat(),
                                    'symbol': selected.get('symbol'),
                                    'direction': selected.get('direction'),
                                    'regime_id': selected.get('regime_id'),
                                    'router_decision': selected.get('router_decision'),
                                    'shortlist_rank': selected.get('shortlist_rank'),
                                    'shortlist_size': selected.get('shortlist_size'),
                                    'scanner': selected.get('scanner'),
                                    'signal': selected.get('signal'),
                                }, sort_keys=True, default=str))
                    pending[uid] = (selected, time.monotonic())
            except Exception:
                logger.exception('DEMO scanner/router error user=%s', uid)
        return pending, next_offset

    def select_signal(self) -> None:
        """Adaptador síncrono para pruebas; producción usa collect_signals en thread."""
        self.pending_by_user, self.scan_offset = self.collect_signals()

    async def subscribe_new(self, ws) -> None:
        wanted = {_ws_coin(str(a['position']['symbol']))
                  for a in ledger.COLLECTION.find({'position_open': True}) if a.get('position')}
        wanted.discard('')
        # Solo símbolos de señales frescas. Un WS sin cotización NO puede abrir.
        now = time.monotonic()
        self.pending_by_user = {uid: p for uid, p in self.pending_by_user.items() if now - p[1] <= 30}
        wanted.update(_ws_coin(str(selected['symbol'])) for selected, _ in self.pending_by_user.values())
        wanted.discard('')
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
            # Once per successful leader start: bounded read-only 1m replay
            # of the five most recent retained DEMO closes.
            from app.demo.audit import replay_recent_history_to_logs
            asyncio.create_task(asyncio.to_thread(replay_recent_history_to_logs))
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
                # A veto can arm a different coin without opening a position;
                # immediately update WS subscriptions instead of waiting 5m.
                pending_before = {
                    uid: _ws_coin(str(choice.get('symbol') or ''))
                    for uid, (choice, _) in self.pending_by_user.items()
                }
                opened = await asyncio.to_thread(self.open_if_pending, symbol, bid, ask)
                pending_after = {
                    uid: _ws_coin(str(choice.get('symbol') or ''))
                    for uid, (choice, _) in self.pending_by_user.items()
                }
                if closed or opened or pending_before != pending_after:
                    logger.info('DEMO WS symbol=%s opened=%s closed=%s', symbol, opened, closed)
                    await self.subscribe_new(ws)


async def async_main() -> None:
    if not DEMO_ENABLED:
        raise SystemExit('DEMO_ENABLED=false')
    if os.getenv('DEMO_WORKER_MODE', '').lower() != 'true':
        raise SystemExit('DEMO_WORKER_MODE=true requerido en servicio worker dedicado')
    ledger.COLLECTION.create_index('enabled')
    ledger.COLLECTION.create_index('position.symbol')
    from app.config import SCAN_INTERVAL, SCANNER_MIN_TOP_BOOK_NOTIONAL
    from app.demo.settings import DEMO_BREAK_EVEN_ENABLED
    from app.strategies.liquidity_sweep_reversal import SWEEP_MAX_AGE_BARS
    logger.info(
        'DEMO_LIVE_PARITY strategy_context=live scanner_context=live '
        'H1_M15_M5=live liquidity_sweep_max_age_bars=%s '
        'break_even=%s fallback_alternatives=off '
        'scanner_s=%.1f live_scanner_s=%s scanner_book_usdc=%.2f '
        'execution=paper_bid_ask_only',
        SWEEP_MAX_AGE_BARS, DEMO_BREAK_EVEN_ENABLED,
        DEMO_SCAN_SECONDS, SCAN_INTERVAL, SCANNER_MIN_TOP_BOOK_NOTIONAL)
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
