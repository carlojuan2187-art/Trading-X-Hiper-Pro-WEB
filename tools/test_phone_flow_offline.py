"""Pruebas offline del flujo de teléfono sin depender de un Mongo externo.

Mock de phonenumbers limitado a los números de prueba (no valida el catálogo real).
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from datetime import datetime, timedelta, timezone


class DuplicateKeyError(Exception):
    pass


class FakeCollection:
    def __init__(self, field=None):
        self.rows = {}
        self.field = field

    @staticmethod
    def matches(doc, query):
        for k, v in query.items():
            actual = doc.get(k)
            if isinstance(v, dict):
                for op, limit in v.items():
                    if op == '$gt' and not (actual > limit):
                        return False
                    if op == '$lte' and not (actual <= limit):
                        return False
            elif actual != v:
                return False
        return True

    def create_index(self, *_, **__):
        return None

    def find_one(self, query):
        return next((dict(v) for v in self.rows.values() if self.matches(v, query)), None)

    def insert_one(self, data):
        if data['_id'] in self.rows:
            raise DuplicateKeyError()
        if self.field and any(d.get(self.field) == data.get(self.field) for d in self.rows.values()):
            raise DuplicateKeyError()
        self.rows[data['_id']] = dict(data)

    def update_one(self, query, patch):
        for key, row in self.rows.items():
            if self.matches(row, query):
                if '$set' in patch:
                    row.update(patch['$set'])
                if '$unset' in patch:
                    for k in patch['$unset']:
                        row.pop(k, None)
                if '$inc' in patch:
                    for k, v in patch['$inc'].items():
                        row[k] = row.get(k, 0) + v
                return types.SimpleNamespace(modified_count=1)
        return types.SimpleNamespace(modified_count=0)

    def find_one_and_update(self, query, patch, return_document=None):
        doc = self.find_one(query)
        if doc:
            self.update_one(query, patch)
            return self.find_one({'_id': doc['_id']})
        return None


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, item):
        if item not in self.collections:
            self.collections[item] = FakeCollection('telegram_user_id' if item == 'phone_credentials' else None)
        return self.collections[item]


def init_module():
    database = types.ModuleType('app.database')
    database.db = FakeDB()
    database.users_col = database.db['users']
    database.create_user = lambda uid, username: database.users_col.rows.setdefault(uid, {'_id': uid, 'user_id': uid})
    database.set_referrer = lambda uid, ref: None
    sys.modules['app.database'] = database

    pymongo = types.ModuleType('pymongo')
    pymongo.ReturnDocument = types.SimpleNamespace(AFTER=True)
    exceptions = types.ModuleType('pymongo.errors')
    exceptions.DuplicateKeyError = DuplicateKeyError
    sys.modules['pymongo'] = pymongo
    sys.modules['pymongo.errors'] = exceptions

    pn = types.ModuleType('phonenumbers')
    class PhoneError(Exception):
        pass
    pn.NumberParseException = PhoneError
    pn.PhoneNumberFormat = types.SimpleNamespace(E164='E164')
    def parse(raw, region):
        raw = raw.strip()
        if raw.startswith('+'):
            digits = '+' + ''.join(c for c in raw[1:] if c.isdigit())
        elif region == 'US':
            digits = '+1' + ''.join(c for c in raw if c.isdigit())
        else:
            raise PhoneError()
        if not digits[1:].isdigit():
            raise PhoneError()
        return types.SimpleNamespace(number=digits)
    pn.parse = parse
    pn.is_valid_number = lambda n: n.number.startswith('+1') and len(n.number) == 12
    pn.format_number = lambda n, mode: n.number
    pn.region_code_for_number = lambda n: 'US'
    sys.modules['phonenumbers'] = pn
    sys.modules.pop('app.auth.phone_login', None)
    return importlib.import_module('app.auth.phone_login')


class PhoneFlowTests(unittest.TestCase):
    def setUp(self):
        self.auth = init_module()
        self.auth.init_indexes()

    def start(self, mode='register', password='LongStrongPassword!23', phone='+17875551234'):
        return self.auth.begin_flow(phone, password, mode, '', 'tester')

    def test_e164_validation(self):
        self.assertEqual(self.auth.normalize_phone('+17875551234'), '+17875551234')
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.normalize_phone('nonsense')

    def test_contact_comparison(self):
        self.assertTrue(self.auth.normalized_contact('7875551234', '+17875551234'))
        self.assertFalse(self.auth.normalized_contact('+17875559999', '+17875551234'))

    def test_register_requires_telegram(self):
        flow, secret = self.start()
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.finish_flow(flow, secret)
        self.assertFalse(self.auth.CREDENTIALS.rows)
        self.assertEqual(self.auth.confirm_contact(flow, 505, '+17875551234'), 'ok')
        self.assertEqual(self.auth.finish_flow(flow, secret), 505)
        self.assertEqual(self.auth.CREDENTIALS.find_one({'_id': '+17875551234'})['telegram_user_id'], 505)

    def test_browser_secret_is_not_bot_link(self):
        flow, secret = self.start()
        self.assertNotIn(secret, flow)
        self.assertNotIn(secret, str(self.auth.FLOWS.find_one({'_id': flow})))
        self.assertEqual(self.auth.confirm_contact(flow, 505, '+17875551234'), 'ok')
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.finish_flow(flow, 'fake-browser-secret-other-user-1234567890123')

    def test_wrong_number_does_not_verify(self):
        flow, secret = self.start()
        self.assertEqual(self.auth.confirm_contact(flow, 505, '+17875559999'), 'mismatch')
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.finish_flow(flow, secret)

    def test_login_wrong_password(self):
        flow, secret = self.start()
        self.auth.confirm_contact(flow, 505, '+17875551234')
        self.auth.finish_flow(flow, secret)
        with self.assertRaises(self.auth.PhoneAuthError) as wrong:
            self.start('login', 'incorrect-password-xxxxxxxx')
        self.assertEqual(wrong.exception.status, 401)

    def test_login_must_reconfirm_telegram_user_id(self):
        flow, secret = self.start()
        self.auth.confirm_contact(flow, 505, '+17875551234')
        self.auth.finish_flow(flow, secret)
        flow2, secret2 = self.start('login')
        self.assertEqual(self.auth.confirm_contact(flow2, 404, '+17875551234'), 'different_account')
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.finish_flow(flow2, secret2)
        self.assertEqual(self.auth.confirm_contact(flow2, 505, '+17875551234'), 'ok')
        self.assertEqual(self.auth.finish_flow(flow2, secret2), 505)

    def test_used_flow_cannot_be_replayed(self):
        flow, secret = self.start()
        self.auth.confirm_contact(flow, 505, '+17875551234')
        self.auth.finish_flow(flow, secret)
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.finish_flow(flow, secret)

    def test_expiration_blocks_verification(self):
        flow, secret = self.start()
        self.auth.FLOWS.rows[flow]['expires_at'] = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.auth.confirm_contact(flow, 505, '+17875551234'), 'expired')
        with self.assertRaises(self.auth.PhoneAuthError):
            self.auth.finish_flow(flow, secret)


if __name__ == '__main__':
    unittest.main(verbosity=2)
