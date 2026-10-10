#!/usr/bin/env bash
# scripts/ctx-live.sh — live monitor for CTX Day 1 testing.
#
# Watches:
#   - OpenCode main log (server/plugin/MCP activity)
#   - Harness metrics log (tool calls)
#   - CTX smoke test artifacts (stub provider, request body)
#
# Usage:
#   ./scripts/ctx-live.sh           # refresh every 2s
#   ./scripts/ctx-live.sh --once    # single snapshot then exit

set -uo pipefail

OC_LOG="${HOME}/.local/share/opencode/log/opencode.log"
METRICS="${HOME}/.config/opencode/.harness-token-metrics.log"

snapshot() {
  echo "════════════════════════════════════════════════════════════════"
  echo "  CTX Day 1 live monitor — $(date '+%H:%M:%S')"
  echo "════════════════════════════════════════════════════════════════"
  echo

  echo "── OpenCode log — last 15 lines ──"
  if [[ -f "$OC_LOG" ]]; then
    tail -15 "$OC_LOG" | sed 's/^/  /'
  else
    echo "  (no OpenCode log at $OC_LOG)"
  fi
  echo

  echo "── Plugin activity (last 5) ──"
  if [[ -f "$OC_LOG" ]]; then
    grep -iE "plugin|harness|auto-context|session.hook" "$OC_LOG" 2>/dev/null | tail -5 | sed 's/^/  /'
    echo
  fi

  echo "── Harness tool calls (last 8) ──"
  if [[ -f "$METRICS" ]]; then
    grep "tool=" "$METRICS" 2>/dev/null | tail -8 | sed 's/^/  /'
  else
    echo "  (no metrics log)"
  fi
  echo

  echo "── CTX smoke test artifacts ──"
  if [[ -d /tmp/opencode/ctx_spike ]]; then
    echo "  stub provider log:"
    tail -3 /tmp/opencode/ctx_spike/stub.out 2>/dev/null | sed 's/^/    /' || echo "    (no stub.out)"
    echo
    echo "  request capture:"
    if [[ -f /tmp/opencode/ctx_spike/request.jsonl ]]; then
      echo "    last request bytes: $(tail -1 /tmp/opencode/ctx_spike/request.jsonl | wc -c)"
      if tail -1 /tmp/opencode/ctx_spike/request.jsonl | grep -q "HARNESS REPOSITORY CONTEXT"; then
        echo "    ✅ CTX PACKET DETECTED in last request"
      else
        echo "    ⚠️  no CTX packet in last request"
      fi
    else
      echo "    (no request.jsonl yet — smoke test has not run)"
    fi
  fi
  echo

  echo "── Running processes ──"
  pgrep -af "opencode run --standalone" | head -3 | sed 's/^/  /' || true
  pgrep -af "stub_provider" | head -3 | sed 's/^/  /' || true
  echo
}

case "${1:-}" in
  --once)
    snapshot
    ;;
  *)
    while true; do
      clear
      snapshot
      sleep 2
    done
    ;;
esac
