#!/usr/bin/env python3
"""Print only an allowlist of non-secret strategy/execution settings.

Run inside the existing bot service to identify environment overrides. Does not
import the bot, connect to the database, or send orders.
"""
import json
import os

DEFAULTS = {
    'STRATEGY_ROUTER_MODE': 'enforced',
    'STRATEGY_ROUTER_SETUP_FIRST_ENABLED': 'true',
    'STRATEGY_ROUTER_LIQUIDITY_PROBE_ENABLED': 'true',
    'STRATEGY_ROUTER_RANGE_CONTINUATION_ENABLED': 'true',
    'STRATEGY_ROUTER_RANGE_LIVE_ENABLED': 'false',
    'MTF_H1_PULLBACK_ENABLED': 'true', 'MTF_H1_PULLBACK_MAX_ATR': '0.5',
    'MTF_H1_REQUIRE_EMA200': 'true', 'MTF_H1_TRANSITION_ENABLED': 'true',
    'MTF_H1_TRANSITION_ADX_MIN': '16', 'MTF_H1_ADX_MIN': '12',
    'MTF_M15_PULLBACK_ENABLED': 'true', 'MTF_M15_REQUIRE_EMA200': 'true',
    'MTF_M15_ADX_MIN': '11', 'MTF_MIN_SCORE_TO_SIGNAL': '69',
    'MTF_SL_MIN_PCT': '0.0045', 'MTF_SL_HARD_MAX_PCT': '0.014',
    'MIN_ENTRY_RR': '1.15', 'MTF_RR_MIN': '1.15', 'LIQUIDITY_MIN_FINAL_RR': '1.15',
    'ENTRY_RR_RESERVE': '0.05', 'SOFTWARE_EXIT_GUARD_ENABLED': 'true',
    'MAX_TRADES_PER_DAY': '12', 'MAX_TRADES_PER_HOUR': '4',
    'POSITION_PERCENT': '1.0', 'MAX_TRADE_MARGIN_USDC': '0.0',
    'MIN_CAPITAL_USDC': '5.0', 'LEVERAGE': '10', 'OWNER_FEE_PERCENT': '0.15',
    'SCANNER_MIN_24H_NOTIONAL': '2000000', 'SCANNER_MIN_OPEN_INTEREST': '250000',
    'SCANNER_MAX_SPREAD_BPS': '25', 'EXECUTION_MAX_SPREAD_BPS': '20',
    'SCANNER_MIN_TOP_BOOK_NOTIONAL': '2000', 'SHORTLIST_HARD_CAP': '8',
    'ALLOWED_TRADE_SYMBOLS': '', 'BLOCKED_MEME_KEYWORDS': '(built-in policy)',
}

if __name__ == '__main__':
    print(json.dumps({key: {'value': os.getenv(key, default),
                           'source': 'environment' if key in os.environ else 'code default'}
                      for key, default in DEFAULTS.items()}, indent=2))
