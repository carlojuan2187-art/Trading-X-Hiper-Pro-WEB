"""One symbol policy shared by scanner and every strategy."""
import os

DEFAULT_BLOCKED = {
    'DOGE', 'SHIB', 'PEPE', 'BONK', 'FLOKI', 'WIF', 'POPCAT', 'PENGU',
    'TURBO', 'MOG', 'BOME', 'MYRO', 'BRETT', 'NEIRO', 'MEME', 'BABYDOGE',
    'KISHU', 'WOJAK', 'PONKE', 'MEW', 'TRUMP', 'MAGA', 'HARRYPOTTEROBAMA', 'HYPE',
}


def base_coin(symbol):
    value = str(symbol or '').strip().upper()
    for suffix in ('-PERP', '-USDC', '-USDT', '-USD'):
        if value.endswith(suffix):
            return value[:-len(suffix)]
    return value


ALLOWED_SYMBOLS = {base_coin(x) for x in os.getenv('ALLOWED_TRADE_SYMBOLS', '').split(',') if x.strip()}
_raw = os.getenv('BLOCKED_MEME_KEYWORDS')
BLOCKED_MEME_KEYWORDS = ({x.strip().upper() for x in _raw.split(',') if x.strip()}
                         if _raw and _raw.strip() else set(DEFAULT_BLOCKED))
# Explicit NONE avoids accidentally disabling the default policy with an empty value.
if BLOCKED_MEME_KEYWORDS == {'NONE'}:
    BLOCKED_MEME_KEYWORDS = set()


def rejection_reason(symbol):
    coin = base_coin(symbol)
    if not coin:
        return 'BAD_SYMBOL'
    if ALLOWED_SYMBOLS and coin not in ALLOWED_SYMBOLS:
        return 'SYMBOL_NOT_ALLOWED'
    if any(token in coin for token in BLOCKED_MEME_KEYWORDS):
        return 'MEME_SYMBOL_BLOCKED'
    return ''
