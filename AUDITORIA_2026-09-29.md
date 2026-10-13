# Auditoría del ZIP que está ejecutándose

Base: Trading-X-Hiper-Pro-main(2).zip. Comparado por contenido con la última entrega: todo el código de producción coincide; difieren un archivo de pruebas y la presencia de cuatro documentos. No se ha demostrado un despliegue incompleto.

## Resultado comprobado

El último registro cubre 2026-09-29 02:55:03–03:41:14 UTC, unos 46 minutos. En sus 114 resúmenes de escaneo aparecen 330 rechazos repetidos, incluyendo distintos usuarios y caché; no representan 330 oportunidades independientes.

| Motivo | Rechazos registrados |
|---|---:|
| Sin confirmación de continuación 5m | 82 |
| Régimen de rango bloqueado | 52 |
| Sin sesgo 1h | 52 |
| Relación beneficio/riesgo estructural insuficiente | 36 |
| Sin toque de reset 5m | 30 |
| Régimen desconocido bloqueado | 24 |
| Reset demasiado profundo | 18 |
| Precio demasiado extendido | 18 |
| Sin patrón liquidity sweep | 14 |
| Sin sesgo 15m | 4 |

No hay agotamiento de presupuesto del escaneo ni mensajes de bloqueo del gobernador en este fragmento. No aparecen señales aceptadas ni intentos fallidos de envío de órdenes. Esto sitúa el bloqueo observado antes de la ejecución, pero no demuestra el comportamiento durante las 30 horas completas.

El router en modo enforced no tiene estrategia en vivo para RANGE por defecto; UNKNOWN también se bloquea. Liquidity sweep tiene probe habilitado en el registro, pero su evaluación depende del régimen y de las condiciones del probe. Se registran 40 evaluaciones rechazadas y 53 casos NOT_ROUTED. Habilitar el probe no equivale a evaluar sweep en todos los mercados.

La estrategia MTF exige sesgo 1h, confirmación 15m, reset 5m, vela de continuación, extensión limitada, stop válido, score y RR. Cumplir el precio de confirmación no basta: la vela también debe cerrar del lado correcto de EMA20 y tener el color requerido. El registro anterior omitía esos valores, por lo que un NO_5M_CONTINUATION_CONFIRM no permitía saber cuál condición falló.

La señal exige RR mínimo 1.15 + reserva 0.05 = 1.20, aunque el diagnóstico devolvía 1.15. El motor tampoco imprimía el detalle de STRUCTURAL_RR_TOO_LOW. No es posible distinguir con ese registro entre un objetivo estructural demasiado cercano y un rechazo marginal causado por la reserva. No se ha probado que la reserva explique la ausencia de operaciones.

## Cambios de esta entrega

1. app/strategies/breakout_reset.py: diagnóstico con apertura, máximo, mínimo, EMA20, ATR y resultado de las condiciones de continuación; RR muestra el umbral real, reserva, referencia, stop, beneficio requerido y si el objetivo estructural limita la operación.
2. app/trading_engine.py: REJECTION_DIAGNOSTICS en cada resultado fresco rechazado, con cierre de vela, estrategia, régimen, valores medidos e intentos de estrategias secundarias. La caché no vuelve a imprimir el diagnóstico. No incluye credenciales ni datos de cuenta.
3. tests/test_strategy_contracts.py: dos pruebas nuevas para el RR realmente exigido y una vela cuyo precio confirma pero cuyo color invalida la continuación.
4. AUDITORIA_2026-09-29.md: este informe.

No se han cambiado reglas de entrada, capital, stops, objetivos ni variables de entorno. No hay nuevas variables obligatorias. Esta entrega corrige una carencia de evidencia; NO demuestra ni promete recuperar operaciones. No se ha desplegado desde aquí.

Validación local: 79 pruebas aprobadas (77 de la base y 2 nuevas), compilación correcta. Las pruebas usan contextos sintéticos; no sustituyen una reproducción de mercado ni certifican producción.

## Evidencia necesaria para cerrar la causa y su corrección

Con esta versión, exportar un ciclo de velas nuevas del registro que incluya REJECTION_DIAGNOSTICS permite identificar el valor exacto que está bloqueando cada señal avanzada, en particular BTC y SOL rechazados por RR en el último fragmento. No es necesario esperar otras 30 horas: deben aparecer diagnósticos nuevos al evaluar nuevas velas. Para demostrar oportunidades perdidas históricas se requieren las velas usadas en ese periodo y la configuración efectiva, no solamente el resumen de motivos. No compartir claves, tokens ni direcciones privadas de conexión.
