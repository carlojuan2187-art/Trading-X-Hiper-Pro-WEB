"""Inicio y parada del bot Telegram dentro del ciclo de vida FastAPI.

Al arrancar registra el webhook oficial; no usa getUpdates/polling.
"""
from __future__ import annotations

import logging
import asyncio

from telegram.ext import ApplicationBuilder

from app.api.telegram_webhook_security import derive_webhook_secret
from app.config import BOT_USERNAME, TELEGRAM_BOT_TOKEN, WEBAPP_SESSION_SECRET, WEB_BASE_URL

logger = logging.getLogger(__name__)
WEBHOOK_PATH = '/api/v1/telegram/webhook'


async def start_telegram_webhook(app) -> None:
    if not WEB_BASE_URL.startswith('https://') or not TELEGRAM_BOT_TOKEN:
        raise RuntimeError('Telegram webhook requiere WEB_BASE_URL HTTPS y TELEGRAM_BOT_TOKEN')
    secret = derive_webhook_secret(WEBAPP_SESSION_SECRET)
    # httpx y httpcore podrían imprimir el token dentro de URLs del Bot API.
    logging.getLogger('httpx').disabled = True
    logging.getLogger('httpcore').disabled = True

    from app.bot import register_bot_handlers

    tg = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    register_bot_handlers(tg)
    await tg.initialize()
    try:
        identity = await tg.bot.get_me()
        expected_username = BOT_USERNAME.lstrip('@')
        if not expected_username or identity.username.casefold() != expected_username.casefold():
            raise RuntimeError('BOT_USERNAME no coincide con TELEGRAM_BOT_TOKEN')
        await tg.start()
        result = await tg.bot.set_webhook(
            url=f'{WEB_BASE_URL}{WEBHOOK_PATH}',
            secret_token=secret,
            allowed_updates=['message', 'callback_query'],
            drop_pending_updates=False,
            max_connections=10,
        )
        if not result:
            raise RuntimeError('Telegram no confirmó setWebhook')
    except Exception:
        if tg.running:
            await tg.stop()
        await tg.shutdown()
        raise
    app.state.telegram_application = tg
    app.state.telegram_webhook_secret = secret
    # Heartbeat REAL del webhook dentro de WEB/API (no arrancar polling).
    async def _webhook_runtime_heartbeat():
        from app.database import touch_runtime_component
        while getattr(app.state, 'telegram_application', None) is tg and tg.running:
            try:
                await asyncio.to_thread(touch_runtime_component, 'telegram_bot',
                    'online', metadata={'mode': 'webhook', 'execution_mode': 'WEB', 'path': WEBHOOK_PATH})
            except Exception:
                logger.warning('No se pudo actualizar heartbeat Telegram webhook; se reintentará')
            await asyncio.sleep(50)

    app.state.telegram_heartbeat_task = asyncio.create_task(_webhook_runtime_heartbeat())
    logger.info('Telegram webhook listo en %s (sin polling ni trading loop)', WEBHOOK_PATH)


async def stop_telegram_webhook(app) -> None:
    tg = getattr(app.state, 'telegram_application', None)
    task = getattr(app.state, 'telegram_heartbeat_task', None)
    app.state.telegram_heartbeat_task = None
    app.state.telegram_application = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    app.state.telegram_webhook_secret = None
    if tg is not None:
        if tg.running:
            await tg.stop()
        await tg.shutdown()
    # No borrar el webhook remoto: Telegram reintentará durante un redeploy.
