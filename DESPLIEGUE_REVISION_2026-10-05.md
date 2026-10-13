# Ejecutar, comprobar y evaluar la revisión

## Instalación local

Desde la raíz del proyecto, con Python 3.11 según el runtime declarado:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q app tools tests main.py web_main.py
node --check app/web/static/app.js
node tests/test_frontend_escape.cjs
```

Las pruebas Python offline no necesitan instalar las dependencias de producción. Node solo se necesita para las comprobaciones JavaScript; no se añade al runtime Python del bot. No hay nuevas dependencias de producción ni migraciones de esquema obligatorias en esta entrega.

No ejecutar `python main.py` con credenciales reales para hacer pruebas: inicia el bot/motor. `python web_main.py` inicia la API/MiniApp. El proyecto no carga `.env` automáticamente; `.env.example` es una referencia.

## Variables nuevas (opcionales, con default)

| Variable | Default | Función |
|---|---|---|
| `STRATEGY_ROUTER_SETUP_FIRST_ENABLED` | `true` | Permite estrategia alternativa completa en regímenes conocidos tras rechazar la primaria. |
| `MTF_H1_PULLBACK_ENABLED` | `true` | Permite retroceso H1 acotado conservando la estructura requerida. |
| `MTF_H1_PULLBACK_MAX_ATR` | `0.50` | Distancia máxima adversa a EMA20 en esa vía, medida en ATR H1. |
| `SOFTWARE_EXIT_GUARD_ENABLED` | `true` | Respaldo reduce-only al cruzar TP/SL con cotización bid/ask válida. |

No hay variables nuevas obligatorias, variables a eliminar ni variables nuevas de frontend.

## Variables existentes que deben comprobarse

```dotenv
STRATEGY_ROUTER_MODE=enforced
STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED=true
STRATEGY_ROUTER_RANGE_CONTINUATION_ENABLED=true
STRATEGY_ROUTER_RANGE_LIVE_ENABLED=false
MTF_H1_REQUIRE_EMA200=true
MTF_H1_TRANSITION_ENABLED=true
MTF_H1_TRANSITION_ADX_MIN=16
MTF_M15_REQUIRE_EMA200=true
MTF_M15_PULLBACK_ENABLED=true
MIN_ENTRY_RR=1.15
MTF_RR_MIN=1.15
LIQUIDITY_MIN_FINAL_RR=1.15
ENTRY_RR_RESERVE=0.05
```

Estos son valores de referencia de esta revisión, no valores observados en Railway. Una variable ya definida allí prevalece. Para ver las principales variables efectivas y su origen, sin exponer secretos:

```bash
python tools/audit_runtime_config.py
```

Los defaults de cuota siguen siendo 4 operaciones/hora y 12/día; `0` desactiva la cuota correspondiente. La cuota de 12 no explica por sí sola operar dos o tres veces. No se retiraron frenos por pérdidas ni límites de liquidez para aumentar artificialmente la frecuencia.

Mantener los valores reales de `TELEGRAM_BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `MONGO_URL`, `MONGO_DB_NAME`, `WEBAPP_SESSION_SECRET`, `PRIVATE_KEY_ENCRYPTION_SECRET`, `MINIAPP_URL` y `MINIAPP_ALLOWED_ORIGINS`. Los tres primeros son exigidos por la configuración al importar. No sustituirlos por placeholders del ejemplo. No rotar secretos al instalar esta revisión.

Mantener explícitos `POSITION_PERCENT`, `MAX_TRADE_MARGIN_USDC`, `MIN_CAPITAL_USDC` y `LEVERAGE`. La revisión no altera esos parámetros. El ejemplo de margen de 3 USDC no es una orden de cambiar el tamaño actual ni una garantía sobre pérdidas máximas.

## Evaluación histórica reproducible

Preparar `data/BTC.csv` y `data/SOL.csv` (y repetir para otros símbolos): columnas `t,o,h,l,c,v`, velas consecutivas 5m, timestamp UTC de apertura en milisegundos. Aportar al menos 240 horas previas para calentar H1. No se incluyen datos de mercado inventados en el paquete.

Ejemplo para bash, costes ilustrativos a sustituir por los reales:

```bash
python tools/replay_ohlcv.py --csv-dir data --symbol SOL --strategy router --fee-bps 5 --slippage-bps 2 --platform-fee-rate 0.15 --output results/revised.json
MTF_H1_PULLBACK_ENABLED=false STRATEGY_ROUTER_SETUP_FIRST_ENABLED=false python tools/replay_ohlcv.py --csv-dir data --symbol SOL --strategy router --fee-bps 5 --slippage-bps 2 --platform-fee-rate 0.15 --output results/selection-rollback.json
```

La segunda ejecución revierte solo la selección H1/router; conserva las correcciones de stops y datos. Para una comparación exacta contra el código original, ejecutar el replay original en otra carpeta con la misma entrada y comparar costes con cuidado: el original no incluye el flag de fee de plataforma. No mezclar sus métricas sin ajustar esa diferencia.

No sumar replays independientes de símbolos como si fueran una cartera ejecutable: el motor solo permite una posición por usuario y aplica otros límites. Evaluar expectancy R, ganancia/pérdida media, profit factor, drawdown y número de trades fuera de muestra; no elegir parámetros por el mejor win rate del mismo período de ajuste.

## Actualización controlada y rollback

1. Conservar una copia de la versión y variables actuales. Completar primero los pendientes críticos del informe para un lanzamiento comercial.
2. Para una prueba de la revisión, detener nuevas entradas desde el panel conservando la gestión de posiciones abiertas. Esperar a que no haya posición ni cierre pendiente.
3. Actualizar el conjunto completo, incluidos `app/exit_guard.py` y `app/trade_accounting.py`. No hacer redeploy entre archivos dependientes.
4. Conservar una sola réplica del bot. API y bot siguen usando los entrypoints existentes; no se cambia la topología.
5. Tras reiniciar, comprobar parámetros, salud de API/bot, acceso MongoDB, ausencia de posiciones huérfanas y la identificación de estrategias revisadas.
6. Verificar la primera operación controlada desde fills: cantidad abierta = cantidad cerrada; comisiones de entrada + salida; PnL bruto y neto trading. El funding y el cargo de plataforma se concilian por separado.
7. Si aparece `RECONCILIATION_PENDING`, comprobar los fills y el contexto conservado antes de reactivar. No borrar estado para ocultar el problema. Casos truncados a 2.000 fills o posiciones manuales requieren conciliación asistida.

Rollback de hipótesis de selección, sin perder correcciones operativas:

```dotenv
STRATEGY_ROUTER_SETUP_FIRST_ENABLED=false
MTF_H1_PULLBACK_ENABLED=false
SOFTWARE_EXIT_GUARD_ENABLED=true
```

Un rollback completo requiere restaurar el paquete anterior en una ventana sin operaciones abiertas. Esta sesión no desplegó ni envió órdenes; las comprobaciones de Railway/exchange siguen pendientes.
