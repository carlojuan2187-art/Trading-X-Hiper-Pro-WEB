"""Integration of actual strategy modules with synthetic closed-candle contexts."""
import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'offline-test')
os.environ.setdefault('ADMIN_TELEGRAM_ID', '1')
os.environ.setdefault('MONGO_URL', 'mongodb://offline.invalid')
adapter = types.ModuleType('app.hyperliquid_client')
def no_network(*args, **kwargs):
    raise AssertionError('Network is forbidden in tests')
adapter.make_request = no_network
adapter.norm_coin = lambda x: str(x).upper().removesuffix('-PERP')
sys.modules.setdefault('app.hyperliquid_client', adapter)

from app.strategies import breakout_reset as mtf
from app.strategies import range_mean_reversion as range_strategy
from app.strategies.router import StrategyRouter
from app import market_context
from app.symbol_policy import rejection_reason
from app.trading_quality import absolute_risk_reward


class StrategyContracts(unittest.TestCase):
    def context(self):
        n = 320
        candles = [dict(t=1_700_000_100_000+i*300_000,T=1_700_000_400_000+i*300_000,
                        o=99.6,h=99.9,l=99.4,c=99.8,v=100) for i in range(n)]
        candles[-1].update(c=100,h=100.2)
        tf = dict(status='OK',stale=False,context_ok=True,candles=candles,
                  o=[x['o'] for x in candles],h=[x['h'] for x in candles],
                  l=[x['l'] for x in candles],c=[x['c'] for x in candles],v=[100]*n,
                  ema20=[99.5-(n-1-i)*.002 for i in range(n)],
                  ema50=[99-(n-1-i)*.001 for i in range(n)],ema200=[98]*n,
                  close=100,atr=1,atr_pct=.01,adx=30,last_close_t=candles[-1]['T'],last_t=candles[-1]['t'])
        return {'coin':'SOL','symbol':'SOL','timeframes':{'5m':tf}}

    def test_mtf_accepted_signal_has_consistent_absolute_levels(self):
        ctx = self.context(); tf = ctx['timeframes']['5m']
        with patch.object(mtf,'_resolve_tf_snapshot',return_value=tf), patch.object(mtf,'LOG_SIGNAL_DIAGNOSTICS',False):
            signal = mtf._evaluate_market_context(ctx)
        self.assertTrue(signal.get('signal'),signal)
        plan = absolute_risk_reward(direction=signal['direction'],entry_price=signal['signal_reference_price'],
                                    stop_price=signal['stop_price'],target_price=signal['target_price'])
        self.assertTrue(plan['ok'])
        self.assertGreaterEqual(plan['rr']+1e-10, 1.2)
        self.assertAlmostEqual(plan['risk_pct'],signal['sl_price_pct'])

    def test_range_preserves_structural_stop_and_targets_before_vwap(self):
        ctx = self.context(); ctx['timeframes']['5m']['adx']=12
        with patch.object(range_strategy,'compute_choppiness',return_value=65), \
             patch.object(range_strategy,'compute_efficiency_ratio',return_value=.1), \
             patch.object(range_strategy,'compute_vwap_distance',return_value={'rolling_vwap':102}), \
             patch.object(range_strategy,'_detect_long_reversion',return_value=(True,'OK',{'score':90,'diag':{},'stop_price':99})), \
             patch.object(range_strategy,'_detect_short_reversion',return_value=(False,'NONE',{})), \
             patch.object(range_strategy,'LOG_SIGNAL_DIAGNOSTICS',False):
            signal=range_strategy.RangeMeanReversionStrategy().evaluate('SOL',ctx)
        self.assertTrue(signal.get('signal'),signal)
        self.assertEqual(signal['stop_price'],99)
        self.assertLess(signal['target_price'],102)
        self.assertAlmostEqual(signal['tp_activation_price'],.018)
        self.assertEqual(signal['trail_retrace_price'],0)

    def test_range_too_wide_stop_is_rejected_not_clamped(self):
        ctx=self.context();ctx['timeframes']['5m']['adx']=12
        with patch.object(range_strategy,'compute_choppiness',return_value=65), \
             patch.object(range_strategy,'compute_efficiency_ratio',return_value=.1), \
             patch.object(range_strategy,'compute_vwap_distance',return_value={'rolling_vwap':110}), \
             patch.object(range_strategy,'_detect_long_reversion',return_value=(True,'OK',{'score':90,'diag':{},'stop_price':97})), \
             patch.object(range_strategy,'_detect_short_reversion',return_value=(False,'NONE',{})):
            signal=range_strategy.RangeMeanReversionStrategy().evaluate('SOL',ctx)
        self.assertEqual(signal['reason'],'STRUCTURAL_SL_TOO_WIDE')

    def test_range_live_is_explicit_opt_in(self):
        with patch.dict(os.environ,{'STRATEGY_ROUTER_RANGE_LIVE_ENABLED':'false'}):
            self.assertNotIn('RANGE',StrategyRouter()._regime_strategy_map)
        with patch.dict(os.environ,{'STRATEGY_ROUTER_RANGE_LIVE_ENABLED':'true'}):
            self.assertEqual(StrategyRouter()._regime_strategy_map['RANGE'],'range_mean_reversion')

    def test_m15_relaxation_only_removes_slow_stack(self):
        tf=self.context()['timeframes']['5m'];tf['ema200']=[101]*320
        self.assertEqual(mtf._bias_from_tf(tf,adx_min=11)[0],'none')
        self.assertEqual(mtf._bias_from_tf(tf,adx_min=11,require_slow_stack=False)[0],'long')

    def test_pending_m5_tolerance_expands_only_in_demo(self):
        data = dict(direction='long', o=[], h=[], l=[], c=[], v=[],
                    ema20=[], ema50=[], ema200=[], atr=1)
        with patch.object(mtf, 'find_pending_reset', return_value=(False, 'NO_VALID_PENDING_RESET', {})) as reset:
            mtf._detect_simple_mtf_trigger(**data, demo_trial=False)
            live = dict(reset.call_args.kwargs)
            mtf._detect_simple_mtf_trigger(**data, demo_trial=True)
            demo = dict(reset.call_args.kwargs)
        self.assertAlmostEqual(live['touch_atr'], mtf.RESET_TOUCH_TOL_ATR)
        self.assertAlmostEqual(live['confirm_atr'], mtf.CONTINUATION_CONFIRM_TOL_ATR)
        self.assertAlmostEqual(demo['touch_atr'], .58)
        self.assertAlmostEqual(demo['confirm_atr'], .32)
        self.assertEqual(demo['break_atr'], live['break_atr'])
        self.assertEqual(demo['max_extension'], live['max_extension'])

    def test_policy_matches_strategies(self):
        self.assertEqual(rejection_reason('HYPE-PERP'),'MEME_SYMBOL_BLOCKED')
        self.assertIn('HYPE',mtf.BLOCKED_MEME_KEYWORDS)
        self.assertEqual(rejection_reason('SOL-PERP'),'')

    def test_candle_cache_expires_at_next_close_not_just_ttl(self):
        market_context._market_context_cache.clear()
        key=('tf','SOL','5m',320)
        with patch('time.time',return_value=301.6):
            market_context._cache_set(key, {'status':'OK','last_close_t':300000})
            self.assertIsNotNone(market_context._cache_get(key))
        with patch('time.time',return_value=601.6):
            self.assertIsNone(market_context._cache_get(key))

    def test_raw_candle_fetch_is_shared_and_retried_after_close(self):
        market_context._raw_candle_cache.clear()
        rows=[{'t':0,'T':300000}]
        with patch.object(market_context,'_fetch_candles_uncached',return_value=(rows,'OK')) as fetch:
            with patch('time.time',return_value=301.6):
                market_context.fetch_candles('SOL','5m',320)
                market_context.fetch_candles('SOL','5m',320)
                self.assertEqual(fetch.call_count,1)
            with patch('time.time',return_value=601.6):
                market_context.fetch_candles('SOL','5m',320)
                self.assertEqual(fetch.call_count,2)


