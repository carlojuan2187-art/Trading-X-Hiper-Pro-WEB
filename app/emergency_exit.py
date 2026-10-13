"""Fail-closed emergency reduction, independent of the exchange adapter."""
import math


def close_verified(*, read_size, close_order, cancel_orders, protect, attempts=3):
    """Unknown state is not flat. Re-read partial fills before every retry."""
    last_error = ''
    for _ in range(attempts):
        try:
            size = read_size()
            if size is None or not math.isfinite(size):
                raise ValueError('POSITION_STATE_UNKNOWN')
            if size == 0:
                cancel_orders()
                return {'closed': True, 'remaining': 0.0}
            close_order(size)
            remaining = read_size()
            if remaining is None or not math.isfinite(remaining):
                raise ValueError('POST_CLOSE_STATE_UNKNOWN')
            if remaining == 0:
                cancel_orders()
                return {'closed': True, 'remaining': 0.0}
            protect(remaining)
            last_error = 'RESIDUAL_POSITION'
        except Exception as exc:
            last_error = str(exc)
    return {'closed': False, 'reason': last_error}
