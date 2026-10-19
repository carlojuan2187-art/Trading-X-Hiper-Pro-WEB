"""Lightweight contextual paper-feedback update to the *same* PPO actor.

The original PPO actor stays immutable and verified by SHA256. DEMO learns an
online last-layer residual solely from actual closed paper trades after fees;
offline PPO checkpoints are still required for critic/on-policy retraining.

This is an auxiliary off-policy policy-gradient update, NOT a PPO gradient.
It does NOT create fake trades, artificial reward bonuses or alter SL/TP.
"""
from __future__ import annotations
import math

from app.demo.ai_experience import clean_observation

ACTIONS = ("WAIT", "LONG", "SHORT", "CLOSE")


def probabilities(logits: list[float]) -> list[float]:
    m = max(logits)
    e = [math.exp(x-m) for x in logits]
    return [v/sum(e) for v in e]


class PaperFeedbackActor:
    """Bounded model-local correction learned online from closed net PnL."""
    def __init__(self, size: int = 32, *, rate: float = 0.025):
        self.size = size
        self.rate = rate
        self.weights = [[0.0]*size for _ in range(4)]
        self.bias = [0.0]*4
        self.seen: set[str] = set()
        self.updated = 0
        self.positive = 0
        self.negative = 0

    def logits(self, base_logits: list[float], embedding: list[float]) -> list[float]:
        if len(embedding) != self.size or len(base_logits) < 4:
            raise ValueError("online_feedback_shape")
        output = list(base_logits)
        for k in range(4):
            output[k] += self.bias[k] + sum(w*h for w, h in zip(self.weights[k], embedding))
        return output

    def learn(self, record: dict, features) -> bool:
        """features(observation) -> (32-d hidden activation, baseline 10 logits)."""
        identity = str(record["_id"])
        if identity in self.seen:
            return False
        obs = clean_observation(record["observation"])
        action = str(record["action"]).upper()
        if action not in ("LONG", "SHORT"):
            raise ValueError("feedback_action_must_be_long_or_short")
        margin = float(record["margin_usdc"])
        pnl = float(record["pnl_net_usdc"])
        if not all(map(math.isfinite,(margin, pnl))) or margin <= 0:
            raise ValueError("feedback_outcome_invalid")
        hidden, base_logits = features(obs)
        if len(hidden) != self.size:
            raise ValueError("feedback_embedding_shape")
        corrected = self.logits(base_logits, hidden)
        probs = probabilities(corrected[:4])
        target = ACTIONS.index(action)
        # ±1 directional reward measured with REAL realized AFTER-FEES PnL.
        # 3% of margin is typical scale; tanh robustly clips extreme outliers.
        advantage = math.tanh(pnl / (0.03 * margin))
        if abs(advantage) > 1e-10:
            for k in range(4):
                step = self.rate * advantage * ((1.0 if k == target else 0.0)-probs[k])
                self.bias[k] = min(0.8,max(-0.8,self.bias[k]+step))
                for j, feature in enumerate(hidden):
                    delta = step * feature / max(1, self.size)
                    self.weights[k][j] = min(0.08,max(-0.08,self.weights[k][j]+delta))
            if pnl > 0:
                self.positive += 1
            elif pnl < 0:
                self.negative += 1
        self.seen.add(identity)
        self.updated += 1
        return True

    def snapshot(self) -> dict:
        return {"samples":self.updated, "positive":self.positive,
                "negative":self.negative, "method":"bounded_paper_feedback_policy_gradient",
                "max_bias":round(max(map(abs,self.bias)),6)}
