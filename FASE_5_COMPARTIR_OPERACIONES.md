# Fase 5 — Compartir operaciones con tarjeta PNG y QR

**Base:** `Trading-X-Hiper-Pro-WEB-Fase4-WebSocket.zip`. La versión de Fase 5 modifica **solo la interfaz web** y sus pruebas; no modifica las órdenes, las estrategias, la base de datos, la API, el motor LIVE ni el worker DEMO.

## Implementado

- **DEMO abierta:** botón `Compartir operación DEMO` en la ficha de posición.
- **DEMO cerrada:** botón `Compartir` por operación del historial.
- **LIVE abierta:** botón `Compartir operación LIVE` al lado del resumen de la operación abierta.
- **LIVE cerrada:** botón `Compartir resultado` por operación del historial.
- Tarjeta vertical 1080×1280 PNG con identidad **Trading X Hiper Pro** y logo existente, símbolo, LONG/SHORT, apalancamiento cuando está registrado, modalidad DEMO/LIVE, entrada, precio observado o salida, estrategia si consta, motivo de cierre si consta, PnL y ROI/ROE cuando hay información suficiente.
- Modal con **vista previa Canvas**, opción de ocultar ROE/PNL, descargar PNG y compartir por Web Share API (en dispositivos compatibles). Si el navegador no soporta compartir archivos, se descarga la tarjeta PNG para compartir manualmente.
- QR generado **localmente**, sin servidores externos, que enlaza exclusivamente al **referido del usuario actual** recibido en `GET /api/v1/referrals`. Validación de HTTPS, host Telegram y coincidencia del código de referido. Si falta/no es válido, se omite el QR; nunca se usa el referido de otro usuario.
- Para LIVE abierta, si la interfaz recibió una cotización `hl:market-quote` del mismo símbolo en los últimos 15 s, y la orden conserva cantidad y precio de entrada, la tarjeta puede estimar **PnL bruto sin comisiones** usando el lado correcto bid/ask. Si no hay cotización reciente ni cantidad, no se inventa PnL. Se muestra ROE solo cuando el margen está registrado o puede derivarse de notional y apalancamiento registrados.
- DEMO abierta refleja PnL flotante **estimado** del estado del navegador; DEMO cerrada utiliza PnL **neto del ledger**. LIVE cerrada utiliza el `profit` guardado y nunca inventa datos ausentes.
- No se incluyen claves privadas, wallet de trading ni tokens de autenticación en la imagen ni en el QR.

## Importante sobre el QR

El sistema de referidos de esta versión previa **genera enlaces `https://t.me/<bot>?start=<codigo>`**. Por tanto, el QR lleva al **bot de Telegram con el código de referido**, no directamente a una futura URL de registro web. Al adoptar enlaces de referido web, habrá que modificar el generador y ampliar la lista segura de destinos. No se incluye un dominio web inventado.

## Archivos exactos respecto a Fase 4

**Modificados (3):**
- `app/web/static/index.html` — carga del módulo compartir y versión de assets.
- `app/web/static/app.js` — botones DEMO/LIVE y snapshots de cotizaciones WS recientes.
- `app/web/static/styles.css` — modal responsive, tarjeta y controles.

**Nuevos (5):**
- `app/web/static/trade-share/qr-code.js` — adaptación JavaScript del QR autocontenido de KAELEON.
- `app/web/static/trade-share/share-trade.js` — normalización, tarjeta Canvas, vista previa, descarga y Web Share.
- `app/web/static/trade-share/package.json` — activa modo ESM para pruebas Node.
- `tests/test_trade_share_phase5.mjs` — pruebas unitarias de datos, cotización y QR.
- `FASE_5_COMPARTIR_OPERACIONES.md` — este documento.

**Eliminados:** ninguno. **Variables de entorno nuevas:** ninguna. **Paquetes npm nuevos:** ninguno.

## Cómo ejecutar

1. En un entorno de **staging**, configurar los mismos servicios y secretos de Fase 4; no copiar claves LIVE a DEMO WORKER.
2. Instalar dependencias y construir la web: `bash build_web.sh` (requiere acceso npm y Node ≥22.12 para el gráfico KLineCharts previo).
3. Iniciar `bash start_web.sh`, `python -m app.demo.worker` como servicio separado para DEMO.
4. Entrar por Telegram OIDC, visitar `Cuenta DEMO` y `Operaciones`.
5. Abrir el modal, comprobar datos, QR personal, ocultar/mostrar métricas; descargar y compartir imagen desde móvil.
6. Verificar visualmente una operación DEMO abierta y cerrada y comparar las cifras con ledger. Repetir en LIVE con registros sintéticos en staging, **sin activar órdenes reales**.

## Pruebas efectuadas aquí

- `node --test tests/test_trade_share_phase5.mjs`: **8/8** aprobadas.
- `node --check` de los tres JS modificados/nuevos: aprobado.
- `python tools/test_demo_offline.py`: **9/9**.
- `python tools/test_market_terminal_offline.py`: **8/8**.
- `python tools/test_market_api_auth_offline.py`: **4/4**.
- `python tools/test_phase4_offline.py`: **9/9**.
- `python tools/test_web_auth_integration_offline.py`: aprobado.
- Decodificación real del bitmap QR generado por el módulo usando OpenCV: `https://t.me/TradingXHiperPro_bot?start=12345`, exactamente el valor de prueba.
- **No se pudo hacer una prueba visual real en navegador**: Playwright/Chromium devuelve `ERR_BLOCKED_BY_ADMINISTRATOR` incluso para `localhost` y `file://` en este entorno. No se validó el Web Share nativo ni la apariencia en escritorio/móvil. El gráfico compilado de Fase 4 sigue pendiente de build npm y validación. No se desplegó nada.

## Pendientes / riesgos reales

- Tarjetas compartidas son **imágenes informativas**, editables por terceros; no prueban por sí solas operaciones auténticas. Para verificación pública futura se necesitaría un link firmado de comprobación de operación sin exponer datos privados.
- En algunos registros LIVE viejos puede faltar leverage, margen, cantidad o precios; la tarjeta los deja en `—` sin inventarlos.
- El enlace del QR sigue al bot de Telegram por compatibilidad del sistema actual. Migrarlo a invitación web requerirá implementar y probar el parámetro de referido en OIDC y una URL de registro web.
- La lógica anterior sobre cierres DEMO ante desconexiones, WS de LIVE y compilación de KLineCharts conserva las limitaciones de la Fase 4; esta fase no afirma resolverlas.
