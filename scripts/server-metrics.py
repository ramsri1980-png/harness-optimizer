#!/usr/bin/env python3
"""Summarize the server-side metrics log written in lean mode.

  python3 scripts/server-metrics.py [--log PATH] [--since 24h]

Numbers are ESTIMATES (len//4 of full-file baseline minus returned text), not
provider-reported usage. Use scripts/poc-measure.sh for provider numbers.
"""
import argparse
import collections
import json
import os
import re
import sys
import time


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=os.environ.get("HARNESS_METRICS_LOG") or os.path.expanduser("~/.local/state/harness-optimizer/metrics.jsonl"))
    ap.add_argument("--since", default="", help="e.g. 30m, 24h, 7d")
    a = ap.parse_args(argv)
    if not os.path.exists(a.log):
        sys.exit(f"No log at {a.log} (is HARNESS_LEAN=1 set for the MCP server?)")
    cutoff = 0
    if a.since:
        m = re.fullmatch(r"(\d+)([mhd])", a.since)
        if not m:
            sys.exit("--since must look like 30m, 24h or 7d")
        cutoff = time.time() - int(m.group(1)) * {"m": 60, "h": 3600, "d": 86400}[m.group(2)]
    rows = collections.defaultdict(lambda: [0, 0, 0, 0])
    bad = 0
    with open(a.log, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
                if r["ts"] < cutoff:
                    continue
                t = rows[r["tool"]]
                t[0] += 1
                t[1] += r["saved"]
                t[2] += r["baseline"]
                t[3] += r["actual"]
            except (ValueError, KeyError, TypeError):
                bad += 1
    if not rows:
        sys.exit("No matching entries.")
    print(f"{'tool':26}{'calls':>7}{'est.saved':>11}{'baseline':>10}{'actual':>8}")
    tot = [0, 0, 0, 0]
    for tool, v in sorted(rows.items(), key=lambda kv: -kv[1][1]):
        print(f"{tool:26}{v[0]:>7}{v[1]:>11}{v[2]:>10}{v[3]:>8}")
        tot = [x + y for x, y in zip(tot, v)]
    print(f"{'TOTAL':26}{tot[0]:>7}{tot[1]:>11}{tot[2]:>10}{tot[3]:>8}")
    if bad:
        print(f"({bad} unreadable line(s) skipped)")
    print("Estimates only: baseline = whole file / full rewrite; not provider-reported.")


if __name__ == "__main__":
    main()
