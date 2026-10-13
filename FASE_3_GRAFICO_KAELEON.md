# Fase 3 — Gráfico de operaciones Hyper League basado en KAELEON

Estado: **código fuente implementado y pruebas offline aprobadas; compilación npm y prueba visual de navegador pendientes. NO desplegado en LIVE.**

## Qué se estudió en el ZIP KAELEON

- `frontend/src/components/trading/LiveMarketChart.tsx`
- `frontend/src/components/trading/MarketMicrostructure.tsx`
- `app/market/chart_service.py`
- `docs/LIVE_MARKET_CHART.md`
- `frontend/package.json` / `package-lock.json`.

KAELEON utiliza KLineCharts **10.0.3**, velas históricas, WebSocket público, herramientas de dibujo y overlays bloqueados ENTRY / SL / TP, zonas de beneficio y riesgo, y puntos de apertura/cierre. Esta fase reutiliza la biblioteca y adapta las marcas principales al esquema de Hyperliquid. NO traslada el frontend completo de KAELEON ni replica todavía todos sus indicadores, alertas y configuraciones.

## Terminal creado

La web existente mantiene su estructura, sesiones Telegram (Mini App y OIDC web) y MongoDB. Se añade un módulo ESM compilable con Vite ubicado en `market-terminal/` que se monta en los paneles **Cuenta DEMO** y **Operaciones LIVE**.

- Velas profesionales y temporalidades 1m, 3m, 5m, 15m, 30m, 1h, 4h y 1d.
- Indicadores MA / EMA / BOLL y panel VOL / MACD / RSI.
- Herramientas de dibujo de tendencia, horizontal, segmento y Fibonacci; fullscreen.
- WebSocket **público** de Hyperliquid: `candle`, `l2Book`, `trades`, `activeAssetCtx`. Histórico y libro inicial REST autenticados y normalizados por el backend. Fallback de velas por REST a intervalos de 15 segundos; reconexión con backoff.
- Línea y etiqueta de ENTRY (azul), SL (rojo), TP (verde). Zonas de riesgo y beneficio semitransparentes. Marcador de apertura LONG/SHORT y cierre con motivo TP/SL y PnL cuando los datos están registrados.
- Al abrir una operación el gráfico sigue automáticamente su símbolo; el usuario puede explorar otros contratos y volver con **Ver operación**.
- Dos vistas separadas: DEMO usa `demo_accounts`, LIVE usa `active_trades` y `trades` **del usuario autenticado**.
- Lectura cada 3 segundos únicamente cuando la pestaña está visible. No se envían claves API a la UI. Los overlays están bloqueados y son informativos: ni el gráfico ni sus herramientas colocan, modifican o cancelan órdenes.

**Limitación histórica LIVE:** los trade records previos pueden carecer de hora de apertura, TP/SL finales u otros metadatos. En esos casos se muestran solo marcadores sustentados por datos realmente existentes. Para nuevos cierres se añade `opened_at_ms` en los metadatos que el motor ya conserva en su auditoría, **sin modificar ejecución, señales, stop o target**. Los históricos antiguos no se fabrican ni se alteran.

## API

Todas las rutas usan sesión Telegram validada (`require_session`):

| Método | Ruta | Uso |
|---|---|---|
| GET | `/api/v1/market/instruments?query=BTC&limit=100` | Catálogo Hyperliquid filtrado |
| GET | `/api/v1/market/candles?symbol=BTC&timeframe=5m&limit=400` | OHLCV validado |
| GET | `/api/v1/market/snapshot?symbol=BTC` | Snapshot libro L2 |
| GET | `/api/v1/market/overlays?mode=DEMO` | Operaciones del usuario en DEMO |
| GET | `/api/v1/market/overlays?mode=LIVE` | Operaciones del usuario en LIVE |

El backend comprueba el símbolo contra el catálogo real y valida temporalidad, límite, geometría OHLCV y valores numéricos. Ninguna nueva ruta llama a endpoints `/exchange` ni métodos de firma.

## Instalación y build en staging

La fase 3 conserva **tres servicios** (WEB/API, BOT LIVE, DEMO WORKER); **NO** requiere un cuarto servicio para el gráfico.

En la raíz del proyecto, con Python, npm y Node compatibles:

```bash
python -m pip install -r requirements.txt
npm ci --prefix market-terminal --no-audit --no-fund
npm run build --prefix market-terminal
bash start_web.sh
```

