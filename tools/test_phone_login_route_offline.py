"""Pruebas HTTP (sin Mongo) del acceso recurrente por teléfono y contraseña."""
from __future__ import annotations
import os
import sys
import types
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.test_phone_flow_offline import init_module


class TestRoute(TestCase):
    def setUp(self):
        self.auth = init_module()
        self.auth.init_indexes()
        conf = types.ModuleType('app.config')
        conf.ADMIN_TELEGRAM_ID = 999
        conf.BOT_USERNAME = 'MyTestBot'
        conf.WEBAPP_SESSION_TTL_SECONDS = 3600
        conf.WEB_BASE_URL = 'https://example.test'
        sec = types.ModuleType('app.api.security')
        sec.create_session_token = lambda profile: 'test-signed-cookie-for-' + str(profile['user_id'])
        oidc = types.ModuleType('app.api.telegram_oidc')
        oidc.CSRF_COOKIE = 'csrf_token'
        oidc.SESSION_COOKIE = 'hl_session'
        import importlib
        self.patches = [patch.dict(sys.modules, {
            'app.config': conf, 'app.api.security': sec, 'app.api.telegram_oidc': oidc}),
            patch.dict(os.environ, {
                'WEBAPP_SESSION_SECRET': 'offline-secret', 'BOT_USERNAME': 'MyTestBot'})]
        for p in self.patches:
            p.start()
        sys.modules.pop('app.api.routes_phone', None)
        route = importlib.import_module('app.api.routes_phone')
        self.app = FastAPI()
        self.app.include_router(route.router)
        self.client = TestClient(self.app, base_url='https://example.test',
                                 headers={'origin': 'https://example.test'})
        self.phone = '+17875551234'
        self.password = 'LongStrongPassword!23'

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        sys.modules.pop('app.api.routes_phone', None)

    def post(self, mode='login', password=None):
        return self.client.post('/api/v1/auth/phone/start', json={
            'phone': self.phone, 'password': password or self.password,
            'mode': mode, 'referrer': ''})

    def test_registration_needs_telegram_and_login_does_not(self):
        registered = self.post('register')
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertIn('telegram_url', registered.json())
        self.assertNotIn('authenticated', registered.json())
        flow = registered.json()['flow_id']
        self.assertEqual(self.auth.confirm_contact(flow, 505, self.phone), 'ok')
        self.assertIn('hl_phone_verification', registered.headers.get('set-cookie', ''))
        finished = self.client.post('/api/v1/auth/phone/complete', json={'flow_id': flow})
        self.assertEqual(finished.status_code, 200, finished.text)
        self.assertTrue(finished.json()['authenticated'])
        previous_flows = len(self.auth.FLOWS.rows)
        logged_in = self.post('login')
        self.assertEqual(logged_in.status_code, 200, logged_in.text)
        self.assertEqual(logged_in.json(), {'authenticated': True})
        self.assertEqual(len(self.auth.FLOWS.rows), previous_flows)
        self.assertIn('hl_session', logged_in.headers.get('set-cookie', ''))
        self.assertEqual(self.client.cookies.get('hl_session'), 'test-signed-cookie-for-505')

    def test_invalid_or_unregistered_cannot_login(self):
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post('register').status_code, 200)
        self.assertEqual(self.post().status_code, 401)


if __name__ == '__main__':
    main(verbosity=2)
