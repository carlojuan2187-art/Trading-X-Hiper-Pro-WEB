#!/usr/bin/env python3
"""Offline PPO experiment for Trading X DEMO, no credentials and no exchange orders.

Install separately: pip install -r requirements-ai-train.txt
Input CSV: timestamp,open,high,low,close,volume,symbol ; timestamp epoch ms,
continuous 5m candles. NO training or heavy ML dependencies on Railway.
"""
from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import time

from app.demo.ai_features import OBS_NAMES, BAR_MS, MODEL_SCHEMA, observation, STOP_ATR, TAKE_ATR


def load_csv(path: Path, symbol: str) -> list[dict]:
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        for line in csv.DictReader(handle):
            if line.get("symbol", "").upper() != symbol:
                continue
            row = {"t": int(line["timestamp"])}
            row.update({x: float(line[y]) for x, y in (
                ("o", "open"), ("h", "high"), ("l", "low"),
                ("c", "close"), ("v", "volume"))})
            rows.append(row)
    rows.sort(key=lambda item: item["t"])
    if len(rows) < 1500:
        raise ValueError("Provide >=1500 REAL, continuous closed candles for this symbol")
    if any(row["t"] % BAR_MS != 0 for row in rows):
        raise ValueError("Candle timestamps must be 5-minute aligned")
    if len(set(r["t"] for r in rows)) != len(rows):
        raise ValueError("duplicate_candle")
    if any(b["t"] - a["t"] != BAR_MS for a, b in zip(rows, rows[1:])):
        raise ValueError("candle_gap")
    for end in range(40, len(rows), 200):
        observation(rows[end-40:end], rows[end-1]["c"]*.9999,
                    rows[end-1]["c"]*1.0001)
    return rows


