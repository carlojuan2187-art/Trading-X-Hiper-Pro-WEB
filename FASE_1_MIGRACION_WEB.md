# FASE 1 · Web externa + Login Telegram

## Qué se entrega

Esta fase permite abrir Trading X Hiper Pro fuera de Telegram en un navegador y autenticarse con **Telegram OIDC Authorization Code + PKCE**. Reutiliza `user_id` de Telegram y, por tanto, el usuario/historial existente en MongoDB. Conserva acceso Mini App para compatibilidad temporal.

- Pantalla web pública «Continuar con Telegram».
- Inicio OIDC y callback server-side, `state` y `nonce`, cookie firmada HttpOnly para el flujo, validación de firma JWKS y claims del ID token.
- Sesión web en cookie HttpOnly/Secure/SameSite=Lax, CSRF para mutaciones vía cookie y logout.
- Los endpoints antiguos pueden seguir usando Bearer Token de la Mini App sin cambios.
- Se mantienen dashboard, control, operaciones, billing, administración, estilos y todo el motor LIVE existentes.
- Enlaces web `/?ref=TELEGRAM_ID` vinculan el referido de forma firmada al flujo OIDC y respetan la asignación única actual en MongoDB.
- **NO implementa cuenta DEMO ni gráfico de velas**: fases posteriores. **No ha sido conectado a credenciales ni bases de datos reales**.

## Configuración del servicio WEB/API (HTTPS obligatorio)

Conservar las variables de servicio actuales. Antes de activar la web externa, agregar/configurar:

```dotenv
WEB_LOGIN_ENABLED=true
WEB_BASE_URL=https://TU-SUBDOMINIO.railway.app
TELEGRAM_OIDC_CLIENT_ID=VALOR_PROPORCIONADO_POR_BOTFATHER
TELEGRAM_OIDC_CLIENT_SECRET=VALOR_SECRETO_DE_BOTFATHER
TELEGRAM_OIDC_REDIRECT_URI=https://TU-SUBDOMINIO.railway.app/api/v1/auth/telegram/oidc/callback
```

La ruta `WEB_BASE_URL` debe ser exactamente el **origen HTTPS**, sin barra final ni rutas. El callback debe coincidir exactamente con `WEB_BASE_URL` + `/api/v1/auth/telegram/oidc/callback`.

Variables existentes **indispensables** en WEB/API, ya usadas por el proyecto:

```dotenv
TELEGRAM_BOT_TOKEN=(el token real existente, NUNCA compartir)
ADMIN_TELEGRAM_ID=(Telegram ID admin actual)
MONGO_URL=(conexión a Atlas existente)
MONGO_DB_NAME=TRADING_X_HIPER_PRO
WEBAPP_SESSION_SECRET=(secreto largo y aleatorio, exclusivo de sesiones)
PRIVATE_KEY_ENCRYPTION_SECRET=(mantener la clave actualmente usada para descifrar las keys de clientes)
MINIAPP_ALLOWED_ORIGINS=https://TU-SUBDOMINIO.railway.app
```

**No cambies `PRIVATE_KEY_ENCRYPTION_SECRET` sin un plan de rotación**, pues invalidaría las claves cifradas si no hay fallback. `WEBAPP_SESSION_SECRET` hoy admite un fallback al token del bot por compatibilidad; en producción es obligatorio fijar un valor propio. Si no está fijado en el despliegue actual y la clave de cifrado cayó a ese fallback, planificar su rotación antes de separarlas: no cambiarlas a ciegas.

Pasos en @BotFather: abrir configuración del bot → Web Login/Login Widget → registrar `https://TU-SUBDOMINIO.railway.app` y la URI exacta de callback; copiar Client ID/Secret a variables **solo del servicio WEB/API**. No publicar el Client Secret ni el token del bot en el frontend.

