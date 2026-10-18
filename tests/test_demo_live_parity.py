"""Guardrails: DEMO invokes LIVE decision rules but cannot place LIVE orders."""
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _method(path, function):
    tree = ast.parse((ROOT / path).read_text())
    return next(n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == function)


class DemoLiveParityContract(unittest.TestCase):
    def test_shared_selector_scans_and_evaluates_using_live_rules(self):
        method = _method('app/trading_engine.py',
                         '_select_best_signal_from_scanner_shortlist')
        ranked = [n for n in ast.walk(method)
                  if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)
                  and n.func.id == 'get_ranked_symbols']
        self.assertEqual(len(ranked), 1)
        mode = next(k.value for k in ranked[0].keywords
                    if k.arg == 'execution_mode')
        self.assertIsInstance(mode, ast.Constant)
        self.assertEqual(mode.value, 'live')

        context_assignments = [n for n in ast.walk(method)
                               if isinstance(n, ast.Assign)
                               and any(isinstance(t, ast.Subscript)
                                       and isinstance(t.value, ast.Name)
                                       and t.value.id == 'strategy_ctx'
                                       and isinstance(t.slice, ast.Constant)
                                       and t.slice.value == '_execution_mode'
                                       for t in n.targets)]
        self.assertEqual(len(context_assignments), 1)
        self.assertIsInstance(context_assignments[0].value, ast.Constant)
        self.assertEqual(context_assignments[0].value.value, 'live')

        method_text = ast.get_source_segment(
            (ROOT / 'app/trading_engine.py').read_text(), method)
        self.assertNotIn('DEMO_MTF_REGIME_CONFLICT', method_text)

    def test_worker_uses_demo_execution_but_single_live_ranked_choice(self):
        method = _method('app/demo/worker.py', 'tick')
        # tick is a compatibility path; it must never place actual orders.
        self.assertNotIn('execute_trade_cycle', ast.unparse(method))
        source = (ROOT / 'app/demo/worker.py').read_text()
        self.assertIn("execution_mode='demo', demo_fallback_limit=1", source)
        self.assertIn("execution_mode='demo'", source)
        self.assertNotIn('execute_trade_cycle(', source)
        self.assertNotIn('open_live_position(', source)

    def test_extra_demo_entry_guards_and_auto_break_even_are_disabled(self):
        source = (ROOT / 'app/demo/simulator.py').read_text()
        self.assertNotIn('demo_symbol_loss_gate(', source)
        self.assertNotIn('DEMO_M5_ALT_PRICE_MOVED', source)
        config = (ROOT / 'app/demo/settings.py').read_text()
        self.assertIn('DEMO_BREAK_EVEN_ENABLED = False', config)
        strategy = (ROOT / 'app/strategies/breakout_reset.py').read_text()
        self.assertNotIn('find_m5_continuation', strategy)


if __name__ == '__main__':
    unittest.main()
