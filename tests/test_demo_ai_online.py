"""Offline regressions for real-outcome online learning of Trading X PPO DEMO."""
from __future__ import annotations

import unittest

from app.demo.ai_online import PaperFeedbackActor, probabilities
from app.demo.ai_features import OBS_NAMES


def model_features(obs):
    return [0.3]*32, [0.0, 1.0, 0.0, -1.0]+[0.0]*6


def experience(i, pnl, action="LONG"):
    return {
        "_id":f"paper_v1:{i}",
        "symbol":"BTC", "action":action,
        "observation":[0.0]*len(OBS_NAMES),
        "pnl_net_usdc":pnl, "margin_usdc":25.0,
    }


class TestPaperLearning(unittest.TestCase):
    def test_losses_reduce_prob_of_repeating_bad_action(self):
        actor=PaperFeedbackActor()
        baseline=probabilities(actor.logits(model_features([])[1], [0.3]*32)[:4])[1]
        for i in range(50):
            self.assertTrue(actor.learn(experience(i,-1.0),model_features))
        after=probabilities(actor.logits(model_features([])[1], [0.3]*32)[:4])[1]
        self.assertLess(after,baseline)
        self.assertEqual(actor.snapshot()["negative"],50)
        self.assertFalse(actor.learn(experience(49,-1.0),model_features))
        self.assertEqual(actor.snapshot()["samples"],50)

    def test_positive_outcomes_increase_action_probability(self):
        actor=PaperFeedbackActor()
        old=probabilities(actor.logits(model_features([])[1],[0.3]*32)[:4])[2]
        for i in range(50):
            self.assertTrue(actor.learn(experience(i,1.0,"SHORT"),model_features))
        new=probabilities(actor.logits(model_features([])[1],[0.3]*32)[:4])[2]
        self.assertGreater(new,old)
        self.assertEqual(actor.snapshot()["positive"],50)

    def test_replay_is_deterministic_and_bounded(self):
        records=[experience(i,-2.5 if i%2 else 2.5) for i in range(300)]
        a=PaperFeedbackActor()
        b=PaperFeedbackActor()
        for record in records:
            a.learn(record,model_features)
            b.learn(record,model_features)
        self.assertEqual(a.weights,b.weights)
        self.assertEqual(a.bias,b.bias)
        self.assertTrue(all(abs(v)<=.8 for v in a.bias))
        self.assertTrue(all(abs(w)<=.08 for row in a.weights for w in row))
        self.assertEqual(a.snapshot()["samples"],300)


if __name__ == "__main__":
    unittest.main()
