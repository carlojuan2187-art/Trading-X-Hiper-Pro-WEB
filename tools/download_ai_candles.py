#!/usr/bin/env python3
"""Download REAL 5m Hyperliquid OHLCV for offline AI training; no credentials."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import time

from tools.live_training_universe import select_live_prefilter, scanner_limits

import httpx

BAR_MS = 300_000
FIELDS = ("timestamp", "open", "high", "low", "close", "volume", "symbol")


def download(symbol: str, start: int, end: int, *, url: str, client: httpx.Client):
    candles = {}
    current = start
    while current < end:
        next_end = min(end, current+400*BAR_MS)
        payload = {"type":"candleSnapshot",
                   "req":{"coin":symbol,"interval":"5m","startTime":current,"endTime":next_end}}
        data = post_with_retry(client, url, payload)
        if not isinstance(data, list):
            raise ValueError(f"Invalid OHLCV result for {symbol}")
        for row in data:
            if row.get("s") != symbol or row.get("i") != "5m":
                continue
            t = int(row["t"])
            if start <= t and t + BAR_MS <= end:
                candles[t] = (t, row["o"], row["h"], row["l"], row["c"], row["v"], symbol)
        current = next_end
        time.sleep(0.08)  # Respect public exchange API capacity across large LIVE universes.
    result = [candles[t] for t in sorted(candles)]
    if len(result) < 1500:
        raise ValueError(f"{symbol}: insufficient authentic candles ({len(result)})")
    if any(b[0]-a[0] != BAR_MS for a, b in zip(result, result[1:])):
        raise ValueError(f"{symbol}: market history has gaps; refuse training")
    return result




def post_with_retry(client: httpx.Client, url: str, payload: dict, attempts: int = 5):
    for attempt in range(attempts):
        try:
            response = client.post(url, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            transient = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code in (429, 500, 502, 503, 504)
            if not transient or attempt == attempts-1:
                raise
            time.sleep(min(3 * 2**attempt, 30))


def persist_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--universe", choices=("explicit", "live"), default="explicit",
                   help="live: current LIVE scanner prefilter, across all L2 rotation cycles")
    p.add_argument("--symbols", default="BTC,ETH,SOL")
    p.add_argument("--days", type=int, default=10)
    p.add_argument("--network", choices=("mainnet", "testnet"), default="mainnet")
    p.add_argument("--output", default="data/trading_x_real_5m.csv")
    p.add_argument("--report", default="data/trading_x_live_universe.json")
    p.add_argument("--min-symbols", type=int, default=10)
    p.add_argument("--min-coverage", type=float, default=0.8)
    a = p.parse_args()
    if not 6 <= a.days <= 17:
        p.error("--days must be 6..17 due to 5000-candle provider retention")
    if not 0 < a.min_coverage <= 1 or a.min_symbols < 1:
        p.error("invalid minimum research coverage")
    now = int(time.time()*1000)//BAR_MS*BAR_MS
    start = now - a.days*24*60//5*BAR_MS
    url = ("https://api.hyperliquid.xyz/info" if a.network == "mainnet"
           else "https://api.hyperliquid-testnet.xyz/info")
    output, report_path = Path(a.output), Path(a.report)
    report = {
        "source": "LIVE_SCANNER_PREFILTER" if a.universe == "live" else "EXPLICIT_SYMBOLS",
        "snapshot_timestamp_ms": now, "interval": "5m", "requested_history_days": a.days,
        "exchange_network": a.network, "market_policy": "app.symbol_policy",
        "live_l2_note": "All prefiltered coins eligible across LIVE scanner rotations; live L2 spread/depth separately checked at execution",
        "scanner_limits": scanner_limits() if a.universe == "live" else {},
        "requested": [], "downloaded": [], "excluded": {},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    all_rows = []
    with httpx.Client() as client:
        if a.universe == "live":
            market_data = post_with_retry(client, url, {"type": "metaAndAssetCtxs"})
            selected = select_live_prefilter(market_data, limits=report["scanner_limits"])
            symbols = tuple(row["symbol"] for row in selected)
            report["eligible_market_details"] = selected
        else:
            symbols = tuple(dict.fromkeys(s.strip().upper() for s in a.symbols.split(",") if s.strip()))
            if not symbols or any(not s.isalnum() for s in symbols):
                p.error("invalid ordinary perp symbols")
        report["requested"] = list(symbols)
        print(f"RESEARCH_UNIVERSE mode={a.universe} requested={len(symbols)} coins={','.join(symbols)}", flush=True)
        for symbol in symbols:
            try:
                rows = download(symbol, start, now, url=url, client=client)
            except (ValueError, httpx.HTTPError, KeyError, TypeError) as exc:
                report["excluded"][symbol] = f"{type(exc).__name__}: {str(exc)[:300]}"
                print(f"UNIVERSE_EXCLUDED coin={symbol} reason={report['excluded'][symbol]}", flush=True)
                continue
            print(f"Downloaded {symbol}: {len(rows)} verified real candles", flush=True)
            report["downloaded"].append({"symbol": symbol, "candles": len(rows)})
            all_rows.extend(rows)
    report["coverage"] = round(len(report["downloaded"])/max(1, len(symbols)), 4)
    persist_report(report_path, report)
    print(f"RESEARCH_UNIVERSE_COVERAGE candidates={len(symbols)} trained={len(report['downloaded'])} "
          f"excluded={len(report['excluded'])} coverage={report['coverage']:.2%} report={report_path}", flush=True)
    if (not report["downloaded"] or (a.universe == "live" and
        (len(report["downloaded"]) < a.min_symbols or report["coverage"] < a.min_coverage))):
        raise ValueError("Insufficient LIVE training universe coverage; read the excluded symbols report")
    temp = output.with_suffix(output.suffix+".tmp")
    with temp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(all_rows)
    temp.replace(output)
    print(f"Saved {output} (not synthetic). Never commit market data or secrets.", flush=True)


if __name__ == "__main__":
    main()
