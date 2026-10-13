"""Bot de verificación de contactos, sin motor LIVE ni trading loop.

Una sola instancia debe usar polling con el token Telegram. No arrancar en
paralelo con main.py (que también usa polling para el mismo bot).
"""
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from app.auth.phone_login import init_indexes
from app.auth.telegram_contact import start_phone_verification, verify_contact
from app.config import TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO)
# HTTPX registra URLs de Telegram que incluyen el token del bot: no permitirlo.
logging.getLogger('httpx').disabled = True
logging.getLogger('httpcore').disabled = True


async def start(update, context):
    if not await start_phone_verification(update, context):
        await update.message.reply_text('Para verificar tu teléfono, comienza el registro o inicio de sesión desde la plataforma web.')


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError('TELEGRAM_BOT_TOKEN es obligatorio para el bot de verificación')
    init_indexes()
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.CONTACT, verify_contact))
    print('Bot de verificación de teléfono activo (SIN trading LIVE)', flush=True)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
