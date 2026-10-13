# Auditoría y revisión del motor — Trading X Hiper Pro

Fecha: 5 de octubre de 2026 UTC (noche del 4 de octubre en Cuba).
Base: `Trading-X-Hiper-Pro-router-h1.zip`, ZIP íntegro de 2.692.006 bytes y 95 entradas.
Plataforma: Python, Hyperliquid PERP, MongoDB, Telegram y MiniApp FastAPI.

## Dictamen

Hay defectos concretos en contabilidad, protección local, confirmación de barridos y selección de estrategias. Se entregan correcciones y una revisión acotada de las condiciones H1/router. No existe evidencia suficiente en el adjunto para afirmar que las estrategias tienen expectativa positiva ni que los cambios la produzcan. No es una certificación para lanzar el producto con capital de terceros.

La frecuencia de dos o tres operaciones diarias y las pérdidas recientes son información aportada por el usuario. El ZIP no contiene el historial reciente de fills, velas históricas, variables efectivas de Railway ni registros actuales suficientes para atribuir cuantitativamente ese resultado a cada filtro. `AUDIT_LOG_SUMMARY.json` corresponde a unos 43 minutos del 28 de septiembre; sus rechazos repetidos no equivalen a oportunidades independientes ni describen necesariamente la configuración actual.

## Hallazgos y tratamiento

| Prioridad | Hallazgo comprobado en código | Cambio / consecuencia |
|---|---|---|
| Alta | La lectura anterior seleccionaba el último lote de cierre y excluía las comisiones de apertura. | La ruta del motor concilia el ciclo completo: apertura, todos los fills de cierre, cantidades y comisiones en USDC. No toma un cierre parcial como operación completa. |
| Alta | Se enviaba `startTime` con `userFills`, en lugar del tipo documentado para consultas temporales. | Se usa `userFillsByTime` al proporcionar fecha de inicio. La ruta completa consulta desde la apertura, sin recortar operaciones largas a dos horas. |
| Alta | Si fallaba la conciliación con la posición ya cerrada, se borraba la identidad activa. | Se conservan timestamp y cantidad; se reintenta la conciliación antes de permitir una nueva entrada. El bloqueo se identifica como `RECONCILIATION_PENDING`. |
| Alta | TP/SL locales solo se comprobaban si las banderas históricas `tp_in_exchange` / `sl_in_exchange` eran falsas. | Respaldo local independiente de esas banderas, confirmado contra bid para cerrar LONG y ask para cerrar SHORT. Usa el mecanismo existente de cierre reduce-only y verificación de posición residual. |
| Alta | Barridos toleraban un retest de 0,58 ATR, pero el stop se calculaba a 0,18 ATR del extremo original. | El stop queda fuera del extremo de todo el patrón, incluida la vela de confirmación. Puede ampliar el SL y rechazar un setup por su RR o límite de riesgo: no se recorta dentro de la estructura. |
| Media | Un LONG de barrido podía confirmarse con vela roja; el SHORT, con vela verde. | Se exige cuerpo en la dirección de recuperación. No garantiza reversión; elimina esa incoherencia de confirmación. |
| Media | El router reservaba barridos en tendencia para otra combinación de métricas del clasificador; en rango no los probaba. En volatilidad no probaba MTF si fallaba el barrido. | La etiqueta conocida da prioridad a una estrategia, pero puede evaluarse la alternativa completa tras su rechazo. UNKNOWN/datos inválidos siguen bloqueados y no se activa la estrategia de rango en live. |
| Media | H1 exigía precio/pendiente rápida compatibles con impulso incluso cuando el setup buscaba un retroceso. | Nueva vía de retroceso hasta 0,50 ATR, por encima/debajo de EMA50 según dirección, pendiente EMA50 favorable, ADX/separación y estructura lenta válidos. Después debe pasar M15 y trigger 5M. |
| Media | El mínimo RR de ejecución podía diferir del mínimo usado para construir la señal. | MTF y Sweep respetan también `MIN_ENTRY_RR`; el mínimo específico de estrategia no reduce el de ejecución. |
| Alta | Tras fallar el insert en MongoDB se reintentaba con una firma antigua, descartando metadatos. | Se usa solo la firma que realmente existe. Un fallo de persistencia deja el cierre pendiente; no continúa como si se hubiera guardado. |
| Media | Una caché de mids podía seguir devolviendo precios viejos tras fallos de actualización. | Se devuelve precio no disponible cuando la cotización almacenada excede el límite de frescura, en lugar de presentarla como actual. |
| Media | No se rechazaban sistemáticamente OHLC no finitos o geométricamente inválidos. | Validación de precios, volumen y timestamps antes de calcular indicadores. Se exige historia suficiente para EMA200 en las consultas H1/M15. |
| Alta | Textos de actividad, motivos y mensajes administrativos se interpolaban en HTML sin escape. | Escape en esas superficies; prueba de regresión de actividad con markup malicioso. No equivale a un pentest completo del frontend. |
| Alta | Una respuesta inesperada resting podía tratarse como no-fill y reintentarse. | Respuestas ambiguas/resting inesperadas se clasifican UNKNOWN y no se reenvían como no-fill confirmado. Sigue pendiente un journal duradero de órdenes, descrito abajo. |

