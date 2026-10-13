"""Offline API integration: OIDC redirect/callback, cookie session, CSRF and logout.

Run directly. Stubs DB and Telegram token exchange; does NOT verify a real OIDC
provider response or touch any user account.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'offline-test')
os.environ.setdefault('ADMIN_TELEGRAM_ID', '1')
os.environ.setdefault('MONGO_URL', 'mongodb://offline.invalid')
os.environ.setdefault('WEBAPP_SESSION_SECRET', 'offline-session-secret')

fake_db = types.ModuleType('app.database')
fake_db.set_referrer = lambda *_: None
sys.modules['app.database'] = fake_db
fake_service = types.ModuleType('app.services.user_service')
fake_service.ensure_user_exists = lambda *_: None
fake_service.get_user_profile = lambda uid: {'user_id': uid, 'username': 'test-user'}
sys.modules['app.services.user_service'] = fake_service

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api import routes_auth, dependencies
from app.api.telegram_oidc import CSRF_COOKIE

app = FastAPI()
app.include_router(routes_auth.router)

class FakeTokenResponse:
    def raise_for_status(self):
        return None
    def json(self):
        return {'id_token': 'offline.jwt'}

class FakeHttpClient:
    def __init__(self, *args, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return None
    async def post(self, *args, **kwargs):
        assert kwargs['data']['grant_type'] == 'authorization_code'
        assert kwargs['data']['code_verifier']
        return FakeTokenResponse()

with (
    patch.object(routes_auth, 'WEB_LOGIN_ENABLED', True),
    patch.object(routes_auth, 'WEB_BASE_URL', 'https://trading.example'),
    patch.object(routes_auth, 'TELEGRAM_OIDC_CLIENT_ID', '123456'),
    patch.object(routes_auth, 'TELEGRAM_OIDC_CLIENT_SECRET', 'offline-secret'),
    patch.object(routes_auth, 'TELEGRAM_OIDC_REDIRECT_URI', 'https://trading.example/api/v1/auth/telegram/oidc/callback'),
    patch.object(dependencies, 'WEB_BASE_URL', 'https://trading.example'),
    patch.object(routes_auth.httpx, 'AsyncClient', FakeHttpClient),
    patch.object(routes_auth, 'verify_telegram_id_token', return_value={'user_id': 155, 'username': 'test-user'}),
):
    with TestClient(app, base_url='https://trading.example') as client:
        start = client.get('/api/v1/auth/telegram/oidc/start', follow_redirects=False)
        assert start.status_code == 302, start.text
        location = urlsplit(start.headers['location'])
        assert location.netloc == 'oauth.telegram.org'
        state = parse_qs(location.query)['state'][0]
        assert 'code_challenge' in parse_qs(location.query)
        assert '__Host-txh_oidc' in start.headers.get('set-cookie', '')

        tamper = client.get('/api/v1/auth/telegram/oidc/callback?code=abc&state=invalid', follow_redirects=False)
        assert tamper.status_code == 401, tamper.text

        callback = client.get(f'/api/v1/auth/telegram/oidc/callback?code=abc&state={state}', follow_redirects=False)
        assert callback.status_code == 303, callback.text
        assert '__Host-txh_session' in callback.headers.get('set-cookie', '')
        session = client.get('/api/v1/auth/session')
        assert session.status_code == 200, session.text
        assert session.json()['user']['user_id'] == 155

        csrf = client.cookies[CSRF_COOKIE]
        assert client.post('/api/v1/auth/logout', headers={'Origin': 'https://attacker.example', 'X-CSRF-Token': csrf}).status_code == 403
        response = client.post('/api/v1/auth/logout', headers={'Origin': 'https://trading.example', 'X-CSRF-Token': csrf})
        assert response.status_code == 204, response.text
        assert client.get('/api/v1/auth/session').status_code == 401
print('Offline OIDC API integration: passed')
