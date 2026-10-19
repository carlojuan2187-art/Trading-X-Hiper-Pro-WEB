#!/usr/bin/env python3
"""Download REAL 5m Hyperliquid OHLCV for offline AI training; no credentials."""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import time

import httpx

BAR_MS = 300_000
FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "symbol")


def download(symbol: str, start: int, end: int, *, url: str, client: httpx.Client):
    candles = {}
    current = start
    while current < end:
        next_end = min(end, current+400*BAR_MS)
        response = client.post(url, json={"type":"candleSnapshot",
            "req":{"coin":symbol,"interval":"5m","startTime":current,"endTime":next_end}},
            timeout=20)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError(f"Invalid OHLCV result for {symbol}")
        for row in data:
            if row.get("s") != symbol or row.get("i") != "5m":
                continue
            t = int(row["t"])
            if start <= t and t + BAR_MS <= end:
                candles[t] = (t, row["o"], row["h"], row["l"], row["c"], row["v"], symbol)
        current = next_end
    result = [candles[t] for t in sorted(candles)]
    if len(result) < 1500:
        raise ValueError(f"{symbol}: insufficient authentic candles ({len(result)})")
    if any(b[0]-a[0] != BAR_MS for a, b in zip(result, result[1:])):
        raise ValueError(f"{symbol}: market history has gaps; refuse training")
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTC,ETH,SOL")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--network", choices=("mainnet", "testnet"), default="mainnet")
    p.add_argument("--output", default="data/trading_x_real_5m.csv")
    a = p.parse_args()
    if not 6 <= a.days <= 17:
        p.error("--days must be 6..17 due to 5000-candle provider retention")
    symbols = tuple(dict.fromkeys(s.strip().upper() for s in a.symbols.split(",") if s.strip()))
    if not symbols or len(symbols) > 12 or any(not s.isalnum() for s in symbols):
        p.error("1..12 ordinary perp symbols required")
    now = int(time.time()*1000)//BAR_MS*BAR_MS
    start = now - a.days*24*60//5*BAR_MS
    url = ("https://api.hyperliquid.xyz/info" if a.network == "mainnet"
           else "https://api.hyperliquid-testnet.xyz/info")
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    all_rows = []
    with httpx.Client() as client:
        for symbol in symbols:
            rows = download(symbol, start, now, url=url, client=client)
            print(f"Downloaded {symbol}: {len(rows)} verified real candles")
            all_rows.extend(rows)
    temp = output.with_suffix(output.suffix+".tmp")
    with temp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(all_rows)
    temp.replace(output)
    print(f"Saved {output} (not synthetic). Never commit market data or secrets.")


if __name__ == "__main__":
    main()
