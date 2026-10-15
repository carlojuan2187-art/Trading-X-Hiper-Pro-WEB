"""Offline 1m replay correctness; no Mongo, live orders or network."""
import unittest
from app.demo.audit import analyze_1m_trade, replay_recent_history_to_logs


def row(t, low, high):
    return {'t': t, 'o': str((low+high)/2), 'h': str(high),
            'l': str(low), 'c': str((low+high)/2), 'v':'12'}


class FakeRows:
    def __init__(self, docs):
        self.docs=docs
    def find(self, query, projection):
        assert projection == {'history':1, '_id':1}
        return self.docs


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.start = '2026-10-15T10:00:20+00:00'
        self.end = '2026-10-15T10:03:20+00:00'
        self.trade = {'symbol':'FET-PERP','direction':'long','entry_price':100.0,
            'exit_price':98.0,'stop_price':99.0,'target_price':102.0,
            'opened_at':self.start, 'closed_at':self.end, 'strategy_id':'liquidity_sweep_reversal'}
        self.candles = [
            {'timestamp':1792058400000, 'high':100.6,'low':99.8},
            {'timestamp':1792058460000, 'high':101.2,'low':99.5},
            {'timestamp':1792058520000, 'high':102.2,'low':98.8},
            {'timestamp':1792058580000, 'high':99.3,'low':97.5},
        ]

    def test_both_levels_same_bar_is_unknown_sequence(self):
        # Use calculated timestamps to avoid depending on hardcoded epoch numbers.
        from datetime import datetime
        base=int(datetime.fromisoformat(self.start).timestamp()*1000)//60000*60000
        candles=[{'timestamp':base,'high':100.6,'low':99.8},
                 {'timestamp':base+60000,'high':101.2,'low':99.5},
                 {'timestamp':base+120000,'high':102.2,'low':98.8},
                 {'timestamp':base+180000,'high':99.3,'low':97.5}]
        result=analyze_1m_trade(self.trade,candles)
        self.assertEqual(result['status'],'APPROX_1M_NOT_TICK')
        self.assertEqual(result['first_sl_tp_touch_1m'],'BOTH_UNORDERED')
        self.assertFalse(result['can_determine_intraminute_touch_order'])
        self.assertGreater(result['mfe_price_pct_approx'],2)
        self.assertLess(result['mae_price_pct_approx'],-2)

    def test_replay_caps_remote_requests_at_five_and_does_not_mutate_database(self):
        from datetime import datetime
        base=int(datetime.fromisoformat(self.start).timestamp()*1000)//60000*60000
        requests=[]
        def request(endpoint,payload,**kwargs):
            requests.append((endpoint,payload))
            return [row(base,99.9,100.2),row(base+60000,98.8,102.1)]
        documents=[{'_id':i+1,'history':[dict(self.trade)]} for i in range(9)]
        self.assertEqual(replay_recent_history_to_logs(collection=FakeRows(documents),
                                                       request=request),5)
        self.assertEqual(len(requests),5)
        self.assertEqual(requests[0][1]['req']['coin'],'FET')
        self.assertEqual(documents[0]['history'][0]['entry_price'],100.0)

    def test_missing_data_are_explicit(self):
        self.assertEqual(analyze_1m_trade(self.trade,[])['status'],'NO_1M_CANDLES')


if __name__ == '__main__':
    unittest.main()
