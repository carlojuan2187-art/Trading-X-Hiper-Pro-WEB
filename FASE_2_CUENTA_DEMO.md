# FASE 2 — DEMO integrado en la web (sin órdenes reales)

**Estado: implementación local + pruebas offline. NO desplegado ni probado con Hyperliquid/MongoDB reales.**
La Fase 1 (@BotFather, Telegram OIDC y web externa) sigue siendo un requisito.

## Qué incorpora

- Nueva sección «Cuenta DEMO» en web escritorio/móvil (mismos usuarios Telegram).
- Inicio y pausa de nuevas operaciones DEMO, reset permitido solo pausado y sin posición abierta.
- Saldo simulado inicial por cuenta (100 USDC de manera predeterminada); balance, equity, margen bloqueado/libre, PnL, operaciones e historial de resultados (sin gráfico de velas aún).
- Worker **separado** `python -m app.demo.worker`; necesita `DEMO_ENABLED=true` y mismos `MONGO_URL`/`MONGO_DB_NAME` que el servicio WEB/API; se puede usar DB de staging para pruebas.
- Reutiliza scanner/ranking, `get_entry_signal` y `strategy_router` desde el propio `trading_engine._select_best_signal_from_scanner_shortlist`, `_entry_drift_check`, `_coalesce_management_params`, `_resolve_structural_exit_plan`, `risk.validate_trade_conditions` y umbrales principales LIVE.
- Misma selección de estrategia, filtros de señales y SL/TP estructurales, con ejecución **PAPER**. No llama a `execute_trade_cycle`, `place_market_order`, `place_stop_loss` ni a APIs privadas.
- Libro L2 público de Hyperliquid (`get_best_bid_ask`) para precio ejecutable de apertura y salida, slippage adverso configurable y comisión taker en ambos lados. Cierra TP/SL cuando el bid/ask observado lo cruza (incluidos gaps); si falta L2 no inventa fill ni cierra.
- Una posición a la vez por usuario; cuotas máximas por hora/día, cooldown entre trades y protecciones de pérdidas/PF **por usuario DEMO**.
- Mongo `demo_accounts`: escrituras completas condicionadas por versión, historial reciente acotado a 200 trades y contadores vitalicios; índices `enabled`, `position_open`.
- Lock Mongo global para múltiples instancias y activación bajo demanda en worker; no procesar DEMO dentro de FastAPI.

## Diferencias reales frente a LIVE (importantes)

Esta fase **NO es una reproducción 1:1 de la ejecución LIVE**. Compartimos el scanner, router, SL/TP y controles indicados, pero PAPER no genera fills reales; usa libro superficial (top of book), slippage fijo, sin profundidad ejecutada, partial fills, funding, margin calls, liquidación ni latencia real. TP/SL se revisan cada `DEMO_TICK_SECONDS` (no hay un stop en el exchange): un salto de precio puede causar salida peor que el stop. Tampoco se comparte el **gobernador global** de riesgo LIVE (sería peligroso para los usuarios reales); DEMO solo aplica límites por cuenta. La simulación no representa resultados futuros.

El margen objetivo viene de las **mismas variables de riesgo** que LIVE (`POSITION_PERCENT`, `MAX_TRADE_MARGIN_USDC`, `LEVERAGE`); en DEMO se reserva una pequeña porción explícita para pagar comisión de entrada, de modo que el saldo no quede negativo al operar con 100 % de equity. La UI enseña margen efectivo y objetivo en los documentos internos.

El selector se ejecuta **una vez por ventana de scanner** y ofrece esa misma señal a cuentas DEMO habilitadas; la decisión de aceptar el sizing, cooldown y riesgo es individual. Eventos del router se etiquetan como `demo`/`demo_router` para no contarlos como aperturas LIVE.

## Variables por servicio, Railway

### Servicio WEB/API (existente)

