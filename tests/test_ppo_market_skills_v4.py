"""Research-only PPO-v4 verification: closed market context and trade curriculum."""
from __future__ import annotations

from copy import deepcopy
import math
import unittest

from app.demo.ai_market_skills import SKILL_NAMES,market_skill_features
from tools.ppo_skill_curriculum_v4 import (
    SKILLS_OBS_NAMES,SKILLS_MODEL_SCHEMA,V4_ACTIONS,
    skills_observation,build_skills_curriculum,_historical_position,
    _managed_net,
)


PROFILE={
    "initial_capital_usdc":100.0,"margin_cap_usdc":25.0,
    "leverage":10,"position_percent":1.0,
    "taker_fee_bps":5.0,"slippage_bps":2.0,
    "assumed_spread_bps":1.0,
}


def candles(n=1200,direction=1):
    rows=[]
    price=100.
    for i in range(n):
        op=price
        change=(0.011 * math.sin(i/11) + 0.014*direction +
                0.008*math.sin(i/31))
        price=max(1.0,op+change)
        rows.append({
            "t":i*300000,"o":op,
            "h":max(op,price)+0.017,
            "l":min(op,price)-0.017,
            "c":price,"v":300 + 100*abs(math.sin(i/13)),
        })
    return rows


class MarketSkillsTests(unittest.TestCase):
    def test_structure_features_stable_and_finite(self):
        bars=candles()
        f=market_skill_features(bars[-480:])
        self.assertEqual(len(f),len(SKILL_NAMES))
        self.assertTrue(all(math.isfinite(x) and -3<=x<=3 for x in f))
        self.assertGreater(f[SKILL_NAMES.index("h1_trend_slope")],0)
        p=bars[479]["c"]
        obs,atr=skills_observation(bars[:480],p*.99999,p*1.00001)
        self.assertEqual(len(obs),len(SKILLS_OBS_NAMES))
        self.assertGreater(atr,0)
        self.assertEqual(SKILLS_MODEL_SCHEMA,"trading_x_ppo_v4_market_curriculum")

    def test_future_never_in_observation(self):
        original=candles()
        index=850
        past=original[index-480:index]
        price=past[-1]["c"]
        a,_=skills_observation(past,price*.99999,price*1.00001)
        future=deepcopy(original)
        for i in range(index,len(future)):
            future[i]["o"]*=1.08
            future[i]["h"]*=1.08
            future[i]["l"]*=1.08
            future[i]["c"]*=1.08
        b,_=skills_observation(future[index-480:index],price*.99999,price*1.00001)
        self.assertEqual(a,b)

    def test_no_fake_history_and_no_gaps(self):
        bars=candles()
        with self.assertRaisesRegex(ValueError,"480"):
            market_skill_features(bars[:300])
        discontinuous=deepcopy(bars[:480])
        discontinuous[20]["t"]+=300000
        with self.assertRaisesRegex(ValueError,"contiguous"):
            market_skill_features(discontinuous)

    def test_train_only_curriculum_contains_entry_and_management(self):
        data=candles()
        lessons=build_skills_curriculum(
            {"ARB":data,"BTC":deepcopy(data)},PROFILE,
            flat_per_symbol=28,manage_per_symbol=55,seed=13)
        self.assertEqual({x.symbol for x in lessons},{"ARB","BTC"})
        self.assertTrue(any(x.domain=="entry" for x in lessons))
        self.assertTrue(any(x.domain=="position_management" for x in lessons))
        self.assertTrue(all(len(x.context)==len(SKILLS_OBS_NAMES) for x in lessons))
        self.assertTrue(all(x.action in range(6) for x in lessons))
        for x in lessons:
            if x.domain=="entry":
                self.assertIn(x.action,(0,1,2))
            else:
                self.assertIn(x.action,(0,3,4,5))
        self.assertTrue(all(x.candle_index+12<=len(data) for x in lessons))

    def test_stop_management_alternatives_obey_state(self):
        data=candles(direction=1)
        pos=_historical_position(data,at=850,side=1,age=3,profile=PROFILE)
        self.assertIsNotNone(pos)
        immediate=_managed_net(data,850,pos,3,PROFILE)
        wait=_managed_net(data,850,pos,0,PROFILE)
        self.assertTrue(math.isfinite(immediate))
        self.assertTrue(math.isfinite(wait))
        self.assertIn(_managed_net(data,850,pos,5,PROFILE) is None,(True,False))


if __name__=="__main__":
    unittest.main()
