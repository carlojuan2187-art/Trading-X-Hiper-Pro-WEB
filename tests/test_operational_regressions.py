"""Offline regression tests; no credentials, network or exchange orders."""
import ast
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import threading
import time
import types
import unittest
from unittest.mock import patch

from app.emergency_exit import close_verified
from app.scan_coverage import Coverage
from app.signal_cache import SignalCache
from app.pending_setup import find_pending_reset
from app.trading_quality import meets_min_rr, absolute_risk_reward

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, namespace):
    """Load production functions without importing DB/SDK side effects."""
    tree = ast.parse((ROOT / path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(nodes) == len(names), names
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, 'exec'), namespace)
    return namespace


class CoverageTests(unittest.TestCase):
    def test_no_fill_rounds_cover_all_symbols(self):
        tracker = Coverage()
        rows = [{'symbol': str(i)} for i in range(25)]
        seen = set()
        for _ in range(4):
            for row in tracker.order(rows)[:8]:
                seen.add(row['symbol'])
                tracker.mark(row['symbol'])
        self.assertEqual(len(seen), 25)

    def test_unseen_symbols_are_prioritized(self):
        tracker = Coverage()
        tracker.mark('BTC')
        self.assertEqual(tracker.order([{'symbol': 'BTC'}, {'symbol': 'ETH'}])[0]['symbol'], 'ETH')


class EmergencyTests(unittest.TestCase):
    def run_close(self, sizes, response=None):
        reads = iter(sizes)
        events = []
        def close(size):
            events.append(('close', size))
            return response
        result = close_verified(read_size=lambda: next(reads), close_order=close,
            cancel_orders=lambda: events.append(('cancel',)),
            protect=lambda size: events.append(('protect', size)), attempts=3)
        return result, events

    def test_rejection_never_cancels_protection(self):
        result, events = self.run_close([1] * 6, {'status': 'err'})
        self.assertFalse(result['closed'])
        self.assertNotIn(('cancel',), events)
        self.assertEqual(sum(x[0] == 'protect' for x in events), 3)

    def test_partial_fill_retries_only_residual(self):
        result, events = self.run_close([1, .4, .4, 0])
        self.assertTrue(result['closed'])
        self.assertEqual([x[1] for x in events if x[0] == 'close'], [1, .4])
        self.assertEqual(events[-1], ('cancel',))

    def test_unknown_is_not_flat(self):
        result, events = self.run_close([None] * 3)
        self.assertFalse(result['closed'])
        self.assertEqual(events, [])

    def test_nan_is_not_flat(self):
        result, events = self.run_close([float('nan')] * 3)
        self.assertFalse(result['closed'])
        self.assertEqual(events, [])

    def test_already_flat_does_not_send_order(self):
        result, events = self.run_close([0])
        self.assertTrue(result['closed'])
        self.assertEqual(events, [('cancel',)])


class CacheTests(unittest.TestCase):
    def test_concurrent_users_compute_once_and_do_not_share_mutations(self):
        cache = SignalCache()
        calls = []
        def compute():
            calls.append(1)
            time.sleep(.01)
            return {'signal': True, 'nested': {'value': 1}}
        with ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(lambda _: cache.evaluate('BTC', (1, 1), compute), range(8)))
        self.assertEqual(len(calls), 1)
        outputs[0]['nested']['value'] = 99
        self.assertEqual(cache.evaluate('BTC', (1, 1), compute)['nested']['value'], 1)

    def test_new_bar_recomputes(self):
        cache = SignalCache(); calls = []
        def compute():
            calls.append(1); return {'signal': False, 'reason': 'ATR_TOO_LOW'}
        cache.evaluate('BTC', 1, compute); cache.evaluate('BTC', 2, compute)
        self.assertEqual(len(calls), 2)

    def test_data_errors_are_retried(self):
        cache = SignalCache(); calls = []
        def compute():
            calls.append(1); return {'signal': False, 'reason': 'BAD_HIGHER_TF_DATA'}
        cache.evaluate('BTC', 1, compute); cache.evaluate('BTC', 1, compute)
        self.assertEqual(len(calls), 2)