class BiasDiagnosticsTests(unittest.TestCase):
    def test_named_checks_preserve_original_decisions(self):
        import random
        rng = random.Random(20260927)
        for _ in range(1000):
            close = rng.uniform(95, 105)
            e20 = [rng.uniform(98, 102)] * 6 + [rng.uniform(98, 102)]
            e50 = [rng.uniform(98, 102)] * 6 + [rng.uniform(98, 102)]
            e200 = [rng.uniform(98, 102)] * 7
            adx = rng.uniform(0, 40)
            slow = rng.choice((True, False))
            s20 = mtf._pct_change(e20[-1], e20[0])
            s50 = mtf._pct_change(e50[-1], e50[0])
            spread = abs(e20[-1]-e50[-1])/close
            common = adx >= 12 and spread >= mtf.TREND_STACK_MIN_PCT
            long = common and close > e20[-1] > e50[-1] and (not slow or e50[-1] > e200[-1]) and s20 > 0 and s50 >= -.00010
            short = common and close < e20[-1] < e50[-1] and (not slow or e50[-1] < e200[-1]) and s20 < 0 and s50 <= .00010
            expected = 'long' if long else 'short' if short else 'none'
            direction, diag = mtf._bias_from_tf(dict(c=[close], ema20=e20,
                ema50=e50, ema200=e200, adx=adx), adx_min=12, require_slow_stack=slow)
            self.assertEqual(direction, expected)
            self.assertEqual(not diag['failed_long'], bool(long))
            self.assertEqual(not diag['failed_short'], bool(short))

    def test_equal_fast_price_remains_blocked(self):
        direction, diag = mtf._bias_from_tf(dict(c=[100], ema20=[99]*6+[100],
            ema50=[98]*6+[99], ema200=[97]*7, adx=25), adx_min=12)
        self.assertEqual(direction, 'none')
        self.assertIn('PRICE_ABOVE_EMA20', diag['failed_long'])


