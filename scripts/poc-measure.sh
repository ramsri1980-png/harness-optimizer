#!/usr/bin/env bash
# scripts/poc-measure.sh — POC: measure real token usage with/without harness.
#
# Usage:
#   ./scripts/poc-measure.sh snapshot   # print current max(time_created)
#   ./scripts/poc-measure.sh window <t_start_ms> <t_end_ms>  # sum usage in range
#   ./scripts/poc-measure.sh compare <t0> <t1> <t2>  # baseline vs treatment
#
# Real usage is stored by OpenCode in ~/.local/share/opencode/opencode.db
# at JSON path $.tokens.input / $.tokens.output.

set -euo pipefail

DB="${HOME}/.local/share/opencode/opencode.db"

if [[ ! -f "$DB" ]]; then
  echo "ERROR: no OpenCode DB at $DB" >&2
  exit 1
fi

cmd="${1:-}"

case "$cmd" in
  snapshot)
    sqlite3 "$DB" "SELECT COALESCE(max(time_created), 0) FROM session_message;"
    ;;

  window)
    t_start="${2:?t_start_ms required}"
    t_end="${3:?t_end_ms required}"
    sqlite3 "$DB" "
      SELECT
        sum(json_extract(data, '\$.tokens.input'))  AS input_tokens,
        sum(json_extract(data, '\$.tokens.output')) AS output_tokens,
        count(*) AS turns
      FROM session_message
      WHERE type = 'assistant'
        AND json_type(data, '\$.tokens.input') = 'integer'
        AND time_created >  $t_start
        AND time_created <= $t_end;
    "
    ;;

  compare)
    t0="${2:?t0_ms required}"
    t1="${3:?t1_ms required}"
    t2="${4:?t2_ms required}"

    read -r base_in base_out base_turns < <(sqlite3 "$DB" "
      SELECT
        COALESCE(sum(json_extract(data, '\$.tokens.input')), 0),
        COALESCE(sum(json_extract(data, '\$.tokens.output')), 0),
        count(*)
      FROM session_message
      WHERE type = 'assistant'
        AND json_type(data, '\$.tokens.input') = 'integer'
        AND time_created >  $t0
        AND time_created <= $t1;
    " | tr '|' ' ')

    read -r treat_in treat_out treat_turns < <(sqlite3 "$DB" "
      SELECT
        COALESCE(sum(json_extract(data, '\$.tokens.input')), 0),
        COALESCE(sum(json_extract(data, '\$.tokens.output')), 0),
        count(*)
      FROM session_message
      WHERE type = 'assistant'
        AND json_type(data, '\$.tokens.input') = 'integer'
        AND time_created >  $t1
        AND time_created <= $t2;
    " | tr '|' ' ')

    echo "════════════════════════════════════════"
    echo "  POC — real provider-reported usage"
    echo "════════════════════════════════════════"
    printf "  baseline (harness off)  input: %'d  output: %'d  turns: %d\n" \
      "$base_in" "$base_out" "$base_turns"
    printf "  treatment (harness on)  input: %'d  output: %'d  turns: %d\n" \
      "$treat_in" "$treat_out" "$treat_turns"

    if [[ "$base_in" -gt 0 ]]; then
      diff=$((base_in - treat_in))
      if [[ "$diff" -ge 0 ]]; then
        pct=$(( diff * 100 / base_in ))
        printf "\n  input reduction: %'d tokens ( %d%% )\n" "$diff" "$pct"
      else
        pct=$(( -diff * 100 / base_in ))
        printf "\n  input INCREASE: %'d tokens ( %d%% )\n" "$((-diff))" "$pct"
      fi
      printf "  caveat: single-run sample; rerun for a stable number\n"
    fi
    ;;

  *)
    echo "Usage: $0 {snapshot|window <t0> <t1>|compare <t0> <t1> <t2>}" >&2
    exit 2
    ;;
esac