class PrecisionAndSizingTests(unittest.TestCase):
    def test_rounding_regression_uses_full_precision(self):
        ns = {'Tuple': tuple, 'MTF_SL_MIN_PCT': .0045, 'MTF_RR_MIN': 1.15,
              'MTF_RR_MAX': 1.34, 'MTF_TP_MIN_PCT': .006, 'MTF_TP_MAX_PCT': .018,
              'ENTRY_RR_RESERVE': 0.0}
        functions('app/strategies/breakout_reset.py', {'_clamp', '_compute_simple_mtf_fixed_tp_pct'}, ns)
        tp, rr = ns['_compute_simple_mtf_fixed_tp_pct'](score=69, atr_pct=.002, sl_pct=.006801)
        self.assertTrue(meets_min_rr(tp / .006801, 1.15))
        self.assertFalse(meets_min_rr(1.1499, 1.15))

    def test_nonfinite_prices_rejected(self):
        self.assertFalse(absolute_risk_reward(direction='long', entry_price=float('nan'), stop_price=99, target_price=102)['ok'])

    def sizing(self):
        import math
        ns = {'math': math, 'POSITION_PERCENT': 1.0, 'MAX_TRADE_MARGIN_USDC': 0}
        return functions('app/risk.py', {'validate_trade_conditions'}, ns)

    def test_full_configured_three_usdc_margin(self):
        ns = self.sizing(); ns['MAX_TRADE_MARGIN_USDC'] = 3
        for balance in (3, 5.52, 8.76, 100):
            self.assertEqual(ns['validate_trade_conditions'](balance, .8),
                             {'ok': True, 'position_size': 3})

    def test_balance_and_percentage_define_margin(self):
        ns = self.sizing(); fn = ns['validate_trade_conditions']
        self.assertEqual(fn(8.76, .8)['position_size'], 8.76)
        ns['MAX_TRADE_MARGIN_USDC'] = 3
        self.assertEqual(fn(2, .8)['position_size'], 2)
        ns['POSITION_PERCENT'] = .5
        self.assertEqual(fn(4, .8)['position_size'], 2)

    def test_invalid_capital_rejected(self):
        fn = self.sizing()['validate_trade_conditions']
        for balance in (None, float('nan'), float('inf'), 0, -1):
            self.assertFalse(fn(balance, .8)['ok'])

    def test_margin_not_affected_by_signal_strength(self):
        ns = self.sizing(); ns['MAX_TRADE_MARGIN_USDC'] = 3
        for strength in (0, .5, 1):
            self.assertEqual(ns['validate_trade_conditions'](100, strength)['position_size'], 3)


class PendingTests(unittest.TestCase):
    def data(self):
        return dict(direction='long', o=[100]*10, h=[100.5]*10, l=[99.8]*10,
                    c=[100.2]*9+[100.6], ema20=[100]*10, ema50=[99.8]*10,
                    atr=1, max_age=3)

    def test_confirmation_uses_closed_candle_and_keeps_structural_stop(self):
        ok, reason, diag = find_pending_reset(**self.data())
        self.assertTrue(ok)
        self.assertEqual(diag['structural_extreme'], 99.8)

    def test_broken_reset_is_rejected(self):
        data = self.data(); data['l'][-1] = 98
        self.assertFalse(find_pending_reset(**data)[0])

    def test_extended_confirmation_is_not_chased(self):
        data = self.data(); data['c'][-1] = 102
        self.assertEqual(find_pending_reset(**data)[1], 'TOO_EXTENDED_AFTER_CONFIRM')

    def test_short_is_symmetric(self):
        data = self.data(); data['direction'] = 'short'
        data.update(o=[200-x for x in data['o']], h=[200-x for x in self.data()['l']],
                    l=[200-x for x in self.data()['h']], c=[200-x for x in data['c']],
                    ema20=[200-x for x in data['ema20']], ema50=[200-x for x in data['ema50']])
        self.assertTrue(find_pending_reset(**data)[0])


class VerifiedPositionTests(unittest.TestCase):
    def read(self, response):
        ns = {'Optional': __import__('typing').Optional, 'get_user_wallet': lambda _: 'wallet',
              'norm_coin': lambda x: x.upper(), 'make_request': lambda *a: response}
        return functions('app/hyperliquid_client.py', {'get_position_size_verified'}, ns)['get_position_size_verified'](1, 'BTC')

    def test_unavailable_or_malformed_is_unknown(self):
        for response in [None, {}, {'_http_error': True}, {'assetPositions': [{}]},
                         {'assetPositions': [{'position': {'coin': 'BTC', 'szi': 'nan'}}]}]:
            self.assertIsNone(self.read(response))

    def test_confirmed_flat_and_short(self):
        self.assertEqual(self.read({'assetPositions': []}), 0)
        self.assertEqual(self.read({'assetPositions': [{'position': {'coin': 'BTC', 'szi': '-2'}}]}), -2)


