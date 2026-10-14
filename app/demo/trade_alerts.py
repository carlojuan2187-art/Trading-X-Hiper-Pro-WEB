"""Avisos de operaciones DEMO a Telegram a través del WEB/API existente.

El worker carece del token del bot. La cola persistente Mongo desacopla el
trading del envío. Se entrega como mínimo una vez; un crash después de enviar
pero antes de confirmar puede causar un duplicado excepcional.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.database import db

logger = logging.getLogger(__name__)
OUTBOX = db['telegram_trade_outbox']


def _utcnow():
    return datetime.now(timezone.utc)


def _number(value):
    try:
        return f'{float(value):.6f}'.rstrip('0').rstrip('.')
    except (TypeError, ValueError, OverflowError):
        return '—'


def _trade_text(trade: dict, event: str) -> str:
    symbol = str(trade.get('symbol') or '?')[:30]
    direction = str(trade.get('direction') or '?').upper()[:10]
    strategy = str(trade.get('strategy_id') or '—')[:64]
    if event == 'OPEN':
        return '\n'.join([
            '🟢 HYPER LEAGUE — DEMO | Operación abierta',
            f'Par: {symbol} | Dirección: {direction}',
            f'Estrategia: {strategy}',
            f'Entrada: {_number(trade.get("entry_price"))}',
            f'Cantidad: {_number(trade.get("quantity"))}',
            f'Margen: {_number(trade.get("margin_usdc"))} USDC | Apalancamiento: {trade.get("leverage", "—")}x',
            f'SL: {_number(trade.get("stop_price"))} | TP: {_number(trade.get("target_price"))}',
            'Modo de prueba: sin órdenes reales.',
        ])
    pnl = float(trade.get('pnl_net_usdc') or 0)
    return '\n'.join([
        ('✅' if pnl > 0 else '❌') + ' HYPER LEAGUE — DEMO | Operación cerrada',
        f'Par: {symbol} | Dirección: {direction}',
        f'Estrategia: {strategy}',
        f'Entrada: {_number(trade.get("entry_price"))} | Salida: {_number(trade.get("exit_price"))}',
        f'Motivo: {str(trade.get("close_reason") or "—")[:24]}',
        f'PnL neto: {pnl:+.6f} USDC (comisiones incluidas)',
        'Modo de prueba: sin órdenes reales.',
    ])


def queue_transition(previous: dict, updated: dict) -> bool:
    """Cola idempotente tras un save CAS exitoso; notifica sólo OPEN/CLOSE."""
    before = previous.get('position') or None
    after = updated.get('position') or None
    event = 'OPEN' if (not before and after) else 'CLOSE' if (before and not after) else None
    if not event:
        return False
    trade = after if event == 'OPEN' else (
        (updated.get('history') or [])[-1] if updated.get('history') else None
    )
    if not trade or (event == 'CLOSE' and trade.get('id') != before.get('id')):
        return False
    uid = int(updated['_id'])
    trade_id = str(trade.get('id') or '')
    if uid <= 0 or not trade_id:
        return False
    now = _utcnow()
    document = {
        '_id': f'demo:{uid}:{trade_id}:{event.lower()}',
        'chat_id': uid, 'event': event, 'text': _trade_text(trade, event),
        'status': 'pending', 'attempts': 0, 'next_attempt': now,
        'created_at': now, 'trade_id': trade_id,
    }
    # No overwrite tras envío: la persistencia es la garantía anti duplicados.
    OUTBOX.update_one({'_id': document['_id']}, {'$setOnInsert': document}, upsert=True)
    return True


def ensure_indexes() -> None:
    OUTBOX.create_index([('status', 1), ('next_attempt', 1)])
    OUTBOX.create_index([('status', 1), ('locked_until', 1)])


def _claim_next():
    from pymongo import ReturnDocument
    now = _utcnow()
    return OUTBOX.find_one_and_update(
        {'$or': [
            {'status': 'pending', 'next_attempt': {'$lte': now}},
            {'status': 'sending', 'locked_until': {'$lte': now}},
        ]},
        {'$set': {'status': 'sending', 'locked_until': now + timedelta(seconds=90)},
         '$inc': {'attempts': 1}},
        sort=[('created_at', 1)], return_document=ReturnDocument.AFTER,
    )


def _mark_sent(identifier):
    OUTBOX.update_one({'_id': identifier, 'status': 'sending'},
                      {'$set': {'status': 'sent', 'sent_at': _utcnow()},
                       '$unset': {'locked_until': ''}})


def _mark_failure(identifier, attempts: int, forbidden: bool):
    if forbidden or attempts >= 8:
        OUTBOX.update_one({'_id': identifier, 'status': 'sending'},
                          {'$set': {'status': 'undeliverable', 'failed_at': _utcnow(),
                                    'failure': 'bot_blocked' if forbidden else 'retry_exhausted'},
                           '$unset': {'locked_until': ''}})
    else:
        delay = min(3600, 15 * 2 ** min(attempts, 8))
        OUTBOX.update_one({'_id': identifier, 'status': 'sending'},
                          {'$set': {'status': 'pending', 'next_attempt': _utcnow() + timedelta(seconds=delay)},
                           '$unset': {'locked_until': ''}})


async def dispatch_batch(bot, limit: int = 10) -> int:
    from telegram.error import Forbidden, TelegramError
    count = 0
    for _ in range(limit):
        message = await asyncio.to_thread(_claim_next)
        if not message:
            break
        try:
            await bot.send_message(chat_id=int(message['chat_id']),
                                   text=message['text'])
        except Forbidden:
            logger.warning('Telegram blocked chat; no alert delivered (user=%s)', message['chat_id'])
            await asyncio.to_thread(_mark_failure, message['_id'], message['attempts'], True)
        except TelegramError as exc:
            logger.warning('Telegram trade alert retry user=%s type=%s', message['chat_id'], type(exc).__name__)
            await asyncio.to_thread(_mark_failure, message['_id'], message['attempts'], False)
        except Exception:
            logger.exception('Unexpected Telegram notification failure user=%s', message['chat_id'])
            await asyncio.to_thread(_mark_failure, message['_id'], message['attempts'], False)
        else:
            await asyncio.to_thread(_mark_sent, message['_id'])
            count += 1
    return count


async def dispatch_loop(bot) -> None:
    indexed = False
    while True:
        try:
            if not indexed:
                await asyncio.to_thread(ensure_indexes)
                indexed = True
            await dispatch_batch(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception('DEMO Telegram outbox temporarily unavailable; retrying')
        await asyncio.sleep(4)
