# Verificación realizada

Fecha: 2026-09-28. Entorno de verificación: Python 3.12.14.
Runtime declarado de despliegue: Python 3.11 (no se cambió).

- `python -m unittest discover -s tests -v`: 80 pruebas, todas aprobadas.
- `python -m compileall -q app tools main.py web_main.py`: código de salida 0.
- Replay offline completo sobre CSV sintéticos de 3.020 velas BTC/SOL: completado,
  0 trades en esa serie. Comprueba el flujo, no el rendimiento.
- Comparación AST: preservadas las funciones y clases de módulo existentes.
- Comparación estática de referencias globales: ninguna referencia global nueva
  sin definición frente a la versión original. No equivale a un lint completo.
- Tamaño comprobado: margen completo de 3 USDC con el tope configurado,
  límite por saldo, porcentaje configurado y rechazo de capital no finito.
- IOC probada con firma/transporte simulados, límites protectores de precio,
  reducción de residuales y contrato del signer preservado.
- Integración con contextos sintéticos de MTF y rango, caché concurrente,
  lectura estricta de posiciones, persistencia simulada y recuperación de cooldown.

No se probaron MongoDB, Telegram o Hyperliquid reales, ni se enviaron órdenes.
No se instaló el conjunto completo de dependencias de producción. No se ejecutó
un type checker ni un linter externo. Hay avisos de deprecación de `utcnow()` en
Python 3.12; no son fallos de las pruebas. La validación en Python 3.11 y en el
entorno del despliegue queda pendiente. Los módulos financieros más extensos
se prueban por funciones aisladas para evitar efectos de importación externos.

El estado de riesgo nuevo comienza a persistirse con esta versión. En la primera
actualización no se reconstruye automáticamente el estado en memoria perdido de
la versión anterior. Las variantes experimentales quedan desactivadas de forma
predeterminada hasta validación temporal con datos históricos reales.

Revisión 27/09: equivalencia del filtro H1/15M en 1.000 contextos sintéticos,
límite precio=EMA20 y expiración de rechazos de régimen al cambiar de vela.

Liquidity Sweep: 2.000 evaluaciones sintéticas comparadas con la entrega previa;
decisiones idénticas, incluidos 26 candidatos aceptados con niveles idénticos.
Pruebas de diagnóstico sin barrido, intento alternativo oculto y estados del router.

28/09: scanner vacío vs error, timeout por request, recuperación de workers tras
timeout, validación de autenticación y comparación H1. Instalación completa
intentada pero bloqueada por timeout del proxy de descarga; no hubo E2E.

Revisión de la tarde: transición H1 LONG/SHORT y bloqueos adicionales, integración
MTF completa y fallback Sweep con score. Nuevos defaults de estrategia; no backtest.

Release M15 pullback: 80 pruebas; LONG/SHORT, límites estructurales y ATR,
confirmación5m obligatoria e integración MTF. No hay backtest ni E2E real nuevo.
