# Trading X Pro — IA autónoma PPO para DEMO (investigación)

## Alcance
- El servicio `app.demo.worker` usa exclusivamente el actor PPO cuando `DEMO_DECISION_ENGINE=ai_ppo`.
- El modelo decide LONG, SHORT, WAIT, CLOSE y dos multiplicadores discretos de ATR para SL y TP.
- **No hay scanner/router ni estrategias originales decidiendo entradas/salidas en este modo.**
- Se mantiene ledger DEMO aislado, WebSocket de book, fees, slippage, alertas, historiales, API y barreras de riesgo.
- LIVE no se toca. El viejo método `tick()` conserva compatibilidad de pruebas, pero el worker real no lo llama.
- Un modelo ausente, corrupto, no aprobado o con símbolos ajenos **detiene el arranque**; nunca cambia silenciosamente a las estrategias previas.

## Arquitectura
1. Offline: históricos reales 5m de Hyperliquid -> CSV continuo validado.
2. Offline: PPO Stable-Baselines3 / Gymnasium, política MultiDiscrete [4,3,3]. Entradas observadas solo en velas completadas; holdout cronológico final 25%. La simulación aplica comisiones/slippage, pero no reproduce completamente gaps, order book, funding o liquidaciones.
3. Exportación: `actor.json` portable con capas Tanh y `manifest.json` con SHA256. Ambos son inseparables. Predicción determinista, sin exploración aleatoria en DEMO.
4. Worker: bootstrap inicial de 65 velas por REST una vez al conectar; después datos de velas y bid/ask por WebSocket. Las cotizaciones caducadas y las series incompletas impiden operar.
5. Per-user: la IA evalúa los símbolos permitidos, selecciona una acción favorable; ejecuciones paper mediante libro real y coste modelado. La IA puede pedir CLOSE por vela cerrada. Stop y TP permanecen garantizados *como reglas del simulador*, no como garantías de ejecución real.

## Entrenar con el universo dinámico del LIVE, sin afectar al servidor
El universo no es BTC, ETH y SOL. El scanner LIVE construye su lista a partir
de `metaAndAssetCtxs`, `app.symbol_policy`, volumen mínimo diario de 2M USDC e
interés abierto nocional mínimo de 250k USDC. Sus valores configurables
`SCANNER_*` se respetan también en el recopilador offline. LIVE examina hasta
25 libros L2 por ciclo, con rotación: **no debemos entrenar únicamente esos
25 pares momentáneos**, sino el conjunto completo que supera el prefiltrado,
antes de la evaluación L2, para cubrir los candidatos de las siguientes rondas.
La lista cambia con el mercado; no se utiliza un número fijo de símbolos.

