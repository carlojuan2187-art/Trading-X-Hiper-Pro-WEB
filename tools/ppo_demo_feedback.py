"""Offline auxiliary learning from actual CLOSED Trading X DEMO PPO trades.

This is an explicitly OFF-POLICY reward-weighted actor refinement, NOT an
on-policy PPO replay. Never claim the resulting holdout is independent if these
trades happened during the holdout period. No broker credentials or network IO.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path

from app.demo.ai_experience import clean_observation

MIN_SAMPLES = 30


def load_feedback(path: str, trained_symbols: set[str]) -> list[dict]:
    """Validate all rows, fail closed on tampering/duplicates rather than skip."""
    rows = []
    seen = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            identifier = str(item["_id"])
            symbol = str(item["symbol"])
            action = str(item["action"])
            if (identifier in seen or item.get("source") != "TRADING_X_PRIVATE_DEMO_CLOSED"
                    or symbol not in trained_symbols or action not in ("LONG", "SHORT")):
                raise ValueError("invalid_or_duplicate_demo_feedback")
            opened = datetime.fromisoformat(item["opened_at"])
            closed = datetime.fromisoformat(item["closed_at"])
            if (opened.tzinfo is None or closed.tzinfo is None or closed < opened
                    or int(item["entry_candle_ms"]) % 300000):
                raise ValueError("invalid_feedback_chronology")
            pnl = float(item["pnl_net_usdc"])
            margin = float(item["margin_usdc"])
            fees = float(item["fees_usdc"])
            if not all(math.isfinite(n) for n in (pnl, margin, fees)) or margin <= 0 or fees < 0:
                raise ValueError("invalid_feedback_economics")
            rows.append({"id": identifier, "symbol": symbol, "action": action,
                         "observation": clean_observation(item["observation"]),
                         "pnl_net_usdc": pnl, "margin_usdc": margin,
                         "closed_at": closed})
            seen.add(identifier)
    rows.sort(key=lambda r: (r["closed_at"], r["id"]))
    return rows


def refine_actor_with_closed_feedback(model, rows: list[dict], *, min_samples: int = MIN_SAMPLES,
                                      lr: float = 0.00001, epochs: int = 2) -> dict:
    """Conservative, bounded signed-return actor update.

    Profitable actions receive more probability; losing actions receive less.
    Uses actual AFTER-FEES PnL. Neither creates trades nor changes risk gates.
    This actor update is NOT PPO because historical transitions are off-policy.
    """
    summary = {"samples": len(rows), "applied": False,
               "method": "off_policy_signed_return_actor_refinement"}
    if len(rows) < min_samples:
        summary["reason"] = f"minimum_{min_samples}_closed_ai_trades_required"
        return summary
    import torch
    import torch.nn.functional as F
    policy = model.policy
    net = policy.mlp_extractor.policy_net
    head = policy.action_net
    observations = torch.tensor([r["observation"] for r in rows], dtype=torch.float32)
    indices = torch.tensor([1 if r["action"] == "LONG" else 2 for r in rows], dtype=torch.long)
    # Signed net PnL/margin capped to avoid one outlier dominating.
    advantages = torch.tensor([max(-1, min(1, r["pnl_net_usdc"]/r["margin_usdc"]))
                               for r in rows], dtype=torch.float32)
    if not torch.any(torch.abs(advantages) > 1e-6):
        summary["reason"] = "no_nonzero_net_outcomes"
        return summary
    with torch.no_grad():
        reference = F.softmax(head(net(observations))[:, :4], dim=-1).detach()
    optimizer = torch.optim.Adam(list(net.parameters()) + list(head.parameters()), lr=lr)
    for _ in range(epochs):
        for start in range(0, len(rows), 64):
            end = start + 64
            logits = head(net(observations[start:end]))[:, :4]
            logprobs = F.log_softmax(logits, dim=-1)
            chosen = logprobs.gather(1, indices[start:end,None]).squeeze(1)
            signed_loss = -(advantages[start:end] * chosen).mean()
            # KL trust penalty keeps the learned historical PPO action
            # distribution close to the starting model, avoiding huge swings.
            p0 = reference[start:end]
            kl = (p0 * (torch.log(p0 + 1e-9) - logprobs)).sum(dim=-1).mean()
            loss = signed_loss + 3.0*kl
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(list(net.parameters())+list(head.parameters()), 0.5)
            optimizer.step()
    summary.update(applied=True, net_feedback_pnl_usdc=round(sum(r["pnl_net_usdc"] for r in rows), 6),
                   latest_closed_at=rows[-1]["closed_at"].isoformat(),
                   positive_samples=sum(r["pnl_net_usdc"] > 0 for r in rows),
                   negative_samples=sum(r["pnl_net_usdc"] < 0 for r in rows))
    return summary