class LiquidityDiagnosticsTests(unittest.TestCase):
    def test_demo_sweep_age_is_isolated_from_live(self):
        from app.strategies import liquidity_sweep_reversal as sweep
        self.assertEqual(sweep._sweep_age_limit({'_execution_mode': 'live'}), 8)
        self.assertEqual(sweep._sweep_age_limit({'_execution_mode': 'demo'}), 4)
        self.assertEqual(sweep._sweep_age_limit({}), 8)
        with patch.object(sweep, 'DEMO_LIQUIDITY_MAX_SWEEP_AGE_BARS', 8):
            self.assertEqual(sweep._sweep_age_limit({'_execution_mode': 'demo'}), 8)
            self.assertEqual(sweep._sweep_age_limit({'_execution_mode': 'live'}), 8)

    def test_flat_market_reports_missing_sweep_both_directions(self):
        from app.strategies import liquidity_sweep_reversal as sweep
        for detector in (sweep._detect_long_sweep, sweep._detect_short_sweep):
            ok, reason, diag = detector([100]*100, [101]*100, [99]*100,
                [100]*100, [10]*100, [100]*100, [100]*100, 1)
            self.assertFalse(ok)
            self.assertGreater(diag['examined'], 0)
            self.assertEqual(diag['failures'], {'NO_SWEEP_DEPTH': diag['examined']})

    def test_router_reports_attempt_hidden_by_primary_rejection(self):
        router = StrategyRouter()
        attempt = {'strategy_id': 'liquidity_sweep_reversal', 'signal': False,
                   'reason': 'NO_LIQUIDITY_SWEEP_SETUP', 'diag': {'long_search': {'examined': 3}}}
        result = {'signal': False, 'reason': 'NO_H1_BIAS',
                  'router_strategy_id': 'mtf_simple', 'router_considered_strategies': [attempt]}
        with patch.object(router, '_route_symbol_impl', return_value=result):
            diag = router.route_symbol('SOL')['liquidity_diagnostics']
        self.assertTrue(diag['evaluated'])
        self.assertEqual(diag['status'], 'EVALUATED_REJECTED')
        self.assertEqual(diag['attempt'], attempt)

    def test_router_distinguishes_not_routed_selected_and_probe_score(self):
        router = StrategyRouter()
        for result, expected in [
            ({'signal': False, 'reason': 'ROUTER_REGIME_NO_TRADE'}, 'NOT_ROUTED'),
            ({'signal': True, 'router_strategy_id': 'mtf_simple'}, 'PRIMARY_SIGNAL_PRIORITY'),
            ({'signal': True, 'router_strategy_id': 'liquidity_sweep_reversal',
              'router_considered_strategies': [{'strategy_id':'liquidity_sweep_reversal','signal':True}]}, 'SELECTED'),
            ({'signal': False, 'router_strategy_id': 'mtf_simple',
              'router_considered_strategies': [{'strategy_id':'liquidity_sweep_reversal','signal':True,'score':70}]}, 'PROBE_SCORE_REJECTED')]:
            with patch.object(router, '_route_symbol_impl', return_value=result):
                self.assertEqual(router.route_symbol('SOL')['liquidity_diagnostics']['status'], expected)


