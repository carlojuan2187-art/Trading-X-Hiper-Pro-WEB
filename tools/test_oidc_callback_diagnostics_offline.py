"""Offline regression tests for Telegram OIDC callback: no real credentials/network.

Run: python tools/test_oidc_callback_diagnostics_offline.py
"""
from __future__ import annotations

import io
import logging
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'offline-test-token')
os.environ.setdefault('ADMIN_TELEGRAM_ID', '1')
os.environ.setdefault('MONGO_URL', 'mongodb://offline.invalid')
os.environ.setdefault('WEBAPP_SESSION_SECRET', 'test-only-session-secret')

fake_db = types.ModuleType('app.database')
fake_db.set_referrer = lambda *_: None
sys.modules['app.database'] = fake_db
fake_service = types.ModuleType('app.services.user_service')
fake_service.ensure_user_exists = lambda *_: None
fake_service.get_user_profile = lambda uid: {'user_id': uid, 'username': 'demo'}
sys.modules['app.services.user_service'] = fake_service

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api import routes_auth, dependencies

app = FastAPI()
app.include_router(routes_auth.router)

class FakeHttpClient:
    payload = {'id_token': 'test.jwt'}
    status = 200
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        return None
    def __init__(self, *_args, **_kwargs):
        pass
    async def post(self, url, **kwargs):
        assert url == routes_auth.OIDC_TOKEN_URL
        assert kwargs['data']['code_verifier']
        assert kwargs['auth'][0] == '123456'
        return httpx.Response(self.status, json=self.payload,
                              request=httpx.Request('POST', url))

log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
logger = logging.getLogger(routes_auth.__name__)
logger.addHandler(handler)
logger.setLevel(logging.WARNING)
try:
    with (
        patch.object(routes_auth, 'WEB_LOGIN_ENABLED', True),
        patch.object(routes_auth, 'WEB_BASE_URL', 'https://web.example'),
        patch.object(routes_auth, 'TELEGRAM_OIDC_CLIENT_ID', '123456'),
        patch.object(routes_auth, 'TELEGRAM_OIDC_CLIENT_SECRET', 'secret-for-test'),
        patch.object(routes_auth, 'TELEGRAM_OIDC_REDIRECT_URI', 'https://web.example/api/v1/auth/telegram/oidc/callback'),
        patch.object(dependencies, 'WEB_BASE_URL', 'https://web.example'),
        patch.object(routes_auth.httpx, 'AsyncClient', FakeHttpClient),
        patch.object(routes_auth, 'verify_telegram_id_token', return_value={'user_id': 155, 'username': 'demo'}),
    ):
        cases = [
            ({'error': 'invalid_client', 'error_description': 'SENSITIVE_SERVER_DETAILS'}, 200, 401, 'oauth_error=invalid_client'),
            ({'access_token': 'SENSITIVE_ACCESS_TOKEN', 'token_type': 'Bearer'}, 200, 401, 'missing id_token'),
            ({'id_token': None, 'error': 'invalid_grant'}, 200, 401, 'oauth_error=invalid_grant'),
            ({'error': 'invalid_client'}, 401, 401, 'http_status=401'),
            ({'id_token': 'offline.jwt'}, 200, 303, None),
        ]
        for i, (payload, status, expected, marker) in enumerate(cases):
            log_stream.seek(0)
            log_stream.truncate(0)
            FakeHttpClient.payload = payload
            FakeHttpClient.status = status
            with TestClient(app, base_url='https://web.example') as client:
                start = client.get('/api/v1/auth/telegram/oidc/start', follow_redirects=False)
                assert start.status_code == 302
                state = parse_qs(urlsplit(start.headers['location']).query)['state'][0]
                resp = client.get('/api/v1/auth/telegram/oidc/callback', params={'code': 'SENSITIVE_LOGIN_CODE', 'state': state}, follow_redirects=False)
                assert resp.status_code == expected, (i, resp.status_code, resp.text)
                if expected == 401:
                    assert resp.json()['detail'] == 'No se pudo verificar el acceso con Telegram'
                else:
                    assert client.get('/api/v1/auth/session').status_code == 200
            logs = log_stream.getvalue()
            if marker:
                assert marker in logs, (marker, logs)
            assert 'SENSITIVE' not in logs
            assert 'secret-for-test' not in logs
            assert 'offline.jwt' not in logs
finally:
    logger.removeHandler(handler)

print('OIDC callback diagnostic regression tests: 5 passed')
