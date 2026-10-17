import unittest
from app.pending_setup import find_pending_reset, find_m5_continuation

class PendingLifecycleTests(unittest.TestCase):
    def data(self):
        d=dict(direction='long',o=[100.7]*20,h=[100.95]*20,l=[100.6]*20,c=[100.65]*20,
               ema20=[100.0]*20,ema50=[99.5]*20,atr=1,max_age=8)
        d['l'][13]=99.9
        d['c'][-1]=100.9
        return d

    def test_six_bar_reset_remains_available_and_keeps_anchor(self):
        ok,reason,diag=find_pending_reset(**self.data())
        self.assertTrue(ok,reason)
        self.assertEqual(diag['bars_since_reset'],6)
        self.assertEqual(diag['structural_extreme'],99.9)

    def test_expired_reset_cannot_enter(self):
        d=self.data();d['max_age']=5
        self.assertFalse(find_pending_reset(**d)[0])

    def test_prior_confirmation_cannot_be_reused(self):
        d=self.data();d['c'][16]=100.9
        self.assertEqual(find_pending_reset(**d)[1],'PENDING_SETUP_ALREADY_CONFIRMED')

    def test_prior_extended_confirmation_cannot_be_reused(self):
        d=self.data();d['c'][16]=102;d['h'][16]=102
        self.assertEqual(find_pending_reset(**d)[1],'PENDING_SETUP_ALREADY_CONFIRMED')

    def test_structure_break_cancels_old_setup(self):
        d=self.data();d['l'][16]=99
        self.assertFalse(find_pending_reset(**d)[0])

    def test_short_has_symmetric_lifecycle(self):
        d=self.data();d.update(direction='short',o=[200-x for x in d['o']],h=[200-x for x in d['l']],l=[200-x for x in d['h']],c=[200-x for x in d['c']],ema20=[200-x for x in d['ema20']],ema50=[200-x for x in d['ema50']])
        self.assertTrue(find_pending_reset(**d)[0])


class DemoM5ExpandedPatternTests(unittest.TestCase):
    def data(self):
        n = 12
        d = dict(direction='long', o=[100.15]*n, h=[100.5]*n,
                 l=[99.7]*n, c=[100.3]*n, ema20=[100.0]*n,
                 atr=1.0, max_extension=0.95)
        # A closed counter-candle pause followed by an unmistakable recovery.
        d['o'][-2], d['h'][-2], d['l'][-2], d['c'][-2] = 100.5, 100.6, 100.1, 100.3
        d['o'][-1], d['h'][-1], d['l'][-1], d['c'][-1] = 100.1, 100.9, 100.0, 100.8
        return d

    def test_new_closed_m5_pause_is_valid_for_long(self):
        ok, reason, diag = find_m5_continuation(**self.data())
        self.assertTrue(ok, reason)
        self.assertEqual(diag['m5_entry_path'], 'pause_continuation')
        self.assertEqual(diag['structural_extreme'], 100.0)
        self.assertEqual(diag['bars_since_reset'], 1)

    def test_short_is_symmetric(self):
        d = self.data()
        d.update(direction='short', o=[200-x for x in d['o']],
                 h=[200-x for x in d['l']], l=[200-x for x in d['h']],
                 c=[200-x for x in d['c']],
                 ema20=[200-x for x in d['ema20']])
        ok, reason, diag = find_m5_continuation(**d)
        self.assertTrue(ok, reason)
        self.assertEqual(diag['m5_entry_path'], 'pause_continuation')

    def test_opposite_candle_is_not_valid_continuation(self):
        d = self.data()
        d['o'][-1] = 100.9
        ok, reason, _ = find_m5_continuation(**d)
        self.assertFalse(ok)
        self.assertEqual(reason, 'M5_ALT_NO_DIRECTIONAL_BODY')

    def test_overextended_move_is_not_chased(self):
        d = self.data()
        d['c'][-1] = 101.3
        d['h'][-1] = 101.4
        ok, reason, _ = find_m5_continuation(**d)
        self.assertFalse(ok)
        self.assertEqual(reason, 'M5_ALT_TOO_EXTENDED')

    def test_unconfirmed_breakout_is_not_approved(self):
        d = self.data()
        d['h'][-2] = 101.4
        ok, reason, _ = find_m5_continuation(**d)
        self.assertFalse(ok)
        self.assertEqual(reason, 'M5_ALT_NO_PRICE_CONFIRM')
