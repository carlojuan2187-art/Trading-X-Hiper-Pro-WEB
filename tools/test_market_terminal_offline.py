"""Pruebas reales del adaptador Hyperliquid y marcas, sin red ni Mongo.
python tools/test_market_terminal_offline.py
"""
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Aisla el módulo del cliente live; es obligatorio que el gráfico no lo use para órdenes.
fake_market = types.ModuleType('app.hyperliquid_client')
fake_market.make_request = lambda *args, **kwargs: {}
sys.modules['app.hyperliquid_client'] = fake_market
from app.market import terminal
from app.market.overlays import project, project_account, when


class FakeHyperliquid:
    def __init__(self):
        self.queries = []

    def __call__(self, path, body, **kwargs):
        self.queries.append(body)
        if body['type'] == 'meta':
            return {'universe': [
                {'name': 'BTC', 'szDecimals': 5}, {'name': 'ETH', 'szDecimals': 4},
                {'name': 'DEAD', 'isDelisted': True}, {'name': '../bad'},
            ]}
        if body['type'] == 'candleSnapshot':
            return [
                {'t': 1000, 'o': '20', 'h': '22', 'l': '19', 'c': '21', 'v': '100'},
                {'t': 2000, 'o': '21', 'h': '23', 'l': '20', 'c': '22', 'v': '101'},
                {'t': 3000, 'o': '21', 'h': '22', 'l': '25', 'c': '21.5', 'v': '99'},
            ]
        if body['type'] == 'l2Book':
            return {'time': 2000, 'levels': [[{'px': '10', 'sz': '5'}], [{'px': '11', 'sz': '3'}]]}
        raise AssertionError('El gráfico no debe ejecutar órdenes')


class MarketTests(unittest.TestCase):
    def setUp(self):
        terminal._catalog.update(at=0., rows=[])
        self.source = FakeHyperliquid()

    def test_symbol_whitelist_and_invalid_symbol(self):
        self.assertEqual(terminal.canonical_coin('btc-usdc'), 'BTC')
        self.assertEqual(terminal.validate_coin('BTC-USDC', self.source), 'BTC')
        with self.assertRaises(terminal.MarketError):
            terminal.validate_coin('DEAD', self.source)
        with self.assertRaises(terminal.MarketError):
            terminal.validate_coin('../etc/passwd', self.source)
        with self.assertRaises(terminal.MarketError):
            terminal.validate_coin('UNKNOWN', self.source)

    def test_candles_are_validated_normalized_and_sorted(self):
        result = terminal.candles('ETH-USDC', '5m', 25, self.source)
        self.assertEqual(len(result), 2)
        self.assertEqual([c['timestamp'] for c in result], [1000, 2000])
        self.assertEqual(result[1]['close'], 22)
        payload = self.source.queries[-1]['req']
        self.assertEqual(payload['interval'], '5m')
        self.assertEqual(payload['coin'], 'ETH')
        self.assertGreater(payload['endTime'], payload['startTime'])

    def test_reject_unbounded_requests(self):
        for tf, n in [('2x', 20), ('1m', 10000), ('5m', 19)]:
            with self.assertRaises(terminal.MarketError):
                terminal.candles('BTC', tf, n, self.source)

    def test_book_normalized(self):
        book = terminal.snapshot('BTC', self.source)
        self.assertEqual(book['bids'][0]['price'], 10)
        self.assertEqual(book['asks'][0]['price'], 11)

    def test_demo_open_and_close_levels(self):
        account = {'position': {'id': 't', 'symbol': 'SOL', 'direction': 'long', 'entry_price': 100,
                                'stop_price': 97, 'target_price': 105, 'opened_at': '2026-10-13T02:00:00+00:00'},
                   'history': [{'id': 'old', 'symbol': 'SOL', 'direction': 'short',
                                'entry_price': 110, 'exit_price': 105, 'pnl_net_usdc': 2.5,
                                'close_reason': 'TP', 'opened_at': '2026-10-12T02:00:00+00:00',
                                'closed_at': '2026-10-12T03:00:00+00:00'}]}
        result = project_account(account, mode='DEMO')
        self.assertEqual(result['active']['stop_price'], 97)
        self.assertEqual(result['active']['target_price'], 105)
        self.assertGreater(result['active']['opened_at'], 0)
        self.assertEqual(result['history'][0]['exit_price'], 105)
        self.assertEqual(result['history'][0]['close_reason'], 'TP')

    def test_live_original_fields_and_never_guess_missing_markers(self):
        close = project({'symbol': 'BTC', 'side': 'BUY', 'entry_price': 90, 'exit_price': 100,
                         'opened_at_ms': 1_780_000_000_000, 'timestamp': datetime.now(timezone.utc),
                         'profit': 4, 'exit_reason': 'TP'}, mode='LIVE', closed=True)
        self.assertEqual(close['direction'], 'LONG')
        self.assertEqual(close['opened_at'], 1_780_000_000_000)
        self.assertEqual(close['close_reason'], 'TP')
        partial = project({'symbol': 'BTC'}, mode='LIVE', closed=True)
        self.assertIsNone(partial['opened_at'])
        self.assertIsNone(partial['entry_price'])
        self.assertIsNone(partial['exit_price'])

    def test_live_snapshot_and_nested_audit_metadata(self):
        active = project({'symbol': 'SOL', 'direction': 'long', 'entry_price': 100,
                          'opened_at_ms': 1_780_000_000_000,
                          'trade_plan': {'structural_stop_price': 97, 'structural_target_price': 105}},
                         mode='LIVE', closed=False)
        self.assertEqual(active['stop_price'], 97)
        self.assertEqual(active['target_price'], 105)
        closed = project({'symbol': 'SOL', 'direction': 'long', 'entry_price': 100, 'exit_price': 105,
                          'timestamp': datetime.now(timezone.utc), 'profit': 5,
                          'metadata': {'opened_at_ms': 1_780_000_000_000, 'strategy_id': 'breakout_retest',
                                       'trade_plan': {'structural_stop_price': 97, 'structural_target_price': 105}}},
                         mode='LIVE', closed=True)
        self.assertEqual(closed['opened_at'], 1_780_000_000_000)
        self.assertEqual(closed['target_price'], 105)
        self.assertEqual(closed['strategy'], 'breakout_retest')

    def test_reject_non_finite_prices_and_timestamps(self):
        row = project({'symbol': 'BTC', 'entry_price': 'nan', 'stop_price': 'inf',
                       'target_price': -5, 'opened_at': 'garbage'}, mode='DEMO', closed=False)
        self.assertIsNone(row['entry_price'])
        self.assertIsNone(row['stop_price'])
        self.assertIsNone(row['target_price'])
        self.assertIsNone(row['opened_at'])
        self.assertIsNone(when('not a date'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
