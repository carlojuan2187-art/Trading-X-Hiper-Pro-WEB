# Auditoría: observabilidad del scanner (14-10-2026)

## Objetivo
Medir por qué el motor DEMO de Hyper League genera pocos candidatos sin modificar decisiones de trading.

## Observación en Railway
En 133 ciclos con candidatos se registraron 447 evaluaciones; 164 rechazos H1 y 155 M15. En otros 41 ciclos el scanner no devolvió candidatos. Estos números son observaciones de logs, no una estimación de rentabilidad futura.

## Instrumentación
`app/market_scanner.py` publica `SCANNER_FUNNEL` con mercados totales, exclusiones, políticas de símbolo, volumen mínimo, interés abierto, prefiltrado, muestras L2, descartes por presupuesto, datos L2 no disponibles, spread, profundidad y shortlist final. El resumen sale como máximo una vez cada 300 segundos (valor predeterminado); el snapshot se actualiza en cada refresco y también para mercados no disponibles.

`app/demo/worker.py` reduce el ruido de mensajes HTTP 200 de `httpx`, preservando WARNING y ERROR.

## Validación
Ejecutar `python tools/test_scanner_funnel_offline.py` y `python -m py_compile app/market_scanner.py app/demo/worker.py`.

## Restricciones
No cambia H1, M15, Liquidity Sweep, entradas, salidas, SL, TP, apalancamiento, parámetros de riesgo, autenticación ni ejecución LIVE. No crea servicios ni exige variables nuevas.

## Siguiente paso
Revisar en Railway los valores `SCANNER_FUNNEL` por varios ciclos y cruzarlos con `SCAN_DIAGNOSTICS` para diseñar experimentos aislados en DEMO.
