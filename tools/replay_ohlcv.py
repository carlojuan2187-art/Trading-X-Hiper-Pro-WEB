#!/usr/bin/env python3
"""Offline OHLCV strategy replay. Never imports an exchange client or sends orders.

Input: CSV files BTC.csv, SOL.csv, etc, with t,o,h,l,c,v; t is UTC open time in
milliseconds. All rows must be consecutive 5m candles. BTC.csv is mandatory.
No scanner liquidity/order-book simulation. Outputs are research, not live PnL.
"""
import argparse
import bisect
from collections import Counter
import contextlib
import csv
import io
import json
import math
import os
from pathlib import Path
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STEP = 300_000


def load_csv(path):
    with path.open(newline='') as stream:
        rows = [{key: (int(row[key]) if key == 't' else float(row[key]))
                 for key in ('t', 'o', 'h', 'l', 'c', 'v')} for row in csv.DictReader(stream)]
    for i, row in enumerate(rows):
        if row['t'] % STEP or (i and row['t'] - rows[i-1]['t'] != STEP):
            raise ValueError(f'{path}: timestamps must be consecutive UTC 5m opens')
        if not all(math.isfinite(row[k]) for k in ('o', 'h', 'l', 'c', 'v')):
            raise ValueError(f'{path}: nonfinite values')
        if min(row[k] for k in ('o', 'h', 'l', 'c')) <= 0 or row['v'] < 0:
            raise ValueError(f'{path}: invalid prices or volume')
        if row['l'] > min(row['o'], row['c']) or row['h'] < max(row['o'], row['c']):
            raise ValueError(f'{path}: invalid OHLC geometry')
        row['T'] = row['t'] + STEP
    return rows


