"""Research-only skill curriculum built from real, strictly earlier training bars.

Stage A: the actor sees a full M5/M15/H1 technical context and learns from
cost-aware counterfactual outcomes LONG/SHORT/WAIT. The teacher's future labels
are used ONLY on the training split, NEVER provided to inference or holdout.
Stage B: MaskablePPO explores entry/SL/TP/exit/protection from equity rewards.
This is a transparent *limited trading curriculum*, not preloaded expertise.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import random

from app.demo.ai_features import ADVANCED_HISTORY_BARS, advanced_observation, observation


@dataclass(frozen=True)
class Lesson:
    symbol: str
    candle_index: int
    context: tuple[float, ...]
    action: int  # 0 WAIT, 1 LONG, 2 SHORT
    long_net: float
    short_net: float
    regime: str


def _finite_positive(value):
    return math.isfinite(value) and value > 0


def trade_after_costs(rows: list[dict], at: int, side: int, *,
                      profile: dict, horizon: int = 12,
                      stop_atr: float = 1.2, target_atr: float = 2.0) -> float:
    """Counterfactual fill after decision at prior bar's close.

    Trades execute at next bar's OPEN, never at a future known close.
    A single OHLC bar touching both SL and TP resolves SL first.
    Exit includes side-dependent adverse spread/slippage and 2 taker fees.
    This is a fixed-horizon supervised label, NOT a forward price forecast.
    """
    if side not in (-1, 1) or not ADVANCED_HISTORY_BARS <= at or at+horizon > len(rows):
        raise ValueError("invalid_teacher_window")
    from app.demo.ai_features import observation as obs
    back = rows[at-40:at]
    _, atr = obs(back, back[-1]["c"]*0.99999, back[-1]["c"]*1.00001)
    margin = float(profile["margin_cap_usdc"])
    leverage = float(profile["leverage"])
    fee_rate = float(profile["taker_fee_bps"])/10000
    impact = (float(profile["slippage_bps"])+
              float(profile["assumed_spread_bps"]))/10000
    raw_entry = float(rows[at]["o"])
    entry = raw_entry*(1+side*impact)
    qty = margin*leverage/entry
    if not _finite_positive(qty):
        raise ValueError("invalid_teacher_notional")
    stop = entry-side*stop_atr*atr
    target = entry+side*target_atr*atr
    if min(stop,target) <= 0:
        raise ValueError("invalid_teacher_brackets")
    exit_raw = float(rows[at+horizon-1]["c"])
    for candle in rows[at:at+horizon]:
        opening = float(candle["o"])
        stop_hit = candle["l"]<=stop if side>0 else candle["h"]>=stop
        tp_hit = candle["h"]>=target if side>0 else candle["l"]<=target
        if stop_hit:
            exit_raw = min(stop,opening) if side>0 else max(stop,opening)
            break
        if tp_hit:
            exit_raw = target
            break
    exit_fill = exit_raw*(1-side*impact)
    gross = side*(exit_fill-entry)*qty
    return gross - (abs(entry*qty)+abs(exit_fill*qty))*fee_rate


def regime_name(features: tuple[float, ...]) -> str:
    from app.demo.ai_features import ADVANCED_OBS_NAMES
    m5 = features[ADVANCED_OBS_NAMES.index("m5_ema9_21_pct")]
    m15 = features[ADVANCED_OBS_NAMES.index("m15_ema9_21_pct")]
    h1 = features[ADVANCED_OBS_NAMES.index("h1_ema9_21_pct")]
    adx = features[ADVANCED_OBS_NAMES.index("m15_adx14")]
    if adx < -0.15:
        return "RANGE_OR_WEAK"
    if min(m5,m15,h1)>0.03:
        return "ALIGNED_BULL"
    if max(m5,m15,h1)<-0.03:
        return "ALIGNED_BEAR"
    return "MIXED_REGIME"


def build_lessons(datasets: dict[str,list[dict]], profile: dict, *,
                  max_per_symbol: int = 160, horizon: int = 12,
                  min_net_usdc: float = 0.06, seed: int = 29) -> list[Lesson]:
    """Deterministic symbol/time-balanced samples from TRAIN SPLIT ONLY.

    Draw indices from across the full training history, not just BTC/ETH/SOL
    or one random short segment. The last horizon bars are label-only and
    never included as features. Keep all training examples private/offline.
    """
    if max_per_symbol < 4 or horizon < 2 or min_net_usdc < 0:
        raise ValueError("invalid_curriculum_config")
    rng = random.Random(seed)
    lessons=[]
    for symbol, bars in sorted(datasets.items()):
        low=ADVANCED_HISTORY_BARS
        high=len(bars)-horizon
        if high<=low:
            raise ValueError("insufficient_curriculum_history: "+symbol)
        samples=min(max_per_symbol, high-low+1)
        # Stratified chronological coverage, reproducible across reruns.
        times=[]
        for slot in range(samples):
            lo=low+(high-low+1)*slot//samples
            hi=low+(high-low+1)*(slot+1)//samples-1
            times.append(rng.randint(lo,max(lo,hi)))
        for at in times:
            candle_context=bars[at-ADVANCED_HISTORY_BARS:at]
            last=float(candle_context[-1]["c"])
            features,_=advanced_observation(
                candle_context,last*0.99999,last*1.00001)
            long_net=trade_after_costs(bars,at,1,profile=profile,horizon=horizon)
            short_net=trade_after_costs(bars,at,-1,profile=profile,horizon=horizon)
            # WAIT if neither side has a genuinely net-positive outcome.
            action=(1 if long_net>=short_net else 2) if max(
                long_net,short_net)>min_net_usdc else 0
            lessons.append(Lesson(
                symbol,at,tuple(features),action,long_net,short_net,
                regime_name(tuple(features))))
    return lessons


def pretrain_actor(model, lessons: list[Lesson], *,
                   epochs: int = 3, batch_size: int = 128, lr: float = 5e-4,
                   seed: int = 29) -> dict:
    """Supervised bootstrap on training-only net-outcome examples.

    Cross entropy updates ONLY policy features and policy action head;
    critic stays reserved for PPO. This is not proof of predictive edge.
    """
    if len(lessons)<60 or epochs<1 or batch_size<8:
        raise ValueError("insufficient_curriculum_lessons")
    import numpy as np
    import torch
    import torch.nn.functional as F
    rng = np.random.default_rng(seed)
    counts=Counter(x.action for x in lessons)
    weights=torch.tensor(
        [min(4.0, math.sqrt(len(lessons)/max(counts.get(i,1)*3,1)))
         for i in range(3)],dtype=torch.float32,device=model.device)
    xs=np.asarray([x.context for x in lessons],dtype=np.float32)
    ys=np.asarray([x.action for x in lessons],dtype=np.int64)
    params=list(model.policy.mlp_extractor.policy_net.parameters())
    params+=list(model.policy.action_net.parameters())
    opt=torch.optim.Adam(params,lr=lr)
    losses=[]
    for _ in range(epochs):
        for indexes in np.array_split(rng.permutation(len(lessons)),
                                       max(1,math.ceil(len(lessons)/batch_size))):
            obs=torch.as_tensor(xs[indexes],dtype=torch.float32,device=model.device)
            targets=torch.as_tensor(ys[indexes],dtype=torch.long,device=model.device)
            features=model.policy.extract_features(obs)
            latent=model.policy.mlp_extractor.forward_actor(features)
            logits=model.policy.action_net(latent)[:,:6]
            loss=F.cross_entropy(logits[:,:3],targets,weight=weights)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params,0.75)
            opt.step()
            losses.append(float(loss.detach().cpu()))
    return {
        "stage":"TRAIN_ONLY_SUPERVISED_BOOTSTRAP_THEN_MASKABLE_PPO",
        "lessons":len(lessons),"symbols":len(set(x.symbol for x in lessons)),
        "actions":{str(i):counts.get(i,0) for i in (0,1,2)},
        "regimes":dict(Counter(x.regime for x in lessons)),
        "epochs":epochs,
        "mean_loss":round(sum(losses)/len(losses),6),
        "teacher_horizon_m5_bars":12,
        "note":"historical counterfactual labels from TRAIN ONLY; no predictive edge implied",
    }