class H1ControlledComparisonTests(unittest.TestCase):
    def test_optional_slow_stack_does_not_bypass_slopes(self):
        tf = dict(c=[97],ema20=[100]*6+[99],ema50=[102]*6+[101],ema200=[95]*7,adx=25)
        strict, _ = mtf._bias_from_tf(tf,adx_min=12,require_slow_stack=True)
        relaxed, _ = mtf._bias_from_tf(tf,adx_min=12,require_slow_stack=False)
        self.assertEqual(strict,'none')
        self.assertEqual(relaxed,'short')
        tf['ema20']=[98]*6+[99]
        blocked, _ = mtf._bias_from_tf(tf,adx_min=12,require_slow_stack=False)
        self.assertEqual(blocked,'none')


class ConfirmedH1TransitionTests(unittest.TestCase):
    def short_context(self):
        return dict(c=[99]*5+[97,96],ema20=[102,102,102,102,102,100,99],
                    ema50=[104,104,104,104,104,102,101],ema200=[95]*7,adx=25)

    def test_short_transition_passes_only_slow_ema_veto(self):
        tf=self.short_context()
        with patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',True), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
            direction,diag=mtf._h1_bias(tf)
            self.assertEqual(direction,'short')
            self.assertEqual(diag['h1_mode'],'confirmed_transition')

    def test_long_transition_is_symmetric(self):
        tf=self.short_context()
        for key in ('c','ema20','ema50','ema200'):tf[key]=[200-x for x in tf[key]]
        with patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',True), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
            direction,diag=mtf._h1_bias(tf)
            self.assertEqual(direction,'long')
            self.assertEqual(diag['h1_mode'],'confirmed_transition')

    def test_transition_blocks_weak_trend_and_single_bar_bounce(self):
        for change in ('adx','previous_close','recent_slope'):
            tf=self.short_context()
            if change=='adx':tf['adx']=15  # El umbral efectivo de transición LIVE es 16
            if change=='previous_close':tf['c'][-2]=103
            if change=='recent_slope':tf['ema50'][-2]=100.5
            with patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',True), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
                self.assertEqual(mtf._h1_bias(tf)[0],'none',change)

    def test_disable_restores_old_strict_behavior(self):
        with patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',False), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
            self.assertEqual(mtf._h1_bias(self.short_context())[0],'none')

    def test_other_original_failures_cannot_be_bypassed(self):
        tf=self.short_context();tf['c'][-1]=105
        with patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',True), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
            self.assertEqual(mtf._h1_bias(tf)[0],'none')


class BalancedEntryIntegrationTests(unittest.TestCase):
    context = StrategyContracts.context
    def test_transition_reaches_complete_mtf_signal(self):
        from copy import deepcopy
        ctx=self.context();base=ctx['timeframes']['5m'];h1=deepcopy(base)
        h1['ema200']=[101]*len(h1['c'])
        def resolve(symbol,interval,*args,**kwargs):return h1 if interval=='1h' else base
        with patch.object(mtf,'_resolve_tf_snapshot',side_effect=resolve), patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',True), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
            out=mtf._evaluate_market_context(ctx)
        self.assertTrue(out['signal'],out)
        self.assertEqual(out['h1_mode'],'confirmed_transition')
        with patch.object(mtf,'_resolve_tf_snapshot',side_effect=resolve), patch.object(mtf,'MTF_H1_TRANSITION_ENABLED',False), patch.object(mtf,'MTF_H1_REQUIRE_EMA200',True):
            self.assertEqual(mtf._evaluate_market_context(ctx)['reason'],'NO_H1_BIAS')

    def test_probe_uses_full_alternative_signal_and_score_gate(self):
        from app.strategies import router as router_module
        from unittest.mock import Mock
        router=StrategyRouter()
        regime={'active_regime':router_module.REGIME_TREND,'candidate_regime':router_module.REGIME_VOLATILE,
                'candidate_confidence':.8,'candidate_scores':{},'features':{},'state':{}}
        for score,expected in [(80,True),(70,False)]:
            evaluate=Mock(side_effect=[{'signal':False,'reason':'NO_H1_BIAS'},
                {'signal':True,'score':score,'strategy_id':'liquidity_sweep_reversal'}])
            with patch.object(router_module,'_ROUTER_LIQUIDITY_PROBE_ENABLED',True), patch.object(router_module,'detect_regime',return_value=regime), patch.object(router,'_context_failure_reason',return_value=None), patch.object(router,'_resolve_regime_for_routing',return_value=(router_module.REGIME_TREND,'active')), patch.object(router,'_evaluate_strategy',evaluate), patch.object(router,'_attach_shadow_metadata',side_effect=lambda **kw:kw['out']):
                out=router.route_symbol('SOL',market_context={},btc_context={})
            self.assertEqual(bool(out['signal']),expected)
            self.assertEqual(evaluate.call_count,2)


