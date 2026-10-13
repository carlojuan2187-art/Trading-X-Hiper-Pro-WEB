"""Regression scenarios from the October audit; no exchange connectivity."""
import math
import types
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch
from typing import Any, Optional, Dict
from test_operational_regressions import functions
from app.exit_guard import exit_guard_reason
from app.trade_accounting import summarize_trade_fills
from test_strategy_contracts import mtf, StrategyRouter, market_context
from app.strategies import liquidity_sweep_reversal as sweep


class AccountingTests(unittest.TestCase):
    def rows(self):
        return [dict(coin='SOL',time=100,tid=1,oid=10,dir='Open Long',px='100',sz='2',
                     closedPnl='0',fee='.09',feeToken='USDC',startPosition='0'),
                dict(coin='SOL',time=200,tid=2,oid=20,dir='Close Long',px='101',sz='1',
                     closedPnl='1',fee='.04545',feeToken='USDC',startPosition='2'),
                dict(coin='SOL',time=300,tid=3,oid=21,dir='Close Long',px='102',sz='1',
                     closedPnl='2',fee='.0459',feeToken='USDC',startPosition='1')]

    def run_ledger(self, rows, **kw):
        return summarize_trade_fills(rows,coin='SOL',opened_after_ms=100,
                                     expected_qty=2,direction=kw.pop('direction','long'),**kw)

    def test_all_close_orders_and_open_fees_are_included(self):
        out=self.run_ledger(self.rows())
        self.assertEqual(out['gross_pnl'],3)
        self.assertAlmostEqual(out['fees'],.18135)
        self.assertAlmostEqual(out['net_pnl'],2.81865)
        self.assertEqual(out['exit_price'],101.5)
        self.assertEqual(out['close_order_ids'],['20','21'])
        self.assertFalse(out['funding_included'])

    def test_partial_settlement_is_not_a_completed_trade(self):
        self.assertEqual(self.run_ledger(self.rows()[:2])['fills'],0)

    def test_missing_opening_fees_fail_closed(self):
        self.assertEqual(self.run_ledger(self.rows()[1:])['fills'],0)

    def test_duplicate_fill_id_not_double_counted(self):
        rows=self.rows();rows.append(deepcopy(rows[-1]))
        self.assertAlmostEqual(self.run_ledger(rows)['net_pnl'],2.81865)

    def test_conflicting_duplicates_rejected(self):
        rows=self.rows();rows.append({**rows[-1],'fee':'50'})
        self.assertEqual(self.run_ledger(rows)['reason'],'CONFLICTING_FILL_ID')

    def test_prior_trade_and_other_symbols_excluded(self):
        rows=self.rows();rows.extend([{**rows[0],'time':50},{**rows[0],'coin':'BTC'}])
        self.assertEqual(self.run_ledger(rows)['gross_pnl'],3)

    def test_new_trade_cannot_be_mixed_in(self):
        rows=self.rows();rows.append({**rows[0],'time':400,'tid':4})
        self.assertEqual(self.run_ledger(rows)['reason'],'MULTIPLE_POSITION_LIFECYCLES')

    def test_invalid_values_and_currency_rejected(self):
        for key,value in [('fee','nan'),('px','inf'),('sz','-1'),('feeToken','HYPE')]:
            rows=self.rows();rows[0][key]=value
            self.assertEqual(self.run_ledger(rows)['fills'],0)

    def test_page_limit_and_wrong_order_rejected(self):
        self.assertEqual(self.run_ledger(self.rows()*667)['fills'],0)
        self.assertEqual(self.run_ledger(self.rows(),expected_order_id='99')['fills'],0)

    def test_short_accounting_and_rebates(self):
        rows=self.rows()
        for r in rows:
            r['dir']=r['dir'].replace('Long','Short');r['px']=str(200-float(r['px']))
            r['startPosition']=str(-float(r['startPosition']))
        rows[0]['fee']='-.01'
        self.assertAlmostEqual(self.run_ledger(rows,direction='short')['net_pnl'],2.91865)