Mantener **todas** las variables actuales que ya utiliza la Fase 1, en particular `MONGO_URL`, `MONGO_DB_NAME`, `TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `WEBAPP_SESSION_SECRET`, `WEB_BASE_URL`, `WEB_LOGIN_ENABLED` y parámetros OIDC. AÑADIR:

```dotenv
DEMO_ENABLED=true
DEMO_INITIAL_EQUITY_USDC=100
DEMO_TAKER_FEE_BPS=5
DEMO_SLIPPAGE_BPS=2
```

Si quieres el mismo comportamiento de riesgo LIVE, mantener los valores actuales de `POSITION_PERCENT`, `MAX_TRADE_MARGIN_USDC`, `LEVERAGE`, `MIN_ENTRY_RR`, cuotas y `USER_RISK_*`. El frontend solo consume APIs del WEB/API. **No agregar worker a este servicio.**

### Servicio nuevo DEMO WORKER (separado del bot LIVE)

Comando de ejecución: `python -m app.demo.worker`.

Variables mínimas de arranque (el worker no necesita el token privado de Telegram):

```dotenv
DEMO_ENABLED=true
MONGO_URL=(URL de la DB elegida para esta instalación)
MONGO_DB_NAME=TRADING_X_HIPER_PRO
DEMO_WORKER_MODE=true
ADMIN_TELEGRAM_ID=(ID admin existente, entero positivo)
DEMO_INITIAL_EQUITY_USDC=100
DEMO_TAKER_FEE_BPS=5
DEMO_SLIPPAGE_BPS=2
DEMO_TICK_SECONDS=10
DEMO_SCAN_SECONDS=300
POSITION_PERCENT=1.0
MAX_TRADE_MARGIN_USDC=0
LEVERAGE=10
```

**Importante:** añadir en este worker el resto de variables de *estrategia, scanner y filtros del motor LIVE* (router, MTF, RR, límites, etc.) con **exactamente los mismos valores**, de lo contrario usaría los defaults y las decisiones no serían iguales. No necesita `TELEGRAM_BOT_TOKEN`, `PRIVATE_KEY_ENCRYPTION_SECRET`, wallets o permisos de trading para generar fills PAPER; la configuración global puede exigir variables adicionales según evolucione el repositorio. No incluir credenciales privadas de Hyperliquid.

### Servicio BOT/LIVE (existente)

Mantenerlo intacto: `python main.py`. No agregar `DEMO_ENABLED`; no tocar API keys ni permisos de retiros.

**Activación segura:** desplegar WEB/API en staging con `DEMO_ENABLED=true`, verificar login, luego desplegar exactamente **UNA** réplica del worker DEMO sobre esa misma DB. No utilizar datos reales de usuarios para pruebas de staging. No activar el motor LIVE en staging.

## API nueva (sesión Telegram obligatoria; mutaciones protegidas por CSRF web)

- `GET /api/v1/demo`: estado de cuenta, posición e historial.
- `POST /api/v1/demo/start`: buscar señales automáticamente (no enviar órdenes LIVE).
- `POST /api/v1/demo/pause`: frenar entradas nuevas; **sigue supervisando** posición actual.
- `POST /api/v1/demo/reset`: poner saldo inicial y borrar historial solo si está pausado y sin posición (409 de otro modo).

Sin `DEMO_ENABLED=true`, todas responden HTTP 503. `user_id` se obtiene exclusivamente de la sesión; ningún endpoint recibe otro usuario como parámetro.

## Pruebas

Se ejecutaron **offline** 9 pruebas de cuenta, LONG/SHORT, comisiones, gaps, TP/SL, concurrencia, reset, cooldown y worker mock; también compila el código y pasa el test existente de escape HTML.

```bash
python tools/test_demo_offline.py
node --check app/web/static/app.js
node tests/test_frontend_escape.cjs
python -m compileall -q app tools web_main.py main.py
```

Con `requirements.txt` en el entorno real, ejecutar también:

```bash
python -m unittest discover -s tests -v
python tools/test_web_auth_integration_offline.py
```

**No realizadas:** prueba E2E con MongoDB, datos WebSocket/L2 en producción, arranque del worker con el SDK/requirements completo, depuración del fallo H1 preexistente y validación del login OIDC real.

## Requisitos de la siguiente fase

Terminal de velas real con feed público WebSocket (mark, bid/ask, órdenes recientes, libro, gráficos e indicadores) y reconciliación del estado DEMO con eventos de mercado, mayor granularidad de cierres y funding/liquidación simulado. Añadir rate limiting y pruebas de integración/staging antes de ofrecerlo públicamente.

## Archivos nuevos

- `app/demo/__init__.py`
- `app/demo/settings.py`
- `app/demo/ledger.py`
- `app/demo/simulator.py`
- `app/demo/worker.py`
- `app/api/routes_demo.py`
- `tools/test_demo_offline.py`
- `FASE_2_CUENTA_DEMO.md`

## Archivos modificados

- `app/api/app_factory.py`
- `app/config.py` (excepción acotada para worker DEMO sin token Telegram)
- `app/trading_engine.py` (**solo** parámetro opcional `execution_mode`, default LIVE, para etiquetar el uso del selector por DEMO; no cambia el flujo de órdenes existente)
- `app/web/static/index.html`
- `app/web/static/app.js`
- `app/web/static/styles.css`
- `.env.example`

**Archivos eliminados: ninguno.**
