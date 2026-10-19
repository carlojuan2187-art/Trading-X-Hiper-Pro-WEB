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
`trading-x-ppo-research-unapproved` (actor, manifest y checkpoint PPO completo para reanudar). Retención: 14 días.

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

Los archivos `models/trading_x_ppo/manifest.json`, `actor.json` y `ppo_model.zip` son artefactos del entrenamiento, NO se generan hasta correr estos comandos. El entrenador exporta `approved_for_demo=false` **siempre**, aunque la evaluación salga positiva. Requiere auditoría de calidad, replay y aprobación explícita del responsable antes de marcarlo true y entregar ambos ficheros al worker. Nunca declarar rentabilidad a partir de un único holdout.

Para un cambio de modelo, pausar nuevas entradas, dejar que terminen las posiciones actuales o confirmar la política de traspaso; el worker sigue gestionando SL/TP pero no ejecuta CLOSE de un modelo distinto al que abrió la operación.

## Aprendizaje continuado desde operaciones DEMO cerradas (sin acceso LIVE)

**El objetivo es que los cierres de la propia IA se conviertan en datos de
aprendizaje, no que se pierdan ni se sustituyan por señales algorítmicas.**

1. Cuando PPO abre una operación paper, el worker almacena la observación
   real de 13 variables, dirección, sello de vela y versión del modelo.
2. Cuando se cierra por TP, SL o CLOSE de PPO, `app.demo.ledger.save` guarda
   el trade con CAS y después archiva un registro idempotente en la colección
   privada `demo_ai_experiences`. No se guarda ninguna clave, token ni
   identificador de usuario. Fallar al archivar nunca retrasa un cierre.
3. En un host de entrenamiento seguro, con `MONGO_URL` y `MONGO_DB_NAME`,
   exporta el registro acumulativo SIN subirlo a GitHub:

```bash
python -m tools.export_demo_ai_experiences --output private/demo_experiences_001.jsonl
```

4. Reentrena el PPO en los históricos reales actualizados, con el **universo
   LIVE elegible**, y añade el aprendizaje auxiliar de las operaciones DEMO:

```bash
python -m tools.download_ai_candles --universe live --days 16 --output data/trading_x_real_5m.csv --report data/trading_x_live_universe.json
python -m tools.train_demo_ppo --symbols auto --csv data/trading_x_real_5m.csv --universe-report data/trading_x_live_universe.json --steps 250000 --demo-feedback private/demo_experiences_001.jsonl --min-feedback-trades 30 --output models/trading_x_ppo
```

**Importante:** PPO es `on-policy` y no es válido fingir que operaciones
anteriores son trayectorias `on-policy` del modelo actual. El nuevo
`ppo_demo_feedback.py` usa un **ajuste auxiliar off-policy, conservador,
basado en PnL neto real y penalizado por divergencia KL**: refuerza con
moderación acciones rentables y reduce el peso de acciones con pérdidas.
Al menos 30 trades AI cerrados válidos son necesarios antes de aplicarlo;
mientras haya menos, se preservan sin inventar feedback ni forzar operaciones.
El PPO continúa entrenándose en el simulador histórico normal.

### Continuar el aprendizaje entre versiones sin empezar de cero

El entrenador guarda un tercer archivo exclusivo de investigación:
`ppo_model.zip`, con la política PPO, su crítico y estado del optimizador.
El siguiente reentrenamiento puede cargarlo con `--resume-model` y seguir
aprendiendo con velas más recientes y experiencias DEMO acumuladas:

```bash
python -m tools.train_demo_ppo --symbols auto --csv data/trading_x_real_5m.csv --universe-report data/trading_x_live_universe.json --steps 250000 --resume-model ruta/checkpoint_anterior --demo-feedback private/demo_experiences_002.jsonl --output models/trading_x_ppo
```

Requiere `ppo_model.zip` + `manifest.json` de un entrenador propio y de
confianza, con SHA256 verificado, mismo contrato de observación/acciones y
perfil de riesgo. **No cargar checkpoints de PyTorch de fuentes no confiables.**
El worker DEMO únicamente necesita `actor.json` + `manifest.json`: la
dependencia pesada queda en el entorno de entrenamiento. El modo sin
`--resume-model` entrena desde cero, para tener una referencia reproducible.

El informe marca cualquier solapamiento de experiencias DEMO con el holdout
histórico. Si existe, **la evaluación deja de ser prospectiva** y no puede
aprobar el modelo; requiere nuevas velas futuras sin contaminación.
No hay aprobado automático ni garantía de mejora de rentabilidad.

El workflow público de GitHub Actions solo entrena desde datos públicos.
**Nunca se suministran allí secretos de Mongo ni los registros privados**.
El refinamiento privado es una capacidad implementada, pero su ciclo
programado/despliegue en un entorno seguro se debe configurar por separado.
No se instalará PyTorch en el worker que gestiona SL/TP.

### Qué permanece sin cambiar

- DEMO sigue siendo un entorno paper, sin órdenes reales.
- PPO toma decisiones de entrada/salida; no vuelve el router algorítmico.
- Si un candidato nuevo falla la calidad o no tiene suficientes operaciones,
  **no se sustituye automáticamente un modelo PPO anterior que esté
  funcionando**. Se conservan los registros de aprendizaje y el historial.
- LIVE continúa sin cambios. No se resetean estadísticas para ocultar pérdidas.

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
- Las operaciones DEMO cerradas pueden alimentar el refinamiento privado off-policy del PPO; el reentrenamiento recurrente automático y su entrega segura a Railway aún no están configurados ni desplegados. Se exige comprobación fuera de muestra posterior a esos cierres.
- El retorno se mide con net PnL, profit factor, max drawdown y número de trades. Resultados con pocas operaciones no acreditan mejora estadística.
- `DEMO_AI_SYMBOLS=AUTO` (o variable ausente) usa todos los mercados entrenados y aprobados incluidos en el manifest. La lista se congela por versión del modelo, con un límite técnico de 250, y requiere observar carga de WebSocket, REST inicial y recursos del worker antes de activarse. Un símbolo nuevo no entrenado no puede operar sin un nuevo checkpoint aprobado. Puede restringirse a un subconjunto explícito, pero ya no se vuelve silenciosamente a BTC/ETH/SOL.
- El universo LIVE elegible rota con el tiempo y la ventana histórica de Hyperliquid limita las velas M5 recientes disponibles. Ampliar la cobertura y el historial es distinto de garantizar ventaja predictiva.
