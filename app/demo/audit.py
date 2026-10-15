"""Read-only, bounded historical DEMO trade replay for diagnostics.

The public Hyperliquid 1-minute OHLCV feed cannot establish intrabar hit order
and must never be passed off as tick-level MFE/MAE. No database updates.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from app.market.terminal import canonical_coin, normalize_candle

logger = logging.getLogger(__name__)


def _ms(value: str) -> int:
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def analyze_1m_trade(trade: dict, candles: list[dict]) -> dict:
    """Candle-range gross excursions only, without synthetic bid/ask or fills."""
    entry = float(trade['entry_price'])
    sl = float(trade['stop_price'])
    tp = float(trade['target_price'])
    side = str(trade['direction']).lower()
    opened = _ms(trade['opened_at'])
    closed = _ms(trade['closed_at'])
    if side not in {'long', 'short'} or min(entry, sl, tp) <= 0 or opened >= closed:
        raise ValueError('Trade geometry invalid')
    observed = sorted((c for c in candles
                       if opened < int(c['timestamp']) + 60000
                       and int(c['timestamp']) <= closed), key=lambda x: x['timestamp'])
    if not observed:
        return {'status': 'NO_1M_CANDLES', 'candles': 0}
    max_up = max(float(c['high']) for c in observed)
    min_down = min(float(c['low']) for c in observed)
    if side == 'long':
        favorable, adverse = max_up / entry - 1, min_down / entry - 1
    else:
        favorable, adverse = 1 - min_down / entry, 1 - max_up / entry
    first = 'NONE'
    first_bar = None
    for c in observed:
        touched_sl = float(c['low']) <= sl if side == 'long' else float(c['high']) >= sl
        touched_tp = float(c['high']) >= tp if side == 'long' else float(c['low']) <= tp
        if touched_sl or touched_tp:
            first = 'BOTH_UNORDERED' if touched_sl and touched_tp else 'SL' if touched_sl else 'TP'
            first_bar = int(c['timestamp'])
            break
    # The first and last 1m bars may extend outside the lifetime of the trade.
    # Even a continuous series is NOT sufficient to infer intraminute order.
    expected_minutes = max(1, math.ceil((closed - opened) / 60000))
    missing_bars = max(0, expected_minutes - len(observed) - 1)
    return {
        'status': 'APPROX_1M_NOT_TICK', 'candles': len(observed),
        'missing_minute_bars_lower_bound': missing_bars,
        'mfe_price_pct_approx': round(max(0, favorable) * 100, 4),
        'mae_price_pct_approx': round(min(0, adverse) * 100, 4),
        'first_sl_tp_touch_1m': first, 'first_touch_minute_utc_ms': first_bar,
        'entry_minute_and_exit_minute_partial': True,
        'can_determine_intraminute_touch_order': False,
    }


def replay_recent_history_to_logs(*, limit: int = 5, request=None, collection=None) -> int:
    """Fetch at most 5 retained closes from Mongo and 1 public REST call each."""
    from app.demo import ledger
    from app.hyperliquid_client import make_request
    db = collection if collection is not None else ledger.COLLECTION
    call = request if request is not None else make_request
    # Projection avoids copying secrets or credentials into RAM/logs.
    rows = db.find({}, {'history': 1, '_id': 1})
    candidates = []
    for row in rows:
        for trade in (row.get('history') or []):
            if trade.get('opened_at') and trade.get('closed_at'):
                candidates.append((str(trade['closed_at']), int(row['_id']), trade))
    candidates.sort(reverse=True, key=lambda x: x[0])
    done = 0
    for _, uid, trade in candidates[:max(0, min(5, int(limit)))]:
        symbol = str(trade.get('symbol') or '')
        result = {'symbol': symbol, 'direction': trade.get('direction'),
                  'strategy_id': trade.get('strategy_id'),
                  'opened_at': trade['opened_at'], 'closed_at': trade['closed_at'],
                  'entry_price': trade.get('entry_price'),
                  'exit_price': trade.get('exit_price'),
                  'stop_price': trade.get('stop_price'),
                  'target_price': trade.get('target_price'),
                  'rr': trade.get('rr'),
                  'pnl_net_usdc': trade.get('pnl_net_usdc'),
                  'pnl_gross_usdc': trade.get('pnl_gross_usdc'),
                  'entry_fee_usdc': trade.get('entry_fee_usdc'),
                  'exit_fee_usdc': trade.get('exit_fee_usdc')}
        try:
            start = max(0, _ms(trade['opened_at']) - 60000)
            end = _ms(trade['closed_at']) + 60000
            # The 1m candle API is public. Never use trading credentials.
            if end <= start or end - start > 18 * 3600000:
                result['replay'] = {'status': 'DURATION_OUT_OF_BOUNDS'}
            else:
                raw = call('/info', {'type': 'candleSnapshot', 'req': {
                    'coin': canonical_coin(symbol), 'interval': '1m',
                    'startTime': start, 'endTime': end,
                }}, retries=1, timeout=6)
                if not isinstance(raw, list):
                    result['replay'] = {'status': 'PUBLIC_CANDLE_FETCH_FAILED'}
                else:
                    candles = [clean for item in raw if (clean := normalize_candle(item))]
                    result['replay'] = analyze_1m_trade(trade, candles)
        except (TypeError, ValueError, OverflowError, KeyError) as exc:
            result['replay'] = {'status': 'REPLAY_FAILED', 'reason': str(exc)[:100]}
        except Exception:
            logger.exception('DEMO_HISTORICAL_REPLAY_ERROR user=%s symbol=%s', uid, symbol)
            result['replay'] = {'status': 'FETCH_EXCEPTION'}
        logger.info('DEMO_HISTORICAL_REPLAY user=%s data=%s',
                    uid, json.dumps(result, sort_keys=True, default=str))
        done += 1
    logger.info('DEMO_HISTORICAL_REPLAY_FINISHED trades=%d no_ledger_changes=true', done)
    return done
