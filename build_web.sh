#!/usr/bin/env bash
# Assets del mercado versionados: desplegar WEB no depende de npm ni de Node.
set -euo pipefail
python -m pip install -r requirements.txt
python - <<'PYTEST'
from pathlib import Path
root = Path('app/web/static/market')
required = ('market-terminal.js', 'market-terminal.css', 'chart-local.js')
for name in required:
    f = root / name
    if not f.is_file() or f.stat().st_size < 1000:
        raise SystemExit(f'ERROR: falta el terminal real: {f}')
if 'Placeholder seguro' in (root / 'market-terminal.js').read_text():
    raise SystemExit('ERROR: se intentó publicar el placeholder del gráfico')
print('OK: Terminal real disponible, sin compilación npm.')
PYTEST