class ExitGuardTests(unittest.TestCase):
    def test_uses_bid_for_long_ask_for_short(self):
        self.assertEqual(exit_guard_reason('long',101.9,102.1,99,102),'')
        self.assertEqual(exit_guard_reason('long',102,102.1,99,102),'TP_SOFTWARE_GUARD')
        self.assertEqual(exit_guard_reason('short',97.9,98.1,101,98),'')
        self.assertEqual(exit_guard_reason('short',97.9,98,101,98),'TP_SOFTWARE_GUARD')
        self.assertEqual(exit_guard_reason('long',98.9,99.1,99,102),'STOP_SOFTWARE_GUARD')
        self.assertEqual(exit_guard_reason('short',100.9,101,101,98),'STOP_SOFTWARE_GUARD')

    def test_bad_or_crossed_quotes_never_exit(self):
        for bid,ask in [(0,0),(math.nan,100),(102,101)]:
            self.assertEqual(exit_guard_reason('long',bid,ask,99,102),'')

    def test_manager_exits_even_if_exchange_flags_still_true(self):
        state={'sl_price_pct':.01,'sl_in_exchange':True,'tp_in_exchange':True}
        finalize=Mock()
        ns=dict(Any=Any,Optional=Optional,time=types.SimpleNamespace(time=lambda:100,sleep=Mock(side_effect=AssertionError('Must exit'))),
                _get_active_trade=lambda _:state,_coalesce_management_params=lambda **kw:{'tp_activate_price':.02,'bucket':'base'},
                _update_active_trade_fields=Mock(),log=Mock(),get_position_size_verified=Mock(return_value=1),
                get_price=lambda _:102.2,_pct_to_abs_price=lambda e,p,d,kind:e*(1-p if kind=='sl' else 1+p),
                POSITION_SYNC_INTERVAL=2,PRICE_CHECK_INTERVAL=1,SOFTWARE_EXIT_GUARD_ENABLED=True,
                exit_guard_reason=exit_guard_reason,get_best_bid_ask=lambda _:(102.1,102.3),
                close_verified=Mock(return_value={'closed':True}),_finalize_trade_close=finalize)
        functions('app/trading_engine.py',{'_manage_trade_until_close'},ns)
        ns['_manage_trade_until_close'](user_id=1,symbol='SOL',symbol_for_exec='SOL',direction='long',
             side='buy',opposite='sell',entry_price=100,qty_coin_for_log=1,qty_usdc_for_profit=100,
             best_score=80,entry_strength=.8,mode='TEST',sl_price_pct=.01)
        self.assertEqual(finalize.call_args.kwargs['exit_reason'],'TP_SOFTWARE_GUARD')
        self.assertEqual(finalize.call_args.kwargs['exit_price'],102.1)


class StrategyReviewTests(unittest.TestCase):
    def h1(self):
        return dict(c=[100]*6+[99.8],ema20=[100.1]*6+[100],ema50=[98.9]*6+[99],
                    ema200=[98]*7,atr=1,adx=20)

    def test_h1_bounded_pullback_long_short_and_rollback(self):
        tf=self.h1()
        for direction in ('long','short'):
            if direction=='short':
                for key in ('c','ema20','ema50','ema200'):tf[key]=[200-x for x in tf[key]]
            with patch.object(mtf,'MTF_H1_PULLBACK_ENABLED',True):
                d,diag=mtf._h1_bias(tf);self.assertEqual(d,direction);self.assertEqual(diag['h1_mode'],'bounded_pullback')
            with patch.object(mtf,'MTF_H1_PULLBACK_ENABLED',False):self.assertEqual(mtf._h1_bias(tf)[0],'none')

    def test_h1_still_blocks_broken_structure_and_deep_pullbacks(self):
        for key,value in [('c',[99.3]*7),('ema50',[99.2]*6+[99]),('adx',5),('ema200',[101]*7)]:
            tf=self.h1();tf[key]=value
            with patch.object(mtf,'MTF_H1_PULLBACK_ENABLED',True):self.assertEqual(mtf._h1_bias(tf)[0],'none')

    def sweep_data(self):
        n=100;d=dict(o=[101.0]*n,h=[104.0]*n,l=[100.0]*n,c=[102.0]*n,v=[100.0]*n,
                     ema20=[101.0]*n,ema50=[101.0]*n,atr_value=1.0)
        for i,vals in [(97,(100.8,101,99.6,100.7)),(98,(99.3,101.2,99.25,100.8)),(99,(100.5,101.8,100,101.2))]:
            for key,val in zip(('o','h','l','c'),vals):d[key][i]=val
        return d

    def test_sweep_stop_covers_retest_extreme_long_and_short(self):
        d=self.sweep_data();ok,reason,diag=sweep._detect_long_sweep(**d)
        self.assertTrue(ok,(reason,diag));self.assertAlmostEqual(diag['stop_price'],99.07)
        old=deepcopy(d)
        for key,source in [('o','o'),('h','l'),('l','h'),('c','c'),('ema20','ema20'),('ema50','ema50')]:
            d[key]=[200-x for x in old[source]]
        ok,reason,diag=sweep._detect_short_sweep(**d)
        self.assertTrue(ok,(reason,diag));self.assertAlmostEqual(diag['stop_price'],100.93)

    def test_long_sweep_does_not_confirm_on_red_candle(self):
        d=self.sweep_data();d['o'][-1]=101.6
        ok,_,diag=sweep._detect_long_sweep(**d)
        self.assertFalse(ok);self.assertGreater(diag['failures'].get('TRIGGER_DIRECTION',0),0)

    def test_router_probes_range_without_volatile_classifier(self):
        from app.strategies import router as rm
        with patch.object(rm,'_ROUTER_LIQUIDITY_PROBE_ENABLED',True),patch.dict('os.environ',{'STRATEGY_ROUTER_SETUP_FIRST_ENABLED':'true'}):
            self.assertTrue(StrategyRouter()._should_probe_liquidity({},rm.REGIME_RANGE))
            self.assertFalse(StrategyRouter()._should_probe_liquidity({},rm.REGIME_UNKNOWN))
        with patch.dict('os.environ',{'STRATEGY_ROUTER_SETUP_FIRST_ENABLED':'false'}):
            self.assertFalse(StrategyRouter()._should_probe_liquidity({},rm.REGIME_RANGE))


