# Hyper League WEB — Terminal V3: línea de precio y catálogo completo

Actualización sobre la versión Terminal PRO V2. No modifica estrategias, autenticación, órdenes ni motor LIVE/DEMO.

## Cambios
- El canvas dibuja una guía horizontal discontinua del último tick recibido por WebSocket hasta la etiqueta de precio en el eje derecho. Cuando se selecciona otro par, se borra el tick anterior. Si se revisa histórico desplazado y el precio en vivo queda fuera de la escala, la etiqueta se muestra en el borde sin deformar las velas.
- En PC y móvil, el control del símbolo tiene flecha desplegable, caja de búsqueda, accesibilidad mediante teclado y listado con scroll. El total proviene de `/api/v1/market/instruments` (contratos perpetuos públicos del catálogo base de Hyperliquid, no de un arreglo inventado).
- La API permite hasta 2.000 contratos para no truncar el catálogo por el antiguo límite de 500. En el cliente se cargan los resultados de 80 en 80 al desplazarse para evitar cientos de botones simultáneos en móvil.
- Se limpia el temporizador de búsqueda al elegir o cerrar el menú, evitando que aparezca de nuevo después de cambiar de par.

## Archivos modificados
- `app/web/static/market/chart-local.js`
- `app/web/static/market/market-terminal.js`
- `app/web/static/market/market-terminal.css`
- `app/api/routes_market.py`

## Archivos nuevos
- `tools/test_terminal_price_line_and_pairs.py`
- `MEJORA_TERMINAL_V3_PRECIO_Y_PARES.md`

## Eliminados
Ninguno.

## Despliegue Railway
Actualizar WEB/API, no MOTOR-TRADING. No añadir ni cambiar variables ni comandos.

Build: `bash build_web.sh`
Start: `bash start_web.sh`

Importante: el script de pruebas usa datos simulados y Chromium local. No constituye una comprobación de datos reales ni de conexiones en Railway.

## Pruebas
`node --check app/web/static/market/chart-local.js`
`node --check app/web/static/market/market-terminal.js`
`python tools/test_market_terminal_offline.py`
`python tools/test_terminal_price_line_and_pairs.py`

La prueba con navegador comprueba guía horizontal, dropdown de 722 símbolos simulados (paginación hasta el final), búsqueda, cambio de par y nueva suscripción WebSocket, ausencia de errores JS y ancho del desplegable en móvil.