class M15PullbackTests(unittest.TestCase):
    def short_context(self):
        return dict(c=[97]*6+[99.2],ema20=[98.8]*6+[99],ema50=[101]*6+[100],
                    ema200=[102]*7,adx=25,atr=1)

    def test_structured_short_pullback_is_accepted(self):
        with patch.object(mtf,'MTF_M15_PULLBACK_ENABLED',True):
            side,diag=mtf._m15_confirmation(self.short_context(),'short')
        self.assertEqual(side,'short');self.assertEqual(diag['m15_mode'],'bounded_pullback')

    def test_long_symmetry(self):
        tf=self.short_context()
        for key in ('c','ema20','ema50','ema200'):tf[key]=[200-x for x in tf[key]]
        with patch.object(mtf,'MTF_M15_PULLBACK_ENABLED',True):
            self.assertEqual(mtf._m15_confirmation(tf,'long')[0],'long')

    def test_structure_depth_strength_and_data_still_block(self):
        for key,value in [('c',[97]*6+[100.1]),('c',[97]*6+[99.8]),('adx',5),('atr',0),('atr',float('nan')),('ema50',[98]*6+[100])]:
            tf=self.short_context();tf[key]=value
            with patch.object(mtf,'MTF_M15_PULLBACK_ENABLED',True):
                self.assertEqual(mtf._m15_confirmation(tf,'short')[0],'none',(key,value))

    def test_disable_preserves_previous_rejection(self):
        with patch.object(mtf,'MTF_M15_PULLBACK_ENABLED',False):
            self.assertEqual(mtf._m15_confirmation(self.short_context(),'short')[0],'none')

    def test_no_h1_direction_cannot_enable_pullback(self):
        with patch.object(mtf,'MTF_M15_PULLBACK_ENABLED',True):
            self.assertEqual(mtf._m15_confirmation(self.short_context(),'none')[0],'none')

    def test_complete_strategy_still_requires_m5_trigger(self):
        from copy import deepcopy
        ctx=StrategyContracts.context(self);base=ctx['timeframes']['5m'];m15=deepcopy(base)
        m15['ema20']=[100.1]*len(base['c']);m15['ema20'][-1]=99.9
        m15['c'][-1]=99.7;m15['close']=99.7
        def resolve(symbol,interval,*args,**kwargs):return m15 if interval=='15m' else base
        with patch.object(mtf,'MTF_M15_PULLBACK_ENABLED',True), patch.object(mtf,'_resolve_tf_snapshot',side_effect=resolve), patch.object(mtf,'LOG_SIGNAL_DIAGNOSTICS',False):
            out=mtf._evaluate_market_context(ctx)
            self.assertTrue(out['signal'],out)
            self.assertEqual(out['m15_mode'],'bounded_pullback')
            with patch.object(mtf,'_detect_simple_mtf_trigger',return_value=(False,'NO_5M_CONTINUATION_CONFIRM',{})):
                self.assertEqual(mtf._evaluate_market_context(ctx)['reason'],'NO_5M_CONTINUATION_CONFIRM')


