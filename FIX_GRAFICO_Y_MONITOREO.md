# Hyper League WEB — Corrección del gráfico y monitor de estado

Base: `carlojuan2187-art/Trading-X-Hiper-Pro-WEB`, rama `main`, Fase 6C.

- El gráfico se sirve desde `app/web/static/market/market-terminal.js`, ya funcional y versionado. Intenta KLineCharts 10.0.3 como mejora visual; si no está disponible la biblioteca externa, carga `chart-local.js`, que dibuja velas, volumen y niveles de operaciones sin npm.
- `build_web.sh` deja de exigir npm y comprueba que estén presentes los assets reales. `start_web.sh` tampoco compila nada en cada reinicio: falla solo si faltan los archivos necesarios.
- WEB/API publica heartbeat de `telegram_bot` al tener el webhook registrado y activo.
- MOTOR-DEMO publica heartbeats `demo_worker` y `demo_scanner` desde el líder conectado a WebSocket. El panel Sistema muestra estos componentes y deja de buscar los procesos LIVE no habilitados.
- La conexión WebSocket y la ejecución de operaciones LIVE no se modifican; solo la escritura de estado del worker DEMO.

## Despliegue

Subir los archivos del parche a las rutas exactas indicadas. Desplegar primero WEB/API (`bash build_web.sh`, `bash start_web.sh`) y después MOTOR-TRADING (`pip install -r requirements.txt`, `python -m app.demo.worker`) desde el mismo commit. No agregar variables ni servicios.

Antes de declarar éxito, comprobar en la web real que el gráfico pinta velas en DEMO/Trades, que el servicio WEB/API no sirve un placeholder y que el panel Sistema actualiza los estados después de ~1 minuto.

No se ha efectuado despliegue real en Railway; pruebas offline de navegador realizadas con datos simulados. No se probaron conexión real a Hyperliquid ni flujos con MongoDB/Telegram reales.
