# Hyper League WEB — Terminal PRO v2 (14-10-2026)

Base: último paquete entregado **Hyper-League-WEB-Fix-Grafico-Estados-COMPLETO**. Se contrastó con el gráfico de KAELEON, incluidas sus temporalidades, overlays y parámetros de indicadores. Las rutas de mercado y ejecución de órdenes NO se modificaron.

## Corregido

- Gráfico **determinista y autónomo**: el navegador no intenta descargar una biblioteca de terceros a veces sí y a veces no. Un mismo motor dibuja velas e indicadores en PC y móvil, sin depender de npm, CDN ni claves de trading.
- Ancho inicial de 40 velas visibles, cuerpos más gruesos y mechas distinguibles, cursor OHLC, volumen, ejes, zoom por rueda/botones/pinch y desplazamiento horizontal.
- Indicadores reales: MA(5,10,30,60), EMA(6,12,20), BOLL(20,2), VOL, MACD(12,26,9), RSI(6,12,24). Antes el fallback dibujaba una MA20 con cualquier opción y no trazaba MACD/RSI verdaderos.
- Dibujos locales: tendencia, segmento, horizontal, Fibonacci; herramientas desactivables y limpieza explícita. Referencia a coordenadas de precio/tiempo (no píxeles fijos) para no perder el trazo al ampliar.
- Catálogo consultado una vez por pestaña (cache en memoria), selector con búsqueda interactiva y sin aceptar símbolos ajenos al catálogo. Cambios de par y temporalidad descartan peticiones de velas anteriores y suscripciones obsoletas.
- WebSocket público de Hyperliquid: canales `candle`, `l2Book`, `trades`, `activeAssetCtx`, con ping/reconexión. Indicador del último precio coloreado por comparación de ticks de la misma fuente; no mezcla mark, trades y cierre de velas. **No hay polling periódico de precios.**
- Order book: niveles ASK rojos y BID verdes con barras de profundidad acumulada, spread y mejores precios. Animación UI limitada a frames del navegador para no reconstruir DOM más veces que el refresco visual.
- DEMO/LIVE: ambos conservan los overlays de operaciones ENTRY, TP, SL e histórico, estrictamente en modo lectura.
- Asset cache bust en HTML para evitar continuar mostrando el archivo antiguo.

## Archivos estrictamente modificados respecto al último ZIP entregado

- `app/web/static/index.html`
- `app/web/static/market/chart-local.js`
- `app/web/static/market/market-terminal.js`
- `app/web/static/market/market-terminal.css`

## Archivos nuevos

- `tools/test_terminal_v2_browser.py` (prueba de navegador opcional; no forma parte del arranque)
- `MEJORA_TERMINAL_PRO_V2.md`

Se incluyen además `build_web.sh` y `start_web.sh` **sin cambiar su versión anterior** porque el repositorio GitHub había conservado variantes antiguas que compilan y sobrescriben el archivo público con Vite. El parche asegura que ninguna build vuelva a publicar el placeholder.

## Configuración de Railway

- WEB/API Build: `bash build_web.sh`
- WEB/API Start: `bash start_web.sh`
- **Sin nuevas variables**. No hay que reiniciar o modificar el motor DEMO/LIVE para esta mejora visual.
- No ejecutar `npm run build --prefix market-terminal`: el código fuente React/KLineCharts viejo sigue disponible como referencia, pero ese comando sobreescribiría el bundle local mejorado.

## Pruebas ejecutadas

- `node --check` en ambos módulos JavaScript, OK.
- Pruebas numéricas de MA, EMA, Bollinger, RSI, MACD con series controladas, OK.
- `python tools/test_market_terminal_offline.py`, 8 pruebas, OK.
- `python tools/test_terminal_v2_browser.py`: Chromium a 393px, mock de APIs y WebSocket con selecciones ETH, 1h, EMA, RSI, zoom y order book: OK, sin excepciones JS.
- **NO verificado** en el despliegue real de Railway ni contra WebSocket real durante la prueba. La prueba de navegador usó datos simulados para aislar el código. Validar tras desplegar un único commit.
