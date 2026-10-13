"""Offline log analysis; repeated evaluations are not independent opportunities."""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re


def analyze(text):
    reasons, bias_reasons, statuses, regimes, failures = (Counter() for _ in range(5))
    scans = budget_exhausted = ema200_only_h1 = 0
    for match in re.finditer(r'SCAN_DIAGNOSTICS user=\d+ evaluated=\d+ shortlist=\d+ cache_hits=\d+ rejected=(\{[^}]*\}) budget_exhausted=(True|False)', text):
        scans += 1
        budget_exhausted += match[2] == 'True'
        for key, value in ast.literal_eval(match[1]).items():
            reasons[key.removeprefix('CACHED:')] += value
    decoder = json.JSONDecoder()
    for match in re.finditer(r'(BIAS_DIAGNOSTICS|LIQUIDITY_DIAGNOSTICS) symbol=(\S+)(?: reason=(\S+))? diag=', text):
        try:
            diag, _ = decoder.raw_decode(text[match.end():])
        except ValueError:
            continue
        if match[1] == 'BIAS_DIAGNOSTICS':
            bias_reasons[match[3]] += 1
            if match[3] == 'NO_H1_BIAS' and (diag.get('failed_short') == ['EMA50_BELOW_EMA200'] or diag.get('failed_long') == ['EMA50_ABOVE_EMA200']):
                ema200_only_h1 += 1
        else:
            statuses[diag.get('status', 'UNKNOWN')] += 1
            regimes[diag.get('regime', 'UNKNOWN')] += 1
            attempt = diag.get('attempt') or {}
            for side in ('long_search', 'short_search'):
                failures.update(((attempt.get('diag') or {}).get(side) or {}).get('failures', {}))
    timestamps=re.findall(r'^\d{4}-\d\d-\d\dT[^ ]+', text, re.MULTILINE)
    return dict(first_timestamp=timestamps[0] if timestamps else None,
                last_timestamp=timestamps[-1] if timestamps else None,
                scans=scans, rejections=dict(reasons), total_rejections=sum(reasons.values()),
                budget_exhausted=budget_exhausted, bias_diagnostics=dict(bias_reasons),
                h1_ema200_only=ema200_only_h1, liquidity_statuses=dict(statuses),
                liquidity_regimes=dict(regimes), sweep_failure_counts=dict(failures),
                gate_block_messages=text.count('Bloqueo responsable:'),
                limitations='Counts include repeated evaluations and users; not independent trades. First-failure filters censor later gates.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path)
    args=parser.parse_args()
    print(json.dumps(analyze(args.log.read_text(errors='replace')),indent=2,ensure_ascii=False))
