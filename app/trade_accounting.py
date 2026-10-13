"""Strict accounting for one isolated perpetual position, using exchange fills.

No synthetic PnL. Reject incomplete/ambiguous lifecycles instead of billing a
partial close. Funding is separate from fills and is explicitly not included.
"""
import math


def summarize_trade_fills(raw, *, coin, opened_after_ms, expected_qty,
                          direction, expected_order_id=None):
    def fail(reason):
        return {'fills': 0, 'reason': reason, 'source': 'exchange_trade_lifecycle',
                'funding_included': False}
    if not isinstance(raw, list):
        return fail('FILLS_UNAVAILABLE')
    if len(raw) >= 2000:
        return fail('FILLS_PAGE_LIMIT_REQUIRES_RECONCILIATION')
    try:
        qty = float(expected_qty)
        if direction not in {'long', 'short'} or not math.isfinite(qty) or qty <= 0:
            return fail('INVALID_TRADE_IDENTITY')
        rows = []
        seen = {}
        for item in raw:
            if not isinstance(item, dict):
                return fail('INVALID_FILL')
            if str(item.get('coin') or '').upper() != coin.upper():
                continue
            ts = int(item['time'])
            if ts < opened_after_ms:
                continue
            tid = item.get('tid')
            if tid is not None:
                if tid in seen:
                    if seen[tid] != item:
                        return fail('CONFLICTING_FILL_ID')
                    continue
                seen[tid] = item
            kind = str(item.get('dir') or '').lower()
            if kind not in {'open ' + direction, 'close ' + direction}:
                return fail('AMBIGUOUS_POSITION_LIFECYCLE')
            px, size, pnl, fee = (float(item[k]) for k in ('px', 'sz', 'closedPnl', 'fee'))
            if not all(math.isfinite(x) for x in (px, size, pnl, fee)) or min(px, size) <= 0:
                return fail('INVALID_FILL_VALUES')
            if str(item.get('feeToken') or '').strip().upper() != 'USDC':
                return fail('UNSUPPORTED_FEE_CURRENCY')
            rows.append(dict(ts=ts, kind=kind, px=px, size=size, pnl=pnl, fee=fee,
                             oid=str(item.get('oid') or ''), start=item.get('startPosition'), tid=tid))
        # Same-millisecond fills are ordered by execution tid where supplied.
        rows.sort(key=lambda x: (x['ts'], int(x['tid']) if x['tid'] is not None else 0))
        tolerance = max(1e-10, qty * 1e-7)
        balance = opened = closed = gross = entry_fees = exit_fees = px_qty = 0.0
        closes = []
        closing_started = flat = False
        for row in rows:
            if flat:
                return fail('MULTIPLE_POSITION_LIFECYCLES')
            if row['start'] is not None:
                signed = float(row['start'])
                if not math.isfinite(signed) or abs(signed - (balance if direction == 'long' else -balance)) > tolerance:
                    return fail('POSITION_HISTORY_MISMATCH')
            if row['kind'].startswith('open'):
                if closing_started or abs(row['pnl']) > 1e-10:
                    return fail('AMBIGUOUS_POSITION_LIFECYCLE')
                opened += row['size']; balance += row['size']; entry_fees += row['fee']
            else:
                closing_started = True
                if row['size'] > balance + tolerance:
                    return fail('MISSING_OPEN_FILLS')
                closed += row['size']; balance -= row['size']
                gross += row['pnl']; exit_fees += row['fee']
                px_qty += row['px'] * row['size']; closes.append(row)
                flat = abs(balance) <= tolerance
        if not closes or not flat or abs(opened-qty) > tolerance or abs(closed-qty) > tolerance:
            return fail('INCOMPLETE_TRADE_FILLS')
        if expected_order_id and str(expected_order_id) not in {r['oid'] for r in closes}:
            return fail('EXPECTED_CLOSE_ORDER_MISSING')
        return dict(gross_pnl=gross, fees=entry_fees+exit_fees,
                    entry_fees=entry_fees, exit_fees=exit_fees,
                    net_pnl=gross-entry_fees-exit_fees, fills=len(closes),
                    entry_fills=len(rows)-len(closes), exit_price=px_qty/closed,
                    closed_qty=closed, close_time_ms=closes[-1]['ts'],
                    close_order_id=closes[-1]['oid'],
                    close_order_ids=list(dict.fromkeys(r['oid'] for r in closes)),
                    source='exchange_trade_lifecycle', funding_included=False)
    except (KeyError, TypeError, ValueError, OverflowError):
        return fail('INVALID_FILL_SCHEMA')