class RiskPersistenceTests(unittest.TestCase):
    def namespace(self, store):
        ns = {'deque': deque, 'datetime': datetime, 'time': time, 'log': lambda *a: None,
              'database_module': types.SimpleNamespace(settings_col=store),
              '_risk_lock': threading.Lock(), '_risk_loaded': False, '_risk_dirty': False,
              '_risk_pending_closes': [], '_global_seen_opportunities': deque(maxlen=2000),
              '_processed_risk_closes': deque(maxlen=4000), '_user_risk_state': {},
              '_global_risk_state': {'results': deque(), 'consec_losses': 0, 'cooldown_until': 0},
              'USER_RISK_WINDOW': 10, 'GLOBAL_RISK_WINDOW': 20,
              'USER_RISK_PF_WINDOW': 6, 'GLOBAL_RISK_PF_WINDOW': 10,
              'USER_RISK_LOSS_COOLDOWN_SECONDS': 1200, 'USER_RISK_MAX_CONSEC_LOSSES': 2,
              'USER_RISK_COOLDOWN_SECONDS': 1800, 'USER_RISK_MIN_PF': .95,
              'USER_RISK_PF_COOLDOWN_SECONDS': 1800, 'GLOBAL_RISK_MAX_CONSEC_LOSSES': 6,
              'GLOBAL_RISK_COOLDOWN_SECONDS': 3600, 'GLOBAL_RISK_MIN_PF': .9,
              'GLOBAL_RISK_PF_COOLDOWN_SECONDS': 3600}
        return functions('app/trading_engine.py', {'_risk_pf', '_risk_load_locked', '_risk_save_locked',
            '_risk_flush_locked', '_risk_record_close', '_apply_risk_close', '_risk_governor_allows_new_entries'}, ns)

    def test_persists_cooldown_and_deduplicates_shared_opportunity(self):
        class Store:
            doc = None
            def find_one(self, query): return deepcopy(self.doc)
            def update_one(self, query, update, **kwargs): self.doc = deepcopy(update['$set'])
        store = Store(); ns = self.namespace(store)
        ns['_risk_record_close'](1, -1, 'same-signal', -.01, 'close1')
        ns['_risk_record_close'](2, -2, 'same-signal', -.01, 'close2')
        ns['_risk_record_close'](1, -1, 'same-signal', -.01, 'close1')
        self.assertEqual(len(ns['_global_risk_state']['results']), 1)
        self.assertEqual(len(ns['_user_risk_state'][1]['results']), 1)
        restarted = self.namespace(store)
        self.assertFalse(restarted['_risk_governor_allows_new_entries'](1)[0])

    def test_unavailable_persistence_blocks_entries(self):
        class Store:
            def find_one(self, _): raise OSError('offline')
        ns = self.namespace(Store())
        self.assertEqual(ns['_risk_governor_allows_new_entries'](1), (False, 'RISK_STATE_UNAVAILABLE'))

    def test_pf_uses_last_six_not_ten(self):
        class Store:
            def find_one(self, _): return None
            def update_one(self, *a, **kw): pass
        ns = self.namespace(Store())
        # Four large old losses, then six wins. Final PF must use only six wins.
        for i, profit in enumerate([-100]*4 + [1]*6):
            ns['_risk_record_close'](1, profit, str(i), profit, str(i))
        self.assertFalse(ns['_user_risk_state'][1]['cooldown_reason'].startswith('USER_PF_0.01'))
        # Last close must not extend the existing PF cooldown once old losses leave the window.
        self.assertEqual(list(ns['_user_risk_state'][1]['results'])[-6:], [1]*6)


