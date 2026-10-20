"""Separate v4 multi-skill RESEARCH trainer for Trading X DEMO.

Learns EMA/RSI/ADX/DI/MACD/Bollinger over closed M5/M15/H1 bars, and chooses
WAIT/LONG/SHORT/CLOSE/PROTECT/TRAIL. Valid-state action masks prevent CLOSE
while flat and LONG/SHORT while in a position. Uses real 5m candles only;
always exports an UNAPPROVED checkpoint, never places orders or touches LIVE.
"""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import time

from app.demo.ai_features import (
    ADVANCED_ACTIONS, ADVANCED_HISTORY_BARS, BAR_MS, STOP_ATR, TAKE_ATR,
)
from app.demo.ai_stop_management import tighten_stop, maybe_follow_trailing
from tools.train_demo_ppo import load_csv
from tools.ppo_skill_curriculum_v4 import (
    SKILLS_OBS_NAMES, SKILLS_MODEL_SCHEMA, skills_observation,
    build_skills_curriculum, pretrain_skills_actor,
)


def research_env(datasets, *, training, profile, seed=21):
    import gymnasium as gym
    from gymnasium import spaces
    import numpy as np

    class MTFAgentEnvironment(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self.action_space = spaces.MultiDiscrete([len(ADVANCED_ACTIONS), 3, 3])
            self.observation_space = spaces.Box(
                low=-3.0, high=3.0, shape=(len(SKILLS_OBS_NAMES),),
                dtype=np.float32)
            self.symbols = list(datasets)
            self.prng = random.Random(seed)
            self.prng.shuffle(self.symbols)
            self.sample_index = 0
            self.initial = float(profile["initial_capital_usdc"])
            self.leverage = int(profile["leverage"])
            self.margin_cap = float(profile["margin_cap_usdc"])
            self.position_percent = float(profile["position_percent"])
            self.fee_rate = float(profile["taker_fee_bps"]) / 10000
            self.impact = (float(profile["slippage_bps"]) +
                           float(profile["assumed_spread_bps"])) / 10000
            self.book_spread = float(profile["assumed_spread_bps"]) / 10000
            self.rows = next(iter(datasets.values()))
            self.idx = ADVANCED_HISTORY_BARS
            self.start = self.idx
            self.cash = self.initial
            self.peak = self.initial
            self.max_drawdown = 0.0
            self.position = None
            self.profits = []
            self.actions_flat = [0]*len(ADVANCED_ACTIONS)
            self.actions_position = [0]*len(ADVANCED_ACTIONS)
            self.stop_moves = 0

        def action_masks(self):
            # Primary MultiDiscrete head followed by unrestricted SL/TP bins.
            allowed = ([True, False, False, True, True, True] if self.position
                       else [True, True, True, False, False, False])
            return np.asarray(allowed + [True]*6, dtype=bool)

        def _price(self, px, side):
            return px*(1+self.impact if side=="buy" else 1-self.impact)

        def _marked(self, px):
            if not self.position:
                return self.cash
            p=self.position
            sign=1 if p["direction"]=="long" else -1
            q=p["quantity"]
            # Mark includes a prospective exit fee. Entry fee has already been
            # deducted from cash at opening, so it is never charged twice.
            exit_price=self._price(px,"sell" if sign>0 else "buy")
            return self.cash+sign*(exit_price-p["entry_price"])*q - abs(exit_price*q)*self.fee_rate

        def _observation(self):
            bar_end=self.idx
            p=None
            if self.position is not None:
                p=dict(self.position)
                p["ai_bars_held"]=self.idx-self.position["ai_start"]
            rows=self.rows[bar_end-ADVANCED_HISTORY_BARS:bar_end]
            px=rows[-1]["c"]
            return np.asarray(skills_observation(
                rows, px*(1-self.book_spread/2),
                px*(1+self.book_spread/2), p)[0], dtype=np.float32)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if training:
                if self.sample_index and self.sample_index%len(self.symbols)==0:
                    self.prng.shuffle(self.symbols)
                self.rows=datasets[self.symbols[self.sample_index%len(self.symbols)]]
                self.sample_index+=1
            else:
                self.rows=next(iter(datasets.values()))
            length=len(self.rows)
            start=ADVANCED_HISTORY_BARS
            if training and length>start+513:
                start=self.prng.randrange(start, length-512)
            self.idx=self.start=start
            self.cash=self.peak=self.initial
            self.max_drawdown=0.
            self.position=None
            self.profits=[]
            self.actions_flat=[0]*len(ADVANCED_ACTIONS)
            self.actions_position=[0]*len(ADVANCED_ACTIONS)
            self.stop_moves=0
            return self._observation(), {}

        def _close(self, price):
            p=self.position
            sign=1 if p["direction"]=="long" else -1
            exit_price=self._price(price,"sell" if sign>0 else "buy")
            gross=sign*(exit_price-p["entry_price"])*p["quantity"]
            fee=abs(exit_price*p["quantity"])*self.fee_rate
            self.cash+=gross-fee
            self.profits.append(gross-fee-p["entry_fee_usdc"])
            self.position=None

        def step(self, action):
            act,stop_bin,take_bin=map(int,action)
            if not self.action_masks()[act]:
                raise ValueError("Invalid primary action: MaskablePPO mask failure")
            bar=self.rows[self.idx]
            previous_close=self.rows[self.idx-1]["c"]
            before=max(self._marked(previous_close),1e-9)
            prior_dd=max(0.0,1-before/max(self.peak,1e-9))
            (self.actions_position if self.position else self.actions_flat)[act]+=1
            opening=bar["o"]
            # This step uses only the NEXT bar OPEN for decisions, never its
            # future high/low/close. OHLC outcomes come after the action.
            if self.position is not None:
                if act==3:
                    self._close(opening)
                else:
                    p=self.position
                    bid=opening*(1-self.book_spread/2)
                    ask=opening*(1+self.book_spread/2)
                    revised=None
                    if act in (4,5):
                        revised=tighten_stop(p,bid,ask,
                                             action="PROTECT" if act==4 else "TRAIL")
                    elif p.get("ai_trailing_active"):
                        revised=maybe_follow_trailing(p,bid,ask)
                    if revised is not None:
                        self.stop_moves+=1
                        self.position=revised
            elif act in (1,2):
                sign=1 if act==1 else -1
                entry=self._price(opening,"buy" if sign>0 else "sell")
                margin=min(max(0,self.cash*self.position_percent),self.margin_cap)
                notional=margin*self.leverage
                entry_fee=notional*self.fee_rate
                if notional>=10.2 and self.cash>=margin+entry_fee:
                    from app.demo.ai_features import observation
                    old=self.rows[self.idx-40:self.idx]
                    atr=observation(old,old[-1]["c"]*.9999,old[-1]["c"]*1.0001)[1]
                    stop=entry-sign*STOP_ATR[stop_bin]*atr
                    tp=entry+sign*TAKE_ATR[take_bin]*atr
                    if min(stop,tp)>0:
                        qty=notional/entry
                        self.cash-=entry_fee
                        self.position={
                            "direction":"long" if sign>0 else "short",
                            "decision_source":"ai_ppo_only",
                            "entry_price":entry,"quantity":qty,
                            "margin_usdc":margin,
                            "initial_stop_price":stop,"stop_price":stop,
                            "target_price":tp,"ai_entry_atr":atr,"ai_start":self.idx,
                            "entry_fee_usdc":entry_fee,
                            "audit_mfe_usdc":0.0,
                            "ai_trailing_active":False,"ai_stop_moves":0,
                        }
            if self.position:
                p=self.position
                sign=1 if p["direction"]=="long" else -1
                stop=p["stop_price"]
                tp=p["target_price"]
                stop_hit=bar["l"]<=stop if sign>0 else bar["h"]>=stop
                target_hit=bar["h"]>=tp if sign>0 else bar["l"]<=tp
                # When both are touched, assume STOP first (conservative).
                if stop_hit:
                    stop_price=min(stop,opening) if sign>0 else max(stop,opening)
                    self._close(stop_price)
                elif target_hit:
                    self._close(tp)
                else:
                    favorable=(bar["h"]-p["entry_price"] if sign>0
                               else p["entry_price"]-bar["l"])
                    p["audit_mfe_usdc"]=max(
                        p["audit_mfe_usdc"],max(0.0,favorable*p["quantity"]))
            equity=max(self._marked(bar["c"]),1e-9)
            self.peak=max(self.peak,equity)
            dd=max(0,1-equity/max(self.peak,1e-9))
            self.max_drawdown=max(self.max_drawdown,dd)
            reward=100*math.log(equity/before)-2*max(0,dd-prior_dd)
            self.idx+=1
            done=self.idx>=len(self.rows)-1 or equity<.2*self.initial
            truncated=bool(training and self.idx-self.start>=512)
            if done or truncated:
                if self.position:
                    before_exit=equity
                    self._close(bar["c"])
                    equity=max(self.cash,1e-9)
                    reward+=100*math.log(equity/before_exit)
            info={"net_pnl":round(self.cash-self.initial,6),
                  "trades":len(self.profits),
                  "max_drawdown":round(self.max_drawdown,6),
                  "stop_moves":self.stop_moves}
            return (np.zeros(len(SKILLS_OBS_NAMES),dtype=np.float32)
                    if done or truncated else self._observation()),float(reward),done,truncated,info

    return MTFAgentEnvironment()


def evaluate(model, rows, profile):
    env=research_env({"validation":rows},training=False,profile=profile)
    obs,_=env.reset()
    terminated=truncated=False
    while not (terminated or truncated):
        action,_=model.predict(obs,action_masks=env.action_masks(),deterministic=True)
        obs,_,terminated,truncated,info=env.step(action)
    wins=[p for p in env.profits if p>0]
    losses=[-p for p in env.profits if p<0]
    return {"trades":len(env.profits),"wins":len(wins),
            "net_pnl_usdc":round(env.cash-env.initial,5),
            "gross_profit_usdc":round(sum(wins),5),
            "gross_loss_usdc":round(sum(losses),5),
            "profit_factor":round(sum(wins)/sum(losses),5) if losses else None,
            "max_drawdown":round(env.max_drawdown,5),
            "stop_moves":env.stop_moves,
            "actions_flat":dict(zip(ADVANCED_ACTIONS,env.actions_flat)),
            "actions_position":dict(zip(ADVANCED_ACTIONS,env.actions_position)),
            "holdout_candles":len(rows)}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--csv",required=True)
    p.add_argument("--universe-report",required=True)
    p.add_argument("--output",default="models/trading_x_ppo_v4_market_skills_research")
    p.add_argument("--steps",type=int,default=120000)
    p.add_argument("--seed",type=int,default=21,help="Reproducible experimental seed")
    p.add_argument("--initial-capital",type=float,default=100)
    p.add_argument("--leverage",type=int,default=10)
    p.add_argument("--margin-cap",type=float,default=25)
    p.add_argument("--position-percent",type=float,default=1.0)
    p.add_argument("--taker-fee-bps",type=float,default=5.0)
    p.add_argument("--slippage-bps",type=float,default=2.0)
    p.add_argument("--assumed-spread-bps",type=float,default=1.0)
    p.add_argument("--lessons-per-symbol",type=int,default=160)
    p.add_argument("--curriculum-epochs",type=int,default=3)
    p.add_argument("--management-lessons-per-symbol",type=int,default=60)
    p.add_argument("--skip-curriculum",action="store_true",
                   help="Controlled ablation: train pure PPO without teacher lessons")
    a=p.parse_args()
    if (a.steps<20000 or not 10<=a.initial_capital<=100000
            or not 1<=a.leverage<=50 or not 1<=a.margin_cap<=1000
            or not 0<a.position_percent<=1 or
            any(not 0<=x<=50 for x in
                (a.taker_fee_bps,a.slippage_bps,a.assumed_spread_bps))):
        p.error("unsafe training profile")
    from sb3_contrib import MaskablePPO
    import torch
    report=json.loads(Path(a.universe_report).read_text(encoding="utf-8"))
    if report.get("source")!="LIVE_SCANNER_PREFILTER":
        p.error("Research must use full dynamic LIVE/Light universe report")
    symbols=sorted(x["symbol"] for x in report["downloaded"])
    requested=report["requested"]
    if not symbols or len(symbols)<10 or len(symbols)/len(requested)<.8:
        p.error("Insufficient genuine LIVE market data coverage")
    datasets={sym:load_csv(Path(a.csv),sym) for sym in symbols}
    profile={
        "initial_capital_usdc":a.initial_capital,"leverage":a.leverage,
        "margin_cap_usdc":a.margin_cap,"position_percent":a.position_percent,
        "taker_fee_bps":a.taker_fee_bps,"slippage_bps":a.slippage_bps,
        "assumed_spread_bps":a.assumed_spread_bps}
    train={}
    holdout={}
    for symbol, rows in datasets.items():
        split=int(len(rows)*.75)
        if split<ADVANCED_HISTORY_BARS+514 or len(rows)-split<200:
            raise ValueError("too few separate MTF train/holdout candles: "+symbol)
        train[symbol]=rows[:split]
        # Warm up indicators ONLY on older training-time candles. Scored
        # actions begin at split; labels and rewards come from future holdout.
        holdout[symbol]=rows[split-ADVANCED_HISTORY_BARS:]
    env=research_env(train,training=True,profile=profile,seed=a.seed)
    model=MaskablePPO("MlpPolicy",env,verbose=0,seed=a.seed,device="cpu",
                      n_steps=1024,batch_size=256,gamma=.995,
                      gae_lambda=.97,learning_rate=1e-4,ent_coef=.015,
                      policy_kwargs={"net_arch":{"pi":[64,64],"vf":[64,64]},
                                     "activation_fn":torch.nn.Tanh})
    if not a.skip_curriculum:
        lessons=build_skills_curriculum(
            train,profile,flat_per_symbol=a.lessons_per_symbol,
            manage_per_symbol=a.management_lessons_per_symbol,seed=a.seed)
        lesson_report=pretrain_skills_actor(
            model,lessons,epochs=a.curriculum_epochs,seed=a.seed)
    else:
        lesson_report={"stage":"ABLATION_PPO_ONLY","lessons":0,"epochs":0}
    print("V4_SKILL_CURRICULUM "+json.dumps(lesson_report,sort_keys=True),flush=True)
    effective=max(a.steps,len(symbols)*512*4)
    print(f"V4_TRAINING_COVERAGE symbols={len(symbols)} requested_steps={a.steps} effective_steps={effective} full_M5_M15_H1=True",flush=True)
    model.learn(total_timesteps=effective)
    results={symbol:evaluate(model,rows,profile) for symbol,rows in holdout.items()}
    trades=sum(x["trades"] for x in results.values())
    pnl=sum(x["net_pnl_usdc"] for x in results.values())
    pos=sum(x["gross_profit_usdc"] for x in results.values())
    neg=sum(x["gross_loss_usdc"] for x in results.values())
    active_symbols=sum(1 for x in results.values() if x["trades"] > 0)
    flat_actions={action:sum(x["actions_flat"].get(action,0) for x in results.values())
                  for action in ADVANCED_ACTIONS}
    position_actions={action:sum(x["actions_position"].get(action,0) for x in results.values())
                      for action in ADVANCED_ACTIONS}
    reasons=[]
    if trades<len(symbols): reasons.append("INSUFFICIENT_HOLDOUT_TRADES")
    if active_symbols < math.ceil(len(symbols)*0.60):
        reasons.append("INSUFFICIENT_SYMBOL_ACTIVITY")
    if pnl<=0: reasons.append("NEGATIVE_OR_FLAT_NET_PNL")
    if any(x["max_drawdown"]>.25 for x in results.values()): reasons.append("EXCESSIVE_SINGLE_MARKET_DRAWDOWN")
    quality={"trades":trades,"wins":sum(x["wins"] for x in results.values()),
             "active_symbols":active_symbols,"trained_symbols":len(symbols),
             "action_distribution_flat":flat_actions,
             "action_distribution_in_position":position_actions,
             "net_pnl_sum_separate_100usdc_accounts":round(pnl,4),
             "profit_factor_aggregated":round(pos/neg,4) if neg else None,
             "stop_moves":sum(x["stop_moves"] for x in results.values()),
             "candidate_quality_reasons":reasons,"model_approved":False}
    print("V4_HOLDOUT_QUALITY "+json.dumps(quality),flush=True)
    policy=model.policy
    def layer(module):
        return {"weight":module.weight.detach().cpu().tolist(),
                "bias":module.bias.detach().cpu().tolist()}
    actor={"activation":"tanh",
           "hidden":[layer(m) for m in policy.mlp_extractor.policy_net
                     if isinstance(m,torch.nn.Linear)],
           "head":layer(policy.action_net)}
    if len(actor["hidden"])!=2 or len(actor["head"]["bias"])!=12:
        raise ValueError("unexpected v3 action dimensions")
    output=Path(a.output)
    output.mkdir(parents=True,exist_ok=True)
    actor_data=json.dumps(actor,separators=(",",":"),allow_nan=False).encode()
    (output/"actor.json").write_bytes(actor_data)
    model.save(str(output/"ppo_model.zip"))
    meta={"schema":SKILLS_MODEL_SCHEMA,"version":f"ppo-v4-{int(time.time())}",
          "observation_names":list(SKILLS_OBS_NAMES),"action_dims":[6,3,3],
          "actor_file":"actor.json","actor_sha256":sha256(actor_data).hexdigest(),
          "trained_symbols":symbols,"approved_for_demo":False,
          "training_algorithm":"MaskablePPO","training_steps":int(model.num_timesteps),
          "training_seed":a.seed,
          "training_checkpoint_file":"ppo_model.zip",
          "training_checkpoint_sha256":sha256((output/"ppo_model.zip").read_bytes()).hexdigest(),
          "runtime_profile":profile,"universe_source":"LIVE_SCANNER_PREFILTER",
          "universe_snapshot_timestamp_ms":report.get("snapshot_timestamp_ms"),
          "universe_coverage":report.get("coverage"),
          "indicator_timeframes":["M5","M15","H1"],
          "skills":["price_structure","support_resistance","breakout","rejection_wicks","trend_regime","momentum_shift","volatility_expansion","relative_volume","management_protect_trail_close"],
          "indicators":["EMA9","EMA21","RSI14","ADX14","DI+","DI-","MACD","Bollinger","volume","ATR"],
          "action_semantics":list(ADVANCED_ACTIONS),
          "training_candles":{s:len(train[s]) for s in symbols},
          "holdout":results,"candidate_quality":quality,
          "training_curriculum":lesson_report,
          "feedback_from_demo_trade_archive":"NOT_APPLIED_V2_SCHEMA_NEEDS_RECONSTRUCTION",
          "deployment_status":"RESEARCH_ONLY_NOT_DEPLOYED"}
    (output/"manifest.json").write_text(json.dumps(meta,indent=2)+"\n",encoding="utf-8")
    print("V4_RESEARCH_ARTIFACT "+json.dumps({"model_version":meta["version"],"quality":quality,"path":str(output),"approved":False}),flush=True)


if __name__=="__main__":
    main()
