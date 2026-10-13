"""Autorización del terminal sin Mongo, exchange ni claves LIVE."""
import sys
import types
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

fake_exchange = types.ModuleType('app.hyperliquid_client')
fake_exchange.make_request = lambda *args, **kwargs: {}
sys.modules['app.hyperliquid_client'] = fake_exchange
fake_dependencies = types.ModuleType('app.api.dependencies')

def session_required():
    raise HTTPException(status_code=401, detail='Se requiere autenticación')

fake_dependencies.require_session = session_required
sys.modules['app.api.dependencies'] = fake_dependencies

fake_ledger = types.ModuleType('app.demo.ledger')
accounts = {
    1: {'position': {'symbol': 'BTC', 'entry_price': 100, 'stop_price': 98, 'target_price': 104}, 'history': []},
    2: {'position': {'symbol': 'ETH', 'entry_price': 200, 'stop_price': 190, 'target_price': 220}, 'history': []},
}
fake_ledger.read = lambda user_id: accounts.get(user_id)
sys.modules['app.demo.ledger'] = fake_ledger

fake_db = types.ModuleType('app.database')
fake_db.get_user_active_trade_snapshot = lambda user_id: {'symbol': 'SOL' if user_id == 1 else 'DOGE'}
fake_db.get_user_trades_limited = lambda user_id, limit=100: [{'symbol': 'BTC'}] if user_id == 1 else []
sys.modules['app.database'] = fake_db

from app.api.routes_market import router
app = FastAPI()
app.include_router(router)
client = TestClient(app)


class AccessTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_routes_require_identity(self):
        for url in ['/api/v1/market/instruments', '/api/v1/market/candles?symbol=BTC',
                    '/api/v1/market/snapshot?symbol=BTC', '/api/v1/market/overlays?mode=DEMO']:
            self.assertEqual(client.get(url).status_code, 401)

    def test_demo_never_reads_another_users_position(self):
        app.dependency_overrides[session_required] = lambda: {'user_id': 1}
        user1 = client.get('/api/v1/market/overlays?mode=DEMO').json()
        self.assertEqual(user1['active']['symbol'], 'BTC')
        app.dependency_overrides[session_required] = lambda: {'user_id': 2}
        user2 = client.get('/api/v1/market/overlays?mode=DEMO').json()
        self.assertEqual(user2['active']['symbol'], 'ETH')

    def test_live_and_demo_are_distinct_data_sources(self):
        app.dependency_overrides[session_required] = lambda: {'user_id': 1}
        demo = client.get('/api/v1/market/overlays?mode=DEMO').json()
        live = client.get('/api/v1/market/overlays?mode=LIVE').json()
        self.assertEqual(demo['active']['symbol'], 'BTC')
        self.assertEqual(live['active']['symbol'], 'SOL')

    def test_mode_validation(self):
        app.dependency_overrides[session_required] = lambda: {'user_id': 1}
        self.assertEqual(client.get('/api/v1/market/overlays?mode=ARBITRARY').status_code, 422)


if __name__ == '__main__':
    unittest.main(verbosity=2)
