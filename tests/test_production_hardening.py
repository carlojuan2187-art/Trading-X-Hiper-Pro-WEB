"""Offline regression checks for concrete production audit findings."""
import asyncio
import base64
import hashlib
import hmac
import json
import threading
import types
import unittest
from typing import Optional, Dict, Any, List, Set
from urllib.parse import urlencode, parse_qsl
from unittest.mock import Mock
from test_operational_regressions import functions
from app.cycle_workers import CycleWorkers


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_keeps_result_and_does_not_start_second_worker(self):
        workers = CycleWorkers()
        entered, release = threading.Event(), threading.Event()
        calls = []
        def compute(user):
            calls.append(user); entered.set(); release.wait(2)
            return {'event': 'OPEN', 'id': user}
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await workers.run(7, compute, .01)
            self.assertTrue(entered.is_set())
            release.set()
            self.assertEqual(await workers.run(7, compute, 1), {'event': 'OPEN', 'id': 7})
            self.assertEqual(calls, [7])
            self.assertFalse(workers._pending)
        finally:
            release.set()

    async def test_worker_exception_is_observed_and_allows_later_retry(self):
        workers = CycleWorkers()
        def fail(_): raise RuntimeError('offline')
        with self.assertRaises(RuntimeError): await workers.run(7, fail, 1)
        self.assertEqual(await workers.run(7, lambda _: 'ok', 1), 'ok')


class ScannerTests(unittest.TestCase):
    def namespace(self, result):
        ns = dict(Set=Set, List=List, _LAST_GOOD_RESULTS=[{'symbol':'OLD'}],
                  _LAST_UPDATE_TS=99, MAX_CACHE_AGE=300,
                  time=types.SimpleNamespace(time=lambda:100),safe_log=lambda *a:None,
                  _get_live_results=lambda _:result)
        return functions('app/market_scanner.py', {'_refresh_scored_results'}, ns)

    def test_valid_empty_scan_clears_old_candidates(self):
        ns=self.namespace([])
        self.assertEqual(ns['_refresh_scored_results'](set()), [])
        self.assertEqual(ns['_LAST_GOOD_RESULTS'], [])
        self.assertEqual(ns['_LAST_UPDATE_TS'], 100)

    def test_unavailable_scan_uses_only_unexpired_fallback(self):
        ns=self.namespace(None)
        self.assertEqual(ns['_refresh_scored_results'](set()), [{'symbol':'OLD'}])
        ns['_LAST_UPDATE_TS']=-500
        self.assertEqual(ns['_refresh_scored_results'](set()), [])

    def test_cached_empty_scan_does_not_refresh_per_user(self):
        refresh=Mock(return_value=[])
        ns=dict(Set=Set,List=List,_scan_lock=threading.Lock(),_LAST_GOOD_RESULTS=[],
                _LAST_UPDATE_TS=99,_SCAN_REFRESH_SECONDS=5,
                time=types.SimpleNamespace(time=lambda:100),_refresh_scored_results=refresh)
        functions('app/market_scanner.py',{'_get_scored_results'},ns)
        self.assertEqual(ns['_get_scored_results'](set()),[])
        refresh.assert_not_called()


class HTTPTests(unittest.TestCase):
    def test_each_request_carries_its_own_timeout(self):
        response=types.SimpleNamespace(status_code=200,json=lambda:{'ok':True})
        client=types.SimpleNamespace(post=Mock(return_value=response))
        ns=dict(Optional=Optional,REQUEST_TIMEOUT=20,HYPER_BASE_URL='https://offline.invalid',
                _get_http_client=lambda _:client,time=types.SimpleNamespace(sleep=lambda _:None),safe_log=lambda *a:None)
        functions('app/hyperliquid_client.py',{'make_request'},ns)
        ns['make_request']('/info',{},timeout=4)
        ns['make_request']('/info',{},timeout=20)
        self.assertEqual([c.kwargs['timeout'] for c in client.post.call_args_list],[4,20])


class AuthTests(unittest.TestCase):
    def namespace(self):
        class HTTPException(Exception):
            def __init__(self,status_code,detail):self.status_code=status_code;self.detail=detail
        ns=dict(Any=Any,base64=base64,hashlib=hashlib,hmac=hmac,json=json,parse_qsl=parse_qsl,
                time=types.SimpleNamespace(time=lambda:1000),HTTPException=HTTPException,
                status=types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401),TELEGRAM_BOT_TOKEN='test',
                ADMIN_TELEGRAM_ID=1,WEBAPP_SESSION_SECRET='test-secret',WEBAPP_SESSION_TTL_SECONDS=100,
                WEBAPP_INITDATA_MAX_AGE_SECONDS=300)
        functions('app/api/security.py',{'_b64url_encode','_b64url_decode','verify_telegram_init_data',
                  'create_session_token','verify_session_token'},ns)
        return ns

    def signed_init(self,user,auth_date=1000):
        fields={'auth_date':str(auth_date),'user':json.dumps(user)}
        key=hmac.new(b'WebAppData',b'test',hashlib.sha256).digest()
        fields['hash']=hmac.new(key,'\n'.join(f'{k}={v}' for k,v in sorted(fields.items())).encode(),hashlib.sha256).hexdigest()
        return urlencode(fields)

    def test_valid_telegram_identity(self):
        ns=self.namespace()
        self.assertEqual(ns['verify_telegram_init_data'](self.signed_init({'id':2}))['user_id'],2)

    def test_signed_bad_shape_and_future_date_return_401(self):
        ns=self.namespace()
        for payload in (self.signed_init([]),self.signed_init({'id':'bad'}),self.signed_init({'id':2},1040)):
            with self.assertRaises(ns['HTTPException']) as caught:ns['verify_telegram_init_data'](payload)
            self.assertEqual(caught.exception.status_code,401)

    def test_stale_admin_claim_does_not_grant_admin(self):
        ns=self.namespace()
        token=ns['create_session_token']({'user_id':2,'is_admin':True})
        self.assertFalse(ns['verify_session_token'](token)['is_admin'])


class WorkerTimeoutFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_raised_inside_worker_does_not_latch_forever(self):
        workers=CycleWorkers()
        def fail(_): raise TimeoutError('inside worker')
        with self.assertRaises(TimeoutError): await workers.run(7,fail,1)
        self.assertFalse(workers._pending)
        self.assertEqual(await workers.run(7,lambda _: 'recovered',1),'recovered')
