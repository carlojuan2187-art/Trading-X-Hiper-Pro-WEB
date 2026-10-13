# Seguimiento del retroceso — fase de evaluación real

Base: Trading-X-Hiper-Pro-admin-detallado.zip. Se conserva la entrega de notificaciones y los diagnósticos anteriores.

Se activa por defecto MTF_PENDING_SETUP_ENABLED=true. El detector reconstruye el retroceso desde las velas cerradas disponibles; no requiere una base de datos de setups. La ventana máxima es MTF_SETUP_MAX_AGE_BARS=8 (40 minutos en 5m). Busca el toque más reciente elegible, compara ese toque con sus propias EMA y conserva el extremo estructural. Confirma mediante cierre en dirección de la entrada y nivel de continuación; no basta una mecha. Mantiene el límite de extensión de 0.95 ATR. Un toque nuevo válido puede iniciar un setup nuevo.

Se rechazan los setups cuya estructura se invalida y aquellos que ya habían confirmado en una vela anterior; incluye confirmaciones anteriores demasiado extendidas. Se añade a Telegram estado del setup, edad en velas, índice de reset, extensión y extremo estructural.

Límites: la reconstrucción usa el ATR del contexto actual; no es un registro persistente de todas las decisiones históricas. H1 y M15 siguen siendo requisitos al evaluar la entrada. Este cambio no elimina el principal bloqueo H1 observado ni activa RANGE ni cambia liquidity sweep. Tampoco constituye un backtest o prueba de rentabilidad. Evita repetir ajustes indiscriminados mientras permite observar específicamente el seguimiento del retroceso.

Capital y apalancamiento conservan la configuración existente. El importe de margen configurado no equivale a una pérdida máxima garantizada de 0.10–0.15 USDC; el resultado depende del stop, ejecución y costes.

## Despliegue
Desplegar el ZIP completo. Si Railway ya contiene MTF_PENDING_SETUP_ENABLED=false, cambiarlo a true; una variable explícita prevalece sobre el nuevo valor del archivo. MTF_SETUP_MAX_AGE_BARS tiene valor predeterminado 8. No es necesario cambiar variables de capital, router, ADX o RR. Para volver al detector anterior: MTF_PENDING_SETUP_ENABLED=false.

## Verificación
`python -m unittest discover -s tests -q`: 89 pruebas aprobadas.
`python -m compileall -q app tests`: compilación correcta.
Incluye pruebas de retroceso de seis velas, vencimiento, invalidación, simetría short y cancelación de confirmación previa (normal y extendida).
No se enviaron órdenes, no se desplegó desde aquí y no se ejecutó una comparación con velas históricas reales. Pendiente: comprobar en el despliegue los informes de apertura y cierre para evaluar esta fase.

## Archivos de esta entrega
- .env.example
- app/pending_setup.py
- app/strategies/breakout_reset.py
- app/trade_notifications.py
- tests/test_pending_lifecycle.py (nuevo)
- AJUSTE_RETROCESO.md (nuevo)