class RejectionEvidenceTests(unittest.TestCase):
    context = StrategyContracts.context
    def test_rr_diagnostic_reports_the_threshold_actually_enforced(self):
        ctx = self.context()
        tf = ctx['timeframes']['5m']
        with patch.object(mtf, '_resolve_tf_snapshot', return_value=tf), \
             patch.object(mtf, '_nearest_structural_target', return_value=100.5), \
             patch.object(mtf, 'ENTRY_RR_RESERVE', .05):
            result = mtf._evaluate_market_context(ctx)
        self.assertEqual(result['reason'], 'STRUCTURAL_RR_TOO_LOW')
        diag = result['diag']
        self.assertAlmostEqual(diag['min_rr'], 1.20)
        self.assertAlmostEqual(diag['execution_min_rr'], 1.15)
        self.assertTrue(diag['structural_target_binding'])
        self.assertLess(diag['structural_tp_pct'], diag['required_tp_pct'])
        self.assertLess(diag['tp_rr_full_precision'], diag['min_rr'])

    def test_short_price_confirmation_does_not_hide_wrong_candle_color(self):
        n = 210
        with patch.object(mtf, 'MTF_PENDING_SETUP_ENABLED', False):
            ok, reason, diag = mtf._detect_simple_mtf_trigger(
                direction='short', o=[99.4]*n, h=[100.0]*n,
                l=[99.4]*n, c=[99.5]*n, v=[100]*n,
                ema20=[100.0]*n, ema50=[101.0]*n, ema200=[102.0]*n, atr=1)
        self.assertFalse(ok)
        self.assertEqual(reason, 'NO_5M_CONTINUATION_CONFIRM')
        self.assertLessEqual(diag['confirm_close'], diag['confirm_level'])
        self.assertGreater(diag['confirm_close'], diag['confirm_open'])
        self.assertFalse(diag['reclaim_ok'])
        self.assertTrue(diag['reset_touched'])


class DemoResetTrialTests(unittest.TestCase):
    def test_demo_confirms_recent_short_past_wider_candle_tolerance(self):
        n = 16
        o = [100.0] * n
        h = [100.2] * n
        l = [98.9] * n
        c = [100.0] * n
        e20 = [100.0] * n
        e50 = [101.0] * n
        o[-1], h[-1], l[-1], c[-1] = 99.3, 99.5, 99.05, 99.1
        args = dict(direction="short", o=o, h=h, l=l, c=c,
                    v=[100] * n, ema20=e20, ema50=e50,
                    ema200=[102] * n, atr=1.0)
        with patch.object(mtf, "MTF_PENDING_SETUP_ENABLED", True):
            live_ok, live_reason, _ = mtf._detect_simple_mtf_trigger(**args)
            demo_ok, demo_reason, diag = mtf._detect_simple_mtf_trigger(
                **args, demo_trial=True)
        self.assertFalse(live_ok)
        self.assertEqual(live_reason, "PENDING_CONTINUATION_CONFIRM")
        self.assertTrue(demo_ok, (demo_reason, diag))
        self.assertEqual(demo_reason, "OK")
        self.assertLessEqual(diag["extension_atr"], 0.95)

    def test_live_reset_parameters_unchanged(self):
        args = dict(direction="long", o=[100.] * 12, h=[101.] * 12,
                    l=[99.] * 12, c=[100.] * 12,
                    v=[100.] * 12, ema20=[100.] * 12, ema50=[99.] * 12,
                    ema200=[98.] * 12, atr=1.)
        with patch.object(mtf, "MTF_PENDING_SETUP_ENABLED", True), \
             patch.object(mtf, "find_pending_reset", return_value=(False, "NO_VALID_PENDING_RESET", {})) as mock:
            mtf._detect_simple_mtf_trigger(**args)
            live = dict(mock.call_args.kwargs)
            mtf._detect_simple_mtf_trigger(**args, demo_trial=True)
            demo = dict(mock.call_args.kwargs)
        self.assertEqual(live["touch_atr"], mtf.RESET_TOUCH_TOL_ATR)
        self.assertEqual(live["confirm_atr"], mtf.CONTINUATION_CONFIRM_TOL_ATR)
        self.assertGreater(demo["touch_atr"], live["touch_atr"])
        self.assertGreater(demo["confirm_atr"], live["confirm_atr"])
        self.assertEqual(demo["break_atr"], live["break_atr"])
        self.assertEqual(demo["max_extension"], live["max_extension"])



