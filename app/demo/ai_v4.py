"""PPO V4 versioned feature contract shared by training and isolated DEMO inference."""
from __future__ import annotations
from app.demo.ai_features import ADVANCED_OBS_NAMES, advanced_observation
from app.demo.ai_market_skills import SKILL_NAMES, market_skill_features

SKILLS_OBS_NAMES = ADVANCED_OBS_NAMES + SKILL_NAMES
SKILLS_MODEL_SCHEMA = "trading_x_ppo_v4_market_curriculum"
V4_ACTIONS = ("WAIT", "LONG", "SHORT", "CLOSE", "PROTECT", "TRAIL")


def skills_observation(candles, bid, ask, position=None):
    """480 contiguous closed M5 bars; no lookahead or future price features."""
    values, atr = advanced_observation(candles, bid, ask, position)
    combined = values + market_skill_features(candles)
    if len(combined) != len(SKILLS_OBS_NAMES):
        raise ValueError("bad_v4_skills_schema")
    return combined, atr
