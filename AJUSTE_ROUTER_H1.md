# Ajuste acotado de router y transición H1

Base: Trading-X-Hiper-Pro-retroceso.zip.

1. RANGE sin estrategia live mapeada ahora permite evaluar MTF completo. La etiqueta RANGE deja de ser veto automático. Deben pasar H1, M15, retroceso, confirmación, extensión, score, stop y RR; no se activa mean reversion. UNKNOWN y datos inválidos conservan sus bloqueos. Si se habilitó explícitamente range_mean_reversion live se respeta su mapeo. La decisión queda registrada como range_continuation_full_validation.
2. Transición H1: ADX mínimo predeterminado 18 -> 16. Solo aplica cuando el veto pendiente es EMA200; conserva pendientes favorables y dos velas cerradas alineadas. La vía estricta no cambia. No garantiza superar H1 en todos los casos: cruces EMA20/50 contrarios, falta de confirmación y otros fallos siguen rechazándose.

No modifica capital, apalancamiento, mínimos de RR, stops ni parámetros de liquidity sweep. Una clasificación RANGE con tendencia H1 válida puede ahora producir una entrada; eso incrementa exposición a falsas continuaciones y debe evaluarse con los informes de apertura y cierre. No se ha probado mejora de rentabilidad ni se ha hecho backtest histórico.

## Railway
STRATEGY_ROUTER_RANGE_CONTINUATION_ENABLED=true
MTF_H1_TRANSITION_ENABLED=true
MTF_H1_TRANSITION_ADX_MIN=16

Los valores anteriores son los nuevos predeterminados. Si existen variables en Railway deben actualizarse; prevalecen sobre el archivo. STRATEGY_ROUTER_MODE=enforced puede permanecer. ALLOW_RANGE_CANDIDATE no controla esta nueva ruta: permite candidatos de régimen, no la evaluación MTF dentro de RANGE.

Reversión: STRATEGY_ROUTER_RANGE_CONTINUATION_ENABLED=false y MTF_H1_TRANSITION_ADX_MIN=18.

## Archivos tocados respecto a la entrega anterior
.env.example
app/strategies/router.py
app/strategies/breakout_reset.py
tests/test_strategy_contracts.py
AJUSTE_ROUTER_H1.md (nuevo)

Validación local: 93 pruebas aprobadas; compilación correcta. Pruebas nuevas: RANGE acepta señal completa, conserva rechazo H1, desactivar restaura veto; ADX17 permite transición conservando condición de precio. No se enviaron órdenes ni se desplegó desde aquí.
Ejecutar desde la raíz: python -m unittest discover -s tests -q
