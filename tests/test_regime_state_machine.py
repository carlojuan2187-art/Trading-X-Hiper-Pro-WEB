import unittest

from app.regime.state_machine import advance_regime_state


class RegimeStateMachineTests(unittest.TestCase):
    def test_same_closed_bar_does_not_advance_confirmation(self):
        first = advance_regime_state(
            "TREND_CONTINUATION",
            None,
            confirm_bars=3,
            bar_id="5m:1000",
        )
        repeated = advance_regime_state(
            "VOLATILE_SWEEP",
            first,
            confirm_bars=3,
            bar_id="5m:1000",
        )
        self.assertTrue(repeated["duplicate_bar"])
        self.assertEqual(repeated["bars_in_active"], first["bars_in_active"])
        self.assertEqual(repeated["pending_count"], first["pending_count"])

    def test_new_bars_advance_pending_count(self):
        state = advance_regime_state(
            "TREND_CONTINUATION",
            None,
            confirm_bars=3,
            min_active_bars=0,
            bar_id="5m:1000",
        )
        state = advance_regime_state(
            "VOLATILE_SWEEP",
            state,
            confirm_bars=3,
            min_active_bars=0,
            bar_id="5m:2000",
        )
        self.assertEqual(state["pending_count"], 1)
        state = advance_regime_state(
            "VOLATILE_SWEEP",
            state,
            confirm_bars=3,
            min_active_bars=0,
            bar_id="5m:3000",
        )
        self.assertEqual(state["pending_count"], 2)
        state = advance_regime_state(
            "VOLATILE_SWEEP",
            state,
            confirm_bars=3,
            min_active_bars=0,
            bar_id="5m:4000",
        )
        self.assertEqual(state["active_regime"], "VOLATILE_SWEEP")
        self.assertTrue(state["changed"])


if __name__ == "__main__":
    unittest.main()
