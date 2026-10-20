# PPO V4: DEMO research activation (2026-10-20)

Source branch: `feature/demo-autonomous-ppo`. The WEB/LIVE branch `main` is unchanged.

Runtime: `app.demo.worker` handles the independent PPO actor only. V4
requires 480 contiguous CLOSED 5-minute candles, including completed M15/H1
features. V4 actions are WAIT/LONG/SHORT/CLOSE/PROTECT/TRAIL. Stops can
only tighten within the PAPER simulator. Existing positions from earlier
models keep their protective SL/TP until close; no model-switch forced close.

The pinned GitHub Actions artifact `12242993756` (from run `39374049128`)
contains V4 actor `ppo-v4-1792474705`. The installer workflow first runs
runtime regression tests, checks actor SHA256 and paper-only approval status,
then replaces only `models/trading_x_ppo/actor.json` and `manifest.json`
on the DEMO branch. It never edits LIVE or resets Mongo paper history.

## Mandatory DEMO-only environment
```dotenv
DEMO_ENABLED=true
DEMO_WORKER_MODE=true
DEMO_DECISION_ENGINE=ai_ppo
DEMO_AI_PAPER_RESEARCH_MODE=true
DEMO_AI_MANIFEST=models/trading_x_ppo/manifest.json
DEMO_AI_MAX_MARGIN_USDC=25
DEMO_AI_SYMBOLS=AUTO
LEVERAGE=10
POSITION_PERCENT=1.0
MAX_TRADE_MARGIN_USDC=25
DEMO_TAKER_FEE_BPS=5
DEMO_SLIPPAGE_BPS=2
```

The runtime fails closed on absent/mismatched/altered files or profile and
will not switch silently to the legacy scanner/router.

## Quality warning
Last V4 holdout: 46 symbols, 4 closed trades, 0 wins, aggregated net
PnL -13.8154 USDC (46 separate 100-USDC paper accounts). Candidate remains
unapproved: INSUFFICIENT_HOLDOUT_TRADES and NEGATIVE_OR_FLAT_NET_PNL.
Deployment to PAPER DEMO is **experimental**, not evidence of profitability.

Confirm logs display `DEMO_AI_PPO_ONLY`, `DEMO_AI_PAPER_RESEARCH_ACTIVE`,
and `DEMO_AI_UNIVERSE`; check incoming candles and CLOSED/OPENED
trade traces. Keep one replica. Stop new entries and use a reviewed
rollback if V4 cannot operate; do not reset statistics. Never apply this
unapproved model to LIVE.
