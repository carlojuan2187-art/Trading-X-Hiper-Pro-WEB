"""Read-only diagnostic of REAL Trading X private paper-trade outcomes.

Print AGGREGATE statistics only: no user IDs, credentials, tokens, wallet
addresses, entry-level observations, or individual private trade records.
May run in DEMO worker startup; does not mutate Mongo or place orders.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import logging
import math

log = logging.getLogger(__name__)


def _date(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def summarize(rows: list[dict], *, source: str):
    pnl = [_number(t.get("pnl_net_usdc")) for t in rows]
    wins = [n for n in pnl if n > 0]
    losses = [-n for n in pnl if n < 0]
    fees = sum(_number(t.get("fees_usdc", t.get("audit_total_fees_usdc",
                     _number(t.get("entry_fee_usdc")) +
                     _number(t.get("exit_fee_usdc"))))) for t in rows)
    reasons = Counter(str(t.get("close_reason") or "UNKNOWN") for t in rows)
    symbols = defaultdict(lambda: {"trades": 0, "pnl_net_usdc": 0.0})
    for t, net in zip(rows, pnl):
        sym = str(t.get("symbol") or "UNKNOWN").replace("-PERP", "").upper()
        symbols[sym]["trades"] += 1
        symbols[sym]["pnl_net_usdc"] += net
    worst = sorted(symbols.items(), key=lambda x: x[1]["pnl_net_usdc"])[:8]
    return {
        "source": source, "closed": len(rows), "wins": len(wins),
        "losses": len(losses), "breakeven": len(rows)-len(wins)-len(losses),
        "win_rate_pct": round(100*len(wins)/len(rows), 2) if rows else None,
        "pnl_net_usdc": round(sum(pnl), 5),
        "gross_profit_usdc": round(sum(wins), 5),
        "gross_loss_usdc": round(sum(losses), 5),
        "profit_factor": round(sum(wins)/sum(losses), 3) if losses else None,
        "sum_fees_usdc": round(fees, 5),
        "average_net_usdc": round(sum(pnl)/len(rows), 5) if rows else None,
        "close_reasons": dict(reasons.most_common(12)),
        "worst_symbols": [
            {"symbol": symbol, "trades": v["trades"],
             "pnl_net_usdc": round(v["pnl_net_usdc"], 5)}
            for symbol, v in worst
        ],
    }


def inspect(*, hours=24):
    from app.database import db
    cutoff = datetime.now(timezone.utc)-timedelta(hours=hours)
    experiences = list(db["demo_ai_experiences"].find(
        {"source": "TRADING_X_PRIVATE_DEMO_CLOSED",
         "closed_at": {"$gte": cutoff.isoformat()}},
        {"_id": 0, "model_version": 1, "symbol": 1, "action": 1,
         "closed_at": 1, "pnl_net_usdc": 1, "fees_usdc": 1,
         "margin_usdc": 1, "close_reason": 1},
    ).limit(50000))
    # The stored closed_at is UTC ISO. Never expose identifiers.
    experiences = [x for x in experiences if (d:=_date(x.get("closed_at"))) and d >= cutoff]
    accounts = list(db["demo_accounts"].find({}, {
        "_id": 0, "enabled": 1, "position": 1, "history": 1,
        "balance_usdc": 1, "initial_equity_usdc": 1,
        "realized_pnl_usdc": 1, "trades_closed": 1,
    }).limit(2000))
    recent_trades = []
    open_positions = []
    for a in accounts:
        for t in a.get("history") or []:
            if (t.get("decision_source") == "ai_ppo_only" and
                (d:=_date(t.get("closed_at"))) and d >= cutoff):
                recent_trades.append(t)
        p = a.get("position") or {}
        if p.get("decision_source") == "ai_ppo_only":
            open_positions.append(p)
    models = {}
    for version in sorted({str(r.get("model_version") or "unknown") for r in experiences}):
        models[version] = summarize(
            [x for x in experiences if str(x.get("model_version") or "unknown") == version],
            source="persisted_feedback")
    # Not all older trades have enough entry context to be persisted as
    # experiences; quantify the gap rather than pretend all are sampled.
    current = {
        "diagnostic": "TRADING_X_DEMO_PPO_READ_ONLY",
        "lookback_hours": hours, "accounts_total": len(accounts),
        "accounts_enabled": sum(bool(x.get("enabled")) for x in accounts),
        "accounts_with_paper_positions": len(open_positions),
        "open_paper_unrealized_sum_excluding_paid_entry_fee": round(
            sum(_number(p.get("unrealized_pnl_usdc")) for p in open_positions), 5),
        "recent_ai_history": summarize(recent_trades, source="retained_account_history"),
        "durable_feedback": summarize(experiences, source="private_experiences"),
        "by_model": models,
        "retained_history_missing_durable_feedback_possible":
            max(0, len(recent_trades)-len(experiences)),
        "max_historical_retention_per_account": 200,
    }
    return current


def main():
    try:
        print("DEMO_AI_RUNTIME_AUDIT "+json.dumps(inspect(), sort_keys=True), flush=True)
    except Exception as exc:
        # Diagnostic must never block fills/reconnects if database has outage.
        log.exception("DEMO_AI_RUNTIME_AUDIT_FAILED diagnostic_only=%s", type(exc).__name__)


if __name__ == "__main__":
    main()
