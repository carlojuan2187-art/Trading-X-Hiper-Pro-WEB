"""TRAIN-ONLY market and position-management curriculum for PPO-v4 research.

Each historical lesson has a fully closed M5/M15/H1 observation. Forward
outcomes are private TRAIN-only teaching labels, not predictors supplied at
inference time and never evaluated on the holdout segment.

The teacher tests WAIT/LONG/SHORT plus CLOSE/PROTECT/TRAIL on realistic
hypothetical paper positions; it does not assert perfect market knowledge.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
import math
import random

from app.demo.ai_features import ADVANCED_OBS_NAMES, ADVANCED_HISTORY_BARS, advanced_observation
from app.demo.ai_market_skills import SKILL_NAMES, market_skill_features
from app.demo.ai_stop_management import tighten_stop, maybe_follow_trailing
from tools.ppo_skill_curriculum import build_lessons, trade_after_costs

SKILLS_OBS_NAMES = ADVANCED_OBS_NAMES + SKILL_NAMES
SKILLS_MODEL_SCHEMA = "trading_x_ppo_v4_market_curriculum"
V4_ACTIONS = ("WAIT","LONG","SHORT","CLOSE","PROTECT","TRAIL")


def skills_observation(candles, bid, ask, position=None):
    values, atr=advanced_observation(candles,bid,ask,position)
    extra=market_skill_features(candles)
    combined=values+extra
    if len(combined)!=len(SKILLS_OBS_NAMES):
        raise ValueError("bad_v4_skills_schema")
    return combined,atr


@dataclass(frozen=True)
class SkillLesson:
    symbol: str
    candle_index: int
    context: tuple[float,...]
    action: int
    domain: str
    regime: str


def _regime(context):
    obs=dict(zip(SKILLS_OBS_NAMES,context))
    if obs["m5_volatility_expansion"]>0.75:
        return "VOLATILITY_EXPANSION"
    if obs["m5_breakout_up_atr"]>0.12:
        return "BREAKOUT_UP"
    if obs["m5_breakout_down_atr"]>0.12:
        return "BREAKOUT_DOWN"
    if obs["m15_adx14"] < -0.2:
        return "RANGE_OR_WEAK_TREND"
    structure=obs["mtf_structure_alignment"]
    if structure>0.18:
        return "MULTITIMEFRAME_BULL"
    if structure< -0.18:
        return "MULTITIMEFRAME_BEAR"
    return "MIXED_OR_TRANSITION"


def _historical_position(rows, at, side, age, profile):
    """Only positions surviving from historical earlier bars qualify."""
    entry_idx=at-age
    if entry_idx<ADVANCED_HISTORY_BARS or age<2:
        return None
    start=rows[entry_idx]["o"]
    # Use fully closed candles available before hypothetical entry.
    previous=rows[entry_idx-40:entry_idx]
    _,atr=advanced_observation(
        rows[entry_idx-ADVANCED_HISTORY_BARS:entry_idx],
        previous[-1]["c"]*0.99999,previous[-1]["c"]*1.00001)
    impact=(profile["slippage_bps"]+profile["assumed_spread_bps"])/10000
    fee=profile["taker_fee_bps"]/10000
    entry=start*(1+side*impact)
    stop=entry-side*1.2*atr
    target=entry+side*2.0*atr
    if min(stop,target)<=0:
        return None
    for bar in rows[entry_idx:at]:
        hit_stop=(bar["l"]<=stop if side>0 else bar["h"]>=stop)
        hit_target=(bar["h"]>=target if side>0 else bar["l"]<=target)
        if hit_stop or hit_target:
            return None
    notional=profile["margin_cap_usdc"]*profile["leverage"]
    qty=notional/entry
    if notional<10.2 or qty<=0:
        return None
    # Only completed historical bars contribute MFE.
    favorable=max(0, max(
        (b["h"]-entry if side>0 else entry-b["l"])
        for b in rows[entry_idx:at]))
    return {
        "direction":"long" if side>0 else "short",
        "decision_source":"ai_ppo_only",
        "entry_price":entry,"quantity":qty,
        "margin_usdc":profile["margin_cap_usdc"],
        "entry_fee_usdc":fee*notional,
        "initial_stop_price":stop,"stop_price":stop,
        "target_price":target,"ai_entry_atr":atr,
        "ai_bars_held":age,
        "audit_mfe_usdc":favorable*qty,
        "ai_trailing_active":False,"ai_stop_moves":0,
    }


def _managed_net(rows, at, position, action, profile, *, horizon=12):
    """Counterfactual executable exit after a single modeled AI decision."""
    p=deepcopy(position)
    side=1 if p["direction"]=="long" else -1
    impact=(profile["slippage_bps"]+profile["assumed_spread_bps"])/10000
    spread=profile["assumed_spread_bps"]/10000
    fee=profile["taker_fee_bps"]/10000
    qty=p["quantity"]

    def result(px):
        exit_price=px*(1-side*impact)
        return side*(exit_price-p["entry_price"])*qty-p["entry_fee_usdc"]-exit_price*qty*fee

    if action==3:
        return result(rows[at]["o"])
    first=rows[at]["o"]
    quote_bid=first*(1-spread/2)
    quote_ask=first*(1+spread/2)
    if action in (4,5):
        tightened=tighten_stop(
            p,quote_bid,quote_ask,
            action="PROTECT" if action==4 else "TRAIL")
        if tightened is None:
            return None
        p=tightened
    for i,bar in enumerate(rows[at:at+horizon]):
        if i and p.get("ai_trailing_active"):
            bid=bar["o"]*(1-spread/2)
            ask=bar["o"]*(1+spread/2)
            new=maybe_follow_trailing(p,bid,ask)
            if new is not None:
                p=new
        stop=p["stop_price"]
        target=p["target_price"]
        stop_hit=(bar["l"]<=stop if side>0 else bar["h"]>=stop)
        tp_hit=(bar["h"]>=target if side>0 else bar["l"]<=target)
        # Conservatively assume stop first whenever intrabar path ambiguous.
        if stop_hit:
            executed=min(stop,bar["o"]) if side>0 else max(stop,bar["o"])
            return result(executed)
        if tp_hit:
            return result(target)
        p["audit_mfe_usdc"]=max(
            p.get("audit_mfe_usdc",0),
            max(0,(bar["h"]-p["entry_price"] if side>0
                   else p["entry_price"]-bar["l"])*qty))
    return result(rows[at+horizon-1]["c"])


def build_skills_curriculum(datasets,profile,*,flat_per_symbol=140,
                             manage_per_symbol=60,horizon=12,seed=31):
    """Balanced flat and open-position lessons from the TRAIN split only."""
    base=build_lessons(datasets,profile,max_per_symbol=flat_per_symbol,
                       horizon=horizon,seed=seed)
    lessons=[]
    for item in base:
        bars=datasets[item.symbol]
        context=bars[item.candle_index-ADVANCED_HISTORY_BARS:item.candle_index]
        features=list(item.context)+market_skill_features(context)
        lessons.append(SkillLesson(item.symbol,item.candle_index,
                                   tuple(features),item.action,
                                   "entry",_regime(features)))
    rng=random.Random(seed)
    for symbol,rows in sorted(datasets.items()):
        eligible=range(ADVANCED_HISTORY_BARS+17,len(rows)-horizon)
        if not eligible:
            continue
        # Cover earlier/later market cycles rather than only one short episode.
        count=min(manage_per_symbol,len(eligible))
        for k in range(count):
            left=eligible.start+(len(eligible)*k)//count
            right=eligible.start+(len(eligible)*(k+1))//count-1
            at=rng.randint(left,max(left,right))
            side=1 if (k%2)==0 else -1
            age=(3,6,12,16)[k%4]
            position=_historical_position(rows,at,side,age,profile)
            if position is None:
                continue
            past=rows[at-ADVANCED_HISTORY_BARS:at]
            price=past[-1]["c"]
            spread=profile["assumed_spread_bps"]/10000
            state,_=skills_observation(
                past,price*(1-spread/2),price*(1+spread/2),position)
            values={}
            for action in (0,3,4,5):
                outcome=_managed_net(rows,at,position,action,profile,
                                     horizon=horizon)
                if outcome is not None and math.isfinite(outcome):
                    values[action]=outcome
            if not values:
                continue
            best=max(values,key=values.get)
            # Discard tiny hindsight differences that are not worth teaching.
            # WAIT is the neutral management action when futures are ambiguous.
            if best!=0 and values[best]-values.get(0,-math.inf)<0.05:
                best=0
            lessons.append(SkillLesson(symbol,at,tuple(state),best,
                                       "position_management",_regime(state)))
    return lessons


def pretrain_skills_actor(model,lessons,*,epochs=3,batch_size=128,lr=5e-4,seed=31):
    """State-masked supervised bootstrap, critic reserved for PPO rollout.

    History-specific winners cannot be assumed to generalize. Approval must
    come from temporally disjoint, fee-adjusted holdout and forward DEMO.
    """
    if len(lessons)<60 or epochs<1 or batch_size<8:
        raise ValueError("insufficient_skills_examples")
    import numpy as np
    import torch
    import torch.nn.functional as F
    counts=Counter(item.action for item in lessons)
    domains=Counter(item.domain for item in lessons)
    if domains["entry"]<20 or domains["position_management"]<10:
        raise ValueError("insufficient_management_skill_coverage")
    xs=np.asarray([i.context for i in lessons],dtype=np.float32)
    ys=np.asarray([i.action for i in lessons],dtype=np.int64)
    in_position=np.asarray([i.domain=="position_management" for i in lessons],dtype=bool)
    allowed_flat=torch.tensor([True,True,True,False,False,False],device=model.device)
    allowed_open=torch.tensor([True,False,False,True,True,True],device=model.device)
    # Make rare behaviors learnable without fabricating positive rewards.
    weight=torch.tensor([
        min(5.0,math.sqrt(len(lessons)/max(counts.get(i,1)*6,1)))
        for i in range(6)],dtype=torch.float32,device=model.device)
    params=list(model.policy.mlp_extractor.policy_net.parameters())
    params+=list(model.policy.action_net.parameters())
    opt=torch.optim.Adam(params,lr=lr)
    rng=np.random.default_rng(seed)
    losses=[]
    for _ in range(epochs):
        for ids in np.array_split(rng.permutation(len(lessons)),
                                  max(1,math.ceil(len(lessons)/batch_size))):
            obs=torch.as_tensor(xs[ids],dtype=torch.float32,device=model.device)
            target=torch.as_tensor(ys[ids],dtype=torch.long,device=model.device)
            opened=torch.as_tensor(in_position[ids],dtype=torch.bool,device=model.device)
            allowed=torch.where(opened[:,None],allowed_open[None,:],allowed_flat[None,:])
            feature=model.policy.extract_features(obs)
            actor=model.policy.mlp_extractor.forward_actor(feature)
            logits=model.policy.action_net(actor)[:,:6].masked_fill(~allowed,-1e9)
            loss=F.cross_entropy(logits,target,weight=weight)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params,0.8)
            opt.step()
            losses.append(float(loss.detach().cpu()))
    return {
        "stage":"TRAIN_ONLY_MULTI_SKILL_SUPERVISION_THEN_MASKABLE_PPO",
        "lessons":len(lessons),
        "domains":dict(domains),
        "symbols":len({i.symbol for i in lessons}),
        "actions":{V4_ACTIONS[i]:counts.get(i,0) for i in range(6)},
        "regimes":dict(Counter(x.regime for x in lessons)),
        "epochs":epochs,
        "mean_loss":round(sum(losses)/len(losses),6),
        "teacher_outcomes":"TRAIN_ONLY_COST_ADJUSTED_COUNTERFACTUALS",
        "limitations":"OHLC intrabar ordering estimated; no verified positive edge or funding/order book features",
    }