class DataBoundaryTests(unittest.TestCase):
    def test_malformed_candles_are_not_indicators(self):
        row=dict(t=300000,T=600000,o=100,h=102,l=99,c=101,v=10)
        for key,val in [('c',math.nan),('h',100),('l',102),('v',-1),('o',0)]:
            self.assertIsNone(market_context.parse_candle({**row,key:val},interval='5m'))

    def test_adapter_requests_time_endpoint(self):
        request=Mock(return_value=[])
        ns=dict(Any=Any,Optional=Optional,Dict=Dict,get_user_wallet=lambda _:'0xwallet',make_request=request)
        functions('app/hyperliquid_client.py',{'get_user_fills'},ns)
        ns['get_user_fills'](1,start_time_ms=100)
        self.assertEqual(request.call_args.args[1]['type'],'userFillsByTime')
        ns['get_user_fills'](1)
        self.assertEqual(request.call_args.args[1]['type'],'userFills')

class PersistenceTests(unittest.TestCase):
    def test_database_failure_is_not_retried_with_legacy_signature(self):
        register=Mock(side_effect=RuntimeError('database unavailable'))
        ns=dict(Any=Any,Optional=Optional,register_trade=register)
        functions('app/trading_engine.py',{'_register_trade_safe'},ns)
        with self.assertRaises(RuntimeError):
            ns['_register_trade_safe'](user_id=1,symbol='SOL',direction='long',side='buy',
                entry_price=100,exit_price=101,qty=1,profit=.9,exit_reason='TP',best_score=80)
        self.assertEqual(register.call_count,1)

    def test_unreconciled_close_keeps_active_identity(self):
        state={'opened_at_ms':100,'qty_coin_for_log':1}
        update=Mock();clear=Mock()
        ns=dict(_try_begin_trade_finalize=lambda *a:{},_get_active_trade=lambda _:state,
                _read_trade_realized_pnl=lambda *a,**kw:None,log=Mock(),
                _update_active_trade_fields=update,_clear_active_trade=clear,
                time=types.SimpleNamespace(time=lambda:200))
        functions('app/trading_engine.py',{'_finalize_trade_close'},ns)
        result=ns['_finalize_trade_close'](user_id=1,symbol='SOL',direction='long',side='buy',
            entry_price=100,exit_price=101,qty_coin=1,qty_usdc_for_profit=100,best_score=80,
            exit_reason='TP',exit_pnl_pct=.01,source='TEST')
        self.assertEqual(result,0);clear.assert_not_called()
        self.assertTrue(update.call_args.kwargs['reconciliation_pending'])
        self.assertFalse(update.call_args.kwargs['close_in_progress'])

    def test_stale_mid_is_not_reused_after_failed_refresh(self):
        ns=dict(norm_coin=lambda x:x,_refresh_mids_cache=lambda:None,
                _cache_lock=__import__('threading').Lock(),
                _MIDS_CACHE={'ts':90,'mids':{'SOL':100}},MIDS_TTL=1,
                time=types.SimpleNamespace(time=lambda:100))
        functions('app/hyperliquid_client.py',{'get_price'},ns)
        self.assertEqual(ns['get_price']('SOL'),0)
        ns['_MIDS_CACHE']['ts']=99
        self.assertEqual(ns['get_price']('SOL'),100)

    def test_full_accounting_adapter_uses_trade_start_not_two_hour_window(self):
        rows=AccountingTests().rows();fetch=Mock(return_value=rows)
        ns=dict(Any=Any,Optional=Optional,Dict=Dict,norm_coin=lambda x:x,get_user_fills=fetch)
        functions('app/hyperliquid_client.py',{'get_last_closed_trade_snapshot'},ns)
        result=ns['get_last_closed_trade_snapshot'](1,'SOL',opened_after_ms=100,
            expected_qty=2,direction='long')
        self.assertAlmostEqual(result['net_pnl'],2.81865)
        fetch.assert_called_once_with(1,start_time_ms=100)

