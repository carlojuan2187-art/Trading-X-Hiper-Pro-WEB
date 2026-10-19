"""Offline-only contract tests: approved PPO inference and paper trade safety."""
from __future__ import annotations

from hashlib import sha256
import json
import os
# Offline test only: the project config requires these fields at import time.
os.environ.setdefault("DEMO_WORKER_MODE", "true")
os.environ.setdefault("DEMO_ENABLED", "true")
os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
import tempfile
from pathlib import Path
import time
import unittest

from app.demo.ai_ppo import (
    BAR_MS, OBS_NAMES, CandleStore, Decision, PPOPolicy, MODEL_SCHEMA,
    observation, open_ai_position,
)
from app.demo.simulator import update_position


def bars(now=None):
    now = int(time.time()*1000)//BAR_MS*BAR_MS if now is None else now
    return [{"t": now-(41-i)*BAR_MS, "o": 100+i*.1,
             "h": 101+i*.1, "l": 99+i*.1,
             "c": 100.5+i*.1, "v": 100+i} for i in range(41)]


class TestAutonomousPPO(unittest.TestCase):
    def test_candles_require_closed_and_fresh_quote(self):
        store = CandleStore()
        history = bars()
        self.assertEqual(store.prime("BTC", [{"s":"BTC", "i":"5m", **row} for row in history]), 41)
        now = int(time.time()*1000)
        store.push_book("BTC", 104, 104.1)
        snapshot = store.snapshot("BTC", now_ms=now)
        self.assertIsNotNone(snapshot)
        rows, bid, ask = snapshot
        self.assertEqual(len(rows), 40)
        self.assertTrue(all(row["t"] + BAR_MS <= now for row in rows))
        obs, atr = observation(rows, bid, ask)
        self.assertEqual(len(obs), len(OBS_NAMES))
        self.assertGreater(atr, 0)

    def test_non_contiguous_candles_fail(self):
        data = bars()[-40:]
        data[10]["t"] += 1000
        with self.assertRaisesRegex(ValueError, "candle_gap"):
            observation(data, 104, 104.1)

    def test_model_must_be_approved_and_unmodified(self):
        zero_layer = lambda inputs, outputs: {
            "weight": [[0.0]*inputs for _ in range(outputs)],
            "bias": [0.0]*outputs,
        }
        actor = {"activation":"tanh",
                 "hidden":[zero_layer(len(OBS_NAMES), 32),zero_layer(32,32)],
                 "head":zero_layer(32,10)}
        actor["head"]["bias"] = [0., 8., 0., 0., 0., 9., 0., 0., 0., 7.]
        raw = json.dumps(actor).encode()
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d/"actor.json").write_bytes(raw)
            meta = {"schema": MODEL_SCHEMA,
                    "version":"unit-test", "observation_names":list(OBS_NAMES),
                    "action_dims":[4,3,3], "trained_symbols":["BTC"],
                    "approved_for_demo":False,
                    "actor_file":"actor.json","actor_sha256":sha256(raw).hexdigest()}
            manifest=d/"manifest.json"
            manifest.write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, "not approved"):
                PPOPolicy(str(manifest))
            meta["approved_for_demo"] = True
            manifest.write_text(json.dumps(meta))
            model = PPOPolicy(str(manifest))
            decision = model.predict("BTC", [0.0]*len(OBS_NAMES))
            self.assertEqual(decision.action, "LONG")
            self.assertEqual(decision.stop_atr, 1.2)
            self.assertEqual(decision.take_atr, 3.0)
            with self.assertRaises(ValueError):
                model.predict("DOGE", [0.0]*len(OBS_NAMES))
            (d/"actor.json").write_bytes(raw+b" ")
            with self.assertRaisesRegex(ValueError, "integrity"):
                PPOPolicy(str(manifest))

    def test_ai_owns_entry_and_discretionary_close(self):
        account={"_id": 1, "mode": "DEMO", "version": 0, "enabled": True,
                 "balance_usdc": 100.0, "position": None, "position_open": False,
                 "history": [], "trades_closed":0, "wins":0, "losses":0,
                 "realized_pnl_usdc":0, "entry_timestamps":[]}
        decision = Decision("BTC", "LONG", 1.2, 2.0, 0.9, "unit-test")
        updated = open_ai_position(account, decision, 100., 100.01, 1.)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["position"]["decision_source"], "ai_ppo_only")
        self.assertIsNone(account["position"])  # original is not mutated
        closed = update_position(updated, 100.05, 100.06, force_close_reason="AI_EXIT")
        self.assertIsNone(closed["position"])
        self.assertEqual(closed["history"][-1]["close_reason"], "AI_EXIT")
        with self.assertRaisesRegex(ValueError, "AI exit requires"):
            other = dict(updated)
            other["position"] = dict(updated["position"], decision_source="live_scanner_router")
            update_position(other, 100.05, 100.06, force_close_reason="AI_EXIT")


if __name__ == "__main__":
    unittest.main()
