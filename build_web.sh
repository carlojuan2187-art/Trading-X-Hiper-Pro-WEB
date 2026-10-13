#!/usr/bin/env bash
# Para Railway: Build Command = bash build_web.sh
# No descarga dependencias en el runtime ni modifica worker LIVE.
set -euo pipefail
python -m pip install -r requirements.txt
npm ci --prefix market-terminal --no-audit --no-fund
npm run build --prefix market-terminal
printf 'Terminal compilado en app/web/static/market/\n'
