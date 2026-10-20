"""Private, append-only DEMO PPO experiences: never reads or changes LIVE.

Only paper-trade decisions with real entry observations and closed net results
are eligible for future OFFLINE training. No Telegram/user credentials exported.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.demo.ai_features import OBS_NAMES, ADVANCED_OBS_NAMES, ACTIONS


def clean_observation(values) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) not in (len(OBS_NAMES), len(ADVANCED_OBS_NAMES)):
        raise ValueError("missing_or_incompatible_ai_observation")
    cleaned = [float(x) for x in values]
    if not all(math.isfinite(x) and -3 <= x <= 3 for x in cleaned):
        raise ValueError("invalid_ai_observation")
    return cleaned


def build_closed_experience(trade: dict) -> dict | None:
    """Sanitize a fully CLOSED AI trade for an idempotent private Mongo record."""
    if trade.get("decision_source") != "ai_ppo_only":
        return None
    if not trade.get("closed_at") or not trade.get("pnl_net_usdc"):
        # Zero PnL is allowed, but the field must actually exist.
        if "pnl_net_usdc" not in trade or not trade.get("closed_at"):
            return None
    try:
        obs = clean_observation(trade["ai_entry_observation"])
        action = str(trade["direction"]).upper()
        if action not in ("LONG", "SHORT"):
            return None
        trade_id = str(trade["id"])
        version = str(trade["ai_model_version"])
        symbol = str(trade["symbol"]).upper().removesuffix("-PERP")
        if not trade_id or not version or not symbol:
            return None
        opened = datetime.fromisoformat(str(trade["opened_at"]).replace("Z", "+00:00"))
        closed = datetime.fromisoformat(str(trade["closed_at"]).replace("Z", "+00:00"))
        if opened.tzinfo is None or closed.tzinfo is None or closed < opened:
            return None
        pnl = float(trade["pnl_net_usdc"])
        margin = float(trade["margin_usdc"])
        fees = float(trade.get("audit_total_fees_usdc", 
                               float(trade.get("entry_fee_usdc", 0)) + 
                               float(trade.get("exit_fee_usdc", 0))))
        if not all(map(math.isfinite, (pnl, margin, fees))) or margin <= 0 or fees < 0:
            return None
        return {
            "_id": f"{version}:{trade_id}",
            "source": "TRADING_X_PRIVATE_DEMO_CLOSED",
            "model_version": version,
            "symbol": symbol,
            "action": action,
            "observation": obs,
            "entry_candle_ms": int(trade["ai_decision_candle_ms"]),
            "opened_at": opened.astimezone(timezone.utc).isoformat(),
            "closed_at": closed.astimezone(timezone.utc).isoformat(),
            "pnl_net_usdc": round(pnl, 8),
            "margin_usdc": round(margin, 8),
            "fees_usdc": round(fees, 8),
            "close_reason": str(trade.get("close_reason") or "")[:64],
        }
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def backfill_closed_experiences(accounts_collection, experiences_collection) -> dict:
    """Recover feedback after a transient Mongo archive failure or worker restart.

    Reads only retained DEMO history, never LIVE. Upserts are idempotent. Runs
    in a dedicated background thread so WS exits and protective orders continue.
    """
    import logging
    log = logging.getLogger(__name__)
    recovered, existing, invalid, errors = 0, 0, 0, 0
    cursor = accounts_collection.find(
        {"history.decision_source": "ai_ppo_only"},
        {"history": 1}
    )
    for account in cursor:
        for trade in account.get("history", []):
            if trade.get("decision_source") != "ai_ppo_only":
                continue
            experience = build_closed_experience(trade)
            if experience is None:
                invalid += 1
                continue
            try:
                result = experiences_collection.update_one(
                    {"_id": experience["_id"]},
                    {"$setOnInsert": experience}, upsert=True)
                if result.upserted_id is not None:
                    recovered += 1
                else:
                    existing += 1
            except Exception:
                errors += 1
                log.exception("DEMO_AI_EXPERIENCE_BACKFILL_FAILED trade=%s", trade.get("id"))
    totals = {"recovered": recovered, "already_saved": existing,
              "legacy_missing_entry_state": invalid, "errors": errors}
    log.info("DEMO_AI_EXPERIENCE_BACKFILL totals=%s", totals)
    return totals
