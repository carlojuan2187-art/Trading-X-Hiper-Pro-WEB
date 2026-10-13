# Revisión operativa y preparación de producción — 28/09/2026

## Dictamen

La ventana enviada muestra un motor activo, sin señales aceptadas, no una pausa
permanente causada por la última pérdida. No permite explicar 16–18 horas completas:
solo cubre 09:45:46–10:28:59 de Cuba (13:45:46–14:28:59 UTC), unos 43 minutos.
Los diagnósticos H1 y Liquidity Sweep confirman que los cambios recientes están
presentes en el proceso, pero no identifican el commit ni todas sus variables.

Esta entrega es una candidata con correcciones verificadas offline, NO una
certificación de producción, rentabilidad o frecuencia mínima. Hay pendientes
materiales de integración y recuperación de órdenes enumerados abajo.

## Evidencia del registro

144 bloques SCAN_DIAGNOSTICS; 420 rechazos contando repeticiones entre usuarios y
ciclos. No sumar estos conteos como oportunidades distintas o trades recuperables.
Los filtros se detienen en el primer fallo: no sabemos si pasarían los posteriores.

| Motivo | Rechazos | % |
|---|---:|---:|
| NO_H1_BIAS | 305 | 72.62 |
| ROUTER_REGIME_NO_TRADE | 60 | 14.29 |
| NO_5M_CONTINUATION_CONFIRM | 19 | 4.52 |
| NO_LIQUIDITY_SWEEP_SETUP | 12 | 2.86 |
| NO_M15_BIAS | 11 | 2.62 |
| ATR_TOO_LOW | 6 | 1.43 |
| STRUCTURAL_SL_TOO_WIDE | 3 | 0.71 |
| TOO_EXTENDED_AFTER_CONFIRM | 3 | 0.71 |
| RESET_TOO_DEEP | 1 | 0.24 |

- Sin mensajes «Bloqueo responsable», ERROR/CRITICAL/WARN ni agotamiento del
  presupuesto de evaluación. Esto no demuestra ausencia de fallos fuera de la ventana.
- Saldos withdrawable observados: 5.557937 y 8.991304 USDC. El motor llegó a evaluar
  señales después de comprobar elegibilidad, capital y gobernadores.
- El cooldown individual tras una pérdida tiene default 1.200 segundos (20 min).
  No se conoce el valor desplegado ni el estado de las horas anteriores. El código
  compara una fecha de vencimiento; no la extiende simplemente al consultar.
- 64 diagnósticos H1 y 6 de 15M. En 11 de los H1, el único impedimento para SHORT
  fue EMA50 todavía por encima de EMA200: SOL, LTC, ADA, TAO, DOT y AAVE, repetidos.
- Muchos otros H1 fallan por precio rebotando sobre EMA20 con medias descendentes
  o alineación contradictoria. Ejemplo BTC: precio 83556 sobre EMA20 83542.59,
  EMA50 83924.64 y EMA200 83708.48. Quitar solo EMA200 no aprobaría ese caso.
- Liquidity Sweep: 103 diagnósticos del router, 9 evaluaciones efectivas y 94 sin
  evaluar. El probe alternativo figura deshabilitado. Las 9 evaluaciones rechazaron
  el setup; 108 velas candidatas examinadas entre ambos sentidos fallaron profundidad.
  Los submotivos de trigger pueden coincidir en una vela y no se suman como trades.
- Los 60 bloqueos RANGE son coherentes con rango en observación; no son por sí
  solos un bug ni evidencia suficiente para habilitar rango en real.

Resultados reproducibles en AUDIT_LOG_SUMMARY.json y con:

```bash
python tools/analyze_runtime_log.py ruta/al/registro.log.txt
```

## Revisión por componente

| Componente | Hallazgo y alcance |
|---|---|
| Elegibilidad/capital | Los usuarios observados pasan sus gates en esta ventana. Se conserva la fórmula por capital configurado, no sizing por pérdida. |
| Pérdidas y pausas | Se inspeccionaron creación/vencimiento y persistencia. No hay evidencia del governor como causa actual; hace falta registro anterior al cierre. |
| Scanner/caché | Detectado y corregido arrastre de candidatos antiguos ante un vacío válido. Se conserva fallback temporal solo ante respuesta de mercados no disponible. |
| H1/15M/5M | El mayor bloqueo es real dentro de las reglas actuales. Se añade una opción de comparación H1 aislada, conservadora por defecto. |
| Router/rango/sweep | Probe deshabilitado en logs; rango fuera de live. No se activan estrategias sin resultados históricos. |
| HTTP | Corregido timeout mutable compartido entre consultas concurrentes. |
| Loop/workers | Corregida pérdida del resultado después de timeout de la espera asíncrona. El lock del motor ya evitaba ejecución simultánea, pero no recuperaba el resultado del waiter cancelado. |
| Ejecución/SL/TP/emergencias | Revisados guards, respuesta IOC, verificación de posición y reanudación de emergencia. Persiste un riesgo de respuesta ambigua descrito abajo. |
| API/autenticación | Revisadas dependencias de sesión/admin, verificación HMAC y respuestas de error. Se endurece validación de forma/fecha y se evita devolver texto de RuntimeError al cliente. |
| Persistencia/pagos | Inspección de índices, locks e idempotencia declarada. No se ejecutaron transacciones o pruebas de caída contra MongoDB ni pagos on-chain. |
| Frontend | Revisión estática parcial: existen interpolaciones de textos en innerHTML. Se requiere revisión completa de orígenes/escape y prueba de navegador; no se certifica XSS-safe. |
| Infraestructura | Runtime declarado Python 3.11, una réplica del bot. No hay coordinación distribuida que autorice escalar el motor horizontalmente. |