Comandos para Python 3.11, **solo en máquina de entrenamiento, nunca en Railway**:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-ai-train.txt
python -m tools.download_ai_candles --universe live --days 16 --min-symbols 10 --min-coverage 0.8 --output data/trading_x_real_5m.csv --report data/trading_x_live_universe.json
python -m tools.train_demo_ppo --csv data/trading_x_real_5m.csv --symbols auto --universe-report data/trading_x_live_universe.json --steps 100000 --output models/trading_x_ppo
python -m unittest discover -s tests -p 'test_*ai*.py' -v
```

El workflow `.github/workflows/train-demo-ppo.yml` permite entrenar con
**`universe=live`** (opción predeterminada al lanzar manualmente el workflow
desde `main`, cuando sea integrado). En PR #24 se permiten pruebas
aisladas, a 20.000 pasos para validar la infraestructura sin publicarla.
Guarda dos artifacts: `trading-x-live-universe-coverage` (solicitados,
historial descargado, excluidos, razón y cobertura) y
`trading-x-ppo-research-unapproved` (actor y manifest). Retención: 14 días.

Si alguno de los símbolos recién listados carece de 1.500 velas de 5m
continuas, se registra explícitamente como excluido. El entrenamiento aborta
si se consiguen menos de 10 mercados o menos de un 80% de cobertura:
**no se puede presentar como cobertura total de LIVE si hay exclusiones**.
Para superar esta limitación se necesita ampliar el almacenamiento histórico
de datos y realizar un plan de incorporación/reentrenamiento de nuevos pares.
El informe y el manifest identifican el universo observado y su momento de
selección. Que un mercado pase los umbrales hoy no garantiza que fuese elegible
en todas las velas históricas (sesgo de selección). Tampoco se confunde
preselección con aptitud de ejecución real: el spread y profundidad se validan
por separado en el motor paper.

Los archivos `models/trading_x_ppo/manifest.json` y `actor.json` son artefactos del entrenamiento, NO se generan hasta correr estos comandos. El entrenador exporta `approved_for_demo=false` **siempre**, aunque la evaluación salga positiva. Requiere auditoría de calidad, replay y aprobación explícita del responsable antes de marcarlo true y entregar ambos ficheros al worker. Nunca declarar rentabilidad a partir de un único holdout.

Para un cambio de modelo, pausar nuevas entradas, dejar que terminen las posiciones actuales o confirmar la política de traspaso; el worker sigue gestionando SL/TP pero no ejecuta CLOSE de un modelo distinto al que abrió la operación.

## Configuración del servicio DEMO (NO LIVE)
Conserva variables actuales `MONGO_URL`, `MONGO_DB_NAME`, `ADMIN_TELEGRAM_ID`, `DEMO_ENABLED=true`, `DEMO_WORKER_MODE=true`, `HYPERLIQUID_WS_URL`. Agrega:

```dotenv
DEMO_DECISION_ENGINE=ai_ppo
DEMO_AI_MANIFEST=models/trading_x_ppo/manifest.json
DEMO_AI_SYMBOLS=AUTO
DEMO_AI_MAX_MARGIN_USDC=25
```

El entrenador usa por defecto 100 USDC de capital, 25 USDC de margen máximo, 10x, `POSITION_PERCENT=1.0`, 5 bps por comisión, 2 bps de slippage y 1 bp estimado de spread por lado. **Los parámetros pueden ajustarse al worker DEMO** mediante `--initial-capital`, `--margin-cap`, `--leverage`, `--position-percent`, `--taker-fee-bps`, `--slippage-bps` y `--assumed-spread-bps`. Por ejemplo, si el worker limita el margen a 3 USDC, entrena con `--margin-cap 3` y confirma que su apalancamiento, fees y `POSITION_PERCENT` coinciden. El servidor rechaza modelos con perfil incompatible; el spread real se utiliza para la ejecución DEMO y no se asume constante.

Build: `pip install -r requirements.txt`
Start: `python -m app.demo.worker`
Replica: 1 (el lease de Mongo evita colisiones). Asegura que `actor.json` y `manifest.json` aprobados estén disponibles en el mismo directorio del contenedor. El worker no instala dependencies de entrenamiento y no necesita API key de exchange para el paper trading.

## Activación segura, roll back
1. Revisar diffs y ejecutar suite de CI.
2. Verificar datos, estabilidad de datos faltantes, pesos y holdout **por símbolo** en período fuera de muestra.
3. Validar entradas/SL/TP/CLOSE/capital en DEMO sin credenciales LIVE; observar 24-48h y después muestras mayores.
4. Mantener `main` y LIVE intactos hasta superar pruebas.
5. Si el worker AI falla, **no activar el motor viejo**: pausar DEMO, preservar posiciones y rollback explícito a una versión verificada. No eliminar ni resetear historial con posiciones abiertas.

## Limitaciones reconocidas
- Las pruebas PPO de BTC/ETH/SOL iniciales no prueban rentabilidad ni cobertura LIVE. Los modelos de investigación generados en GitHub Actions permanecen no aprobados y se almacenan como artifacts, no como archivos comprometidos en el repositorio.
- PPO no garantiza ganancias, el simulador OHLCV no replica ordenes parcialmente llenadas, book completo ni gaps de la cuenta real.
- Entrenamiento offline por versiones, no aprendizaje incremental por operación cerrada en producción: el reentrenamiento con resultados DEMO es una fase posterior con controles contra overfitting y contaminación del holdout.
- El retorno se mide con net PnL, profit factor, max drawdown y número de trades. Resultados con pocas operaciones no acreditan mejora estadística.
- `DEMO_AI_SYMBOLS=AUTO` (o variable ausente) usa todos los mercados entrenados y aprobados incluidos en el manifest. La lista se congela por versión del modelo, con un límite técnico de 250, y requiere observar carga de WebSocket, REST inicial y recursos del worker antes de activarse. Un símbolo nuevo no entrenado no puede operar sin un nuevo checkpoint aprobado. Puede restringirse a un subconjunto explícito, pero ya no se vuelve silenciosamente a BTC/ETH/SOL.
- El universo LIVE elegible rota con el tiempo y la ventana histórica de Hyperliquid limita las velas M5 recientes disponibles. Ampliar la cobertura y el historial es distinto de garantizar ventaja predictiva.