def aggregate(rows, multiplier):
    width = STEP * multiplier
    buckets = {}
    for row in rows:
        buckets.setdefault(row['t'] // width * width, []).append(row)
    result = []
    for start, group in buckets.items():
        if len(group) != multiplier or group[0]['t'] != start:
            continue
        result.append(dict(t=start, T=start+width, o=group[0]['o'],
                           h=max(x['h'] for x in group), l=min(x['l'] for x in group),
                           c=group[-1]['c'], v=sum(x['v'] for x in group)))
    return result


def exit_on_bar(position, bar, slippage):
    long = position['direction'] == 'long'
    stop, target = position['stop_price'], position['target_price']
    stop_hit = bar['l'] <= stop if long else bar['h'] >= stop
    target_hit = bar['h'] >= target if long else bar['l'] <= target
    # Conservative intrabar ambiguity: if both are touched, stop wins.
    if stop_hit:
        price = min(stop, bar['o']) if long else max(stop, bar['o'])
        return price * (1-slippage if long else 1+slippage), 'SL'
    if target_hit:
        return target * (1-slippage if long else 1+slippage), 'TP'
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv-dir', type=Path, required=True)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--strategy', choices=['router', 'mtf_simple', 'liquidity_sweep_reversal', 'range_mean_reversion'], default='router')
    parser.add_argument('--fee-bps', type=float, required=True, help='Assumed fee per side, not inferred from exchange tier')
    parser.add_argument('--slippage-bps', type=float, required=True)
    parser.add_argument('--platform-fee-rate', type=float, default=0.0, help='Fraction charged on each positive realized trade, e.g. 0.15; excludes funding')
    parser.add_argument('--start-ms', type=int, default=0)
    parser.add_argument('--end-ms', type=int, default=2**63-1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not all(math.isfinite(x) and 0 <= x < 10000 for x in [args.fee_bps, args.slippage_bps]):
        parser.error('Costs must be finite basis points between 0 and 10000')
    if not math.isfinite(args.platform_fee_rate) or not 0 <= args.platform_fee_rate < 1:
        parser.error('platform-fee-rate must be finite and in [0, 1)')
    if args.start_ms >= args.end_ms:
        parser.error('start-ms must precede end-ms')
    symbol = args.symbol.upper()
    if not symbol.isalnum():
        parser.error('symbol must be alphanumeric')
    # Replace the adapter BEFORE importing strategy modules: network calls are impossible.
    adapter = types.ModuleType('app.hyperliquid_client')
    def forbidden(*a, **kw):
        raise RuntimeError('Network/exchange disabled in offline replay')
    adapter.make_request = forbidden
    adapter.norm_coin = lambda value: str(value).upper().removesuffix('-PERP')
    sys.modules['app.hyperliquid_client'] = adapter
    for key, value in {'TELEGRAM_BOT_TOKEN':'offline', 'ADMIN_TELEGRAM_ID':'1', 'MONGO_URL':'mongodb://offline.invalid', 'MARKET_CONTEXT_CACHE_TTL_SECONDS':'0'}.items():
        os.environ[key] = value
    from app import market_context as mc
    from app.strategies.router import StrategyRouter
    from app.strategies.registry import get_strategy_registry
    from app.trading_quality import absolute_risk_reward, evaluate_entry_drift, adaptive_entry_drift_limit, meets_min_rr
    from app.config import MIN_ENTRY_RR, ENTRY_MAX_DRIFT_PCT, ENTRY_MAX_DRIFT_ATR_MULT, ENTRY_MAX_DRIFT_HARD_PCT, ENTRY_MAX_ADVERSE_DRIFT_MULT
    data = {}
    for coin in {symbol, 'BTC'}:
        rows = load_csv(args.csv_dir / f'{coin}.csv')
        for tf, multiplier in [('5m',1),('15m',3),('1h',12)]:
            series = rows if multiplier == 1 else aggregate(rows, multiplier)
            data[(coin, tf)] = (series, [x['T'] for x in series])
    now_ms = 0
    def fetch(coin, interval, limit):
        rows, closes = data.get((adapter.norm_coin(coin), interval), ([], []))
        n = bisect.bisect_right(closes, now_ms)
        return rows[max(0,n-limit):n], 'OK' if n else 'NO_CANDLES'
    # Breakout imported the original function by alias; patch both references.
    import app.strategies.breakout_reset as mtf
    router = StrategyRouter()
    strategy = None if args.strategy == 'router' else get_strategy_registry().get(args.strategy)
    candles = data[(symbol,'5m')][0]
    fee = args.fee_bps / 10000
    slip = args.slippage_bps / 10000
    rejected = Counter(); trades = []; position = None; pending = None
    for i, bar in enumerate(candles):
        if bar['t'] < args.start_ms or bar['T'] > args.end_ms:
            continue
        now_ms = bar['T']
        if pending and position is None:
            entry = bar['o'] * (1+slip if pending['direction']=='long' else 1-slip)
            plan = absolute_risk_reward(direction=pending['direction'], entry_price=entry,
                stop_price=pending['stop_price'], target_price=pending['target_price'])
            limit = adaptive_entry_drift_limit(atr_pct=pending.get('atr_pct',0), base_pct=ENTRY_MAX_DRIFT_PCT,
                atr_mult=ENTRY_MAX_DRIFT_ATR_MULT, hard_pct=ENTRY_MAX_DRIFT_HARD_PCT)
            drift = evaluate_entry_drift(direction=pending['direction'], signal_price=pending['signal_reference_price'],
                current_price=entry, allowed_pct=limit, adverse_mult=ENTRY_MAX_ADVERSE_DRIFT_MULT)
            if plan.get('ok') and meets_min_rr(plan['rr'],MIN_ENTRY_RR) and drift['ok']:
                position = {**pending, 'entry':entry, 'entry_t':bar['t'], 'risk_pct':plan['risk_pct']}
            else:
                rejected['NEXT_OPEN_ENTRY_INVALID'] += 1
            pending = None
        if position:
            exit_result = exit_on_bar(position,bar,slip)
            if exit_result:
                price, reason = exit_result
                sign = 1 if position['direction']=='long' else -1
                net = sign*(price-position['entry'])/position['entry'] - fee*(1+price/position['entry'])
                platform_fee = max(net, 0.0) * args.platform_fee_rate
                user_net = net - platform_fee
                trades.append(dict(strategy_id=position.get('strategy_id'), regime_id=position.get('regime_id'), entry_t=position['entry_t'], exit_t=bar['T'], direction=position['direction'],
                    entry=position['entry'], exit=price, reason=reason, trading_net_return=net, platform_fee_return=platform_fee, net_return=user_net, net_r=user_net/position['risk_pct']))
                position = None
        # Full warmup for H1 EMA200 and other contexts; no fabricated history.
        if i < 12*240 or position is not None:
            continue
        with patch.object(mc,'fetch_candles',fetch), patch.object(mtf,'fetch_closed_candles',fetch), patch('time.time',return_value=now_ms/1000+2), contextlib.redirect_stdout(io.StringIO()):
            ctx = mc.build_market_context(symbol)
            btc = mc.build_market_context('BTC')
            signal = router.route_symbol(symbol,ctx,btc) if strategy is None else strategy.evaluate(symbol,market_context=ctx)
        if signal.get('signal'):
            pending = signal
        else:
            rejected[signal.get('reason','UNKNOWN')] += 1
    wins = sum(max(t['net_r'],0) for t in trades)
    losses = sum(max(-t['net_r'],0) for t in trades)
    equity_r = peak = drawdown_r = 0
    for trade in trades:
        equity_r += trade['net_r']; peak=max(peak,equity_r); drawdown_r=max(drawdown_r,peak-equity_r)
    result = {'symbol':symbol,'strategy':args.strategy,'trades':trades,'rejections':dict(rejected),
              'closed_trades':len(trades),'win_rate':sum(t['net_r']>0 for t in trades)/len(trades) if trades else None,
              'profit_factor_r':wins/losses if losses else None,'net_r':equity_r,'max_drawdown_r':drawdown_r,
              'average_win_r':wins/sum(t['net_r']>0 for t in trades) if wins else None,
              'average_loss_r':losses/sum(t['net_r']<0 for t in trades) if losses else None,
              'expectancy_r':equity_r/len(trades) if trades else None,
              'platform_fee_return_sum':sum(t['platform_fee_return'] for t in trades),
              'open_position_at_end':position is not None,
              'assumptions':{'fee_bps_per_side':args.fee_bps,'slippage_bps_per_side':args.slippage_bps,
                'entry':'next candle open','both_levels_same_bar':'stop first','funding_included':False, 'platform_fee_rate':args.platform_fee_rate,
                'scanner_liquidity_simulated':False,'risk_governor_simulated':False,
                'warning':'Strategy-only OHLC replay, not production execution or portfolio validation'}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,allow_nan=False))
    print(json.dumps({k:v for k,v in result.items() if k not in {'trades','rejections'}},indent=2))


if __name__ == '__main__':
    main()
