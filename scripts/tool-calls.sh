#!/usr/bin/env bash
# scripts/tool-calls.sh — summarize harness tool usage from the metrics log.
#
# Usage:
#   scripts/tool-calls.sh           # today's summary
#   scripts/tool-calls.sh --all     # all-time summary
#   scripts/tool-calls.sh --watch   # refresh every 3s
#   scripts/tool-calls.sh --live    # tail -f style

set -euo pipefail

LOG="${HOME}/.config/opencode/.harness-token-metrics.log"
TOTALS="${HOME}/.config/opencode/.harness-token-totals.json"

if [[ ! -f "$LOG" ]]; then
  echo "No log file at $LOG"
  exit 1
fi

today_utc() { date -u +%Y-%m-%d; }

filter_today() {
  local today
  today=$(today_utc)
  grep -E "^\[${today}T" "$LOG" || true
}

summary() {
  local stream="$1"
  local total harness native
  total=$(echo "$stream" | grep -c 'tool=' || true)
  harness=$(echo "$stream" | grep -c 'tool=.*saved=' || true)
  native=$((total - harness))

  echo "──────────── summary ────────────"
  printf "total tool calls:   %s\n" "$total"
  printf "harness calls:      %s\n" "$harness"
  printf "native calls:       %s\n" "$native"
  echo

  if [[ $total -gt 0 ]]; then
    echo "──────────── by tool ────────────"
    echo "$stream" | grep -oP 'tool=\K[a-z_]+' | sort | uniq -c | sort -rn
    echo
    echo "──────────── last 10 calls ────────────"
    echo "$stream" | grep 'tool=' | tail -10
  fi

  if [[ -f "$TOTALS" ]]; then
    echo
    echo "──────────── running total ────────────"
    cat "$TOTALS"
  fi
}

case "${1:-}" in
  --all)
    summary "$(cat "$LOG")"
    ;;
  --watch)
    while true; do
      clear
      echo "Tool calls — $(date)"
      echo
      summary "$(filter_today)"
      sleep 3
    done
    ;;
  --live)
    tail -f "$LOG" | grep --line-buffered 'tool=\|plugin loaded'
    ;;
  *)
    summary "$(filter_today)"
    ;;
esac