def environment(datasets: dict[str, list[dict]], *, training: bool,
                profile: dict, seed: int = 1):
    import gymnasium as gym
    import numpy as np
    from gymnasium import spaces

    class PaperEnv(gym.Env):
        metadata = {"render_modes": []}

        def __init__(self):
            super().__init__()
            self.action_space = spaces.MultiDiscrete([4, 3, 3])
            self.observation_space = spaces.Box(low=-3, high=3,
                                                shape=(len(OBS_NAMES),), dtype=np.float32)
            self.random = random.Random(seed)
            self.symbol_rotation = list(datasets)
            self.rotation_index = 0
            self.random.shuffle(self.symbol_rotation)
            self.initial = float(profile["initial_capital_usdc"])
            self.price_impact = (float(profile["slippage_bps"]) +
                                 float(profile["assumed_spread_bps"])) / 10000
            self.fee_rate = float(profile["taker_fee_bps"]) / 10000
            self.leverage = int(profile["leverage"])
            self.margin_cap = float(profile["margin_cap_usdc"])
            self.position_percent = float(profile["position_percent"])
            self.rows = next(iter(datasets.values()))
            self.start = 40
            self.idx = 40
            self.cash = self.initial
            self.pos = None
            self.peak = self.initial
            self.trades = []
            self.max_drawdown = 0.0
            self.flat_action_counts = [0, 0, 0, 0]
            self.position_action_counts = [0, 0, 0, 0]

        def _mark(self, px):
            if self.pos is None:
                return self.cash
            p = self.pos
            return self.cash + p["side"] * (px - p["entry"]) * p["qty"]

        def _obs(self):
            # Observation at step i uses CLOSED bars [i-40, i) only.
            rows = self.rows
            last = rows[self.idx-1]
            spread = 0.0002
            px = last["c"]
            pos = None
            if self.pos:
                pos = {
                    "direction": "long" if self.pos["side"] > 0 else "short",
                    "entry_price": self.pos["entry"],
                    "ai_bars_held": self.idx - self.pos["start"],
                }
            result, _ = observation(rows[self.idx-40:self.idx], px*(1-spread/2),
                                    px*(1+spread/2), pos)
            return np.array(result, dtype=np.float32)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            if training:
                if self.rotation_index and self.rotation_index % len(self.symbol_rotation) == 0:
                    self.random.shuffle(self.symbol_rotation)
                coin = self.symbol_rotation[self.rotation_index % len(self.symbol_rotation)]
                self.rotation_index += 1
                self.rows = datasets[coin]
            else:
                self.rows = next(iter(datasets.values()))
            rows = self.rows
            if training and len(rows) > 700:
                self.idx = self.random.randrange(40, len(rows)-512)
            else:
                self.idx = 40
            self.start = self.idx
            self.cash = self.initial
            self.peak = self.initial
            self.max_drawdown = 0.0
            self.pos = None
            self.trades = []
            self.flat_action_counts = [0, 0, 0, 0]
            self.position_action_counts = [0, 0, 0, 0]
            return self._obs(), {}

        def _close(self, px):
            p = self.pos
            # Adverse spread + slippage + taker on both sides.
            exit_price = px * (1-self.price_impact if p["side"] > 0 else 1+self.price_impact)
            gross = p["side"]*(exit_price-p["entry"])*p["qty"]
            exit_fee = abs(exit_price*p["qty"])*self.fee_rate
            pnl = gross-p["entry_fee"]-exit_fee
            self.cash += gross-exit_fee
            self.trades.append(pnl)
            self.pos = None

        def step(self, action):
            act, stop_bin, take_bin = map(int, action)
            rows = self.rows
            bar = rows[self.idx]
            before = self._mark(rows[self.idx-1]["c"])
            previous_drawdown = max(0, 1-before/max(self.peak, 1e-9))
            (self.position_action_counts if self.pos else self.flat_action_counts)[act] += 1
            px = bar["o"]
            if self.pos and act == 3:
                self._close(px)
            elif not self.pos and act in (1, 2):
                side = 1 if act == 1 else -1
                price = px*(1+self.price_impact if side > 0 else 1-self.price_impact)
                # Match the approved model's broker-independent risk profile.
                margin = min(max(0.0, self.cash*self.position_percent),
                             self.margin_cap)
                size = margin*self.leverage
                qty = size/price if price > 0 else 0
                entry_fee = size*self.fee_rate
                atr = observation(rows[self.idx-40:self.idx],
                                  rows[self.idx-1]["c"]*.9999,
                                  rows[self.idx-1]["c"]*1.0001)[1]
                stop = price - side*STOP_ATR[stop_bin]*atr
                target = price + side*TAKE_ATR[take_bin]*atr
                if min(stop, target) > 0 and size >= 10.2:
                    self.pos = dict(side=side, entry=price, qty=qty,
                                    stop=stop, target=target,
                                    start=self.idx, entry_fee=entry_fee)
                    self.cash -= entry_fee
            if self.pos:
                p = self.pos
                # When BOTH levels touch the same OHLC bar: conservatively stop first.
                stop_hit = bar["l"] <= p["stop"] if p["side"] > 0 else bar["h"] >= p["stop"]
                tp_hit = bar["h"] >= p["target"] if p["side"] > 0 else bar["l"] <= p["target"]
                if stop_hit:
                    # Stop crossed between bars? Fill at the WORSE opening price.
                    adverse_stop = (min(p["stop"], px) if p["side"] > 0
                                    else max(p["stop"], px))
                    self._close(adverse_stop)
                elif tp_hit:
                    self._close(p["target"])
            equity = self._mark(bar["c"])
            self.peak = max(self.peak, equity)
            drawdown = max(0, 1-equity/self.peak) if self.peak > 0 else 1
            self.max_drawdown = max(self.max_drawdown, drawdown)
            # Log equity return is scaled for PPO optimizer, but still comes
            # only from ACTUAL simulated PnL (fees, stops and slippage included).
            # Penalize INCREASES in drawdown, not perpetual time spent in DD.
            # No fake bonus for taking trades; WAIT remains legitimate.
            reward = (100.0*math.log(max(equity, 1e-9)/max(before, 1e-9))
                      - 2.0*max(0, drawdown-previous_drawdown))
            self.idx += 1
            done = self.idx >= len(rows)-1 or equity < self.initial*0.2
            truncated = bool(training and self.idx-self.start >= 512)
            if done or truncated:
                if self.pos:
                    marked_before_exit = equity
                    self._close(bar["c"])
                    equity = self.cash
                    # Terminal reward also charges final execution costs.
                    reward += 100.0*math.log(max(equity, 1e-9)/max(marked_before_exit, 1e-9))
                else:
                    equity = self.cash
            info = {"equity": round(equity, 6), "trades": len(self.trades),
                    "max_drawdown": round(self.max_drawdown, 6),
                    "net_pnl": round(self.cash-self.initial, 6)}
            return (self._obs() if not (done or truncated) else
                    np.zeros(len(OBS_NAMES), dtype=np.float32)), float(reward), done, truncated, info

    return PaperEnv()


