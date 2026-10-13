"""Fair market coverage independent of order fills."""
import threading


class Coverage:
    def __init__(self):
        self._lock = threading.Lock()
        self._last = {}
        self._sequence = 0

    def order(self, rows):
        with self._lock:
            # Stable sorting preserves market rank among equally unseen symbols.
            return sorted(rows, key=lambda row: self._last.get(row['symbol'], 0))

    def mark(self, symbol):
        with self._lock:
            self._sequence += 1
            self._last[symbol] = self._sequence
            if len(self._last) > 2048:
                cutoff = self._sequence - 1024
                self._last = {k: v for k, v in self._last.items() if v >= cutoff}
