"""Integrity gate for resuming PPO training, never for Railway runtime.

The full Stable-Baselines checkpoint includes optimizer/value network and
allows continuation rather than restarting the actor every research cycle.
Only load artifacts from one's own trusted training runs: PyTorch serialization
is not safe with untrusted ZIP/pickle content.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from app.demo.ai_features import MODEL_SCHEMA, OBS_NAMES


def verified_checkpoint(directory: str, profile: dict) -> tuple[Path, dict]:
    parent = Path(directory)
    manifest = json.loads((parent / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != MODEL_SCHEMA or
        manifest.get("observation_names") != list(OBS_NAMES) or
        manifest.get("action_dims") != [4, 3, 3]):
        raise ValueError("INCOMPATIBLE_PREVIOUS_PPO_SCHEMA")
    if manifest.get("runtime_profile") != profile:
        raise ValueError("INCOMPATIBLE_PREVIOUS_PPO_RISK_PROFILE")
    filename = manifest.get("training_checkpoint_file")
    digest = manifest.get("training_checkpoint_sha256")
    if filename != "ppo_model.zip" or not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("MISSING_SIGNED_PPO_TRAINING_CHECKPOINT")
    path = parent / filename
    if sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("CORRUPTED_PREVIOUS_PPO_CHECKPOINT")
    return path, manifest
