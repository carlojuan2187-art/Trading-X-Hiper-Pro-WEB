"""Pruebas offline de fase 4, sin wallet privada, red ni MongoDB.

python tools/test_phase4_offline.py
"""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_demo_offline as fake  # carga los dobles existentes, sin ejecutar suite
from app.demo.credentials import credential_ready
from app.demo.market_events import decode_book
from app.demo import worker, ledger
from app.api import routes_demo
from fastapi.testclient import TestClient
from fastapi import FastAPI

# Las pruebas no necesitan importar ni descifrar claves reales.
auth_stub = types.ModuleType('app.api.security')
auth_stub.verify_session_token = lambda token: {'user_id': 77, 'exp': 2000000000} if token == 'valid' else _deny()
def _deny():
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail='invalid')
sys.modules['app.api.security'] = auth_stub
session_stub = types.ModuleType('app.api.telegram_oidc')
session_stub.SESSION_COOKIE = 'hl_session'
sys.modules['app.api.telegram_oidc'] = session_stub
fake.pymongo_errors.PyMongoError = type('PyMongoError', (Exception,), {})
fake.config_module.WEB_BASE_URL = 'https://trading.example'
from app.realtime.stream import authorize_ws, event_owner, SharedChangeHub


class PhaseFourTests(unittest.TestCase):
    def setUp(self):
        fake.fake_accounts.rows.clear()
        fake.fake_leases.rows.clear()

    def test_credential_gate_rejects_plaintext_and_invalid(self):
        good = dict(wallet_configured=True, private_key_configured=True,
                    private_key_storage='encrypted', private_key_health='configured',
                    credential_repair_required=False)
        self.assertTrue(credential_ready(good))
        for change in ({'private_key_storage': 'legacy_plaintext'},
                       {'private_key_health': 'invalid'},
                       {'private_key_configured': False},
                       {'wallet_configured': False},
                       {'credential_repair_required': True}):
            self.assertFalse(credential_ready({**good, **change}))
        self.assertFalse(credential_ready(None))

    def test_demo_start_needs_credential_even_if_session_valid(self):
        app = FastAPI()
        app.include_router(routes_demo.router)
        app.dependency_overrides[fake._require_session_stub] = lambda: {'user_id': 55}
        with patch.object(routes_demo, 'user_ready', return_value=False):
            response = TestClient(app).post('/api/v1/demo/start')
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(ledger.read(55))

    def test_ws_bid_ask_valid(self):
        packet = {'channel': 'l2Book', 'data': {'coin': 'SOL', 'levels': [
            [{'px': '150.1', 'sz': '10'}], [{'px': '150.2', 'sz': '12'}],
        ]}}
        self.assertEqual(decode_book(packet), ('SOL', 150.1, 150.2))
        self.assertIsNone(decode_book({'channel': 'trades', 'data': {}}))
        packet['data']['levels'][0][0]['px'] = '99999'
        self.assertIsNone(decode_book(packet))

    def test_ws_malformed_quotes_never_trade(self):
        for raw in ('{not json', '{}', 'null', '[]',
                    {'channel': 'l2Book', 'data': {'coin': 'SOL', 'levels': [[], []]}},
                    {'channel': 'l2Book', 'data': {'coin': 'SOL', 'levels': [
                        [{'px': 'nan'}], [{'px': '42'}],
                    ]}}):
            self.assertIsNone(decode_book(raw))

    def test_authenticated_cookie_and_origin_required(self):
        self.assertEqual(authorize_ws('valid', 'https://trading.example', 'https://trading.example'), 77)
        for cookie, origin, base in [(None, 'https://trading.example', 'https://trading.example'),
                                     ('valid', 'https://evil.example', 'https://trading.example'),
                                     ('bad', 'https://trading.example', 'https://trading.example'),
                                     ('valid', 'http://trading.example', 'http://trading.example')]:
            with self.assertRaises(ValueError):
                authorize_ws(cookie, origin, base)

    def test_mongo_events_isolated_by_owner(self):
        self.assertEqual(event_owner({'ns': {'coll': 'demo_accounts'}, 'documentKey': {'_id': 55}}),
                         (55, 'demo_accounts'))
        self.assertEqual(event_owner({'ns': {'coll': 'users'}, 'fullDocument': {'user_id': 77}}),
                         (77, 'users'))
        self.assertIsNone(event_owner({'ns': {'coll': 'users'}, 'fullDocument': {'user_id': '77'}}))
        self.assertIsNone(event_owner({'ns': {'coll': 'invoices'}, 'fullDocument': {'user_id': 77}}))

    def test_fanout_delivers_only_for_correct_user(self):
        hub = SharedChangeHub()
        one, two = asyncio.Queue(), asyncio.Queue()
        hub.subscribers[77].add(one)
        hub.subscribers[88].add(two)
        hub.publish(77, 'demo_accounts')
        self.assertEqual(one.get_nowait(), 'demo_accounts')
        self.assertTrue(two.empty())

    def test_overflow_triggers_resync_without_leaking_document(self):
        hub = SharedChangeHub()
        q = asyncio.Queue(maxsize=1)
        hub.subscribers[77].add(q)
        q.put_nowait('demo_accounts')
        hub.publish(77, 'users')
        self.assertEqual(q.get_nowait(), 'resync')
        self.assertTrue(q.empty())

    def test_worker_market_update_uses_event_quote_and_closes(self):
        account = ledger.change_status(55, active=True)
        opened = fake.simulator.open_position(account, fake.signal(), 100, 100.1)
        ledger.save(account, opened)
        runner = worker.DemoMarketRunner()
        saved_find = ledger.COLLECTION.find
        ledger.COLLECTION.find = lambda q: [ledger.read(55)] if 'position.symbol' in q and q['position.symbol'] == 'SOL' else saved_find(q)
        try:
            _, closed = runner.protected_positions('SOL', 104.2, 104.3)
        finally:
            ledger.COLLECTION.find = saved_find
        self.assertEqual(closed, 1)
        self.assertEqual(ledger.read(55)['trades_closed'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
