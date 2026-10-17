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


def find_m5_continuation(*, direction, o, h, l, c, ema20, atr,
                         max_extension=0.95):
    """DEMO alternative to a perfect EMA20 reset: one CLOSED 5m pause/reclaim.

    Only accepts a new directional confirmation on the most recent closed bar.
    The regular pending-reset lifecycle (including consumed setups) stays intact.
    This helper does not decide H1/M15 bias, position sizing, RR, or execution.
    """
    import math

    arrays = (o, h, l, c, ema20)
    n = len(c)
    if (direction not in {'long', 'short'} or n < 5 or atr <= 0
            or not math.isfinite(atr) or any(len(a) != n for a in arrays)):
        return False, 'M5_ALT_INVALID_DATA', {}
    i = n - 1
    recent = [float(a[k]) for a in arrays for k in (i - 2, i - 1, i)]
    if not all(math.isfinite(x) for x in recent):
        return False, 'M5_ALT_INVALID_DATA', {}
    if any(h[k] < l[k] or c[k] <= 0 or ema20[k] <= 0
           for k in (i - 2, i - 1, i)):
        return False, 'M5_ALT_INVALID_DATA', {}

    side = 1 if direction == 'long' else -1
    extension = abs(c[i] - ema20[i]) / atr
    diag = {'m5_entry_path': 'none', 'extension_atr': extension,
            'confirm_index': i, 'setup_state': 'M5_ALT_PENDING'}
    # No chasing an extended move or accepting a candle against the direction.
    if extension > max_extension:
        return False, 'M5_ALT_TOO_EXTENDED', diag
    if side * (c[i] - ema20[i]) <= 0:
        return False, 'M5_ALT_WRONG_EMA20_SIDE', diag
    if side * (ema20[i] - ema20[i - 2]) < -0.03 * atr:
        return False, 'M5_ALT_EMA20_OPPOSING', diag
    if side * (c[i] - o[i]) < 0.14 * atr:
        return False, 'M5_ALT_NO_DIRECTIONAL_BODY', diag
    candle_range = max(h[i] - l[i], 1e-12)
    close_location = (c[i] - l[i]) / candle_range
    if (direction == 'long' and close_location < 0.62) or (
            direction == 'short' and close_location > 0.38):
        return False, 'M5_ALT_WEAK_CLOSE', diag

    prev = i - 1
    prev_range = max(h[prev] - l[prev], 1e-12)
    counter_or_pause = (
        side * (c[prev] - o[prev]) <= 0
        or abs(c[prev] - o[prev]) <= 0.42 * prev_range
    )
    ema20_test = (l[prev] <= ema20[prev] + 0.50 * atr if direction == 'long'
                  else h[prev] >= ema20[prev] - 0.50 * atr)
    reclaimed = (
        side * (c[prev] - ema20[prev]) <= 0.08 * atr
        and side * (c[i] - ema20[i]) >= 0.08 * atr
    )
    breakout = (c[i] >= h[prev] - 0.12 * atr if direction == 'long'
                else c[i] <= l[prev] + 0.12 * atr)
    diag.update({'pause': bool(counter_or_pause), 'ema20_test': bool(ema20_test),
                 'reclaimed': bool(reclaimed), 'breakout': bool(breakout),
                 'close_location': round(close_location, 4)})
    if not (counter_or_pause or ema20_test or reclaimed):
        return False, 'M5_ALT_NO_FRESH_PAUSE', diag
    if not breakout:
        return False, 'M5_ALT_NO_PRICE_CONFIRM', diag

    path = 'ema20_reclaim' if reclaimed else 'pause_continuation'
    diag.update({'m5_entry_path': path, 'setup_state': 'M5_ALT_CONFIRMED',
                 'bars_since_reset': 1, 'reset_index': prev,
                 'structural_extreme': min(l[prev], l[i]) if direction == 'long'
                                       else max(h[prev], h[i])})
    return True, 'OK_M5_ALT', diag
