# Hyper League WEB — corrección de diagnóstico del login Telegram OIDC

**Origen:** `Trading-X-Hiper-Pro-WEB-main.zip` aportado por el usuario (repositorio real de Railway).

## Hallazgo confirmado

El flujo inicia correctamente (`/api/v1/auth/telegram/oidc/start`, HTTP 302), pero el callback falla con `KeyError` y HTTP 401. En esta versión del repositorio, el callback leía `token_response.json()['id_token']` sin comprobar si el proveedor devolvía `id_token` o un objeto de error. El flujo `state`/cookie comprueba los valores requeridos antes de ese acceso, por lo que la ausencia de `id_token` es la explicación respaldada por el código y los logs, **sin demostrar aún por qué el proveedor lo omitió**.

## Cambios

- `app/api/routes_auth.py`: valida la estructura y la presencia de `id_token`; registra solamente nombres de campos conocidos y códigos OAuth sanitizados (no tokens ni descripciones), HTTP status de rechazos y fase de error (transporte, formato o validación de firma/claims); mantiene `state`, `nonce`, PKCE, firma JWKS y todas las restricciones existentes. No otorga acceso si falta `id_token`.
- `start_web.sh`: usa `--no-access-log` para impedir que Uvicorn registre `code` y `state` de OAuth en la query del callback. Los errores y mensajes del backend siguen registrándose.
- `tools/test_oidc_callback_diagnostics_offline.py`: cinco pruebas de respuesta de error, falta de token, fallo HTTP y login válido.
- `HOTFIX_TELEGRAM_OIDC_REPOSITORIO_WEB.md`: este documento.

Sin cambios en el motor LIVE, DEMO, WebSocket, indicadores, gráficos, pagos ni parámetros de estrategias. Sin cambios en variables de entorno obligatorias. No se eliminaron archivos.

## Railway

En el servicio **WEB/API**:

```text
Build Command: bash build_web.sh
Start Command: bash start_web.sh
```

Subir el ZIP como contenido del mismo repositorio en GitHub (o copiar solo los dos archivos modificados y el test), confirmar que Railway despliega el commit nuevo y volver a intentar el acceso desde una sesión nueva. **No desplegar BOT LIVE o DEMO WORKER para depurar el login**.

## Interpretación de los nuevos diagnósticos

| Mensaje parcial | Posibles causas / siguiente comprobación |
|---|---|
| `token exchange rejected (http_status=401)` | Telegram rechazó la petición al endpoint token. Comparar Client ID, Client Secret y bot configurados en Railway/BotFather. |
| `token response missing id_token (oauth_error=invalid_client)` | La respuesta indica problema de credenciales OIDC; validar Client ID y Secret **Web Login**, no el token del bot. |
| `token response missing id_token (oauth_error=invalid_grant)` | Código expirado/usado, discrepancia del redirect o problema de PKCE. Reiniciar el login sin reutilizar URL antigua. |
| `token response missing id_token (oauth_error=unknown)` | Telegram devolvió campos sin ID token. Compartir solo mensaje saneado. |
| `token signature/claims rejected` | ID token recibido pero la firma, audiencia, emisor, vencimiento o nonce no pasaron la validación. |
| `OIDC token response is not valid JSON` | Respuesta inesperada del proveedor. |

No inventar valores OIDC. La URL de retorno que usa esta aplicación debe ser, de forma exacta:

`https://trading-x-hiper-pro-web-production.up.railway.app/api/v1/auth/telegram/oidc/callback`

Y el origen web:

`https://trading-x-hiper-pro-web-production.up.railway.app`

Las mismas direcciones deben estar autorizadas en la configuración Web Login de BotFather. Consultar: https://core.telegram.org/bots/telegram-login.

## Comprobaciones locales realizadas

- `python tools/test_oidc_callback_diagnostics_offline.py`: 5 casos correctos.
- `python tools/test_web_auth_integration_offline.py`: integración simulada aprobada.
- `python -m unittest tests.test_web_oidc`: 8 pruebas aprobadas.
- `python tools/test_demo_offline.py`: 9 pruebas aprobadas.
- `python tools/test_market_api_auth_offline.py`: 4 pruebas aprobadas.
- `python tools/test_market_terminal_offline.py`: 8 pruebas aprobadas.
- `python tools/test_phase4_offline.py`: 9 pruebas aprobadas.
- `node tests/test_frontend_escape.cjs`: correcto.
- `node tests/test_trade_share_phase5.mjs`: 8 pruebas aprobadas.
- `python -m compileall -q app`, `bash -n start_web.sh`, `node --check` de módulos JS: correctos.

**Pendiente:** prueba real de Telegram OIDC desplegado (no tenemos el Client Secret ni acceso a la configuración del bot; no deben compartirse), prueba visual del navegador y build de la interfaz en Railway. No se ha ejecutado ningún cambio en el servidor.

**Seguridad urgente:** El log enviado previamente incluía un token completo del bot Telegram. Revocarlo en BotFather y reemplazarlo en todos los servicios que lo usen. No compartir secretos, `code`, `state`, cookies ni URLs del callback con parámetros en los próximos logs.
