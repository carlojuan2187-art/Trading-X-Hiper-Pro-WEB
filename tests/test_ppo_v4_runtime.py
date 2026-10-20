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
from app.demo.ai_features import OBS_NAMES, ADVANCED_HISTORY_BARS
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