## Qué cambia en las estrategias

**MTF:** conserva el setup de continuación, las velas cerradas, M15, confirmación 5M, stop estructural, extensión máxima y score. Se añade una vía H1 de retroceso acotado. No se baja indiscriminadamente el score ni se elimina ADX, EMA50, límites de stop o validación de ejecución. El modo queda identificado como `h1_mode=bounded_pullback`.

**Sweep:** conserva la búsqueda de barrido y recuperación, score mínimo, volumen, cuerpo, recuperación de niveles y RR. Se corrige el extremo del stop y se exige dirección de la vela confirmatoria. Esto puede reducir entradas falsas y también reducir la cantidad de barridos; su efecto económico requiere comparación con datos reales.

**Router:** con `STRATEGY_ROUTER_SETUP_FIRST_ENABLED=true`, mantiene prioridad por régimen. Si MTF falla en tendencia/rango, puede probar Sweep cuando `STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED=true`. Si Sweep falla en volatilidad, prueba MTF. Una señal primaria válida conserva prioridad. No compara scores como si fueran probabilidades de acierto ni mezcla ambas direcciones en una operación.

Las revisiones se etiquetan `mtf_simple_continuation_5m_v4_audit` y `liquidity_sweep_reversal_5m_v2_audit`. La estrategia `range_mean_reversion` sigue en observación salvo que el despliegue la haya habilitado expresamente.

No se ha demostrado que el rango de TP/RR actual sea óptimo. Alargar objetivos, cambiar stops o añadir una estrategia nueva sin una comparación histórica solo sustituiría una hipótesis por otra. La intervención entregada corrige mecanismos identificables y deja reversibles los dos cambios principales de selección.

## Rentabilidad y costes: lo que el porcentaje de aciertos no explica

El criterio correcto es la expectativa neta por operación, junto con drawdown, exposición y estabilidad fuera de muestra. Tener más operaciones no garantiza mejorar ninguno de esos valores.

Ejemplo puramente aritmético, no backtest ni tarifa afirmada para tu cuenta: LONG con nominal inicial 100 USDC, SL 0,50%, TP 0,60% y comisión hipotética de 0,05% por lado. Sin slippage ni funding, un TP deja aproximadamente 0,4997 USDC y un SL pierde 0,59975 USDC. El equilibrio requiere aproximadamente 54,55% de aciertos. Si además se cobra 15% sobre cada trade positivo, supera aproximadamente 58,54%. Con slippage o funding adverso empeora.

El proyecto acumula `OWNER_FEE_PERCENT` sobre cada cierre positivo; no compensa pérdidas anteriores con un High-Water Mark. No cambié esa política comercial. El PnL del cierre ahora incluye ambas comisiones de ejecución, pero **no funding ni la comisión de plataforma**. El mensaje lo denomina PnL neto trading (sin funding); el informe histórico debe considerar también la comisión de plataforma para evaluar el beneficio del usuario.

`tools/replay_ohlcv.py` permite indicar costes por lado y ahora también `--platform-fee-rate`. Añade expectativa R, ganancias/pérdidas medias y estrategia/régimen por trade. No reproduce funding, liquidez real, impacto del tamaño, scanner, todas las latencias, cartera multiusuario ni el gobernador de riesgo. Los resultados no son rentabilidad certificada de producción.

## Cobertura de revisión

Se revisaron los puntos de entrada del bot/API, configuración, scanner y cachés; MTF/Sweep/rango, router y máquina de estados; ruta de apertura, guards de drift/RR, instalación de protección, supervisor, emergencia y conciliación; lectura de fills, registro y cargos; autenticación, cifrado, renderizado de actividad y configuración de arranque.

Es una revisión estática y de regresión centrada en el flujo de trading y riesgos relacionados. No se ejecutó cada ruta del backend ni una auditoría externa de seguridad, carga o infraestructura. No se conectó MongoDB, Railway, una cuenta de Hyperliquid ni Telegram. No se enviaron órdenes, pagos ni mensajes.

## Validación realizada

