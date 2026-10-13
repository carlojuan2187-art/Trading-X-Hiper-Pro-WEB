# Fase 4 — Eventos WebSocket, detalles de posición y requisitos de credenciales

**Estado:** implementación de código + pruebas offline verificadas. **NO desplegado, NO probado visualmente, NO certificado para LIVE.** Base: ZIP WEB Fase 3 (no reemplaza el servidor LIVE).

## Alcance implementado

1. **Motor DEMO por eventos:** `python -m app.demo.worker` consume Hyperliquid `l2Book` vía WebSocket y revisa SL/TP en cada libro recibido. Elige nuevas señales con el scanner/router original (programación por tiempo de evaluación, distinta de consultar precios). Sin polling REST en el loop del worker de producción. Recupera conexiones con backoff. Usa lease Mongo único y CAS por usuario. Si se pierde el WebSocket no inventa fills ni marca TP/SL con precios obsoletos.
2. **Realtime privado web:** `wss://TU-DOMINIO/api/v1/stream`, cookie Telegram OIDC HttpOnly, Origin HTTPS exacto, token firmado y expiración; event hub MongoDB **compartido** por proceso API, con colas acotadas y resync ante overflow. Emite snapshots de cuenta DEMO, overlays DEMO y LIVE, perfil seguro, control y nuevos estados de operaciones/performance cuando se modifican documentos. Un único listener MongoDB por proceso, no uno por visitante.
3. **Frontend sin polling de estado:** eliminado `setInterval(12000)` del panel DEMO y `setInterval(3000)` de overlays en el gráfico. Eliminado fallback REST de velas cada 15s. Se mantiene REST para login, históricos iniciales, catálogo, configuración y acciones manuales; los estados dinámicos se reciben por WS. Se actualizan marcas ENTRY, TP/SL e historial en respuesta a eventos.
4. **Ficha de operaciones:** entrada, precio actual bid/ask, dirección, leverage, margen, notional, cantidad, stop, objetivo, variación %, ROE y PnL (valores estimados en el navegador distinguidos del ledger). Para trades cerrados muestra entrada, salida, motivo, resultado neto y ROE cuando existen datos. El chart es de lectura y jamás envía órdenes.
5. **Clave privada obligatoria en ambos modos según requisito:** LIVE ya exigía wallet+private key. `/api/v1/demo/start` ahora exige wallet y private key cifrada con estado válido. El worker vuelve a comprobar el estado antes de nuevas entradas. **DEMO no descifra, importa, transmite, firma con ni usa esa clave para ejecutar trades.** Los cierres de posiciones existentes se mantienen aunque se retire la clave. Para LIVE se recomienda utilizar una API wallet (agent) limitada al trading, no la clave maestra.

## Arquitectura resumida

```text
Hyperliquid /ws --> DEMO worker [l2Book; dispara SL/TP en cada evento]
                        | CAS demo_accounts en Mongo
MongoDB Change Streams --> API Hub [1 watcher por proceso]
                              | filtra por usuario, carga snapshots seguros
                              +--> navegador autenticado /api/v1/stream
Hyperliquid /ws --> KLineCharts del navegador [velas, l2Book, trades, mark]
```

## Límites importantes (NO presentarlos como resueltos)

- **El motor LIVE de Hyper League conserva su arquitectura existente.** No se han migrado sus llamadas REST ni ciclos del worker LIVE a eventos. Tampoco todo el subsistema admin/pagos se ha convertido en stream. NO declarar que el 100% de la plataforma es WebSocket hasta completar auditoría y migración LIVE.
- **DEMO no puede garantizar el toque de SL/TP durante desconexiones:** el worker se detiene sin precios, y el siguiente book no informa necesariamente de todos los extremos intermedios. Requiere backfill/reconciliación verificada y pruebas de gaps antes de producción. El simulador tampoco modela profundidad total, liquidación, funding ni ejecución garantizada.
- **MongoDB debe soportar Change Streams (replica set o Atlas).** En standalone, el WS privado informa error y reintenta la conexión, pero no sustituye con polling HTTP. Validar la topología antes de desplegar.
- Las tasas de comisión/slippage DEMO son aproximaciones configurables. PnL local mostrado entre persistencias es estimativo; el ledger Mongo y los registros del exchange son fuente de verdad. No hay capacidad de mover TP/SL desde el gráfico.
- La actualización de LIVE procede de los documentos `users`, `active_trades` y `trades` que el motor persiste. Un borrado sin documento posterior `trades` puede no disparar evento LIVE porque MongoDB no incluye user_id en el evento de delete. Auditar e instrumentar eventos de cierre LIVE antes de la salida a producción.
- El frontend KLineCharts aún necesita **compilación npm**. La dependencia no está disponible offline: `npm ci --offline` falla por tarball `klinecharts-10.0.3.tgz` no cacheado. No se ha probado el render ni los WebSockets externos de Hyperliquid con servicio real.
- El hub usa un watcher por proceso API y permite hasta 250 conexiones (máximo 4 por cuenta); para escala horizontal alta evaluar un bus de eventos dedicado. El worker DEMO mantiene un lease, **no** se permiten múltiples líderes. No se ha hecho carga, resiliencia ni pruebas de MongoDB real.

