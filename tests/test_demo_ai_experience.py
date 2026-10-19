"""Offline checks: learning samples are REAL closed DEMO trades and private."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from app.demo.ai_experience import build_closed_experience, clean_observation
from app.demo.ai_features import OBS_NAMES
from tools.ppo_demo_feedback import load_feedback, refine_actor_with_closed_feedback


def closed_trade(*, pnl=0.0, action="long", version="ppo-test", trade_id="one"):
    at = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
    return {
        "id": trade_id, "decision_source": "ai_ppo_only",
        "ai_model_version": version, "symbol": "BTC-PERP",
        "direction": action, "ai_entry_observation": [0.0]*len(OBS_NAMES),
        "ai_decision_candle_ms": 1792411200000,
        "opened_at": at.isoformat(), "closed_at": (at+timedelta(minutes=5)).isoformat(),
        "pnl_net_usdc": pnl, "margin_usdc": 25.0,
        "entry_fee_usdc": .1, "exit_fee_usdc": .1, "close_reason": "TP",
    }


class TestPPOClosedExperience(unittest.TestCase):
    def test_closed_zero_pnl_is_learnable_and_private(self):
        source=closed_trade(pnl=0.0)
        source["user_id"]=123456789
        source["api_key"]="secret"
        row=build_closed_experience(source)
        self.assertIsNotNone(row)
        self.assertEqual(row["pnl_net_usdc"], 0)
        self.assertEqual(row["action"], "LONG")
        self.assertNotIn("user_id", row)
        self.assertNotIn("api_key", row)
        self.assertEqual(row["_id"], "ppo-test:one")

    def test_reject_legacy_and_incomplete_ai_entry(self):
        source=closed_trade()
        source["decision_source"]="legacy"
        self.assertIsNone(build_closed_experience(source))
        source["decision_source"]="ai_ppo_only"
        del source["ai_entry_observation"]
        self.assertIsNone(build_closed_experience(source))
        with self.assertRaises(ValueError):
            clean_observation([0.0]*2)

    def test_feedback_load_dedup_and_minimum_samples(self):
        row=build_closed_experience(closed_trade(pnl=-0.4))
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"demo.jsonl"
            path.write_text(json.dumps(row)+"\n", encoding="utf-8")
            records=load_feedback(str(path), {"BTC"})
            self.assertEqual(records[0]["pnl_net_usdc"], -0.4)
            skipped=refine_actor_with_closed_feedback(None, records, min_samples=30)
            self.assertFalse(skipped["applied"])
            path.write_text(json.dumps(row)+"\n"+json.dumps(row)+"\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_feedback(str(path), {"BTC"})
            with self.assertRaisesRegex(ValueError, "invalid_or_duplicate"):
                load_feedback(str(path), {"ETH"})


if __name__ == "__main__":
    unittest.main()
