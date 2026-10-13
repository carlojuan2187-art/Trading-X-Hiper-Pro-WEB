# Informe de apertura para administrador

La apertura ahora incluye usuario e ID, ID de correlación (usuario/par/hora), hora UTC, dirección, precio ejecutado, cantidad y nocional; estrategia/modelo/versiones, régimen y decisión del router, score y fuerza; sesgos H1/M15, indicadores disponibles y datos de sweep; referencia y hora de vela; capital aprobado, apalancamiento, cantidades previstas y ejecutadas; precios iniciales SL/TP, RR y estado reportado de instalación de ambas protecciones; estimación de PnL bruto en SL/TP y desviación adversa frente a la referencia cuando hay datos.

Los campos ausentes dicen «no disponible». No se inventan indicadores que la estrategia no devuelve. Los porcentajes con sufijo _pct se muestran como fracciones (0.01 = 1%). Los precios de SL/TP proceden de los niveles enviados al exchange. El PnL estimado excluye comisiones, funding y deslizamiento. sl_ok/tp_ok son respuestas de instalación, no garantía de ejecución.

Se envía solamente al ADMIN_TELEGRAM_ID existente, como texto plano, dividido automáticamente en partes numeradas si es largo. No se necesitan nuevas variables. No se envían claves ni respuestas completas del exchange. Se conservan las notificaciones habituales del usuario y las reglas de trading.

Este informe cubre aperturas nuevas. No reconstruye operaciones anteriores ni determina anticipadamente el motivo de cierre. Reenviar todas las partes permite revisar la información de apertura; para reconstruir el recorrido del precio aún pueden ser necesarias velas/historial del exchange. El transporte mantiene reintento por rate limit; no incorpora una cola durable para caídas del proceso o errores permanentes de Telegram.

## Archivos respecto a Trading-X-Hiper-Pro-auditado.zip
- app/trading_engine.py: adjunta evidencia seleccionada a OPEN.
- app/trading_loop.py: compone el informe y envía partes al administrador.
- app/trade_notifications.py: nuevo, selección segura y formato.
- tests/test_trade_notifications.py: nuevo, 4 pruebas.
- NOTIFICACIONES_ADMIN.md: nuevo, instrucciones.

## Instalación y comprobación
Sustituir esos archivos o desplegar el ZIP completo. No cambiar variables de capital ni estrategia. Ejecutar desde la raíz: `python -m unittest discover -s tests -q` y `python -m compileall -q app tests`.

Validación local: 83 pruebas aprobadas, compilación correcta. Envío simulado; no se enviaron mensajes reales ni se abrió ninguna operación. Falta comprobar entrega con el bot desplegado. Esta modificación de observabilidad no corrige por sí sola la rentabilidad.
