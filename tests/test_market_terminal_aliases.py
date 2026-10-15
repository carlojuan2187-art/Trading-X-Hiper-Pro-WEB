"""Check legacy DEMO/LIVE history aliases at the public market API boundary, offline."""
import re
import unittest

from test_operational_regressions import functions


class MarketSymbolAliasTests(unittest.TestCase):
    def test_public_market_canonical_supports_legacy_perpetuals(self):
        class MarketError(ValueError):
            pass
        ns = functions('app/market/terminal.py', {'canonical_coin'}, {
            're': re,
            '_SYMBOL_RE': re.compile(r'^[A-Z0-9_:]{1,48}$'),
            'MarketError': MarketError,
        })
        canonical = ns['canonical_coin']
        for raw, expected in [
            ('FET-PERP', 'FET'),
            ('FET-PERP/USDC', 'FET'),
            ('fet-perp', 'FET'),
            ('BTC-USDC', 'BTC'),
            ('SOL/USDT', 'SOL'),
            ('xyz:ABC', 'XYZ:ABC'),
        ]:
            self.assertEqual(canonical(raw), expected, raw)
        for bad in ('FET-PERP-NOTREAL', '../FET', '', 'a' * 49):
            with self.assertRaises(MarketError):
                canonical(bad)


if __name__ == '__main__':
    unittest.main()