## Correcciones implementadas

1. Scanner: separa «respuesta no disponible» de «consulta válida sin candidatos».
   El segundo caso vacía la caché y también se comparte unos segundos entre usuarios.
   Evita reutilizar símbolos que dejaron de pasar los filtros. No aumenta señales
   por sí solo y no relaja los filtros de libro/volumen.
2. HTTP: cada post recibe su propio timeout; no modifica el cliente compartido.
3. Loop: mantiene el Future pendiente por usuario y lo protege de la cancelación
   de wait_for. El siguiente ciclo recupera ese mismo resultado, incluso OPEN,
   sin enviar otro worker. El aviso informa que el trabajo sigue pendiente.
   Una excepción TimeoutError interna del worker libera su registro para reintentar.
4. Auth: rechaza init_data con auth_date futuro más allá de 30 s; valida tipo de
   user/id; un claim admin solo vale para el ID configurado actualmente; payloads
   de sesión mal formados generan 401. No cambia claves de cifrado ni sesiones válidas.
5. API: RuntimeError devuelve mensaje genérico, no texto potencialmente interno.
6. Herramienta offline para resumir los registros; no usa exchange ni credenciales.

## Equilibrio entre calidad y ejecución

Se preparó MTF_H1_REQUIRE_EMA200=true, con el comportamiento anterior por defecto.
Ponerla en false retira SOLO la comparación EMA50/EMA200 de 1H. Mantiene precio
respecto a EMA20, EMA20/50, pendientes, ADX, separación mínima, confirmación 15M,
trigger 5M, RR, SL, costes de ejecución y capital configurado.

No se activó false por defecto. Los 11 diagnósticos prueban que EMA200 bloqueó
esa etapa, no que esas operaciones hubieran sido ganadoras o siquiera ejecutables.
La comparación se puede hacer con la herramienta histórica ya incluida:

```bash
python tools/replay_ohlcv.py --csv-dir data --symbol SOL --strategy router --fee-bps 5 --slippage-bps 2 --output results/strict.json
MTF_H1_REQUIRE_EMA200=false python tools/replay_ohlcv.py --csv-dir data --symbol SOL --strategy router --fee-bps 5 --slippage-bps 2 --output results/h1-without-ema200.json
```

Son comandos para bash. Costes ilustrativos, sustituir por los reales. Hacen falta
CSV 5m de BTC y del símbolo, período largo y evaluación fuera de muestra. No se
ha ejecutado esa comparación con datos reales en esta revisión. No activar además
el probe/rango al comparar H1: impediría atribuir resultados al cambio.

El probe STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED ya existía. Su activación permite
considerar Sweep en ciertas condiciones de tendencia/volatilidad, no acepta
cualquier barrido ni elimina su score mínimo. Permanece false en el ejemplo.

## Bloqueos reales para declarar producción verificada

1. **Resultado incierto de una orden.** El adaptador puede clasificar una respuesta
   HTTP vacía/errónea o sin statuses como error; el motor retorna sin armar en esa
   rama una intención durable de recuperación. Si la orden sí llegó al exchange,
   la recuperación depende del ciclo siguiente/watchdog. Falta cerrar el protocolo
   durable por client-order-id antes del envío, reconciliar resultado incierto y
   probar caída/red perdida entre aceptación y respuesta. Una IOC reportada como
   resting también exige conciliación explícita. No se resolvió improvisando un
   reenvío: debe probarse contra el contrato del exchange.
2. **Integración real.** No se instaló el conjunto de dependencias: la descarga de
   python-telegram-bot falló por timeout del proxy hacia files.pythonhosted.org.
   No arrancaron bot/API con MongoDB, Telegram o Hyperliquid. La suite aislada no
   sustituye esas comprobaciones ni valida Python 3.11 (ejecución local 3.12).
3. **Caídas/persistencia.** Falta ensayar reinicio con posición abierta, fill parcial,
   pérdida de DB, SL/TP rechazado y emergencia pendiente en un entorno controlado.
4. **Seguridad del conjunto.** No hubo revisión dinámica de frontend, rate limiting,
   dependencias vulnerables ni permisos de infraestructura. El escape de DOM y la
   política de errores requieren validación end-to-end. No se cambiaron pagos.
5. **Evidencia de estrategia.** Falta el registro completo desde la última pérdida,
   histórico de operaciones con comisiones y series históricas para medir frecuencia,
   PnL neto y drawdown fuera de muestra. Frecuencia sola no acredita calidad.

## Verificación y entrega

- 67 pruebas offline aprobadas (incluyen regresiones de scanner, HTTP, workers,
  autenticación y opción de comparación H1).
- compileall de app, tools, main.py y web_main.py sin errores.
- Las pruebas financieras usan funciones aisladas y simulaciones para evitar efectos
  de importación externos. No es un arranque completo de producción.
- Conservados el margen configurado, apalancamiento, SL/TP y gates de pérdida.
- No se modificó ningún despliegue ni se enviaron órdenes reales.
- Lista exacta de archivos en CHANGED_FILES.md.

Mantener una réplica, conservar secretos y variables, comprobar operación abierta
antes de reiniciar. No reemplazar todas las variables por .env.example.
Solicitar exportación completa de logs desde antes del cierre perdedor hasta ahora,
CSV de operaciones (fecha/símbolo/estrategia/PnL/comisiones) y valores de variables
operativas sin tokens, private keys, contraseñas ni URLs con credenciales.

## Referencias técnicas consultadas

- HTTPX, configuración de timeout por request:
  https://www.python-httpx.org/advanced/timeouts/
- Hyperliquid, exchange endpoint (cloid y expiresAfter):
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint
- Hyperliquid, consulta de estado por oid o cloid:
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
