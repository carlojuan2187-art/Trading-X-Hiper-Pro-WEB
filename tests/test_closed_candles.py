import importlib
import sys
import types
import unittest


# market_context solo necesita estas dos funciones al importar. Las aislamos para
# probar el filtro temporal sin cargar DB/config/credenciales del proyecto.
stub = types.ModuleType("app.hyperliquid_client")
stub.make_request = lambda *args, **kwargs: []
stub.norm_coin = lambda value: str(value or "").upper()
sys.modules.setdefault("app.hyperliquid_client", stub)

market_context = importlib.import_module("app.market_context")


class ClosedCandleTests(unittest.TestCase):
    def test_open_candle_is_removed(self):
        step = market_context.interval_ms("5m")
        now_ms = 10_000_000
        closed_t = now_ms - (2 * step)
        open_t = now_ms - 100_000
        rows = [
            {"t": closed_t, "T": closed_t + step, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
            {"t": open_t, "T": open_t + step, "o": 1.5, "h": 2, "l": 1.2, "c": 1.7, "v": 8},
        ]
        parsed = [market_context.parse_candle(x, interval="5m") for x in rows]
        filtered = market_context._closed_candles_only(parsed, "5m", now_ms=now_ms)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["t"], closed_t)

    def test_close_timestamp_is_inferred_when_missing(self):
        step = market_context.interval_ms("15m")
        row = {"t": 1_000_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}
        parsed = market_context.parse_candle(row, interval="15m")
        self.assertEqual(parsed["T"], 1_000_000 + step)


if __name__ == "__main__":
    unittest.main()
