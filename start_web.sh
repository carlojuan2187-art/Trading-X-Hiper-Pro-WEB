#!/usr/bin/env bash
set -euo pipefail
# Railway puede ejecutar un build automático de Python sin build_web.sh.
# Garantizar en arranque que nunca se sirva de nuevo la pantalla placeholder.
if [ ! -s app/web/static/market/market-terminal.js ] || \
   grep -q 'Terminal pendiente de compilar' app/web/static/market/market-terminal.js; then
  echo '⚙️ Terminal no compilado: preparando assets antes de iniciar WEB/API...'
  command -v npm >/dev/null || { echo 'ERROR: Node/npm no instalado. Usa bash build_web.sh en Build Command.' >&2; exit 1; }
  npm ci --prefix market-terminal --no-audit --no-fund
  npm run build --prefix market-terminal
fi
if [ ! -s app/web/static/market/market-terminal.js ] || \
   grep -q 'Terminal pendiente de compilar' app/web/static/market/market-terminal.js; then
  echo 'ERROR: no se pudo publicar el terminal real; abortando WEB/API' >&2
  exit 1
fi
echo '🌐 Iniciando Trading X Hiper Pro MiniApp API...'
exec uvicorn web_main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log
