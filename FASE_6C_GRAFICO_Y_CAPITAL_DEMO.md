# Fase 6C — gráfico real y capital DEMO

**WEB/API** conserva el mismo servicio, con Build Command `bash build_web.sh` y Start Command `bash start_web.sh`. No necesita otro servicio. Node 22 se instala mediante `mise.toml` para compilar `market-terminal` con Vite. El arranque incorpora una validación obligatoria y un fallback de compilación si Railway omitió el build personalizado. Si npm no está disponible fallará claramente en lugar de mostrar el mensaje "Terminal pendiente de compilar".

**Capital DEMO**: nuevo formulario en DEMO, `POST /api/v1/demo/capital` con sesión autenticada, validación de 10–100000 USDC, máximo dos decimales, y control de concurrencia por versión. Puede configurarse por usuario mientras esté pausado y sin posiciones/historial. Cambiar capital nunca borra operaciones: si existen, debe utilizarse explícitamente "Reiniciar saldo" y volver a configurar. Reiniciar conserva el último capital elegido. El capital DEMO no toca ni crea fondos LIVE. No modifica el margen por operación ni el apalancamiento.

**Worker DEMO**: agrega ping de aplicación a Hyperliquid cada 25 s, incluso cuando no existen símbolos suscritos, evitando cierres `1000 Inactive` por ausencia de actividad. Conserva stop seguro mientras el WebSocket no está disponible. Comandos/variables de MOTOR-TRADING no cambian.

**Prueba**: verificar en Deploy Logs `OK: Terminal profesional compilado y verificado.` y después visualizar un gráfico de velas con selectores de par/intervalo en DEMO y LIVE. Si Railway construyó solo con Python, comprobar la impresión de compilación al inicio. Verificar que `POST /api/v1/demo/capital` no funciona con DEMO activo, posiciones o historial; que un reset no borra el importe inicial configurado; y que un usuario no modifica la cuenta de otro.

**Limitaciones**: npm no se puede instalar desde el registro en este entorno offline, así que los assets Vite no se han compilado aquí y la representación real del gráfico en Railway requiere validación de build. No afirmar que el gráfico está solucionado en producción hasta comprobarlo. El ping no sustituye monitorización ni garantiza ausencia de otros motivos de desconexión.

**Eventos privados WebSocket**: la interrupción de Change Stream por cierre del último navegador (`CursorKilled`) deja de registrarse erróneamente como fallo de replica set; el resto de cortes se informa como reconexión y se mantiene la recuperación segura por resync.
