"""Backup exits use executable L2 quotes, never an assumed exchange order state."""
import math


def exit_guard_reason(direction, bid, ask, stop, target):
    try:
        bid, ask, stop, target = map(float, (bid, ask, stop, target))
    except (TypeError, ValueError):
        return ''
    if not all(math.isfinite(x) and x > 0 for x in (bid, ask, stop, target)) or bid > ask:
        return ''
    if direction == 'long' and stop < target:
        return 'STOP_SOFTWARE_GUARD' if bid <= stop else 'TP_SOFTWARE_GUARD' if bid >= target else ''
    if direction == 'short' and target < stop:
        return 'STOP_SOFTWARE_GUARD' if ask >= stop else 'TP_SOFTWARE_GUARD' if ask <= target else ''
    return ''
