"""Telegram: verificar solamente el contacto propio enviado en chat privado."""
from __future__ import annotations

import re

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes

from app.auth.phone_login import confirm_contact, FLOWS
from datetime import datetime, timezone

FLOW_PATTERN = re.compile(r'^verify_([A-Za-z0-9_-]{20,48})$')


async def start_phone_verification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat.type != 'private' or not update.message:
        return False
    arg = context.args[0] if context.args else ''
    match = FLOW_PATTERN.fullmatch(arg)
    if not match:
        return False
    flow_id = match.group(1)
    pending = FLOWS.find_one({'_id': flow_id, 'state': 'pending',
                              'expires_at': {'$gt': datetime.now(timezone.utc)}})
    if not pending:
        await update.message.reply_text('La solicitud venció. Regresa a la web y comienza otra vez.')
        return True
    context.user_data['phone_auth_flow_id'] = flow_id
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton('📱 Compartir mi número con Hyper League', request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )
    await update.message.reply_text(
        'Hyper League — Verificación del teléfono\n\n'
        'Pulsa el botón para compartir TU PROPIO contacto de Telegram. '
        'El número debe coincidir con el que escribiste en la página web.\n\n'
        'Nunca te pediremos tu contraseña ni claves privadas por este chat.',
        reply_markup=keyboard,
    )
    return True


async def verify_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != 'private' or not update.message:
        return
    flow_id = context.user_data.get('phone_auth_flow_id')
    if not flow_id:
        await update.message.reply_text('Primero solicita la verificación desde la plataforma web.')
        return
    contact = update.message.contact
    actor = update.effective_user
    if not contact or not actor or int(contact.user_id or 0) != int(actor.id):
        await update.message.reply_text('Debes compartir tu propio número con el botón de Telegram, no otro contacto.')
        return
    outcome = confirm_contact(flow_id, actor.id, contact.phone_number)
    if outcome == 'ok':
        context.user_data.pop('phone_auth_flow_id', None)
        await update.message.reply_text('✅ Número confirmado. Regresa a Hyper League para continuar.',
                                        reply_markup=ReplyKeyboardRemove())
    elif outcome == 'mismatch':
        await update.message.reply_text('❌ El número de tu Telegram no coincide con el registrado en la web. Corrige el número en la página y comienza nuevamente.')
    elif outcome == 'different_account':
        await update.message.reply_text('❌ Este número o cuenta Telegram ya está asociado a otra cuenta. Contacta al soporte.')
    else:
        context.user_data.pop('phone_auth_flow_id', None)
        await update.message.reply_text('La solicitud venció. Inicia otra verificación desde la web.',
                                        reply_markup=ReplyKeyboardRemove())
