"""Pruebas de integración offline del subsistema DEMO sin Mongo/exchange ni SDK LIVE.

Ejecutar: python tools/test_demo_offline.py
En producción ejecutar tests completos también con requirements.txt instalados.
"""
import copy
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DEMO_ENABLED', 'true')


class FakeResult:
    def __init__(self, count):
        self.modified_count = count


class FakeCollection:
    def __init__(self):
        self.rows = {}

    def _matches(self, doc, query):
        for k, v in query.items():
            nested = doc
            for part in k.split('.'):
                nested = nested.get(part) if isinstance(nested, dict) else None
            if k == '$or':
                if not any(self._matches(doc, q) for q in v):
                    return False
            elif isinstance(v, dict) and '$ne' in v:
                if nested == v['$ne']:
                    return False
            elif isinstance(v, dict) and '$lte' in v:
                if nested is None or nested > v['$lte']:
                    return False
            elif nested != v:
                return False
        return True

    def update_one(self, query, update, upsert=False):
        ident = query['_id']
        row = self.rows.get(ident)
        if row is None:
            if not upsert:
                return FakeResult(0)
            self.rows[ident] = copy.deepcopy(update.get('$setOnInsert', {'_id': ident}))
            return FakeResult(1)
        if not self._matches(row, query):
            return FakeResult(0)
        row.update(copy.deepcopy(update.get('$set', {})))
        return FakeResult(1)

    def find_one(self, query):
        row = self.rows.get(query['_id'])
        return copy.deepcopy(row) if row and self._matches(row, query) else None

    def find(self, query):
        return [copy.deepcopy(v) for v in self.rows.values() if self._matches(v, query)]

    def replace_one(self, query, new):
        row = self.rows.get(query['_id'])
        if not row or not self._matches(row, query):
            return FakeResult(0)
        self.rows[query['_id']] = copy.deepcopy(new)
        return FakeResult(1)


fake_accounts = FakeCollection()
fake_leases = FakeCollection()
db = {'demo_accounts': fake_accounts, 'demo_worker_leases': fake_leases}

db_module = types.ModuleType('app.database')
db_module.db = db
db_module.touch_runtime_component = lambda *args, **kwargs: None
sys.modules['app.database'] = db_module
config_module = types.ModuleType('app.config')
for k, v in {
    'LEVERAGE': 10, 'EXECUTION_MAX_SPREAD_BPS': 100,
    'MAX_TRADES_PER_DAY': 12, 'MAX_TRADES_PER_HOUR': 4,
    'USER_RISK_MAX_CONSEC_LOSSES': 3, 'USER_RISK_COOLDOWN_SECONDS': 3600,
    'USER_RISK_LOSS_COOLDOWN_SECONDS': 600, 'USER_RISK_PF_WINDOW': 5,
    'USER_RISK_MIN_PF': 1.0, 'USER_RISK_PF_COOLDOWN_SECONDS': 7200,
}.items():
    setattr(config_module, k, v)
sys.modules['app.config'] = config_module
risk_module = types.ModuleType('app.risk')
risk_module.validate_trade_conditions = lambda balance, strength: {'ok': True, 'position_size': balance}
sys.modules['app.risk'] = risk_module
engine = types.ModuleType('app.trading_engine')
engine.MIN_TRADE_STRENGTH = 0.18
engine.MIN_CAPITAL_USDC = 3
engine.MIN_NOTIONAL_USDC = 10
engine.MIN_QTY_COIN = 0.0001
engine.USER_TRADE_COOLDOWN_SECONDS = 600
engine._entry_drift_check = lambda signal, current_price, direction: (abs(current_price / signal['signal_reference_price'] - 1) < .03, {})
engine._coalesce_management_params = lambda **k: {'tp_activate_price': 0.01}
engine._resolve_structural_exit_plan = lambda signal, entry_price, direction, fallback_sl_pct, fallback_tp_pct: (
    True, {'stop_price': signal['stop_price'], 'target_price': signal['target_price'], 'rr': 1.5}
)
sys.modules['app.trading_engine'] = engine
market = types.ModuleType('app.hyperliquid_client')
market.get_best_bid_ask = lambda symbol: (100.0, 100.1)
sys.modules['app.hyperliquid_client'] = market
pymongo = types.ModuleType('pymongo')
pymongo_errors = types.ModuleType('pymongo.errors')
pymongo_errors.DuplicateKeyError = type('DuplicateKeyError', (Exception,), {})
sys.modules['pymongo'] = pymongo
sys.modules['pymongo.errors'] = pymongo_errors

