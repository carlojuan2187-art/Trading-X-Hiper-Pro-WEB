"""Compatibility shim for the active MTF breakout/reset strategy.

The production strategy lives in ``app.strategies.breakout_reset``.
This module intentionally re-exports that implementation so legacy imports
cannot drift into a second, contradictory copy of the strategy.
"""

from app.strategies.breakout_reset import *  # noqa: F401,F403
from app.strategies.breakout_reset import DEFAULT_STRATEGY, STRATEGY_ID, STRATEGY_MODEL, STRATEGY_VERSION

__all__ = [
    "DEFAULT_STRATEGY",
    "STRATEGY_ID",
    "STRATEGY_MODEL",
    "STRATEGY_VERSION",
]