- Integridad del ZIP original: todas sus entradas verificadas por CRC.
- Base recibida: 93 pruebas Python aprobadas.
- Revisión: **120 pruebas Python aprobadas**, incluyendo 27 nuevas.
- Prueba Node de escape de contenido de actividad: aprobada.
- `compileall` sobre app/tools/tests y los dos entrypoints: correcto.
- `node --check app/web/static/app.js`: correcto.
- Replay CLI en dos configuraciones, con 3.100 velas sintéticas de SOL/BTC: ejecución correcta y cero trades cerrados. Solo comprueba el recorrido del programa; no aporta evidencia de rentabilidad ni una comparación estadística entre perfiles.
- Runtime de comprobación: Python 3.12.14 y Node 24.19.0. El proyecto declara Python 3.11; falta validar el despliegue con ese runtime y sus dependencias reales.
- Hay avisos heredados de `datetime.utcnow()` en Python 3.12. No causaron fallos.
- No se ejecutó un linter/type checker: no hay configuración correspondiente ni ejecutable ruff disponible. Compilación y pruebas no sustituyen esa comprobación.

## Riesgos y pendientes antes del lanzamiento

1. **Edge no demostrado.** Faltan velas de un período representativo, fills e historial reciente. Comparar original/revisado con las mismas fechas y costes, separar ajuste y evaluación posterior, y observar resultados prospectivos. No se promete un número diario de operaciones.
2. **Órdenes con confirmación incierta.** Clasificar UNKNOWN evita reintentos indebidos, pero aún falta una intención persistente previa al envío, cloid/idempotencia y conciliación inmediata de timeouts. Una orden aceptada por el exchange cuya respuesta se pierde puede quedar sin el contexto de apertura hasta la recuperación posterior. Es un riesgo material para lanzamiento.
3. **Registro y cargos no transaccionales.** La colección de operaciones y la acumulación de comisiones no forman una transacción con clave única de ciclo. Un fallo después de escribir y antes de finalizar puede duplicar efectos al reintentar. El cambio elimina el segundo insert inmediato por firma antigua; no resuelve exactamente-una-vez entre procesos o caídas.
4. **Funding y fee de plataforma.** No se agregan al PnL de fills. No usar el panel como prueba de beneficio económico total hasta conciliar esos flujos.
5. **Límites del nuevo conciliador.** Solo un ciclo aislado por símbolo, sin operar manualmente la misma posición. Si faltan fills, hay cantidades contradictorias, dirección mixta, moneda de comisión diferente de USDC o una página de 2.000 fills, se bloquea la conciliación en lugar de adivinar. En esos casos se necesita reconciliación asistida/paginación antes de reactivar. Las posiciones heredadas cuyo inicio se reconstruyó después de abrir pueden no tener suficiente contexto.
6. **Protección local no equivale a ejecución exacta.** Sigue dependiendo de sondeo, red, libro y disponibilidad del proceso. El exchange usa mark price para sus triggers; el respaldo local usa bid/ask ejecutable. Puede actuar en un momento distinto. Mantener los triggers nativos; no se garantiza fill al precio SL/TP.
7. **Infraestructura:** una sola réplica del motor; locks y parte de las cachés son locales. Escalarlo requiere coordinación distribuida. No se hizo prueba de reinicio con posiciones reales.
8. **Configuración real desconocida.** Variables de Railway prevalecen sobre los defaults. `POSITION_PERCENT=1` sin `MAX_TRADE_MARGIN_USDC` positivo usa todo el saldo elegible como margen. No cambié capital ni apalancamiento automáticamente.
9. **Seguridad:** quedan pendientes pruebas de navegador de todas las interpolaciones HTML, revisión integral de secretos/permisos y pruebas de abuso de endpoints. Conservar el secreto de cifrado; rotarlo sin migración impide recuperar claves existentes.

## Próxima validación concreta

Ejecutar `python tools/audit_runtime_config.py` en el servicio actual: imprime solo una lista permitida de parámetros no secretos. Recoger registros nuevos de scanner/rechazos, aperturas, cierres, fills y costes de varios días, sin credenciales. Comparar las dos configuraciones en replay con datos reales y luego en un entorno controlado. No desplegar archivo por archivo mientras el motor mantiene posiciones abiertas: las nuevas rutas se apoyan en módulos nuevos.

## Fuentes técnicas contrastadas

- [Hyperliquid: TP/SL y referencia mark price](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/take-profit-and-stop-loss-orders-tp-sl).
- [Hyperliquid: Info endpoint, userFills / userFillsByTime y campos fee](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).
- [Hyperliquid: Fees](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees). El ejemplo económico anterior utiliza un supuesto explícito; la tarifa efectiva de la cuenta no se obtuvo.