class OrderAcknowledgementTests(unittest.TestCase):
    def test_ambiguous_ack_and_unexpected_resting_are_not_no_fill(self):
        ns=dict(Any=Any,Dict=Dict,_unwrap_exchange=lambda r:('ok',r),
                _extract_statuses=lambda r:r.get('statuses',[]),
                _parse_status=lambda r:r)
        functions('app/hyperliquid_client.py',{'_detect_fill'},ns)
        for response in (None,{'_http_error':True}, {'status':'ok'}, {'statuses':[{'kind':'resting'}]}):
            self.assertEqual(ns['_detect_fill'](response)['status'],'UNKNOWN')
        self.assertEqual(ns['_detect_fill']({'statuses':[{'kind':'error','error':'IocCancel'}]})['status'],'NO_FILL')

class RouterFallbackTests(unittest.TestCase):
    def test_volatile_label_allows_full_mtf_after_failed_sweep(self):
        from app.strategies import router as rm
        router=StrategyRouter()
        regime={'active_regime':rm.REGIME_VOLATILE,'candidate_regime':rm.REGIME_VOLATILE,
                'candidate_confidence':.9,'state':{},'features':{}}
        with patch.dict('os.environ',{'STRATEGY_ROUTER_SETUP_FIRST_ENABLED':'true'}), \
             patch.object(rm,'detect_regime',return_value=regime), \
             patch.object(router,'_context_failure_reason',return_value=None), \
             patch.object(router,'_attach_shadow_metadata',side_effect=lambda **kw:kw['out']), \
             patch.object(router,'_evaluate_strategy',side_effect=[{'signal':False,'reason':'NO_SWEEP'},
                          {'signal':True,'score':70,'direction':'long'}]) as evaluate:
            result=router.route_symbol('SOL',{}, {})
        self.assertTrue(result['signal']);self.assertEqual(result['router_strategy_id'],'mtf_simple')
        self.assertEqual(evaluate.call_count,2)

    def test_unknown_regime_remains_blocked(self):
        from app.strategies import router as rm
        router=StrategyRouter()
        regime={'active_regime':rm.REGIME_UNKNOWN,'candidate_regime':rm.REGIME_UNKNOWN,
                'candidate_confidence':0,'state':{},'features':{}}
        with patch.dict('os.environ',{'STRATEGY_ROUTER_SETUP_FIRST_ENABLED':'true'}), \
             patch.object(rm,'detect_regime',return_value=regime), \
             patch.object(router,'_context_failure_reason',return_value=None), \
             patch.object(router,'_attach_shadow_metadata',side_effect=lambda **kw:kw['out']), \
             patch.object(router,'_evaluate_strategy') as evaluate:
            result=router.route_symbol('SOL',{}, {})
        self.assertFalse(result['signal']);evaluate.assert_not_called()
