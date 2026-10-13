"""Pruebas offline de validación antes de entregar updates al bot."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.telegram_webhook_security import (
    MAX_UPDATE_BYTES, decode_update, derive_webhook_secret, valid_webhook_secret,
)


class TestWebhookSecurity(unittest.TestCase):
    def test_stable_and_separate_secret(self):
        secret = derive_webhook_secret('a' * 48)
        self.assertEqual(secret, derive_webhook_secret('a' * 48))
        self.assertNotEqual(secret, derive_webhook_secret('b' * 48))
        self.assertNotEqual(secret, 'a' * 48)
        self.assertEqual(len(secret), 64)

    def test_weak_secret_fails(self):
        with self.assertRaises(ValueError):
            derive_webhook_secret('tiny')

    def test_forbidden_without_exact_header(self):
        value = derive_webhook_secret('x' * 48)
        self.assertTrue(valid_webhook_secret(value, value))
        self.assertFalse(valid_webhook_secret(None, value))
        self.assertFalse(valid_webhook_secret(value.upper(), value))

    def test_accept_json(self):
        self.assertEqual(decode_update(b'{"update_id":123,"message":{}}')['update_id'], 123)

    def test_bad_json(self):
        for payload in [b'{}', b'[]', b'{', b'null', b'']:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    decode_update(payload)

    def test_huge_payload(self):
        with self.assertRaises(ValueError):
            decode_update(b' ' * (MAX_UPDATE_BYTES + 1))


if __name__ == '__main__':
    unittest.main()
