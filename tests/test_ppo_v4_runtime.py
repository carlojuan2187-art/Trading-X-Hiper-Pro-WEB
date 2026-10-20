"""Regression: V4 actor must use full trained observation/actions in DEMO only."""
from __future__ import annotations

import os
os.environ.setdefault("ADMIN_TELEGRAM_ID", "1")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017/?serverSelectionTimeoutMS=1000")
os.environ.setdefault("WEBAPP_SESSION_SECRET", "test-only-session-secret")
os.environ.setdefault("DEMO_WORKER_MODE", "true")
os.environ.setdefault("DEMO_ENABLED", "true")

import json
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from app.demo.ai_ppo import PPOPolicy
from app.demo.ai_v4 import SKILLS_OBS_NAMES, SKILLS_MODEL_SCHEMA
from app.demo.ai_features import OBS_NAMES, ADVANCED_HISTORY_BARS, CandleStore, BAR_MS
from app.demo.ai_v4 import skills_observation
from app.demo.ai_experience import clean_observation
from app.config import LEVERAGE, POSITION_PERCENT, MAX_TRADE_MARGIN_USDC
from app.demo.settings import DEMO_TAKER_FEE_BPS, DEMO_SLIPPAGE_BPS


class V4RuntimeTests(unittest.TestCase):
    def make_policy(self, root: Path):
        n = len(SKILLS_OBS_NAMES)
        def layer(a,b):
            return {"weight": [[0.0]*a for _ in range(b)],
                    "bias": [0.0]*b}
        actor = {"activation": "tanh",
                 "hidden": [layer(n,32),layer(32,32)],"head":layer(32,12)}
        # In a flat account only LONG wins; with an open position TRAIL wins.
        actor["head"]["bias"][:6] = [0.0,8.0,2.0,8.0,9.0,10.0]
        raw = json.dumps(actor).encode()
        (root/"actor.json").write_bytes(raw)
        cap = float(os.getenv("DEMO_AI_MAX_MARGIN_USDC","25"))
        effective = min(cap,MAX_TRADE_MARGIN_USDC) if MAX_TRADE_MARGIN_USDC > 0 else cap
        meta = {"schema":SKILLS_MODEL_SCHEMA,
                "version":"unit-v4","observation_names":list(SKILLS_OBS_NAMES),
                "action_dims":[6,3,3],"trained_symbols":["BTC"],
                "approved_for_demo":False,
                "runtime_profile":{"leverage":LEVERAGE,
                    "position_percent":POSITION_PERCENT,"margin_cap_usdc":effective,
                    "taker_fee_bps":DEMO_TAKER_FEE_BPS,
                    "slippage_bps":DEMO_SLIPPAGE_BPS},
                "actor_file":"actor.json",
                "actor_sha256":sha256(raw).hexdigest()}
        (root/"manifest.json").write_text(json.dumps(meta))
        return root/"manifest.json"

    def test_fail_closed_and_state_masked_v4(self):
        with tempfile.TemporaryDirectory() as td:
            m = self.make_policy(Path(td))
            with patch.dict(os.environ,{"DEMO_AI_PAPER_RESEARCH_MODE":"false",
                                        "DEMO_DECISION_ENGINE":"ai_ppo",
                                        "DEMO_ENABLED":"true","DEMO_WORKER_MODE":"true"}):
                with self.assertRaisesRegex(ValueError,"not approved"):
                    PPOPolicy(str(m))
            with patch.dict(os.environ,{"DEMO_AI_PAPER_RESEARCH_MODE":"true",
                                        "DEMO_DECISION_ENGINE":"ai_ppo",
                                        "DEMO_ENABLED":"true","DEMO_WORKER_MODE":"true"}):
                p = PPOPolicy(str(m))
                self.assertTrue(p.v4)
                self.assertEqual(p.lookback,ADVANCED_HISTORY_BARS)
                flat = [0.0]*len(SKILLS_OBS_NAMES)
                self.assertEqual(p.predict("BTC",flat).action,"LONG")
                active=list(flat)
                active[OBS_NAMES.index("position_sign")]=1.0
                self.assertEqual(p.predict("BTC",active).action,"TRAIL")
                self.assertEqual(len(clean_observation(flat)),len(SKILLS_OBS_NAMES))
                with self.assertRaises(ValueError):
                    p.predict("DOGE",flat)
                (Path(td)/"actor.json").write_bytes(b"tamper")
                with self.assertRaisesRegex(ValueError,"integrity"):
                    PPOPolicy(str(m))

    def test_v4_paper_active_entry_is_model_led_and_gated(self):
        with tempfile.TemporaryDirectory() as td:
            m = self.make_policy(Path(td))
            actor = json.loads((Path(td)/"actor.json").read_text())
            # WAIT dominates the unconditioned head, as in the dry V4 model.
            actor["head"]["bias"][:6] = [9.0, 2.0, 0.0, 0, 0, 0]
            raw = json.dumps(actor).encode()
            (Path(td)/"actor.json").write_bytes(raw)
            meta = json.loads(m.read_text())
            meta["actor_sha256"] = sha256(raw).hexdigest()
            m.write_text(json.dumps(meta))
            with patch.dict(os.environ, {"DEMO_AI_PAPER_RESEARCH_MODE":"true",
                                         "DEMO_DECISION_ENGINE":"ai_ppo",
                                         "DEMO_ENABLED":"true",
                                         "DEMO_WORKER_MODE":"true",
                                         "DEMO_AI_ACTIVE_PAPER_MODE":"true"}):
                p = PPOPolicy(str(m))
                flat = [0.0]*len(SKILLS_OBS_NAMES)
                # Zero evidence cannot create fake trades.
                self.assertEqual(p.predict("BTC",flat).action,"WAIT")
                for name,value in (
                    ("m5_ema9_21_pct",0.10),("m15_ema9_21_pct",0.09),
                    ("h1_ema9_21_pct",0.04),("m5_market_structure",0.3),
                    ("mtf_structure_alignment",0.2),
                ):
                    flat[SKILLS_OBS_NAMES.index(name)] = value
                choice = p.predict("BTC",flat)
                self.assertEqual(choice.action,"LONG")
                self.assertEqual(choice.selection,"PPO_V4_PAPER_DIRECTIONAL")
                self.assertGreater(choice.probability,0.5)
                flat[SKILLS_OBS_NAMES.index("m15_ema9_21_pct")] = -0.20
                self.assertEqual(p.predict("BTC",flat).action,"WAIT")
                with patch.dict(os.environ,{"DEMO_AI_ACTIVE_PAPER_MODE":"false"}):
                    self.assertEqual(p.predict("BTC",flat).action,"WAIT")
            with patch.dict(os.environ, {"DEMO_AI_PAPER_RESEARCH_MODE":"false",
                                         "DEMO_DECISION_ENGINE":"ai_ppo",
                                         "DEMO_ENABLED":"true",
                                         "DEMO_WORKER_MODE":"true"}):
                # Unapproved actors cannot use this in any nonresearch mode.
                with self.assertRaisesRegex(ValueError,"not approved"):
                    PPOPolicy(str(m))

    def test_hyperliquid_string_candles_normalize_before_mtf(self):
        # Hyperliquid public candleSnapshot and WS candles encode numeric
        # OHLCV as strings; the V4 runtime must match float CSV training data.
        import time
        aligned = int(time.time()*1000)//BAR_MS*BAR_MS
        store = CandleStore()
        original = []
        for index in range(505):
            price = 100.0 + index*0.015
            original.append({
                "s":"BTC", "i":"5m", "t":aligned-(505-index)*BAR_MS,
                "o":str(price), "h":str(price+0.03),
                "l":str(price-0.03), "c":str(price+0.01),
                "v":str(10+index%7),
            })
        self.assertEqual(store.prime("BTC",original),505)
        store.push_book("BTC",107.55,107.56)
        # Snapshot freshness must use live wall-clock milliseconds; the
        # latest CLOSED candle ends at "aligned", but the book was just received.
        snap=store.snapshot("BTC",now_ms=int(time.time()*1000),
                            lookback=ADVANCED_HISTORY_BARS)
        self.assertIsNotNone(snap)
        candles,bid,ask=snap
        self.assertIsInstance(candles[-1]["c"],float)
        values,atr=skills_observation(candles,bid,ask)
        self.assertEqual(len(values),len(SKILLS_OBS_NAMES))
        self.assertGreater(atr,0)
        bad=dict(original[-1],t=aligned,o="not-a-price")
        self.assertFalse(store.push_candle(bad))

    def test_invalid_action_contract_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.make_policy(Path(td))
            meta=json.loads(m.read_text())
            meta["action_dims"]=[4,3,3]
            m.write_text(json.dumps(meta))
            with patch.dict(os.environ,{"DEMO_AI_PAPER_RESEARCH_MODE":"true",
                                        "DEMO_DECISION_ENGINE":"ai_ppo",
                                        "DEMO_ENABLED":"true","DEMO_WORKER_MODE":"true"}):
                with self.assertRaisesRegex(ValueError,"action schema"):
                    PPOPolicy(str(m))


if __name__ == "__main__":
    unittest.main()
