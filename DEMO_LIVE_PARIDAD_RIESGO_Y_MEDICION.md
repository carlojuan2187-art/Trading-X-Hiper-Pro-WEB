# Hyper League WEB — DEMO comparativo con las decisiones LIVE

## Alcance implementado

- **Mismo scanner/router y conjunto de estrategias LIVE**: `app.trading_engine._select_best_signal_from_scanner_shortlist(..., execution_mode='demo')`. Se ejecuta **por usuario**, no se reutiliza una señal decidida para otra cuenta.
- **Mismos filtros reutilizables de entrada**: validación de intensidad mínima, capital mínimo, spread máximo, validación de riesgo con `app.risk.validate_trade_conditions`, drift de entrada y plano SL/TP estructural con RR mínimo desde `app.trading_engine._resolve_structural_exit_plan`.
- **Disciplina de riesgo DEMO aislada**: cuotas por hora/día y cooldowns individuales, persistidos en `demo_accounts` con control de versión. Gobernador GLOBAL de LIVE, wallet, posiciones y estados de LIVE permanecen aislados.
- **Gestión de salida**: TP y SL **fijos**, que es lo que usa actualmente el manager LIVE; se ejecutan de forma simulada en cada actualización de bid/ask por WebSocket (sin polling REST). Con desconexión de mercado NO se inventan fills.
- **Ejecución paper**: entrada a ask para long y bid para short; salida con contrapartida, costes de taker y slippage configurados. No hay órdenes, cancelaciones, ni operaciones reales.
- **Scanner asíncrono frente al book**: las evaluaciones profundas se realizan en un thread del mismo worker y ya no bloquean el procesamiento WS de posiciones abiertas. No se crean servicios Railway nuevos.
- **Sin señales envejecidas**: una selección sin cotización a los 30 segundos se descarta. Si la cuenta se pausa, abre una posición, cambia de estado o incumple riesgo, el intento se descarta en la revalidación.
- **Trazabilidad**: las nuevas operaciones guardan estrategia, régimen, fuerza de señal, score, RR, spread de entrada, tarifas y modelo de ejecución. La API de DEMO devuelve `recent_strategy_metrics` con trades, wins/losses, net PnL, win rate y profit factor *solo del historial retenido* (máximo 200 operaciones).

## Diferencias inevitables DEMO / LIVE

**La equivalencia de decisiones de estrategia NO es igualdad garantizada de PnL.** El LIVE consulta y concilia posiciones y órdenes del exchange, incluye fills parciales, rechazos, liquidaciones, latencia de red y ejecución protegida de triggers. DEMO no puede reproducir esos eventos sin ejecutarlos; simula bid/ask, comisiones y slippage.

La lógica global de bloqueo de riesgo LIVE no se comparte para evitar que una pérdida de DEMO pause un usuario de LIVE o al contrario. Las credenciales DEMO solo se comprueban mediante los indicadores de disponibilidad, nunca se descifra ni usa la private key.

## Limitación de carga

`DEMO_MAX_USERS_PER_SCAN` **opcional**: 5 por defecto (rango 1–25). Si hay más usuarios activos, el scanner rota de manera equitativa. `DEMO_SCAN_SECONDS` ya controla la cadencia. Cambiar estos límites afecta recursos y latencia de entrada; no altera estrategias.

## Despliegue

Solo actualizar el servicio `MOTOR-TRADING`, con Build `pip install -r requirements.txt` y Start `python -m app.demo.worker`. Se mantienen `MONGO_URL`, `MONGO_DB_NAME`, `ADMIN_TELEGRAM_ID`, `DEMO_ENABLED=true`, `DEMO_WORKER_MODE=true`. No es necesario reiniciar WEB/API salvo que se desee exponer el campo nuevo de estadísticas por estrategia (el API serializa automáticamente las claves sin cambios en sus rutas).

No reiniciar ni modificar el bot LIVE. Para evaluar rentabilidad, dejar el capital y filtros constantes durante un número suficiente de trades y comparar tasas por estrategia después de costes, no solo win rate.

## Pruebas

`python tools/test_demo_offline.py` — pruebas aisladas sin Mongo, exchange ni claves reales. **Esto no reemplaza pruebas con el WebSocket y el exchange en producción**.
