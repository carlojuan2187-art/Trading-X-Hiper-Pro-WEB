"""Export ONLY sanitized private PPO DEMO closed experiences for offline training.

Run on a controlled host with MongoDB access; NEVER in public GitHub Actions.
Never upload the JSONL to GitHub, Telegram, or Railway build logs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from pymongo import MongoClient

from app.demo.ai_experience import clean_observation


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--max-records", type=int, default=10000)
    a = p.parse_args()
    if not 1 <= a.max_records <= 100000:
        p.error("max-records must be 1..100000")
    url = os.getenv("MONGO_URL")
    name = os.getenv("MONGO_DB_NAME")
    if not url or not name:
        raise RuntimeError("MONGO_URL and MONGO_DB_NAME required for private offline export")
    out = Path(a.output)
    if out.suffix != ".jsonl":
        p.error("Specify a .jsonl output file")
    if out.exists():
        raise FileExistsError("Refusing to overwrite historical training export")
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with MongoClient(url, serverSelectionTimeoutMS=10000) as client:
        collection = client[name]["demo_ai_experiences"]
        rows = collection.find({"source": "TRADING_X_PRIVATE_DEMO_CLOSED"}).sort(
            "closed_at", 1).limit(a.max_records)
        with out.open("x", encoding="utf-8") as handle:
            os.chmod(out, 0o600)
            for r in rows:
                clean_observation(r["observation"])
                # A narrow allowlist: NEVER export account ID, token, secret,
                # balance snapshot, or other fields added to the database.
                keys = ("_id", "source", "model_version", "symbol", "action",
                        "observation", "entry_candle_ms", "opened_at",
                        "closed_at", "pnl_net_usdc", "margin_usdc", "fees_usdc",
                        "close_reason")
                handle.write(json.dumps({k:r[k] for k in keys if k in r}, allow_nan=False)+"\n")
                count += 1
    print(f"DEMO_AI_PRIVATE_EXPORT count={count} output={out} (do not upload/share)")


if __name__ == "__main__":
    main()
