#!/usr/bin/env bash
# scripts/tool-calls.sh — summarize harness tool usage from the metrics log.

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

fmt_num() {
  printf "%'d" "$1" 2>/dev/null || printf "%d" "$1"
}

summary() {
  local stream="$1"
  local total harness native today_saved
  total=$(echo "$stream" | grep -c 'tool=' || true)
  harness=$(echo "$stream" | grep -c 'tool=.*saved=' || true)
  native=$((total - harness))
  today_saved=$(echo "$stream" | grep -oP 'saved=\K[0-9]+' | awk '{s+=$1} END {print s+0}')

  echo "════════════════════════════════════════"
  echo "  🛠  Harness tool calls"
  echo "════════════════════════════════════════"
  printf "  total tool calls:   %s\n" "$(fmt_num "$total")"
  printf "  harness calls:      %s\n" "$(fmt_num "$harness")"
  printf "  native calls:       %s\n" "$(fmt_num "$native")"
  echo

  if [[ $total -gt 0 ]]; then
    echo "────────────────────────────────────────"
    echo "  📊 by tool"
    echo "────────────────────────────────────────"
    echo "$stream" | grep -oP 'tool=\K[a-z_]+' | sort | uniq -c | sort -rn | \
      awk '{ printf "  %5d  %s\n", $1, $2 }'
    echo

    echo "────────────────────────────────────────"
    echo "  ⏱  last 5 calls"
    echo "────────────────────────────────────────"
    echo "$stream" | grep 'tool=' | tail -5 | sed 's/^/  /'
  fi

  echo

  if [[ -f "$TOTALS" ]]; then
    local cum_total cum_calls
    cum_total=$(python3 -c "import json; print(json.load(open('$TOTALS'))['totalSaved'])" 2>/dev/null || echo 0)
    cum_calls=$(python3 -c "import json; print(json.load(open('$TOTALS'))['calls'])" 2>/dev/null || echo 0)

    echo "════════════════════════════════════════"
    printf "  💰 TOTAL SAVED:  %s tokens\n" "$(fmt_num "$cum_total")"
    printf "  📞 calls:        %s\n" "$(fmt_num "$cum_calls")"
    echo "════════════════════════════════════════"
  fi

  if [[ "$today_saved" -gt 0 ]]; then
    echo
    printf "  💵 today:        %s tokens\n" "$(fmt_num "$today_saved")"
  fi
}

case "${1:-}" in
  --all)   summary "$(cat "$LOG")" ;;
  --watch)
    while true; do
      clear
      echo "Tool calls — $(date)"
      echo
      summary "$(filter_today)"
      sleep 3
    done
    ;;
  --live)  tail -f "$LOG" | grep --line-buffered 'tool=\|plugin loaded' ;;
  *)       summary "$(filter_today)" ;;
esac
