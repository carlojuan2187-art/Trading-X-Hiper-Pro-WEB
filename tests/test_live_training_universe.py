"""Regression: PPO research must cover LIVE scanner eligible markets, not BTC/ETH/SOL."""
from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from app.demo.ai_features import configured_symbols
from tools.live_training_universe import scanner_limits, select_live_prefilter


def ctx(px=100, volume=3_000_000, interest=10_000, prev=100):
    return {"markPx": str(px), "prevDayPx": str(prev),
            "dayNtlVlm": str(volume), "openInterest": str(interest)}


class TestLivePPOUniverse(TestCase):
    def test_scanner_prefilter_matches_volume_oi_and_symbol_policy(self):
        names = ["BTC", "ETH", "DOGE", "LOW", "ILLQ"]
        universe = [{"name": name} for name in names]
        data = [{"universe": universe}, [
            ctx(volume=80_000_000), ctx(volume=9_000_000),
            ctx(volume=60_000_000), ctx(volume=100), ctx(interest=2),
        ]]
        with patch.dict(os.environ, {"BLOCKED_MEME_KEYWORDS": "DOGE"}):
            # Default policy is imported at module load; DOGE is default blocked.
            rows = select_live_prefilter(data)
        self.assertEqual({row["symbol"] for row in rows}, {"BTC", "ETH"})
        self.assertEqual(rows[0]["symbol"], "BTC")

    def test_not_hardcoded_to_three_or_twelve(self):
        names = [f"ASSET{i}" for i in range(35)]
        data = [{"universe": [{"name": coin} for coin in names]},
                [ctx(volume=9_000_000) for _ in names]]
        selected = select_live_prefilter(data)
        self.assertEqual(len(selected), 35)
        with patch.dict(os.environ, {"DEMO_AI_SYMBOLS": "AUTO"}):
            self.assertEqual(len(configured_symbols(names)), 35)
            self.assertEqual(set(configured_symbols(names)), set(names))
        with patch.dict(os.environ, {"DEMO_AI_SYMBOLS": "BTC,ETH"}):
            self.assertEqual(configured_symbols(names), ("BTC", "ETH"))

    def test_invalid_market_payload_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid_hyperliquid"):
            select_live_prefilter({"universe": []})
        limits = scanner_limits()
        self.assertGreater(limits["live_l2_rotation_depth"], 0)
