# PPO V4: active paper research entry mode

The research worker uses the V4 policy normally. When a V4 unapproved PPO
actor chooses WAIT on a flat account, and `DEMO_AI_ACTIVE_PAPER_MODE` is
not `false`, it may choose the PPO actor's preferred LONG/SHORT head
**only** when the directional head has >=54% conditional preference AND
at least two closed-bar M5/M15/H1 skill confirmations, including a
positive near-term M5 or M15 EMA trend, and no strong higher-timeframe
contradiction. The direction is always the PPO actor's own preference.
This is a deterministic **experimental selection calibration**, not a
demonstration of a profitable PPO policy. It is not random and does not
manufacture prices or trades. No additional account loss cooldowns or
per-day quotas. Paper execution still enforces fresh book, spread,
sizing, margin, price, SL/TP and single-open-position gates.

Only `research_only` V4 inference permits this behavior; V2/V3, approved
models, LIVE and open positions are unchanged. Records have
`ai_decision_selection=PPO_V4_PAPER_DIRECTIONAL` and a conditional
direction confidence, so experimental trades are explicitly identifiable
and cannot be confused with ordinary PPO argmax. Disable immediately via
`DEMO_AI_ACTIVE_PAPER_MODE=false`.

Research benchmark's previous PPO-v4-1792529364 holdout scored zero trades
across 54 markets. The previously deployed V4-v1 actor also had negative
holdout performance and is *not approved*. Never use this research mode
for LIVE trading. Retrain a new policy with a real positive net holdout
before considering promotion.
