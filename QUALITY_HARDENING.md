# Trading quality hardening

Esta versión conserva la política de salida **TP fijo + SL fijo** y elimina la gestión dinámica de break-even/trailing. El endurecimiento se centra en mejorar la calidad de la entrada sin desactivar el bot.

## Cambios aplicados

1. **Velas cerradas solamente**
   - `app.market_context` conserva el timestamp de cierre `T` y elimina la vela aún abierta.
   - 5m, 15m y 1h comparten la misma fuente de datos.
   - El antiguo `app/strategies/market_context.py` quedó como wrapper del contexto canónico.

2. **Régimen por vela real**
   - La máquina de régimen usa un `bar_id` basado en la última vela cerrada.
   - Varias ejecuciones del scanner dentro de la misma vela no incrementan `pending_count`, `bars_in_active` ni cooldown.

3. **SL estructural real**
   - Breakout y Liquidity Sweep ya no recortan un stop estructural para encajarlo en un máximo pequeño.
   - Si la invalidación necesita un stop demasiado grande, el setup se rechaza con `STRUCTURAL_SL_TOO_WIDE`.

4. **TP antes de la barrera estructural**
   - El objetivo se calcula desde el riesgo real y se limita antes del nivel estructural conocido.
   - Si el objetivo disponible no permite el RR mínimo, el setup se rechaza con `STRUCTURAL_RR_TOO_LOW`.

5. **Anti-chase y revalidación del RR**
   - Se compara el precio actual con el precio de la señal.
   - Hay validación al seleccionar, justo antes de la IOC y después del fill real.
   - Si el fill consume el target o degrada el RR por debajo de `MIN_ENTRY_RR`, la posición se cierra inmediatamente por seguridad.

6. **Liquidez y ejecución**
   - Scanner filtra por volumen 24h, open interest, spread y top-book real.
   - El score de mercado usa magnitud del movimiento 24h, no solo subidas.
   - Las IOC de apertura usan límites en basis points y validan profundidad ejecutable; ya no dependen del precio nominal de la moneda.

7. **Calidad adicional de setup**
   - El score de Breakout vuelve a ser realmente filtrante.
   - Liquidity Sweep exige una confirmación algo más sólida de sweep/volumen/cuerpo y limita extensiones excesivas.

## Variables nuevas o relevantes

```env
# Entrada / anti-chase
ENTRY_MAX_DRIFT_PCT=0.0025
ENTRY_MAX_DRIFT_ATR_MULT=0.45
ENTRY_MAX_DRIFT_HARD_PCT=0.0065
ENTRY_MAX_ADVERSE_DRIFT_MULT=1.50

# Ejecución IOC
EXECUTION_MAX_SPREAD_BPS=20
EXECUTION_BOOK_IMPACT_BPS=25
EXECUTION_MIN_BOOK_COVERAGE=1.10
EXECUTION_IOC_BASE_SLIPPAGE_BPS=12
EXECUTION_IOC_RETRY_STEP_BPS=8
EXECUTION_IOC_MAX_SLIPPAGE_BPS=28
EXECUTION_EXIT_IOC_BASE_SLIPPAGE_BPS=30
EXECUTION_EXIT_IOC_MAX_SLIPPAGE_BPS=80

# Scanner
SCANNER_MIN_24H_NOTIONAL=2000000
SCANNER_MIN_OPEN_INTEREST=250000
SCANNER_MAX_SPREAD_BPS=25
SCANNER_MIN_TOP_BOOK_NOTIONAL=2000
SCANNER_SHORTLIST_DEPTH_FOR_L2=25
SCANNER_L2_CACHE_TTL=1.5

# Estructura
MTF_SL_HARD_MAX_PCT=0.0140
MTF_STRUCTURAL_TARGET_LOOKBACK=96
MTF_STRUCTURAL_TARGET_BUFFER=0.88
LIQUIDITY_SL_HARD_MAX_PCT=0.0140
LIQUIDITY_TP_HARD_MAX_PCT=0.0180
LIQUIDITY_STRUCTURAL_TARGET_BUFFER=0.86

# Datos
CLOSED_CANDLE_GRACE_MS=1500
```

Los valores son defaults. Pueden ajustarse por entorno sin tocar código.

## Verificación incluida

- `python -m unittest discover -s tests -v`
- `python -m compileall -q .`

Las pruebas cubren: filtro de vela abierta, inferencia del cierre, deduplicación de régimen por vela, guard de drift, RR absoluto y geometría de TP/SL.

## Actualización 2026-09-26

La guía vigente es `UPGRADE_2026-09-26.md`. Se conserva el tamaño por capital
configurado, sin porcentaje de pérdida ni reducción del margen por distancia al
stop. Se añadieron cobertura independiente de fills, límites de precio por RR,
cierre verificado, caché por vela, persistencia PF y opciones experimentales
explícitas. El rendimiento requiere validación histórica y de ejecución.
