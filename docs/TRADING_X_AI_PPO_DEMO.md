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

## Entrenar sin afectar al servidor
Usa Python 3.11. Solo en máquina de entrenamiento, NUNCA instalar PyTorch en el worker Railway:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-ai-train.txt
python tools/download_ai_candles.py --symbols BTC,ETH,SOL --days 10 --output data/trading_x_real_5m.csv
python tools/train_demo_ppo.py --csv data/trading_x_real_5m.csv --symbols BTC,ETH,SOL --steps 250000 --output models/trading_x_ppo
python -m unittest tests.test_demo_ai_ppo -v
python -m compileall -q app/demo tools/train_demo_ppo.py tools/download_ai_candles.py
```

### Alternativa sin ordenador local
Se añadió `.github/workflows/train-demo-ppo.yml` para entrenamiento **manual** en GitHub Actions usando CPU. Cuando dicho workflow esté disponible en la rama predeterminada, selecciona **Actions → Entrenar IA PPO DEMO (manual) → Run workflow**. Genera un artifact llamado `trading-x-ppo-research-unapproved` con ambos JSON y retención de 14 días. **No entrena automáticamente ni aprueba modelos**; depende de la disponibilidad/cupos de GitHub Actions y del acceso del runner al histórico real. En la presente Pull Request en borrador, el workflow aún no está integrado en `main`.

Los archivos `models/trading_x_ppo/manifest.json` y `actor.json` son artefactos del entrenamiento, NO se generan hasta correr estos comandos. El entrenador exporta `approved_for_demo=false` **siempre**, aunque la evaluación salga positiva. Requiere auditoría de calidad, replay y aprobación explícita del responsable antes de marcarlo true y entregar ambos ficheros al worker. Nunca declarar rentabilidad a partir de un único holdout.

Para un cambio de modelo, pausar nuevas entradas, dejar que terminen las posiciones actuales o confirmar la política de traspaso; el worker sigue gestionando SL/TP pero no ejecuta CLOSE de un modelo distinto al que abrió la operación.

## Configuración del servicio DEMO (NO LIVE)
Conserva variables actuales `MONGO_URL`, `MONGO_DB_NAME`, `ADMIN_TELEGRAM_ID`, `DEMO_ENABLED=true`, `DEMO_WORKER_MODE=true`, `HYPERLIQUID_WS_URL`. Agrega:

```dotenv
DEMO_DECISION_ENGINE=ai_ppo
DEMO_AI_MANIFEST=models/trading_x_ppo/manifest.json
DEMO_AI_SYMBOLS=BTC,ETH,SOL
DEMO_AI_MAX_MARGIN_USDC=25
```

El primer entrenador usa el perfil explícito: 100 USDC de capital, margen máximo 25 USDC, 10x, POSITION_PERCENT=1.0, 5 bps por comisión y 2 bps de slippage más 1 bp aproximado de spread por lado. Por seguridad la carga del modelo **rechaza** una configuración que no coincida con el perfil (incluido `MAX_TRADE_MARGIN_USDC`, que debe ser 0 o al menos 25). Si la configuración real difiere, hay que adaptar el simulador de entrenamiento y volver a entrenar antes de aprobarlo. El spread real nunca se asume constante en el worker.

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
- **No se ha entrenado ni validado un modelo real en este cambio**: no hay CSV ni checkpoint del usuario en el repositorio.
- PPO no garantiza ganancias, el simulador OHLCV no replica ordenes parcialmente llenadas, book completo ni gaps de la cuenta real.
- Entrenamiento offline por versiones, no aprendizaje incremental por operación cerrada en producción: el reentrenamiento con resultados DEMO es una fase posterior con controles contra overfitting y contaminación del holdout.
- El retorno se mide con net PnL, profit factor, max drawdown y número de trades. Resultados con pocas operaciones no acreditan mejora estadística.
- La selección de símbolos trabaja con universo autorizado (por defecto BTC/ETH/SOL) y máximo de 12 símbolos para limitar upstream y recursos. Se debe entrenar el modelo con exactamente ese universo.
