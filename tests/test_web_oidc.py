"""Offline regression suite for browser OIDC, signed state and cookie CSRF."""
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'offline-test-bot')
os.environ.setdefault('ADMIN_TELEGRAM_ID', '1')
os.environ.setdefault('MONGO_URL', 'mongodb://offline.invalid')
os.environ.setdefault('WEBAPP_SESSION_SECRET', 'offline-test-session-secret-independent')

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api import dependencies
from app.api.security import create_session_token
from app.api.telegram_oidc import (
    CSRF_COOKIE, SESSION_COOKIE, create_login_flow, verify_login_flow,
    verify_telegram_id_token,
)


class TelegramOIDCStateTests(unittest.TestCase):
    def test_valid_state_and_pkce(self):
        public, cookie = create_login_flow(now=1_800_000_000)
        signed = verify_login_flow(cookie, public['state'], now=1_800_000_180)
        self.assertEqual(signed['state'], public['state'])
        self.assertEqual(signed['nonce'], public['nonce'])
        self.assertGreater(len(public['code_challenge']), 35)
        self.assertNotIn(signed['verifier'], public.values())

    def test_referrer_is_bound_in_signed_flow(self):
        public, cookie = create_login_flow(now=1_800_000_000, referrer_id=1234567)
        self.assertEqual(verify_login_flow(cookie, public['state'], now=1_800_000_010)['referrer_id'], 1234567)

    def test_reject_tampered_state(self):
        public, cookie = create_login_flow(now=1_800_000_000)
        with self.assertRaises(ValueError):
            verify_login_flow(cookie[:-1] + ('x' if cookie[-1] != 'x' else 'y'), public['state'], now=1_800_000_020)
        with self.assertRaises(ValueError):
            verify_login_flow(cookie, 'incorrect-state', now=1_800_000_020)

    def test_reject_old_or_future_state(self):
        public, cookie = create_login_flow(now=1_800_000_000)
        with self.assertRaises(ValueError):
            verify_login_flow(cookie, public['state'], now=1_800_000_400)
        with self.assertRaises(ValueError):
            verify_login_flow(cookie, public['state'], now=1_799_999_900)

    def test_oidc_signature_and_claims_are_verified(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {'iss': 'https://oauth.telegram.org', 'aud': '1234567', 'sub': '1234567',
                  'id': 998877, 'preferred_username': 'fixture', 'iat': 1_800_000_000,
                  'exp': 1_800_003_600, 'nonce': 'nonce-fixture'}
        mock_jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key()))
        import time
        claims['iat'] = int(time.time()) - 30
        claims['exp'] = int(time.time()) + 3600
        signed_jwt = jwt.encode(claims, key, algorithm='RS256')
        with patch('app.api.telegram_oidc._get_jwks_client', return_value=mock_jwks):
            out = verify_telegram_id_token(signed_jwt, client_id='1234567', nonce='nonce-fixture')
            self.assertEqual(out['user_id'], 998877)
            with self.assertRaises(ValueError):
                verify_telegram_id_token(signed_jwt, client_id='1234567', nonce='wrong-nonce')
            with self.assertRaises(jwt.InvalidAudienceError):
                verify_telegram_id_token(signed_jwt, client_id='other-client', nonce='nonce-fixture')


class CookieSessionCSRFTests(unittest.TestCase):
    @staticmethod
    def make_request(method='GET', *, origin='https://trading.example', csrf_header='', csrf_cookie=''):
        return SimpleNamespace(method=method, headers={'origin': origin, 'x-csrf-token': csrf_header},
                               cookies={SESSION_COOKIE: create_session_token({'user_id': 10, 'is_admin': False}),
                                        CSRF_COOKIE: csrf_cookie})

    def test_cookie_session_get_ok(self):
        request = self.make_request()
        self.assertEqual(dependencies.require_session(request, None)['user_id'], 10)

    def test_cookie_session_post_requires_origin_and_csrf(self):
        with patch.object(dependencies, 'WEB_BASE_URL', 'https://trading.example'):
            csrf = 'safe_csrf_fixture_with_enough_entropy_chars'
            request = self.make_request('POST', csrf_header=csrf, csrf_cookie=csrf)
            self.assertEqual(dependencies.require_session(request, None)['user_id'], 10)
            for bad in (self.make_request('POST'), self.make_request('POST', csrf_header='wrong', csrf_cookie=csrf),
                        self.make_request('POST', origin='https://attacker.example', csrf_header=csrf, csrf_cookie=csrf)):
                with self.assertRaises(HTTPException) as exc:
                    dependencies.require_session(bad, None)
                self.assertEqual(exc.exception.status_code, 403)

    def test_legacy_bearer_works_without_csrf(self):
        token = create_session_token({'user_id': 10, 'is_admin': False})
        request = SimpleNamespace(method='POST', headers={}, cookies={})
        auth = HTTPAuthorizationCredentials(scheme='Bearer', credentials=token)
        self.assertEqual(dependencies.require_session(request, auth)['user_id'], 10)


if __name__ == '__main__':
    unittest.main()
