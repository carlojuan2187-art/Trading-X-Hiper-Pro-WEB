"""Recepción Telegram Bot API en el MISMO servicio WEB/API.

No ejecuta un segundo servicio, polling ni trading loop.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from telegram import Update

from app.api.telegram_webhook_security import (
    MAX_UPDATE_BYTES, decode_update, valid_webhook_secret,
)

router = APIRouter(tags=['telegram-webhook'])


@router.post('/api/v1/telegram/webhook', include_in_schema=False)
async def telegram_webhook(request: Request) -> JSONResponse:
    expected = getattr(request.app.state, 'telegram_webhook_secret', None)
    received = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if not expected or not valid_webhook_secret(received, expected):
        raise HTTPException(status_code=403, detail='Webhook no autorizado')
    if request.headers.get('content-type', '').split(';')[0].lower() != 'application/json':
        raise HTTPException(status_code=415, detail='Tipo de contenido inválido')
    # Límite también para transferencias chunked, no solo Content-Length.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPDATE_BYTES:
            raise HTTPException(status_code=413, detail='Actualización demasiado grande')
    try:
        doc = decode_update(bytes(data))
    except ValueError:
        raise HTTPException(status_code=400, detail='Actualización inválida') from None

    bot_application = getattr(request.app.state, 'telegram_application', None)
    if bot_application is None:
        raise HTTPException(status_code=503, detail='Bot temporalmente no disponible')
    update = Update.de_json(doc, bot_application.bot)
    if update is None:
        raise HTTPException(status_code=400, detail='Actualización inválida')
    await bot_application.process_update(update)
    return JSONResponse({'ok': True})
