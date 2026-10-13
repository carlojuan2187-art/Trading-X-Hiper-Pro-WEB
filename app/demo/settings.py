"""Configuración de paper trading. Parámetros por defecto conservadores."""
import os


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
DEMO_SCAN_SECONDS = _float_env('DEMO_SCAN_SECONDS', 300.0, 60.0, 3600.0)
DEMO_HISTORY_LIMIT = 200
