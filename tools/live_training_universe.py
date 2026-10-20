"""Universe for OFFLINE PPO research: reproduce LIVE scanner's market prefilter.

LIVE uses metaAndAssetCtxs + app.symbol_policy + score thresholds. The LIVE
L2 rotation is intentionally not used to permanently exclude symbols: an asset
can qualify in later scans when spread/depth improve. We train against the
ENTIRE prefiltered pool, not just the 25 assets checked in one LIVE scan.
This module never places orders and needs no MongoDB/Telegram credentials.
"""
from __future__ import annotations

import math
import os

from app.symbol_policy import rejection_reason


def scanner_limits() -> dict:
    """Same defaults/variable names used by app.config, without importing secrets."""
    return {
        "min_day_notional": float(os.getenv("SCANNER_MIN_24H_NOTIONAL", "2000000")),
        "min_open_interest": float(os.getenv("SCANNER_MIN_OPEN_INTEREST", "250000")),
        "live_scanner_depth": int(os.getenv("SCANNER_DEPTH", "80")),
        "live_l2_rotation_depth": int(os.getenv("SCANNER_SHORTLIST_DEPTH_FOR_L2", "25")),
        "max_spread_bps": float(os.getenv("SCANNER_MAX_SPREAD_BPS", "25")),
        "min_top_book_notional": float(os.getenv("SCANNER_MIN_TOP_BOOK_NOTIONAL", "2000")),
    }


def select_live_prefilter(data: object, *, limits: dict | None = None) -> list[dict]:
    """Use LIVE _fetch_markets/_score_symbol inputs, policy and rank.

    Return ALL eligible markets across scanning rotations. Current L2 quality
    remains a separate LIVE/execution safety check, not an offline label.
    """
    limits = limits or scanner_limits()
    if (not isinstance(data, list) or len(data) < 2 or
            not isinstance(data[0], dict) or not isinstance(data[1], list) or
            not isinstance(data[0].get("universe"), list)):
        raise ValueError("invalid_hyperliquid_metaAndAssetCtxs")
    universe = data[0]["universe"]
    result = []
    seen = set()
    for i, ctx in enumerate(data[1]):
        if not isinstance(ctx, dict):
            continue
        asset = universe[i] if i < len(universe) else {}
        coin = str(ctx.get("coin") or (asset.get("name") if isinstance(asset, dict) else "") or "").strip().upper()
        if not coin or coin in seen or rejection_reason(coin):
            continue
        try:
            px = float(ctx.get("markPx", 0))
            prev = float(ctx.get("prevDayPx", px))
            volume = float(ctx.get("dayNtlVlm", 0))
            oi_units = float(ctx.get("openInterest", 0))
        except (ValueError, TypeError, OverflowError):
            continue
        if not all(math.isfinite(x) for x in (px, prev, volume, oi_units)):
            continue
        if px <= 0 or volume < limits["min_day_notional"]:
            continue
        oi_notional = max(0.0, oi_units * px)
        if oi_notional < limits["min_open_interest"]:
            continue
        change = ((px - prev) / prev * 100) if prev > 0 else 0.0
        score = round(min(volume / 25_000_000, 1.0) * .50 +
                      min(oi_notional / 15_000_000, 1.0) * .30 +
                      min(abs(change) / 6, 1.0) * .20, 4)
        result.append({"symbol": coin, "scanner_score": score,
                       "day_notional": round(volume, 2),
                       "open_interest_notional": round(oi_notional, 2)})
        seen.add(coin)
    result.sort(key=lambda x: (-x["scanner_score"], x["symbol"]))
    return result
