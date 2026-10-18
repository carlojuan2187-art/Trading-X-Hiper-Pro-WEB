"""Configuración de paper trading. Parámetros por defecto conservadores."""
import os
from app.config import SCAN_INTERVAL


def _float_env(key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.getenv(key, str(default)))
    except ValueError as exc:
        raise RuntimeError(f'{key} inválido') from exc
    import math
    if not math.isfinite(value) or not lo <= value <= hi:
        raise RuntimeError(f'{key} fuera de límites [{lo}, {hi}]')
    return value


DEMO_ENABLED = os.getenv('DEMO_ENABLED', 'false').strip().lower() == 'true'
DEMO_INITIAL_EQUITY_USDC = _float_env('DEMO_INITIAL_EQUITY_USDC', 100.0, 10.0, 100000.0)
DEMO_TAKER_FEE_BPS = _float_env('DEMO_TAKER_FEE_BPS', 5.0, 0.0, 50.0)
DEMO_SLIPPAGE_BPS = _float_env('DEMO_SLIPPAGE_BPS', 2.0, 0.0, 50.0)
DEMO_SCAN_SECONDS = _float_env('DEMO_SCAN_SECONDS', float(SCAN_INTERVAL), 5.0, 3600.0)
# Exact LIVE exit policy: SL and TP are fixed. No paper-only BE/trailing.
# Existing open paper positions retain their frozen per-position settings.
DEMO_BREAK_EVEN_ENABLED = False
DEMO_BREAK_EVEN_TRIGGER_R = _float_env('DEMO_BREAK_EVEN_TRIGGER_R', 1.0, 0.5, 3.0)
DEMO_BREAK_EVEN_BUFFER_BPS = _float_env('DEMO_BREAK_EVEN_BUFFER_BPS', 2.0, 0.0, 30.0)
DEMO_HISTORY_LIMIT = 200

# Límite de evaluaciones profundas por ciclo para no sobrecargar el scanner.
# Las cuentas elegibles rotan equitativamente entre ciclos. No cambia estrategias.
DEMO_MAX_USERS_PER_SCAN = int(_float_env('DEMO_MAX_USERS_PER_SCAN', 5, 1, 25))