class DemoBiasExperimentTests(unittest.TestCase):
    """La flexibilización alcanza sólo DEMO, sin alterar la ruta LIVE."""

    @staticmethod
    def trend(*, direction='long', m15=False):
        sign = 1 if direction == 'long' else -1
        # EMA50 sigue al lado contrario de EMA200; todo lo demás cumple.
        base = 100
        c = [base + sign*1.3] * 6 + [base + sign*1.5]
        e20 = [base + sign*(1.0 - .10*(6-i)) for i in range(7)]
        e50 = [base + sign*(.65 - .05*(6-i)) for i in range(7)]
        e200 = [base + sign*1.8] * 7
        return dict(c=c,ema20=e20,ema50=e50,ema200=e200,adx=25,atr=1)

    def test_demo_h1_early_transition_needs_fast_trend_and_recent_slopes(self):
        tf=self.trend()
        # La segunda vela previa no confirma necesariamente la dirección:
        # en DEMO basta el cierre actual si hay ADX y pendientes consistentes.
        tf['c'][-2] = 99.0
        self.assertEqual(mtf._h1_bias(tf)[0], 'none')
        direction, diag=mtf._h1_bias(tf,demo_trial=True)
        self.assertEqual(direction, 'long',diag)
        self.assertEqual(diag['h1_mode'], 'demo_early_transition')
        tf['adx']=10
        self.assertEqual(mtf._h1_bias(tf,demo_trial=True)[0],'none')
        tf['adx']=25
        tf['ema50'][-2]=tf['ema50'][-1] + .1
        self.assertEqual(mtf._h1_bias(tf,demo_trial=True)[0],'none')

    def test_demo_m15_accepts_only_slow_ema_veto(self):
        tf=self.trend()
        self.assertEqual(mtf._m15_confirmation(tf,'long')[0], 'none')
        direction, diag=mtf._m15_confirmation(tf,'long',demo_trial=True)
        self.assertEqual(direction,'long',diag)
        self.assertEqual(diag['m15_mode'],'demo_fast_trend_confirmed')
        tf['adx']=12
        self.assertEqual(mtf._m15_confirmation(tf,'long',demo_trial=True)[0],'none')
        tf['adx']=25
        tf['c'][-2]=tf['ema20'][-2]-1
        self.assertEqual(mtf._m15_confirmation(tf,'long',demo_trial=True)[0],'none')
        tf=self.trend()
        tf['ema20'][-1]=tf['ema50'][-1]-0.1
        self.assertEqual(mtf._m15_confirmation(tf,'long',demo_trial=True)[0],'none')

    def test_short_mirrors_long(self):
        tf=self.trend(direction='short')
        self.assertEqual(mtf._m15_confirmation(tf,'short')[0], 'none')
        self.assertEqual(mtf._m15_confirmation(tf,'short',demo_trial=True)[0], 'short')
        tf['c'][-2]=101
        self.assertEqual(mtf._h1_bias(tf)[0],'none')
        self.assertEqual(mtf._h1_bias(tf,demo_trial=True)[0],'short')

    def test_demo_parity_default_and_opt_in_experiment_never_changes_live(self):
        from copy import deepcopy
        ctx=StrategyContracts().context(); base=ctx['timeframes']['5m']; h1=deepcopy(base)
        h1['ema200']=[101]*len(h1['c'])
        h1['c'][-2]=98
        def resolve(symbol,interval,*args,**kwargs):
            return h1 if interval=='1h' else base
        with patch.object(mtf,'_resolve_tf_snapshot',side_effect=resolve):
            trial=dict(ctx,_execution_mode='demo')
            self.assertEqual(mtf._evaluate_market_context(ctx)['reason'],'NO_H1_BIAS')
            with patch.object(mtf, 'DEMO_STRATEGY_EXPERIMENTAL', False):
                self.assertEqual(mtf._evaluate_market_context(trial)['reason'], 'NO_H1_BIAS')
            with patch.object(mtf, 'DEMO_STRATEGY_EXPERIMENTAL', True):
                outcome=mtf._evaluate_market_context(trial)
                self.assertTrue(outcome['signal'], outcome)
                self.assertEqual(outcome['h1_mode'],'demo_early_transition')
                self.assertEqual(mtf._evaluate_market_context(ctx)['reason'],'NO_H1_BIAS')