**Servicio bot/motor:** conservar el comando `python3 main.py` y sus variables actuales. No habilitar un segundo motor LIVE; un solo worker de trading como hasta ahora. `WEB_LOGIN_ENABLED` no es necesario en el servicio bot.

Arranque servicio web: `bash start_web.sh` (o `uvicorn web_main:app --host 0.0.0.0 --port "$PORT"`). No se necesita dominio `.com`; se puede usar un subdominio HTTPS estable de Railway si se registra en @BotFather.

## Ejecutar pruebas offline, SIN trading real

Python 3.11 + dependencias de `requirements.txt`:

```bash
python -m pip install -r requirements.txt
python -m compileall -q app tools main.py web_main.py
node --check app/web/static/app.js
node tests/test_frontend_escape.cjs
python -m unittest tests.test_web_oidc -v
python tools/test_web_auth_integration_offline.py
python -m unittest discover -s tests -v
```

`tools/test_web_auth_integration_offline.py` **simula** base de datos y respuesta de Telegram: verifica redirección, cookie, sesión y CSRF, pero **no verifica integración contra Telegram real**.

En la auditoría, la suite anterior al cambio tenía 116 pruebas con 1 fallo H1 preexistente. Tras estos cambios hay 124 pruebas, 123 OK y **el mismo fallo H1**. No alterar el umbral de la estrategia sin revisar ese caso con datos.

## Prueba manual E2E necesaria antes de publicación

1. Desplegar en staging HTTPS con una base de pruebas o Mongo separado; no meter private keys reales.
2. Configurar BotFather Allowed URLs y las cinco variables nuevas.
3. Abrir página en Chrome/Brave fuera de Telegram → «Continuar con Telegram» → autorizar.
4. Comprobar que `/api/v1/auth/session` refleja el **mismo `user_id`** de Telegram y se muestran los datos esperados de la base de staging.
5. Comprobar `logout`, vencimiento de sesión, bloqueo sin login y HTTP 403 con CSRF inválido.
6. Verificar que rutas admin no aparecen ni responden a usuarios sin rol admin.
7. Validar que el bot y el motor LIVE permanecen en su despliegue actual, sin levantar réplicas extra.

## Limitaciones y riesgos reales (antes de producción)

- Login externo aún no validado con @BotFather real ni con dominio/DB reales.
- Logout elimina cookies pero los Bearer tokens firmados antiguos conservan validez hasta expirar; añadir revocación de sesiones y rate limiting antes de exposición masiva.
- El API original tiene configuración CORS permisiva por defecto; establecer el origen exacto. Faltan CSP, pruebas reales de cookies detrás del proxy y revisión de claves legacy.
- El login OIDC requiere usar HTTPS y un **dominio canónico**; el alias temporal que cambie invalidará la configuración en BotFather.
- La pantalla web sigue siendo la interfaz original (`index.html` y `app.js`) ahora habilitada para navegadores; la renovación de UX, market WebSocket y DEMO son etapas siguientes.

## Archivos modificados (existentes)

- `.env.example`
- `requirements.txt`
- `app/config.py`
- `app/api/app_factory.py`
- `app/api/routes_auth.py`
- `app/api/dependencies.py`
- `app/web/static/index.html`
- `app/web/static/app.js`
- `app/web/static/styles.css`

## Archivos agregados

- `app/api/telegram_oidc.py`
- `tests/test_web_oidc.py`
- `tools/test_web_auth_integration_offline.py`
- `FASE_1_MIGRACION_WEB.md`

**Archivos eliminados:** ninguno. **Trading engine y base de datos original modificados:** ninguno. **Nuevas colecciones o migraciones MongoDB:** ninguna.

## Volver atrás

Restaurar el ZIP original para WEB/API y mantener sin cambios el bot LIVE. Alternativamente, poner `WEB_LOGIN_ENABLED=false` para desactivar nuevos inicios OIDC. No borrar cuentas ni colecciones y no rotar secretos sin un procedimiento controlado.
