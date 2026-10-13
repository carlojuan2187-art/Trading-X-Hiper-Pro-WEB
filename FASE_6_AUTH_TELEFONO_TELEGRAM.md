# Fase 6 — Registro / inicio de sesión por teléfono + contraseña + bot Telegram

## Qué se implementó

- Formulario responsive de **Iniciar sesión** y **Registrarse** con selector de países, banderas, prefijos, filtro de búsqueda y campos de teléfono internacional.
- Contraseña y confirmación (en registro), con controles independientes de mostrar/ocultar.
- Registro solo después de que Telegram comparta su **contacto propio** en un chat privado con el bot. Se compara E.164 mediante `phonenumbers` y `contact.user_id == from_user.id`.
- Inicio de sesión exige **teléfono + contraseña** y una nueva verificación por Telegram.
- Contraseñas Argon2id (solo hashes; Telegram nunca ve la contraseña), expiración del desafío de 10 minutos, limitación de intentos, aislamiento del secreto del navegador y verificación de una sola utilización.
- Id del usuario de Telegram como clave interna para conservar wallets, historial y balances DEMO/LIVE. No se crean cuentas nuevas con el número como `user_id`.
- WebSocket `/api/v1/auth/phone/updates/{flow_id}` con MongoDB Change Streams para confirmación inmediata. Como respaldo, botón manual «Ya compartí mi contacto» (una solicitud explícita, sin polling automático).
- El antiguo flujo Mini App por `initData` sigue intacto por compatibilidad. OIDC ya **no aparece en la pantalla**, y no es necesario configurarlo. Para cerrar el endpoint antiguo, mantener `WEB_LOGIN_ENABLED=false`.

## Variables mínimas por servicio en Railway

Conservar los secretos existentes. **Nunca usar claves antiguas del bot o de cifrado dentro de Git.**

### WEB/API (Build `bash build_web.sh`; Start `bash start_web.sh`)

```dotenv
TELEGRAM_BOT_TOKEN=<TOKEN_DEL_BOT>
ADMIN_TELEGRAM_ID=<ID_ADMIN>
MONGO_URL=<MONGODB_ATLAS>
MONGO_DB_NAME=TradingXWEB
BOT_USERNAME=TridiXbot
WEB_BASE_URL=https://trading-x-hiper-pro-web-production.up.railway.app
MINIAPP_ALLOWED_ORIGINS=https://trading-x-hiper-pro-web-production.up.railway.app
WEBAPP_SESSION_SECRET=<CONSERVAR_SECRETO_ACTUAL>
PRIVATE_KEY_ENCRYPTION_SECRET=<CONSERVAR_SECRETO_ACTUAL>
DEMO_ENABLED=true
```

`WEB_LOGIN_ENABLED` puede omitirse o fijarse a `false`. **No se requieren** `TELEGRAM_OIDC_CLIENT_ID`, `TELEGRAM_OIDC_CLIENT_SECRET` ni `TELEGRAM_OIDC_REDIRECT_URI` para iniciar sesión con teléfono.

### AUTH-BOT (Build `pip install -r requirements.txt`; Start `python -m app.auth.bot_runner`)

```dotenv
TELEGRAM_BOT_TOKEN=<MISMO_TOKEN_WEB>
ADMIN_TELEGRAM_ID=<ID_ADMIN>
MONGO_URL=<MISMA_URL_WEB>
MONGO_DB_NAME=TradingXWEB
BOT_USERNAME=TridiXbot
```

No necesita dominio público, ni clave privada de exchange, ni ejecutar `main.py`. **No inicia el motor LIVE.**

**Advertencia importante:** dos procesos `run_polling()` no pueden consumir simultáneamente las actualizaciones del mismo bot. Si tienes activo BOT LIVE u otro bot con el mismo `TELEGRAM_BOT_TOKEN`, detenlo antes de iniciar AUTH-BOT. Para futuro despliegue LIVE, el handler de contacto ya está integrado en `app/bot.py`: cuando se habilite BOT LIVE, detén AUTH-BOT y deja un solo proceso consumidor. La alternativa futura es centralizar la recepción mediante webhook.

### DEMO WORKER (no cambió)

Mantén las mismas variables y el comando previo `python -m app.demo.worker` **después** de verificar el acceso web. No contiene token de bot.

## Prueba manual en staging

1. Sube los archivos al repositorio WEB correcto; despliega WEB/API y AUTH-BOT, dejando BOT LIVE detenido.
2. Comprueba que ambos servicios conectan al mismo clúster y la misma base de datos.
3. Abre `https://trading-x-hiper-pro-web-production.up.railway.app` en Chrome.
4. **Registrarse:** selecciona bandera/prefijo, introduce teléfono y contraseña de mínimo 10 caracteres, confirma contraseña y pulsa Crear cuenta y verificar.
5. Se abre el bot. Pulsa `Start` y después **Compartir mi número**. Comprueba que el chat pertenece al bot oficial.
6. Regresa a la web. Debe continuar automáticamente gracias al WebSocket; si la conexión cayó, pulsa «Ya compartí mi contacto».
7. Cierra sesión. Repite con **Iniciar sesión**: exige teléfono, contraseña y el contacto de Telegram de nuevo.
8. Prueba un número que no coincida, contacto de otro usuario, contraseña errónea, caducidad y reuso del desafío. Deben ser rechazados.
9. Verifica que las wallets, operaciones y registros antiguos vinculados al mismo Telegram ID no se han alterado.

## Alcance y pendientes

- No se ha conectado este código a MongoDB Atlas ni Telegram reales desde el entorno de construcción. Requiere validación de extremo a extremo en Railway.
- Se añadieron `phonenumbers` y `argon2-cffi` a `requirements.txt`; el build de Railway debe poder descargarlos.
- La reconexión al WebSocket depende de Change Streams. Si MongoDB no los ofrece, el botón de confirmación manual continúa funcionando.
- El catálogo frontend incluye 213 países/regiones con prefijos. La validación real del número la realiza el servidor.
- **No se implementó todavía recuperación de contraseña**. Para producción pública recomendamos un flujo de restablecimiento con verificación de Telegram e invalidación de sesiones.
- El inicio de sesión antigua Mini App sigue funcionando por compatibilidad. Las rutas OIDC antiguas quedan en el repositorio, pero no se utilizan en esta pantalla y están deshabilitadas si `WEB_LOGIN_ENABLED=false`.
- La clave privada para Hyperliquid no participa en la autenticación telefónica ni se entrega al bot.

## Seguridad

El número se almacena en `phone_credentials` y temporalmente en `phone_auth_flows` (datos personales). Los hashes Argon2id se guardan en MongoDB, y el desafío temporal caduca a los 10 minutos. No incluir cuerpos de peticiones ni contact.phone_number en logs. Si el token del bot se expuso en logs antiguos, debe revocarse en BotFather y reemplazarse en los servicios.
