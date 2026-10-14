#!/usr/bin/env bash
set -euo pipefail
if [ ! -s app/web/static/market/chart-local.js ] || [ ! -s app/web/static/market/market-terminal.js ] || \
   grep -q 'Placeholder seguro' app/web/static/market/market-terminal.js; then
  echo 'ERROR: faltan archivos reales del terminal. No publicar placeholder.' >&2
  exit 1
fi
echo '🌐 Iniciando Trading X Hiper Pro WEB/API...'
exec uvicorn web_main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-access-log
