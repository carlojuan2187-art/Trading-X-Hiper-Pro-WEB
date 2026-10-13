import unittest

from app.trading_quality import (
    absolute_risk_reward,
    adaptive_entry_drift_limit,
    evaluate_entry_drift,
    levels_to_percentages,
)


class TradingQualityTests(unittest.TestCase):
    def test_adaptive_limit_is_capped(self):
        limit = adaptive_entry_drift_limit(
            atr_pct=0.03,
            base_pct=0.0025,
            atr_mult=0.45,
            hard_pct=0.0065,
        )
        self.assertAlmostEqual(limit, 0.0065)

    def test_long_chase_is_rejected(self):
        out = evaluate_entry_drift(
            direction="long",
            signal_price=100.0,
            current_price=100.40,
            allowed_pct=0.0030,
            adverse_mult=1.5,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "ENTRY_DRIFT_CHASE")

    def test_small_pullback_is_allowed(self):
        out = evaluate_entry_drift(
            direction="long",
            signal_price=100.0,
            current_price=99.65,
            allowed_pct=0.0030,
            adverse_mult=1.5,
        )
        self.assertTrue(out["ok"])

    def test_short_chase_is_rejected(self):
        out = evaluate_entry_drift(
            direction="short",
            signal_price=100.0,
            current_price=99.60,
            allowed_pct=0.0030,
            adverse_mult=1.5,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "ENTRY_DRIFT_CHASE")

    def test_absolute_rr_long(self):
        out = absolute_risk_reward(
            direction="long",
            entry_price=100.0,
            stop_price=99.0,
            target_price=101.2,
        )
        self.assertTrue(out["ok"])
        self.assertAlmostEqual(out["rr"], 1.2)
        pct = levels_to_percentages(
            direction="long",
            entry_price=100.0,
            stop_price=99.0,
            target_price=101.2,
        )
        self.assertAlmostEqual(pct["sl_pct"], 0.01)
        self.assertAlmostEqual(pct["tp_pct"], 0.012)

    def test_target_consumed_is_invalid(self):
        out = absolute_risk_reward(
            direction="long",
            entry_price=101.3,
            stop_price=99.0,
            target_price=101.2,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "TARGET_INVALID_FOR_ENTRY")


if __name__ == "__main__":
    unittest.main()
