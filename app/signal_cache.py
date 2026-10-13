"""Per-symbol single-flight cache. No balances or execution data are cached."""
from copy import deepcopy
import threading
import time


class SignalCache:
    def __init__(self, ttl=300.0, max_symbols=256):
        self.ttl = ttl
        self.max_symbols = max_symbols
        self._guard = threading.Lock()
        self._locks = [threading.Lock() for _ in range(64)]
        self._values = {}

    def evaluate(self, symbol, key, compute):
        if key is None or self.ttl <= 0:
            return compute()
        with self._locks[hash(symbol) % len(self._locks)]:
            now = time.monotonic()
            with self._guard:
                saved = self._values.get(symbol)
            if saved and saved[0] == key and now - saved[1] < self.ttl:
                result = deepcopy(saved[2])
                result['signal_cache_hit'] = True
                result['regime_changed'] = False
                if isinstance(result.get('regime_context'), dict):
                    result['regime_context']['changed'] = False
                return result
            result = compute()
            reason = str(result.get('reason') or '') if isinstance(result, dict) else 'INVALID'
            transient = any(x in reason for x in ('DATA', 'STALE', 'FETCH', 'EXCEPTION', 'CANDLES', 'INVALID'))
            if isinstance(result, dict) and not transient:
                with self._guard:
                    if len(self._values) >= self.max_symbols and symbol not in self._values:
                        oldest = min(self._values, key=lambda x: self._values[x][1])
                        self._values.pop(oldest, None)
                    self._values[symbol] = (key, now, deepcopy(result))
            return deepcopy(result)
