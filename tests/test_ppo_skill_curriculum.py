"""Unit tests: no future-data leakage in trading lesson features."""
from __future__ import annotations

from copy import deepcopy
import unittest

from tools.ppo_skill_curriculum import build_lessons,trade_after_costs

PROFILE={"initial_capital_usdc":100.0,"margin_cap_usdc":25.0,
         "leverage":10,"position_percent":1.0,
         "taker_fee_bps":5.0,"slippage_bps":2.0,
         "assumed_spread_bps":1.0}


def real_shape_candles(n=600):
    px=100.0
    rows=[]
    for i in range(n):
        op=px
        px=op*(1.00002 if (i//31)%2==0 else .99997)
        rows.append({"t":i*300000,"o":op,
                     "h":max(op,px)*1.00035,
                     "l":min(op,px)*.99965,
                     "c":px,"v":500+i%33})
    return rows


class CurriculumTests(unittest.TestCase):
    def test_uses_every_symbol_and_stratified_history(self):
        rows=real_shape_candles()
        lessons=build_lessons({"BTC":rows,"ARB":deepcopy(rows)},PROFILE,
                              max_per_symbol=6,seed=2)
        self.assertEqual(len(lessons),12)
        self.assertEqual({x.symbol for x in lessons},{"BTC","ARB"})
        self.assertTrue(all(x.candle_index>=480 for x in lessons))
        self.assertTrue(all(len(x.context)>30 for x in lessons))
        self.assertTrue(all(x.action in (0,1,2) for x in lessons))

    def test_teacher_future_not_in_live_observation(self):
        rows=real_shape_candles()
        at=501
        from app.demo.ai_features import advanced_observation
        before=rows[at-480:at]
        px=before[-1]["c"]
        initial=advanced_observation(before,px*.99999,px*1.00001)[0]
        altered=deepcopy(rows)
        for i in range(at,at+12):
            altered[i]["o"]=px*1.05
            altered[i]["c"]=px*1.05
            altered[i]["h"]=px*1.051
            altered[i]["l"]=px*1.049
        after=advanced_observation(altered[at-480:at],px*.99999,px*1.00001)[0]
        self.assertEqual(initial,after)
        self.assertNotEqual(
            trade_after_costs(rows,at,1,profile=PROFILE),
            trade_after_costs(altered,at,1,profile=PROFILE))

    def test_both_sides_fee_adjusted_and_invalid_window(self):
        rows=real_shape_candles()
        long_net=trade_after_costs(rows,520,1,profile=PROFILE)
        short_net=trade_after_costs(rows,520,-1,profile=PROFILE)
        self.assertTrue(isinstance(long_net,float))
        self.assertTrue(isinstance(short_net,float))
        with self.assertRaises(ValueError):
            trade_after_costs(rows,596,1,profile=PROFILE)
        with self.assertRaises(ValueError):
            trade_after_costs(rows,520,0,profile=PROFILE)

    def test_no_invented_learnings_from_too_short_history(self):
        with self.assertRaisesRegex(ValueError,"insufficient_curriculum_history"):
            build_lessons({"BTC":real_shape_candles(485)},PROFILE,
                          max_per_symbol=4)


if __name__=="__main__":
    unittest.main()