El build crea `app/web/static/market/market-terminal.js` y `.css`. Los archivos incluidos en el ZIP son placeholders explícitos para evitar 404 mientras no se compile. **Si no se ejecuta el build, el gráfico profesional NO aparecerá.**

Railway WEB/API:

- `Build Command`: `bash build_web.sh` (instala requirements y compila terminal en build, no durante requests).
- `Start Command`: `bash start_web.sh`.
- `PORT` lo aporta el entorno. Se mantienen las variables actuales de Fases 1 y 2 para web/OIDC y MongoDB.
- Exigir imagen con Node.js >=22.12 (igual que KAELEON). Confirmar que el builder contiene Python y Node antes de usar este Build Command; si no, construir y publicar los estáticos por CI.

**Variables de entorno nuevas: ninguna.** No modificar secrets, wallets ni permisos del BOT LIVE. Si se habilita DEMO, seguir usando las variables y worker de Fase 2.

No subir el ZIP directamente sobre el servicio LIVE sin construir primero en staging y ejecutar el checklist.

## Checklist de aceptación obligatoria

- [ ] Compilar frontend sin errores con `npm ci && npm run build`.
- [ ] Probar OIDC real en HTTPS y navegación en Chrome/Brave y Android.
- [ ] Confirmar con Hyperliquid que llegan histórico, book, trades, funding y mark price con símbolos reales.
- [ ] DEMO: activar bot, abrir LONG y SHORT, verificar ENTRY/SL/TP como **niveles del ledger**, comprobar alineación con precio y tiempo en distintas escalas.
- [ ] DEMO: cerrar TP y SL, comprobar marcador final, precio de cierre, motivo, PnL y persistencia tras recargar.
- [ ] LIVE: verificar un caso con `active_trades` real y un trade posterior al nuevo metadata, y que no hay órdenes enviadas desde web.
- [ ] Verificar aislamiento multiusuario, logout y sesión expirada.
- [ ] Investigar trade history LIVE anteriores sin `opened_at_ms` antes de solicitar backfill; **no inventar** timestamps.
- [ ] Rendimiento: revisar consumo de WS y poll API con carga concurrente, limitar logs de `/api/v1/market/*` si hace falta. A futuro evaluar WS hub para multiplexar muchos navegadores.

## Pruebas hechas localmente

- `python tools/test_market_terminal_offline.py` — 8 pruebas.
- `python tools/test_market_api_auth_offline.py` — 4 pruebas.
- `python tools/test_demo_offline.py` — 9 pruebas Fase 2.
- `node tests/test_frontend_escape.cjs` — pasó.
- `python tools/test_web_auth_integration_offline.py` — pasó.
- `node --check` de JS fuente y compilación sintáctica de módulos Python — pasó.

No se pudo ejecutar **npm build**, acceder al WebSocket real ni abrir la UI compilada con navegador porque el entorno de ejecución no puede descargar paquetes del registro npm. Por tanto la equivalencia funcional visual con KAELEON sigue pendiente de prueba.

## Archivos Fase 3

Modificados:
- `app/api/app_factory.py`
- `app/trading_engine.py` (solo campo de auditoría `opened_at_ms`)
- `app/web/static/index.html`
- `app/web/static/app.js`

Nuevos:
- `app/market/__init__.py`, `app/market/terminal.py`, `app/market/overlays.py`
- `app/api/routes_market.py`
- `market-terminal/package.json`, `market-terminal/package-lock.json`, `market-terminal/vite.config.js`, `market-terminal/chart.js`, `market-terminal/chart.css`
- `app/web/static/market/market-terminal.js`, `app/web/static/market/market-terminal.css` (placeholders reemplazables durante build)
- `tools/test_market_terminal_offline.py`, `tools/test_market_api_auth_offline.py`
- `build_web.sh`, `FASE_3_GRAFICO_KAELEON.md`

El resto de la Fase 2 permanece conservado. No se eliminó ningún archivo previo.

## Pendientes de paridad completa con KAELEON

Esta entrega prioriza datos y marcas visuales de operaciones. El frontend de KAELEON incluye además configuraciones detalladas de indicadores, más herramientas de dibujo, alertas locales, área vs velas, indicadores de profundidad/volume profile y otros ajustes de UI todavía no portados. Se recomienda completar eso tras validar la base del gráfico con posiciones reales y DEMO.
