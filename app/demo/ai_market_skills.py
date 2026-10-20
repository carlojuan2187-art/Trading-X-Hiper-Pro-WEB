"""Structural market-context features for PAPER-ONLY trading-skills research.

All observations use 480 contiguous CLOSED M5 candles, aggregated to completed
M15/H1 bars. Never infer future highs/lows, fill absent data or call an exchange.
"""
from __future__ import annotations
import math

from app.demo.ai_indicators import aggregate, ema, HISTORY_BARS

SKILL_NAMES = (
    "m5_range_location", "m5_resistance_atr", "m5_support_atr",
    "m5_breakout_up_atr", "m5_breakout_down_atr",
    "m5_upper_rejection_atr", "m5_lower_rejection_atr",
    "m5_market_structure", "m5_momentum_shift", "m5_volatility_expansion",
    "m5_volume_price_confirmation",
    "m15_range_location", "m15_market_structure", "m15_rejection",
    "h1_range_location", "h1_market_structure", "h1_trend_slope",
    "mtf_structure_alignment",
)


def _clip(x):
    if not math.isfinite(float(x)):
        raise ValueError("invalid_market_skill")
    return max(-3.0, min(3.0, float(x)))


def _true_range(bars):
    return [
        max(float(bars[i]["h"])-float(bars[i]["l"]),
            abs(float(bars[i]["h"])-float(bars[i-1]["c"])),
            abs(float(bars[i]["l"])-float(bars[i-1]["c"])))
        for i in range(1, len(bars))
    ]


def _structure(bars):
    """Direction of RECENT closed swing ranges, not oracle pivots."""
    older=bars[-18:-9]
    recent=bars[-9:]
    if len(older)!=9 or len(recent)!=9:
        raise ValueError("not_enough_structure_bars")
    scale=max(sum(_true_range(bars)[-14:])/14,1e-12)
    hh=(max(x["h"] for x in recent)-max(x["h"] for x in older))/scale
    hl=(min(x["l"] for x in recent)-min(x["l"] for x in older))/scale
    # HH+HL -> positive; LH+LL -> negative; disagreement -> weaker
    return _clip((hh+hl)/4)


def _range_location(bars, lookback=20):
    prior=bars[-lookback-1:-1]
    if len(prior)!=lookback:
        raise ValueError("not_enough_range_bars")
    high=max(float(x["h"]) for x in prior)
    low=min(float(x["l"]) for x in prior)
    close=float(bars[-1]["c"])
    return _clip(2*(close-low)/max(high-low,1e-12)-1)


def market_skill_features(candles):
    """18 bounded features, invariant to a market's absolute price scale."""
    if len(candles) < HISTORY_BARS:
        raise ValueError("market_skills_need_480_closed_m5_bars")
    bars=candles[-HISTORY_BARS:]
    if any(int(b["t"])-int(a["t"]) != 300000
           for a,b in zip(bars,bars[1:])):
        raise ValueError("market_skills_contiguous_m5_required")
    if any(not all(math.isfinite(float(b[k])) for k in ("o","h","l","c","v"))
           or min(float(b[k]) for k in ("o","h","l","c"))<=0
           or float(b["h"])<max(float(b["o"]),float(b["c"]))
           or float(b["l"])>min(float(b["o"]),float(b["c"]))
           or float(b["v"])<0 for b in bars):
        raise ValueError("invalid_market_skill_candle")
    m15=aggregate(bars,15)
    h1=aggregate(bars,60)
    if len(m15)<36 or len(h1)<36:
        raise ValueError("market_skills_incomplete_higher_timeframe")
    tr=_true_range(bars)
    atr=max(sum(tr[-14:])/14,1e-12)
    prior=bars[-21:-1]
    prior_hi=max(b["h"] for b in prior)
    prior_lo=min(b["l"] for b in prior)
    last=bars[-1]
    body=last["c"]-last["o"]
    upper=last["h"]-max(last["c"],last["o"])
    lower=min(last["c"],last["o"])-last["l"]
    last_changes=[bars[-i]["c"]-bars[-i-1]["c"] for i in range(1,13)]
    fast_momentum=sum(last_changes[:3])/atr
    slow_momentum=sum(last_changes[3:])/max(3*atr,1e-12)
    recent_vol=sum(tr[-14:])/14
    prior_vol=sum(tr[-42:-14])/28
    old_vol=sum(b["v"] for b in bars[-21:-1])/20
    close_vol=last["v"]/max(old_vol,1e-12)
    structure5=_structure(bars)
    structure15=_structure(m15)
    structure60=_structure(h1)
    ema_h1=ema([x["c"] for x in h1[-28:]],21)
    prev_ema_h1=ema([x["c"] for x in h1[-29:-1]],21)
    slope=(ema_h1/prev_ema_h1-1)*100 if prev_ema_h1>0 else 0
    values=[
        _range_location(bars),
        (prior_hi-last["c"])/atr,
        (last["c"]-prior_lo)/atr,
        max(0,last["c"]-prior_hi)/atr,
        max(0,prior_lo-last["c"])/atr,
        upper/atr,
        lower/atr,
        structure5,
        fast_momentum-slow_momentum,
        recent_vol/max(prior_vol,1e-12)-1,
        body/max(atr,1e-12)*min(close_vol,3),
        _range_location(m15),
        structure15,
        (m15[-1]["h"]-max(m15[-1]["o"],m15[-1]["c"]) -
         (min(m15[-1]["o"],m15[-1]["c"])-m15[-1]["l"]))
         /max(sum(_true_range(m15)[-14:])/14,1e-12),
        _range_location(h1),
        structure60,
        slope,
        (structure5+structure15+structure60)/3,
    ]
    if len(values)!=len(SKILL_NAMES):
        raise AssertionError("market_skill_schema_mismatch")
    return [_clip(x) for x in values]
