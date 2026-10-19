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
    if len(set(r["t"] for r in rows)) != len(rows):
        raise ValueError("duplicate_candle")
    if any(b["t"] - a["t"] != BAR_MS for a, b in zip(rows, rows[1:])):
        raise ValueError("candle_gap")
    for end in range(40, len(rows), 200):
        observation(rows[end-40:end], rows[end-1]["c"]*.9999,
                    rows[end-1]["c"]*1.0001)
    return rows


def environment(datasets: dict[str, list[dict]], *, training: bool, seed: int = 1):
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
            self.rows = next(iter(datasets.values()))
            self.start = 40
            self.idx = 40
            self.cash = 100.0
            self.pos = None
            self.peak = 100.0
            self.trades = []
            self.max_drawdown = 0.0

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
            self.rows = datasets[self.random.choice(tuple(datasets))] if training else next(iter(datasets.values()))
            rows = self.rows
            if training and len(rows) > 700:
                self.idx = self.random.randrange(40, len(rows)-512)
            else:
                self.idx = 40
            self.start = self.idx
            self.cash = 100.0
            self.peak = 100.0
            self.max_drawdown = 0.0
            self.pos = None
            self.trades = []
            return self._obs(), {}

        def _close(self, px):
            p = self.pos
            # Adverse spread + slippage + taker on both sides.
            exit_price = px * (1-0.0003 if p["side"] > 0 else 1+0.0003)
            gross = p["side"]*(exit_price-p["entry"])*p["qty"]
            exit_fee = abs(exit_price*p["qty"])*0.0005
            pnl = gross-p["entry_fee"]-exit_fee
            self.cash += gross-exit_fee
            self.trades.append(pnl)
            self.pos = None

        def step(self, action):
            act, stop_bin, take_bin = map(int, action)
            rows = self.rows
            bar = rows[self.idx]
            before = self._mark(rows[self.idx-1]["c"])
            px = bar["o"]
            if self.pos and act == 3:
                self._close(px)
            elif not self.pos and act in (1, 2):
                side = 1 if act == 1 else -1
                price = px*(1+0.0003 if side > 0 else 1-0.0003)
                # Mirror default isolated DEMO AI paper sizing:
                # 100 USDC equity, 25 USDC margin cap, 10x leverage.
                margin = min(max(0.0, self.cash), 25.0)
                size = margin*10.0
                qty = size/price if price > 0 else 0
                entry_fee = size*0.0005
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
            reward = (equity-before)/100.0 - drawdown*0.0004
            self.idx += 1
            done = self.idx >= len(rows)-1 or equity < 20
            truncated = bool(training and self.idx-self.start >= 512)
            if done or truncated:
                if self.pos:
                    self._close(bar["c"])
                equity = self.cash
            info = {"equity": round(equity, 6), "trades": len(self.trades),
                    "max_drawdown": round(self.max_drawdown, 6),
                    "net_pnl": round(self.cash-100, 6)}
            return (self._obs() if not (done or truncated) else
                    np.zeros(len(OBS_NAMES), dtype=np.float32)), float(reward), done, truncated, info

    return PaperEnv()


def evaluate_one(model, rows):
    env = environment({"validation": rows}, training=False)
    obs, _ = env.reset()
    done = truncated = False
    while not (done or truncated):
        action, _ = model.predict(obs, deterministic=True)
        obs, _, done, truncated, info = env.step(action)
    profits = [x for x in env.trades if x > 0]
    losses = [-x for x in env.trades if x < 0]
    return {
        "trades": len(env.trades), "wins": len(profits),
        "net_pnl_usdc": round(env.cash-100, 4),
        "profit_factor": round(sum(profits)/sum(losses), 4) if losses else None,
        "max_drawdown": round(env.max_drawdown, 4),
        "holdout_candles": len(rows),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbols", default="BTC,ETH,SOL")
    ap.add_argument("--output", default="models/trading_x_ppo")
    ap.add_argument("--steps", type=int, default=250_000)
    args = ap.parse_args()
    if args.steps < 20_000:
        ap.error("minimum 20000 steps")
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_checker import check_env
        import torch
    except ImportError as exc:
        raise SystemExit("Install offline training dependencies from requirements-ai-train.txt") from exc
    symbols = tuple(dict.fromkeys(x.strip().upper() for x in args.symbols.split(",") if x.strip()))
    if not symbols or len(symbols) > 12:
        ap.error("Choose between 1 and 12 symbols")
    datasets = {symbol: load_csv(Path(args.csv), symbol) for symbol in symbols}
    train = {symbol: rows[:int(len(rows)*0.75)] for symbol, rows in datasets.items()}
    test = {symbol: rows[int(len(rows)*0.75)-40:] for symbol, rows in datasets.items()}
    env = environment(train, training=True)
    check_env(env)
    model = PPO("MlpPolicy", env, verbose=1, seed=17, device="cpu",
                policy_kwargs={"net_arch": {"pi": [32, 32], "vf": [32, 32]},
                               "activation_fn": torch.nn.Tanh})
    model.learn(total_timesteps=args.steps)
    result = {symbol: evaluate_one(model, rows) for symbol, rows in test.items()}
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
        "training_algorithm": "PPO", "steps": args.steps,
        "fee_bps_per_side": 5, "slippage_and_spread_bps_per_side": 3,
        "runtime_profile": {"leverage": 10, "position_percent": 1.0,
                            "margin_cap_usdc": 25.0},
    }
    (output/"manifest.json").write_text(json.dumps(meta, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"model_dir": str(output), "holdout": result,
                      "approval": "DENIED until independent review"}, indent=2))


if __name__ == "__main__":
    main()
