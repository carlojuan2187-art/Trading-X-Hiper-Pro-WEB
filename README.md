# Trading X Hiper Pro — revisión del motor 2026-10-05

Lee primero `AUDITORIA_MOTOR_2026-10-05.md` y `DESPLIEGUE_REVISION_2026-10-05.md`.
Esta revisión corrige ejecución/contabilidad y modifica la selección de setups.
No acredita rentabilidad ni autoriza por sí sola un lanzamiento comercial.
La lista exacta de archivos está en `CAMBIOS_REVISION_2026-10-05.md`.

Bot Python para Hyperliquid PERP y Telegram, con MiniApp FastAPI. Esta entrega
corrige cobertura, ejecución y diagnósticos. No constituye una validación de
rentabilidad ni una certificación de producción.

Antecedente: ajuste de confirmación M15. La confirmación M15 permite un retroceso
acotado dentro de estructura, manteniendo dirección H1 y trigger5m. Defaults
activos en código; comprobar ENTRY_PROFILE después del deploy. Incluye los cambios
anteriores. Los pendientes de producción documentados siguen abiertos.

## Estrategias reales

- `mtf_simple`: continuación tras retroceso, con contexto 1H/15M y entrada 5M.
- `liquidity_sweep_reversal`: barrido y recuperación en régimen volátil.
- `range_mean_reversion`: reversión hacia VWAP; observación por defecto.
- TP y SL fijos por operación. Sin trailing ni break-even en el motor.

## Instalar y probar

Python 3.11 es el runtime declarado por el proyecto.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m compileall -q app tools main.py web_main.py
```

En Windows, activa `.venv\Scripts\activate` en lugar del comando de activación
anterior. Las pruebas offline utilizan solo la biblioteca estándar; no requieren
cuentas, MongoDB, Telegram ni órdenes reales.

Configura las variables de `.env.example` en el proveedor o en el entorno del
proceso. El proyecto **no carga automáticamente** un archivo `.env`.

```bash
python main.py       # bot y motor: requiere las credenciales del despliegue
python web_main.py   # MiniApp/API: proceso separado
```

No ejecutes `main.py` con credenciales reales para una simple comprobación: inicia
el motor. Usa las pruebas offline para verificar el código.

## Antes de actualizar Railway

Lee `UPGRADE_2026-09-26.md`: contiene los cambios de comportamiento, las variables
nuevas, las pruebas realizadas y el procedimiento de actualización/rollback.
Mantén **un solo proceso/réplica del bot de trading**. La API puede ir separada.
Los locks, la cobertura y la caché son locales al proceso; esta versión no añade
coordinación distribuida entre varios motores.

## Configuración principal

- Margen = saldo elegible × `POSITION_PERCENT`, limitado por el saldo y `MAX_TRADE_MARGIN_USDC` si es positivo.
- Entrada mínima del adapter: 10.20 USDC de nominal, después del formato.
- Una posición a la vez por usuario en el flujo actual del motor.
- `MAX_TRADES_PER_DAY=0` quita la cuota diaria; no quita protecciones de riesgo.
- `STRATEGY_ROUTER_RANGE_LIVE_ENABLED=false` mantiene rango en observación.
- Las opciones experimentales de tendencia están documentadas en la guía.

Con `POSITION_PERCENT=1.0` y `MAX_TRADE_MARGIN_USDC=3`, se usan 3 USDC de
margen si hay saldo elegible suficiente. A 10x, el nominal objetivo es 30 USDC.
El stop no reduce ese importe. El redondeo de cantidad y el precio del fill pueden
producir pequeñas diferencias en el margen ejecutado. Un nominal inferior al mínimo
se informa como `CONFIGURED_NOTIONAL_BELOW_EXCHANGE_MIN`.

## Investigación offline

`tools/replay_ohlcv.py` evalúa las estrategias reales con CSV históricos locales.
No tiene conexión al exchange. Necesita velas continuas 5m y BTC como contexto.
Incluye entrada a la apertura siguiente, costes explícitos y resolución conservadora
cuando SL y TP se tocan en la misma vela. No simula libro de órdenes, funding,
scanner, cartera multiusuario ni gobernador de riesgo. Consulta `--help` y la guía.

El código anterior se conserva en la versión previa del archivo entregado; los
archivos de interfaz no fueron modificados.