from app.demo import ledger
from app.demo import simulator
from app.demo import worker
from app.demo import credentials

# Small HTTP integration harness: auth dependency denied unless explicitly overridden.
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

def _require_session_stub():
    raise HTTPException(status_code=401, detail='Session requerida')

deps_stub = types.ModuleType('app.api.dependencies')
deps_stub.require_session = _require_session_stub
sys.modules['app.api.dependencies'] = deps_stub
from app.api.routes_demo import router as demo_router

# El entorno offline no dispone del servicio real de usuarios: devuelve un
# perfil sintético cifrado, SIN clave ni wallet reales.
credentials.user_ready = lambda user_id: True
import app.api.routes_demo as _demo_routes
_demo_routes.user_ready = lambda user_id: True
worker.user_ready = lambda user_id: True


def signal(direction='long'):
    return {
        'symbol': 'SOL', 'direction': direction, 'strength': 0.8,
        'signal': {'signal': True, 'signal_reference_price': 100.05,
                   'strategy_id': 'liquidity_sweep_reversal', 'regime_id': 'trend',
                   'score': 0.9, 'stop_price': 98 if direction == 'long' else 103,
                   'target_price': 104 if direction == 'long' else 96,
                   'sl_price_pct': 0.02},
    }


class DemoOfflineTests(unittest.TestCase):
    def setUp(self):
        fake_accounts.rows.clear()
        fake_leases.rows.clear()

    def test_unique_account_per_telegram_user(self):
        a = ledger.get_or_create(7)
        b = ledger.get_or_create(8)
        self.assertEqual(a['balance_usdc'], 100.0)
        self.assertNotEqual(a['_id'], b['_id'])
        ledger.change_status(7, active=True)
        self.assertFalse(ledger.read(8)['enabled'])

    def test_demo_capital_configurable_independent_per_user(self):
        a = ledger.configure_initial_capital(7, 250.50)
        self.assertEqual(a['initial_equity_usdc'], 250.50)
        self.assertEqual(a['balance_usdc'], 250.50)
        self.assertEqual(ledger.get_or_create(8)['balance_usdc'], 100.0)
        reset = ledger.reset(7)
        self.assertEqual(reset['balance_usdc'], 250.50)
        self.assertEqual(reset['initial_equity_usdc'], 250.50)
        self.assertEqual(reset['version'], a['version'] + 1)

    def test_demo_capital_validation_and_history_guard(self):
        for invalid in (-1, 0, 9, 100001, float('nan'), float('inf'), 100.005, True):
            with self.assertRaises(ValueError):
                ledger.configure_initial_capital(7, invalid)
        ledger.configure_initial_capital(7, 300)
        ledger.change_status(7, active=True)
        with self.assertRaises(ValueError):
            ledger.configure_initial_capital(7, 400)
        # La posición se abre con el worker activo; al pausar, se sigue
        # gestionando para cerrar en TP/SL, pero no permite cambiar capital.
        opened = simulator.open_position(ledger.read(7), signal(), 100, 100.1)
        self.assertIsNotNone(opened)
        ledger.save(ledger.read(7), opened)
        ledger.change_status(7, active=False)
        with self.assertRaises(ValueError):
            ledger.configure_initial_capital(7, 400)
        closed = simulator.update_position(ledger.read(7), 104.2, 104.3)
        ledger.save(ledger.read(7), closed)
        with self.assertRaises(ValueError):
            ledger.configure_initial_capital(7, 400)
        self.assertEqual(ledger.read(7)['trades_closed'], 1)
        ledger.reset(7)
        self.assertEqual(ledger.configure_initial_capital(7, 400)['balance_usdc'], 400)

    def test_capital_api_authenticated_and_prevents_cross_user_change(self):
        app = FastAPI()
        app.include_router(demo_router)
        client = TestClient(app)
        self.assertEqual(client.post('/api/v1/demo/capital', json={'capital_usdc': 250}).status_code, 401)
        app.dependency_overrides[_require_session_stub] = lambda: {'user_id': 7}
        self.assertEqual(client.post('/api/v1/demo/capital', json={'capital_usdc': 250}).status_code, 200)
        self.assertEqual(client.get('/api/v1/demo').json()['initial_equity_usdc'], 250)
        self.assertIsNone(ledger.read(8))
        self.assertEqual(client.post('/api/v1/demo/capital', json={'capital_usdc': 1000001}).status_code, 422)
        app.dependency_overrides[_require_session_stub] = lambda: {'user_id': 8}
        self.assertEqual(client.get('/api/v1/demo').json()['initial_equity_usdc'], 100)
        self.assertEqual(ledger.read(7)['initial_equity_usdc'], 250)

    def test_long_full_lifecycle_with_net_fees(self):
        acc = ledger.change_status(7, active=True)
        opened = simulator.open_position(acc, signal(), 100, 100.1)
        self.assertIsNotNone(opened)
        self.assertLessEqual(opened['position']['margin_usdc'], 100)
        self.assertGreater(opened['position']['margin_usdc'], 99)
        self.assertEqual(opened['position']['mode'], 'DEMO')
        self.assertLess(opened['balance_usdc'], 100)
        ledger.save(acc, opened)
        changed = simulator.update_position(opened, 102, 102.1)
        self.assertIsNotNone(changed['position'])
        closed = simulator.update_position(changed, 104.2, 104.3)
        self.assertIsNone(closed['position'])
        self.assertEqual(closed['trades_closed'], 1)
        self.assertEqual(closed['history'][0]['close_reason'], 'TP')
        self.assertAlmostEqual(closed['balance_usdc'] - 100, closed['realized_pnl_usdc'], places=5)

    def test_short_stop_loss_gap_and_fees(self):
        acc = ledger.change_status(9, active=True)
        opened = simulator.open_position(acc, signal('short'), 100, 100.1)
        self.assertIsNotNone(opened)
        # Gap por encima SL; fill al ask real con deslizamiento, no en el nivel SL.
        closed = simulator.update_position(opened, 106, 106.1)
        self.assertEqual(closed['history'][0]['close_reason'], 'SL')
        self.assertGreater(closed['history'][0]['exit_price'], 103)
        self.assertLess(closed['history'][0]['pnl_net_usdc'], 0)
        self.assertEqual(closed['losses'], 1)

    def test_invalid_market_data_blocks_entries_and_closes(self):
        acc = ledger.change_status(10, active=True)
        self.assertIsNone(simulator.open_position(acc, signal(), 0, 0))
        self.assertIsNone(simulator.open_position(acc, signal(), 101, 100))
        opened = simulator.open_position(acc, signal(), 100, 100.1)
        self.assertIsNone(simulator.update_position(opened, 0, 0))
        self.assertIsNotNone(opened['position'])

    def test_concurrent_change_fails_closed(self):
        acc = ledger.get_or_create(7)
        ledger.change_status(7, active=True)
        with self.assertRaises(ledger.Conflict):
            ledger.save(acc, {**acc, 'enabled': True})

    def test_reset_safety(self):
        acc = ledger.change_status(7, active=True)
        with self.assertRaises(ValueError):
            ledger.reset(7)
        opened = simulator.open_position(acc, signal(), 100, 100.1)
        ledger.save(acc, opened)
        ledger.change_status(7, active=False)
        with self.assertRaises(ValueError):
            ledger.reset(7)
        closed = simulator.update_position(ledger.read(7), 104.2, 104.3)
        ledger.save(ledger.read(7), closed)
        reset = ledger.reset(7)
        self.assertFalse(reset['enabled'])
        self.assertEqual(reset['balance_usdc'], 100)
        self.assertEqual(reset['trades_closed'], 0)

    def test_demo_selects_an_independent_strategy_per_user(self):
        ledger.change_status(7, active=True)
        ledger.change_status(8, active=True)
        called = []
        def choose(uid):
            called.append(uid)
            chosen = signal('long' if uid == 7 else 'short')
            chosen['symbol'] = 'SOL' if uid == 7 else 'BTC'
            return chosen
        results = worker.tick(do_scan=True, selector=choose, quote_provider=lambda _: (100, 100.1))
        self.assertEqual(set(called), {7, 8})
        self.assertEqual(results['opened'], 2)
        self.assertEqual(ledger.read(7)['position']['symbol'], 'SOL')
        self.assertEqual(ledger.read(8)['position']['symbol'], 'BTC')
        self.assertEqual(ledger.read(7)['position']['direction'], 'long')
        self.assertEqual(ledger.read(8)['position']['direction'], 'short')

    def test_pending_signals_are_user_scoped_and_symbol_scoped(self):
        ledger.change_status(7, active=True)
        ledger.change_status(8, active=True)
        runner = worker.DemoMarketRunner()
        now = worker.time.monotonic()
        first, second = signal(), signal('short')
        second['symbol'] = 'BTC'
        runner.pending_by_user = {7: (first, now), 8: (second, now)}
        self.assertEqual(runner.open_if_pending('SOL', 100, 100.1), 1)
        self.assertIsNotNone(ledger.read(7)['position'])
        self.assertIsNone(ledger.read(8)['position'])
        self.assertIn(8, runner.pending_by_user)
        self.assertEqual(runner.open_if_pending('BTC', 100, 100.1), 1)
        self.assertEqual(ledger.read(8)['position']['symbol'], 'BTC')
        self.assertEqual(runner.open_if_pending('BTC', 100, 100.1), 0)

    def test_expired_signal_fails_closed(self):
        ledger.change_status(7, active=True)
        runner = worker.DemoMarketRunner()
        runner.pending_by_user = {7: (signal(), worker.time.monotonic() - 31)}
        self.assertEqual(runner.open_if_pending('SOL', 100, 100.1), 0)
        self.assertIsNone(ledger.read(7)['position'])
        self.assertEqual(runner.pending_by_user, {})

    def test_paused_or_risk_blocked_account_never_executes_pending_signal(self):
        ledger.change_status(7, active=True)
        runner = worker.DemoMarketRunner()
        runner.pending_by_user = {7: (signal(), worker.time.monotonic())}
        ledger.change_status(7, active=False)
        self.assertEqual(runner.open_if_pending('SOL', 100, 100.1), 0)
        self.assertIsNone(ledger.read(7)['position'])
        account = ledger.change_status(7, active=True)
        account['risk_cooldown_until'] = '2099-01-01T00:00:00+00:00'
        ledger.save(ledger.read(7), account)
        self.assertEqual(simulator.entry_gate_reason(ledger.read(7)), 'RISK_COOLDOWN')
        runner.pending_by_user = {7: (signal(), worker.time.monotonic())}
        self.assertEqual(runner.open_if_pending('SOL', 100, 100.1), 0)

    def test_deep_scan_is_per_user_and_rotates_fairly(self):
        for uid in range(1, 9):
            ledger.change_status(uid, active=True)
        seen = []
        def selector(uid, excluded, *, execution_mode):
            self.assertEqual(execution_mode, 'demo')
            seen.append(uid)
            return None
        with patch.object(engine, '_select_best_signal_from_scanner_shortlist', side_effect=selector, create=True):
            runner = worker.DemoMarketRunner()
            runner.select_signal()
            runner.select_signal()
        self.assertEqual(seen, [1, 2, 3, 4, 5, 6, 7, 8, 1, 2])

    def test_strategy_metrics_are_net_and_scoped_to_demo_history(self):
        result = ledger.recent_strategy_metrics([
            {'strategy_id': 'breakout', 'pnl_net_usdc': 1.5},
            {'strategy_id': 'breakout', 'pnl_net_usdc': -1},
            {'strategy_id': 'sweep', 'pnl_net_usdc': -0.5},
        ])
        breakout = next(row for row in result if row['strategy_id'] == 'breakout')
        self.assertEqual(breakout['trades'], 2)
        self.assertEqual(breakout['win_rate_pct'], 50.0)
        self.assertAlmostEqual(breakout['net_pnl_usdc'], 0.5)
        self.assertEqual(breakout['profit_factor'], 1.5)
        self.assertEqual(ledger.as_public(ledger.get_or_create(7))['strategy_metrics_scope'], 'retained_demo_history_only')

    def test_slow_scanner_does_not_block_websocket_stop_loss(self):
        import asyncio
        import json
        import time
        import websockets
        acc = ledger.change_status(7, active=True)
        opened = simulator.open_position(acc, signal(), 100, 100.1)
        ledger.save(acc, opened)
        class StopSocket(Exception):
            pass
        class FakeWebsocket:
            def __init__(self):
                self.calls = 0
                self.messages = []
            async def send(self, data):
                self.messages.append(json.loads(data))
            async def recv(self):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.sleep(.025)
                    return json.dumps({'channel': 'l2Book', 'data': {'coin': 'SOL',
                            'levels': [[{'px': '104.2'}], [{'px': '104.3'}]]}})
                raise StopSocket()
        class FakeConnect:
            async def __aenter__(self):
                return FakeWebsocket()
            async def __aexit__(self, *args):
                return False
        runner = worker.DemoMarketRunner()
        def slow_scan():
            time.sleep(.35)
            return {}, 0
        runner.collect_signals = slow_scan
        start = time.monotonic()
        with patch.object(websockets, 'connect', return_value=FakeConnect()), \
             patch.object(worker, 'acquire_lease', return_value=True):
            with self.assertRaises(StopSocket):
                asyncio.run(runner.run_socket())
        # El SL/TP se procesó antes de que terminase el scanner costoso.
        # asyncio.run espera a cerrar el threadpool: comprobamos el hecho,
        # no el tiempo total del proceso.
        self.assertEqual(ledger.read(7)['trades_closed'], 1)

    def test_worker_uses_injected_market_and_selector_without_live_orders(self):
        ledger.change_status(7, active=True)
        result = worker.tick(do_scan=True, quote_provider=lambda _: (100, 100.1), selector=lambda _: signal())
        self.assertTrue(result['scan_attempted'])
        self.assertEqual(result['opened'], 1)
        self.assertIsNotNone(ledger.read(7)['position'])
        paused = ledger.change_status(7, active=False)
        self.assertFalse(paused['enabled'])
        result = worker.tick(do_scan=False, quote_provider=lambda _: (104.2, 104.3))
        self.assertEqual(result['closed_or_marked'], 1)
        self.assertEqual(ledger.read(7)['trades_closed'], 1)

    def test_http_auth_and_user_isolation(self):
        app = FastAPI()
        app.include_router(demo_router)
        client = TestClient(app)
        self.assertEqual(client.get('/api/v1/demo').status_code, 401)
        self.assertEqual(client.post('/api/v1/demo/start').status_code, 401)
        app.dependency_overrides[_require_session_stub] = lambda: {'user_id': 7}
        self.assertEqual(client.get('/api/v1/demo').json()['user_id'], 7)
        self.assertEqual(client.post('/api/v1/demo/start').status_code, 200)
        self.assertTrue(ledger.read(7)['enabled'])
        self.assertIsNone(ledger.read(8))
        app.dependency_overrides[_require_session_stub] = lambda: {'user_id': 8}
        self.assertFalse(client.get('/api/v1/demo').json()['enabled'])
        self.assertTrue(ledger.read(7)['enabled'])

    def test_does_not_reenter_immediately_after_close(self):
        acc = ledger.change_status(7, active=True)
        opened = simulator.open_position(acc, signal(), 100, 100.1)
        closed = simulator.update_position(opened, 104.2, 104.3)
        self.assertIsNone(simulator.open_position(closed, signal(), 100, 100.1))


if __name__ == '__main__':
    unittest.main(verbosity=2)