def evaluate_one(model, rows, profile):
    env = environment({"validation": rows}, training=False, profile=profile)
    obs, _ = env.reset()
    done = truncated = False
    while not (done or truncated):
        action, _ = model.predict(obs, deterministic=True)
        obs, _, done, truncated, info = env.step(action)
    profits = [x for x in env.trades if x > 0]
    losses = [-x for x in env.trades if x < 0]
    return {
        "trades": len(env.trades), "wins": len(profits),
        "net_pnl_usdc": round(env.cash-env.initial, 4),
        "profit_factor": round(sum(profits)/sum(losses), 4) if losses else None,
        "max_drawdown": round(env.max_drawdown, 4),
        "holdout_candles": len(rows),
        "flat_policy_actions": dict(zip(("WAIT","LONG","SHORT","CLOSE"), env.flat_action_counts)),
        "in_position_policy_actions": dict(zip(("WAIT","LONG","SHORT","CLOSE"), env.position_action_counts)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbols", default="BTC,ETH,SOL", help="auto: train on every downloaded LIVE-eligible coin in CSV")
    ap.add_argument("--universe-report", default="", help="JSON coverage report from LIVE downloader")
    ap.add_argument("--output", default="models/trading_x_ppo")
    ap.add_argument("--steps", type=int, default=250_000)
    ap.add_argument("--initial-capital", type=float, default=100)
    ap.add_argument("--leverage", type=int, default=10)
    ap.add_argument("--margin-cap", type=float, default=25)
    ap.add_argument("--position-percent", type=float, default=1.0)
    ap.add_argument("--taker-fee-bps", type=float, default=5)
    ap.add_argument("--slippage-bps", type=float, default=2)
    ap.add_argument("--assumed-spread-bps", type=float, default=1)
    ap.add_argument("--demo-feedback", default="", help="PRIVATE JSONL of closed PPO DEMO experiences (never GitHub)")
    ap.add_argument("--min-feedback-trades", type=int, default=30)
    args = ap.parse_args()
    if args.steps < 20_000:
        ap.error("minimum 20000 steps")
    if (not 10 <= args.initial_capital <= 100000 or
        not 1 <= args.leverage <= 50 or
        not 1 <= args.margin_cap <= 1000 or
        not 0 < args.position_percent <= 1 or
        not 0 <= args.taker_fee_bps <= 50 or
        not 0 <= args.slippage_bps <= 50 or
        not 0 <= args.assumed_spread_bps <= 50):
        ap.error("invalid broker research profile")
    profile = {
        "initial_capital_usdc": args.initial_capital,
        "leverage": args.leverage,
        "margin_cap_usdc": args.margin_cap,
        "position_percent": args.position_percent,
        "taker_fee_bps": args.taker_fee_bps,
        "slippage_bps": args.slippage_bps,
        "assumed_spread_bps": args.assumed_spread_bps,
    }
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_checker import check_env
        import torch
    except ImportError as exc:
        raise SystemExit("Install offline training dependencies from requirements-ai-train.txt") from exc
    universe_report = None
    if args.universe_report:
        universe_report = json.loads(Path(args.universe_report).read_text(encoding="utf-8"))
    if args.symbols.strip().lower() == "auto":
        with Path(args.csv).open(newline="", encoding="utf-8") as handle:
            symbols = tuple(sorted({str(row.get("symbol") or "").upper()
                                    for row in csv.DictReader(handle) if row.get("symbol")}))
        if universe_report is None or universe_report.get("source") != "LIVE_SCANNER_PREFILTER":
            ap.error("--symbols auto requires --universe-report for the LIVE training data")
        report_symbols = {row["symbol"] for row in universe_report["downloaded"]}
        if set(symbols) != report_symbols:
            ap.error("Downloaded LIVE coin set differs from the audited training CSV")
        requested = universe_report["requested"]
        if not requested or len(symbols) < 10 or len(symbols)/len(requested) < 0.8:
            ap.error("Insufficient eligible LIVE training data coverage")
    else:
        symbols = tuple(dict.fromkeys(x.strip().upper() for x in args.symbols.split(",") if x.strip()))
    if not symbols or len(symbols) > 250:
        ap.error("Choose between 1 and 250 symbols")
    datasets = {symbol: load_csv(Path(args.csv), symbol) for symbol in symbols}
    train = {symbol: rows[:int(len(rows)*0.75)] for symbol, rows in datasets.items()}
    test = {symbol: rows[int(len(rows)*0.75)-40:] for symbol, rows in datasets.items()}
    env = environment(train, training=True, profile=profile)
    check_env(env)
    model = PPO("MlpPolicy", env, verbose=1, seed=17, device="cpu",
                learning_rate=1e-4, ent_coef=0.01, n_steps=1024,
                batch_size=256, gamma=0.995, gae_lambda=0.97,
                policy_kwargs={"net_arch": {"pi": [32, 32], "vf": [32, 32]},
                               "activation_fn": torch.nn.Tanh})
    # Balanced 512-bar episodes, at least 2 training episodes per eligible
    # asset. A large LIVE pool must NEVER be silently under-sampled by a fixed
    # 20k-step BTC/ETH/SOL benchmark or random-symbol resets.
    effective_steps = max(args.steps, len(symbols) * 512 * 2)
    print(f"TRAINING_COVERAGE symbols={len(symbols)} requested_steps={args.steps} "
          f"effective_steps={effective_steps} min_episodes_per_symbol=2", flush=True)
    model.learn(total_timesteps=effective_steps)
    feedback_summary = {"samples": 0, "applied": False, "reason": "no_private_feedback_supplied"}
    if args.demo_feedback:
        from tools.ppo_demo_feedback import load_feedback, refine_actor_with_closed_feedback
        records = load_feedback(args.demo_feedback, set(symbols))
        feedback_summary = refine_actor_with_closed_feedback(
            model, records, min_samples=args.min_feedback_trades)
        # Existing historical test is NOT prospective validation when
        # DEMO feedback observations overlap the test's timeframe.
        last_train_close = max(rows[-1]["t"] + BAR_MS for rows in train.values())
        feedback_summary["holdout_independent_of_feedback"] = (
            all(int(r["closed_at"].timestamp()*1000) <= last_train_close for r in records))
        if not feedback_summary["holdout_independent_of_feedback"]:
            feedback_summary["warning"] = "Research only: wait for future out-of-sample data after DEMO feedback"
        print("DEMO_FEEDBACK_REFINEMENT", json.dumps(feedback_summary, default=str), flush=True)
    result = {symbol: evaluate_one(model, rows, profile) for symbol, rows in test.items()}
    total_trades = sum(v["trades"] for v in result.values())
    net_pnl = sum(v["net_pnl_usdc"] for v in result.values())
    # Financial diagnostics are measured on holdout only, not training rewards.
    quality_reasons = []
    if total_trades < max(30, len(symbols)):
        quality_reasons.append("INSUFFICIENT_HOLDOUT_TRADES")
    if net_pnl <= 0:
        quality_reasons.append("NEGATIVE_OR_FLAT_HOLDOUT_PNL")
    if max(v["max_drawdown"] for v in result.values()) > 0.25:
        quality_reasons.append("EXCESSIVE_INDIVIDUAL_DRAWDOWN")
    if feedback_summary.get("holdout_independent_of_feedback") is False:
        quality_reasons.append("FEEDBACK_OVERLAPS_HISTORICAL_HOLDOUT")
    quality_report = {"trades": total_trades, "net_pnl_sum_of_independent_100usd_accounts": round(net_pnl, 4),
                      "markets": len(symbols), "candidate_quality_reasons": quality_reasons,
                      "model_approved": False}
    print("PPO_HOLDOUT_QUALITY", json.dumps(quality_report), flush=True)
    policy = model.policy
    def layer(module):
        return {"weight": module.weight.detach().cpu().tolist(),
                "bias": module.bias.detach().cpu().tolist()}
    actor = {"activation": "tanh",
             "hidden": [layer(m) for m in policy.mlp_extractor.policy_net
                        if isinstance(m, torch.nn.Linear)],
             "head": layer(policy.action_net)}
    if len(actor["hidden"]) != 2 or len(actor["head"]["bias"]) != 10:
        raise ValueError("Unexpected PPO architecture")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(actor, separators=(",", ":"), allow_nan=False).encode()
    (output/"actor.json").write_bytes(raw)
    meta = {
        "schema": MODEL_SCHEMA, "version": f"ppo-{int(time.time())}",
        "observation_names": list(OBS_NAMES), "action_dims": [4, 3, 3],
        "actor_file": "actor.json", "actor_sha256": sha256(raw).hexdigest(),
        "trained_symbols": list(symbols), "approved_for_demo": False,
        "training_candles": {s: len(b) for s, b in train.items()}, "holdout": result,
        "training_algorithm": "PPO", "steps": effective_steps,
        "requested_steps": args.steps,
        "symbol_sampling": "balanced_round_robin_512_bar_episodes",
        "reward_contract": "scaled_log_equity_return_minus_incremental_drawdown_v1",
        "training_entropy_coef": 0.01,
        "demo_feedback": feedback_summary,
        "candidate_quality": quality_report,
        "runtime_profile": profile,
        "universe_source": universe_report.get("source") if universe_report else "EXPLICIT_SYMBOLS",
        "universe_snapshot_timestamp_ms": (universe_report.get("snapshot_timestamp_ms")
                                            if universe_report else None),
        "universe_requested_symbols": universe_report.get("requested") if universe_report else list(symbols),
        "universe_excluded": universe_report.get("excluded", {}) if universe_report else {},
        "universe_coverage": universe_report.get("coverage", 1.0) if universe_report else 1.0,
    }
    (output/"manifest.json").write_text(json.dumps(meta, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"model_dir": str(output), "holdout": result,
                      "feedback": feedback_summary, "quality": quality_report,
                      "approval": "DENIED until independent review"}, indent=2))


if __name__ == "__main__":
    main()
