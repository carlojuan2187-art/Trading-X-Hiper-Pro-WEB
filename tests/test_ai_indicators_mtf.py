"""Regression tests for research PPO v3 M5/M15/H1 technical skill features."""
from __future__ import annotations
import math
import unittest

from app.demo.ai_features import (
    OBS_NAMES, ADVANCED_OBS_NAMES, ADVANCED_HISTORY_BARS,
    advanced_observation, observation
)
from app.demo.ai_indicators import aggregate, adx, rsi
from app.demo.ai_stop_management import tighten_stop, maybe_follow_trailing


def candles(n=480, direction=1):
    out=[]
    px=100.0
    for i in range(n):
        delta=direction*(0.028 + 0.007*math.sin(i/5))
        op=px
        px += delta
        out.append({"t":i*300000,"o":op,
                    "h":max(op,px)+.016, "l":min(op,px)-.013,
                    "c":px, "v":200+50*(1+math.cos(i/9))})
    return out


def position(side="long"):
    return {"decision_source":"ai_ppo_only",
            "direction":side, "entry_price":100.0,
            "quantity":2.5,"margin_usdc":25.0,"entry_fee_usdc":.125,
            "initial_stop_price":99.0 if side=="long" else 101.0,
            "stop_price":99.0 if side=="long" else 101.0,
            "ai_entry_atr":.3,"ai_trailing_active":False}


class IndicatorTests(unittest.TestCase):
    def test_closed_m5_m15_h1_without_phantom_candles(self):
        data=candles()
        self.assertEqual(len(aggregate(data, 15)),160)
        self.assertEqual(len(aggregate(data, 60)),40)
        self.assertEqual(len(aggregate(data[:-1],15)),159)
        self.assertEqual(len(aggregate(data[:-1],60)),39)
        features, atr=advanced_observation(data, data[-1]["c"]-.01,
                                           data[-1]["c"]+.01)
        self.assertEqual(len(features),len(ADVANCED_OBS_NAMES))
        self.assertGreater(atr,0)
        self.assertTrue(all(math.isfinite(x) and -3<=x<=3 for x in features))
        self.assertGreater(features[ADVANCED_OBS_NAMES.index("m15_adx14")],0)
        self.assertGreater(features[ADVANCED_OBS_NAMES.index("h1_di_delta")],0)

    def test_v2_compatibility_and_gaps(self):
        data=candles()
        old, _=observation(data[-40:], data[-1]["c"]-.02, data[-1]["c"]+.02)
        self.assertEqual(len(old),len(OBS_NAMES))
        with self.assertRaisesRegex(ValueError,"advanced_candles_required"):
            advanced_observation(data[-400:],data[-1]["c"]-.02,data[-1]["c"]+.02)
        broken=candles()
        broken[-5]["t"]+=600000
        with self.assertRaisesRegex(ValueError,"multi_timeframe_history_gap"):
            advanced_observation(broken,data[-1]["c"]-.02,data[-1]["c"]+.02)

    def test_position_has_advanced_learning_context(self):
        data=candles()
        p=position()
        p["audit_mfe_usdc"]=2.5
        values,_=advanced_observation(data,101.0,101.02,p)
        self.assertGreater(values[ADVANCED_OBS_NAMES.index("position_net_r")],0)
        self.assertGreater(values[ADVANCED_OBS_NAMES.index("position_best_mfe_r")],0)

    def test_long_protect_and_trail_never_worsens_stop(self):
        p=position()
        self.assertIsNone(tighten_stop(p,100,100.01,action="TRAIL"))
        protected=tighten_stop(p,100.5,100.51,action="PROTECT")
        self.assertIsNotNone(protected)
        self.assertGreater(protected["stop_price"],p["stop_price"])
        self.assertLess(protected["stop_price"],100.5)
        moved=tighten_stop(protected,102,102.01,action="TRAIL")
        self.assertGreater(moved["stop_price"],protected["stop_price"])
        self.assertTrue(moved["ai_trailing_active"])
        # On retracement, trailing never relaxes the previous stop.
        self.assertIsNone(maybe_follow_trailing(moved,101.9,101.91))

    def test_short_protect_symmetry_and_no_premature_lock(self):
        p=position("short")
        self.assertIsNone(tighten_stop(p,100.2,100.21,action="PROTECT"))
        q=tighten_stop(p,99.0,99.01,action="TRAIL")
        self.assertIsNotNone(q)
        self.assertLess(q["stop_price"],p["stop_price"])
        self.assertGreater(q["stop_price"],99.01)


if __name__=="__main__":
    unittest.main()