## Despliegue de staging (NO alterar LIVE)

### Servicio WEB/API

- Usar **HTTPS** y las mismas variables de Fase 1: `WEB_BASE_URL`, `WEB_LOGIN_ENABLED=true`, `WEBAPP_SESSION_SECRET`, OIDC, `MONGO_URL`, `MONGO_DB_NAME`, `DEMO_ENABLED=true`, `DEMO_INITIAL_EQUITY_USDC`, `DEMO_TAKER_FEE_BPS`, `DEMO_SLIPPAGE_BPS`.
- Configurar MongoDB replica set y habilitar acceso a Change Streams en el usuario de la base.
- Build: `bash build_web.sh` (requiere Node >=22.12, Python y acceso a npm). Start: `bash start_web.sh`.

### Servicio DEMO WORKER

- Start: `python -m app.demo.worker`.
- Variables necesarias para DEMO: `MONGO_URL`, `MONGO_DB_NAME`, `DEMO_ENABLED=true`, `DEMO_WORKER_MODE=true`, `DEMO_SCAN_SECONDS=300`, `DEMO_TAKER_FEE_BPS=5`, `DEMO_SLIPPAGE_BPS=2`, `DEMO_INITIAL_EQUITY_USDC=100`, `HYPERLIQUID_WS_URL=wss://api.hyperliquid.xyz/ws`.
- Más cualquier variable del scanner/router actual del motor LIVE que se deba mantener idéntica para la evaluación de estrategias. **NO copiar tokens Telegram, claves privadas de usuarios, secretos de pagos ni variables de envío de órdenes LIVE a este servicio.**
- Sustituir/remover `DEMO_TICK_SECONDS`: ya no se usa por el worker de producción.

### Servicio BOT LIVE

- **No sustituir ni modificar el despliegue LIVE en esta fase.** Continúa utilizando su configuración existente y sus restricciones de acceso.

### Pruebas de aceptación en staging

1. Registro OIDC, wallet y private key cifrada. Comprobar `403` al intentar activar DEMO sin ambas credenciales y que nunca aparecen claves en las cargas WS.
2. Comprobar que el canal privado autentica, no expone cambios ajenos y recupera estado inicial tras reconectar (también con sesión expirada).
3. Mongo replica set: verificar el watcher compartido y evento `demo_accounts` después de start/pause/open/close.
4. Abrir LONG y SHORT DEMO, observar una sola fuente de cotización, SL/TP en tick y marcas del gráfico, y contrastar PnL/ROE con ledger.
5. Forzar desconexiones/reconexiones y caída temporal Mongo; confirmar que no se ejecutan órdenes reales ni cierres especulativos.
6. Repetir con decenas de clientes y registrar latencia, reconexión, CPU/RAM y consumo de conexiones upstream. Verificar LIVE histórico sin inventar marcas ni precios.
7. `npm ci --prefix market-terminal && npm run build --prefix market-terminal`; revisar interfaz compilada en escritorio/móvil.

## Pruebas realmente ejecutadas en este entorno

- `python tools/test_demo_offline.py` — 9 correctas.
- `python tools/test_market_terminal_offline.py` — 8 correctas.
- `python tools/test_market_api_auth_offline.py` — 4 correctas.
- `python tools/test_phase4_offline.py` — 9 correctas (credential gate, WS book, fanout, autenticación y SL/TP event-driven).
- `python tools/test_web_auth_integration_offline.py` — passed.
- `python -m compileall -q app` — pasó.
- `node --check market-terminal/chart.js && node --check app/web/static/app.js` — pasó.
- `npm ci --prefix market-terminal --offline` — **no pasó**, `ENOTCACHED` para KLineCharts. Build y smoke test de navegador pendientes.
- **No se probó** con Mongo real, Hyperliquid real, claves reales, servidor Railway ni LIVE.

## Cambios exactos respecto a Fase 3

**Modificados (11):** `.env.example`, `requirements.txt`, `app/api/app_factory.py`, `app/api/routes_demo.py`, `app/demo/settings.py`, `app/demo/worker.py`, `app/market/overlays.py`, `app/web/static/app.js`, `app/web/static/index.html`, `market-terminal/chart.js`, `tools/test_demo_offline.py`.

**Nuevos (6):** `app/demo/credentials.py`, `app/demo/market_events.py`, `app/realtime/__init__.py`, `app/realtime/stream.py`, `tools/test_phase4_offline.py`, `FASE_4_WEBSOCKET_DETALLES_CREDENCIALES.md`.

**Eliminados: ninguno.** Ninguna estrategia ni método de colocación/cancelación de órdenes LIVE fue modificado en Fase 4.
