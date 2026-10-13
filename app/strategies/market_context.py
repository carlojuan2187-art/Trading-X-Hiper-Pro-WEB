"""Compatibility wrapper for the canonical closed-candle market context.

Historically the strategy package had a second candle downloader. Keeping two
implementations allowed one path to evaluate an in-progress candle while the
main engine used another. All callers now share ``app.market_context``.
"""

from app.market_context import (  # noqa: F401
    TF_5M,
    TF_15M,
    TF_1H,
    adx,
    atr,
    build_market_context,
    build_timeframe_context,
    ema,
    extract_series,
    fetch_candles,
    interval_ms,
    is_stale,
    last,
    parse_candle,
    pct_change,
    rma,
)

__all__ = [
    "TF_5M",
    "TF_15M",
    "TF_1H",
    "adx",
    "atr",
    "build_market_context",
    "build_timeframe_context",
    "ema",
    "extract_series",
    "fetch_candles",
    "interval_ms",
    "is_stale",
    "last",
    "parse_candle",
    "pct_change",
    "rma",
]