class IOCContractTests(unittest.TestCase):
    def namespace(self):
        from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation
        orders=[]
        class Signer:
            def __init__(self, key): pass
            def sign(self,*a,**kw): return {}
        ns={'Optional':__import__('typing').Optional,'Decimal':Decimal,'ROUND_DOWN':ROUND_DOWN,
            'ROUND_UP':ROUND_UP,'InvalidOperation':InvalidOperation,'time':time,
            'get_user_wallet':lambda _: 'test-wallet','get_user_private_key':lambda _: 'test-key',
            'PrivateKeyDecryptError':ValueError,'norm_coin':lambda x:x,'get_asset_index':lambda _:0,
            'get_sz_decimals':lambda _:4,'get_tick_size':lambda _:.01,'FORCE_ISOLATED':True,
            'FORCE_LEVERAGE':10,'_set_isolated_leverage':lambda **k:{'ok':True},
            'must_log':lambda *a:None,'safe_log':lambda *a:None,'get_price':lambda _:100,
            '_get_execution_book_metrics':lambda *a,**k:{'best_bid':99.99,'best_ask':100.01,'spread_bps':2,'depth_notional':100000},
            'EXECUTION_BOOK_IMPACT_BPS':25,'EXECUTION_IOC_RETRY_STEP_BPS':8,
            'EXECUTION_MIN_BOOK_COVERAGE':1.1,'EXECUTION_MAX_SPREAD_BPS':20,
            '_default_slippage':lambda *a,**k:.0028,'_clamp_slippage':lambda x,**k:x,
            '_MIN_TRADE_NOTIONAL':10,'_MIN_TRADE_BUFFER':.2,'HyperliquidSigner':Signer,
            'make_request':lambda path,payload:orders.append(payload) or {},
            '_detect_fill':lambda r:{'status':'FILLED','filled_sz':1}}
        functions('app/hyperliquid_client.py',{'place_market_order','_format_price_tick','_format_price_side',
            '_format_size','_format_size_round','_strip_trailing_zeros','_to_decimal','_quant'},ns)
        return ns,orders

    def test_long_tick_rounding_respects_absolute_rr_cap(self):
        ns,orders=self.namespace()
        result=ns['place_market_order'](1,'SOL','buy',1,entry_price_limit=100.025)
        self.assertTrue(result['filled'])
        self.assertEqual(float(orders[0]['action']['orders'][0]['p']),100.02)

    def test_short_tick_rounding_respects_absolute_rr_floor(self):
        ns,orders=self.namespace()
        result=ns['place_market_order'](1,'SOL','sell',1,entry_price_limit=99.975)
        self.assertTrue(result['filled'])
        self.assertEqual(float(orders[0]['action']['orders'][0]['p']),99.98)

    def test_dust_reduce_only_does_not_require_new_position_minimum_or_leverage_change(self):
        ns,orders=self.namespace()
        ns['_set_isolated_leverage']=lambda **k: (_ for _ in ()).throw(AssertionError('must not change leverage to close'))
        result=ns['place_market_order'](1,'SOL','sell',.001,reduce_only=True)
        self.assertTrue(result['filled'])
        self.assertTrue(orders[0]['action']['orders'][0]['r'])

    def test_rr_limit_matches_long_and_short_minimum(self):
        from app.trading_quality import rr_entry_price_limit
        for direction,stop,target in [('long',99,101.2),('short',101,98.8)]:
            entry=rr_entry_price_limit(stop_price=stop,target_price=target,minimum_rr=1.15)
            self.assertAlmostEqual(absolute_risk_reward(direction=direction,entry_price=entry,
                stop_price=stop,target_price=target)['rr'],1.15)

    def test_production_signer_contract_is_preserved(self):
        import sys
        from typing import Any, Optional
        called=[]
        signing=types.ModuleType('hyperliquid.utils.signing')
        signing.sign_l1_action=lambda *args: called.append(args) or {'signature':'test'}
        account_module=types.ModuleType('eth_account')
        account_module.Account=types.SimpleNamespace(from_key=lambda value: 'test-account')
        ns={'Any':Any,'Optional':Optional,'HYPER_BASE_URL':'https://api.hyperliquid.xyz'}
        functions('app/hyperliquid_client.py',{'HyperliquidSigner'},ns)
        with patch.dict(sys.modules,{'hyperliquid.utils.signing':signing,'eth_account':account_module}):
            signer=ns['HyperliquidSigner']('test-key')
            result=signer.sign({'type':'order'},1000)
        self.assertEqual(result,{'signature':'test'})
        self.assertEqual(called[0],('test-account',{'type':'order'},None,1000,61000,True))
