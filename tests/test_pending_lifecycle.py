import unittest
from app.pending_setup import find_pending_reset

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
