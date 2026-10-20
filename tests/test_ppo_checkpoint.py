"""Full PPO resume requires immutable schema, matching risk and SHA."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from app.demo.ai_features import MODEL_SCHEMA, OBS_NAMES
from tools.ppo_checkpoint import verified_checkpoint


class TestPPOTrailingCheckpoint(unittest.TestCase):
    def test_previous_checkpoint_integrity_and_profile(self):
        profile = {"margin_cap_usdc": 25.0, "leverage": 10}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            checkpoint=b"research-checkpoint-only"
            (p/"ppo_model.zip").write_bytes(checkpoint)
            meta={"schema":MODEL_SCHEMA,
                  "observation_names":list(OBS_NAMES), "action_dims":[4,3,3],
                  "runtime_profile":profile, "version":"ppo-previous",
                  "training_checkpoint_file":"ppo_model.zip",
                  "training_checkpoint_sha256":sha256(checkpoint).hexdigest()}
            (p/"manifest.json").write_text(json.dumps(meta))
            verified, info=verified_checkpoint(d, profile)
            self.assertEqual(verified.name,"ppo_model.zip")
            self.assertEqual(info["version"],"ppo-previous")
            with self.assertRaisesRegex(ValueError, "RISK_PROFILE"):
                verified_checkpoint(d, {"leverage": 5})
            (p/"ppo_model.zip").write_bytes(checkpoint+b"altered")
            with self.assertRaisesRegex(ValueError, "CORRUPTED"):
                verified_checkpoint(d, profile)


if __name__ == "__main__":
    unittest.main()
