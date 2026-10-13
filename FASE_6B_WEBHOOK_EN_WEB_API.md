# Hyper League WEB — Bot Telegram dentro de WEB/API

## Problema resuelto

La autenticación telefónica de Fase 6 abría `t.me/<bot>?start=verify_...`, pero WEB/API no tenía un receptor de actualizaciones Telegram. Se había sugerido ejecutar `AUTH-BOT` por polling, lo cual añadía un servicio y competiría con `main.py` cuando este también usara polling. No es necesario.

## Nueva arquitectura (sin un AUTH-BOT)

- El **único** servicio WEB/API aloja interfaz, API, autorización telefónica y **webhook Telegram** en `POST /api/v1/telegram/webhook`.
- En el arranque WEB/API verifica el bot con `getMe`, comprueba que `BOT_USERNAME` coincide con el token, inicia los handlers existentes (incluyendo `/start`, contacto, menús y callbacks) y registra el webhook con `setWebhook` y `secret_token`.
- El servicio WEB no ejecuta el trading loop y no usa `getUpdates`/polling.
- El worker DEMO puede seguir siendo el segundo servicio de aplicación. Para LIVE hay que separar el motor de `main.py` antes de habilitarlo: `main.py` contiene un segundo consumidor de mensajes Telegram por polling y no debe arrancarse simultáneamente.

## Railway: WEB/API

Build Command: `bash build_web.sh`

Start Command: `bash start_web.sh`

**Ninguna nueva variable requerida.** Deben existir estas variables **ya configuradas**:

- `TELEGRAM_BOT_TOKEN`: token actualizado después de su exposición anterior en logs.
- `BOT_USERNAME`: usuario del bot sin `@`; debe coincidir con el token. La aplicación fallará al arrancar si son distintos.
- `WEB_BASE_URL`: `https://trading-x-hiper-pro-web-production.up.railway.app`.
- `WEBAPP_SESSION_SECRET`: secreto aleatorio, privado, persistente, de al menos 32 caracteres. Del mismo se deriva un secreto distinto para el webhook mediante HMAC-SHA256 (no se envía como contraseña ni sale en logs).
- `MONGO_URL`, `MONGO_DB_NAME`, `ADMIN_TELEGRAM_ID`: variables existentes.

Las variables antiguas `TELEGRAM_OIDC_*` no participan en el nuevo login telefónico y no hace falta modificarlas para el webhook.

**No desplegar** `python -m app.auth.bot_runner` ni `python main.py` con el mismo bot mientras se utilice el webhook WEB. Telegram tiene un solo destino de actualizaciones por bot: un proceso `run_polling()` puede eliminar/interferir con el webhook.

## Comprobación

1. Subir los archivos modificados a sus **rutas exactas** o el ZIP completo y desplegar WEB/API.
2. Buscar en logs `Telegram webhook listo en /api/v1/telegram/webhook (sin polling ni trading loop)`.
3. Abrir registro telefónico en la web, completar teléfono/contraseña y pulsar el enlace del bot.
4. En Telegram pulsar Start; debe aparecer `Compartir mi número con Hyper League`.
5. Compartir *el propio contacto* y volver a la web. La confirmación de navegador por WebSocket depende de MongoDB Change Streams; también hay confirmación manual si el socket se corta.
6. Si no aparece la línea de webhook listo, revisar el error de arranque, la coincidencia `BOT_USERNAME`/token y que no exista otro consumidor de actualizaciones.

## Seguridad y límites

- Webhook HTTPS con `X-Telegram-Bot-Api-Secret-Token`, comparación en tiempo constante, JSON validado y límite de payload.
- Las actualizaciones no autenticadas son rechazadas antes de invocar handlers.
- No registrar el cuerpo de las actualizaciones ni secretos. `httpx/httpcore` deshabilitados para impedir que imprima URLs con token.
- Las claves privadas de exchange y estrategias no se tocan.
- El secreto del browser que autoriza completar un registro **nunca** se envía al bot de Telegram.
- Mantener WEB/API con **una réplica** para la primera validación. La `user_data` temporal del bot (relación entre `/start` y contacto) es local al proceso; si se reinicia entre ambos pasos habrá que repetir `/start`. Antes de escalar réplicas hay que persistir dicha relación o usar un almacenamiento compartido.
- No probado aún en Railway real ni con BotFather/Telegram en este entorno: sólo sintaxis Python y pruebas offline de seguridad.

## Lista exacta — Fase 6B

**Modificados (3):**

- `app/api/app_factory.py`
- `app/bot.py`
- `app/auth/telegram_contact.py`

**Nuevos (5):**

- `app/api/routes_telegram_webhook.py`
- `app/api/telegram_webhook_runtime.py`
- `app/api/telegram_webhook_security.py`
- `tools/test_telegram_webhook_security_offline.py`
- `FASE_6B_WEBHOOK_EN_WEB_API.md`

**Eliminados: 0.** `app/auth/bot_runner.py` permanece por compatibilidad pero **NO se ejecuta**.
