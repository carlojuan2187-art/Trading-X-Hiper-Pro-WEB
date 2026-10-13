"""Reconstruct pending pullbacks from closed candles; deterministic after restart."""


def find_pending_reset(*, direction, o, h, l, c, ema20, ema50, atr,
                       max_age=8, touch_atr=0.38, break_atr=0.48,
                       invalidation_atr=0.10, confirm_atr=0.10, max_extension=0.95):
    import math
    arrays = (o, h, l, c, ema20, ema50)
    if any(len(values) != len(c) for values in arrays) or not math.isfinite(atr):
        return False, "NO_PENDING_SETUP_DATA", {}
    if direction not in {'long', 'short'} or atr <= 0 or len(c) < max_age + 2:
        return False, 'NO_PENDING_SETUP_DATA', {}
    i = len(c) - 1
    long = direction == 'long'
    extension = abs(c[i] - ema20[i]) / atr
    # Most recent eligible touch first. Each touch is anchored to its OWN EMA.
    for j in range(i - 1, max(-1, i - max_age - 1), -1):
        touched = (l[j] <= ema20[j] + touch_atr * atr and h[j] >= ema20[j] - touch_atr * atr
                   if long else h[j] >= ema20[j] - touch_atr * atr and l[j] <= ema20[j] + touch_atr * atr)
        intact = l[j] >= ema50[j] - break_atr * atr if long else h[j] <= ema50[j] + break_atr * atr
        if not touched or not intact:
            continue
        extreme = l[j] if long else h[j]
        invalid = (min(l[j + 1:i + 1]) < extreme - invalidation_atr * atr if long
                   else max(h[j + 1:i + 1]) > extreme + invalidation_atr * atr)
        if invalid:
            continue
        diag = {'bars_since_reset': i - j, 'reset_index': j,
                'structural_extreme': extreme, 'extension_atr': extension,
                'setup_state': 'PENDING', 'setup_max_age_bars': max_age}
        # A previously confirmed/overextended setup must not become a new
        # entry later merely because the rolling previous-candle level changed.
        for k in range(j + 1, i):
            prior_confirm = (c[k] > ema20[k] and c[k] > o[k] and c[k] >= h[k-1] - confirm_atr * atr if long
                             else c[k] < ema20[k] and c[k] < o[k] and c[k] <= l[k-1] + confirm_atr * atr)
            if prior_confirm:
                diag['setup_state'] = 'CONSUMED_OR_EXPIRED'
                return False, 'PENDING_SETUP_ALREADY_CONFIRMED', diag
        diag.update(confirm_open=o[i], confirm_close=c[i], confirm_ema20=ema20[i],
                    confirm_level=h[i-1] - confirm_atr * atr if long else l[i-1] + confirm_atr * atr,
                    atr=atr, max_extension_atr=max_extension)
        confirmed = (c[i] > ema20[i] and c[i] > o[i] and c[i] >= h[i - 1] - confirm_atr * atr if long
                     else c[i] < ema20[i] and c[i] < o[i] and c[i] <= l[i - 1] + confirm_atr * atr)
        if not confirmed:
            return False, 'PENDING_CONTINUATION_CONFIRM', diag
        if extension > max_extension:
            diag['setup_state'] = 'EXPIRED_EXTENSION'
            return False, 'TOO_EXTENDED_AFTER_CONFIRM', diag
        diag['setup_state'] = 'CONFIRMED'
        return True, 'OK', diag
    return False, 'NO_VALID_PENDING_RESET', {'setup_state': 'EXPIRED_OR_INVALIDATED'}
