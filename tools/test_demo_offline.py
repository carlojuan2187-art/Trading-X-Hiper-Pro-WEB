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
            if k == '$or':
                if not any(self._matches(doc, q) for q in v):
                    return False
            elif isinstance(v, dict) and '$ne' in v:
                if doc.get(k) == v['$ne']:
                    return False
            elif isinstance(v, dict) and '$lte' in v:
                if doc.get(k) is None or doc[k] > v['$lte']:
                    return False
            elif doc.get(k) != v:
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
