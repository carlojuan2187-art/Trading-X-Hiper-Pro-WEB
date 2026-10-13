"""Position margin determined exclusively by the configured capital variables."""
import math
from app.config import POSITION_PERCENT, MAX_TRADE_MARGIN_USDC


def validate_trade_conditions(balance: float, strength: float):
    try:
        balance = float(balance)
        if not math.isfinite(balance) or balance <= 0:
            raise ValueError()
        margin = min(balance * POSITION_PERCENT, balance)
        if MAX_TRADE_MARGIN_USDC > 0:
            margin = min(margin, MAX_TRADE_MARGIN_USDC)
        margin = math.floor(margin * 1_000_000) / 1_000_000
        if margin <= 0:
            return {'ok': False, 'reason': 'Position size inválido'}
        return {'ok': True, 'position_size': margin}
    except (ValueError, TypeError, OverflowError):
        return {'ok': False, 'reason': 'INVALID_CAPITAL_INPUT'}
