#!/usr/bin/env bash
# Railway WEB/API Build Command. La compilación de mercado es obligatoria.
set -euo pipefail
python -m pip install -r requirements.txt
node --version
npm --version
npm ci --prefix market-terminal --no-audit --no-fund
npm run build --prefix market-terminal
# Impedir desplegar el placeholder provisional por error.
if ! test -s app/web/static/market/market-terminal.js || \
   grep -q 'Terminal pendiente de compilar' app/web/static/market/market-terminal.js; then
  echo 'ERROR: la compilación del terminal falló o se publicó el placeholder' >&2
  exit 1
fi
if ! test -s app/web/static/market/market-terminal.css; then
  echo 'ERROR: market-terminal.css no fue compilado' >&2
  exit 1
fi
echo 'OK: Terminal profesional compilado y verificado.'
