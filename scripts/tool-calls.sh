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

fmt_num() {
  printf "%'d" "$1" 2>/dev/null || printf "%d" "$1"
}

show_default() {
  local stream
  stream=$(cat "$LOG")

  local total harness native total_saved
  total=$(echo "$stream" | grep -c 'tool=' || true)
  harness=$(echo "$stream" | grep -c 'tool=.*saved=' || true)
  native=$((total - harness))
  total_saved=$(echo "$stream" | grep -oP 'tool=\S+.*?saved=\K[0-9]+' | awk '{s+=$1} END {print s+0}')

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
  echo "════════════════════════════════════════"
  printf "  💰 TOTAL SAVED:  %s tokens\n" "$(fmt_num "$total_saved")"
  printf "  📞 calls:        %s\n" "$(fmt_num "$total")"
  echo "════════════════════════════════════════"
}

today_stats() {
  local today today_lines today_calls today_saved
  today=$(today_utc)
  today_lines=$(grep -E "^\[${today}T" "$LOG" || true)
  today_calls=$(echo "$today_lines" | grep -c 'tool=' || true)
  today_saved=$(echo "$today_lines" | grep -oP 'tool=\S+.*?saved=\K[0-9]+' | awk '{s+=$1} END {print s+0}')
  printf "💵 today: %s tokens  (%s calls)\n" "$(fmt_num "$today_saved")" "$(fmt_num "$today_calls")"
}

session_stats() {
  local last_loaded_line line_num since_ts session_lines session_calls session_saved
  last_loaded_line=$(grep -n 'plugin loaded' "$LOG" | tail -1 || true)
  if [[ -n "$last_loaded_line" ]]; then
    line_num=$(echo "$last_loaded_line" | cut -d: -f1)
    since_ts=$(echo "$last_loaded_line" | grep -oP '\[\K[^\]]+' || echo "unknown")
    session_lines=$(tail -n +"$((line_num + 1))" "$LOG")
  else
    since_ts=$(head -1 "$LOG" | grep -oP '\[\K[^\]]+' || echo "start")
    session_lines=$(cat "$LOG")
  fi
  session_calls=$(echo "$session_lines" | grep -c 'tool=' || true)
  session_saved=$(echo "$session_lines" | grep -oP 'tool=\S+.*?saved=\K[0-9]+' | awk '{s+=$1} END {print s+0}')
  printf "🕐 session: %s tokens  (%s calls)  since %s\n" "$(fmt_num "$session_saved")" "$(fmt_num "$session_calls")" "$since_ts"
}

show_stats() {
  show_default
  echo
  today_stats
  session_stats
}

case "${1:-}" in
  --today)   today_stats ;;
  --session) session_stats ;;
  --stats)   show_stats ;;
  --all)     show_default ;;
  --watch)
    while true; do
      clear
      echo "Tool calls — $(date)"
      echo
      show_default
      sleep 3
    done
    ;;
  --live)  tail -f "$LOG" | grep --line-buffered 'tool=\|plugin loaded' ;;
  *)       show_default ;;
esac
