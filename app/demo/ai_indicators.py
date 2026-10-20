"""Pure, look-ahead-free M5/M15/H1 indicator observations for PPO v3.

All timeframes are built from *fully closed* real M5 candles only. No fake
candles, no price prediction and no broker secrets. Not used by old v2 actors.
"""
from __future__ import annotations

import math

BAR_MS = 300_000
HISTORY_BARS = 480
INDICATOR_NAMES = (
    "m5_ema9_21_pct", "m5_rsi14", "m5_adx14", "m5_di_delta",
    "m5_macd_hist_pct", "m5_bb_z", "m5_volume_ratio",
    "m15_ema9_21_pct", "m15_rsi14", "m15_adx14", "m15_di_delta",
    "m15_macd_hist_pct", "m15_bb_z",
    "h1_ema9_21_pct", "h1_rsi14", "h1_adx14", "h1_di_delta",
    "h1_macd_hist_pct", "h1_bb_z",
)


def _finite_clamp(value, limit=3.0):
    if not math.isfinite(value):
        raise ValueError("nonfinite_indicator")
    return max(-limit, min(limit, value))


def ema(values, span):
    if len(values) < span or span < 1:
        raise ValueError("indicator_history_short")
    result = sum(values[:span]) / span
    k = 2.0 / (span + 1)
    for value in values[span:]:
        result += k * (value - result)
    return result


def ema_series(values, span):
    if len(values) < span:
        raise ValueError("indicator_history_short")
    current = sum(values[:span]) / span
    result = [current]
    for v in values[span:]:
        current += (2.0 / (span+1)) * (v-current)
        result.append(current)
    return result


def rsi(closes, period=14):
    if len(closes) <= period:
        raise ValueError("rsi_history_short")
    moves = [b-a for a, b in zip(closes, closes[1:])]
    gains = [max(0.0, x) for x in moves]
    losses = [max(0.0, -x) for x in moves]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain += (gain-avg_gain) / period
        avg_loss += (loss-avg_loss) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    return 100.0 if avg_loss == 0 else 100.0 - 100.0/(1.0+avg_gain/avg_loss)


def adx(bars, period=14):
    """Wilder +DI, -DI, ADX; requires 2*period+1 completed bars."""
    if len(bars) < period*2+1:
        raise ValueError("adx_history_short")
    tr, plus, minus = [], [], []
    for previous, now in zip(bars, bars[1:]):
        up, down = now["h"]-previous["h"], previous["l"]-now["l"]
        tr.append(max(now["h"]-now["l"], abs(now["h"]-previous["c"]),
                      abs(now["l"]-previous["c"])))
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    p_tr = sum(tr[:period])
    p_plus = sum(plus[:period])
    p_minus = sum(minus[:period])
    dx_series = []
    for i in range(period-1, len(tr)):
        if i >= period:
            p_tr += tr[i] - p_tr / period
            p_plus += plus[i] - p_plus / period
            p_minus += minus[i] - p_minus / period
        pdi = 100 * p_plus/max(p_tr, 1e-12)
        mdi = 100 * p_minus/max(p_tr, 1e-12)
        dx_series.append(100*abs(pdi-mdi)/max(pdi+mdi, 1e-12))
    current = sum(dx_series[:period])/period
    for dx in dx_series[period:]:
        current += (dx-current)/period
    return current, pdi, mdi


def aggregate(bars, minutes):
    if minutes == 5:
        return list(bars)
    if minutes not in (15, 60):
        raise ValueError("unsupported_timeframe")
    step = minutes // 5
    interval = BAR_MS*step
    result = []
    current = []
    group_start = None
    for b in bars:
        t = int(b["t"])
        target = t//interval*interval
        if group_start is not None and target != group_start:
            if len(current) == step and int(current[0]["t"]) == group_start:
                result.append({
                    "t": group_start, "o": current[0]["o"],
                    "h": max(r["h"] for r in current),
                    "l": min(r["l"] for r in current),
                    "c": current[-1]["c"], "v": sum(r["v"] for r in current),
                })
            current = []
        group_start = target
        current.append(b)
    if current and len(current)==step and int(current[0]["t"])==group_start:
        result.append({
            "t": group_start, "o": current[0]["o"], "h": max(r["h"] for r in current),
            "l": min(r["l"] for r in current), "c": current[-1]["c"],
            "v": sum(r["v"] for r in current),
        })
    return result


def timeframe_indicators(bars):
    if len(bars) < 36:
        raise ValueError("indicator_minimum_history")
    closes = [b["c"] for b in bars]
    last = closes[-1]
    ema9, ema21 = ema(closes, 9), ema(closes, 21)
    r = rsi(closes)
    a, pdi, mdi = adx(bars)
    macd_line = [fast-slow for fast, slow in zip(
        ema_series(closes, 12)[14:], ema_series(closes, 26))]
    if len(macd_line) < 9:
        raise ValueError("macd_history_short")
    histogram = macd_line[-1]-ema(macd_line, 9)
    sample = closes[-20:]
    middle = sum(sample)/20
    sigma = math.sqrt(sum((x-middle)**2 for x in sample)/20)
    bb_z = (last-middle)/max(2*sigma, last*1e-9)
    volume = [b["v"] for b in bars]
    vol_ratio = volume[-1]/max(sum(volume[-21:-1])/20, 1e-9)-1
    return [
        _finite_clamp((ema9/ema21-1)*100),
        _finite_clamp((r-50)/25),
        _finite_clamp((a-25)/25),
        _finite_clamp((pdi-mdi)/40),
        _finite_clamp(histogram/last*100),
        _finite_clamp(bb_z),
        _finite_clamp(vol_ratio),
    ]


def multi_timeframe_features(bars):
    if len(bars) < HISTORY_BARS:
        raise ValueError("multi_timeframe_history_short")
    bars = bars[-HISTORY_BARS:]
    if any(int(b["t"])-int(a["t"])!=BAR_MS for a,b in zip(bars,bars[1:])):
        raise ValueError("multi_timeframe_history_gap")
    values=[]
    for period in (5,15,60):
        series = aggregate(bars, period)
        if period == 60 and len(series) < 36:
            raise ValueError("h1_not_enough_closed_bars")
        outputs = timeframe_indicators(series)
        values.extend(outputs if period==5 else outputs[:6])
    if len(values) != len(INDICATOR_NAMES):
        raise AssertionError("indicator_feature_contract")
    return values
