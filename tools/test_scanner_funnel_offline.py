"""Pruebas offline de contadores del scanner; sin mercado real."""
from __future__ import annotations
import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_scanner():
    config = types.ModuleType("app.config")
    values = {
        "PRODUCTION_MODE": True, "SCANNER_DEPTH": 80, "VERBOSE_LOGS": False,
        "SCANNER_MIN_24H_NOTIONAL": 2_000_000.0,
        "SCANNER_MIN_OPEN_INTEREST": 250_000.0,
        "SCANNER_MAX_SPREAD_BPS": 25.0, "SCANNER_MIN_TOP_BOOK_NOTIONAL": 2_000.0,
        "SCANNER_SHORTLIST_DEPTH_FOR_L2": 25, "SCANNER_L2_CACHE_TTL": 1.5,
    }
    for key, value in values.items():
        setattr(config, key, value)
    exchange = types.ModuleType("app.hyperliquid_client")
    exchange.make_request = lambda *_a, **_kw: None
    spec = importlib.util.spec_from_file_location(
        "audit_scanner_funnel", ROOT / "app/market_scanner.py"
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"app.config": config, "app.hyperliquid_client": exchange}):
        spec.loader.exec_module(mod)
    return mod


def market(volume=15_000_000, oi=3_000_000, px=10):
    return {"markPx": str(px), "prevDayPx": str(px * .99),
            "dayNtlVlm": str(volume), "openInterest": str(oi)}


def quality(spread=10, book=5000):
    return {"spread_bps": spread, "top_book_notional": book,
            "best_bid": 9.99, "best_ask": 10.01}


class ScannerFunnelTest(unittest.TestCase):
    def setUp(self):
        self.scanner = load_scanner()

    def test_observation_preserves_selection_and_reports_all_stages(self):
        scanner = self.scanner
        markets = {
            "BTC-PERP": market(),
            "SPREAD-PERP": market(),
            "THIN-PERP": market(),
            "NOL2-PERP": market(),
            "LOWVOL-PERP": market(volume=100_000),
            "LOWOI-PERP": market(oi=10_000),
            "DOGE-PERP": market(),
        }
        book = {
            "BTC-PERP": quality(),
            "SPREAD-PERP": quality(spread=40),
            "THIN-PERP": quality(book=100),
            "NOL2-PERP": None,
        }
        with patch.object(scanner, "_fetch_markets", return_value=markets), \
             patch.object(scanner, "_fetch_l2_quality", side_effect=lambda symbol: book[symbol]):
            result = scanner._get_live_results(set())
        self.assertEqual([row["symbol"] for row in result], ["BTC-PERP"])
        counts = scanner.get_scanner_funnel_snapshot()
        expected = {
            "markets": 7, "symbol_policy": 1,
            "min_24h_notional": 1, "min_open_interest": 1,
            "prefiltered": 4, "l2_checked": 4,
            "l2_unavailable": 1, "spread_rejected": 1,
            "top_book_rejected": 1, "shortlisted": 1,
        }
        for key, value in expected.items():
            self.assertEqual(counts.get(key), value, key)

    def test_no_market_does_not_fail_open(self):
        with patch.object(self.scanner, "_fetch_markets", return_value=None):
            self.assertIsNone(self.scanner._get_live_results(set()))
        self.assertEqual(
            self.scanner.get_scanner_funnel_snapshot().get("market_source_unavailable"), 1
        )

    def test_budget_records_unchecked_symbols(self):
        scanner = self.scanner
        with patch.object(scanner, "_fetch_markets", return_value={
                "BTC-PERP": market(), "ETH-PERP": market()}), \
             patch.object(scanner, "_fetch_l2_quality", return_value=quality()), \
             patch.object(scanner, "_L2_BUDGET_SECONDS", -1):
            result = scanner._get_live_results(set())
        self.assertEqual(len(result), 1)
        self.assertEqual(scanner.get_scanner_funnel_snapshot().get("l2_budget_skipped"), 1)

    def test_logging_is_throttled(self):
        scanner = self.scanner
        with self.assertLogs(scanner._scanner_logger, level=logging.INFO) as logs:
            scanner._publish_scanner_funnel(scanner.Counter({"shortlisted": 2}))
            scanner._publish_scanner_funnel(scanner.Counter({"shortlisted": 3}))
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(scanner.get_scanner_funnel_snapshot()["shortlisted"], 3)


    def test_demo_looser_book_filter_never_leaks_to_live_cache(self):
        scanner = self.scanner
        markets = {"BTC-PERP": market(), "ETH-PERP": market()}
        with patch.object(scanner, "_fetch_markets", return_value=markets), \
             patch.object(scanner, "_fetch_l2_quality", return_value=quality(book=750)):
            demo = scanner.get_ranked_symbols(execution_mode="demo")
            live = scanner.get_ranked_symbols()
            demo_again = scanner.get_ranked_symbols(execution_mode="demo")
            live_again = scanner.get_ranked_symbols()
        self.assertEqual({r["symbol"] for r in demo}, {"BTC-PERP", "ETH-PERP"})
        self.assertEqual(live, [])
        self.assertEqual(demo_again, demo)
        self.assertEqual(live_again, [])
        self.assertEqual(scanner._DEMO_LAST_GOOD_RESULTS, demo)
        self.assertEqual(scanner._LAST_GOOD_RESULTS, [])

    def test_demo_book_filter_still_blocks_very_thin_or_wide_books(self):
        scanner = self.scanner
        markets = {"BTC-PERP": market(), "ETH-PERP": market()}
        books = {"BTC-PERP": quality(book=200), "ETH-PERP": quality(spread=35, book=3000)}
        with patch.object(scanner, "_fetch_markets", return_value=markets), \
             patch.object(scanner, "_fetch_l2_quality", side_effect=books.get):
            self.assertEqual(scanner._get_live_results(set(), execution_mode="demo"), [])
        counters = scanner.get_scanner_funnel_snapshot()
        self.assertEqual(counters.get("top_book_rejected"), 1)
        self.assertEqual(counters.get("spread_rejected"), 1)
        self.assertEqual(counters.get("mode_demo"), 1)


if __name__ == "__main__":
    unittest.main()
